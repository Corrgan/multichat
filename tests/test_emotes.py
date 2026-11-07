# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio

from multichat.chat.emotes import EmoteStore
from multichat.chat.kick import kick_parts
from multichat.chat.twitch import twitch_parts


def seven(name, eid, files=("1x.webp","2x.webp","1x.png","2x.png")):
    return {"name": name, "data": {"host": {"url": f"//cdn.7tv.app/emote/{eid}", "files": [{"name": f} for f in files]}}}
FAKE = {
  "https://7tv.io/v3/emote-sets/global": {"emotes": [seven("EZ","g1"), seven("Clap","g2")]},
  "https://api.betterttv.net/3/cached/emotes/global": [{"id":"b1","code":"catJAM"}, {"id":"b2","code":"EZ"}],
  "https://api.frankerfacez.com/v1/set/global": {"default_sets":[3], "sets":{"3":{"emoticons":[{"name":"ZreknarF","urls":{"1":"//cdn.frankerfacez.com/emote/1/1","2":"//cdn.frankerfacez.com/emote/1/2"}}]}, "4":{"emoticons":[{"name":"NOTDEFAULT","urls":{"1":"x"}}]}}},
  "https://7tv.io/v3/users/twitch/123": {"emote_set": {"emotes": [seven("peepoHey","c1")]}},
  "https://api.betterttv.net/3/cached/users/twitch/123": {"channelEmotes":[{"id":"cb","code":"KEKW"}], "sharedEmotes":[{"id":"sb","code":"Clap"}]},
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
    assert dict((a,v) for k,v,a in p if k=="emote")["catJAM"] == "https://cdn.betterttv.net/emote/b1/2x"
    p = s.apply("kick", "examplekick", kick_parts("kickHype [emote:5:KEKW] peepoHey"))
    assert [a for k,v,a in p if k=="emote"] == ["kickHype","KEKW"], p   # peepoHey is a Twitch channel emote only
    s.enabled = False
    assert s.apply("twitch","chan",[("text","EZ","")]) == [("text","EZ","")]
    
