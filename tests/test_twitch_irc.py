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

    # still once only: the per-recipient lines of a community gift
    assert _read("@display-name=Gifter;id=a5;login=gifter;msg-id=subgift;msg-param-community-gift-id=77;"
                 "msg-param-recipient-display-name=Bob;room-id=1;user-id=3 :tmi.twitch.tv USERNOTICE #examplechan",
                 monkeypatch) == []


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

    def feed(name, data):
        kick._handle_event(name, data, "examplekick", got.append, None)

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
        ("YouTube", "notice", "Welcome to live chat!", ""),
        ("@viewer", "notice", "@viewer received a gift membership by @gifter", ""),
        ("YouTube", "notice", "A new kind of line", ""),
        ("@chatter", "", "", "hello"),
    ]
