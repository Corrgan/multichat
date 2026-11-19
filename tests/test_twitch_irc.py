# SPDX-License-Identifier: GPL-3.0-or-later
"""Nothing Twitch puts into the chat is left out: bots, announcements, notices, channel
points, bits."""

def _read(line, monkeypatch):
    """One IRC line through the real reader → what it emits."""
    import asyncio

    from multichat.chat import twitch

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def send(self, _data):
            pass

        def __aiter__(self):
            async def lines():
                yield line
                raise asyncio.CancelledError

            return lines()

    monkeypatch.setattr(twitch.websockets, "connect", lambda *_a, **_k: Socket())
    got = []

    async def run():
        try:
            await twitch.run_twitch("examplechan", got.append, lambda *_a, **_k: None)
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    return got


def test_bot_messages_announcements_and_notices_are_shown(monkeypatch):
    from multichat.chat import alerts

    bot = _read("@badges=moderator/1;display-name=ExampleBot;id=a1;room-id=1;user-id=9 "
                ":examplebot!examplebot@examplebot.tmi.twitch.tv PRIVMSG #examplechan :Follow the channel!", monkeypatch)
    assert [(m.user, m.text, m.event) for m in bot] == [("ExampleBot", "Follow the channel!", "")]

    # /announce – what bots use for their timers – used to be dropped
    announce = _read("@badges=moderator/1;display-name=ExampleBot;id=a2;login=examplebot;msg-id=announcement;"
                     "msg-param-color=PRIMARY;room-id=1;user-id=9 :tmi.twitch.tv USERNOTICE #examplechan "
                     ":Stream starts soon", monkeypatch)
    assert [(m.user, m.text, m.event, alerts.headline(m)) for m in announce] == [
        ("ExampleBot", "Stream starts soon", "notice", "Announcement")]

    streak = _read("@display-name=Viewer;id=a3;login=viewer;msg-id=viewermilestone;room-id=1;user-id=6;"
                   r"system-msg=Viewer\swatched\s5\sconsecutive\sstreams\sand\ssparked\sa\swatch\sstreak! "
                   ":tmi.twitch.tv USERNOTICE #examplechan :still here", monkeypatch)
    assert alerts.headline(streak[0]) == "Viewer watched 5 consecutive streams and sparked a watch streak!"
    assert streak[0].text == "still here"

    badge = _read("@display-name=Cheerer;id=a4;login=cheerer;msg-id=bitsbadgetier;room-id=1;user-id=5;"
                  r"system-msg=bits\sbadge\stier\snotification :tmi.twitch.tv USERNOTICE #examplechan", monkeypatch)
    assert [(m.user, m.parts, alerts.headline(m)) for m in badge] == [("Cheerer", [], "bits badge tier notification")]

    room = _read("@msg-id=slow_on :tmi.twitch.tv NOTICE #examplechan :This room is now in slow mode.", monkeypatch)
    assert [(m.user, m.event, alerts.headline(m)) for m in room] == [
        ("Twitch", "notice", "This room is now in slow mode.")]

    # a community gift: the total, then who got one (not highlighted again, not counted twice)
    total = _read("@display-name=Gifter;id=a5;login=gifter;msg-id=submysterygift;msg-param-mass-gift-count=5;"
                  "room-id=1;user-id=3 :tmi.twitch.tv USERNOTICE #examplechan", monkeypatch)
    single = _read("@display-name=Gifter;id=a6;login=gifter;msg-id=subgift;msg-param-community-gift-id=77;"
                   "msg-param-recipient-display-name=Bob;room-id=1;user-id=3 :tmi.twitch.tv USERNOTICE #examplechan",
                   monkeypatch)
    assert alerts.headline(total[0]) == "Gifter gifted 5 subs" and alerts.is_alert(total[0], alerts.DEFAULT_ALERTS)
    assert alerts.headline(single[0]) == "Gifter gifted a sub to Bob"
    assert single[0].event_value == 0 and not alerts.is_alert(single[0], alerts.DEFAULT_ALERTS)


def test_timeouts_bans_and_cleared_chat_get_a_line(monkeypatch):
    from multichat.chat import alerts
    from multichat.chat.models import Deletion
    from multichat.chat.twitch_events import duration_text

    def lines(tags, target=" :troublemaker"):
        got = _read(f"@{tags}room-id=1 :tmi.twitch.tv CLEARCHAT #examplechan{target}", monkeypatch)
        assert isinstance(got[0], Deletion)  # the messages are still marked as deleted
        return [alerts.headline(m) for m in got[1:]]

    assert lines("ban-duration=600;target-user-id=8;") == ["troublemaker was timed out for 10 min"]
    assert lines("target-user-id=8;") == ["troublemaker was banned"]
    assert lines("", target="") == ["The chat was cleared"]
    assert [duration_text(s) for s in (45, 60, 90, 3600, 172800)] == ["45 s", "1 min", "90 s", "1 h", "2 d"]


