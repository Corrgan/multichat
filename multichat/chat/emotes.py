# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Third-party emotes: 7TV, BetterTTV and FrankerFaceZ.

These emotes are plain words in the text ("KEKW", "catJAM" …). The store loads the
global sets once and the channel sets as soon as the channel ID is known
(Twitch: room-id, Kick: user ID). `apply()` replaces matching words with images.

Precedence for equal names: channel before global; within each, 7TV > BTTV > FFZ.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Callable

import aiohttp

from .models import Part


log = logging.getLogger("multichat.emotes")

_WS = re.compile(r"(\s+)")
EmoteMap = dict[str, str]  # name -> image URL


RETRY_EMPTY = 600  # s until a channel whose emotes could not be loaded is tried again
CHANNEL_TTL = 6 * 3600
MAX_CHANNELS = 64
MAX_CHANNEL_EMOTES = 4096
MAX_GLOBAL_EMOTES = 8192
MAX_CACHED_EMOTES = 32768
MAX_LOADS = 8
MAX_PENDING_LOADS = 64
MAX_JSON_BYTES = 4 * 1024 * 1024
_EXT_ID_RE = re.compile(r"[0-9]{1,20}")


def _https(url: str) -> str | None:
    """Protocol-relative → https; anything that isn't https is dropped (the URLs come from
    third-party APIs and end up in the chat view and the OBS overlay)."""
    url = "https:" + url if url.startswith("//") else url
    return url if url.startswith("https://") else None


