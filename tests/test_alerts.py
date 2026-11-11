# SPDX-License-Identifier: GPL-3.0-or-later
"""Alert events: Twitch USERNOTICE, Kick events, amounts, headlines, thresholds."""
from multichat.chat.alerts import DEFAULT_ALERTS, headline, is_alert, parse_amount
from multichat.chat.kick import kick_event
from multichat.chat.models import ChatMessage
from multichat.chat.twitch import parse_irc, twitch_event


def test_twitch_usernotice():
    tags, _p, cmd, params = parse_irc(
        "@badges=subscriber/12;display-name=Alice;msg-id=resub;msg-param-cumulative-months=12;"
        "msg-param-sub-plan=1000;user-id=1 :tmi.twitch.tv USERNOTICE #chan :still here"
    )
    assert cmd == "USERNOTICE" and params[-1] == "still here"
    assert twitch_event(tags) == ("sub", 12, "Tier 1")
    assert twitch_event({"msg-id": "sub", "msg-param-sub-plan": "Prime"}) == ("sub", 1, "Prime")
    assert twitch_event({"msg-id": "submysterygift", "msg-param-mass-gift-count": "5"}) == ("gift", 5, "")
    # the single notices of a community gift are skipped – the total above counts once
    assert twitch_event({"msg-id": "subgift", "msg-param-community-gift-id": "123"}) is None
    assert twitch_event({"msg-id": "subgift", "msg-param-recipient-display-name": "Bob"}) == ("gift", 1, "Bob")
    assert twitch_event({"msg-id": "raid", "msg-param-viewerCount": "42"}) == ("raid", 42, "")
    # everything else Twitch announces is shown as a notice, in Twitch's own words
    assert twitch_event({"msg-id": "announcement"}) == ("notice", 0, "Announcement")
    assert twitch_event({"msg-id": "viewermilestone", "system-msg": "Viewer sparked a watch streak!"}) == (
        "notice", 0, "Viewer sparked a watch streak!")
    assert twitch_event({"msg-id": "some-new-kind"}) == ("notice", 0, "some new kind")


def test_kick_events():
    assert kick_event("App\\Events\\SubscriptionEvent", {"username": "a", "months": 3}) == ("a", "sub", 3, "")
    assert kick_event("App\\Events\\GiftedSubscriptionsEvent",
                      {"gifter_username": "g", "gifted_usernames": ["x", "y"]}) == ("g", "gift", 2, "")
    assert kick_event("App\\Events\\GiftedSubscriptionsEvent",
                      {"gifter_username": "g", "gifted_usernames": ["x"]}) == ("g", "gift", 1, "x")
    assert kick_event("App\\Events\\StreamHostEvent", {"host_username": "h", "number_viewers": 9}) == ("h", "raid", 9, "")
    assert kick_event("KicksGifted", {"sender": {"username": "k"}, "gift": {"amount": 100}}) == ("k", "bits", 100, "Kicks")
    assert kick_event("App\\Events\\ChatMessageEvent", {}) is None


def test_amounts():
    assert parse_amount("€5.00") == 5.0
    assert parse_amount("$1,000.00") == 1000.0
    assert parse_amount("5,00 €") == 5.0
    assert parse_amount("¥1,000") == 1000.0
    assert parse_amount("1.234,56 €") == 1234.56
    assert parse_amount("") == 0.0


def test_headlines_and_thresholds():
    def ev(event, value, text=""):
        return ChatMessage("twitch", "c", "Alice", "", event=event, event_value=value, event_text=text)

    assert ChatMessage("twitch", "c", "Alice", "", event="raid").parts == []
    assert headline(ev("sub", 12, "Tier 1")) == "Alice resubscribed (12 months) · Tier 1"
    assert headline(ev("sub", 1)) == "Alice subscribed"
    assert headline(ev("gift", 5)) == "Alice gifted 5 subs"
    assert headline(ev("gift", 1, "Bob")) == "Alice gifted a sub to Bob"
    assert headline(ev("raid", 42)) == "Alice is raiding with 42 viewers"
    assert headline(ev("bits", 100)) == "Alice cheered 100 bits"
    assert headline(ev("bits", 50, "Kicks")) == "Alice sent 50 Kicks"
    assert headline(ev("superchat", 5, "€5.00")) == "Alice sent a Super Chat: €5.00"
    assert headline(ChatMessage("twitch", "c", "Alice", "hi")) == ""
    cfg = dict(DEFAULT_ALERTS, min_bits=100, raid=False)
    assert is_alert(ev("bits", 100), cfg) and not is_alert(ev("bits", 99), cfg)
    assert not is_alert(ev("raid", 1000), cfg) and is_alert(ev("sub", 1), cfg)
    assert not is_alert(ChatMessage("twitch", "c", "Alice", "hi"), cfg)
