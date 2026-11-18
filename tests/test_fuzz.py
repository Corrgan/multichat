# SPDX-License-Identifier: GPL-3.0-or-later
"""Fuzz tests (hypothesis) for everything that turns platform data into chat lines: Twitch IRC
lines (through the real reader), Twitch website events, Kick events, YouTube innertube JSON,
emote lists, alerts, the chat view's HTML and the overlay (payloads, server inputs).

The same harness runs every night on the test machine with 2000 examples per test; here it
runs with a few dozen so the suite stays fast. More: MULTICHAT_FUZZ_EXAMPLES=2000.

Where the app itself catches an exception around a parser (Kick event loop, YouTube shape
errors, emote loaders), that exception is not a failure. Twitch IRC lines are only caught by the
reconnect loop of run_twitch, so any exception there is one.
"""

import asyncio
import contextlib
import functools
import json
import os
import re
import signal
import socket
import threading
from html.parser import HTMLParser

import pytest
from hypothesis import HealthCheck, example, given, settings, strategies as st

import multichat.chat.alerts as alerts
import multichat.chat.cheermotes as cheermotes
import multichat.chat.emotes as emotes_mod
import multichat.chat.kick as kick
import multichat.chat.kick_events as kick_events
import multichat.chat.twitch as twitch
import multichat.chat.twitch_events as twitch_events
import multichat.chat.youtube_innertube as yti
import multichat.chat.youtube_notices as ytn
from multichat.chat.models import ChatMessage, Deletion, roles
from multichat.chat.twitch_hermes_events import TOPICS, HermesEvents
from multichat.overlay import server as overlay

EXAMPLES = int(os.environ.get("MULTICHAT_FUZZ_EXAMPLES", "50"))
HANG_S = 10


def fuzz(examples=None, **kw):
    # no deadline: CI machines are slow and uneven; hangs are caught by guarded() instead
    return settings(
        max_examples=examples or EXAMPLES,
        deadline=None,
        database=None,
        suppress_health_check=list(HealthCheck),
        print_blob=True,
        **kw,
    )


class Hang(Exception):
    pass


def guarded(fn):
    """Hard per-example timeout (SIGALRM, main thread): a hang becomes a Hang failure."""

    @functools.wraps(fn)
    def wrapper(*a, **k):
        def alarm(signum, frame):
            raise Hang(f"example ran longer than {HANG_S}s")

        old = signal.signal(signal.SIGALRM, alarm)
        signal.alarm(HANG_S)
        try:
            return fn(*a, **k)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    return wrapper


def tolerant(excs, fn, *a):
    try:
        return fn(*a)
    except excs:  # the app handles these around the parser
        return None


# --------------------------------------------------------------------------- #
# HTML checks
# --------------------------------------------------------------------------- #
ALLOWED_TAGS = {"img", "a", "span", "s", "i", "br"}
ALLOWED_ATTRS = {"src", "height", "width", "align", "style", "href"}


class _Scan(HTMLParser):
    def __init__(self, strict_src=False):
        super().__init__(convert_charrefs=True)
        self.problems = []
        self.strict_src = strict_src

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"unexpected tag <{tag}>")
        for k, v in attrs:
            if k not in ALLOWED_ATTRS:
                self.problems.append(f"unexpected attribute {k}={v!r} on <{tag}>")
            if k == "href" and v != "msg:":
                self.problems.append(f"unexpected href {v!r}")
            if (
                k == "src"
                and tag == "img"
                and self.strict_src
                and not (v.startswith("mcicon:") or v.startswith("https://"))
            ):
                self.problems.append(f"unexpected img src {v!r}")
            if k == "src" and v and self.strict_src and not v.isascii():
                self.problems.append(f"non-ASCII img src {v!r}")
            if k == "style" and re.search(r"[<>\"'(]|url|expression", v or "", re.I):
                self.problems.append(f"suspicious style {v!r}")

    handle_startendtag = handle_starttag