def test_cheermotes_become_animated_pictures(monkeypatch):
    from multichat.chat.cheermotes import cheer_parts, cheermote

    assert cheermote("Cheer100") == ("https://d3aqoihi2n8ty8.cloudfront.net/actions/cheer/dark/animated/100/2.gif", 100)
    assert cheermote("cheer99")[0].endswith("/animated/1/2.gif") and cheermote("Kappa12000")[0].endswith("/kappa/dark/animated/10000/2.gif")
    for word in ("Cheer", "100", "Hello100", "Cheer0", "Cheer100!", "xCheer100"):
        assert cheermote(word) is None, word
    assert cheer_parts([("text", "Cheer100 nice  stream uni5000", ""), ("emote", "https://e/1", "Kappa")]) == [
        ("emote", "https://d3aqoihi2n8ty8.cloudfront.net/actions/cheer/dark/animated/100/2.gif", "Cheer"),
        ("text", "100 nice  stream ", ""),
        ("emote", "https://d3aqoihi2n8ty8.cloudfront.net/actions/uni/dark/animated/5000/2.gif", "uni"),
        ("text", "5000", ""),
        ("emote", "https://e/1", "Kappa"),
    ]
    # only in messages that carry bits: "Cheer100" typed without bits stays text
    with_bits = _read("@bits=100;display-name=Cheerer;id=c1;room-id=1;user-id=5 "
                      ":cheerer!cheerer@cheerer.tmi.twitch.tv PRIVMSG #examplechan :Cheer100 gg", monkeypatch)
    without = _read("@display-name=Cheerer;id=c2;room-id=1;user-id=5 "
                    ":cheerer!cheerer@cheerer.tmi.twitch.tv PRIVMSG #examplechan :Cheer100 gg", monkeypatch)
    assert [p[0] for p in with_bits[0].parts] == ["emote", "text"] and with_bits[0].text == "Cheer100 gg"
    assert [p[0] for p in without[0].parts] == ["text"]


def test_channel_points_bits_and_first_messages_are_marked(monkeypatch):
    from multichat.chat import alerts

    def one(tags, text="look at me"):
        got = _read(f"@{tags};display-name=Viewer;id=b1;room-id=1;user-id=6 "
                    f":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #examplechan :{text}", monkeypatch)
        assert len(got) == 1 and got[0].text == text
        return got[0].event, alerts.headline(got[0])

    assert one("msg-id=highlighted-message") == ("notice", "Highlighted message (channel points)")
    assert one("custom-reward-id=abc-123") == ("notice", "Channel points reward")
    assert one("msg-id=gigantified-emote-message") == ("notice", "Power-up")
    assert one("first-msg=1") == ("notice", "First message")
    assert one("first-msg=0") == ("", "")
    assert one("bits=100;first-msg=1", "Cheer100 nice") == ("bits", "Viewer cheered 100 bits")  # bits come first


def test_notices_render_as_a_plain_line_and_are_no_alert(_session_qapplication):
    from multichat.chat import alerts
    from multichat.chat.models import ChatMessage
    from multichat.ui.chat_view import ChatView

    view = ChatView()
    room = ChatMessage("twitch", "examplechan", "Twitch", "", event="notice", event_text="This room is now in slow mode.")
    said = ChatMessage("twitch", "examplechan", "ExampleBot", "Stream starts soon", event="notice",
                       event_text="Announcement", user_id="9", parts=[("text", "Stream starts soon", "")])
    assert not alerts.is_alert(room, alerts.DEFAULT_ALERTS) and not alerts.is_alert(said, alerts.DEFAULT_ALERTS)
    view.append_many([(1, view.message_html(room), None), (2, view.message_html(said), None)])
    text = view.toPlainText()
    assert "This room is now in slow mode." in text
    assert "Announcement" in text and "ExampleBot: Stream starts soon" in text
    view.close()


