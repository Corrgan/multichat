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

    def __init__(self, parent: QWidget, can_send: bool, twitch: str = "", youtube: str = "") -> None:
        """twitch / youtube: why the platform's own poll can't run alongside ("" = it can)."""
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
        # the platforms' own polls alongside: viewers click there, MultiChat adds their votes
        self.native: dict[str, QCheckBox] = {}
        self._native_why = {"twitch": twitch, "youtube": youtube}
        for platform, label in (("twitch", _("Also as a Twitch poll (your channel, up to 5 options)")),
                                ("youtube", _("Also as a YouTube poll (your live stream, up to 4 options)"))):
            cb = QCheckBox(label)
            cb.setChecked(not self._native_why[platform])
            self.native[platform] = cb
            lay.addWidget(cb)
        self.question.textChanged.connect(self._check)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("Start poll"))
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self._check()

    def choices(self) -> list[str]:
        return [e.text().strip() for e in self.options if e.text().strip()]

    def natives(self) -> set[str]:
        return {p for p, cb in self.native.items() if cb.isEnabled() and cb.isChecked()}

    def _check(self) -> None:
        n = len(self.choices())
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.question.text().strip()) and n >= MIN_OPTIONS)
        for platform, cb in self.native.items():
            why = self._native_why[platform] or (
                _("Twitch polls have at most 5 options") if platform == "twitch" and n > 5 else
                _("YouTube polls have at most 4 options") if platform == "youtube" and n > 4 else "")
            cb.setEnabled(not why)
            cb.setToolTip(why)


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


NATIVE_EVERY = {"twitch": 5, "youtube": 20}  # seconds between reading a platform poll (YouTube: 5 quota units)
MAX_OUTCOMES = 6


