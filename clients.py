# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Chat readers for Twitch, Kick and YouTube (read-only, no login).

Each reader runs as an asyncio task and calls `emit(event)` with either a
`ChatMessage` or a `Deletion` (message deleted / user banned). Status updates go to
`status(platform, template, **values)` with an English template; the UI translates it.
"""
from __future__ import annotations

import asyncio
import html
import itertools
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Union

import websockets

log = logging.getLogger("multichat.chat")

_uid = itertools.count(1)

# A message part: ("text", text, "") or ("emote", image_url, emote_name)
Part = tuple


@dataclass
class ChatMessage:
    platform: str  # "twitch" | "kick" | "youtube"
    channel: str
    user: str
    text: str
    parts: list = field(default_factory=list)
    color: str | None = None
    msg_id: str | None = None  # platform ID (for deleting)
    user_id: str | None = None  # platform ID (for timeout/ban)
    timestamp: float = field(default_factory=time.time)
    uid: int = field(default_factory=lambda: next(_uid))  # local ID
    deleted: bool = False

    def __post_init__(self) -> None:
        if not self.parts:
            self.parts = [("text", self.text, "")]


@dataclass
class Deletion:
    platform: str
    msg_id: str | None = None
    user_id: str | None = None
    clear_all: bool = False


Event = Union[ChatMessage, Deletion]
Emit = Callable[[Event], None]
Status = Callable[..., None]  # status(platform, template, **values)


# --------------------------------------------------------------------------- #
# Twitch: anonymous IRC over WebSocket
# --------------------------------------------------------------------------- #
TWITCH_EMOTE_URL = "https://static-cdn.jtvnw.net/emoticons/v2/{}/default/dark/2.0"


def _parse_tags(raw: str) -> dict[str, str]:
    out = {}
    for part in raw.split(";"):
        k, _, v = part.partition("=")
        out[k] = v.replace("\\s", " ").replace("\\:", ";").replace("\\\\", "\\")
    return out


def parse_irc(line: str) -> tuple[dict[str, str], str, str, list[str]]:
    """Splits an IRC line into (tags, prefix, command, params)."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw, _, line = line[1:].partition(" ")
        tags = _parse_tags(raw)
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, trailing = line.split(" :", 1)
    elif line.startswith(":"):
        line, trailing = "", line[1:]
    params = line.split()
    cmd = params.pop(0) if params else ""
    if trailing is not None:
        params.append(trailing)
    return tags, prefix, cmd, params


def twitch_parts(text: str, emotes: str | None) -> list[Part]:
    """Converts the `emotes` tag (e.g. "25:0-4,6-10/1902:12-16") into parts."""
    if not emotes:
        return [("text", text, "")]
    spans = []
    for entry in emotes.split("/"):
        eid, _, positions = entry.partition(":")
        for rng in positions.split(","):
            if "-" in rng:
                a, b = rng.split("-")
                spans.append((int(a), int(b), eid))
    spans.sort()
    out: list[Part] = []
    i = 0
    for a, b, eid in spans:
        if a < i:
            continue
        if a > i:
            out.append(("text", text[i:a], ""))
        out.append(("emote", TWITCH_EMOTE_URL.format(eid), text[a : b + 1]))
        i = b + 1
    if i < len(text):
        out.append(("text", text[i:], ""))
    return out


def _extra(emotes, platform: str, channel: str, parts: list[Part]) -> list[Part]:
    """Insert 7TV/BTTV/FFZ emotes if an EmoteStore was given."""
    return emotes.apply(platform, channel, parts) if emotes else parts


async def run_twitch(channel: str, emit: Emit, status: Status, emotes=None) -> None:
    channel = channel.lower().lstrip("#")
    while True:
        try:
            status("twitch", "connecting …")
            async with websockets.connect("wss://irc-ws.chat.twitch.tv:443") as ws:
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send("PASS SCHMOOPIIE")
                await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                await ws.send(f"JOIN #{channel}")
                status("twitch", "connected (#{channel})", channel=channel)
                async for raw in ws:
                    for line in str(raw).split("\r\n"):
                        if not line:
                            continue
                        tags, prefix, cmd, params = parse_irc(line)
                        if emotes and tags.get("room-id"):
                            emotes.schedule_channel("twitch", channel, tags["room-id"])
                        if cmd == "PING":
                            await ws.send("PONG :" + (params[-1] if params else "tmi.twitch.tv"))
                        elif cmd == "PRIVMSG" and len(params) >= 2:
                            text = params[1]
                            if text.startswith("\x01ACTION "):
                                text = text[8:].rstrip("\x01")
                            emit(
                                ChatMessage(
                                    platform="twitch",
                                    channel=channel,
                                    user=tags.get("display-name") or prefix.split("!")[0],
                                    text=text,
                                    parts=_extra(emotes, "twitch", channel, twitch_parts(text, tags.get("emotes"))),
                                    color=tags.get("color") or None,
                                    msg_id=tags.get("id"),
                                    user_id=tags.get("user-id"),
                                )
                            )
                        elif cmd == "CLEARMSG":
                            emit(Deletion("twitch", msg_id=tags.get("target-msg-id")))
                        elif cmd == "CLEARCHAT":
                            target = tags.get("target-user-id")
                            emit(Deletion("twitch", user_id=target) if target else Deletion("twitch", clear_all=True))
                        elif cmd == "RECONNECT":
                            raise ConnectionError("Twitch requested a reconnect")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("Twitch chat #%s: %s", channel, e, exc_info=True)
            status("twitch", "error: {error} – retrying in {seconds} s", error=e, seconds=5)
            await asyncio.sleep(5)


