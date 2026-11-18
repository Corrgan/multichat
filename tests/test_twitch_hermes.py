# SPDX-License-Identifier: GPL-3.0-or-later
"""Twitch website event feed (Hermes): the lines made from polls, predictions and hype
trains, and the WebSocket protocol (welcome → subscriptions, keepalive timeout, reconnect,
channels covered by EventSub)."""
import asyncio
import json

import pytest

from multichat.chat import twitch_hermes as th
from multichat.chat.twitch_hermes_events import TOPICS, HermesEvents, poll_text


# ---- payload → text ---------------------------------------------------- #
def poll(kind, status="ACTIVE", votes=(0, 0)):
    choices = [{"choice_id": str(i), "title": t, "votes": {"total": v}} for i, (t, v) in enumerate(zip("AB", votes))]
    return {"type": kind, "data": {"poll": {"title": "Next game?", "status": status, "choices": choices}}}


def test_poll_texts():
    assert poll_text(poll("POLL_CREATE")) == "Poll: Next game? – A / B"
    assert poll_text(poll("POLL_UPDATE", votes=(1, 0))) == ""
    assert poll_text(poll("POLL_COMPLETE", "COMPLETED", (3, 7))) == "Poll ended: Next game? – winner: B (7 votes)"
    assert poll_text(poll("POLL_TERMINATE", "TERMINATED", (3, 7))) == "Poll ended: Next game?"
    assert poll_text(poll("POLL_ARCHIVE", "ARCHIVED", (3, 7))) == ""


def prediction(status, kind="event-updated", winner=None):
    outcomes = [{"id": "x", "title": "Yes"}, {"id": "y", "title": "No"}]
    return {"type": kind, "data": {"event": {"id": "p1", "title": "Win?", "status": status, "outcomes": outcomes,
                                             "winning_outcome_id": winner}}}


def test_prediction_shows_each_status_once():
    ev = HermesEvents()
    lines = [ev.text("prediction", m) for m in (
        prediction("ACTIVE", "event-created"), prediction("ACTIVE"), prediction("ACTIVE"),  # bets resend the state
        prediction("LOCKED"), prediction("RESOLVE_PENDING"), prediction("RESOLVED", winner="y"), prediction("RESOLVED"))]
    assert [t for t in lines if t] == ["Prediction: Win? – Yes / No", "Prediction locked: Win?",
                                       "Prediction ended: Win? – winner: No"]
    assert ev.text("prediction", {**prediction("CANCELED"), "data": {"event": {"id": "p2", "title": "Lose?",
                                                                               "status": "CANCELED"}}}) == \
        "Prediction canceled: Lose?"


def train(kind, level=None, tid="t1"):
    data = {"id": tid}
    if level is not None:
        data["progress"] = {"level": {"value": level, "goal": 1000}}
    return {"type": kind, "data": data}


def test_hype_train_start_levels_and_end():
    ev = HermesEvents()
    lines = [ev.text("hype_train", m) for m in (
        train("hype-train-progression", 1), train("hype-train-progression", 1), train("hype-train-level-up", 2),
        train("hype-train-progression", 2), train("hype-train-progression", 3), train("hype-train-end"))]
    assert [t for t in lines if t] == ["Hype Train started (level 1)", "Hype Train level 2", "Hype Train level 3",
                                       "Hype Train ended (level 3)"]


def test_hype_train_running_before_connecting():
    ev = HermesEvents()
    assert ev.text("hype_train", train("hype-train-progression", 4)) == ""  # not "started": it was running already
    assert ev.text("hype_train", train("hype-train-progression", 5)) == "Hype Train level 5"
    assert ev.text("hype_train", train("hype-train-end", tid="t9")) == "Hype Train ended"  # level unknown


