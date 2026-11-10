# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Quality "Automatic": steps a tile's quality down when the video keeps stalling, and
back up when it has been running smoothly.

mpv does not switch between the variants of a stream by itself, and Twitch runs through
Streamlink as one fixed variant – so the tile reloads the stream at another level (a short
interruption, like changing the quality by hand). This class only decides; it knows
nothing about Qt or mpv, the tile feeds it what it observes:

    loaded()   a stream started playing at self.level
    stall()    mpv began / stopped waiting for data ("paused-for-cache")
    poll()     every few seconds → the level to switch to, or None

Rules:
- down: two stalls within half a minute, one stall of several seconds, or much stalled time
  within a minute. Not counted: the first seconds after loading and Twitch ad breaks (the
  ad filter leaves a gap there).
- up: no stall for a while (three minutes at first) and the picture is smaller than the
  tile. An attempt that fails – a stall soon after – doubles the wait before the next one
  (up to half an hour), so the quality does not swing back and forth.
- Levels that would give the same picture (the stream has no such variant) are skipped
  when the variants are known; otherwise their played heights are learned by trying.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

LEVELS = [("360p", 360), ("480p", 480), ("720p", 720), ("source", 0)]  # low → high; 0 = best
START = "720p"
STARTUP_GRACE = 12.0  # seconds after loading in which stalls say nothing
AD_GRACE = 20.0  # … and after a Twitch ad break
STALL_WINDOW = 30.0
STALLS_FOR_DOWN = 2
LONG_STALL = 5.0  # one stall this long is enough
STALLED_WINDOW = 60.0
STALLED_FOR_DOWN = 8.0  # seconds stalled within STALLED_WINDOW
DOWN_COOLDOWN = 20.0  # after a switch, let the new level show what it does
UP_AFTER = 180.0  # smooth seconds before trying the next level up
UP_AFTER_MAX = 1800.0
PROBE_SECONDS = 120.0  # a step up that lasts this long has worked
HEIGHT_SETTLE = 10.0  # seconds after loading until the played height is trusted


def pick_height(heights, limit: int) -> int:
    """The variant height the stream lookup would choose for a level (0 = best)."""
    if not heights:
        return 0
    if not limit:
        return max(heights)
    return max([h for h in heights if h <= limit] or [min(heights)])