def check_html(h: str, strict_src=False):
    p = _Scan(strict_src)
    p.feed(h)
    p.close()
    assert not p.problems, f"HTML injection/unsafe markup: {p.problems} in {h[:400]!r}"


@pytest.fixture(scope="module")
def view(_session_qapplication):
    from multichat.ui.chat_view import ChatView

    v = ChatView()
    yield v
    v._closed = True
    v.deleteLater()


def check_event(view, ev, strict_src=True):
    """Everything downstream of a ChatMessage: chat HTML, overlay payload, headline."""
    if not isinstance(ev, ChatMessage):
        return
    check_html(view.message_html(ev), strict_src=strict_src)
    p = overlay.message_payload(ev, None, [1])
    json.loads(json.dumps(p), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    for kind, value, alt in ev.parts:
        assert kind in ("text", "emote") and isinstance(value, str) and isinstance(alt, str)
        if strict_src and kind == "emote":
            assert value.startswith("https://") and value.isascii(), f"emote url {value!r}"
    assert len(ev.text) <= 2000


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #
CANARIES = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "'><svg/onload=alert(1)>",
    '"><b>x</b>',
    "&lt;script&gt;",
    "</a><a href='javascript:alert(1)'>",
    "<style>*{}</style>",
    "<iframe src=//evil>",
    "{user}",
    "{0}",
    "{__class__}",
    "%s%d",
    "\x00\x1b[31m",
    "‮⁦",
]
canary = st.sampled_from(CANARIES)
tricky = st.text(
    alphabet=st.sampled_from(
        list("<>&\"'/\\;: =#@[]{}()-_.,0123456789aAzZ\n\r\t\x00\x01\x7f\u202e\u200b\u00e9\u00b2\u0661")
    )
    | st.characters(),
    max_size=60,
)
txt = st.one_of(tricky, canary, st.builds(lambda a, b, c: a + b + c, tricky, canary, tricky))
big = st.integers(-(2**70), 2**70)
digits = st.integers(0, 3000).map(str)

