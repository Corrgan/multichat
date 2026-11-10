# SPDX-License-Identifier: GPL-3.0-or-later
"""Own YouTube chat reader against a fake YouTube, and the fallback to pytchat."""
import asyncio
import json
import sys
import types

from aiohttp import web

from multichat.chat import youtube, youtube_innertube as it
from multichat.chat.models import ChatMessage, Deletion

PORT = 18994


def text_msg(mid, author, runs, channel="UCa"):
    return {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
        "id": mid, "authorName": {"simpleText": author}, "authorExternalChannelId": channel,
        "message": {"runs": runs}}}}}


INITIAL = {
    "contents": {"liveChatRenderer": {
        "continuations": [{"invalidationContinuationData": {"continuation": "TOP", "timeoutMs": 10000}}],
        "header": {"liveChatHeaderRenderer": {"viewSelector": {"sortFilterSubMenuRenderer": {"subMenuItems": [
            {"title": "Top chat", "continuation": {"reloadContinuationData": {"continuation": "TOP"}}},
            {"title": "Live chat", "continuation": {"reloadContinuationData": {"continuation": "ALL"}}},
        ]}}}},
        "actions": [text_msg("m1", "Alice", [{"text": "hello "}, {"emoji": {
            "emojiId": "UCx/abc", "shortcuts": [":wave:"], "isCustomEmoji": True,
            "image": {"thumbnails": [{"url": "https://yt3.example/e1=w24"}, {"url": "https://yt3.example/e1=w48"}]}}}])],
    }}
}
PAGE = ('<html><script>ytcfg.set({"INNERTUBE_API_KEY":"KEY","INNERTUBE_CLIENT_VERSION":"2.20261101.00.00",'
        '"VISITOR_DATA":"VD"});</script><script>window["ytInitialData"] = ' + json.dumps(INITIAL) + ';</script></html>')


def test_parse_page_prefers_live_chat():
    context, token, actions = it.parse_page(PAGE)
    assert token == "ALL" and context["client"]["clientVersion"] == "2.20261101.00.00"
    assert context["client"]["visitorData"] == "VD" and len(actions) == 1


def test_parse_actions():
    actions = [
        text_msg("m2", "Bob", [{"text": "hi "}, {"emoji": {"emojiId": "😀", "shortcuts": [":grin:"]}}]),
        {"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {
            "id": "p1", "authorName": {"simpleText": "Rich"}, "authorExternalChannelId": "UCr",
            "purchaseAmountText": {"simpleText": "€5.00"}, "message": {"runs": [{"text": "gg"}]}}}}},
        {"markChatItemAsDeletedAction": {"targetItemId": "m2"}},
        {"markChatItemsByAuthorAsDeletedAction": {"externalChannelId": "UCbad"}},
        {"addChatItemAction": {"item": {"liveChatViewerEngagementMessageRenderer": {"id": "x"}}}},
        {"addLiveChatTickerItemAction": {}},
        {"addChatItemAction": {"item": {"liveChatPaidStickerRenderer": {
            "id": "s1", "authorName": {"simpleText": "Sticky"}, "purchaseAmountText": {"simpleText": "$2.00"}}}}},
        {"addChatItemAction": {"item": {"liveChatMembershipItemRenderer": {
            "id": "mm", "authorName": {"simpleText": "Loyal"}, "headerPrimaryText": {"runs": [{"text": "Member for "},
                                                                                         {"text": "12"}, {"text": " months"}]}}}}},
        {"addChatItemAction": {"item": {"liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": {
            "id": "g1", "authorExternalChannelId": "UCg", "header": {"liveChatSponsorshipsHeaderRenderer": {
                "authorName": {"simpleText": "Giver"}, "primaryText": {"runs": [{"text": "Gifted "}, {"text": "5"},
                                                                                {"text": " memberships"}]}}}}}}},
    ]
    ev = it.parse_actions(actions, "vid")
    assert isinstance(ev[0], ChatMessage) and ev[0].text == "hi 😀" and ev[0].user_id == "UCa"
    assert ev[1].text == "gg" and ev[1].user == "Rich"
    assert (ev[1].event, ev[1].event_value, ev[1].event_text) == ("superchat", 5.0, "€5.00")
    assert isinstance(ev[2], Deletion) and ev[2].msg_id == "m2"
    assert isinstance(ev[3], Deletion) and ev[3].user_id == "UCbad"
    sticker, member, gift = ev[4:]
    assert sticker.event == "superchat" and sticker.event_value == 2.0 and sticker.parts == []
    assert (member.event, member.event_value) == ("member", 12.0)
    assert (gift.user, gift.event, gift.event_value, gift.user_id) == ("Giver", "gift", 5.0, "UCg")
    assert len(ev) == 7


