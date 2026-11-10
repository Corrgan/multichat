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

from datetime import datetime, timezone

from .kick import fetch_kick_json
from .models import twitch_channel
from .youtube import LIVE_STARTED, live_now, youtube_target

log = logging.getLogger("multichat.chat")

PARALLEL = 3  # Kick and YouTube lookups at the same time


class Result(dict):
    """{source key: True live / False not live / None unknown (no login, lookup failed)};
    .since: {key: start of the stream, seconds since the epoch} for the live ones whose
    platform says when."""

    def __init__(self) -> None:
        super().__init__()
        self.since: dict[str, float] = {}


def started(text) -> float | None:
    """"2026-11-10T21:04:11Z" or "2026-11-10 21:04:11" (UTC unless it says otherwise) →
    seconds since the epoch; None if it is no plausible start of a running stream."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        when = datetime.fromisoformat(text.strip().replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    at = when.timestamp()
    now = datetime.now(timezone.utc).timestamp()
    return at if now - 7 * 86400 <= at <= now + 300 else None  # a stream runs hours, not weeks


async def check(targets: list[tuple[str, str, str]], twitch) -> Result:
    """targets: (source key, platform, channel as entered) → Result. Never raises for a
    single channel: a failed lookup is "unknown"."""
    out = Result()
    tw = [(key, value) for key, platform, value in targets if platform == "twitch"]
    if tw:
        live: dict[str, str] | None = None
        if twitch is not None and twitch.logged_in:
            try:
                live = dict(await twitch.live_streams([twitch_channel(value) for _key, value in tw]))
            except Exception as e:  # noqa: BLE001
                log.debug("Live check Twitch failed: %s", e)
        for key, value in tw:
            out[key] = None if live is None else twitch_channel(value) in live
            at = started(live.get(twitch_channel(value))) if live else None
            if at is not None:
                out.since[key] = at

    gate = asyncio.Semaphore(PARALLEL)

    async def one(key: str, platform: str, value: str) -> tuple[str, bool | None, float | None]:
        async with gate:
            try:
                if platform == "kick":
                    data = await asyncio.to_thread(fetch_kick_json, value)
                    stream = data.get("livestream")
                    info = stream if isinstance(stream, dict) else {}
                    return key, bool(stream), started(info.get("start_time") or info.get("created_at"))
                kind, target = youtube_target(value)
                if kind != "channel":
                    return key, None, None
                found = await asyncio.to_thread(live_now, target)
                return key, found is not None, started(LIVE_STARTED.get(target)) if found else None
            except ValueError:
                return key, False, None  # no such channel
            except Exception as e:  # noqa: BLE001
                log.debug("Live check %s failed: %s", key, e)
                return key, None, None

    others = [one(key, platform, value) for key, platform, value in targets if platform in ("kick", "youtube")]
    for key, state, at in await asyncio.gather(*others):
        out[key] = state
        if state and at is not None:
            out.since[key] = at
    return out