KEYS = [
    "id",
    "message",
    "text",
    "runs",
    "simpleText",
    "emoji",
    "emojiId",
    "shortcuts",
    "image",
    "thumbnails",
    "url",
    "isCustomEmoji",
    "authorName",
    "authorExternalChannelId",
    "authorBadges",
    "liveChatAuthorBadgeRenderer",
    "icon",
    "iconType",
    "customThumbnail",
    "purchaseAmountText",
    "headerPrimaryText",
    "header",
    "primaryText",
    "liveChatSponsorshipsHeaderRenderer",
    "subtext",
    "headerSubtext",
    "addChatItemAction",
    "item",
    "replayChatItemAction",
    "actions",
    "markChatItemAsDeletedAction",
    "targetItemId",
    "markChatItemsByAuthorAsDeletedAction",
    "externalChannelId",
    "liveChatTextMessageRenderer",
    "liveChatPaidMessageRenderer",
    "liveChatMembershipItemRenderer",
    "liveChatPaidStickerRenderer",
    "liveChatViewerEngagementMessageRenderer",
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer",
    "liveChatPlaceholderItemRenderer",
    "continuations",
    "invalidationContinuationData",
    "timedContinuationData",
    "continuation",
    "timeoutMs",
    "sender",
    "username",
    "identity",
    "color",
    "badges",
    "type",
    "content",
    "metadata",
    "original_sender",
    "slug",
    "user",
    "poll",
    "title",
    "options",
    "label",
    "reward_title",
    "user_input",
    "months",
    "gifted_usernames",
    "gifter_username",
    "host_username",
    "number_viewers",
    "gift",
    "amount",
    "duration",
    "permanent",
    "contents",
    "liveChatRenderer",
    "messageRenderer",
    "data",
    "host",
    "files",
    "name",
    "code",
    "emoticons",
    "urls",
    "sets",
    "emotes",
    "emote_set",
    "default_sets",
    "1",
    "2",
]
leaf = st.one_of(st.none(), st.booleans(), big, st.floats(allow_nan=True, allow_infinity=True), txt)
shaped = st.recursive(
    leaf, lambda c: st.lists(c, max_size=4) | st.dictionaries(st.sampled_from(KEYS) | txt, c, max_size=6), max_leaves=25
)
shaped_dict = st.dictionaries(st.sampled_from(KEYS), shaped, max_size=8)
simple = txt.map(lambda t: {"simpleText": t})
runs = st.lists(
    st.one_of(
        txt.map(lambda t: {"text": t}),
        st.fixed_dictionaries({"emoji": st.dictionaries(st.sampled_from(KEYS), shaped, max_size=6)}),
        shaped_dict,
    ),
    max_size=6,
).map(lambda r: {"runs": r})
yt_value = st.one_of(shaped, simple, runs)
yt_renderer = st.dictionaries(st.sampled_from(KEYS), yt_value, max_size=10).map(
    lambda d: {**d, **({"id": "abc"} if "id" not in d else {})}
)
yt_item = st.dictionaries(
    st.sampled_from(
        [
            "liveChatTextMessageRenderer",
            "liveChatPaidMessageRenderer",
            "liveChatMembershipItemRenderer",
            "liveChatPaidStickerRenderer",
            "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer",
            "liveChatViewerEngagementMessageRenderer",
            "x",
        ]
    ),
    yt_renderer | shaped,
    max_size=2,
)
yt_action = st.one_of(
    yt_item.map(lambda i: {"addChatItemAction": {"item": i}}),
    st.fixed_dictionaries(
        {
            "replayChatItemAction": st.fixed_dictionaries(
                {"actions": st.lists(yt_item.map(lambda i: {"addChatItemAction": {"item": i}}), max_size=2)}
            )
        }
    ),
    txt.map(lambda t: {"markChatItemAsDeletedAction": {"targetItemId": t}}),
    txt.map(lambda t: {"markChatItemsByAuthorAsDeletedAction": {"externalChannelId": t}}),
    shaped_dict,
)

# --- Twitch IRC ---
tag_keys = [
    "emotes",
    "badges",
    "bits",
    "msg-id",
    "system-msg",
    "color",
    "display-name",
    "room-id",
    "id",
    "user-id",
    "login",
    "target-user-id",
    "target-msg-id",
    "ban-duration",
    "msg-param-displayName",
    "msg-param-sub-plan",
    "msg-param-cumulative-months",
    "msg-param-recipient-display-name",
    "msg-param-community-gift-id",
    "msg-param-mass-gift-count",
    "msg-param-viewerCount",
    "custom-reward-id",
    "first-msg",
    "source-room-id",
    "source-id",
    "source-msg-id",
    "reply-parent-display-name",
    "reply-parent-user-login",
    "reply-parent-msg-body",
]
span = st.builds(lambda a, b: f"{a}-{b}", st.integers(-5, 3000), st.integers(-5, 3000))
emotes_tag = st.lists(
    st.builds(
        lambda i, s: f"{i}:{','.join(s)}",
        st.one_of(digits, st.just("emotesv2_ab12"), txt, st.just("")),
        st.lists(span | txt, max_size=5),
    ),
    max_size=5,
).map("/".join)
badges_tag = st.lists(
    st.builds(lambda n, v: f"{n}/{v}", st.sampled_from(list(roles.__globals__["ROLE_NAMES"])) | txt, digits), max_size=5
).map(",".join)
tag_value = st.one_of(
    emotes_tag, badges_tag, digits, txt, st.sampled_from(["#FF4500", "#12345", "red", "\\s\\:\\\\", ""])
)
tags_str = st.lists(st.tuples(st.sampled_from(tag_keys) | txt, tag_value), max_size=10).map(
    lambda pairs: ";".join(f"{k}={v}" for k, v in pairs)
)
irc_line = st.one_of(
    st.builds(
        lambda tg, pre, cmd, ch, tr: (
            (f"@{tg} " if tg is not None else "")
            + (f":{pre} " if pre is not None else "")
            + cmd
            + (f" #{ch}" if ch is not None else "")
            + (f" :{tr}" if tr is not None else "")
        ),
        st.none() | tags_str,
        st.none() | txt.map(lambda s: s.replace(" ", "") + "!u@h"),
        st.sampled_from(
            ["PRIVMSG", "USERNOTICE", "NOTICE", "CLEARMSG", "CLEARCHAT", "PING", "RECONNECT", "ROOMSTATE", ""]
        )
        | txt,
        st.none() | txt,
        st.none() | txt,
    ),
    st.text(max_size=300),
)


