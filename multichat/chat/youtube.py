# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""YouTube chat via pytchat (live chat without an API key).

Input: channel (@handle, channel URL, UC… ID) → the current live stream is found
automatically. Alternatively a video URL/ID.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time

from .models import Backoff, ChatMessage, Emit, Part, Status, with_extra_emotes as _extra

log = logging.getLogger("multichat.chat")


YT_POLL = 60  # seconds between "is the channel live?" checks
YT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.8",
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
    backoff = Backoff(first=15)
    while True:
        chat = None
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
            backoff.connected()
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
            delay = backoff.next()
            status("youtube", "error: {error} – retrying in {seconds} s", error=e, seconds=delay)
            await asyncio.sleep(delay)
        finally:
            if chat is not None:  # stops pytchat's background fetching (also on disconnect)
                try:
                    chat.terminate()
                except Exception:  # noqa: BLE001
                    pass
