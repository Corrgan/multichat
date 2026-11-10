# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Action bar above the chat and the user card."""
from __future__ import annotations

import html
import time

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDockWidget, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from ...chat.models import ChatMessage
from ...core.constants import BADGES, NAMES
from ...core.i18n import _
from .. import icons
from .actions import ModActions
from .store import HISTORY_PER_USER
from .util import profile_url, role_label, since_text


def _button(icon_name: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(icons.icon(icon_name))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setAutoRaise(True)
    return b


class ActionBar(QWidget):
    """Row above a group's chat: works on the selected user. `shows(msg)` tells whether the
    selection belongs to this bar's group (the other groups' bars stay empty)."""

    def __init__(self, mod: ModActions, shows=None) -> None:
        super().__init__()
        self.mod = mod
        self.shows = shows or (lambda _m: True)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setMinimumWidth(60)
        self.card = _button("user", _("Open user card"))
        self.card.clicked.connect(lambda: self.mod.selected and self.mod.open_card(self.mod.selected))
        self.mention = _button("at", _("Mention (@)"))
        self.mention.clicked.connect(lambda: self.mod.selected and self.mod.win.mention(self.mod.selected))
        self.delete = _button("trash", _("Delete last message"))
        self.delete.clicked.connect(lambda: self.mod.delete_last(self.mod.selected))
        self.timeout = _button("clock", _("Timeout"))
        self.timeout.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.timeout.setMenu(mod.timeout_menu(self, lambda: self.mod.selected))
        self.ban = _button("ban", _("Ban …"))
        self.ban.clicked.connect(lambda: self.mod.selected and self.mod.moderate(self.mod.selected, "ban"))
        self.unban = _button("unban", _("Unban"))
        self.unban.clicked.connect(lambda: self.mod.selected and self.mod.moderate(self.mod.selected, "unban"))
        self.clear = _button("close", _("Clear selection"))
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


    def refresh(self) -> None:
        m = self.mod.selected
        if not m or not self.shows(m):
            self.label.setText(f"<span style='color:#888'>{html.escape(_('Click a name to select a user'))}</span>")
            for b in (self.card, self.mention, *self.mod_buttons, self.clear):
                b.setEnabled(False)
                b.setVisible(True)
            return
        color = BADGES[m.platform][0]
        self.label.setText(f"<b style='color:{color}'>{html.escape(m.user)}</b> "
                           f"<span style='color:#888'>· {NAMES[m.platform]} · {html.escape(m.channel)}</span>")
        self.label.setToolTip(self.mod.mod_hint(m))
        for b in (self.card, self.mention, self.clear):
            b.setEnabled(True)
        known_no = self.mod.known_not_mod(m)
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
        self.pin = _button("pin", _("Dock next to the chat / float as a window"))
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
            lbl.setTextFormat(Qt.TextFormat.PlainText)  # values come from the platforms
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_labels[key] = lbl
            self.info.addRow(label + ":", lbl)

        self.mention = QPushButton(_("Mention (@)"))
        self.mention.clicked.connect(lambda: self.msg and self.mod.win.mention(self.msg))
        self.profile = QPushButton(_("Profile"))
        self.profile.clicked.connect(lambda: self.msg and self.mod.open_profile(self.msg))
        self.delete = QPushButton(_("Delete last message"))
        self.delete.clicked.connect(lambda: self.mod.delete_last(self.msg))
        self.timeout = QPushButton(_("Timeout"))
        self.timeout.setMenu(mod.timeout_menu(self, lambda: self.msg))
        self.ban = QPushButton(_("Ban …"))
        self.ban.clicked.connect(lambda: self.msg and self.mod.moderate(self.msg, "ban"))
        self.unban = QPushButton(_("Unban"))
        self.unban.clicked.connect(lambda: self.msg and self.mod.moderate(self.msg, "unban"))
        self.mod_buttons = (self.delete, self.timeout, self.ban, self.unban)
        for b, name in ((self.mention, "at"), (self.profile, "user"), (self.delete, "trash"),
                        (self.timeout, "clock"), (self.ban, "ban"), (self.unban, "unban")):
            b.setIcon(icons.icon(name))
        self.hint = QLabel()
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setStyleSheet("color:#888")
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for b in (self.mention, self.profile):
            row1.addWidget(b)
        for b in self.mod_buttons:
            row2.addWidget(b)

        self.history = QTextBrowser()
        self.history.setOpenLinks(False)
        self.history.setStyleSheet("QTextBrowser { background:#18181b; color:#dcdce0; border:none; }")
        font = self.history.font()
        font.setWeight(QFont.Weight.Normal)
        font.setPointSizeF(max(font.pointSizeF(), 10))
        self.history.setFont(font)
        self.history.document().setDefaultFont(font)
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
                           f" <span style='color:#888'>· {NAMES[m.platform]} · {html.escape(m.channel)}</span>")
        self.setWindowTitle(_("User card") + f" – {m.user} · {NAMES[m.platform]} · {m.channel}")
        for key in ("created", "following", "sub", "banned"):
            self.info_labels[key].setText("…")
        self.notes.blockSignals(True)
        self.notes.setPlainText(self.mod.notes.get(m).get("note", ""))
        self.notes.blockSignals(False)
        self.show()
        self.raise_()
        self._refresh_live()
        self._load_info(m)

    def _load_info(self, m: ChatMessage) -> None:
        self._info_req += 1
        req = self._info_req
        self.mod.user_info(m, lambda info: self._show_info(req, info))

    def account_changed(self, platform: str) -> None:
        if self.msg and self.msg.platform == platform:
            self._info_req += 1
            for key in ("created", "following", "sub", "banned"):
                self.info_labels[key].setText("–")
            self._refresh_buttons()
            if self.isVisible():
                self._load_info(self.msg)

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
        if isinstance(months, (int, float)) and not isinstance(months, bool) and months > 0:
            self.info_labels["sub"].setText(_("{n} months").format(n=int(months)))
        else:
            self.info_labels["sub"].setText(unknown if months is None else "–")
        banned = info.get("banned")
        self.info_labels["banned"].setText(unknown if banned is None else (_("yes") if banned else _("no")))

    def _refresh_live(self) -> None:
        m = self.msg
        if not m or not self.isVisible():  # show_user() refreshes when it opens again
            return
        self.msg = m = self.mod.history.latest(m)  # newest message in the card's fixed channel
        msgs = self.mod.messages_of(m)
        roles = [role_label(r) for r in m.badges]
        self.info_labels["roles"].setText(", ".join(roles) or "–")
        self.info_labels["seen"].setText(str(len(msgs)))
        rows = []
        for msg in msgs[-HISTORY_PER_USER:]:
            ts = time.strftime("%H:%M", time.localtime(msg.timestamp))
            text = html.escape(msg.text)
            if msg.deleted:
                text = f"<s style='color:#777'>{text}</s>"
            channel = html.escape(msg.channel)
            rows.append(f"<span style='color:#888'>{ts} · {channel}</span> {text}")
        empty = f"<span style='color:#888'>{html.escape(_('No messages seen yet'))}</span>"
        self.history.setHtml("<br>".join(rows) or empty)
        bar = self.history.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))  # after the layout: newest in view
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
        known_no = self.mod.known_not_mod(m)
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