def twitch_line_events(line: str):
    """One IRC line through the real reader (run_twitch with a fake socket) → what it emits.
    The reader's reconnect loop catches every exception and reports it through status() before it
    waits and reconnects: that report ends the run here, and anything but a reconnect Twitch asked
    for (RECONNECT) is a failure."""

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

    got, errors = [], []

    def status(_platform, msg, **kw):
        if msg.startswith("error"):
            errors.append(kw.get("error"))
            raise asyncio.CancelledError

    async def run():
        orig = twitch.websockets.connect
        twitch.websockets.connect = lambda *_a, **_k: Socket()
        try:
            await twitch.run_twitch("chan", got.append, status)
        except asyncio.CancelledError:
            pass
        finally:
            twitch.websockets.connect = orig

    asyncio.run(run())
    for e in errors:
        assert isinstance(e, ConnectionError) and "reconnect" in str(e), f"reader failed on {line!r}: {e!r}"
    return got


def joined(parts):
    return "".join(v if k == "text" else a for k, v, a in parts)


# --------------------------------------------------------------------------- #
# Twitch
# --------------------------------------------------------------------------- #
@fuzz()
@given(irc_line)
@guarded
def test_twitch_irc_line(view, line):
    """IRC lines through the real reader (tags, emotes, cheermotes, events, shared chat) -> HTML + overlay."""
    for ev in twitch_line_events(line):
        check_event(view, ev)


@fuzz()
@given(st.text(max_size=300))
@guarded
def test_twitch_parse_irc_raw(line):
    tags, prefix, cmd, params = twitch.parse_irc(line)
    assert isinstance(tags, dict) and isinstance(cmd, str) and all(isinstance(p, str) for p in params)


@fuzz()
@example("abc", "\u00b2:0-1")
@example("abc", "\u0661:0-1")
@given(txt, emotes_tag | txt | st.none())
@guarded
def test_twitch_parts_emotes(text, emotes):
    parts = twitch.twitch_parts(text, emotes)
    assert joined(parts) == text, "twitch_parts must not change the message text"
    for k, v, a in parts:
        if k == "emote":
            assert v.isascii(), f"non-ASCII emote id in URL {v!r}"
            assert v.startswith("https://static-cdn.jtvnw.net/emoticons/v2/")


@fuzz()
@given(st.dictionaries(st.sampled_from(tag_keys) | txt, tag_value, max_size=10), txt)
@guarded
def test_twitch_event_tags(tags, login):
    twitch_events.twitch_event(tags)
    twitch_events.message_note(tags)
    twitch_events.moderation_notice(tags, login)
    twitch_events.duration_text(abs(twitch_events._int(tags.get("ban-duration"))))


@fuzz()
@given(st.integers(-(10**12), 10**30))
@guarded
def test_duration_text(n):
    twitch_events.duration_text(n)


