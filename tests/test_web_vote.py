# SPDX-License-Identifier: GPL-3.0-or-later
"""Voting in other channels with the website login (accounts/web.py, ui/vote.py). The request
shapes are the ones recorded from the websites in Nov 2026."""
import asyncio
import json

import pytest

from multichat.accounts import web as webmod
from multichat.accounts.oauth import ApiError
from multichat.chat import kick, youtube_innertube as yti


class Store:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, p):
        return dict(self.data.get(p) or {})

    def replace(self, p, v):
        self.data[p] = dict(v)

    def delete(self, p):
        self.data.pop(p, None)


def test_poll_state_from_every_platform():
    """The box above the chat follows the state each platform sends (shapes from Nov 2026 captures)."""
    import time as _t

    from multichat.chat.poll_state import kick_state, twitch_eventsub_state, twitch_hermes_state, youtube_state

    s = kick_state("App\\Events\\PollUpdateEvent", {"poll": {"title": "ja oder nein", "options": [
        {"id": 0, "label": "ja", "votes": 1}, {"id": 1, "label": "ne", "votes": 3}], "duration": 60, "remaining": 58,
        "result_display_duration": 60}}, "corrgan")
    assert (s.kind, s.id, s.status, s.options) == ("poll", "ja oder nein", "active", [["0", "ja", 1.0], ["1", "ne", 3.0]])
    assert s.share(1) == 75 and 50 < s.ends_at - _t.time() <= 58
    assert kick_state("PollUpdateEvent", {"poll": {"title": "q", "options": [], "remaining": 0}}, "c").status == "ended"
    pred = {"prediction": {"id": "01M7", "title": "Sitzt Top 10 drin?", "state": "RESOLVED", "duration": 60,
                           "created_at": "2026-11-18T09:58:04Z", "winning_outcome_id": "o1", "outcomes": [
                               {"id": "o1", "title": "Klar!", "total_vote_amount": 37966},
                               {"id": "o2", "title": "Neeeeee!", "total_vote_amount": 23095}]}}
    s = kick_state("PredictionUpdated", pred, "c")
    assert (s.kind, s.status, s.winner, s.share(0)) == ("prediction", "ended", "o1", 62)

    poll = {"type": "POLL_UPDATE", "data": {"poll": {"poll_id": "p1", "title": "OPINIÓN", "status": "ACTIVE",
            "started_at": "2026-11-17T21:32:39.009641497Z", "duration_seconds": 300, "choices": [
                {"choice_id": "c1", "title": "GTA", "votes": {"total": 5}}, {"choice_id": "c2", "title": "WoW",
                                                                             "votes": {"total": 15}}]}}}
    s = twitch_hermes_state("poll", poll, "peereira7")
    assert (s.id, s.options[1], s.share(1)) == ("p1", ["c2", "WoW", 15.0], 75)
    import datetime as _dt
    start = _dt.datetime(2026, 11, 17, 21, 32, 39, tzinfo=_dt.timezone.utc).timestamp()
    assert s.ends_at == start + 300
    ev = {"type": "event-updated", "data": {"event": {"id": "e1", "title": "Erster?", "status": "LOCKED",
          "created_at": "2026-11-17T21:40:06Z", "prediction_window_seconds": 300, "outcomes": [
              {"id": "b", "title": "Ja", "total_points": 100}, {"id": "p", "title": "Nein", "total_points": 300}]}}}
    assert twitch_hermes_state("prediction", ev, "giggand").status == "locked"
    archived = {"data": {"poll": {**poll["data"]["poll"], "status": "ARCHIVED"}}}
    assert twitch_hermes_state("poll", archived, "x").status == "gone"
    s = twitch_eventsub_state("channel.poll.progress", {"id": "p9", "title": "Q", "ends_at": "2026-11-19T12:05:00Z",
                              "choices": [{"id": "a", "title": "A", "votes": 2}, {"id": "b", "title": "B", "votes": 0}]}, "corrganx")
    assert (s.status, s.options[0][2]) == ("active", 2.0)
    s = twitch_eventsub_state("channel.prediction.end", {"id": "e", "title": "Q", "status": "resolved",
                              "winning_outcome_id": "x", "outcomes": [{"id": "x", "title": "X", "channel_points": 5}]}, "c")
    assert (s.status, s.winner) == ("ended", "x")
    assert twitch_eventsub_state("channel.poll.begin", "broken", "c") is None

    show = {"showLiveChatActionPanelAction": {"panelToShow": {"liveChatActionPanelRenderer": {"contents": {
        "pollRenderer": {"liveChatPollId": "yp1", "choices": [{"text": {"runs": [{"text": "A"}]}, "voteRatio": 0.24},
                                                             {"text": {"runs": [{"text": "B"}]}, "voteRatio": 0.76}],
                         "header": {"pollHeaderRenderer": {"pollQuestion": {"runs": [{"text": "Q?"}]}}}}}}}}}
    s = youtube_state(show, "vid")
    assert (s.id, s.title, s.percent, s.share(1)) == ("yp1", "Q?", True, 76)
    assert youtube_state({"closeLiveChatActionPanelAction": {"targetPanelId": "yp1"}}, "vid").status == "ended"
    ev = yti.parse_actions([show], "vid")
    assert [type(e).__name__ for e in ev] == ["PollState", "ChatMessage"]  # the box and the line
    got = []
    kick._handle_event("App\\Events\\PollUpdateEvent", {"poll": {"title": "box?", "options": [
        {"id": 0, "label": "a", "votes": 0}, {"id": 1, "label": "b", "votes": 0}], "remaining": 30}}, "corrgan",
        got.append, None)
    assert type(got[0]).__name__ == "PollState" and got[0].channel == "corrgan"


