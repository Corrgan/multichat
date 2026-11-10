# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Staying close to "now" in a live stream.

The chat arrives at once, the video seconds later. Part of that delay is the platform's,
part is ours: how far behind the newest data the player runs. mpv never shortens that
distance by itself – it starts a few segments back, and every wait for data adds to it
for good. This controller watches how much is buffered ahead and plays a little faster
while there is more than needed (mpv corrects the pitch), until the distance is back at
the reserve.

What counts is the *smallest* buffer over a window longer than a segment: HLS data
arrives one segment at a time, so the buffer swings between "almost empty" just before a
segment and "a whole segment" right after. Going by a single reading would drain it
whenever a segment has just arrived.

No Qt and no mpv in here: the tile feeds samples and applies the speed.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

WINDOW = 12.0  # seconds the smallest buffer is taken over (longer than a segment anywhere)
RESERVE = 2.0  # seconds to keep buffered at the worst moment
SLACK = 1.5  # this much more than the reserve is left alone (no speed changes for nothing)
# (seconds above the reserve, speed): the further behind, the faster
SPEEDS = ((6.0, 1.25), (1.5, 1.10), (0.0, 1.05))
NORMAL = 1.0


class CatchUp:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        """A stream was (re)loaded, or the video just waited for data: start measuring
        again at normal speed."""
        self.speed = NORMAL
        self._since = self.clock()
        self._samples: deque[tuple[float, float]] = deque(maxlen=512)

    def sample(self, buffered, at: float | None = None) -> None:
        """buffered: seconds mpv holds ahead of the picture (demuxer-cache-duration)."""
        if isinstance(buffered, (int, float)) and buffered >= 0:
            self._samples.append((self.clock() if at is None else at, float(buffered)))

    def lowest(self) -> float | None:
        """Smallest buffer of the last WINDOW seconds; None until a whole window is known."""
        now = self.clock()
        while self._samples and self._samples[0][0] < now - WINDOW:
            self._samples.popleft()
        if now - self._since < WINDOW or not self._samples:
            return None
        return min(value for _at, value in self._samples)

    def poll(self) -> float | None:
        """→ the speed to set now, or None if it stays as it is."""
        low = self.lowest()
        if low is None:
            return self._set(NORMAL)
        above = low - RESERVE
        if self.speed == NORMAL and above <= SLACK:
            return None
        if above <= 0:
            return self._set(NORMAL)
        return self._set(next(speed for limit, speed in SPEEDS if above >= limit))

    def _set(self, speed: float) -> float | None:
        if speed == self.speed:
            return None
        self.speed = speed
        return speed