@pytest.mark.parametrize("kind", sorted(set(TOPICS.values())))
@pytest.mark.parametrize("msg", [None, [], "x", {}, {"type": 5, "data": "x"}, {"type": "POLL_CREATE", "data": {"poll": 3}},
                                 {"type": "event-updated", "data": {"event": {"id": 7, "status": None}}},
                                 {"type": "hype-train-progression", "data": {"id": "t", "progress": {"level": "x"}}},
                                 {"type": "raid_go_v2", "raid": "x"}, {"type": "raid_go_v2", "raid": {"id": [1]}},
                                 {"type": "goal_achieved", "data": {"goal": []}},
                                 {"type": "pin-message", "data": {"message": 5, "pinned_by": "x"}},
                                 {"type": "chat_rich_embed", "data": {"title": 3, "twitch_metadata": "x"}},
                                 {"type": "session-started",
                                  "data": {"session": {"participants": ["x", {"channel_id": 1}]}}},
                                 {"type": "reward-redeemed", "data": {"redemption": {"user": 1, "reward": "x"}}}])
def test_broken_payloads_never_raise(kind, msg):
    assert isinstance(HermesEvents().text(kind, msg), str)


# ---- protocol ---------------------------------------------------------- #
class FakeWS:
    def __init__(self, url, log):
        self.url, self.log, self.inbox, self.sent, self.closed = url, log, asyncio.Queue(), [], False

    def push(self, msg):
        self.inbox.put_nowait(json.dumps(msg))

    def welcome(self, keepalive=10):
        self.push({"type": "welcome", "welcome": {"keepaliveSec": keepalive}})

    def notify(self, topic_prefix, login_id, payload):
        sid = next(s["subscribe"]["id"] for s in self.subscriptions()
                   if s["subscribe"]["pubsub"]["topic"] == f"{topic_prefix}.{login_id}")
        self.push({"type": "notification", "notification": {"subscription": {"id": sid}, "type": "pubsub",
                                                            "pubsub": json.dumps(payload)}})

    def subscriptions(self):
        return [json.loads(s) for s in self.sent if json.loads(s).get("type") == "subscribe"]

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        return await self.inbox.get()

    async def close(self):
        self.closed = True
        self.log.append(("close", self.url))


class Harness:
    def __init__(self, monkeypatch, channels=None, covered=()):
        monkeypatch.setattr(th, "POLL", 0.02)
        monkeypatch.setattr(th, "KEEPALIVE_BUFFER", 0.0)
        monkeypatch.setattr(th, "Backoff", lambda: type("B", (), {"connected": lambda s: None, "next": lambda s: 0})())
        self.sockets, self.events, self.log, self.lookups = [], [], [], []
        self.channels = channels if channels is not None else {"twitch:alpha": "alpha", "twitch:me": "me"}
        self.covered = set(covered)  # (login, EventSub type)

        async def connect(url):
            ws = FakeWS(url, self.log)
            self.log.append(("connect", url))
            self.sockets.append(ws)
            ws.welcome()
            return ws

        async def lookup(login):
            self.lookups.append(login)
            if login == "missing":
                raise ValueError("unknown channel")
            return {"alpha": "100", "me": "10", "beta": "200"}[login]

        self.client = th.HermesClient(lambda: self.channels, lambda ev, key: self.events.append((key, ev.event_text)),
                                      lambda login, kind: (login, kind) in self.covered, lambda: None, connect, lookup)

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


def test_subscribes_every_channel_and_turns_notifications_into_lines(monkeypatch):
    async def scenario(h):
        await h.until(lambda: h.sockets and len(h.sockets[0].subscriptions()) == 2 * len(TOPICS), "subscriptions")
        ws = h.sockets[0]
        assert ws.url == th.HERMES_URL
        topics = {s["subscribe"]["pubsub"]["topic"] for s in ws.subscriptions()}
        assert topics == {f"{t}.{cid}" for t in TOPICS for cid in ("100", "10")}
        assert all(s["subscribe"]["type"] == "pubsub" for s in ws.subscriptions())
        ws.push({"type": "keepalive"})
        ws.notify("polls", "100", poll("POLL_CREATE"))
        ws.notify("hype-train-events-v2", "100", train("hype-train-progression", 1))
        await h.until(lambda: len(h.events) >= 2, "events")
        assert h.events == [("twitch:alpha", "Poll: Next game? – A / B"), ("twitch:alpha", "Hype Train started (level 1)")]

    run(scenario, monkeypatch)