@fuzz()
@given(
    st.lists(
        st.one_of(
            txt,
            st.sampled_from(["Cheer100", "kappa5", "Cheer0", "cheer99999999", "Cheer١٢", "anon1"]),
            st.builds(lambda n, a: f"{n}{a}", st.sampled_from(sorted(cheermotes.NAMES)), st.integers(-5, 10**9)),
        ),
        max_size=12,
    ),
    st.sampled_from([" ", "  ", "\t", "\n", "　", ""]),
)
@guarded
def test_cheermotes(words, sep):
    text = sep.join(words)
    parts = cheermotes.cheer_parts([("text", text, ""), ("emote", "https://x/y", "E")])
    assert joined(parts[:-1]) == text, "cheer_parts must keep the visible text"
    for w in words:
        r = cheermotes.cheermote(w)
        if r:
            assert r[0].isascii() and r[1] >= 1


# --------------------------------------------------------------------------- #
# Twitch website events (Hermes)
# --------------------------------------------------------------------------- #
HERMES_TYPES = [
    "POLL_CREATE",
    "POLL_COMPLETE",
    "POLL_TERMINATE",
    "event-created",
    "event-updated",
    "hype-train-start",
    "hype-train-progression",
    "hype-train-level-up",
    "hype-train-end",
    "reward-redeemed",
    "raid_go_v2",
    "raid_update_v2",
    "create",
    "goal_achieved",
    "pin-message",
    "unpin-message",
    "update-message",
    "chat_rich_embed",
    "session-started",
    "session-updated",
    "session-ended",
]
hermes_msg = st.one_of(
    shaped,
    st.fixed_dictionaries(
        {"type": st.sampled_from(HERMES_TYPES) | txt, "data": shaped | shaped_dict, "raid": shaped | shaped_dict}
    ),
)


@fuzz()
@given(st.lists(st.tuples(st.sampled_from(sorted(set(TOPICS.values()))), hermes_msg), max_size=6))
@guarded
def test_hermes_events(view, msgs):
    """Website event payloads (undocumented, may change any time) → a line or nothing, never an exception."""
    events = HermesEvents()
    for kind, msg in msgs:
        text = events.text(kind, msg)
        assert isinstance(text, str)
        if text:
            check_event(
                view,
                ChatMessage(platform="twitch", channel="c", user="Twitch", text="", event="notice", event_text=text),
            )


# --------------------------------------------------------------------------- #
# Kick
# --------------------------------------------------------------------------- #
kick_text = st.one_of(
    txt,
    st.builds(lambda i, n: f"[emote:{i}:{n}]", st.one_of(digits, st.just("١٢"), txt), txt),
    st.lists(st.one_of(txt, st.builds(lambda i, n: f"[emote:{i}:{n}]", digits, txt)), max_size=6).map(" ".join),
)


@fuzz()
@given(kick_text)
@guarded
def test_kick_parts(content):
    parts = kick.kick_parts(content)
    assert joined(parts) == kick._KICK_EMOTE_RE.sub(r"\2", content) or content == ""
    for k, v, a in parts:
        if k == "emote":
            assert v.isascii(), f"non-ASCII emote id in URL {v!r}"


KICK_NAMES = [
    "App\\Events\\ChatMessageEvent",
    "ChatMessageEvent",
    "App\\Events\\SubscriptionEvent",
    "GiftedSubscriptionsEvent",
    "StreamHostEvent",
    "KicksGifted",
    "RewardRedeemedEvent",
    "PinnedMessageCreatedEvent",
    "PollUpdateEvent",
    "MessageDeletedEvent",
    "UserBannedEvent",
    "ChatroomClearEvent",
    "Other",
]
kick_data = st.one_of(
    st.fixed_dictionaries(
        {
            "id": txt,
            "content": kick_text,
            "type": st.sampled_from(["message", "reply", ""]) | txt,
            "sender": st.fixed_dictionaries(
                {
                    "id": big | txt,
                    "username": txt,
                    "identity": st.fixed_dictionaries(
                        {
                            "color": txt | st.sampled_from(["#aabbcc", "#abc"]),
                            "badges": st.lists(
                                st.fixed_dictionaries(
                                    {"type": txt | st.sampled_from(["moderator", "vip", "subscriber"])}
                                ),
                                max_size=4,
                            ),
                        }
                    ),
                }
            ),
            "metadata": st.fixed_dictionaries({"original_sender": st.fixed_dictionaries({"username": txt})}),
        }
    ),
    shaped_dict,
)


