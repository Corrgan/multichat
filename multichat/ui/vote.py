# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: voting in other channels' polls and predictions from MultiChat with the
website login taken over from a browser (accounts/web.py) – the settings dialog and what a
click on a vote link under a poll line does. Mixed into MainWindow."""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QInputDialog, QLabel, QPushButton,
    QVBoxLayout,
)

from ..accounts.web import BROWSERS
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

        async def read() -> str:
            import asyncio
            return await asyncio.to_thread(self.web.take_over, platform, browser)

        self.win.run(read(), then=lambda _name: self._show(), on_error=self._show,
                     label=_("{platform} login taken over").format(platform=NAMES[platform]))

    def forget(self, platform: str) -> None:
        self.web.forget(platform)
        self._show()


class VoteMixin:
    """Vote part of MainWindow (expects web, config, panels, message_by_uid, update_lines, run)."""

    def vote_platforms(self) -> set[str]:
        if not self.config.get("web_vote"):
            return set()
        return {p for p in PLATFORMS if self.web.has(p)}

    def _refresh_votes(self) -> None:
        platforms = self.vote_platforms()
        for panel in self.panels:
            panel.view.vote_platforms = platforms
        self.render_all()

    def edit_web_vote(self) -> None:
        dlg = WebVoteDialog(self, self.web, bool(self.config.get("web_vote")))
        dlg.exec()
        self.config["web_vote"] = dlg.enabled.isChecked()
        self.save_config()
        self._refresh_votes()

    def vote_clicked(self, uid: int, index: int) -> None:
        msg = self.message_by_uid(uid)
        v = msg.vote if msg else None
        if not v or msg.platform not in self.vote_platforms() or not 0 <= index < len(v["options"]):
            return
        option_id, label = v["options"][index]
        amount = 0
        if v["kind"] == "prediction":
            amount, ok = QInputDialog.getInt(self, _("Prediction"), _("Channel points on “{outcome}”:").format(
                outcome=label), 100, 1, 1_000_000)
            if not ok:
                return
        web, p = self.web, msg.platform
        if p == "kick":
            coro = web.kick_poll(msg.channel, int(option_id)) if v["kind"] == "poll" else \
                web.kick_prediction(msg.channel, option_id, amount)
        elif p == "twitch":
            coro = web.twitch_poll(v["id"], option_id) if v["kind"] == "poll" else \
                web.twitch_prediction(v["id"], option_id, amount)
        elif p == "youtube" and v["kind"] == "poll":
            coro = web.youtube_poll(msg.channel, v["id"], index)
        else:
            return
        log.info("Vote: %s %s %s option %d", p, v["kind"], msg.channel, index)

        def done(_result) -> None:
            v["chosen"] = index
            self.update_lines([msg])
        self.run(coro, then=done, label=_("Voted for “{option}”").format(option=label))
