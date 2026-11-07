# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Drittanbieter-Emotes: 7TV, BetterTTV und FrankerFaceZ.

Diese Emotes sind einfache Wörter im Text ("KEKW", "catJAM" …). Der Store lädt
die globalen Sets einmal und die Kanal-Sets, sobald die Kanal-ID bekannt ist
(Twitch: room-id, Kick: User-ID). `apply()` ersetzt passende Wörter durch Bilder.

Reihenfolge bei gleichen Namen: Kanal vor global; innerhalb gilt 7TV > BTTV > FFZ.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable

import aiohttp

from clients import Part

log = logging.getLogger("multichat.emotes")

_WS = re.compile(r"(\s+)")
EmoteMap = dict[str, str]  # Name -> Bild-URL


def _https(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


class EmoteStore:
    def __init__(self, session: Callable[[], aiohttp.ClientSession], formats: list[str] | None = None) -> None:
        self._session = session
        # bevorzugte Dateiformate für 7TV (Qt ohne WebP-Plugin → png/gif)
        self.formats = formats or ["webp", "png", "gif"]
        self.enabled = True
        self.global_: EmoteMap = {}
        self.global_loaded = False
        self.channels: dict[tuple[str, str], EmoteMap] = {}
        self._loading: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task] = set()

    # ---- Laden ----------------------------------------------------------- #
    async def _json(self, url: str):
        async with self._session().get(url, headers={"User-Agent": "MultiChat"}) as r:
            if r.status != 200:
                log.debug("Emotes %s → HTTP %s", url, r.status)
                return None
            return await r.json(content_type=None)

    @staticmethod
    async def _merge(*loaders) -> tuple[EmoteMap, dict[str, int]]:
        """Lädt parallel; spätere Quellen überschreiben frühere. Fehler werden ignoriert."""
        results = await asyncio.gather(*loaders, return_exceptions=True)
        merged: EmoteMap = {}
        counts = {}
        for name, res in zip(("ffz", "bttv", "7tv"), results):
            if isinstance(res, dict):
                merged.update(res)
                counts[name] = len(res)
        return merged, counts

    async def load_global(self) -> dict[str, int]:
        self.global_, counts = await self._merge(
            self._ffz_global(), self._bttv_global(), self._7tv_set("https://7tv.io/v3/emote-sets/global")
        )
        self.global_loaded = bool(counts)  # nichts erreichbar → beim nächsten Einschalten erneut versuchen
        return counts

    async def load_channel(self, platform: str, channel: str, ext_id: str) -> None:
        key = (platform, channel.lower())
        if not ext_id or key in self.channels or key in self._loading:
            return
        self._loading.add(key)
        try:
            if platform == "twitch":
                emap, _ = await self._merge(
                    self._ffz_room(ext_id), self._bttv_user(ext_id), self._7tv_user("twitch", ext_id)
                )
            else:
                emap, _ = await self._merge(_none(), _none(), self._7tv_user(platform, ext_id))
            self.channels[key] = emap
        finally:
            self._loading.discard(key)

    def schedule_channel(self, platform: str, channel: str, ext_id: str | None) -> None:
        """Aus einem laufenden Leser heraus aufrufen (gleicher Event-Loop)."""
        key = (platform, channel.lower())
        if not ext_id or key in self.channels or key in self._loading:
            return
        task = asyncio.ensure_future(self.load_channel(platform, channel, ext_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- 7TV ------------------------------------------------------------- #
    def _7tv_url(self, emote: dict) -> str | None:
        host = (emote.get("data") or {}).get("host") or {}
        base = host.get("url")
        if not base:
            return None
        names = {f.get("name") for f in host.get("files") or []}
        for fmt in self.formats:
            for size in ("2x", "1x"):
                if f"{size}.{fmt}" in names:
                    return f"{_https(base)}/{size}.{fmt}"
        return f"{_https(base)}/2x.webp"

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
        return {e["code"]: f"https://cdn.betterttv.net/emote/{e['id']}/2x" for e in emotes or [] if e.get("code")}

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
                url = urls.get("2") or urls.get("1")
                if e.get("name") and url:
                    out[e["name"]] = _https(url)
        return out

    async def _ffz_global(self) -> EmoteMap:
        data = await self._json("https://api.frankerfacez.com/v1/set/global") or {}
        return self._ffz_parse(data.get("sets"), [int(x) for x in data.get("default_sets") or []])

    async def _ffz_room(self, twitch_id: str) -> EmoteMap:
        data = await self._json(f"https://api.frankerfacez.com/v1/room/id/{twitch_id}") or {}
        return self._ffz_parse(data.get("sets"))

    # ---- Ersetzen -------------------------------------------------------- #
    def apply(self, platform: str, channel: str, parts: list[Part]) -> list[Part]:
        if not self.enabled or not (self.global_ or self.channels):
            return parts
        chan = self.channels.get((platform, channel.lower()), {})
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
