# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio

from multichat.chat.emotes import EmoteStore
from multichat.chat.kick import kick_parts
from multichat.chat.twitch import twitch_parts


def seven(name, eid, files=("1x.webp","2x.webp","1x.png","2x.png")):
    return {"name": name, "data": {"host": {"url": f"//cdn.7tv.app/emote/{eid}", "files": [{"name": f} for f in files]}}}
FAKE = {
  "https://7tv.io/v3/emote-sets/global": {"emotes": [seven("EZ","g1"), seven("Clap","g2")]},
  "https://api.betterttv.net/3/cached/emotes/global": [{"id":"5f1b0186cf6d2144653d2970","code":"catJAM"}, {"id":"5f1b0186cf6d2144653d2971","code":"EZ"}],
  "https://api.frankerfacez.com/v1/set/global": {"default_sets":[3], "sets":{"3":{"emoticons":[{"name":"ZreknarF","urls":{"1":"//cdn.frankerfacez.com/emote/1/1","2":"//cdn.frankerfacez.com/emote/1/2"}}]}, "4":{"emoticons":[{"name":"NOTDEFAULT","urls":{"1":"x"}}]}}},
  "https://7tv.io/v3/users/twitch/123": {"emote_set": {"emotes": [seven("peepoHey","c1")]}},
  "https://api.betterttv.net/3/cached/users/twitch/123": {"channelEmotes":[{"id":"5f1b0186cf6d2144653d2972","code":"KEKW"}], "sharedEmotes":[{"id":"5f1b0186cf6d2144653d2973","code":"Clap"}]},
  "https://api.frankerfacez.com/v1/room/id/123": {"sets":{"9":{"emoticons":[{"name":"OMEGALUL","urls":{"2":"https://cdn.frankerfacez.com/emote/9/2"}}]}}},
  "https://7tv.io/v3/users/kick/77": {"emote_set": {"emotes": [seven("kickHype","k1", files=("1x.webp","2x.webp"))]}},
  "https://7tv.io/v3/users/twitch/404": None,
}


def test_emote_store():
    asyncio.run(_run())


async def _run():
    s = EmoteStore(lambda: None, ["png", "gif"])  # simulate Qt without WebP
    async def fake_json(url): return FAKE.get(url)
    s._json = fake_json
    counts = await s.load_global()
    assert counts == {"ffz":1, "bttv":2, "7tv":2}, counts
    assert "NOTDEFAULT" not in s.global_
    assert s.global_["EZ"] == "https://cdn.7tv.app/emote/g1/2x.png", s.global_["EZ"]  # 7TV beats BTTV
    s.schedule_channel("twitch", "Chan", "123"); s.schedule_channel("twitch", "chan", "123")
    s.schedule_channel("kick", "examplekick", "77"); s.schedule_channel("twitch", "empty", "404")
    await asyncio.sleep(0.05)
    assert set(s.channels[("twitch","chan")]) == {"peepoHey","KEKW","Clap","OMEGALUL"}
    assert s.channels[("kick","examplekick")]["kickHype"].endswith("/2x.webp")  # no png available → webp fallback
    assert s.channels[("twitch","empty")] == {}
    text = "Kappa EZ  catJAM KEKW, KEKW"
    p = s.apply("twitch", "chan", twitch_parts(text, "25:0-4"))
    kinds = [(k, a or v) for k, v, a in p]
    assert kinds == [("emote","Kappa"),("text"," "),("emote","EZ"),("text","  "),("emote","catJAM"),("text"," KEKW, "),("emote","KEKW")], kinds
    assert "".join(a if k=="emote" else v for k,v,a in p) == text
    assert dict((a,v) for k,v,a in p if k=="emote")["catJAM"] == "https://cdn.betterttv.net/emote/5f1b0186cf6d2144653d2970/2x"
    p = s.apply("kick", "examplekick", kick_parts("kickHype [emote:5:KEKW] peepoHey"))
    assert [a for k,v,a in p if k=="emote"] == ["kickHype","KEKW"], p   # peepoHey is a Twitch channel emote only
    s.enabled = False
    assert s.apply("twitch","chan",[("text","EZ","")]) == [("text","EZ","")]
    