# --------------------------------------------------------------------------- #
# Kick: Pusher WebSocket. The chatroom ID comes from the Kick website API.
# --------------------------------------------------------------------------- #
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
    data = _fetch_kick_json(slug)
    return int(data["chatroom"]["id"]), str(data.get("user_id") or "")


def _fetch_kick_json(slug: str) -> dict:
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


# --------------------------------------------------------------------------- #
# YouTube: pytchat (live chat without an API key).
# Input: channel (@handle, channel URL, UC… ID) → the current live stream is found
# automatically. Alternatively a video URL/ID.
# --------------------------------------------------------------------------- #
YT_POLL = 60  # seconds between "is the channel live?" checks
YT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
# skips the EU cookie consent page (consent.youtube.com)
YT_COOKIES = {"SOCS": "CAI", "CONSENT": "YES+cb"}
_YT_VIDEO_RE = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/)([\w-]{11})(?![\w-])")
_YT_CHANNEL_RE = re.compile(r"youtube\.com/((?:@|channel/|c/|user/)[^/?#\s]+)")
_YT_CANON_RE = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"')
_YT_TITLE_RE = re.compile(r'<meta name="title" content="([^"]*)"')
_YT_LIVE_RE = re.compile(r'"isLiveNow"\s*:\s*true')


def youtube_target(value: str) -> tuple[str, str]:
    """→ ("video", video_id) or ("channel", channel_url)."""
    v = value.strip()
    m = _YT_VIDEO_RE.search(v)
    if m:
        return "video", m.group(1)
    m = _YT_CHANNEL_RE.search(v)
    if m:
        return "channel", "https://www.youtube.com/" + m.group(1)
    if v.startswith("@"):
        return "channel", "https://www.youtube.com/" + v.split()[0]
    if re.fullmatch(r"UC[\w-]{22}", v):
        return "channel", f"https://www.youtube.com/channel/{v}"
    if re.fullmatch(r"[\w-]{11}", v):
        return "video", v
    return "channel", "https://www.youtube.com/@" + v


def youtube_video_id(value: str) -> str:
    kind, target = youtube_target(value)
    return target if kind == "video" else value.strip()


def parse_live_page(page: str) -> tuple[str, str] | None:
    """Parses the /live page of a channel → (video_id, title) or None."""
    m = _YT_CANON_RE.search(page)
    if not m or not _YT_LIVE_RE.search(page):
        return None  # not live (or only a scheduled stream)
    t = _YT_TITLE_RE.search(page)
    return m.group(1), html.unescape(t.group(1)) if t else ""


async def find_live_video(channel_url: str) -> tuple[str, str] | None:
    import aiohttp

    async with aiohttp.ClientSession(
        trust_env=True, cookies=YT_COOKIES, headers=YT_HEADERS, timeout=aiohttp.ClientTimeout(total=20)
    ) as s:
        async with s.get(channel_url.rstrip("/") + "/live") as r:
            if r.status == 404:
                raise ValueError("YouTube channel not found")
            r.raise_for_status()
            if "consent." in (r.url.host or ""):
                raise RuntimeError("YouTube cookie consent page blocks the lookup")
            page = await r.text()
    return parse_live_page(page)


def _yt_parts(c) -> list[Part]:
    out: list[Part] = []
    for p in getattr(c, "messageEx", None) or [c.message]:
        if isinstance(p, dict):
            if p.get("url"):
                out.append(("emote", p["url"], p.get("txt", "")))
            else:
                out.append(("text", p.get("txt", ""), ""))
        else:
            out.append(("text", str(p), ""))
    if getattr(c, "type", "") in ("superChat", "superSticker") and getattr(c, "amountString", ""):
        out.insert(0, ("text", f"[💰 {c.amountString}] ", ""))
    return out


async def run_youtube(value: str, emit: Emit, status: Status, emotes=None) -> None:
    import pytchat  # type: ignore

    kind, target = youtube_target(value)
    while True:
        try:
            if kind == "video":
                vid, title = target, ""
            else:
                status("youtube", "looking for live stream …")
                found = await find_live_video(target)
                if not found:
                    nxt = time.strftime("%H:%M", time.localtime(time.time() + YT_POLL))
                    status("youtube", "not live – checking again at {time}", time=nxt)
                    await asyncio.sleep(YT_POLL)
                    continue
                vid, title = found
            status("youtube", "connecting …")
            chat = await asyncio.to_thread(pytchat.create, video_id=vid, interruptable=False)
            if title:
                status("youtube", "live: {title}", title=title)
            else:
                status("youtube", "connected ({channel})", channel=vid)
            while chat.is_alive():
                items = await asyncio.to_thread(lambda: list(chat.get().sync_items()))
                for c in items:
                    emit(
                        ChatMessage(
                            platform="youtube",
                            channel=vid,
                            user=c.author.name,
                            text=c.message,
                            parts=_extra(emotes, "youtube", vid, _yt_parts(c)),
                            msg_id=getattr(c, "id", None),
                            user_id=getattr(c.author, "channelId", None),
                        )
                    )
                await asyncio.sleep(1)
            if kind == "video":
                status("youtube", "stream ended or chat unreachable – retrying in {seconds} s", seconds=15)
            else:
                status("youtube", "stream ended – waiting for the next one")
            await asyncio.sleep(15)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("YouTube chat %s: %s", value, e, exc_info=True)
            status("youtube", "error: {error} – retrying in {seconds} s", error=e, seconds=15)
            await asyncio.sleep(15)
