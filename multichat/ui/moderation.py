# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Moderation helpers: selected user, action bar above the chat, user card, notes.

- Clicking a name selects that user; the action bar then works on the selection.
- Whether you may moderate a channel is asked from the platform (Twitch lists the
  channels you moderate; Kick/YouTube only know your own channel) and also read from
  the badges on your own chat messages. Mod actions are hidden where you are known
  not to be a moderator.
- Notes and a log of your moderation actions are kept locally per user
  (~/.config/multichat/modnotes.json, readable only by you).
"""
from __future__ import annotations

import html
import json
import logging
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDockWidget, QFormLayout, QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from ..chat.models import ChatMessage, user_key
from ..core.config import write_private
from ..core.constants import BADGES, NAMES
from ..core.i18n import _
from .chat_view import ROLE_ICONS

log = logging.getLogger("multichat.moderation")

HISTORY_USERS = 3000  # users whose recent messages are kept for the user card
HISTORY_PER_USER = 100
NOTES_MAX_USERS = 5000
NOTE_MAX_CHARS = 4000
ACTIONS_PER_USER = 50
MOD_ROLES = {"moderator", "broadcaster"}

def role_label(role: str) -> str:
    return {
        "broadcaster": _("Broadcaster"), "moderator": _("Moderator"), "vip": "VIP",
        "subscriber": _("Subscriber / member"), "verified": _("Verified"),
    }.get(role, role)


def profile_url(m: ChatMessage) -> QUrl | None:
    """Link to the user's page – built from a fixed prefix, the name is URL-quoted."""
    if m.platform == "twitch":
        return QUrl("https://www.twitch.tv/" + quote(m.user.lower(), safe=""))
    if m.platform == "kick":
        return QUrl("https://kick.com/" + quote(m.user.lower(), safe=""))
    if m.platform == "youtube" and m.user_id:
        return QUrl("https://www.youtube.com/channel/" + quote(m.user_id, safe=""))
    return None


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def duration_text(days: int) -> str:
    if days < 60:
        return _("{n} days").format(n=days)
    if days < 730:
        return _("{n} months").format(n=days // 30)
    return _("{n} years").format(n=days // 365)


def since_text(value, now: datetime | None = None) -> str:
    """ISO time → "2021-03-04 (3 years)"; "" if unknown."""
    dt = _parse_time(value)
    if not dt:
        return ""
    now = now or datetime.now(timezone.utc)
    days = max(0, (now - dt).days)
    return f"{dt.astimezone().strftime('%Y-%m-%d')} ({duration_text(days)})"


class ModNotes:
    """Per-user notes and moderation log, stored as JSON next to the settings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: OrderedDict[str, dict] = OrderedDict()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, dict):
                        self.data[k] = {
                            "name": str(v.get("name", ""))[:100],
                            "note": str(v.get("note", ""))[:NOTE_MAX_CHARS],
                            "actions": [a for a in v.get("actions", []) if isinstance(a, dict)][-ACTIONS_PER_USER:],
                        }
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("%s unreadable, starting without notes: %s", path, e)
        self._timer: QTimer | None = None

    @staticmethod
    def key(m: ChatMessage) -> str:
        return f"{m.platform}:{user_key(m)}"

    def get(self, m: ChatMessage) -> dict:
        return self.data.get(self.key(m)) or {"name": m.user, "note": "", "actions": []}

    def _entry(self, m: ChatMessage) -> dict:
        k = self.key(m)
        entry = self.data.pop(k, None) or {"name": m.user, "note": "", "actions": []}
        entry["name"] = m.user
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
        entry["actions"] = (entry["actions"] + [{"t": int(time.time()), "a": action}])[-ACTIONS_PER_USER:]
        self.save_later()

    def save_later(self) -> None:
        """Typing a note saves once a second at most."""
        if self._timer is None:
            self._timer = QTimer(singleShot=True, interval=1000)
            self._timer.timeout.connect(self.save)
        self._timer.start()

    def save(self) -> None:
        if self._timer:
            self._timer.stop()
        try:
            write_private(self.path, dict(self.data))
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


class ModActions(QObject):
    """What the action bar and the user card share: selection, history, permissions."""

    selection_changed = pyqtSignal()
    history_changed = pyqtSignal()

    def __init__(self, win, notes: ModNotes) -> None:
        super().__init__()
        self.win = win
        self.notes = notes
        self.state = ModState()
        self.selected: ChatMessage | None = None  # latest message of the selected user
        self.history: OrderedDict[tuple[str, str], deque] = OrderedDict()

    # ---- incoming messages ---------------------------------------------- #
    def observe(self, m: ChatMessage) -> None:
        key = (m.platform, user_key(m))
        hist = self.history.pop(key, None) or deque(maxlen=HISTORY_PER_USER)
        hist.append(m)
        self.history[key] = hist
        while len(self.history) > HISTORY_USERS:
            self.history.popitem(last=False)
        acc = self.win.accounts[m.platform]
        if acc.logged_in:
            own = (m.user_id and m.user_id == acc.user_id) or m.user.lower() == acc.user_name.lower()
            if own and MOD_ROLES & set(m.badges):
                self.state.set(m.platform, m.channel, True)
            self._ask(m.platform, m.channel)
        if self.selected and (self.selected.platform, user_key(self.selected)) == key:
            self.selected = m
            self.history_changed.emit()

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
        self.selection_changed.emit()

    # ---- selection -------------------------------------------------------- #
    def select(self, m: ChatMessage | None) -> None:
        if m is not None:  # the latest message of that user, not the clicked one
            hist = self.history.get((m.platform, user_key(m)))
            m = hist[-1] if hist else m
        self.selected = m
        self.win.view.selected = (m.platform, user_key(m)) if m else None
        self.win.render_all()
        self.selection_changed.emit()

    def messages_of(self, m: ChatMessage) -> list[ChatMessage]:
        return list(self.history.get((m.platform, user_key(m))) or [])

    def last_deletable(self, m: ChatMessage) -> ChatMessage | None:
        for msg in reversed(self.messages_of(m)):
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

    def mod_hint(self, m: ChatMessage) -> str:
        if not self.logged_in(m):
            return _("Log in to {platform} to moderate").format(platform=NAMES[m.platform])
        if self.is_mod(m) is False:
            return _("You are not a moderator in this channel")
        return ""

    # ---- actions ---------------------------------------------------------- #
    def record(self, m: ChatMessage, action: str) -> None:
        self.notes.add_action(m, action)
        self.history_changed.emit()

    def timeout_menu(self, parent, msg_getter) -> QMenu:
        from .main_window import TIMEOUTS  # noqa: PLC0415 – avoids an import cycle

        menu = QMenu(parent)
        for secs, label in TIMEOUTS:
            menu.addAction(_(label)).triggered.connect(
                lambda _checked=False, s=secs: (m := msg_getter()) and self.win.moderate(m, "timeout", s)
            )
        return menu

    def open_profile(self, m: ChatMessage) -> None:
        url = profile_url(m)
        if url:
            QDesktopServices.openUrl(url)


def _button(text: str, tip: str = "") -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip or text)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    b.setAutoRaise(True)
    return b


class ActionBar(QWidget):
    """Row above the chat: works on the selected user."""

    def __init__(self, mod: ModActions, open_card) -> None:
        super().__init__()
        self.mod = mod
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setMinimumWidth(60)
        self.card = _button("👤", _("Open user card"))
        self.card.clicked.connect(lambda: self.mod.selected and open_card(self.mod.selected))
        self.mention = _button("@", _("Mention (@)"))
        self.mention.clicked.connect(lambda: self.mod.selected and self.mod.win.mention(self.mod.selected))
        self.delete = _button("🗑", _("Delete last message"))
        self.delete.clicked.connect(self._delete_last)
        self.timeout = _button("⏱", _("Timeout"))
        self.timeout.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.timeout.setMenu(mod.timeout_menu(self, lambda: self.mod.selected))
        self.ban = _button("⛔", _("Ban …"))
        self.ban.clicked.connect(lambda: self.mod.selected and self.mod.win.moderate(self.mod.selected, "ban"))
        self.unban = _button("✅", _("Unban"))
        self.unban.clicked.connect(lambda: self.mod.selected and self.mod.win.moderate(self.mod.selected, "unban"))
        self.clear = _button("✕", _("Clear selection"))
        self.clear.clicked.connect(lambda: self.mod.select(None))
        self.mod_buttons = (self.delete, self.timeout, self.ban, self.unban)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.label, 1)
        for b in (self.card, self.mention, *self.mod_buttons, self.clear):
            row.addWidget(b)
        mod.selection_changed.connect(self.refresh)
        mod.history_changed.connect(self.refresh)
        mod.state.changed.connect(self.refresh)
        self.refresh()

    def _delete_last(self) -> None:
        m = self.mod.selected and self.mod.last_deletable(self.mod.selected)
        if m:
            self.mod.win.moderate(m, "delete")

    def refresh(self) -> None:
        m = self.mod.selected
        if not m:
            self.label.setText(f"<span style='color:#888'>{html.escape(_('Click a name to select a user'))}</span>")
            for b in (self.card, self.mention, *self.mod_buttons, self.clear):
                b.setEnabled(False)
                b.setVisible(True)
            return
        color = BADGES[m.platform][0]
        self.label.setText(f"<b style='color:{color}'>{html.escape(m.user)}</b> "
                           f"<span style='color:#888'>· {NAMES[m.platform]}</span>")
        self.label.setToolTip(self.mod.mod_hint(m))
        for b in (self.card, self.mention, self.clear):
            b.setEnabled(True)
        known_no = self.mod.logged_in(m) and self.mod.is_mod(m) is False
        can = self.mod.can_moderate(m)
        for b in self.mod_buttons:
            b.setVisible(not known_no)
            b.setEnabled(can and bool(m.user_id))
        self.delete.setEnabled(can and self.mod.last_deletable(m) is not None)


class UserCard(QDockWidget):
    """Popup with infos about one user; the pin button docks it next to the chat."""

    docked_changed = pyqtSignal(bool)

    def __init__(self, mod: ModActions, parent) -> None:
        super().__init__(_("User card"), parent)
        self.setObjectName("usercard")
        self.mod = mod
        self.msg: ChatMessage | None = None
        self._info_req = 0
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)

        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setWordWrap(True)
        self.pin = _button("📌", _("Dock next to the chat / float as a window"))
        self.pin.setCheckable(True)
        self.pin.toggled.connect(self._pin)
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(self.pin)

        self.info = QFormLayout()
        self.info_labels: dict[str, QLabel] = {}
        for key, label in (("roles", _("Roles")), ("created", _("Account created")),
                           ("following", _("Following since")), ("sub", _("Subscribed")),
                           ("banned", _("Banned")), ("seen", _("Messages seen"))):
            lbl = QLabel("–")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_labels[key] = lbl
            self.info.addRow(label + ":", lbl)

        self.mention = QPushButton(_("Mention (@)"))
        self.mention.clicked.connect(lambda: self.msg and self.mod.win.mention(self.msg))
        self.profile = QPushButton(_("Profile"))
        self.profile.clicked.connect(lambda: self.msg and self.mod.open_profile(self.msg))
        self.delete = QPushButton(_("Delete last message"))
        self.delete.clicked.connect(self._delete_last)
        self.timeout = QPushButton(_("Timeout"))
        self.timeout.setMenu(mod.timeout_menu(self, lambda: self.msg))
        self.ban = QPushButton(_("Ban …"))
        self.ban.clicked.connect(lambda: self.msg and self.mod.win.moderate(self.msg, "ban"))
        self.unban = QPushButton(_("Unban"))
        self.unban.clicked.connect(lambda: self.msg and self.mod.win.moderate(self.msg, "unban"))
        self.mod_buttons = (self.delete, self.timeout, self.ban, self.unban)
        self.hint = QLabel()
        self.hint.setStyleSheet("color:#888")
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for b in (self.mention, self.profile):
            row1.addWidget(b)
        for b in self.mod_buttons:
            row2.addWidget(b)

        self.history = QTextBrowser()
        self.history.setOpenLinks(False)
        self.history.setStyleSheet("QTextBrowser { background:#18181b; color:#efeff1; border:none; }")
        self.actions = QLabel()
        self.actions.setWordWrap(True)
        self.actions.setTextFormat(Qt.TextFormat.RichText)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(_("Your notes about this user (only stored on this computer)"))
        self.notes.setMaximumHeight(90)
        self.notes.textChanged.connect(self._note_changed)

        box = QVBoxLayout()
        box.addLayout(head)
        box.addLayout(self.info)
        box.addLayout(row1)
        box.addLayout(row2)
        box.addWidget(self.hint)
        box.addWidget(QLabel(f"<b>{html.escape(_('Recent messages'))}</b>"))
        box.addWidget(self.history, 1)
        box.addWidget(QLabel(f"<b>{html.escape(_('Your moderation actions'))}</b>"))
        box.addWidget(self.actions)
        box.addWidget(QLabel(f"<b>{html.escape(_('Notes'))}</b>"))
        box.addWidget(self.notes)
        w = QWidget()
        w.setLayout(box)
        self.setWidget(w)
        self.resize(380, 560)

        self.topLevelChanged.connect(lambda floating: self._sync_pin(not floating))
        mod.history_changed.connect(self._refresh_live)
        mod.state.changed.connect(self._refresh_buttons)

    # ---- docking ---------------------------------------------------------- #
    def _pin(self, docked: bool) -> None:
        if self.isFloating() == docked:
            self.setFloating(not docked)
        self.docked_changed.emit(docked)

    def _sync_pin(self, docked: bool) -> None:
        self.pin.blockSignals(True)
        self.pin.setChecked(docked)
        self.pin.blockSignals(False)
        self.docked_changed.emit(docked)

    # ---- content ---------------------------------------------------------- #
    def show_user(self, m: ChatMessage) -> None:
        self.msg = m
        color = m.color or BADGES[m.platform][0]
        self.title.setText(f"<span style='font-size:16px;font-weight:bold;color:{color}'>{html.escape(m.user)}</span>"
                           f" <span style='color:#888'>· {NAMES[m.platform]}</span>")
        self.setWindowTitle(_("User card") + f" – {m.user}")
        for key in ("created", "following", "sub", "banned"):
            self.info_labels[key].setText("…")
        self.notes.blockSignals(True)
        self.notes.setPlainText(self.mod.notes.get(m).get("note", ""))
        self.notes.blockSignals(False)
        self._refresh_live()
        self._load_info(m)
        self.show()
        self.raise_()

    def _load_info(self, m: ChatMessage) -> None:
        self._info_req += 1
        req = self._info_req
        acc = self.mod.win.accounts[m.platform]
        if m.platform != "kick" and not acc.logged_in:
            self._show_info(req, {"_hint": _("log in for more")})
            return

        async def fetch():
            try:
                return await acc.user_info(m)
            except Exception as e:  # noqa: BLE001 – the card stays usable without it
                log.info("%s user info failed: %s", NAMES[m.platform], e)
                return {"_hint": _("not available")}

        self.mod.win.run(fetch(), then=lambda info: self._show_info(req, info))

    def _show_info(self, req: int, info: dict) -> None:
        if req != self._info_req:
            return  # another user was opened meanwhile
        unknown = info.get("_hint") or "–"
        self.info_labels["created"].setText(since_text(info.get("created")) or unknown)
        following = info.get("following_since")
        if following is None:
            self.info_labels["following"].setText(unknown)
        else:
            self.info_labels["following"].setText(since_text(following) or _("not following"))
        months = info.get("subscribed_months")
        self.info_labels["sub"].setText(_("{n} months").format(n=months) if months else unknown if months is None else "–")
        banned = info.get("banned")
        self.info_labels["banned"].setText(unknown if banned is None else (_("yes") if banned else _("no")))

    def _refresh_live(self) -> None:
        m = self.msg
        if not m:
            return
        sel = self.mod.selected
        if sel and (sel.platform, user_key(sel)) == (m.platform, user_key(m)):
            self.msg = m = sel  # newest message of this user
        msgs = self.mod.messages_of(m)
        roles = [f"{ROLE_ICONS.get(r, '')} {role_label(r)}" for r in m.badges]
        self.info_labels["roles"].setText(", ".join(roles) or "–")
        self.info_labels["seen"].setText(str(len(msgs)))
        rows = []
        for msg in msgs[-HISTORY_PER_USER:]:
            ts = time.strftime("%H:%M", time.localtime(msg.timestamp))
            text = html.escape(msg.text)
            if msg.deleted:
                text = f"<s style='color:#777'>{text}</s>"
            rows.append(f"<span style='color:#888'>{ts}</span> {text}")
        self.history.setHtml("<div style='font-weight:normal'>" + "<br>".join(rows) + "</div>" if rows else f"<span style='color:#888'>{html.escape(_('No messages seen yet'))}</span>")
        bar = self.history.verticalScrollBar()
        bar.setValue(bar.maximum())
        acts = self.mod.notes.get(m).get("actions", [])
        self.actions.setText("<br>".join(
            f"<span style='color:#888'>{time.strftime('%Y-%m-%d %H:%M', time.localtime(a.get('t', 0)))}</span> "
            f"{html.escape(str(a.get('a', '')))}" for a in reversed(acts[-10:])
        ) or f"<span style='color:#888'>{html.escape(_('none'))}</span>")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        m = self.msg
        if not m:
            return
        known_no = self.mod.logged_in(m) and self.mod.is_mod(m) is False
        can = self.mod.can_moderate(m)
        for b in self.mod_buttons:
            b.setVisible(not known_no)
            b.setEnabled(can and bool(m.user_id))
        self.delete.setEnabled(can and self.mod.last_deletable(m) is not None)
        self.profile.setEnabled(profile_url(m) is not None)
        hint = self.mod.mod_hint(m)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))

    def _note_changed(self) -> None:
        if self.msg:
            self.mod.notes.set_note(self.msg, self.notes.toPlainText())

    def _delete_last(self) -> None:
        m = self.msg and self.mod.last_deletable(self.msg)
        if m:
            self.mod.win.moderate(m, "delete")
