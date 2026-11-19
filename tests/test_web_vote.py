# SPDX-License-Identifier: GPL-3.0-or-later
"""Voting in other channels with the website login (accounts/web.py, ui/vote.py). The request
shapes are the ones recorded from the websites in Nov 2026."""
import asyncio
import json

import pytest

from multichat.accounts import web as webmod
from multichat.accounts.oauth import ApiError
from multichat.chat import kick, youtube_innertube as yti
from multichat.chat.kick_events import kick_vote
from multichat.chat.twitch_hermes_events import twitch_vote


class Store:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, p):
        return dict(self.data.get(p) or {})

    def replace(self, p, v):
        self.data[p] = dict(v)

    def delete(self, p):
        self.data.pop(p, None)


def test_vote_info_on_the_lines_that_start_a_poll_or_prediction():
    assert kick_vote("App\\Events\\PollUpdateEvent", {"poll": {"title": "ja oder nein", "options": [
        {"id": 0, "label": "ja", "votes": 0}, {"id": 1, "label": "ne", "votes": 0}]}}) == {
        "kind": "poll", "id": "", "options": [["0", "ja"], ["1", "ne"]]}
    pred = {"prediction": {"id": "01M7", "title": "Boss?", "state": "ACTIVE",
                           "outcomes": [{"id": "o1", "title": "Klar!"}, {"id": "o2", "title": "Nee"}]}}
    assert kick_vote("PredictionCreated", pred)["options"] == [["o1", "Klar!"], ["o2", "Nee"]]
    assert kick_vote("PredictionUpdated", {"prediction": {**pred["prediction"], "state": "RESOLVED"}}) is None
    assert kick_vote("PollUpdateEvent", {"poll": "broken"}) is None
    got = []
    kick._handle_event("App\\Events\\PollUpdateEvent", {"poll": {"title": "Q-vote?", "options": [
        {"id": 0, "label": "a"}, {"id": 1, "label": "b"}]}}, "corrgan", got.append, None)
    assert got[0].vote["options"] == [["0", "a"], ["1", "b"]]

    tw_poll = {"type": "POLL_CREATE", "data": {"poll": {"poll_id": "p1", "title": "T", "status": "ACTIVE", "choices": [
        {"choice_id": "c1", "title": "GTA"}, {"choice_id": "c2", "title": "WoW"}]}}}
    assert twitch_vote("poll", tw_poll) == {"kind": "poll", "id": "p1", "options": [["c1", "GTA"], ["c2", "WoW"]]}
    assert twitch_vote("poll", {**tw_poll, "type": "POLL_UPDATE"}) is None
    tw_pred = {"type": "event-created", "data": {"event": {"id": "e1", "status": "ACTIVE", "outcomes": [
        {"id": "b", "title": "Ja"}, {"id": "p", "title": "Nein"}]}}}
    assert twitch_vote("prediction", tw_pred)["id"] == "e1"
    assert twitch_vote("prediction", "x") is None

    ev = yti.parse_actions([{"showLiveChatActionPanelAction": {"panelToShow": {"liveChatActionPanelRenderer": {
        "contents": {"pollRenderer": {"liveChatPollId": "yp-vote", "choices": [
            {"text": {"runs": [{"text": "A"}]}}, {"text": {"runs": [{"text": "B"}]}}],
            "header": {"pollHeaderRenderer": {"pollQuestion": {"runs": [{"text": "Q?"}]}}}}}}}}}], "vid")
    assert ev[0].vote == {"kind": "poll", "id": "yp-vote", "options": [["0", "A"], ["1", "B"]]}


def test_take_over_keeps_only_what_a_vote_needs(monkeypatch):
    class C:
        def __init__(self, domain, name, value, expires=None):
            self.domain, self.name, self.value, self.expires = domain, name, value, expires

    jars = {"firefox": [C(".twitch.tv", "auth-token", "TOK"), C(".twitch.tv", "login", "corrganx"),
                        C(".twitch.tv", "tracking", "no"), C(".kick.com", "session_token", "old", expires=1),
                        C("www.example.com", "auth-token", "no")]}
    import yt_dlp.cookies
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", lambda b: jars.get(b, []))
    w = webmod.WebLogins(Store(), lambda: None)
    assert w.take_over("twitch", "firefox") == "corrganx"
    assert w.cookies("twitch") == {"auth-token": "TOK", "login": "corrganx"} and w.has("twitch")
    with pytest.raises(webmod.NotLoggedIn):
        w.take_over("kick", "firefox")  # only an expired session
    w.forget("twitch")
    assert not w.has("twitch")


