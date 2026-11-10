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
        return types.SimpleNamespace(status_code=200, text=page, url=url)

    from multichat.chat import youtube

    monkeypatch.setattr(youtube, "_get", fake_get)
    monkeypatch.setattr(streams, "check_dns", lambda host: None)
    s = streams._youtube("@examplechannel", 480)
    assert seen == ["https://www.youtube.com/@examplechannel/live"]
    assert s.url.endswith("watch?v=abcdefghijk") and s.title == "Example stream" and s.ytdl_format


def test_youtube_offline(monkeypatch):
    from multichat.chat import youtube

    monkeypatch.setattr(youtube, "_get", lambda url, **kw: types.SimpleNamespace(status_code=200, text="<html>", url=url))
    monkeypatch.setattr(streams, "check_dns", lambda host: None)
    with pytest.raises(streams.Offline):
        streams._youtube("@examplechannel", 480)


def test_chat_and_video_share_the_live_check(monkeypatch):
    """Chat and video tile ask at the same moment – one request, both get the answer."""
    import asyncio
    import threading
    import time as _time

    from multichat.chat import youtube

    page = ('<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
            '<meta name="title" content="Example stream">"isLiveNow":true')
    calls = []

    def slow_get(url, **kw):
        calls.append(url)
        _time.sleep(0.3)
        return types.SimpleNamespace(status_code=200, text=page, url=url)

    monkeypatch.setattr(youtube, "_get", slow_get)
    monkeypatch.setattr(streams, "check_dns", lambda host: None)
    out = {}
    video = threading.Thread(target=lambda: out.setdefault("video", streams._youtube("@examplechannel", 480)))
    video.start()
    out["chat"] = asyncio.run(youtube.find_live_video("https://www.youtube.com/@examplechannel"))
    video.join()
    assert len(calls) == 1
    assert out["chat"] == ("abcdefghijk", "Example stream") and out["video"].url.endswith("watch?v=abcdefghijk")


def test_missing_youtube_channel(monkeypatch):
    from multichat.chat import youtube

    monkeypatch.setattr(youtube, "_get", lambda url, **kw: types.SimpleNamespace(status_code=404, text="", url=url))
    monkeypatch.setattr(streams, "check_dns", lambda host: None)
    with pytest.raises(RuntimeError, match="YouTube channel not found"):
        streams._youtube("@examplechannel", 480)
    with pytest.raises(ValueError):
        youtube.live_now("https://www.youtube.com/@examplechannel")  # errors are not kept


def test_kick_channel_info_is_shared(monkeypatch):
    from multichat.chat import kick

    calls = []
    import threading
    import time as _time

    def slow(url, slug):
        calls.append(url)
        _time.sleep(0.2)
        return {"chatroom": {"id": 5}, "user_id": 7}

    monkeypatch.setattr(kick, "_kick_get", slow)
    both = [threading.Thread(target=kick.fetch_kick_json, args=("examplekick",)) for _ in range(2)]
    for t in both:
        t.start()
    assert kick._fetch_kick_channel("Examplekick") == (5, "7")
    for t in both:
        t.join()
    assert len(calls) == 1  # three callers at once, one request


def test_discarded_stream_result_is_closed_in_the_background():
    """A result for a channel that was already changed again: Streamlink's close waits for
    its threads – that must not happen on the UI thread."""
    import threading
    import time as _time

    from multichat.video.tiles import VideoTile

    closed = threading.Event()

    def slow_close():
        _time.sleep(0.5)
        closed.set()

    tile = types.SimpleNamespace(gen=2, platform="twitch")
    t0 = _time.perf_counter()
    VideoTile._resolved(tile, 1, ("ok", streams.Stream("fdclose://-1", closer=slow_close)))
    assert _time.perf_counter() - t0 < 0.1 and closed.wait(2)


def test_only_https_streams_reach_mpv(monkeypatch):
    """mpv opens any address it is given – file paths or other protocols from a platform
    response are refused."""
    from multichat.chat import kick

    monkeypatch.setattr(kick, "_kick_get", lambda url, slug: {"livestream": {"session_title": "t"},
                                                              "playback_url": "file:///etc/passwd"})
    with pytest.raises(RuntimeError, match="unexpected stream address"):
        streams.resolve("kick", "examplekick", "best")

    monkeypatch.setattr(streams, "_youtube", lambda value, max_h: streams.Stream("http://example.invalid/v.m3u8"))
    with pytest.raises(RuntimeError, match="unexpected stream address"):
        streams.resolve("youtube", "@examplechannel", "best")
    monkeypatch.setattr(streams, "_youtube", lambda value, max_h: streams.Stream(
        "https://example.invalid/v", audio_url="rtmp://example.invalid/a"))
    with pytest.raises(RuntimeError, match="unexpected stream address"):
        streams.resolve("youtube", "@examplechannel", "best")
    # our own Streamlink pipe and ordinary https pass
    monkeypatch.setattr(streams, "_twitch", lambda channel, max_h: streams.Stream("fdclose://7", pipe_fd=7))
    assert streams.resolve("twitch", "examplechan", "best").url == "fdclose://7"
    monkeypatch.setattr(streams, "_youtube", lambda value, max_h: streams.Stream("https://www.youtube.com/watch?v=abcdefghijk"))
    assert streams.resolve("youtube", "@examplechannel", "best").url.startswith("https://")
