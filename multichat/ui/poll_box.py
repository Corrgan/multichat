# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""The polls and predictions (channel bets) running in a group's channels, above its chat –
like the box the websites show: the question, a button per option with a bar and its share,
the time left; the winner at the end. With the website login taken over and voting turned on
(ui/vote.py) a click votes; otherwise the buttons only show the state."""
from __future__ import annotations

import html
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from ..chat.models import PollState
from ..core.constants import NAMES
from ..core.i18n import _

ENDED_SHOWN_S = 20  # how long a result stays
BAR = {"twitch": "#9146FF", "kick": "#53FC18", "youtube": "#FF0000"}


def _bar_style(color: str, share: int, chosen: bool, winner: bool) -> str:
    p = max(0.0, min(1.0, share / 100))
    fill = "#2e7d4f" if winner else color + "66"  # 40 % alpha
    stops = (f"stop:0 {fill}, stop:{p:.3f} {fill}, stop:{min(1.0, p + 0.001):.3f} rgba(255,255,255,12), "
             f"stop:1 rgba(255,255,255,12)") if p > 0 else "stop:0 rgba(255,255,255,12), stop:1 rgba(255,255,255,12)"
    border = "#e0e0e0" if chosen else "rgba(255,255,255,40)"
    return (f"QPushButton {{ text-align:left; padding:4px 8px; border:1px solid {border}; border-radius:6px;"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {stops}); }}"
            f" QPushButton:hover {{ border-color:#ffffff; }}")


class PollCard(QFrame):
    """One poll or prediction."""

    def __init__(self, state: PollState, vote, close) -> None:
        super().__init__()
        self.state = state
        self.chosen: int | None = None
        self._vote, self.ended_at = vote, 0.0
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(3)
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.info = QLabel()
        x = QToolButton()
        x.setText("×")
        x.setAutoRaise(True)
        x.setToolTip(_("Hide"))
        x.clicked.connect(lambda _checked=False: close())  # clicked(bool) must not reach close
        head.addWidget(self.title, 1)
        head.addWidget(self.info)
        head.addWidget(x)
        lay.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(3)
        lay.addLayout(self.rows)
        self.buttons: list[QPushButton] = []

    def update_state(self, state: PollState, can_vote: bool, why: str) -> None:
        if not state.options:  # YouTube's "closed": keeps the last counts
            state.options, state.title = self.state.options, self.state.title
        self.state = state
        if state.status in ("ended", "gone") and not self.ended_at:
            self.ended_at = time.time()
        kind = _("Prediction") if state.kind == "prediction" else _("Poll")
        where = f"{NAMES.get(state.platform, state.platform)} · {html.escape(state.channel)}"
        self.title.setText(f"<span style='color:#aaa'>{where}</span><br><b>{kind}:</b> {html.escape(state.title or '?')}")
        if len(self.buttons) != len(state.options):
            for b in self.buttons:
                b.deleteLater()
            self.buttons = []
            for i in range(len(state.options)):
                b = QPushButton()
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _c=False, i=i: self._vote(self, i))
                self.rows.addWidget(b)
                self.buttons.append(b)
        open_ = state.status == "active"
        for i, (b, (oid, label, value)) in enumerate(zip(self.buttons, state.options)):
            share = state.share(i)
            mark = "✔ " if self.chosen == i else ""
            win = state.status == "ended" and state.winner and oid == state.winner
            amount = "" if state.percent else (f" · {int(value):,}".replace(",", ".") +
                                               (_(" points") if state.kind == "prediction" else ""))
            b.setText(f"{'🏆 ' if win else ''}{mark}{label}   {share} %{amount}")
            b.setStyleSheet(_bar_style(BAR.get(state.platform, "#888888"), share, self.chosen == i, bool(win)))
            b.setEnabled(open_ and can_vote)
            b.setToolTip("" if can_vote or not open_ else why)
        self.tick()

    def tick(self) -> None:
        s = self.state
        if s.status in ("ended", "gone"):
            self.info.setText(_("ended"))
        elif s.status == "locked":
            self.info.setText(_("bets locked"))
        elif s.ends_at:
            left = max(0, int(s.ends_at - time.time()))
            self.info.setText(f"{left // 60}:{left % 60:02d}")
        else:
            self.info.setText("")


class PollBox(QWidget):
    """All running polls/predictions of a group's channels (usually none: then it takes no space)."""

    def __init__(self, vote) -> None:
        super().__init__()
        self._vote = vote
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 2)
        self.lay.setSpacing(3)
        self.cards: dict[tuple, PollCard] = {}
        self.hidden: set[tuple] = set()
        self.hide()

    @staticmethod
    def key(s: PollState) -> tuple:
        return (s.source, s.kind, s.id)

    def update_state(self, state: PollState, can_vote: bool, why: str) -> None:
        key = self.key(state)
        if key in self.hidden:
            return
        card = self.cards.get(key)
        if card is None:
            if state.status in ("ended", "gone") or not state.options:
                return  # nothing seen of it before: no box only for its end
            if state.kind == "poll":  # a new poll replaces an older one of the same channel (Kick: no IDs)
                for k in [k for k in self.cards if k[:2] == key[:2]]:
                    self._remove(k)
            card = self.cards[key] = PollCard(state, self._vote, lambda k=key: self.close_card(k))
            self.lay.addWidget(card)
        card.update_state(state, can_vote, why)
        if state.status == "gone":
            self._remove(key)
        self.setVisible(bool(self.cards))

    def tick(self) -> None:
        """Every second: the time left; results that were shown long enough go."""
        now = time.time()
        for key, card in list(self.cards.items()):
            if card.ended_at and now - card.ended_at > ENDED_SHOWN_S:
                self._remove(key)
            elif card.state.status == "active" and card.state.ends_at and now > card.state.ends_at + 60:
                self._remove(key)  # its end never came (connection lost)
            else:
                card.tick()
        self.setVisible(bool(self.cards))

    def close_card(self, key: tuple) -> None:
        self.hidden.add(key)
        self._remove(key)
        self.setVisible(bool(self.cards))

    def refresh(self, block) -> None:
        """After the vote setting or a login changed – block(state): why one can't vote ("" = can)."""
        for card in self.cards.values():
            why = block(card.state)
            card.update_state(card.state, not why, why)

    def _remove(self, key: tuple) -> None:
        card = self.cards.pop(key, None)
        if card is not None:
            card.deleteLater()
