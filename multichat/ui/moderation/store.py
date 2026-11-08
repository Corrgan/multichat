# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Moderation data without widgets: notes/action log on disk, moderator status, recent
messages per user. All of it is bounded, so a long session in a busy chat can't grow
without limit."""
from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict, deque
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ...chat.models import ChatMessage, Deletion, user_key
from ...core.config import write_private
from ...core.constants import NAMES

log = logging.getLogger("multichat.moderation")

HISTORY_USERS = 3000  # users whose recent messages are kept for the user card
HISTORY_PER_USER = 100
HISTORY_TOTAL = 20000  # messages over all users (≈ a few MB)
NOTES_MAX_USERS = 5000
NOTE_MAX_CHARS = 4000
ACTIONS_PER_USER = 50
ACTION_MAX_CHARS = 200


def _clean_action(a) -> dict | None:
    """An action log entry from the file – only {"t": int, "a": str} is kept."""
    if not isinstance(a, dict):
        return None
    t, text = a.get("t"), a.get("a")
    if not isinstance(t, int) or isinstance(t, bool) or not 0 <= t < 2**32 or not isinstance(text, str):
        return None
    return {"t": t, "a": text[:ACTION_MAX_CHARS]}


class ModNotes:
    """Per-user notes and moderation log, stored as JSON next to the settings (0600)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: OrderedDict[str, dict] = OrderedDict()
        self._dirty = False
        self._timer: QTimer | None = None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("%s unreadable, starting without notes: %s", path, e)
            return
        if not isinstance(raw, dict):
            log.warning("%s: unexpected content, starting without notes", path)
            return
        for k, v in list(raw.items())[-NOTES_MAX_USERS:]:
            if isinstance(k, str) and len(k) <= 200 and isinstance(v, dict):
                actions = v.get("actions") if isinstance(v.get("actions"), list) else []
                self.data[k] = {
                    "name": str(v.get("name", ""))[:100],
                    "note": str(v.get("note", ""))[:NOTE_MAX_CHARS],
                    "actions": [a for a in map(_clean_action, actions) if a][-ACTIONS_PER_USER:],
                }

    @staticmethod
    def key(m: ChatMessage) -> str:
        return f"{m.platform}:{user_key(m)}"

    def get(self, m: ChatMessage) -> dict:
        return self.data.get(self.key(m)) or {"name": m.user, "note": "", "actions": []}

    def _entry(self, m: ChatMessage) -> dict:
        k = self.key(m)
        entry = self.data.pop(k, None) or {"name": m.user, "note": "", "actions": []}
        entry["name"] = m.user[:100]
        self.data[k] = entry  # most recently used last
        while len(self.data) > NOTES_MAX_USERS:
            self.data.popitem(last=False)
        return entry

    def set_note(self, m: ChatMessage, text: str) -> None:
        entry = self._entry(m)
        entry["note"] = text[:NOTE_MAX_CHARS]
        if not entry["note"] and not entry["actions"]:
            self.data.pop(self.key(m), None)
        self.save_later()

    def add_action(self, m: ChatMessage, action: str) -> None:
        entry = self._entry(m)
        entry["actions"] = (entry["actions"] + [{"t": int(time.time()), "a": action[:ACTION_MAX_CHARS]}])[-ACTIONS_PER_USER:]
        self.save_later()

    def save_later(self) -> None:
        """Typing a note saves once a second at most."""
        self._dirty = True
        if self._timer is None:
            self._timer = QTimer(singleShot=True, interval=1000)
            self._timer.timeout.connect(self.save)
        self._timer.start()

    def save(self) -> None:
        if self._timer:
            self._timer.stop()
        if not self._dirty:
            return
        try:
            write_private(self.path, dict(self.data))
            self._dirty = False
        except OSError as e:
            log.error("Saving %s failed: %s", self.path, e)


class ModState(QObject):
    """Moderator status per (platform, channel): True, False or None (unknown)."""

    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.state: dict[tuple[str, str], bool | None] = {}
        self.asked: set[tuple[str, str]] = set()

    def get(self, platform: str, channel: str) -> bool | None:
        return self.state.get((platform, channel.lower()))

    def set(self, platform: str, channel: str, value: bool | None) -> None:
        key = (platform, channel.lower())
        if self.state.get(key) is True and value is not True:
            return  # seen as moderator in the chat – an older API answer doesn't override that
        if self.state.get(key) != value:
            self.state[key] = value
            log.info("%s %s: moderator=%s", NAMES.get(platform, platform), channel, value)
            self.changed.emit()

    def reset(self, platform: str) -> None:
        for key in [k for k in self.state if k[0] == platform]:
            del self.state[key]
        self.asked = {k for k in self.asked if k[0] != platform}
        self.changed.emit()


class UserHistory:
    """Recent messages per user (for the user card), least recently active users dropped first."""

    def __init__(self) -> None:
        self.users: OrderedDict[tuple[str, str], deque] = OrderedDict()
        self.total = 0

    @staticmethod
    def key(m: ChatMessage) -> tuple[str, str]:
        return m.platform, user_key(m)

    def add(self, m: ChatMessage) -> None:
        key = self.key(m)
        hist = self.users.pop(key, None)
        if hist is None:
            hist = deque(maxlen=HISTORY_PER_USER)
        if len(hist) < HISTORY_PER_USER:
            self.total += 1
        hist.append(m)
        self.users[key] = hist
        while len(self.users) > HISTORY_USERS or self.total > HISTORY_TOTAL:
            _k, old = self.users.popitem(last=False)
            self.total -= len(old)

    def of(self, m: ChatMessage) -> list[ChatMessage]:
        return list(self.users.get(self.key(m)) or [])

    def latest(self, m: ChatMessage) -> ChatMessage:
        hist = self.users.get(self.key(m))
        return hist[-1] if hist else m

    def apply_deletion(self, ev: Deletion) -> bool:
        """Marks a user's messages deleted after a timeout/ban (also those older than the chat view)."""
        if not ev.user_id:
            return False
        hist = self.users.get((ev.platform, ev.user_id))
        changed = False
        for m in hist or ():
            if ev.source and m.source and m.source != ev.source:
                continue  # banned in another channel of the platform
            if not m.deleted:
                m.deleted = changed = True
        return changed
