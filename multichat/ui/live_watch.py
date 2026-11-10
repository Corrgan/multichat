# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Keeps track of which of the entered channels are live (see chat/live.py) – connected or
not. The main window shows it as a red dot next to the channel and on the group's tab."""
from __future__ import annotations

import logging
import os
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..chat import live

log = logging.getLogger("multichat")

CHECK_SECONDS = 120  # between two looks at a channel
MAX_BACKOFF_SECONDS = 900  # a channel whose lookup keeps failing (blocked, no login) is asked less often
SETTLE_MS = 2500  # after the last change of a channel field (not for every letter typed)
TICK_MS = 15_000
CLOCK_MS = 30_000  # how often the "live for …" displays are refreshed (they show minutes)
FORGET_AFTER = 2  # failed lookups in a row after which "live" is no longer claimed


class LiveWatch(QObject):
    changed = pyqtSignal()  # self.live differs from before

    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        self.live: dict[str, bool] = {}  # source key → live (missing: unknown)
        self.since: dict[str, float] = {}  # source key → start of the stream (epoch), where known
        self._due: dict[str, float] = {}
        self._errors: dict[str, int] = {}
        self._busy = False
        self._tick = QTimer(self, interval=TICK_MS)
        self._tick.timeout.connect(self.check)
        self._settle = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle.timeout.connect(self.check)
        self._clock = QTimer(self, interval=CLOCK_MS)  # the "live for 1:23" displays count on
        self._clock.timeout.connect(lambda: self.since and self.changed.emit())

    def start(self) -> None:
        if os.environ.get("MULTICHAT_NO_LIVE_CHECK"):
            return  # no lookups (also keeps the tests off the platforms)
        self._tick.start()
        self._settle.start()
        self._clock.start()

    def stop(self) -> None:
        self._tick.stop()
        self._settle.stop()
        self._clock.stop()

    def uptime(self, key: str) -> float | None:
        """Seconds the channel has been live; None if it is not live or the start is unknown."""
        at = self.since.get(key) if self.live.get(key) else None
        return max(0.0, time.time() - at) if at is not None else None

    def targets(self) -> dict[str, tuple[str, str]]:
        """{source key: (platform, channel)} of every channel entered in any group."""
        out: dict[str, tuple[str, str]] = {}
        for panel in self.win.panels:
            for row in panel.all_rows():
                key = row.key()
                if key:
                    out.setdefault(key, (row.platform, row.value()))
        return out

    def rows_changed(self) -> None:
        """A channel field was edited: forget channels that are gone, look at new ones soon."""
        present = self.targets().keys()
        gone = [key for key in {*self.live, *self._due, *self._errors} if key not in present]
        was_live = any(self.live.get(key) for key in gone)
        for key in gone:
            self.live.pop(key, None)
            self.since.pop(key, None)
            self._due.pop(key, None)
            self._errors.pop(key, None)
        if was_live:
            self.changed.emit()
        if self._tick.isActive():
            self._settle.start()

    def recheck(self, platform: str) -> None:
        """E.g. after a login or logout: ask this platform's channels again now."""
        for key in [k for k in self._due if k.startswith(platform + ":")]:
            del self._due[key]
            self._errors.pop(key, None)
        if self._tick.isActive():
            self._settle.start()

    def check(self) -> None:
        if self._busy or getattr(self.win, "_closing", False):
            return
        now = time.monotonic()
        due = [(key, platform, value) for key, (platform, value) in self.targets().items()
               if self._due.get(key, 0) <= now]
        if not due:
            return
        self._busy = True
        for key, _platform, _value in due:
            self._due[key] = now + CHECK_SECONDS  # also when the answer never comes
        self.win.run(live.check(due, self.win.accounts.get("twitch")), then=self._done, on_error=self._failed,
                     quiet=True)

    def _failed(self) -> None:
        self._busy = False

    def _done(self, results: dict) -> None:
        self._busy = False
        now = time.monotonic()
        present = self.targets().keys()
        before, since_before = dict(self.live), dict(self.since)
        since = getattr(results, "since", {})
        for key, state in results.items():
            if key not in present:
                continue  # the field was changed while the lookup ran
            if state is None:
                errors = self._errors[key] = self._errors.get(key, 0) + 1
                self._due[key] = now + min(MAX_BACKOFF_SECONDS, CHECK_SECONDS * 2 ** errors)
                if errors >= FORGET_AFTER:
                    self.live.pop(key, None)
                    self.since.pop(key, None)
            else:
                self._errors.pop(key, None)
                self._due[key] = now + CHECK_SECONDS
                self.live[key] = bool(state)
                if state and key in since:
                    self.since[key] = since[key]
                elif not state:
                    self.since.pop(key, None)
        if self.live != before or self.since != since_before:
            went_live = sorted(k for k, v in self.live.items() if v and not before.get(k))
            if went_live:
                log.info("Live now: %s", ", ".join(went_live))
            self.changed.emit()