class AutoQuality:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.reason = ""  # why poll() last switched (for the log)
        self.reset()

    # ---- state ------------------------------------------------------------ #
    def reset(self, tile_height: int = 0) -> None:
        """Another channel, or "Automatic" was just chosen."""
        self.index = self._start_index(tile_height)
        self.heights: tuple[int, ...] = ()
        self._observed: dict[int, int] = {}  # level index → played height (variants unknown)
        self._up_wait = UP_AFTER
        self._probe: tuple[float, int] | None = None  # (time, index before) of the last step up
        self._moved: tuple[int, int] | None = None  # (index before, height played before the step)
        self._since = self.clock()
        self._ad_until = 0.0
        self._last_height = 0
        self._stalls: deque[list] = deque(maxlen=64)  # [start, end or None]

    @staticmethod
    def _start_index(tile_height: int) -> int:
        start = next(i for i, (name, _h) in enumerate(LEVELS) if name == START)
        if tile_height > 0:  # a small tile doesn't need 720p to begin with
            for i in range(start):
                if LEVELS[i][1] >= tile_height:
                    return i
        return start

    @property
    def level(self) -> str:
        return LEVELS[self.index][0]

    def loaded(self, heights=(), height: int = 0) -> None:
        """A stream started at self.level. heights: the variants it offers, if known."""
        heights = tuple(sorted({int(h) for h in heights if h}))
        if heights != self.heights:
            # A changed variant list invalidates the empirical map, including when
            # lookups switch between known variants and mpv/yt-dlp selection.
            self._observed.clear()
            self._moved = None
        self.heights = heights
        self._last_height = height
        self._since = self.clock()
        self._stalls.clear()

    def stall(self, stalled: bool, at: float | None = None) -> None:
        at = self.clock() if at is None else at
        if stalled:
            if not self._stalls or self._stalls[-1][1] is not None:
                self._stalls.append([at, None])
        elif self._stalls and self._stalls[-1][1] is None:
            self._stalls[-1][1] = at

    # ---- decisions ---------------------------------------------------------- #
    def _same_picture(self, a: int, b: int) -> bool:
        if self.heights:
            return pick_height(self.heights, LEVELS[a][1]) == pick_height(self.heights, LEVELS[b][1])
        return a in self._observed and self._observed[a] == self._observed.get(b)

    def _lower(self) -> int | None:
        for i in range(self.index - 1, -1, -1):
            if not self._same_picture(i, self.index):
                return i
        return None

    def _higher(self) -> int | None:
        for i in range(self.index + 1, len(LEVELS)):
            if not self._same_picture(i, self.index):
                return i
        return None

    def _played_limit(self) -> int:
        """Height of the picture at the current level (0: unknown/best)."""
        if self.heights:
            return pick_height(self.heights, LEVELS[self.index][1])
        return self._last_height or LEVELS[self.index][1]

    def poll(self, ads_active: bool = False, played_height: int = 0, tile_height: int = 0) -> str | None:
        """→ the level to reload at, or None. played_height: what mpv decodes right now;
        tile_height: device pixels of the visible tile (0: hidden or unknown)."""
        now = self.clock()
        if ads_active:
            self._ad_until = now + AD_GRACE
            return None
        settled = now - self._since >= HEIGHT_SETTLE
        if played_height and settled:
            if not self.heights:
                self._observed[self.index] = played_height
                if self._moved:
                    previous, before = self._moved
                    self._moved = None
                    if before and played_height == before:
                        # This cap is a duplicate, not an absolute end of the range.
                        # Keep the prior name without reloading; another cap may help.
                        self.index = previous
                        self._probe = None
            self._last_height = played_height

        quiet_from = max(self._since + STARTUP_GRACE, self._ad_until)
        # A stall can begin inside a grace period and continue long after it.
        # Count only its unprotected time, retaining the open player state.
        stalls = [(max(start, quiet_from), end) for start, end in self._stalls
                  if (end is None or end > quiet_from) and max(start, quiet_from) <= now]
        recent = [s for s in stalls if s[0] >= now - STALL_WINDOW]
        ongoing = stalls[-1] if stalls and stalls[-1][1] is None else None
        stalled = sum(min(s[1] or now, now) - max(s[0], now - STALLED_WINDOW) for s in stalls
                      if (s[1] or now) > now - STALLED_WINDOW)
        why = ""
        if len(recent) >= STALLS_FOR_DOWN:
            why = f"{len(recent)} stalls in {STALL_WINDOW:.0f} s"
        elif ongoing and now - ongoing[0] >= LONG_STALL:
            why = f"stalled for {now - ongoing[0]:.0f} s"
        elif stalled >= STALLED_FOR_DOWN:
            why = f"stalled {stalled:.0f} s of the last {STALLED_WINDOW:.0f} s"
        if why and now - self._since >= DOWN_COOLDOWN:
            if self._probe and now - self._probe[0] <= PROBE_SECONDS:
                self._up_wait = min(UP_AFTER_MAX, self._up_wait * 2)  # the step up was too much
            self._probe = None
            target = self._lower()
            if target is None:
                return None  # no lower picture: keep waiting, including the open stall
            return self._move(target, why)

        if self._stalls and self._stalls[-1][1] is None:
            return None  # waiting for data is never a reason to try a higher quality
        if self._probe and now - self._probe[0] > PROBE_SECONDS:
            self._probe = None
            self._up_wait = UP_AFTER  # it holds
        last_trouble = max([self._since, self._ad_until - AD_GRACE] + [s[1] or now for s in self._stalls])
        if now - last_trouble < self._up_wait:
            return None
        if tile_height <= 0:
            return None  # hidden (or size unknown): nobody is watching the extra pixels
        limit = self._played_limit()
        if not limit or limit >= tile_height:
            return None  # already as sharp as the tile can show
        target = self._higher()
        if target is None:
            return None
        self._probe = (now, self.index)
        return self._move(target, f"smooth for {now - last_trouble:.0f} s")

    def _move(self, target: int, why: str) -> str:
        self._moved = (self.index, self._last_height)
        self.reason = f"{LEVELS[self.index][0]} → {LEVELS[target][0]} ({why})"
        self.index = target
        self._since = self.clock()
        self._stalls.clear()
        return self.level
