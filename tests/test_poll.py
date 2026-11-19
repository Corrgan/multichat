# SPDX-License-Identifier: GPL-3.0-or-later
"""Chat poll: !1, !2 … from Twitch, Kick and YouTube counted together."""
import pytest

from multichat.chat.models import ChatMessage
from multichat.chat.poll import ChatPoll

SOURCES = {"twitch:chan", "kick:chan", "youtube:vid"}


def msg(platform, user, text, uid=None, source=None, event=""):
    m = ChatMessage(platform, "chan", user, text, user_id=uid, event=event)
    m.source = source or {"twitch": "twitch:chan", "kick": "kick:chan", "youtube": "youtube:vid"}[platform]
    return m


def test_votes_from_all_platforms_one_per_person():
    p = ChatPoll(" Next game? ", ["WoW", "Werkstatt", " "], SOURCES)
    assert p.options == ["WoW", "Werkstatt"] and p.question == "Next game?"
    assert p.vote(msg("twitch", "Alice", "!1", "1"))
    assert p.vote(msg("kick", "Alice", "!2 weil Autos", "1"))  # same name, other platform: another person
    assert p.vote(msg("youtube", "Bob", "! 2", "UCb"))
    assert not p.vote(msg("twitch", "Alice", "!1", "1"))  # the same vote again
    assert p.vote(msg("twitch", "alice", "!2", "1"))  # changes her mind: replaces
    assert p.counts == [0, 3] and p.total == 3 and p.per_platform["twitch"] == [0, 1]
    for text in ("1", "!3", "!0", "!20", "!12", "hi !1", "!x"):
        assert not p.vote(msg("twitch", "Carol", text, "3")), text
    assert not p.vote(msg("twitch", "Dave", "!1", "4", source="twitch:otherchan"))  # not in the group
    assert not p.vote(msg("twitch", "Erin", "!1", "5", event="bits"))  # an alert is no vote
    assert p.vote(msg("twitch", "NoId", "!1"))  # without user ID: by name
    assert not p.vote(msg("twitch", "NOID", "!1"))


def test_result_time_limit_and_overlay_payload():
    clock = [100.0]
    p = ChatPoll("Pizza?", ["Ja", "Nein"], SOURCES, seconds=60, now=lambda: clock[0])
    assert p.remaining() == 60 and not p.due()
    assert p.result() == "Poll ended: Pizza?"  # no votes: no winner
    for i, choice in enumerate(("!1", "!1", "!2")):
        p.vote(msg("twitch", f"u{i}", choice, str(i)))
    assert p.announcement() == "Poll: Pizza? – !1 Ja / !2 Nein"
    pay = p.payload([2])
    assert pay["state"] == "active" and pay["groups"] == [2] and pay["total"] == 3
    assert [(o["n"], o["votes"], o["pct"], o["win"]) for o in pay["options"]] == [(1, 2, 67, False), (2, 1, 33, False)]
    clock[0] = 161
    assert p.due() and p.remaining() == 0
    p.end()
    assert not p.vote(msg("twitch", "late", "!2", "9"))
    assert p.result() == "Poll ended: Pizza? – winner: Ja (3 votes) – Ja 67% · Nein 33%"
    pay = p.payload()
    assert pay["state"] == "ended" and pay["options"][0]["win"] and pay["label"] == "3 votes"


def test_needs_two_to_six_options():
    with pytest.raises(ValueError):
        ChatPoll("?", ["only one"], SOURCES)
    with pytest.raises(ValueError):
        ChatPoll("?", [str(i) for i in range(7)], SOURCES)
    p = ChatPoll("Tie?", ["a", "b"], SOURCES)
    p.vote(msg("twitch", "x", "!1", "1"))
    p.vote(msg("kick", "y", "!2", "2"))
    assert p.winners() == [0, 1] and "winner: a / b" in p.result()
