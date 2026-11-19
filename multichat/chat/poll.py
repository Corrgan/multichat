# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""A poll in the chat of all platforms at once: viewers answer with !1, !2 … in Twitch, Kick
or YouTube, MultiChat counts. Pure logic – the window shows it, the overlay draws it."""
from __future__ import annotations

import re
import time

from ..core.i18n import _
from .models import ChatMessage

MIN_OPTIONS, MAX_OPTIONS = 2, 6
MAX_VOTERS = 200_000  # one entry per voter – a bound for very large chats
_VOTE = re.compile(r"^\s*!\s?(\d{1,2})(?!\d)")  # "!2", "! 2", "!2 because …"; not "!20" for 2 options


class ChatPoll:
    """Counts the votes of the channels `sources` (source keys of one group). One vote per
    person and platform; a later vote replaces the earlier one."""

    def __init__(self, question: str, options: list[str], sources: set[str], seconds: int = 0,
                 now=time.monotonic) -> None:
        options = [o.strip() for o in options if o and o.strip()]
        if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
            raise ValueError(f"a poll needs {MIN_OPTIONS}–{MAX_OPTIONS} options")
        self.question = question.strip()
        self.options = options
        self.sources = set(sources)
        self._now = now
        self.ends_at = now() + seconds if seconds > 0 else None
        self.ended = False
        self.votes: dict[tuple[str, str], int] = {}  # (platform, voter) → option index
        self.chat_counts = [0] * len(options)
        self.per_platform: dict[str, list[int]] = {}
        # the platforms' own polls running alongside ("twitch", "youtube" → votes per option);
        # there the chat's !1 … don't count, so nobody votes twice
        self.native: dict[str, list[int]] = {}

    def add_native(self, platform: str) -> None:
        self.native[platform] = [0] * len(self.options)

    def set_native(self, platform: str, votes: list[int]) -> bool:
        """The latest votes of a platform's own poll. True if they changed."""
        votes = [max(0, int(v)) for v in votes[:len(self.options)]] + [0] * max(0, len(self.options) - len(votes))
        if platform not in self.native or self.native[platform] == votes:
            return False
        self.native[platform] = votes
        return True

    @property
    def counts(self) -> list[int]:
        return [c + sum(n[i] for n in self.native.values()) for i, c in enumerate(self.chat_counts)]

    def vote(self, msg: ChatMessage) -> bool:
        """Counts `msg` if it is a vote in one of the poll's channels. True if the tally changed."""
        if self.ended or msg.event or msg.source not in self.sources or msg.platform in self.native:
            return False
        m = _VOTE.match(msg.text or "")
        if not m:
            return False
        choice = int(m.group(1)) - 1
        if not 0 <= choice < len(self.options):
            return False
        key = (msg.platform, msg.user_id or msg.user.lower())
        old = self.votes.get(key)
        if old == choice or (old is None and len(self.votes) >= MAX_VOTERS):
            return False
        if old is not None:
            self.chat_counts[old] -= 1
            self.per_platform[msg.platform][old] -= 1
        self.votes[key] = choice
        self.chat_counts[choice] += 1
        self.per_platform.setdefault(msg.platform, [0] * len(self.options))[choice] += 1
        return True

    @property
    def total(self) -> int:
        return len(self.votes) + sum(sum(n) for n in self.native.values())

    def remaining(self) -> int | None:
        """Seconds left (None without a time limit)."""
        return None if self.ends_at is None else max(0, round(self.ends_at - self._now()))

    def due(self) -> bool:
        return not self.ended and self.ends_at is not None and self._now() >= self.ends_at

    def end(self) -> None:
        self.ended = True

    def percent(self, i: int) -> int:
        total = self.total
        return round(100 * self.counts[i] / total) if total else 0

    def winners(self) -> list[int]:
        counts = self.counts
        best = max(counts)
        return [i for i, n in enumerate(counts) if n == best] if best else []

    def announcement(self, platform: str = "") -> str:
        """What is sent to a chat when the poll starts – where the platform's own poll runs,
        without the !1 … (people vote there)."""
        if platform in self.native:
            return _("Poll: {title} – {options}").format(title=self.question, options=" / ".join(self.options))
        options = " / ".join(f"!{i + 1} {o}" for i, o in enumerate(self.options))
        return _("Poll: {title} – {options}").format(title=self.question, options=options)

    def result(self) -> str:
        """One line with the result, for the chats and the window."""
        votes = " · ".join(f"{o} {self.percent(i)}%" for i, o in enumerate(self.options))
        if not self.winners():
            return _("Poll ended: {title}").format(title=self.question)
        winner = " / ".join(self.options[i] for i in self.winners())
        return (_("Poll ended: {title} – winner: {winner} ({votes} votes)").format(
            title=self.question, winner=winner, votes=self.total) + f" – {votes}")

    def payload(self, groups: list[int] | None = None) -> dict:
        """For the OBS overlay."""
        return {
            "type": "poll", "state": "ended" if self.ended else "active", "question": self.question,
            "options": [{"n": i + 1, "text": o, "votes": n, "pct": self.percent(i),
                         "win": self.ended and i in self.winners()} for i, (o, n) in enumerate(zip(self.options, self.counts))],
            "total": self.total, "remaining": self.remaining(), "groups": groups or [],
            "label": _("{n} votes").format(n=self.total) if self.ended
            else _("{n} votes · answer with !1, !2 …").format(n=self.total),
        }
