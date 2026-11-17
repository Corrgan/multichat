# SPDX-License-Identifier: GPL-3.0-or-later
"""Twitch EventSub: the lines made from the payloads, and the WebSocket protocol
(welcome → subscriptions, keepalive timeout, reconnect, duplicates, missing scopes)."""
import asyncio
import json

import pytest

from multichat.accounts.oauth import ApiError
from multichat.chat import twitch_eventsub as es
from multichat.chat.twitch_eventsub_events import SUBSCRIPTIONS, eventsub_text


# ---- payload → text ---------------------------------------------------- #
def test_texts():
    t = eventsub_text
    assert t("channel.follow", {"user_name": "Alice", "user_login": "alice"}) == "Alice followed"
    assert t("channel.follow", {"user_login": "alice"}) == "alice followed"
    reward = {"user_name": "Bob", "reward": {"title": "Hydrate", "cost": 100}, "user_input": ""}
    assert t("channel.channel_points_custom_reward_redemption.add", reward) == "Bob redeemed Hydrate"
    poll = {"title": "Next game?", "choices": [{"id": "1", "title": "A"}, {"id": "2", "title": "B"}, "junk"]}
    assert t("channel.poll.begin", poll) == "Poll: Next game? – A / B"
    end = {"title": "Next game?", "status": "completed",
           "choices": [{"title": "A", "votes": 3}, {"title": "B", "votes": 7}]}
    assert t("channel.poll.end", end) == "Poll ended: Next game? – winner: B (7 votes)"
    tie = {**end, "choices": [{"title": "A", "votes": 2}, {"title": "B", "votes": 2}]}
    assert "winner: A / B (2 votes)" in t("channel.poll.end", tie)
    assert t("channel.poll.end", {**end, "status": "terminated"}) == "Poll ended: Next game?"
    outcomes = [{"id": "x", "title": "Yes"}, {"id": "y", "title": "No"}]
    pred = {"title": "Win?", "outcomes": outcomes}
    assert t("channel.prediction.begin", pred) == "Prediction: Win? – Yes / No"
    assert t("channel.prediction.lock", pred) == "Prediction locked: Win?"
    assert t("channel.prediction.end", {**pred, "status": "resolved", "winning_outcome_id": "y"}) == \
        "Prediction ended: Win? – winner: No"
    assert t("channel.prediction.end", {**pred, "status": "canceled"}) == "Prediction canceled: Win?"
    assert t("channel.hype_train.begin", {"level": 2}) == "Hype Train started (level 2)"
    assert t("channel.hype_train.end", {"level": "4"}) == "Hype Train ended (level 4)"
    assert t("channel.ad_break.begin", {"duration_seconds": "90"}) == "Ad break (90 s)"
    assert t("channel.ad_break.begin", {"duration_seconds": 60}) == "Ad break (60 s)"


def test_redemption_with_input_is_left_to_irc():
    base = {"user_name": "Bob", "reward": {"title": "Say it"}}
    kind = "channel.channel_points_custom_reward_redemption.add"
    assert eventsub_text(kind, {**base, "user_input": "hello"}) == ""
    assert eventsub_text(kind, {**base, "user_input": "  "}) == "Bob redeemed Say it"


@pytest.mark.parametrize("kind", sorted(SUBSCRIPTIONS))
@pytest.mark.parametrize("event", [None, [], "x", {}, {"title": 5, "choices": "x", "outcomes": [None, 3], "reward": 7,
                                                        "level": None, "duration_seconds": "n/a", "user_name": 4}])
def test_broken_payloads_never_raise(kind, event):
    assert isinstance(eventsub_text(kind, event), str)


def test_unknown_type_is_ignored():
    assert eventsub_text("channel.something.new", {"title": "x"}) == ""


