# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Kick chat: Pusher WebSocket; the chatroom ID comes from the Kick website API."""
from __future__ import annotations

import asyncio
import json
import logging
import re

import websockets

from ..core.i18n import _
from .kick_events import ban_notice, kick_event, kick_notice  # noqa: F401 – kick_event is imported from here elsewhere
from .models import Backoff, ChatMessage, Deletion, Emit, Part, Status, TTLCache, kick_channel, roles, with_extra_emotes as _extra

log = logging.getLogger("multichat.chat")


# Kick rotated its Pusher key in October 2026. The app asks Kick for the current
# credentials; these are only fallback values.
KICK_REALTIME_URL = "https://web.kick.com/api/v1/realtime/connection"
KICK_PUSHER_FALLBACKS = [("34bf7a0ff419a2a775b9", "us2"), ("32cbd69e4b950bf97679", "us2")]
_kick_pusher_bad: set[str] = set()  # keys Kick rejected with 4001


def kick_pusher_url(key: str, cluster: str) -> str:
    return f"wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0-rc2&flash=false"


_PUSHER_KEY_RE = re.compile(r"[0-9a-f]{16,40}")
_PUSHER_CLUSTER_RE = re.compile(r"[a-z0-9-]{1,16}")


def _find_pusher_creds(obj) -> tuple[str, str] | None:
    """Looks for app_key/cluster anywhere in the response (the format may change).
    Both end up in the WebSocket host name, so only plausible values are accepted."""
    if isinstance(obj, dict):
        key, cluster = str(obj.get("app_key") or ""), str(obj.get("cluster") or "us2")
        if _PUSHER_KEY_RE.fullmatch(key) and _PUSHER_CLUSTER_RE.fullmatch(cluster):
            return key, cluster
        for v in obj.values():
            found = _find_pusher_creds(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_pusher_creds(v)
            if found:
                return found
    return None


def _fetch_kick_pusher_creds() -> tuple[str, str]:
    """Asks Kick for the Pusher credentials (offering only the "pusher" provider)."""
    import uuid

    body = {
        "client": {"id": str(uuid.uuid4()), "type": "web"},
        "capabilities": {"accepted_providers": [{"provider": "pusher"}]},
    }
    try:
        try:
            from curl_cffi import requests as cf  # type: ignore

            r = cf.post(KICK_REALTIME_URL, json=body, impersonate="chrome", timeout=15,
                        headers={"Accept": "application/json"})
        except ImportError:
            import requests

            r = requests.post(KICK_REALTIME_URL, json=body, timeout=15,
                              headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        log.debug("Kick realtime → HTTP %s: %s", r.status_code, r.text[:400])
        if r.status_code == 200:
            found = _find_pusher_creds(r.json())
            if found and found[0] not in _kick_pusher_bad:
                log.info("Kick Pusher credentials received: key %s, cluster %s", *found)
                return found
            log.warning("Kick realtime: no (new) Pusher credentials in the response: %s", r.text[:300])
        else:
            log.warning("Kick realtime HTTP %s: %s", r.status_code, re.sub(r"\s+", " ", r.text[:300]))
    except Exception as e:  # noqa: BLE001
        log.warning("Kick realtime request failed: %s", e)
    for key, cluster in KICK_PUSHER_FALLBACKS:
        if key not in _kick_pusher_bad:
            log.info("Kick Pusher: using fallback %s / %s", key, cluster)
            return key, cluster
    _kick_pusher_bad.clear()  # tried everything → start over
    return KICK_PUSHER_FALLBACKS[0]


class KickKeyRejected(Exception):
    pass


KICK_EMOTE_URL = "https://files.kick.com/emotes/{}/fullsize"
_KICK_EMOTE_RE = re.compile(r"\[emote:([0-9]+):([^\]]*)\]")  # ASCII digits only: the id goes into a CDN URL


def kick_parts(content: str) -> list[Part]:
    out: list[Part] = []
    i = 0
    for m in _KICK_EMOTE_RE.finditer(content):
        if m.start() > i:
            out.append(("text", content[i : m.start()], ""))
        out.append(("emote", KICK_EMOTE_URL.format(m.group(1)), m.group(2)))
        i = m.end()
    if i < len(content):
        out.append(("text", content[i:], ""))
    return out or [("text", "", "")]


def _fetch_kick_channel(slug: str) -> tuple[int, str]:
    """Returns (chatroom ID, streamer user ID)."""
    data = fetch_kick_json(slug)
    return int(data["chatroom"]["id"]), str(data.get("user_id") or "")


def fetch_kick_json(slug: str) -> dict:
    """Channel info from the Kick website API (also contains `livestream` and `playback_url`).
    Kick sits behind Cloudflare, hence curl_cffi (browser fingerprint). Answers are shared
    for a few seconds (chat and video tile ask together)."""
    slug = kick_channel(slug)
    return _channel_cache.fetch(slug, lambda: _kick_get(f"https://kick.com/api/v2/channels/{slug}", slug))


_channel_cache = TTLCache(20)


_KICK_USER_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def fetch_kick_user(slug: str, username: str) -> dict:
    """A chatter as seen in a channel (unofficial website endpoint, no login needed):
    following_since, subscribed_for (months), badges, banned …"""
    if not _KICK_USER_RE.fullmatch(username):
        raise ValueError(f"invalid Kick user name: {username!r}")
    return _kick_get(f"https://kick.com/api/v2/channels/{kick_channel(slug)}/users/{username.lower()}", slug)


def _kick_get(url: str, slug: str) -> dict:
    try:
        from curl_cffi import requests as cf  # type: ignore

        lib = "curl_cffi"
        r = cf.get(url, impersonate="chrome", timeout=15)
    except ImportError:
        import requests

        lib = "requests (curl_cffi missing!)"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    ctype = r.headers.get("content-type", "")
    log.debug("Kick %s → HTTP %s, %s, %d Bytes, via %s", url, r.status_code, ctype, len(r.content), lib)
    if r.status_code == 404:
        raise ValueError(f"Kick channel “{slug}” not found")
    if r.status_code != 200 or "json" not in ctype:
        body = re.sub(r"\s+", " ", r.text[:300])
        cloudflare = "cloudflare" in r.text[:5000].lower() or "just a moment" in r.text[:5000].lower()
        log.warning(
            "Kick API error: HTTP %s, content type %r, via %s, Cloudflare=%s, response: %s",
            r.status_code, ctype, lib, cloudflare, body,
        )
        hint = " (blocked by Cloudflare)" if cloudflare else ""
        raise RuntimeError(f"Kick API HTTP {r.status_code}{hint}")
    return r.json()


def _handle_event(name: str, data: dict, slug: str, emit: Emit, emotes) -> None:
    if notice := kick_notice(name, data, slug):
        user, headline, message = notice
        emit(ChatMessage(platform="kick", channel=slug, user=user, text=_KICK_EMOTE_RE.sub(r"\2", message),
                         parts=_extra(emotes, "kick", slug, kick_parts(message)) if message else [],
                         event="notice", event_text=headline))
        return
    if name.endswith("ChatMessageEvent"):
        sender = data.get("sender") or {}
        content = str(data.get("content") or "")
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        original = meta.get("original_sender") if isinstance(meta.get("original_sender"), dict) else {}
        reply_to = original.get("username") if data.get("type") == "reply" else None
        note = ({"event": "notice", "event_text": _("Replying to {user}").format(user=reply_to)}
                if isinstance(reply_to, str) and reply_to.strip() else {})
        emit(
            ChatMessage(
                platform="kick",
                channel=slug,
                user=str(sender.get("username") or "?"),
                text=_KICK_EMOTE_RE.sub(r"\2", content),
                parts=_extra(emotes, "kick", slug, kick_parts(content)),
                color=(sender.get("identity") or {}).get("color"),
                badges=roles(b.get("type", "") for b in (sender.get("identity") or {}).get("badges") or [] if isinstance(b, dict)),
                msg_id=str(data.get("id") or "") or None,
                user_id=str(sender.get("id") or "") or None,
                **note,
            )
        )
    elif ev := kick_event(name, data):
        user, event, value, text = ev
        emit(ChatMessage(platform="kick", channel=slug, user=user, text="", parts=[],
                         event=event, event_value=value, event_text=text))
    elif name.endswith("MessageDeletedEvent"):
        mid = (data.get("message") or {}).get("id")
        if mid:
            emit(Deletion("kick", msg_id=str(mid)))
    elif name.endswith("UserBannedEvent"):
        uid = (data.get("user") or {}).get("id")
        if uid:
            emit(Deletion("kick", user_id=str(uid)))
        if line := ban_notice(data):
            emit(ChatMessage(platform="kick", channel=slug, user="Kick", text="", event="notice", event_text=line))
    elif name.endswith("ChatroomClearEvent"):
        emit(Deletion("kick", clear_all=True))


async def run_kick(channel: str, emit: Emit, status: Status, emotes=None) -> None:
    slug = kick_channel(channel)
    backoff = Backoff(first=10)
    while True:
        key = ""
        try:
            status("kick", "getting chatroom ID …")
            room_id, streamer_id = await asyncio.to_thread(_fetch_kick_channel, slug)
            log.info("Kick %s: chatroom %s, streamer ID %s", slug, room_id, streamer_id)
            if emotes:
                emotes.schedule_channel("kick", slug, streamer_id)
            status("kick", "connecting …")
            key, cluster = await asyncio.to_thread(_fetch_kick_pusher_creds)
            async with websockets.connect(kick_pusher_url(key, cluster)) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "event": "pusher:subscribe",
                            "data": {"auth": "", "channel": f"chatrooms.{room_id}.v2"},
                        }
                    )
                )
                status("kick", "subscribing to chat …")
                async for raw in ws:
                    try:
                        evt = json.loads(raw)
                        name = str(evt.get("event") or "")
                    except (ValueError, AttributeError):
                        continue
                    if name == "pusher:ping":
                        await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                        continue
                    if name.startswith("pusher"):
                        if name == "pusher:error":
                            err = evt.get("data") or {}
                            log.warning("Kick Pusher error: %s", err)
                            if isinstance(err, dict) and err.get("code") == 4001:
                                _kick_pusher_bad.add(key)
                                raise KickKeyRejected(f"Kick rejected Pusher key {key} – trying the next one")
                        elif name == "pusher_internal:subscription_succeeded":
                            log.info("Kick %s: chat subscribed (key %s)", slug, key)
                            status("kick", "connected ({channel})", channel=slug)
                            backoff.connected()
                        else:
                            log.debug("Kick Pusher: %s %s", name, str(evt.get("data"))[:200])
                        continue
                    try:  # one malformed event must not drop the connection
                        data = evt.get("data") or "{}"
                        data = json.loads(data) if isinstance(data, str) else data
                        if isinstance(data, dict):
                            if emotes and name.endswith("ChatMessageEvent"):
                                # Retry empty/expired/evicted sets without reconnecting.
                                emotes.schedule_channel("kick", slug, streamer_id)
                            _handle_event(name, data, slug, emit, emotes)
                    except Exception as e:  # noqa: BLE001
                        log.debug("Kick %s: event %s skipped: %s", slug, name, e)
        except asyncio.CancelledError:
            raise
        except KickKeyRejected as e:
            log.warning("%s", e)
            status("kick", "key rejected – trying the next one …")
            await asyncio.sleep(1)
        except websockets.exceptions.ConnectionClosedError as e:
            if e.rcvd and e.rcvd.code == 4001:  # server closes right away with 4001
                _kick_pusher_bad.add(key)
                log.warning("Kick: key %s rejected (4001)", key)
                status("kick", "key rejected – trying the next one …")
                await asyncio.sleep(1)
                continue
            log.warning("Kick chat %s: %s", slug, e, exc_info=not isinstance(e, ValueError))
            delay = backoff.next()
            status("kick", "error: {error} – retrying in {seconds} s", error=e, seconds=delay)
            await asyncio.sleep(delay)
        except Exception as e:  # noqa: BLE001
            log.warning("Kick chat %s: %s", slug, e, exc_info=not isinstance(e, ValueError))
            delay = backoff.next()
            status("kick", "error: {error} – retrying in {seconds} s", error=e, seconds=delay)
            await asyncio.sleep(delay)
