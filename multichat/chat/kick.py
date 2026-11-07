# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Kick chat: Pusher WebSocket; the chatroom ID comes from the Kick website API."""
from __future__ import annotations

import asyncio
import json
import logging
import re

import websockets

from .models import ChatMessage, Deletion, Emit, Part, Status, with_extra_emotes as _extra

log = logging.getLogger("multichat.chat")


# Kick rotated its Pusher key in October 2026. The app asks Kick for the current
# credentials; these are only fallback values.
KICK_REALTIME_URL = "https://web.kick.com/api/v1/realtime/connection"
KICK_PUSHER_FALLBACKS = [("34bf7a0ff419a2a775b9", "us2"), ("32cbd69e4b950bf97679", "us2")]
_kick_pusher_bad: set[str] = set()  # keys Kick rejected with 4001


def kick_pusher_url(key: str, cluster: str) -> str:
    return f"wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0-rc2&flash=false"


def _find_pusher_creds(obj) -> tuple[str, str] | None:
    """Looks for app_key/cluster anywhere in the response (the format may change)."""
    if isinstance(obj, dict):
        if obj.get("app_key"):
            return str(obj["app_key"]), str(obj.get("cluster") or "us2")
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
_KICK_EMOTE_RE = re.compile(r"\[emote:(\d+):([^\]]*)\]")


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
    Kick sits behind Cloudflare, hence curl_cffi (browser fingerprint)."""
    url = f"https://kick.com/api/v2/channels/{slug}"
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


async def run_kick(channel: str, emit: Emit, status: Status, emotes=None) -> None:
    slug = channel.lower().strip()
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
                    evt = json.loads(raw)
                    name = evt.get("event", "")
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
                        else:
                            log.debug("Kick Pusher: %s %s", name, str(evt.get("data"))[:200])
                        continue
                    data = json.loads(evt.get("data") or "{}")
                    if name.endswith("ChatMessageEvent"):
                        sender = data.get("sender") or {}
                        content = data.get("content", "")
                        emit(
                            ChatMessage(
                                platform="kick",
                                channel=slug,
                                user=sender.get("username", "?"),
                                text=_KICK_EMOTE_RE.sub(r"\2", content),
                                parts=_extra(emotes, "kick", slug, kick_parts(content)),
                                color=(sender.get("identity") or {}).get("color"),
                                msg_id=str(data.get("id") or "") or None,
                                user_id=str(sender.get("id") or "") or None,
                            )
                        )
                    elif name.endswith("MessageDeletedEvent"):
                        mid = (data.get("message") or {}).get("id")
                        if mid:
                            emit(Deletion("kick", msg_id=str(mid)))
                    elif name.endswith("UserBannedEvent"):
                        uid = (data.get("user") or {}).get("id")
                        if uid:
                            emit(Deletion("kick", user_id=str(uid)))
                    elif name.endswith("ChatroomClearEvent"):
                        emit(Deletion("kick", clear_all=True))
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
            log.warning("Kick chat %s: %s", slug, e, exc_info=True)
            status("kick", "error: {error} – retrying in {seconds} s", error=e, seconds=10)
            await asyncio.sleep(10)
        except Exception as e:  # noqa: BLE001
            log.warning("Kick chat %s: %s", slug, e, exc_info=True)
            status("kick", "error: {error} – retrying in {seconds} s", error=e, seconds=10)
            await asyncio.sleep(10)
