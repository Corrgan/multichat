# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: voting in other channels' polls and predictions from MultiChat with the
website login taken over from a browser (accounts/web.py) – the settings dialog and what a
click on a vote link under a poll line does. Mixed into MainWindow."""
from __future__ import annotations

import logging
from dataclasses import replace

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..accounts.web import BROWSERS, NotLoggedIn
from ..core.constants import NAMES, PLATFORMS
from ..core.i18n import _

log = logging.getLogger("multichat")


class WebVoteDialog(QDialog):
    """Turn voting on, take the website logins over from a browser, forget them."""

    def __init__(self, parent, web, enabled: bool) -> None:
        super().__init__(parent)
        self.win, self.web = parent, web
        self.setWindowTitle(_("Vote in other channels"))
        lay = QVBoxLayout(self)
        warn = QLabel(_(
            "<b>Unofficial.</b> The platforms have no interface for viewers to vote. MultiChat votes the way their "
            "websites do, with your <b>website login</b>, taken over from a browser on this computer where you are "
            "logged in. The platforms don't allow this in their terms – in the worst case they can restrict your "
            "account – and it stops working when a website changes. MultiChat only votes when you click."))
        warn.setWordWrap(True)
        lay.addWidget(warn)
        self.enabled = QCheckBox(_("Show vote links under polls and predictions"))
        self.enabled.setChecked(enabled)
        lay.addWidget(self.enabled)
        grid = QGridLayout()
        self.status: dict[str, QLabel] = {}
        self.browser: dict[str, QComboBox] = {}
        for row, p in enumerate(PLATFORMS):
            grid.addWidget(QLabel(f"<b>{NAMES[p]}</b>"), row, 0)
            self.status[p] = QLabel()
            grid.addWidget(self.status[p], row, 1)
            combo = QComboBox()
            for b in BROWSERS:
                combo.addItem(b.capitalize(), b)
            used = web.store.get(p).get("browser")
            if used in BROWSERS:
                combo.setCurrentIndex(BROWSERS.index(used))
            self.browser[p] = combo
            grid.addWidget(combo, row, 2)
            take = QPushButton(_("Take over login"))
            take.clicked.connect(lambda _c=False, p=p: self.take_over(p))
            grid.addWidget(take, row, 3)
            forget = QPushButton(_("Forget"))
            forget.clicked.connect(lambda _c=False, p=p: self.forget(p))
            grid.addWidget(forget, row, 4)
        lay.addLayout(grid)
        hint = QLabel(_("Log in on the platform's website in that browser first. Close Chrome-based browsers "
                        "if reading fails. The login ends when you log out there – take it over again then."))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.accept)
        lay.addWidget(bb)
        self._show()
        # wide enough for the warning and the rows at the current font, as high as the wrapped text needs
        # (Qt's own guess for word-wrapped labels was a narrow, unreadable window)
        width = max(self.sizeHint().width(), self.fontMetrics().averageCharWidth() * 95)
        self.resize(width, max(self.sizeHint().height(), lay.totalHeightForWidth(width)))

    def _show(self) -> None:
        for p, label in self.status.items():
            label.setText(_("taken over ({name})").format(name=self.web.name(p) or _("logged in"))
                          if self.web.has(p) else _("not taken over"))

    def take_over(self, platform: str) -> None:
        browser = self.browser[platform].currentData()
        self.status[platform].setText(_("reading …"))
        self._error = ""

        async def read() -> str:
            import asyncio
            try:
                return await asyncio.to_thread(self.web.take_over, platform, browser)
            except NotLoggedIn as e:
                self._error = str(e)
                raise

        self.win.run(read(), then=lambda _name: self._show(), on_error=self._failed,
                     label=_("{platform} login taken over").format(platform=NAMES[platform]))

    def _failed(self) -> None:
        self._show()
        if self._error:  # what to do (e.g. the Flatpak command) – selectable, the status bar can't be copied
            box = QMessageBox(QMessageBox.Icon.Warning, _("Take over login"), self._error,
                              QMessageBox.StandardButton.Ok, self)
            box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.open()
            self.error_box = box

    def forget(self, platform: str) -> None:
        self.web.forget(platform)
        self._show()


class VoteMixin:
    """Vote part of MainWindow (expects web, config, panels, _route and run): feeds each group's
    PollBox (ui/poll_box.py) and votes when one of its buttons is clicked."""

    def can_vote(self, platform: str) -> bool:
        return bool(self.config.get("web_vote")) and self.web.has(platform)

    def vote_block(self, state) -> str:
        """Why a click can't vote in this poll/prediction ("" = it can)."""
        if not self.can_vote(state.platform):
            return _("To vote from here: Accounts → Vote in other channels (unofficial) …")
        if state.platform == "twitch" and state.kind == "prediction":
            own = {n.lower() for n in (self.web.name("twitch"), self.accounts["twitch"].user_name) if n}
            if state.channel.lower() in own:
                return _("Twitch doesn't let you bet in the predictions of your own channel")
        return ""

    def poll_box_state(self, state) -> None:
        """A poll/prediction started, changed or ended in one of the channels."""
        why = self.vote_block(state)
        for panel in self._route.get(state.source, []):
            panel.poll_box.update_state(replace(state), not why, why)
        if not hasattr(self, "_poll_box_timer"):
            self._poll_box_timer = QTimer(self, interval=1000)
            self._poll_box_timer.timeout.connect(self._poll_box_tick)
        if not self._poll_box_timer.isActive():
            self._poll_box_timer.start()

    def _poll_box_tick(self) -> None:
        for panel in self.panels:
            panel.poll_box.tick()
        if not any(panel.poll_box.cards for panel in self.panels):
            self._poll_box_timer.stop()

    def _refresh_votes(self) -> None:
        for panel in self.panels:
            panel.poll_box.refresh(self.vote_block)

    def edit_web_vote(self) -> None:
        dlg = WebVoteDialog(self, self.web, bool(self.config.get("web_vote")))
        dlg.exec()
        self.config["web_vote"] = dlg.enabled.isChecked()
        self.save_config()
        self._refresh_votes()

    def poll_box_vote(self, card, index: int) -> None:
        s = card.state
        if s.status != "active" or self.vote_block(s) or not 0 <= index < len(s.options):
            return
        option_id, label, _value = s.options[index]
        amount = 0
        if s.kind == "prediction":
            amount, ok = QInputDialog.getInt(self, _("Prediction"), _("Channel points on “{outcome}”:").format(
                outcome=label), 100, 1, 1_000_000)
            if not ok:
                return
        web, p = self.web, s.platform
        if p == "kick":
            coro = web.kick_poll(s.channel, int(option_id)) if s.kind == "poll" else \
                web.kick_prediction(s.channel, option_id, amount)
        elif p == "twitch":
            coro = web.twitch_poll(s.id, option_id) if s.kind == "poll" else \
                web.twitch_prediction(s.id, option_id, amount)
        elif p == "youtube" and s.kind == "poll":
            coro = web.youtube_poll(s.channel, s.id, index)
        else:
            return
        log.info("Vote: %s %s %s option %d", p, s.kind, s.channel, index)

        def done(_result) -> None:
            card.chosen = index
            why = self.vote_block(card.state)
            card.update_state(card.state, not why, why)
        self.run(coro, then=done, label=_("Voted for “{option}”").format(option=label))
