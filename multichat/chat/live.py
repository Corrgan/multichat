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
from .models import twitch_channel, viewer_count
from .youtube import LIVE_STARTED, LIVE_VIEWERS, live_now, youtube_target

log = logging.getLogger("multichat.chat")

PARALLEL = 3  # Kick and YouTube lookups at the same time
TIMEOUT = 25  # seconds for one lookup – none may hold up the next round


class Result(dict):
    """{source key: True live / False not live / None unknown (no login, lookup failed)};
    .since: {key: start of the stream, seconds since the epoch} for the live ones whose
    platform says when; .viewers: {key: people watching} where the platform says."""

    def __init__(self) -> None:
        super().__init__()
        self.since: dict[str, float] = {}
        self.viewers: dict[str, int] = {}



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
        live: dict[str, tuple[str, int | None]] | None = None
        if twitch is not None and twitch.logged_in:
            logins = [twitch_channel(value) for _key, value in tw]
            try:
                if hasattr(twitch, "live_info"):
                    live = dict(await asyncio.wait_for(twitch.live_info(logins), TIMEOUT))
                else:
                    live = {k: (v, None) for k, v in (await asyncio.wait_for(twitch.live_streams(logins), TIMEOUT)).items()}
            except Exception as e:  # noqa: BLE001
                log.debug("Live check Twitch failed: %s", e)
        for key, value in tw:
            out[key] = None if live is None else twitch_channel(value) in live
            at, viewers = live.get(twitch_channel(value), ("", None)) if live else ("", None)
            if started(at) is not None:
                out.since[key] = started(at)
            if viewers is not None:
                out.viewers[key] = viewers

    gate = asyncio.Semaphore(PARALLEL)

    async def one(key: str, platform: str, value: str) -> tuple[str, bool | None, float | None, int | None]:
        async with gate:
            try:
                if platform == "kick":
                    data = await asyncio.wait_for(asyncio.to_thread(fetch_kick_json, value), TIMEOUT)
                    stream = data.get("livestream")
                    info = stream if isinstance(stream, dict) else {}
                    return (key, bool(stream), started(info.get("start_time") or info.get("created_at")),
                            viewer_count(info.get("viewer_count")))
                kind, target = youtube_target(value)
                if kind != "channel":
                    return key, None, None, None
                found = await asyncio.wait_for(asyncio.to_thread(live_now, target), TIMEOUT)
                if not found:
                    return key, False, None, None
                return key, True, started(LIVE_STARTED.get(target)), LIVE_VIEWERS.get(target)
            except ValueError:
                return key, False, None, None  # no such channel
            except Exception as e:  # noqa: BLE001
                log.debug("Live check %s failed: %s", key, e)
                return key, None, None, None

    others = [one(key, platform, value) for key, platform, value in targets if platform in ("kick", "youtube")]
    for key, state, at, viewers in await asyncio.gather(*others):
        out[key] = state
        if state and at is not None:
            out.since[key] = at
        if state and viewers is not None:
            out.viewers[key] = viewers
    return out
