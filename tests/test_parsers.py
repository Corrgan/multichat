# SPDX-License-Identifier: GPL-3.0-or-later
from multichat.chat.kick import kick_parts
from multichat.chat.twitch import parse_irc, twitch_parts
from multichat.chat.youtube import parse_live_page, youtube_target, youtube_video_id

YT = "https://www.youtube.com/"


def test_irc_privmsg_and_emotes():
    tags, _prefix, cmd, params = parse_irc(
        "@badge-info=;color=#FF0000;display-name=Alice;emotes=25:0-4,12-16;id=abc;user-id=42 "
        ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :Kappa hello Kappa"
    )
    assert cmd == "PRIVMSG" and params == ["#chan", "Kappa hello Kappa"] and tags["id"] == "abc"
    parts = twitch_parts(params[1], tags["emotes"])
    assert [p[0] for p in parts] == ["emote", "text", "emote"]
    assert parts[1][1] == " hello " and parts[0][2] == "Kappa"


def test_irc_misc():
    tags, _p, cmd, _params = parse_irc("@target-msg-id=xyz :tmi.twitch.tv CLEARMSG #chan :bad msg")
    assert cmd == "CLEARMSG" and tags["target-msg-id"] == "xyz"
    assert parse_irc("PING :tmi.twitch.tv")[2:] == ("PING", ["tmi.twitch.tv"])


def test_kick_parts():
    assert kick_parts("hi [emote:37226:KEKW] you") == [
        ("text", "hi ", ""),
        ("emote", "https://files.kick.com/emotes/37226/fullsize", "KEKW"),
        ("text", " you", ""),
    ]


def test_youtube_targets():
    cases = {
        "@examplechannel": ("channel", YT + "@examplechannel"),
        "https://www.youtube.com/@examplechannel/streams": ("channel", YT + "@examplechannel"),
        "youtube.com/@Some.Name?si=x": ("channel", YT + "@Some.Name"),
        "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx": ("channel", YT + "channel/UCxxxxxxxxxxxxxxxxxxxxxx"),
        "UCxxxxxxxxxxxxxxxxxxxxxx": ("channel", YT + "channel/UCxxxxxxxxxxxxxxxxxxxxxx"),
        "https://www.youtube.com/watch?v=abcdefghijk&t=3": ("video", "abcdefghijk"),
        "https://youtu.be/abcdefghijk": ("video", "abcdefghijk"),
        "https://www.youtube.com/live/abcdefghijk?si=a": ("video", "abcdefghijk"),
        "abcdefghijk": ("video", "abcdefghijk"),
        "examplechannel": ("channel", YT + "@examplechannel"),
    }
    for value, expected in cases.items():
        assert youtube_target(value) == expected, value
    assert youtube_video_id("https://youtube.com/live/abcdefghijk") == "abcdefghijk"


def test_youtube_live_page():
    canon = '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
    title = '<meta name="title" content="Mission Control &amp; Launch">'
    live = canon + title + '..."liveBroadcastDetails":{"isLiveNow":true,"startTimestamp":"2026"}'
    upcoming = canon + title + '"liveBroadcastDetails":{"isLiveNow":false,"startTimestamp":"2026"}'
    offline = '<link rel="canonical" href="https://www.youtube.com/channel/UCxxx">"isLiveNow":true'
    assert parse_live_page(live) == ("abcdefghijk", "Mission Control & Launch")
    assert parse_live_page(upcoming) is None and parse_live_page(offline) is None


def test_name_colors_are_validated():
    from multichat.chat.models import ChatMessage

    assert ChatMessage("kick", "c", "u", "t", color="#A1b2C3").color == "#A1b2C3"
    for bad in ("red;background:url(http://x.invalid/a.png)", "#12345", "'><script>", "javascript:x"):
        assert ChatMessage("kick", "c", "u", "t", color=bad).color is None


def test_roles_and_badges():
    from multichat.chat.models import roles

    assert roles(["moderator", "subscriber", "glhf-pledge"]) == ("moderator", "subscriber")
    assert roles(["owner", "member", "sub_gifter"]) == ("broadcaster", "subscriber")


def test_since_text():
    from datetime import datetime, timezone

    from multichat.ui.moderation import since_text

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert since_text("2025-12-02T10:00:00Z", now).endswith("(29 days)")
    assert since_text("2025-01-01T00:00:00Z", now).endswith("(12 months)")
    assert since_text("2020-01-01T00:00:00Z", now).endswith("(6 years)")
    assert since_text("", now) == "" and since_text("garbage", now) == ""
