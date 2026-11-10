# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Is a channel live right now? – for the indicators next to the channel fields and on the
group tabs. Works without connecting the chat.

- Twitch: one request for all channels (Helix "streams"); needs the Twitch login.
- Kick: the channel info the chat and the video tile use as well (shared for a few seconds).
- YouTube: the channel's /live page, shared with chat and video tile. A single video
  (URL or ID) has no "channel is live" state and stays unknown.
"""
from __future__ import annotations

import asyncio
import logging

from .kick import fetch_kick_json
from .models import twitch_channel
from .youtube import live_now, youtube_target

log = logging.getLogger("multichat.chat")

PARALLEL = 3  # Kick and YouTube lookups at the same time


async def check(targets: list[tuple[str, str, str]], twitch) -> dict[str, bool | None]:
    """targets: (source key, platform, channel as entered) → {key: True live / False not
    live / None unknown (no login, lookup failed)}. Never raises for a single channel."""
    out: dict[str, bool | None] = {}
    tw = [(key, value) for key, platform, value in targets if platform == "twitch"]
    if tw:
        live: set[str] | None = None
        if twitch is not None and twitch.logged_in:
            try:
                live = await twitch.live_channels([twitch_channel(value) for _key, value in tw])
            except Exception as e:  # noqa: BLE001
                log.debug("Live check Twitch failed: %s", e)
        for key, value in tw:
            out[key] = None if live is None else twitch_channel(value) in live

    gate = asyncio.Semaphore(PARALLEL)

    async def one(key: str, platform: str, value: str) -> tuple[str, bool | None]:
        async with gate:
            try:
                if platform == "kick":
                    data = await asyncio.to_thread(fetch_kick_json, value)
                    return key, bool(data.get("livestream"))
                kind, target = youtube_target(value)
                if kind != "channel":
                    return key, None
                return key, await asyncio.to_thread(live_now, target) is not None
            except ValueError:
                return key, False  # no such channel
            except Exception as e:  # noqa: BLE001
                log.debug("Live check %s failed: %s", key, e)
                return key, None

    others = [one(key, platform, value) for key, platform, value in targets if platform in ("kick", "youtube")]
    out.update(await asyncio.gather(*others))
    return out
