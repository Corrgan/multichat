# SPDX-License-Identifier: GPL-3.0-or-later
import types

import pytest

from multichat.video import streams

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
v360/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=852x480
v480/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720
v720/index.m3u8
"""


def test_quality_helpers():
    assert streams.normalize_quality("Quelle") == "source"  # value stored by older versions
    assert streams.normalize_quality("nonsense") == streams.DEFAULT_QUALITY
    assert streams.quality_height("720p") == 720 and streams.quality_height("source") == 0


def test_pick_variants():
    base = "https://example.invalid/master.m3u8"
    assert streams.pick_hls_variant(MASTER, base, 480).endswith("/v480/index.m3u8")
    assert streams.pick_hls_variant(MASTER, base, 0).endswith("/v720/index.m3u8")
    names = ["audio_only", "160p", "360p", "480p", "720p60", "1080p60", "best", "worst"]
    assert streams._pick_streamlink(names, 480) == "480p"
    assert streams._pick_streamlink(["360p", "720p60", "best"], 0) == "720p60"
    assert streams._pick_streamlink(["720p60", "1080p60"], 360) == "720p60"


def test_youtube_lookup(monkeypatch):
    page = ('<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
            '<meta name="title" content="Example stream">"isLiveNow":true')
    seen = []

    def fake_get(url, **kw):
        seen.append(url)
        return types.SimpleNamespace(status_code=200, text=page)

    monkeypatch.setattr(streams, "_http_get", fake_get)
    monkeypatch.setattr(streams, "check_dns", lambda host: None)
    s = streams._youtube("@examplechannel", 480)
    assert seen == ["https://www.youtube.com/@examplechannel/live"]
    assert s.url.endswith("watch?v=abcdefghijk") and s.title == "Example stream" and s.ytdl_format


def test_youtube_offline(monkeypatch):
    monkeypatch.setattr(streams, "_http_get", lambda url, **kw: types.SimpleNamespace(status_code=200, text="<html>"))
    monkeypatch.setattr(streams, "check_dns", lambda host: None)
    with pytest.raises(streams.Offline):
        streams._youtube("@examplechannel", 480)


def test_chat_and_video_share_the_live_check(monkeypatch):
    """The chat just looked whether the channel is live – the video tile reuses that answer."""
    import asyncio

    from multichat.chat import youtube

    page = ('<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
            '<meta name="title" content="Example stream">"isLiveNow":true')
    youtube.LIVE_CACHE.put("https://www.youtube.com/@examplechannel", ("abcdefghijk", "Example stream"))
    calls = []
    monkeypatch.setattr(streams, "_http_get", lambda url, **kw: calls.append(url) or types.SimpleNamespace(
        status_code=200, text=page))
    monkeypatch.setattr(streams, "check_dns", lambda host: None)
    s = streams._youtube("@examplechannel", 480)
    assert calls == [] and s.url.endswith("watch?v=abcdefghijk")
    # and the chat reuses an answer of the video tile
    assert asyncio.run(youtube.find_live_video("https://www.youtube.com/@examplechannel")) == (
        "abcdefghijk", "Example stream")


def test_kick_channel_info_is_shared(monkeypatch):
    from multichat.chat import kick

    calls = []
    monkeypatch.setattr(kick, "_kick_get", lambda url, slug: calls.append(url) or {"chatroom": {"id": 5}, "user_id": 7})
    assert kick._fetch_kick_channel("examplekick") == (5, "7")
    assert kick.fetch_kick_json("Examplekick")["chatroom"]["id"] == 5
    assert len(calls) == 1
