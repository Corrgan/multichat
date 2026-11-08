# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Notices when the window stops responding (the event loop was busy) and logs it, so a
log shows what makes the window stutter. With the verbose log it also writes a short
summary every minute: messages per second, chat lines, animations, video frames."""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QObject, QTimer

log = logging.getLogger("multichat.perf")

TICK_MS = 100
STALL_MS = 250  # a delay this long is noticeable (mouse and scrolling stutter)
HICCUP_MS = 50


class Watchdog(QObject):
    def __init__(self, parent, stats=None) -> None:
        super().__init__(parent)
        self.stats = stats  # callable → dict for the minute summary
        self._last = time.monotonic()
        self._stalls = 0
        self._worst = 0.0
        self._hiccups = 0  # shorter delays (50–250 ms): many of them feel like a laggy mouse
        self._late_sum = 0.0
        self._ticks = 0
        self._summary_at = self._last + 60
        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        late = (now - self._last) * 1000 - TICK_MS
        self._last = now
        self._ticks += 1
        self._late_sum += max(0.0, late)
        if late > HICCUP_MS:
            self._hiccups += 1
        if late > STALL_MS:
            self._stalls += 1
            self._worst = max(self._worst, late)
            (log.warning if late > 1000 else log.info)("Window was busy for %d ms", late)
        if now >= self._summary_at:
            self._summary_at = now + 60
            if log.isEnabledFor(logging.DEBUG) and self.stats:
                try:
                    info = ", ".join(f"{k} {v}" for k, v in self.stats().items())
                except Exception as e:  # noqa: BLE001 – statistics must never break anything
                    info = f"unavailable ({e})"
                log.debug(
                    "Last minute: %d stalls (worst %d ms), %d delays over %d ms, average delay %.1f ms; %s",
                    self._stalls, self._worst, self._hiccups, HICCUP_MS, self._late_sum / max(1, self._ticks), info,
                )
            self._stalls, self._worst, self._hiccups, self._late_sum, self._ticks = 0, 0.0, 0, 0.0, 0
