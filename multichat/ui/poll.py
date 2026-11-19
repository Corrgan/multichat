# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: a poll in the chat of all platforms (chat/poll.py counts) – the dialog
that starts it, the bar above the input that shows it, the overlay box. Mixed into MainWindow."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..chat.poll import MAX_OPTIONS, MIN_OPTIONS, ChatPoll
from ..core.i18n import _

log = logging.getLogger("multichat")

MAX_QUESTION, MAX_OPTION = 120, 40
OVERLAY_EVERY_MS = 500  # the overlay box follows the votes at most twice a second


class PollDialog(QDialog):
    """Question, options, time limit and whether to announce it in the chats."""

    def __init__(self, parent: QWidget, can_send: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Chat poll"))
        lay = QVBoxLayout(self)
        intro = QLabel(_("Viewers answer with !1, !2 … in the chat – on Twitch, Kick and YouTube at once. "
                         "One vote per person, a later one replaces it. Counted in the channels of the "
                         "active group."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.question = QLineEdit()
        self.question.setMaxLength(MAX_QUESTION)
        form.addRow(_("Question:"), self.question)
        self.options: list[QLineEdit] = []
        for i in range(MAX_OPTIONS):
            e = QLineEdit()
            e.setMaxLength(MAX_OPTION)
            if i >= MIN_OPTIONS:
                e.setPlaceholderText(_("(optional)"))
            e.textChanged.connect(self._check)
            self.options.append(e)
            form.addRow(f"!{i + 1}", e)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 60)
        self.minutes.setValue(2)
        self.minutes.setSpecialValueText(_("until I end it"))
        before, _sep, after = _("{n} min").partition("{n}")
        self.minutes.setPrefix(before)
        self.minutes.setSuffix(after)
        form.addRow(_("Duration:"), self.minutes)
        lay.addLayout(form)
        self.announce = QCheckBox(_("Announce the poll and its result in the chats"))
        self.announce.setChecked(can_send)
        self.announce.setEnabled(can_send)
        if not can_send:
            self.announce.setToolTip(_("Log in under “Accounts” to send"))
        lay.addWidget(self.announce)
        self.question.textChanged.connect(self._check)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("Start poll"))
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self._check()

    def choices(self) -> list[str]:
        return [e.text().strip() for e in self.options if e.text().strip()]

    def _check(self) -> None:
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.question.text().strip()) and len(self.choices()) >= MIN_OPTIONS)


class PollBar(QFrame):
    """The running poll above the input: a bar per option, votes, time left, end/close."""

    def __init__(self, end, close) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        top = QHBoxLayout()
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.info = QLabel()
        self.end_btn = QPushButton(_("End poll"))
        self.end_btn.clicked.connect(end)
        self.close_btn = QPushButton(_("Close"))
        self.close_btn.clicked.connect(close)
        top.addWidget(self.title, 1)
        top.addWidget(self.info)
        top.addWidget(self.end_btn)
        top.addWidget(self.close_btn)
        lay.addLayout(top)
        self.grid = QGridLayout()
        lay.addLayout(self.grid)
        self.bars: list[QProgressBar] = []

    def show_poll(self, poll: ChatPoll) -> None:
        if len(self.bars) != len(poll.options):
            for b in self.bars:
                b.deleteLater()
            self.bars = []
            for i in range(len(poll.options)):
                b = QProgressBar()
                b.setRange(0, 100)
                self.grid.addWidget(b, i, 0)
                self.bars.append(b)
        self.title.setText(f"<b>{_('Poll ended') if poll.ended else _('Poll')}:</b> {_escape(poll.question)}")
        winners = set(poll.winners()) if poll.ended else set()
        for i, (b, option) in enumerate(zip(self.bars, poll.options)):
            b.setValue(poll.percent(i))
            mark = "✔ " if i in winners else ""
            # QProgressBar replaces %p, %v and %m in the format – an option text must not contain them
            b.setFormat(f"{mark}!{i + 1} {option.replace('%', '％')} – {poll.counts[i]} ({poll.percent(i)} %)")
        left = poll.remaining()
        votes = _("{n} votes").format(n=poll.total)
        self.info.setText(votes if poll.ended or left is None else f"{votes} · {left // 60}:{left % 60:02d}")
        self.end_btn.setVisible(not poll.ended)
        self.close_btn.setVisible(poll.ended)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class PollMixin:
    """Poll part of MainWindow (expects active_panel, panels, panels_for, overlay, _send_options,
    _send_all and run from it)."""

    poll: ChatPoll | None = None

    def _build_poll(self) -> QWidget:
        self.poll_bar = PollBar(self.end_poll, self.close_poll)
        self.poll_bar.hide()
        self._poll_targets: list = []
        self._poll_dirty = False
        self._poll_tick = QTimer(self, interval=1000)  # countdown and end at the time limit
        self._poll_tick.timeout.connect(self._poll_second)
        self._poll_overlay = QTimer(self, singleShot=True, interval=OVERLAY_EVERY_MS)
        self._poll_overlay.timeout.connect(self._publish_poll)
        return self.poll_bar

    def start_poll_dialog(self) -> None:
        if self.poll and not self.poll.ended:
            self.statusBar().showMessage(_("A poll is running – end it first."), 5000)
            return
        dlg = PollDialog(self, bool(self._send_options()))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.start_poll(dlg.question.text(), dlg.choices(), dlg.minutes.value() * 60, dlg.announce.isChecked())

    def start_poll(self, question: str, options: list[str], seconds: int, announce: bool) -> None:
        panel = self.active_panel()
        sources = {r.key() for r in panel.all_rows() if r.key()}
        self.poll = ChatPoll(question, options, sources, seconds)
        self._poll_group = self.panels.index(panel) + 1
        # announced where it started – the active group may change meanwhile
        self._poll_targets = self._send_options() if announce else []
        log.info("Poll started: %r, %d options, %d channels", self.poll.question, len(options), len(sources))
        if self._poll_targets:
            self._send_all(self.poll.announcement(), self._poll_targets)
        self.poll_bar.show_poll(self.poll)
        self.poll_bar.show()
        self._poll_tick.start()
        self._publish_poll()

    def count_votes(self, batch) -> None:
        """Called with each batch of new messages."""
        if not self.poll or self.poll.ended:
            return
        changed = False
        for m in batch:
            changed |= self.poll.vote(m)
        if changed:
            self.poll_bar.show_poll(self.poll)
            if not self._poll_overlay.isActive():
                self._poll_overlay.start()

    def _poll_second(self) -> None:
        if not self.poll:
            return
        if self.poll.due():
            self.end_poll()
            return
        self.poll_bar.show_poll(self.poll)

    def end_poll(self) -> None:
        if not self.poll or self.poll.ended:
            return
        self.poll.end()
        self._poll_tick.stop()
        log.info("Poll ended: %s", self.poll.result())
        if self._poll_targets:
            self._send_all(self.poll.result(), self._poll_targets)
        self.statusBar().showMessage(self.poll.result(), 15000)
        self.poll_bar.show_poll(self.poll)
        self._publish_poll()

    def close_poll(self) -> None:
        self.poll_bar.hide()
        if self.poll and self.overlay.running:
            self.overlay.publish({"type": "poll", "state": "closed", "groups": [self._poll_group]})
        self.poll = None

    def _publish_poll(self) -> None:
        if self.poll and self.overlay.running:
            self.overlay.publish(self.poll.payload([self._poll_group]))