@fuzz()
@given(st.sampled_from(KICK_NAMES) | txt, kick_data, st.sampled_from(["chan", "x"]))
@guarded
def test_kick_handle_event(view, name, data, slug):
    out = []
    # run_kick wraps _handle_event in `except Exception` (one bad event must not drop the connection)
    tolerant(Exception, kick._handle_event, name, data, slug, out.append, None)
    for ev in out:
        check_event(view, ev)
    for fn, args in (
        (kick_events.kick_event, (name, data)),
        (kick_events.kick_notice, (name, data, slug)),
        (kick_events.ban_notice, (data,)),
    ):
        tolerant("kick_events." + fn.__name__, Exception, fn, *args)


# --------------------------------------------------------------------------- #
# YouTube / innertube
# --------------------------------------------------------------------------- #
SHAPE = yti._SHAPE_ERRORS + (yti.InnertubeError, yti.ChatEnded)


@fuzz()
@given(st.lists(yt_action, max_size=4), txt)
@guarded
def test_youtube_parse_actions(view, actions, vid):
    # InnertubeChat.run converts shape errors into InnertubeError (reader falls back to pytchat)
    events = tolerant(yti._SHAPE_ERRORS, yti.parse_actions, actions, vid)
    for ev in events or []:
        check_event(view, ev)


@fuzz()
@given(shaped, txt)
@guarded
def test_youtube_notice_and_runs(view, item, vid):
    if isinstance(item, dict):
        ev = tolerant(yti._SHAPE_ERRORS, ytn.notice, item, vid)
        if ev:
            check_event(view, ev)
    tolerant(yti._SHAPE_ERRORS, yti.runs_to_parts, item if isinstance(item, list) else [item])
    tolerant(yti._SHAPE_ERRORS, yti._badges, item if isinstance(item, dict) else {})


page_json = st.one_of(shaped_dict.map(lambda d: {"contents": {"liveChatRenderer": d}}), shaped)
page_str = st.builds(
    lambda pre, js, post, cfg: f"{pre}ytInitialData = {js}{post}{cfg}",
    txt,
    page_json.map(json.dumps),
    txt,
    st.sampled_from(["", '"INNERTUBE_CLIENT_VERSION":"2.2024"', '"clientVersion":"x","VISITOR_DATA":"v"']),
)


@fuzz()
@given(st.one_of(page_str, st.text(max_size=200)), st.sampled_from(["ytInitialData", "x"]))
@guarded
def test_youtube_parse_page(page, marker):
    with contextlib.suppress(*SHAPE):
        yti.parse_page(page)
    with contextlib.suppress(*SHAPE):
        yti._find_json(page, marker)
    yti._cfg(page, "clientVersion")


@guarded
def test_youtube_deep_json_nesting():
    """Deeply nested JSON in the live chat page: expected InnertubeError, not RecursionError (run() only
    converts _SHAPE_ERRORS + InnertubeError)."""
    for depth in (500, 5000, 100000):
        page = "ytInitialData = " + "[" * depth + "]" * depth
        try:
            yti.parse_page(page)
        except SHAPE:
            pass
        except RecursionError as e:
            pytest.fail(f"RecursionError escapes parse_page at depth {depth}: {str(e)[:100]}")


@fuzz()
@given(st.lists(shaped, max_size=5))
@guarded
def test_youtube_continuation(conts):
    with contextlib.suppress(*SHAPE):
        yti._continuation(conts)