def test_what_eventsub_brings_is_left_out(monkeypatch):
    async def scenario(h):
        await h.until(lambda: h.sockets and len(h.sockets[0].subscriptions()) == 2 * len(TOPICS), "subscriptions")
        ws = h.sockets[0]
        ws.notify("polls", "10", poll("POLL_CREATE"))  # own channel: EventSub has it
        ws.notify("predictions-channel-v1", "10", prediction("ACTIVE", "event-created"))  # not covered there
        await h.until(lambda: h.events, "prediction")
        await asyncio.sleep(0.05)
        assert h.events == [("twitch:me", "Prediction: Win? – Yes / No")]

    run(scenario, monkeypatch, covered={("me", "channel.poll.begin")})


def test_new_channels_join_and_unknown_ones_are_retried(monkeypatch):
    async def scenario(h):
        await h.until(lambda: h.sockets and len(h.sockets[0].subscriptions()) == len(TOPICS), "first channel")
        h.channels["twitch:beta"] = "beta"
        await h.until(lambda: len(h.sockets[0].subscriptions()) == 2 * len(TOPICS), "second channel")
        await h.until(lambda: h.lookups.count("missing") >= 2, "retried")
        assert len(h.sockets) == 1 and h.lookups.count("alpha") == 1

    run(scenario, monkeypatch, channels={"twitch:alpha": "alpha", "twitch:missing": "missing"})


def test_disconnected_channel_gets_nothing(monkeypatch):
    async def scenario(h):
        await h.until(lambda: h.sockets and len(h.sockets[0].subscriptions()) == 2 * len(TOPICS), "subscriptions")
        del h.channels["twitch:alpha"]
        h.sockets[0].notify("polls", "100", poll("POLL_CREATE"))
        h.sockets[0].notify("polls", "10", poll("POLL_CREATE"))
        await h.until(lambda: h.events, "event")
        await asyncio.sleep(0.05)
        assert h.events == [("twitch:me", "Poll: Next game? – A / B")]

    run(scenario, monkeypatch)


def test_keepalive_timeout_and_reconnect_request_connect_again(monkeypatch):
    async def scenario(h):
        await h.until(lambda: h.sockets, "first connection")
        h.sockets[0].push({"type": "welcome", "welcome": {"keepaliveSec": 0.05}})  # ignored: only the first counts
        h.sockets[0].push({"type": "reconnect", "reconnect": {"url": "wss://elsewhere"}})
        await h.until(lambda: len(h.sockets) >= 2, "after the reconnect request")
        assert h.sockets[0].closed and h.sockets[1].url == th.HERMES_URL

    run(scenario, monkeypatch)


def test_silence_reconnects(monkeypatch):
    async def scenario(h):
        await h.until(lambda: len(h.sockets) >= 2, "second connection after silence")
        assert h.sockets[0].closed

    monkeypatch.setattr(th, "DEFAULT_KEEPALIVE", 0.05)
    monkeypatch.setattr(FakeWS, "welcome", lambda self, keepalive=0: self.push({"type": "welcome", "welcome": {}}))
    run(scenario, monkeypatch)


def test_no_connection_without_twitch_channels(monkeypatch):
    async def scenario(h):
        await asyncio.sleep(0.1)
        assert h.sockets == []

    run(scenario, monkeypatch, channels={})


def test_default_connect_does_not_wait_for_the_closing_handshake():
    client = th.HermesClient(dict, lambda ev, key: None, lambda *_: False, lambda: None)
    assert client.connect.keywords["close_timeout"] <= 1


