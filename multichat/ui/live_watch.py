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

# Seconds between two looks at a channel. Twitch is one small request for all channels, so
# it is asked often: Twitch's own answer already lags a stream's start by a minute or more.
# Kick and YouTube cost a request (YouTube: a whole page) per channel.
CHECK_SECONDS = {"twitch": 30, "kick": 120, "youtube": 120}
MAX_BACKOFF_SECONDS = 300  # a channel whose lookup keeps failing (blocked, no login) is asked less often
SETTLE_MS = 2500  # after the last change of a channel field (not for every letter typed)
TICK_MS = 5000
BUSY_SECONDS = 60  # a lookup that has not answered by then no longer holds up the next one
HOLD_SECONDS = 300  # a video that plays proves "live"; a lagging "not live" answer is ignored that long
FORGET_AFTER = 2  # failed lookups in a row after which "live" is no longer claimed


class LiveWatch(QObject):
    changed = pyqtSignal()  # self.live differs from before

    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        self.live: dict[str, bool] = {}  # source key → live (missing: unknown)
        self._due: dict[str, float] = {}
        self._errors: dict[str, int] = {}
        self._hold: dict[str, float] = {}  # source key → until when a playing video vouches for "live"
        self._busy: dict[str, float] = {}  # platform → when its running lookup started
        self._tick = QTimer(self, interval=TICK_MS)
        self._tick.timeout.connect(self.check)
        self._settle = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle.timeout.connect(self.check)

    def start(self) -> None:
        if os.environ.get("MULTICHAT_NO_LIVE_CHECK"):
            return  # no lookups (also keeps the tests off the platforms)
        self._tick.start()
        self._settle.start()

    def stop(self) -> None:
        self._tick.stop()
        self._settle.stop()

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
        gone = [key for key in {*self.live, *self._due, *self._errors, *self._hold} if key not in present]
        was_live = any(self.live.get(key) for key in gone)
        for key in gone:
            self.live.pop(key, None)
            self._due.pop(key, None)
            self._errors.pop(key, None)
            self._hold.pop(key, None)
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

    def _playing(self) -> set[str]:
        """Channels whose video tile plays right now – the quickest proof that they are live
        (the stream lookup of a tile is real time, the platforms' "is live" answers lag)."""
        keys = set()
        for panel in self.win.panels:
            for tile in getattr(getattr(panel, "video", None), "tiles", {}).values():
                if getattr(tile, "state", "") == "playing":
                    keys.add(tile.key)
        return keys

    def check(self) -> None:
        if getattr(self.win, "_closing", False):
            return
        now = time.monotonic()
        targets = self.targets()
        before = dict(self.live)
        for key in self._playing() & targets.keys():
            self.live[key] = True
            self._hold[key] = now + HOLD_SECONDS
        self._announce(before)
        # each platform on its own: a slow Kick or YouTube lookup must not delay Twitch
        for platform in CHECK_SECONDS:
            if now - self._busy.get(platform, -BUSY_SECONDS) < BUSY_SECONDS:
                continue
            due = [(key, p, value) for key, (p, value) in targets.items()
                   if p == platform and self._due.get(key, 0) <= now]
            if not due:
                continue
            self._busy[platform] = now
            for key, _p, _value in due:
                self._due[key] = now + CHECK_SECONDS[platform]  # also when the answer never comes
            self.win.run(live.check(due, self.win.accounts.get("twitch")),
                         then=lambda results, platform=platform: self._done(platform, results),
                         on_error=lambda platform=platform: self._busy.pop(platform, None), quiet=True)

    def _done(self, platform: str, results: dict) -> None:
        self._busy.pop(platform, None)
        now = time.monotonic()
        present = self.targets().keys()
        before = dict(self.live)
        for key, state in results.items():
            if key not in present:
                continue  # the field was changed while the lookup ran
            every = CHECK_SECONDS[platform]
            if state is None:
                errors = self._errors[key] = self._errors.get(key, 0) + 1
                self._due[key] = now + min(MAX_BACKOFF_SECONDS, every * 2 ** errors)
                if errors >= FORGET_AFTER and self._hold.get(key, 0) <= now:
                    self.live.pop(key, None)
            else:
                self._errors.pop(key, None)
                self._due[key] = now + every
                if state or self._hold.get(key, 0) <= now:  # "not live" while its video plays: the answer lags
                    self.live[key] = bool(state)
        self._announce(before)

    def _announce(self, before: dict) -> None:
        if self.live != before:
            went_live = sorted(k for k, v in self.live.items() if v and not before.get(k))
            if went_live:
                log.info("Live now: %s", ", ".join(went_live))
            self.changed.emit()