# --------------------------------------------------------------------------- #
# Emotes (7TV/BTTV/FFZ parsing + apply)
# --------------------------------------------------------------------------- #
def make_store():
    return emotes_mod.EmoteStore(lambda: None)


@fuzz()
@given(shaped, shaped, shaped, st.one_of(st.none(), st.lists(big, max_size=3)))
@guarded
def test_emote_json_parsers(seven, bttv, ffz, only):
    store = make_store()
    # EmoteStore._merge ignores loader exceptions, so a raise only discards that provider's set
    tolerant(Exception, store._7tv_parse, seven if isinstance(seven, list) else [seven])
    tolerant(Exception, store._bttv_parse, bttv if isinstance(bttv, list) else [bttv])
    tolerant(Exception, store._ffz_parse, ffz if isinstance(ffz, dict) else {"1": ffz}, only)


emote_name = st.one_of(st.from_regex(r"[A-Za-z0-9_:()\-]{1,12}", fullmatch=True), txt)
emote_url = st.sampled_from(["https://cdn.7tv.app/emote/1/2x.webp", "https://x/y.png"]) | txt


@fuzz()
@given(
    st.dictionaries(emote_name, emote_url, max_size=8),
    st.dictionaries(emote_name, emote_url, max_size=8),
    st.lists(
        st.one_of(st.tuples(st.just("text"), txt, st.just("")), st.tuples(st.just("emote"), emote_url, emote_name)),
        max_size=6,
    ),
    st.sampled_from(["twitch", "kick"]),
    txt,
)
@guarded
def test_emote_apply(view, glob, chan, parts, platform, channel):
    store = make_store()
    store.global_ = glob
    store.channels[(platform, channel.lower())] = chan
    out = store.apply(platform, channel, parts)
    assert joined(out) == joined(parts), "emote apply must keep the visible text"
    store.available(platform, channel)
    ev = ChatMessage(platform, "c", "u", "t", parts=out)
    check_html(view.message_html(ev))


# --------------------------------------------------------------------------- #
# Alerts + ChatMessage + HTML rendering
# --------------------------------------------------------------------------- #
@fuzz()
@given(txt | st.text(max_size=40))
@guarded
def test_parse_amount(t):
    alerts.parse_amount(t)


part_s = st.one_of(
    st.tuples(st.just("text"), txt, txt), st.tuples(st.just("emote"), emote_url, txt), shaped.map(lambda x: x)
)


@fuzz()
@given(
    st.sampled_from(["twitch", "kick", "youtube"]),
    txt,
    txt,
    txt,
    st.lists(part_s, max_size=8),
    st.none() | txt,
    st.sampled_from(["", "sub", "gift", "raid", "bits", "superchat", "member", "notice", "bogus"]),
    st.floats(allow_nan=True, allow_infinity=True) | big,
    txt,
    st.booleans(),
)
@guarded
def test_message_render(view, platform, channel, user, text, parts, color, event, value, etext, deleted):
    """Any ChatMessage -> chat view HTML must only contain the known safe markup (no <script> etc.)."""
    m = ChatMessage(
        platform, channel, user, text, parts=parts, color=color, event=event, event_value=value, event_text=etext
    )
    m.deleted = deleted
    check_html(view.message_html(m))
    view.items([m])
    alerts.headline(m)


# --------------------------------------------------------------------------- #
# Overlay server
# --------------------------------------------------------------------------- #
def test_overlay_page_uses_no_html_sinks():
    from multichat.core.paths import resource

    page = resource("overlay.html").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in page, f"overlay.html uses {sink}"