def test_non_https_urls_are_dropped():
    s = EmoteStore(lambda: None, ["png"])
    assert s._ffz_parse({"1": {"emoticons": [{"name": "A", "urls": {"2": "http://x.invalid/a"}},
                                             {"name": "B", "urls": {"2": "//cdn.example/b"}}]}}) == {"B": "https://cdn.example/b"}
    assert s._7tv_url({"data": {"host": {"url": "file:///etc", "files": [{"name": "2x.png"}]}}}) is None
    assert s._bttv_parse([{"id": "../../x", "code": "C"}]) == {}


def test_channel_metadata_lru_ttl_and_entry_budgets(monkeypatch):
    from multichat.chat import emotes
    monkeypatch.setattr(emotes, "MAX_CHANNELS", 3)
    monkeypatch.setattr(emotes, "MAX_CHANNEL_EMOTES", 2)
    monkeypatch.setattr(emotes, "MAX_CACHED_EMOTES", 5)
    clock = [100.0]
    monkeypatch.setattr(emotes.time, "monotonic", lambda: clock[0])
    async def run():
        s = EmoteStore(lambda: None)
        async def fake_user(platform, ext_id):
            return {f"E{i}": f"https://example.test/{ext_id}/{i}" for i in range(20)}
        s._7tv_user = fake_user
        for name in ("a", "b"):
            await s.load_channel("kick", name, "1")
        s.available("kick", "a")  # recent access protects A, not B
        await s.load_channel("kick", "c", "1")
        assert list(s.channels) == [("kick", "a"), ("kick", "c")]
        assert all(len(v) == 2 for v in s.channels.values())
        assert set(s._loaded_at) == set(s.channels)
        for i in range(100):
            await s.load_channel("kick", str(i), "1")
        assert len(s.channels) <= 3
        assert sum(map(len, s.channels.values())) <= 5
        assert len(s._loaded_at) <= 3
        clock[0] += emotes.CHANNEL_TTL
        assert s.available("kick", "99") == {}
        assert not s.channels and not s._loaded_at and not s._empty_since
    asyncio.run(run())


def test_channel_load_scheduling_is_bounded_and_deduplicated():
    from multichat.chat.emotes import MAX_LOADS, MAX_PENDING_LOADS
    async def run():
        s = EmoteStore(lambda: None)
        gate = asyncio.Event()
        calls = []
        async def fake_user(platform, ext_id):
            calls.append(ext_id)
            await gate.wait()
            return {}
        s._7tv_user = fake_user
        for i in range(1000):
            s.schedule_channel("kick", str(i // 2), str(i // 2))
        assert len(s._tasks) == MAX_PENDING_LOADS
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(calls) == MAX_LOADS
        assert len(set(calls)) == MAX_LOADS
        gate.set()
        await asyncio.gather(*s._tasks)
        await asyncio.sleep(0)
        assert not s._scheduled and not s._loading and not s._tasks
        assert len(calls) == MAX_PENDING_LOADS  # queued channels were not silently lost
    asyncio.run(run())


def test_metadata_stream_aborts_before_reading_unbounded_body(monkeypatch):
    from multichat.chat import emotes
    monkeypatch.setattr(emotes, "MAX_JSON_BYTES", 16)
    chunks_read = []
    class Content:
        async def iter_chunked(self, size):
            for i in range(100):
                chunks_read.append(i)
                yield b" " * 9
    class Response:
        status, content_length, content = 200, None, Content()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Session:
        def get(self, *args, **kwargs): return Response()
    import pytest
    with pytest.raises(ValueError, match="byte limit"):
        asyncio.run(EmoteStore(Session)._json("https://example.test"))
    assert len(chunks_read) == 2