# ---- the other platforms ------------------------------------------------------ #
def test_kick_rewards_pins_polls_and_replies_are_shown():
    from multichat.chat import alerts, kick

    got = []

    def feed(name, data):  # the chat lines (the box above the chat gets PollStates, see test_web_vote)
        kick._handle_event(name, data, "examplekick", lambda ev: got.append(ev) if hasattr(ev, "event_text") else None,
                           None)

    feed("RewardRedeemedEvent", {"username": "Viewer", "reward_title": "Hydrate", "user_input": "drink!"})
    feed("App\\Events\\PinnedMessageCreatedEvent", {"message": {"content": "Rules: be nice", "sender": {"username": "Mod"}}})
    poll = {"poll": {"title": "Next game?", "options": [{"label": "A", "votes": 0}, {"label": "B", "votes": 0}]}}
    feed("App\\Events\\PollUpdateEvent", poll)
    feed("App\\Events\\PollUpdateEvent", poll)  # every vote sends an update: shown once
    feed("App\\Events\\ChatMessageEvent", {"id": "m1", "content": "yes!", "type": "reply", "sender": {"id": 4, "username": "Bob"},
                                         "metadata": {"original_sender": {"username": "Alice"}}})
    feed("App\\Events\\ChatMessageEvent", {"id": "m2", "content": "hello", "type": "message", "sender": {"id": 4, "username": "Bob"}})
    assert [(m.user, alerts.headline(m), m.text) for m in got] == [
        ("Viewer", "Viewer redeemed Hydrate", "drink!"),
        ("Mod", "Pinned message", "Rules: be nice"),
        ("Kick", "Poll: Next game?", "A · B"),
        ("Bob", "Replying to Alice", "yes!"),
        ("Bob", "", "hello"),
    ]
    kick._handle_event("App\\Events\\UserBannedEvent", {"user": {"id": 5, "username": "troublemaker"}, "duration": 10},
                       "examplekick", got.append, None)
    kick._handle_event("App\\Events\\UserBannedEvent", {"user": {"id": 6, "username": "worse"}, "permanent": True},
                       "examplekick", got.append, None)
    assert [alerts.headline(m) for m in got[-3:] if not hasattr(m, "clear_all")] == [
        "troublemaker was timed out for 10 min", "worse was banned"]
    # these events are undocumented: one that looks different is not guessed at
    before = len(got)
    for name, data in (("RewardRedeemedEvent", {"username": "Viewer"}), ("PinnedMessageCreatedEvent", {"message": "x"}),
                       ("PollUpdateEvent", {"poll": None}), ("SomethingNewEvent", {"a": 1})):
        feed(name, data)
    assert len(got) == before


def test_youtube_shows_its_other_chat_lines_too():
    from multichat.chat import alerts
    from multichat.chat.youtube_innertube import parse_actions

    def item(kind, body):
        return {"addChatItemAction": {"item": {kind: body}}}

    actions = [
        item("liveChatModeChangeMessageRenderer", {"id": "n1", "text": {"runs": [{"text": "Slow mode is on"}]},
                                                    "subtext": {"runs": [{"text": "Send a message every 30 seconds"}]}}),
        item("liveChatViewerEngagementMessageRenderer", {"id": "n2", "message": {"runs": [{"text": "Welcome to live chat!"}]}}),
        item("liveChatSponsorshipsGiftRedemptionAnnouncementRenderer", {
            "id": "n3", "authorName": {"simpleText": "@viewer"}, "authorExternalChannelId": "UCviewer",
            "message": {"runs": [{"text": "received a gift membership by "}, {"text": "@gifter"}]}}),
        item("liveChatSomethingNewRenderer", {"id": "n4", "text": {"simpleText": "A new kind of line"}}),
        item("liveChatPlaceholderItemRenderer", {"id": "n5"}),  # a message still being checked: nothing to show
        item("liveChatTextMessageRenderer", {"id": "m1", "authorName": {"simpleText": "@chatter"},
                                             "message": {"runs": [{"text": "hello"}]}}),
    ]
    got = parse_actions(actions, "vid12345678")
    assert [(m.user, m.event, alerts.headline(m), m.text) for m in got] == [
        ("YouTube", "notice", "Slow mode is on", "Send a message every 30 seconds"),
        ("@viewer", "notice", "@viewer received a gift membership by @gifter", ""),
        ("YouTube", "notice", "A new kind of line", ""),
        ("@chatter", "", "", "hello"),
    ]