def test_take_over_keeps_only_what_a_vote_needs(monkeypatch):
    class C:
        def __init__(self, domain, name, value, expires=None):
            self.domain, self.name, self.value, self.expires = domain, name, value, expires

    jars = {"firefox": [C(".twitch.tv", "auth-token", "TOK"), C(".twitch.tv", "login", "corrganx"),
                        C(".twitch.tv", "tracking", "no"), C(".kick.com", "session_token", "old", expires=1),
                        C("www.example.com", "auth-token", "no")]}
    import yt_dlp.cookies
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", lambda b, *a: jars.get(b, []))
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
                     (200, {"data": {"makePrediction": {"error": {"code": "NOT_ENOUGH_POINTS"}}}}),
                     (200, {"data": {"makePrediction": {"error": {"code": "EVENT_MANAGER"}}}})])
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
    with pytest.raises(ApiError, match="not enough channel points"):
        asyncio.run(w.twitch_prediction("e1", "o1", 100))
    with pytest.raises(ApiError, match="own channel"):  # seen in the first live test (no integrity check)
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



def test_the_vote_setting_survives_a_restart(tmp_path, monkeypatch):
    """First test with the AppImage: after a restart the buttons were grey – load_config only keeps
    keys it knows, and web_vote wasn't one of them."""
    from multichat.core import config

    monkeypatch.setattr(config, "CONFIG", tmp_path / "config.json")
    cfg = config.load_config()
    assert cfg["web_vote"] is False  # off by default
    cfg["web_vote"] = True
    config.save_config(cfg)
    assert config.load_config()["web_vote"] is True
    (tmp_path / "config.json").write_text('{"web_vote": "yes"}')
    assert config.load_config()["web_vote"] is False  # wrong type: the default


def test_a_missing_browser_profile_says_what_to_do(monkeypatch, tmp_path):
    """In the Flatpak the sandbox hides the browser profiles: yt-dlp only says "could not find … database"."""
    import yt_dlp.cookies

    def missing(browser, *a, **k):
        raise FileNotFoundError(f"could not find {browser} cookies database in '/home/x/.mozilla/firefox'")

    monkeypatch.setenv("HOME", str(tmp_path))  # no browser folders here
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", missing)
    monkeypatch.setenv("FLATPAK_ID", "io.github.Corrgan.MultiChat")
    with pytest.raises(webmod.NotLoggedIn) as e:
        webmod.read_browser_cookies("firefox", "twitch")
    assert ("flatpak override --user --filesystem=~/.config/mozilla/firefox:ro --filesystem=~/.mozilla/firefox:ro "
            "--filesystem=~/.var/app/org.mozilla.firefox:ro io.github.Corrgan.MultiChat") in str(e.value)
    with pytest.raises(webmod.NotLoggedIn) as e:
        webmod.read_browser_cookies("chrome", "twitch")
    assert ("flatpak override --user --filesystem=~/.config/google-chrome:ro "
            "--filesystem=~/.var/app/com.google.Chrome:ro io.github.Corrgan.MultiChat") in str(e.value)
    monkeypatch.delenv("FLATPAK_ID")
    with pytest.raises(webmod.NotLoggedIn) as e:
        webmod.read_browser_cookies("firefox", "twitch")
    assert "Firefox" in str(e.value) and "flatpak" not in str(e.value).lower()


def test_the_browser_profile_written_last_is_used(monkeypatch, tmp_path):
    """MultiChat finds the profiles itself: in the Flatpak yt-dlp looks under the sandbox's ~/.config only."""
    import os

    import yt_dlp.cookies

    def db(rel, age):
        f = tmp_path / rel
        f.parent.mkdir(parents=True)
        f.write_bytes(b"")
        os.utime(f, (1_700_000_000 - age, 1_700_000_000 - age))

    db(".mozilla/firefox/old.default-release/cookies.sqlite", 1000)
    db(".config/mozilla/firefox/new.default-release/cookies.sqlite", 10)
    db(".var/app/com.google.Chrome/config/google-chrome/Default/Network/Cookies", 5)
    db(".config/google-chrome/Default/Cache/x/y/Cookies", 0)  # deeper than a profile: not looked at
    monkeypatch.setenv("HOME", str(tmp_path))
    assert webmod.find_profile("firefox") == str(tmp_path / ".config/mozilla/firefox")
    assert webmod.find_profile("chrome") == str(tmp_path / ".var/app/com.google.Chrome/config/google-chrome")
    assert webmod.find_profile("brave") is None
    asked = []
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", lambda b, *a: asked.append((b, *a)) or [])
    webmod.read_browser_cookies("chrome", "twitch")
    webmod.read_browser_cookies("brave", "twitch")
    assert asked == [("chrome", str(tmp_path / ".var/app/com.google.Chrome/config/google-chrome")), ("brave",)]
