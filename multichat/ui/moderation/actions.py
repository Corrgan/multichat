# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Moderation controller: selection, moderator check, the actions themselves and the
name menu. Shared by the action bar, the user card and the chat's context menu."""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox

from ...chat.models import ChatMessage, Deletion
from ...core.constants import NAMES
from ...core.i18n import _
from .store import ModNotes, ModState, UserHistory
from .util import profile_url

log = logging.getLogger("multichat.moderation")

TIMEOUTS = [(60, "Timeout 1 min"), (600, "Timeout 10 min"), (3600, "Timeout 1 h")]
MOD_ROLES = {"moderator", "broadcaster"}
INFO_TTL = 300  # seconds a user's account info is reused before asking the platform again


class ModActions(QObject):
    selection_changed = pyqtSignal()
    history_changed = pyqtSignal()

    def __init__(self, win, notes: ModNotes) -> None:
        super().__init__()
        self.win = win  # main window: accounts, run(), apply_deletion(), mention(), login(), view
        self.notes = notes
        self.state = ModState()
        self.history = UserHistory()
        self.selected: ChatMessage | None = None  # latest message of the selected user
        self.card = None  # UserCard, created on first use
        self._info: dict[tuple[str, str], tuple[float, dict]] = {}

    # ---- incoming messages ---------------------------------------------- #
    def observe(self, m: ChatMessage) -> None:
        self.history.add(m)
        acc = self.win.accounts[m.platform]
        if acc.logged_in:
            if self._is_own(acc, m) and MOD_ROLES & set(m.badges):
                self.state.set(m.platform, m.channel, True)
            self._ask(m.platform, m.channel)
        if self.selected and self.history.key(self.selected) == self.history.key(m):
            self.selected = m
            self.history_changed.emit()

    @staticmethod
    def _is_own(acc, m: ChatMessage) -> bool:
        # IDs where both are known – display names aren't unique (e.g. YouTube channel titles)
        if m.user_id and acc.user_id:
            return m.user_id == acc.user_id
        return bool(acc.user_name) and m.user.lower() == acc.user_name.lower()

    def _ask(self, platform: str, channel: str) -> None:
        key = (platform, channel.lower())
        if key in self.state.asked:
            return
        self.state.asked.add(key)
        acc = self.win.accounts[platform]

        async def ask():
            try:
                return await acc.is_moderator(channel)
            except Exception as e:  # noqa: BLE001 – unknown is fine, actions stay visible
                log.info("%s: moderator check failed: %s", NAMES[platform], e)
                return None

        self.win.run(ask(), then=lambda value: self.state.set(platform, channel, value))

    def account_changed(self, platform: str) -> None:
        """Login or logout: the answers so far belong to the other account."""
        self.state.reset(platform)
        self._info = {k: v for k, v in self._info.items() if k[0] != platform}
        self.selection_changed.emit()

    def apply_deletion(self, ev: Deletion) -> None:
        if self.history.apply_deletion(ev):
            self.history_changed.emit()

    # ---- selection -------------------------------------------------------- #
    def select(self, m: ChatMessage | None) -> None:
        m = self.history.latest(m) if m is not None else None  # newest message of that user
        old = self.win.view.selected
        self.selected = m
        new = self.history.key(m) if m else None
        self.win.set_selected_user(new)  # highlighted in every group
        if new != old:  # move the highlight: only the lines of the old and the new user change
            keys = {k for k in (old, new) if k}
            self.win.update_lines([x for x in self.win.messages.values() if self.history.key(x) in keys])
        self.selection_changed.emit()
        if m and self.card and self.card.isVisible():
            self.card.show_user(m)

    def messages_of(self, m: ChatMessage) -> list[ChatMessage]:
        return self.history.of(m)

    def last_deletable(self, m: ChatMessage) -> ChatMessage | None:
        for msg in reversed(self.history.of(m)):
            if msg.msg_id and not msg.deleted:
                return msg
        return None

    # ---- permissions ------------------------------------------------------ #
    def logged_in(self, m: ChatMessage) -> bool:
        return self.win.accounts[m.platform].logged_in

    def is_mod(self, m: ChatMessage) -> bool | None:
        return self.state.get(m.platform, m.channel)

    def can_moderate(self, m: ChatMessage) -> bool:
        """Logged in and not known to lack moderator rights there."""
        return self.logged_in(m) and self.is_mod(m) is not False

    def known_not_mod(self, m: ChatMessage) -> bool:
        return self.logged_in(m) and self.is_mod(m) is False

    def mod_hint(self, m: ChatMessage) -> str:
        if not self.logged_in(m):
            return _("Log in to {platform} to moderate").format(platform=NAMES[m.platform])
        if self.is_mod(m) is False:
            return _("You are not a moderator in this channel")
        return ""

    # ---- account info (user card) ------------------------------------------ #
    def user_info(self, m: ChatMessage, then) -> None:
        """Calls then(info) with the platform's facts about the user (cached a few minutes)."""
        key = self.history.key(m)
        hit = self._info.get(key)
        if hit and hit[0] > time.monotonic():
            then(hit[1])
            return
        acc = self.win.accounts[m.platform]
        if m.platform != "kick" and not acc.logged_in:  # Kick's user info works without login
            then({"_hint": _("log in for more")})
            return

        async def fetch():
            try:
                info = await acc.user_info(m)
                return info if isinstance(info, dict) else {}
            except Exception as e:  # noqa: BLE001 – the card stays usable without it
                log.info("%s user info failed: %s", NAMES[m.platform], e)
                return {"_hint": _("not available"), "_failed": True}

        def done(info: dict) -> None:
            if not info.get("_failed"):
                self._info[key] = (time.monotonic() + INFO_TTL, info)
                while len(self._info) > 500:
                    self._info.pop(next(iter(self._info)))
            then(info)

        self.win.run(fetch(), then=done)

    # ---- actions ---------------------------------------------------------- #
    def moderate(self, msg: ChatMessage, action: str, seconds: int = 0) -> None:
        acc = self.win.accounts[msg.platform]
        ev = None
        if action == "delete":
            coro = acc.delete(msg)
            ev = Deletion(msg.platform, msg_id=msg.msg_id, source=msg.source)
            label = _("Message deleted")
            entry = f"delete: {msg.text[:80]}"
        elif action == "timeout":
            coro = acc.timeout(msg, seconds)
            ev = Deletion(msg.platform, user_id=msg.user_id, source=msg.source)  # only this channel
            label = _("Timeout for {user} ({minutes} min)").format(user=msg.user, minutes=seconds // 60)
            entry = f"timeout {seconds // 60} min"
        elif action == "unban":
            coro = acc.unban(msg)
            label = _("{user} unbanned").format(user=msg.user)
            entry = "unban"
        elif action == "ban":
            answer = QMessageBox.question(
                self.win,
                _("Ban"),
                _("Permanently ban {user} on {platform}?").format(user=msg.user, platform=NAMES[msg.platform]),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            coro = acc.ban(msg)
            ev = Deletion(msg.platform, user_id=msg.user_id, source=msg.source)  # only this channel
            label = _("{user} banned").format(user=msg.user)
            entry = "ban"
        else:
            raise ValueError(f"unknown moderation action {action!r}")

        def done(_result) -> None:
            self.notes.add_action(msg, entry)
            if ev:
                self.win.apply_deletion(ev)
            self.history_changed.emit()

        self.win.run(coro, then=done, label=label)

    def delete_last(self, m: ChatMessage | None) -> None:
        last = m and self.last_deletable(m)
        if last:
            self.moderate(last, "delete")

    def timeout_menu(self, parent, msg_getter) -> QMenu:
        menu = QMenu(parent)
        for secs, label in TIMEOUTS:
            menu.addAction(_(label)).triggered.connect(
                lambda _checked=False, s=secs: (m := msg_getter()) and self.moderate(m, "timeout", s)
            )
        return menu

    def open_profile(self, m: ChatMessage) -> None:
        url = profile_url(m)
        if url:
            QDesktopServices.openUrl(url)

    def open_card(self, m: ChatMessage) -> None:
        from .widgets import UserCard  # noqa: PLC0415 – widgets import this module

        win = self.win
        if self.card is None:
            self.card = UserCard(self, win)
            docked = bool(win.config.get("usercard_docked"))
            win.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.card)
            self.card.setFloating(not docked)
            self.card.pin.setChecked(docked)
            self.card.docked_changed.connect(lambda d: win.config.__setitem__("usercard_docked", d))
            if not docked:
                self.card.move(win.geometry().center() - self.card.rect().center())
        self.card.show_user(self.history.latest(m))

    def menu(self, msg: ChatMessage) -> None:
        """Right-click menu on a name."""
        menu = QMenu(self.win)
        menu.addSection(f"{msg.user} · {NAMES[msg.platform]}")
        menu.addAction(_("Open user card")).triggered.connect(lambda: self.open_card(msg))
        menu.addAction(_("Mention (@)")).triggered.connect(lambda: self.win.mention(msg))
        menu.addAction(_("Copy text")).triggered.connect(lambda: QApplication.clipboard().setText(msg.text))
        menu.addSeparator()
        if not self.logged_in(msg):
            menu.addAction(_("Log in to {platform} to moderate …").format(platform=NAMES[msg.platform])).triggered.connect(
                lambda: self.win.login(msg.platform)
            )
        elif not self.can_moderate(msg):
            menu.addAction(_("You are not a moderator in this channel")).setEnabled(False)
        else:
            a = menu.addAction(_("Delete message"))
            a.setEnabled(bool(msg.msg_id) and not msg.deleted)
            a.triggered.connect(lambda: self.moderate(msg, "delete"))
            for secs, label in TIMEOUTS:
                a = menu.addAction(_(label))
                a.setEnabled(bool(msg.user_id))
                a.triggered.connect(lambda _checked=False, s=secs: self.moderate(msg, "timeout", s))
            for action, text in (("ban", _("Ban …")), ("unban", _("Unban"))):
                a = menu.addAction(text)
                a.setEnabled(bool(msg.user_id))
                a.triggered.connect(lambda _checked=False, act=action: self.moderate(msg, act))
        menu.exec(QCursor.pos())

    def anchor_url(self, url: QUrl) -> ChatMessage | None:
        if url.scheme() != "msg":
            return None
        try:
            return self.win.messages.get(int(url.path()))
        except ValueError:
            return None