# ---- protocol ---------------------------------------------------------- #
class FakeWS:
    def __init__(self, url, log):
        self.url, self.log, self.inbox, self.closed = url, log, asyncio.Queue(), False

    def push(self, kind, payload=None, mid=None, **meta):
        self.inbox.put_nowait(json.dumps({"metadata": {"message_type": kind, "message_id": mid or f"m{id(payload)}{kind}",
                                                      **meta}, "payload": payload or {}}))

    def welcome(self, sid, keepalive=10, mid=None):
        self.push("session_welcome", {"session": {"id": sid, "keepalive_timeout_seconds": keepalive}}, mid=mid)

    async def recv(self):
        return await self.inbox.get()

    async def close(self):
        self.closed = True
        self.log.append(("close", self.url))


class FakeAccount:
    API = "https://api.example/helix"
    user_name, user_id, session_gen, logged_in = "Me", "10", 0, True

    def __init__(self, forbidden=()):
        self.posts, self.forbidden = [], set(forbidden)

    async def is_moderator(self, login):
        return login in ("me", "modchan")

    async def broadcaster_id(self, login):
        return {"me": "10", "modchan": "20"}[login]

    async def request(self, method, url, **kw):
        body = kw["json"]
        self.posts.append(body)
        if body["type"] in self.forbidden:
            raise ApiError("Twitch (403): missing scope", status=403)


class Harness:
    def __init__(self, monkeypatch, account=None, channels=None):
        monkeypatch.setattr(es, "POLL", 0.02)
        monkeypatch.setattr(es, "KEEPALIVE_BUFFER", 0.0)
        monkeypatch.setattr(es, "Backoff", lambda: type("B", (), {"connected": lambda s: None, "next": lambda s: 0})())
        self.sockets, self.events, self.log = [], [], []
        self.pending = {}  # url → socket prepared by the test
        self.account = account or FakeAccount()
        self.channels = channels or {"twitch:me": "me", "twitch:modchan": "modchan", "twitch:other": "other"}

        async def connect(url):
            ws = self.pending.pop(url, None) or FakeWS(url, self.log)
            self.log.append(("connect", url))
            self.sockets.append(ws)
            return ws

        self.client = es.EventSubClient(self.account, lambda: self.channels,
                                        lambda ev, key: self.events.append((key, ev.event_text)), connect)

    def prepare(self, url):
        ws = self.pending[url] = FakeWS(url, self.log)
        return ws

    async def until(self, pred, what="condition"):
        for _ in range(300):
            if pred():
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"timeout: {what}")


def run(coro_fn, monkeypatch, **kw):
    async def main():
        h = Harness(monkeypatch, **kw)
        task = asyncio.create_task(h.client.run())
        try:
            await coro_fn(h)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())


def test_welcome_creates_subscriptions_and_notifications_arrive(monkeypatch):
    async def scenario(h):
        first = h.prepare(es.EVENTSUB_URL)
        first.welcome("S1")
        await h.until(lambda: len(h.account.posts) == len(SUBSCRIPTIONS) + 1, "subscriptions")
        posts = h.account.posts
        assert {p["transport"]["session_id"] for p in posts} == {"S1"}
        assert all(p["transport"]["method"] == "websocket" for p in posts)
        follow = [p for p in posts if p["type"] == "channel.follow"]
        assert {p["condition"]["broadcaster_user_id"] for p in follow} == {"10", "20"}
        assert all(p["condition"]["moderator_user_id"] == "10" and p["version"] == "2" for p in follow)
        # only your own channel gets the broadcaster-only types; a moderated one just the follows
        assert sum(p["condition"]["broadcaster_user_id"] == "20" for p in posts) == 1
        assert not any(p["condition"]["broadcaster_user_id"] == "30" for p in posts)  # "other": neither

        event = {"user_name": "Alice", "broadcaster_user_login": "modchan"}
        first.push("notification", {"event": event}, mid="n1", subscription_type="channel.follow")
        first.push("notification", {"event": event}, mid="n1", subscription_type="channel.follow")  # duplicate
        first.push("session_keepalive")
        first.push("notification", {"event": {"user_name": "Bob", "broadcaster_user_login": "modchan"}}, mid="n2",
                   subscription_type="channel.follow")
        await h.until(lambda: len(h.events) >= 2, "events")
        await asyncio.sleep(0.05)
        assert h.events == [("twitch:modchan", "Alice followed"), ("twitch:modchan", "Bob followed")]

    run(scenario, monkeypatch)


