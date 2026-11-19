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


def test_platform_polls_add_their_votes_and_replace_the_chat_there():
    p = ChatPoll("Pizza?", ["Ja", "Nein"], SOURCES)
    p.add_native("twitch")
    assert not p.vote(msg("twitch", "Alice", "!1", "1"))  # on Twitch people click the Twitch poll
    assert p.vote(msg("kick", "Bob", "!2", "2"))
    assert p.set_native("twitch", [5, 1]) and not p.set_native("twitch", [5, 1])
    assert not p.set_native("youtube", [9, 9])  # no YouTube poll alongside
    assert p.counts == [5, 2] and p.total == 7 and p.percent(0) == 71
    assert p.announcement("twitch") == "Poll: Pizza? – Ja / Nein"
    assert p.announcement("kick") == "Poll: Pizza? – !1 Ja / !2 Nein"
    p.set_native("twitch", [1])  # a short answer: padded
    assert p.native["twitch"] == [1, 0]


def test_account_calls_for_polls_and_predictions():
    import asyncio

    from multichat.accounts import twitch as tw, youtube as yt

    calls = []

    def fake(answers):
        async def request(method, url, **kw):
            calls.append((method, url.rsplit("/", 2)[-2:], kw))
            return answers.pop(0)
        return request

    class Store:
        def get(self, platform):
            return {"user_id": "77", "user_name": "Corrgan", "access_token": "x"}

    t = tw.TwitchAccount.__new__(tw.TwitchAccount)
    t.store = Store()
    t.request = fake([
        {"data": [{"id": "p1"}]},
        {"data": [{"status": "ACTIVE", "choices": [{"votes": 3}, {"votes": "4"}]}]},
        {"data": [{"status": "TERMINATED", "choices": [{"votes": 5}, {"votes": 4}]}]},
        {"data": [{"id": "pr1", "outcomes": [{"id": "o1"}, {"id": "o2"}]}]},
        None,
    ])
    assert t.owns("corrgan") and t.owns("https://twitch.tv/Corrgan") and not t.owns("other")
    assert asyncio.run(t.create_poll("x" * 70, ["a" * 30, "b"], 0)) == "p1"
    method, _url, kw = calls[-1]
    assert method == "POST" and kw["json"]["duration"] == 15 and len(kw["json"]["title"]) == 60
    assert kw["json"]["choices"] == [{"title": "a" * 25}, {"title": "b"}] and kw["json"]["broadcaster_id"] == "77"
    assert asyncio.run(t.poll_votes("p1")) == ("ACTIVE", [3, 4])
    assert asyncio.run(t.end_poll("p1")) == ("TERMINATED", [5, 4]) and calls[-1][2]["json"]["status"] == "TERMINATED"
    assert asyncio.run(t.create_prediction("Boss?", ["Ja", "Nein"], 9999)) == ("pr1", ["o1", "o2"])
    assert calls[-1][2]["json"]["prediction_window"] == 1800
    asyncio.run(t.end_prediction("pr1", "RESOLVED", "o1"))
    assert calls[-1][0] == "PATCH" and calls[-1][2]["json"] == {
        "broadcaster_id": "77", "id": "pr1", "status": "RESOLVED", "winning_outcome_id": "o1"}

    y = yt.YouTubeAccount.__new__(yt.YouTubeAccount)

    async def chat_id(channel):
        return "chat1"

    y.live_chat_id = chat_id
    poll_msg = {"id": "m1", "snippet": {"type": "pollEvent", "pollDetails": {"metadata": {
        "questionText": "Pizza?", "status": "active", "options": [{"optionText": "Ja", "tally": "6"},
                                                                  {"optionText": "Nein", "tally": 2}]}}}}
    closed = {**poll_msg, "snippet": {"pollDetails": {"metadata": {**poll_msg["snippet"]["pollDetails"]["metadata"],
                                                                     "status": "closed"}}}}
    y.request = fake([{"id": "m1"}, {"items": [], "activePollItem": poll_msg}, {"items": []}, closed])
    assert asyncio.run(y.create_poll("@corrgan", "Pizza?", ["Ja", "Nein"])) == ("chat1", "m1")
    body = calls[-1][2]["json"]["snippet"]
    assert body["type"] == "pollEvent" and body["pollDetails"]["metadata"]["options"] == [{"optionText": "Ja"},
                                                                                         {"optionText": "Nein"}]
    assert asyncio.run(y.poll_votes("chat1", "m1")) == ("active", [6, 2])
    assert asyncio.run(y.poll_votes("chat1", "m1")) == ("closed", [])  # no longer the active poll
    assert asyncio.run(y.end_poll("m1")) == ("closed", [6, 2])
    assert calls[-1][1] == ["messages", "transition"] and calls[-1][2]["params"]["status"] == "closed"