def test_kick_vote_requests(monkeypatch):
    sent = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"status": {"code": 200, "message": "Vote submitted successfully", "error": False}}

    import curl_cffi.requests as cf
    monkeypatch.setattr(cf, "post", lambda url, **kw: sent.append((url, kw)) or Resp())
    w = webmod.WebLogins(Store({"kick": {"cookies": {"session_token": "123%7Cabc", "XSRF-TOKEN": "x%3D"}}}), lambda: None)
    asyncio.run(w.kick_poll("corrgan", 1))
    asyncio.run(w.kick_prediction("corrgan", "o2", 250))
    (url, kw), (url2, kw2) = sent
    assert url == "https://kick.com/api/v2/channels/corrgan/polls/vote" and kw["json"] == {"id": 1}
    assert kw["headers"]["Authorization"] == "Bearer 123|abc" and kw["headers"]["X-XSRF-TOKEN"] == "x="
    assert url2.endswith("/predictions/vote") and kw2["json"] == {"outcome_id": "o2", "amount": 250}
    Resp.json = lambda self: {"status": {"code": 403, "message": "Poll already voted", "error": True}}
    with pytest.raises(ApiError, match="already voted"):
        asyncio.run(w.kick_poll("corrgan", 0))
    with pytest.raises(webmod.NotLoggedIn):
        asyncio.run(webmod.WebLogins(Store(), lambda: None).kick_poll("corrgan", 0))


class FakeSession:
    """aiohttp-like: records requests, answers from a list."""

    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def _r(self, method, url, **kw):
        self.sent.append((method, url, kw))
        status, body = self.answers.pop(0)

        class R:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False

            async def text(s):
                return body if isinstance(body, str) else json.dumps(body)

            async def json(s, content_type=None):
                return body

        R.status = status
        return R()

    def post(self, url, **kw):
        return self._r("POST", url, **kw)

    def get(self, url, **kw):
        return self._r("GET", url, **kw)


def test_twitch_vote_requests():
    s = FakeSession([(200, {"data": {"currentUser": {"id": "483016172", "login": "corrganx"}}}),
                     (200, [{"data": {"voteInPoll": {"error": None}}}]),
                     (200, {"data": {"makePrediction": {"error": None}}}),
                     (200, [{"errors": [{"message": "failed integrity check"}]}]),
                     (200, {"data": {"makePrediction": {"error": {"code": "NOT_ENOUGH_POINTS"}}}})])
    w = webmod.WebLogins(Store({"twitch": {"cookies": {"auth-token": "TOK", "unique_id": "dev1"}}}), lambda: s)
    asyncio.run(w.twitch_poll("p1", "c2"))
    _m, url, kw = s.sent[1]
    body = json.loads(kw["data"])[0]
    assert url == webmod.TWITCH_GQL and kw["headers"]["Authorization"] == "OAuth TOK"
    assert kw["headers"]["Client-Id"] == webmod.TWITCH_WEB_CLIENT and kw["headers"]["X-Device-Id"] == "dev1"
    assert body["operationName"] == "ChannelPollContext_VoteInPoll"
    assert body["extensions"]["persistedQuery"]["sha256Hash"] == webmod.VOTE_IN_POLL
    inp = body["variables"]["input"]
    assert (inp["pollID"], inp["choiceID"], inp["userID"], inp["tokens"]) == ("p1", "c2", "483016172", None)
    assert len(inp["voteID"]) == 32
    asyncio.run(w.twitch_prediction("e1", "o1", 100))
    inp = json.loads(s.sent[2][2]["data"])["variables"]["input"]
    assert (inp["eventID"], inp["outcomeID"], inp["points"]) == ("e1", "o1", 100) and inp["transactionID"]
    with pytest.raises(ApiError, match="integrity"):
        asyncio.run(w.twitch_poll("p1", "c1"))
    with pytest.raises(ApiError, match="NOT_ENOUGH_POINTS"):
        asyncio.run(w.twitch_prediction("e1", "o1", 100))


def test_youtube_vote_reads_the_chat_logged_in_and_sends_the_params(monkeypatch):
    poll = {"liveChatPollId": "yp1", "choices": [
        {"text": {"runs": [{"text": "A"}]}, "selectServiceEndpoint": {"sendLiveChatVoteEndpoint": {"params": "PA"}}},
        {"text": {"runs": [{"text": "B"}]}, "selectServiceEndpoint": {"sendLiveChatVoteEndpoint": {"params": "PB"}}}]}
    monkeypatch.setattr(yti, "parse_page", lambda page: ({"client": {"clientName": "WEB"}}, "TOKEN", []))
    monkeypatch.setattr(yti, "_find_json", lambda page, marker: {"contents": {"x": []}})  # poll not on the page
    s = FakeSession([(200, "<html>"), (200, {"continuationContents": {"liveChatContinuation": {"actions": [
        {"showLiveChatActionPanelAction": {"panelToShow": {"liveChatActionPanelRenderer": {
            "contents": {"pollRenderer": poll}}}}}]}}}), (200, {"actions": []})])
    w = webmod.WebLogins(Store({"youtube": {"cookies": {"SAPISID": "sap", "SID": "sid"}}}), lambda: s)
    asyncio.run(w.youtube_poll("vid1", "yp1", 1))
    get, cont, vote = s.sent
    assert get[2]["params"] == {"is_popout": "1", "v": "vid1"} and get[2]["headers"]["Authorization"].startswith("SAPISIDHASH ")
    assert "SID=sid" in get[2]["headers"]["Cookie"] and cont[2]["json"]["continuation"] == "TOKEN"
    assert vote[1].endswith("/youtubei/v1/live_chat/send_live_chat_vote") and vote[2]["json"]["params"] == "PB"
    assert webmod.vote_params({"a": [{"pollRenderer": {**poll, "liveChatPollId": "other"}}]}, "yp1") == []