class PredictionDialog(QDialog):
    """A Twitch prediction in your channel: title, outcomes, how long people can bet."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Twitch prediction"))
        lay = QVBoxLayout(self)
        intro = QLabel(_("Viewers bet channel points on Twitch. You lock the bets and pick the winner here."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setMaxLength(45)
        form.addRow(_("Question:"), self.title)
        self.outcomes: list[QLineEdit] = []
        for i in range(MAX_OUTCOMES):
            e = QLineEdit()
            e.setMaxLength(25)
            if i >= 2:
                e.setPlaceholderText(_("(optional)"))
            e.textChanged.connect(self._check)
            self.outcomes.append(e)
            form.addRow(f"{i + 1}.", e)
        self.seconds = QSpinBox()
        self.seconds.setRange(30, 1800)
        self.seconds.setSingleStep(30)
        self.seconds.setValue(120)
        before, _sep, after = _("{n} s").partition("{n}")
        self.seconds.setPrefix(before)
        self.seconds.setSuffix(after)
        form.addRow(_("Bets open for:"), self.seconds)
        lay.addLayout(form)
        self.title.textChanged.connect(self._check)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("Start prediction"))
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self._check()

    def choices(self) -> list[str]:
        return [e.text().strip() for e in self.outcomes if e.text().strip()]

    def _check(self) -> None:
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.title.text().strip()) and len(self.choices()) >= 2)


class PredictionBar(QFrame):
    """The running prediction: lock the bets, pick the winner, or cancel (points back)."""

    def __init__(self, act) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._act = act
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(6, 4, 6, 4)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.lay.addWidget(self.title, 1)
        self.buttons: list[QPushButton] = []

    def show_prediction(self, title: str, outcomes: list[str], locked: bool) -> None:
        for b in self.buttons:
            b.deleteLater()
        self.buttons = []
        state = _("bets locked") if locked else _("bets open")
        self.title.setText(f"<b>{_('Prediction')}:</b> {_escape(title)} ({state})")

        def button(text: str, *args) -> None:
            b = QPushButton(text)
            b.clicked.connect(lambda _checked=False: self._act(*args))
            self.lay.addWidget(b)
            self.buttons.append(b)

        if not locked:
            button(_("Lock bets"), "LOCKED")
        for i, o in enumerate(outcomes):
            button(_("Winner: {outcome}").format(outcome=o), "RESOLVED", i)
        button(_("Cancel (points back)"), "CANCELED")


class PollMixin:
    """Poll part of MainWindow (expects accounts, active_panel, panels, overlay, _send_options,
    _send_all and run from it)."""

    poll: ChatPoll | None = None

    def _build_poll(self) -> QWidget:
        self.poll_bar = PollBar(self.end_poll, self.close_poll)
        self.poll_bar.hide()
        self.prediction_bar = PredictionBar(self.prediction_action)
        self.prediction_bar.hide()
        self.prediction: dict | None = None
        self._poll_targets: list = []
        self._native: dict[str, tuple] = {}  # platform → IDs of its own poll alongside the chat poll
        self._native_wait: dict[str, int] = {}  # platform → seconds until its votes are read again
        self._poll_tick = QTimer(self, interval=1000)  # countdown, platform polls, end at the time limit
        self._poll_tick.timeout.connect(self._poll_second)
        self._poll_overlay = QTimer(self, singleShot=True, interval=OVERLAY_EVERY_MS)
        self._poll_overlay.timeout.connect(self._publish_poll)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.poll_bar)
        lay.addWidget(self.prediction_bar)
        return box

    def _own_channels(self) -> tuple[str, str]:
        """(your Twitch channel, a YouTube channel/video) in the active group, "" where none."""
        rows = [r for r in self.active_panel().all_rows() if r.key()]
        tw = self.accounts["twitch"]
        twitch = next((r.value() for r in rows if r.platform == "twitch" and tw.logged_in and tw.owns(r.value())), "")
        youtube = next((r.value() for r in rows if r.platform == "youtube" and self.accounts["youtube"].logged_in), "")
        return twitch, youtube

    def _native_reasons(self) -> tuple[str, str]:
        twitch, youtube = self._own_channels()
        why_twitch = "" if twitch else (_("Your Twitch channel is not in the active group")
                                        if self.accounts["twitch"].logged_in else _("Log in to Twitch first"))
        why_youtube = "" if youtube else (_("No YouTube channel in the active group")
                                          if self.accounts["youtube"].logged_in else _("Log in to YouTube first"))
        return why_twitch, why_youtube

    def start_poll_dialog(self) -> None:
        if self.poll and not self.poll.ended:
            self.statusBar().showMessage(_("A poll is running – end it first."), 5000)
            return
        dlg = PollDialog(self, bool(self._send_options()), *self._native_reasons())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.start_poll(dlg.question.text(), dlg.choices(), dlg.minutes.value() * 60, dlg.announce.isChecked(),
                            dlg.natives())

    def start_poll(self, question: str, options: list[str], seconds: int, announce: bool,
                   natives: set[str] = frozenset()) -> None:
        panel = self.active_panel()
        sources = {r.key() for r in panel.all_rows() if r.key()}
        poll = self.poll = ChatPoll(question, options, sources, seconds)
        self._poll_group = self.panels.index(panel) + 1
        # announced where it started – the active group may change meanwhile
        self._poll_targets = self._send_options() if announce else []
        self._native = {}
        twitch, youtube = self._own_channels()
        if "twitch" in natives and twitch:
            poll.add_native("twitch")
            self.run(self.accounts["twitch"].create_poll(question, options, seconds or 1800),
                     then=lambda pid: self._native_started(poll, "twitch", (pid,)),
                     on_error=lambda: self._native_failed(poll, "twitch"))
        if "youtube" in natives and youtube:
            poll.add_native("youtube")
            self.run(self.accounts["youtube"].create_poll(youtube, question, options),
                     then=lambda ids: self._native_started(poll, "youtube", ids),
                     on_error=lambda: self._native_failed(poll, "youtube"))
        log.info("Poll started: %r, %d options, %d channels, platform polls %s",
                 poll.question, len(options), len(sources), sorted(poll.native) or "-")
        for target in self._poll_targets:
            self._send_all(poll.announcement(target[1]), [target])
        self.poll_bar.show_poll(poll)
        self.poll_bar.show()
        self._poll_tick.start()
        self._publish_poll()

    def _native_started(self, poll: ChatPoll, platform: str, ids: tuple) -> None:
        if poll.ended or poll is not self.poll:  # ended before the platform answered: end it there too
            self.run(self.accounts[platform].end_poll(ids[-1]), quiet=True)
            return
        self._native[platform] = ids
        self._native_wait[platform] = NATIVE_EVERY[platform]

    def _native_failed(self, poll: ChatPoll, platform: str) -> None:
        """The platform refused (not affiliate, no live stream …): the chat's !1 … count there again."""
        poll.native.pop(platform, None)
        if poll is self.poll:
            self.poll_bar.show_poll(poll)

    def _read_native(self, poll: ChatPoll, platform: str, coro) -> None:
        def got(state) -> None:
            _status, votes = state
            if votes and poll.set_native(platform, votes) and poll is self.poll:
                self.poll_bar.show_poll(poll)
                self._publish_poll()
        self.run(coro, then=got, quiet=True)

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
        poll = self.poll
        if not poll:
            return
        if poll.due():
            self.end_poll()
            return
        for platform, ids in self._native.items():
            self._native_wait[platform] -= 1
            if self._native_wait[platform] <= 0:
                self._native_wait[platform] = NATIVE_EVERY[platform]
                self._read_native(poll, platform, self.accounts[platform].poll_votes(*ids))
        self.poll_bar.show_poll(poll)

    def end_poll(self) -> None:
        poll = self.poll
        if not poll or poll.ended:
            return
        poll.end()
        self._poll_tick.stop()
        if self._native:
            self._end_natives(poll)  # announces when the platforms sent their final votes
        else:
            self._poll_finished(poll)
        self.poll_bar.show_poll(poll)
        self._publish_poll()

    def _end_natives(self, poll: ChatPoll) -> None:
        natives, self._native = dict(self._native), {}

        async def end_all() -> dict:
            final = {}
            for platform, ids in natives.items():
                try:
                    final[platform] = (await self.accounts[platform].end_poll(ids[-1]))[1]
                except Exception as e:  # noqa: BLE001 – the rest still ends; the last votes read count
                    log.warning("Ending the %s poll failed: %s", platform, e)
            return final

        def done(final: dict) -> None:
            for platform, votes in final.items():
                if votes:
                    poll.set_native(platform, votes)
            self._poll_finished(poll)

        self.run(end_all(), then=done, on_error=lambda: self._poll_finished(poll))

    def _poll_finished(self, poll: ChatPoll) -> None:
        log.info("Poll ended: %s", poll.result())
        for target in self._poll_targets:
            self._send_all(poll.result(), [target])
        self.statusBar().showMessage(poll.result(), 15000)
        if poll is self.poll:
            self.poll_bar.show_poll(poll)
            self._publish_poll()

    def close_poll(self) -> None:
        self.poll_bar.hide()
        if self.poll and self.overlay.running:
            self.overlay.publish({"type": "poll", "state": "closed", "groups": [self._poll_group]})
        self.poll = None

    def _publish_poll(self) -> None:
        if self.poll and self.overlay.running:
            self.overlay.publish(self.poll.payload([self._poll_group]))

    # ---- Twitch predictions ------------------------------------------------ #
    def start_prediction_dialog(self) -> None:
        twitch, _youtube = self._own_channels()
        if not twitch:
            self.statusBar().showMessage(self._native_reasons()[0], 6000)
            return
        if self.prediction:
            self.statusBar().showMessage(_("A prediction is running – end it first."), 5000)
            return
        dlg = PredictionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.start_prediction(dlg.title.text().strip(), dlg.choices(), dlg.seconds.value())

    def start_prediction(self, title: str, outcomes: list[str], seconds: int) -> None:
        def started(ids) -> None:
            pid, outcome_ids = ids
            self.prediction = {"id": pid, "title": title, "outcomes": outcomes, "outcome_ids": outcome_ids,
                               "locked": False}
            self.prediction_bar.show_prediction(title, outcomes, False)
            self.prediction_bar.show()
        self.run(self.accounts["twitch"].create_prediction(title, outcomes, seconds), then=started,
                 label=_("Prediction started"))

    def prediction_action(self, status: str, winner: int | None = None) -> None:
        p = self.prediction
        if not p:
            return
        win = p["outcome_ids"][winner] if winner is not None and winner < len(p["outcome_ids"]) else None
        if status == "RESOLVED" and not win:
            return

        def done(_result) -> None:
            if status == "LOCKED":
                p["locked"] = True
                self.prediction_bar.show_prediction(p["title"], p["outcomes"], True)
            else:
                self.prediction = None
                self.prediction_bar.hide()
        self.run(self.accounts["twitch"].end_prediction(p["id"], status, win), then=done)