@fuzz()
@given(
    st.sampled_from(["twitch", "kick", "youtube"]),
    txt,
    txt,
    st.lists(part_s, max_size=6),
    st.none() | txt,
    st.sampled_from(["", "sub", "notice", "bits"]),
    st.floats(allow_nan=True, allow_infinity=True),
    st.lists(big, max_size=3),
)
@guarded
def test_overlay_payload(platform, user, text, parts, color, event, value, groups):
    m = ChatMessage(
        platform, "c", user, text, parts=parts, color=color, event=event, event_value=value, event_text=text
    )
    p = overlay.message_payload(m, {}, groups)
    json.loads(json.dumps(p), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {c}")))
    json.dumps(overlay.deletion_payload(Deletion(platform, msg_id=text, user_id=user)))


class _Srv:
    """OverlayServer on 127.0.0.1 only, in its own loop thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.server = overlay.OverlayServer(self.loop)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.call(self.server.start(self.port))

    def call(self, coro, timeout=15):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self):
        self.call(self.server.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture(scope="module")
def srv():
    s = _Srv()
    yield s
    s.close()


async def _poke(port, host, origin, path, frames, big_frame):
    import aiohttp

    headers = {}
    if host is not None:
        headers["Host"] = host
    if origin is not None:
        headers["Origin"] = origin
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        if path == "/":
            async with s.get(f"http://127.0.0.1:{port}/", headers=headers) as r:
                await r.read()
                assert r.status in (200, 403)
            return
        try:
            async with s.ws_connect(f"http://127.0.0.1:{port}{path}", headers=headers) as ws:
                for f in frames:
                    if isinstance(f, bytes):
                        await ws.send_bytes(f)
                    else:
                        await ws.send_str(f)
                if big_frame:
                    await ws.send_str("x" * 5000)
                await asyncio.sleep(0.01)
        except aiohttp.WSServerHandshakeError as e:
            assert e.status in (403, 404, 400, 503, 426), e.status
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError):
            pass


header_val = (
    st.none()
    | st.sampled_from(["127.0.0.1", "localhost", "evil.example", "", "127.0.0.1:1"])
    | txt.map(lambda s: s.encode("ascii", "ignore").decode().replace("\r", "").replace("\n", ""))
)


@fuzz(examples=max(20, EXAMPLES // 20))
@given(
    header_val,
    header_val,
    st.sampled_from(["/", "/ws", "/nope"]),
    st.lists(st.binary(max_size=64) | txt, max_size=3),
    st.booleans(),
)
@guarded
def test_overlay_server_inputs(srv, host, origin, path, frames, big_frame):
    """Hostile Host/Origin headers and WebSocket frames: server must answer and stay alive (loopback only)."""
    h = srv.port if host in (None, "") else host
    host = h if isinstance(h, str) else f"127.0.0.1:{h}"
    try:
        srv.call(_poke(srv.port, host, origin, path, frames, big_frame), timeout=10)
    except (ValueError, UnicodeError):
        return  # client library refused the header itself (harness side)
    assert srv.server.running
    # still serves afterwards
    srv.call(_poke(srv.port, f"127.0.0.1:{srv.port}", None, "/", [], False), timeout=10)


@fuzz(examples=max(10, EXAMPLES // 40))
@given(st.lists(st.tuples(txt, txt, st.lists(part_s, max_size=4)), min_size=1, max_size=5))
@guarded
def test_overlay_broadcast_delivers_valid_json(srv, msgs):
    import aiohttp

    async def run():
        got = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.ws_connect(f"http://127.0.0.1:{srv.port}/ws") as ws:
                for _ in range(100):
                    if srv.server.clients:
                        break
                    await asyncio.sleep(0.01)
                for user, text, parts in msgs:
                    srv.server.publish(
                        overlay.message_payload(ChatMessage("twitch", "c", user, text, parts=parts), {}, [1])
                    )
                for _ in msgs:
                    got.append(json.loads(await asyncio.wait_for(ws.receive_str(), 3)))
        return got

    got = srv.call(run(), timeout=15)
    assert len(got) == len(msgs) and all(g["type"] == "msg" for g in got)