class EmoteStore:
    def __init__(self, session: Callable[[], aiohttp.ClientSession], formats: list[str] | None = None) -> None:
        self._session = session
        # preferred file formats for 7TV (Qt without WebP plugin → png/gif)
        self.formats = formats or ["webp", "gif", "png"]
        self.enabled = True
        self.global_: EmoteMap = {}
        self.global_loaded = False
        self.channels: OrderedDict[tuple[str, str], EmoteMap] = OrderedDict()
        self._lock = threading.RLock()  # reader loop writes, Qt completion reads
        self._loaded_at: dict[tuple[str, str], float] = {}
        self._empty_since: dict[tuple[str, str], float] = {}
        self._loading: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task] = set()
        self._scheduled: set[tuple[str, str]] = set()
        self._load_slots = asyncio.Semaphore(MAX_LOADS)

    # ---- Loading ---------------------------------------------------------- #
    async def _json(self, url: str):
        async with self._session().get(url, headers={"User-Agent": "MultiChat"}) as r:
            if r.status != 200:
                log.debug("Emotes %s → HTTP %s", url, r.status)
                return None
            if r.content_length is not None and r.content_length > MAX_JSON_BYTES:
                raise ValueError("emote metadata exceeds byte limit")
            body = bytearray()
            async for chunk in r.content.iter_chunked(65536):
                body.extend(chunk)
                if len(body) > MAX_JSON_BYTES:
                    raise ValueError("emote metadata exceeds byte limit")
            return json.loads(body)

    @staticmethod
    def _bounded(emap: EmoteMap, limit: int) -> EmoteMap:
        out = {}
        for name, url in emap.items():
            if (isinstance(name, str) and 0 < len(name) <= 100
                    and isinstance(url, str) and len(url) <= 2048 and _https(url)):
                out[name] = url
                if len(out) >= limit:
                    break
        return out

    def _prune(self, now: float) -> None:
        """Called with the lock held. Evict expired and least recently used sets."""
        for key in list(self.channels):
            if now - self._loaded_at.get(key, now) >= CHANNEL_TTL:
                self.channels.pop(key, None)
        total = sum(map(len, self.channels.values()))
        while len(self.channels) > MAX_CHANNELS or total > MAX_CACHED_EMOTES:
            _, removed = self.channels.popitem(last=False)
            total -= len(removed)
        for metadata in (self._loaded_at, self._empty_since):
            for key in list(metadata):
                if key not in self.channels:
                    metadata.pop(key, None)

    def _channel(self, key: tuple[str, str]) -> EmoteMap:
        with self._lock:
            self._prune(time.monotonic())
            emap = self.channels.get(key, {})
            if key in self.channels:
                self.channels.move_to_end(key)
            return emap  # stored maps are replaced, never mutated

    @staticmethod
    async def _merge(*loaders) -> tuple[EmoteMap, dict[str, int]]:
        """Loads in parallel; later sources override earlier ones. Errors are ignored."""
        results = await asyncio.gather(*loaders, return_exceptions=True)
        merged: EmoteMap = {}
        counts = {}
        for name, res in zip(("ffz", "bttv", "7tv"), results):
            if isinstance(res, dict):
                merged.update(res)
                counts[name] = len(res)
        return merged, counts

    async def load_global(self) -> dict[str, int]:
        emap, counts = await self._merge(
            self._ffz_global(), self._bttv_global(), self._7tv_set("https://7tv.io/v3/emote-sets/global")
        )
        with self._lock:
            self.global_ = self._bounded(emap, MAX_GLOBAL_EMOTES)
            self.global_loaded = bool(counts)  # nothing reachable → try again next time
        return counts

    def _needs_load(self, key: tuple[str, str], ext_id: str | None) -> bool:
        with self._lock:
            self._prune(time.monotonic())
            if not ext_id or not _EXT_ID_RE.fullmatch(str(ext_id)) or key in self._loading:
                return False
            if key not in self.channels:
                return True
            return not self.channels[key] and time.monotonic() - self._empty_since.get(key, 0) > RETRY_EMPTY

    async def load_channel(self, platform: str, channel: str, ext_id: str) -> None:
        async with self._load_slots:
            await self._load_channel(platform, channel, ext_id)

    async def _load_channel(self, platform: str, channel: str, ext_id: str) -> None:
        key = (platform, channel.lower())
        with self._lock:
            if not self._needs_load(key, ext_id):
                return
            self._loading.add(key)
        try:
            if platform == "twitch":
                emap, _ = await self._merge(
                    self._ffz_room(ext_id), self._bttv_user(ext_id), self._7tv_user("twitch", ext_id)
                )
            else:
                emap, _ = await self._merge(_none(), _none(), self._7tv_user(platform, ext_id))
            with self._lock:
                self.channels[key] = self._bounded(emap, MAX_CHANNEL_EMOTES)
                self.channels.move_to_end(key)
                self._loaded_at[key] = time.monotonic()
                if not self.channels[key]:
                    self._empty_since[key] = time.monotonic()
                else:
                    self._empty_since.pop(key, None)
                self._prune(time.monotonic())
        finally:
            with self._lock:
                self._loading.discard(key)

    def schedule_channel(self, platform: str, channel: str, ext_id: str | None) -> None:
        """Call from within a running reader (same event loop)."""
        key = (platform, channel.lower())
        if key in self._scheduled or len(self._tasks) >= MAX_PENDING_LOADS or not self._needs_load(key, ext_id):
            return
        self._scheduled.add(key)
        task = asyncio.ensure_future(self.load_channel(platform, channel, ext_id))
        self._tasks.add(task)
        def done(future):
            self._tasks.discard(future)
            self._scheduled.discard(key)
            if not future.cancelled() and future.exception() is not None:
                log.debug("Channel emote load failed: %s", future.exception())
        task.add_done_callback(done)

    # ---- 7TV ------------------------------------------------------------- #
    def _7tv_url(self, emote: dict) -> str | None:
        host = (emote.get("data") or {}).get("host") or {}
        base = _https(str(host.get("url") or ""))
        if not base:
            return None
        names = {f.get("name") for f in host.get("files") or []}
        for fmt in self.formats:
            for size in ("2x", "1x"):
                if f"{size}.{fmt}" in names:
                    return f"{base}/{size}.{fmt}"
        return f"{base}/2x.webp"

    def _7tv_parse(self, emotes) -> EmoteMap:
        out = {}
        for e in emotes or []:
            url = self._7tv_url(e)
            if e.get("name") and url:
                out[e["name"]] = url
        return out

    async def _7tv_set(self, url: str) -> EmoteMap:
        data = await self._json(url)
        return self._7tv_parse((data or {}).get("emotes"))

    async def _7tv_user(self, platform: str, ext_id: str) -> EmoteMap:
        data = await self._json(f"https://7tv.io/v3/users/{platform}/{ext_id}")
        return self._7tv_parse(((data or {}).get("emote_set") or {}).get("emotes"))

    # ---- BetterTTV ------------------------------------------------------- #
    @staticmethod
    def _bttv_parse(emotes) -> EmoteMap:
        return {
            e["code"]: f"https://cdn.betterttv.net/emote/{e['id']}/2x"
            for e in emotes or []
            if e.get("code") and re.fullmatch(r"[0-9a-f]{24}", str(e.get("id", "")))
        }

    async def _bttv_global(self) -> EmoteMap:
        return self._bttv_parse(await self._json("https://api.betterttv.net/3/cached/emotes/global"))

    async def _bttv_user(self, twitch_id: str) -> EmoteMap:
        data = await self._json(f"https://api.betterttv.net/3/cached/users/twitch/{twitch_id}") or {}
        return self._bttv_parse((data.get("channelEmotes") or []) + (data.get("sharedEmotes") or []))

    # ---- FrankerFaceZ ---------------------------------------------------- #
    @staticmethod
    def _ffz_parse(sets: dict, only: list | None = None) -> EmoteMap:
        out = {}
        for sid, s in (sets or {}).items():
            if only is not None and int(sid) not in only:
                continue
            for e in s.get("emoticons") or []:
                urls = e.get("urls") or {}
                url = _https(str(urls.get("2") or urls.get("1") or ""))
                if e.get("name") and url:
                    out[e["name"]] = url
        return out

    async def _ffz_global(self) -> EmoteMap:
        data = await self._json("https://api.frankerfacez.com/v1/set/global") or {}
        return self._ffz_parse(data.get("sets"), [int(x) for x in data.get("default_sets") or []])

    async def _ffz_room(self, twitch_id: str) -> EmoteMap:
        data = await self._json(f"https://api.frankerfacez.com/v1/room/id/{twitch_id}") or {}
        return self._ffz_parse(data.get("sets"))

    # ---- Replacing -------------------------------------------------------- #
    def available(self, platform: str, channel: str) -> dict[str, str]:
        """7TV/BTTV/FFZ emotes of this channel and the global ones: code → image URL (for
        the emote suggestions while typing)."""
        if not self.enabled:
            return {}
        return {**self.global_, **self._channel((platform, channel.lower()))}

    def apply(self, platform: str, channel: str, parts: list[Part]) -> list[Part]:
        if not self.enabled or not (self.global_ or self.channels):
            return parts
        chan = self._channel((platform, channel.lower()))
        out: list[Part] = []
        for kind, value, alt in parts:
            if kind != "text":
                out.append((kind, value, alt))
                continue
            buf = ""
            for tok in _WS.split(value):
                url = (chan.get(tok) or self.global_.get(tok)) if tok and not tok.isspace() else None
                if url:
                    if buf:
                        out.append(("text", buf, ""))
                        buf = ""
                    out.append(("emote", url, tok))
                else:
                    buf += tok
            if buf:
                out.append(("text", buf, ""))
        return out


async def _none() -> EmoteMap:
    return {}