def test_redemption_with_input_is_not_emitted(monkeypatch):
    async def scenario(h):
        ws = h.prepare(es.EVENTSUB_URL)
        ws.welcome("S1")
        kind = "channel.channel_points_custom_reward_redemption.add"
        for mid, text in (("r1", "hello"), ("r2", "")):
            ev = {"user_name": "Bob", "user_input": text, "reward": {"title": "Hydrate"}, "broadcaster_user_login": "me"}
            ws.push("notification", {"event": ev}, mid=mid, subscription_type=kind)
        await h.until(lambda: h.events, "redemption")
        await asyncio.sleep(0.05)
        assert h.events == [("twitch:me", "Bob redeemed Hydrate")]

    run(scenario, monkeypatch)


def test_missing_scope_is_skipped(monkeypatch):
    async def scenario(h):
        h.prepare(es.EVENTSUB_URL).welcome("S1")
        await h.until(lambda: len(h.account.posts) == len(SUBSCRIPTIONS) + 1, "all attempted")
        await asyncio.sleep(0.05)
        assert len(h.sockets) == 1  # no reconnect because of the 403s

    run(scenario, monkeypatch, account=FakeAccount(forbidden={"channel.poll.begin", "channel.ad_break.begin"}))


def test_keepalive_timeout_reconnects(monkeypatch):
    async def scenario(h):
        h.prepare(es.EVENTSUB_URL).welcome("S1", keepalive=0.05)  # silence afterwards
        await h.until(lambda: len(h.sockets) >= 2, "second connection")
        assert h.sockets[0].closed and h.sockets[1].url == es.EVENTSUB_URL

    run(scenario, monkeypatch)


def test_session_reconnect_keeps_subscriptions_and_closes_old_last(monkeypatch):
    async def scenario(h):
        old = h.prepare(es.EVENTSUB_URL)
        old.welcome("S1")
        await h.until(lambda: len(h.account.posts) == len(SUBSCRIPTIONS) + 1, "subscriptions")
        new = h.prepare("wss://new.example/ws")
        new.welcome("S2")
        old.push("session_reconnect", {"session": {"id": "S1", "reconnect_url": "wss://new.example/ws"}})
        await h.until(lambda: old.closed, "old closed")
        assert h.log.index(("connect", "wss://new.example/ws")) < h.log.index(("close", es.EVENTSUB_URL))
        ev = {"user_name": "Carol", "broadcaster_user_login": "me"}
        new.push("notification", {"event": ev}, mid="n9", subscription_type="channel.follow")
        await h.until(lambda: h.events, "event from the new session")
        assert h.events == [("twitch:me", "Carol followed")]
        assert len(h.account.posts) == len(SUBSCRIPTIONS) + 1  # they carry over, nothing is created twice
        assert len(h.sockets) == 2

    run(scenario, monkeypatch)


def test_revocation_is_not_fatal_and_logout_ends_session(monkeypatch):
    async def scenario(h):
        ws = h.prepare(es.EVENTSUB_URL)
        ws.welcome("S1")
        ws.push("revocation", {"subscription": {"type": "channel.follow", "status": "authorization_revoked"}})
        await h.until(lambda: len(h.account.posts) > 0)
        h.account.logged_in = False
        await h.until(lambda: ws.closed, "closed after logout")
        assert len(h.sockets) == 1

    run(scenario, monkeypatch)


def test_no_session_without_a_usable_channel(monkeypatch):
    async def scenario(h):
        await asyncio.sleep(0.1)
        assert h.sockets == []

    run(scenario, monkeypatch, channels={"twitch:other": "other"})


def test_default_connect_does_not_wait_for_the_closing_handshake():
    client = es.EventSubClient(FakeAccount(), dict, lambda ev, key: None)
    assert client.connect.keywords["close_timeout"] <= 1  # Twitch never answers it (shutdown hung)