def test_reader_against_fake_youtube(monkeypatch):
    polls = []

    async def page(req):
        assert req.query["v"] == "vid12345678"
        return web.Response(text=PAGE, content_type="text/html")

    async def get_live_chat(req):
        body = await req.json()
        polls.append(body["continuation"])
        if len(polls) == 1:
            return web.json_response({"continuationContents": {"liveChatContinuation": {
                "continuations": [{"timedContinuationData": {"continuation": "NEXT", "timeoutMs": 100}}],
                "actions": [text_msg("m1", "Alice", [{"text": "dup"}]),  # already seen → skipped
                            text_msg("m3", "Carol", [{"text": "new"}])]}}})
        return web.json_response({"responseContext": {}})  # no continuation → stream ended

    async def run():
        app = web.Application()
        app.router.add_get("/live_chat", page)
        app.router.add_post("/get_live_chat", get_live_chat)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
        monkeypatch.setattr(it, "LIVE_CHAT_URL", f"http://127.0.0.1:{PORT}/live_chat")
        monkeypatch.setattr(it, "GET_LIVE_CHAT_URL", f"http://127.0.0.1:{PORT}/get_live_chat")
        got, connected = [], []
        try:
            await youtube._read_chat("vid12345678", got.append, None, lambda: connected.append(1))
        finally:
            await runner.cleanup()
        return got, connected

    got, connected = asyncio.run(run())
    assert connected == [1] and polls == ["ALL", "NEXT"]
    assert [m.user for m in got] == ["Alice", "Carol"]
    assert got[0].parts[1] == ("emote", "https://yt3.example/e1=w48", ":wave:")


def test_falls_back_to_pytchat(monkeypatch):
    async def broken(self, emit, on_connected=None, emotes_apply=None):
        raise it.InnertubeError("ytInitialData not found on the live chat page")

    used = []

    class FakeChat:
        def is_alive(self):
            return not used

        def get(self):
            used.append(1)
            item = types.SimpleNamespace(author=types.SimpleNamespace(name="Viewer", channelId="UC1"),
                                         message="via pytchat", messageEx=["via pytchat"], id="p", type="textMessage")
            return types.SimpleNamespace(sync_items=lambda: [item])

        def terminate(self):
            pass

    monkeypatch.delenv("MULTICHAT_YT_CHAT", raising=False)
    monkeypatch.setattr(it.InnertubeChat, "run", broken)
    monkeypatch.setitem(sys.modules, "pytchat", types.SimpleNamespace(create=lambda **kw: FakeChat(), DefaultProcessor=object))
    got = []
    asyncio.run(youtube._read_chat("vid12345678", got.append, None, lambda: None))
    assert [m.text for m in got] == ["via pytchat"]


def test_changed_response_shape_falls_back_to_pytchat(monkeypatch):
    """YouTube answers in a shape the reader doesn't know (here: a list, and an action that
    is no dict): that must become InnertubeError → pytchat takes over, instead of the same
    reader being retried forever with a KeyError/AttributeError."""
    import sys
    import types

    polls = []

    async def page(req):
        return web.Response(text=PAGE, content_type="text/html")

    async def get_live_chat(req):
        polls.append(1)
        return web.json_response([{"unexpected": True}])  # not an object any more

    class FakeChat:
        def is_alive(self):
            return False

        def terminate(self):
            pass

    created = []

    async def fast_sleep(_s, _real=asyncio.sleep):
        await _real(0)

    async def run():
        app = web.Application()
        app.router.add_get("/live_chat", page)
        app.router.add_post("/get_live_chat", get_live_chat)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", PORT + 1).start()
        monkeypatch.setattr(it, "LIVE_CHAT_URL", f"http://127.0.0.1:{PORT + 1}/live_chat")
        monkeypatch.setattr(it, "GET_LIVE_CHAT_URL", f"http://127.0.0.1:{PORT + 1}/get_live_chat")
        monkeypatch.setattr(it.asyncio, "sleep", fast_sleep)
        monkeypatch.delenv("MULTICHAT_YT_CHAT", raising=False)
        monkeypatch.setitem(sys.modules, "pytchat", types.SimpleNamespace(
            create=lambda **kw: created.append(kw) or FakeChat()))
        try:
            await youtube._read_chat("vid12345678", lambda ev: None, None, lambda: None)
        finally:
            await runner.cleanup()

    asyncio.run(run())
    assert len(polls) == 5 and len(created) == 1  # gave up after 5 tries, pytchat was started


def test_broken_page_shape_is_an_innertube_error():
    import pytest

    page = 'var ytInitialData = {"contents": {"liveChatRenderer": {"continuations": "oops"}}}; "INNERTUBE_CLIENT_VERSION":"2.1"'
    chat = it.InnertubeChat("vid12345678", {}, {})

    class Resp:
        status = 200

        async def text(self):
            return page

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Session:
        def __init__(self, **kw):
            pass

        def get(self, *a, **kw):
            return Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    real = it.aiohttp.ClientSession
    it.aiohttp.ClientSession = Session
    try:
        with pytest.raises(it.InnertubeError, match="unexpected live chat page"):
            asyncio.run(chat.run(lambda ev: None))
    finally:
        it.aiohttp.ClientSession = real