# ---- more of the website's events (payloads as recorded 17/18 Nov 2026) ---- #
def test_redemptions_without_text_raids_shoutouts_goals():
    ev = HermesEvents()
    red = {"type": "reward-redeemed", "data": {"redemption": {"user": {"login": "susi", "display_name": "Susi"},
                                                              "reward": {"title": "Hydrate", "cost": 1000}}}}
    assert ev.text("redemption", red) == "Susi redeemed Hydrate"
    red["data"]["redemption"]["user_input"] = "hello"  # comes as a chat message over IRC already
    assert ev.text("redemption", red) == ""
    raid = {"type": "raid_go_v2", "raid": {"id": "r1", "target_display_name": "Friend", "viewer_count": 8616}}
    assert ev.text("raid", raid) == "Raiding Friend with 8616 viewers"
    assert ev.text("raid", raid) == ""  # Twitch sends it several times
    assert ev.text("raid", {**raid, "type": "raid_update_v2"}) == ""
    assert ev.text("shoutout", {"type": "create", "data": {"targetUserDisplayName": "CptMary"}}) == \
        "Shoutout to CptMary"
    goal = {"type": "goal_achieved", "data": {"goal": {"id": "g1", "contributionType": "SUBS", "description": "",
                                                       "targetContributions": 6900}}}
    assert ev.text("goal", goal) == "Goal reached: 6900 subs"
    assert ev.text("goal", goal) == ""
    assert ev.text("goal", {**goal, "type": "goal_updated"}) == ""


def test_pins_clip_previews_and_shared_chat_sessions(monkeypatch):
    from multichat.chat import twitch_events

    monkeypatch.setattr(twitch_events, "ROOMS", type(twitch_events.ROOMS)())
    ev = HermesEvents()
    pin = {"type": "pin-message", "data": {"pinned_by": {"display_name": "Mod"},
                                           "message": {"content": {"text": "read  the\nrules"}}}}
    assert ev.text("pin", pin) == "Mod pinned: read the rules"
    assert ev.text("pin", {"type": "update-message", "data": {"id": "x"}}) == ""
    assert ev.text("pin", {"type": "unpin-message", "data": {}}) == "Pinned message removed"
    clip = {"type": "chat_rich_embed", "data": {"title": "Charity LAN", "twitch_metadata": {
        "clip_metadata": {"channel_display_name": "Gnu_Live"}}}}
    assert ev.text("embed", clip) == "Clip: Charity LAN (Gnu_Live)"
    assert ev.text("embed", {"type": "updated_room", "data": {}}) == ""
    session = {"type": "session-started", "data": {"session": {"participants": [
        {"channel_id": "1", "channel_login": "a", "channel_display_name": "A", "status": "ACTIVE"},
        {"channel_id": "2", "channel_login": "b", "channel_display_name": "B", "status": "LEFT"}]}}}
    assert ev.text("shared_chat", session) == "Shared chat started: A"
    assert twitch_events.ROOMS == {"1": "A", "2": "B"}  # for "Shared chat: <channel>" on IRC lines
    assert ev.text("shared_chat", {**session, "type": "session-updated"}) == ""
    assert ev.text("shared_chat", {**session, "type": "session-ended"}) == "Shared chat ended"


def test_names_of_shared_chat_channels_are_looked_up_once():
    from multichat.chat import twitch_events

    twitch_events.ROOMS.clear()
    twitch_events.WANTED.clear()
    tags = {"source-room-id": "77", "room-id": "1"}
    assert twitch_events.shared_from(tags) == ""  # not known yet → asked for
    assert twitch_events.shared_from({"source-room-id": "x;1", "room-id": "1"}) == ""  # not an ID: not asked
    asked = []

    async def lookup_name(room_id):
        asked.append(room_id)
        if room_id == "88":
            raise ValueError("unknown channel")
        return "Partner"

    client = th.HermesClient(dict, lambda ev, key: None, lambda *_: False, lambda: None, lookup_name=lookup_name)
    twitch_events.shared_from({"source-room-id": "88", "room-id": "1"})
    asyncio.run(client._name_rooms())
    assert sorted(asked) == ["77", "88"] and twitch_events.shared_from(tags) == "Partner"
    twitch_events.shared_from({"source-room-id": "88", "room-id": "1"})
    asyncio.run(client._name_rooms())
    assert sorted(asked) == ["77", "88"]  # a failed one isn't asked again
    twitch_events.ROOMS.clear()
    twitch_events.WANTED.clear()