def test_shared_chat_lines_of_other_channels_are_marked_and_no_alert(monkeypatch):
    """Shared chat: what is written in another channel of the session arrives here too
    (source-room-id ≠ room-id) – marked with that channel, its bits and subs no alert of this one."""
    from multichat.chat import alerts, twitch_events

    monkeypatch.setattr(twitch_events, "ROOMS", type(twitch_events.ROOMS)())
    twitch_events.remember_room("2", "PartnerChan")
    shared = "source-room-id=2;source-id=s1;room-id=1"
    got = _read(f"@{shared};bits=100;display-name=Viewer;id=b1;user-id=6 "
                ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #examplechan :Cheer100 hi", monkeypatch)
    assert got[0].event == "notice" and alerts.headline(got[0]) == "Shared chat: PartnerChan"
    assert got[0].shared_id == "s1"
    got = _read("@source-room-id=9;source-id=s2;room-id=1;display-name=V;id=b2;user-id=6;first-msg=1 "
                ":v!v@v.tmi.twitch.tv PRIVMSG #examplechan :hi", monkeypatch)
    assert alerts.headline(got[0]) == "Shared chat · First message"  # channel not known (yet)
    own = _read("@source-room-id=1;source-id=b3;room-id=1;display-name=V;id=b3;user-id=6;bits=5 "
                ":v!v@v.tmi.twitch.tv PRIVMSG #examplechan :Cheer5", monkeypatch)
    assert own[0].event == "bits" and own[0].shared_id == "b3"  # this channel's own line of the session
    sub = _read(r"@msg-id=sharedchatnotice;source-msg-id=resub;source-room-id=2;source-id=s4;room-id=1;"
                r"display-name=Fan;login=fan;id=n1;user-id=7;msg-param-cumulative-months=5;"
                r"system-msg=Fan\ssubscribed\sfor\s5\smonths! :tmi.twitch.tv USERNOTICE #examplechan", monkeypatch)
    assert sub[0].event == "notice" and sub[0].event_value == 0
    assert alerts.headline(sub[0]) == "Shared chat: PartnerChan · Fan subscribed for 5 months!"


def test_replies_name_the_message_they_answer(monkeypatch):
    from multichat.chat import alerts

    got = _read(r"@reply-parent-display-name=Alice;reply-parent-user-login=alice;reply-parent-msg-body=what\sabout\s"
                + "x" * 120 + ";display-name=Bob;id=r1;room-id=1;user-id=8 "
                ":bob!bob@bob.tmi.twitch.tv PRIVMSG #examplechan :@Alice yes", monkeypatch)
    head = alerts.headline(got[0])
    assert head.startswith("Reply to Alice: what about xxx") and head.endswith("…") and len(head) < 110
    assert got[0].text == "@Alice yes"


def test_quoted_text_cannot_turn_round_the_message_below(monkeypatch):
    """A reply quotes someone else's message in the line above the reply: a direction override
    in it would also reverse the replier's name and text in the chat view."""
    from multichat.chat import alerts

    got = _read("@reply-parent-display-name=Alice;reply-parent-msg-body=hi‮evil;display-name=Bob;id=r2;"
                "room-id=1;user-id=8 :bob!bob@bob.tmi.twitch.tv PRIVMSG #examplechan :ok", monkeypatch)
    assert "‮" not in alerts.headline(got[0]) and alerts.headline(got[0]) == "Reply to Alice: hi evil"


def test_kick_reads_kicks_rewards_and_gifted_subs_topics(monkeypatch):
    """Kicks, redeemed rewards and gifted subs come on channel_<id> / chatroom_<id>, not on the
    chat topic – the handlers existed but nothing subscribed them. Payloads from a Nov 2026 capture."""
    import asyncio
    import json

    from multichat.chat import alerts, kick

    sent, got, statuses = [], [], []
    frames = [
        {"event": "pusher_internal:subscription_succeeded", "channel": "chatrooms.3852600.v2"},
        {"event": "pusher_internal:subscription_succeeded", "channel": "chatroom_3852600"},
        {"event": "pusher_internal:subscription_succeeded", "channel": "channel_3862536"},
        {"event": "RewardRedeemedEvent", "channel": "chatroom_3852600", "data": json.dumps(
            {"reward_title": "Hydrate", "user_id": 80807837, "channel_id": 3862536, "username": "wilito_qp",
             "user_input": "", "reward_background_color": "#FFFFFF"})},
        {"event": "KicksGifted", "channel": "channel_3862536", "data": json.dumps(
            {"gift_transaction_id": "ffbe6ae9", "message": "", "sender": {"id": 93325386, "username": "finnysfinn"},
             "gift": {"gift_id": "full_send", "name": "Full Send", "amount": 100, "type": "BASIC"}})},
        {"event": "GiftedSubscriptionsEvent", "channel": "chatroom_3852600", "data": json.dumps(
            {"chatroom_id": 3852600, "gifted_usernames": ["gokhl"], "gifter_username": "alkubaisy94",
             "gifted_total": 1, "gifter_total": 1, "chunk_details": None})},
        # the same sub also comes as v2 SubscriptionEvent (shown) – these must not show it twice
        {"event": "App\\Events\\ChannelSubscriptionEvent", "channel": "channel_3862536", "data": json.dumps(
            {"user_ids": [21352035], "username": "killerG2G", "channel_id": 3862536})},
        {"event": "App\\Events\\NewSubscriberUpdatedEvent", "channel": "channel_3862536", "data": json.dumps(
            {"channel_id": 3862536, "last_subscriber": "killerG2G", "count": 1134})},
    ]

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def send(self, data):
            sent.append(json.loads(data))

        def __aiter__(self):
            return self._frames()

        async def _frames(self):
            for f in frames:
                yield json.dumps(f)
            raise asyncio.CancelledError  # ends run_kick

    monkeypatch.setattr(kick, "_fetch_kick_channel", lambda slug: (3852600, "7", 3862536))
    monkeypatch.setattr(kick, "_fetch_kick_pusher_creds", lambda: ("key", "us2"))
    monkeypatch.setattr(kick.websockets, "connect", lambda url: Socket())
    try:  # time limit: on any error run_kick retries forever instead of failing
        asyncio.run(asyncio.wait_for(kick.run_kick("examplekick", lambda ev: hasattr(ev, "event_text") and got.append(ev),
                                                   lambda _p, template, **kw: statuses.append(template)), 5))
    except asyncio.CancelledError:
        pass
    assert [s["data"]["channel"] for s in sent] == ["chatrooms.3852600.v2", "chatroom_3852600", "channel_3862536",
                                                    "predictions-channel-3862536"]
    assert [(m.user, m.event, alerts.headline(m)) for m in got] == [
        ("wilito_qp", "notice", "wilito_qp redeemed Hydrate"),
        ("finnysfinn", "bits", alerts.headline(got[1])),
        ("alkubaisy94", "gift", alerts.headline(got[2])),
    ]
    assert got[1].event_value == 100 and got[2].event_value == 1
    assert statuses.count("connected ({channel})") == 1  # one per connection, not one per topic


def test_kick_topics_without_channel_id():
    from multichat.chat import kick

    assert kick.kick_topics(5, None) == ["chatrooms.5.v2", "chatroom_5"]
    assert kick.kick_topics(5, 9) == ["chatrooms.5.v2", "chatroom_5", "channel_9", "predictions-channel-9"]


def test_kick_predictions_and_goals():
    """Start and result of a prediction once each (every bet sends an update), a goal reached.
    Payloads from a Nov 2026 capture."""
    from multichat.chat import alerts, kick

    got = []

    def feed(name, data):  # the chat lines (the box above the chat gets PollStates, see test_web_vote)
        kick._handle_event(name, data, "examplekick", lambda ev: got.append(ev) if hasattr(ev, "event_text") else None,
                           None)

    outcomes = [{"id": "o1", "title": "Klar!", "total_vote_amount": 0, "vote_count": 0, "top_users": []},
                {"id": "o2", "title": "Neeeeee!", "total_vote_amount": 0, "vote_count": 0, "top_users": []}]
    pred = {"id": "01M7Q1F40N3QYM6TAADBHX51HN", "channel_id": 5818636, "title": "Sitzt Top 10 drin?",
            "outcomes": outcomes, "duration": 60}
    feed("PredictionCreated", {"prediction": {**pred, "state": "ACTIVE"}})
    feed("PredictionUpdated", {"prediction": {**pred, "state": "ACTIVE"}})  # a bet: no line
    feed("PredictionUpdated", {"prediction": {**pred, "state": "LOCKED"}})
    feed("PredictionUpdated", {"prediction": {**pred, "state": "RESOLVED", "winning_outcome_id": "o1"}})
    feed("PredictionUpdated", {"prediction": {**pred, "state": "RESOLVED", "winning_outcome_id": "o1"}})
    feed("PredictionUpdated", {"prediction": {**pred, "id": "other", "state": "CANCELLED"}})
    feed("PredictionUpdated", {"prediction": "broken"})
    feed("GoalAchievedEvent", {"id": "chgoal_01M5SYJ2HBN75WMYZE7J93XAZY", "type": "subscriptions",
                               "target_value": 4000, "current_value": 4000, "status": "achieved"})
    feed("GoalAchievedEvent", {"id": "chgoal_01M5SYJ2HBN75WMYZE7J93XAZY", "type": "subscriptions",
                               "target_value": 4000, "current_value": 4000, "status": "achieved"})
    assert [alerts.headline(m) for m in got] == [
        "Prediction: Sitzt Top 10 drin? – Klar! / Neeeeee!",
        "Prediction ended: Sitzt Top 10 drin? – winner: Klar!",
        "Prediction canceled: Sitzt Top 10 drin?",
        "Goal reached: 4000 subscriptions",
    ]
