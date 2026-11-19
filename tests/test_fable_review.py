# SPDX-License-Identifier: GPL-3.0-or-later
"""Findings of the code review of 19.11.2026 (F1–F7), each with the case that went wrong."""
import asyncio
import time

from multichat.chat.models import ChatMessage, Deletion, PollState


def test_f1_replies_and_first_messages_are_votes_too():
    from multichat.chat.poll import ChatPoll

    p = ChatPoll("Q?", ["a", "b"], {"twitch:chan", "kick:chan"})

    def msg(platform, uid, text, **kw):
        m = ChatMessage(platform, "chan", f"u{uid}", text, user_id=str(uid), **kw)
        m.source = f"{platform}:chan"
        return m

    assert p.vote(msg("twitch", 1, "!1", event="notice", event_text="Replying to Streamer"))
    assert p.vote(msg("twitch", 2, "!2", event="notice", event_text="First message"))
    assert p.vote(msg("kick", 3, "!2", event="notice", event_text="Replying to Streamer"))
    assert not p.vote(msg("twitch", 4, "!1", event="bits", event_value=100))  # an alert is no vote
    system = ChatMessage("twitch", "chan", "Twitch", "!1", event="notice", event_text="Announcement")
    system.source = "twitch:chan"
    assert not p.vote(system)  # the platform's own line (no user ID)
    assert p.counts == [1, 2]


def test_f2_a_login_without_the_moderated_channels_scope_is_not_asked_every_5_s():
    from multichat.accounts import twitch as tw
    from multichat.accounts.oauth import ApiError

    class Store:
        def get(self, platform):
            return {"user_id": "77", "user_name": "Me", "access_token": "x"}

    t = tw.TwitchAccount.__new__(tw.TwitchAccount)
    t.store, t._cache, calls = Store(), {}, []

    async def request(method, url, **kw):
        calls.append(url)
        raise ApiError("Twitch (401): Missing scope: user:read:moderated_channels", status=401)

    t.request = request
    for _ in range(5):
        assert asyncio.run(t.is_moderator("otherchannel")) is None
    assert len(calls) == 1  # once, then remembered for MODERATED_RETRY
    t._cache[("moderated-denied", "77")] -= tw.MODERATED_RETRY + 1
    asyncio.run(t.is_moderator("otherchannel"))
    assert len(calls) == 2  # asked again later (e.g. after logging in again with the scope)


def test_f3_only_the_platforms_own_domain_is_taken_over(monkeypatch):
    import yt_dlp.cookies

    from multichat.accounts import web

    class C:
        def __init__(self, domain, name):
            self.domain, self.name, self.value, self.expires = domain, name, "v", None

    jar = [C("kick.com", "session_token"), C(".kick.com", "XSRF-TOKEN"), C("web.kick.com", "a"),
           C("sidekick.com", "secret"), C(".notkick.com", "secret2"), C("youtube.com.evil", "SAPISID")]
    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", lambda b, *a: jar)
    assert sorted(web.read_browser_cookies("firefox", "kick")) == ["XSRF-TOKEN", "a", "session_token"]
    assert web.read_browser_cookies("firefox", "youtube") == {}


def test_f4_odd_ids_from_a_platform_become_none():
    from multichat.chat.youtube_notices import notice

    n = notice({"liveChatSponsorshipsGiftRedemptionAnnouncementRenderer": {
        "id": 5, "authorName": {"simpleText": "@x"}, "authorExternalChannelId": {"a": 1},
        "message": {"runs": [{"text": "got a membership"}]}}}, "vid")
    assert n.user_id is None and n.msg_id is None
    {(n.platform, n.user_id): 1}  # hashable: user history and notes key on it
    m = ChatMessage("twitch", "c", "u", "hi", user_id=123, msg_id=["x"])
    assert (m.user_id, m.msg_id) == (None, None)


def test_f5_infinite_counts_do_not_break_the_box():
    from multichat.chat.poll_state import youtube_state

    s = PollState("kick", "c", "poll", "q", "Q?", [["0", "a", float("inf")], ["1", "b", float("nan")], ["2", "c", 3]])
    assert [o[2] for o in s.options] == [0.0, 0.0, 3.0] and s.share(2) == 100
    y = youtube_state({"updateLiveChatPollAction": {"pollToUpdate": {"pollRenderer": {
        "liveChatPollId": "p", "choices": [{"text": {"runs": [{"text": "A"}]}, "voteRatio": 1e400}]}}}}, "vid")
    assert y.options[0][2] == 0.0


def test_f6_dropping_poll_updates_is_not_lost_moderation():
    from multichat.ui.incoming import IncomingEvents

    q = IncomingEvents(100, 100, 4)  # per source, total, sources
    q.per_source = 3
    for i in range(5):  # a burst of poll updates for one channel overflows its queue
        s = PollState("kick", "c", "poll", "q", "Q?", [["0", "a", i], ["1", "b", 0]])
        s.source = "kick:c"
        q.put(s)
    assert q._controls == 0 and q.dropped_deletions == 0 and q.dropped == 2
    d = Deletion("kick", msg_id="m1")
    d.source = "kick:c"
    assert q.put(d) == 2  # a real deletion is still delivered promptly


def test_f7_a_card_whose_end_was_missed_goes_after_a_while():
    from multichat.ui.poll_box import STALE_S, PollBox

    box = PollBox(lambda card, i: None)
    s = PollState("youtube", "vid", "poll", "yp", "Q?", [["0", "A", 40], ["1", "B", 60]], percent=True)
    s.source = "youtube:vid"
    box.update_state(s, False, "")
    card = box.cards[("youtube:vid", "poll", "yp")]
    box.tick()
    assert box.cards  # still running
    card.updated_at = time.time() - STALE_S - 1  # no close came (the chat reconnected meanwhile)
    box.tick()
    assert not box.cards
