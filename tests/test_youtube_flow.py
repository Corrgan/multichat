# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import sys
import types

from multichat.chat import youtube

YT = "https://www.youtube.com/"


def test_offline_live_ended_flow(monkeypatch):
    """offline → live → stream ends → keeps looking for the next one"""
    seq = [None, ("abcdefghijk", "Test stream"), None]

    async def fake_find(url):
        assert url == YT + "@examplechannel"
        return seq.pop(0) if seq else None

    class FakeChat:
        def __init__(self):
            self.n = 0

        def is_alive(self):
            self.n += 1
            return self.n <= 1

        def get(self):
            author = types.SimpleNamespace(name="Viewer", channelId="UC1")
            item = types.SimpleNamespace(author=author, message="hello", messageEx=["hello"], id="m1", type="textMessage")
            return types.SimpleNamespace(sync_items=lambda: [item])

    monkeypatch.setenv("MULTICHAT_YT_CHAT", "pytchat")  # this test covers the pytchat path
    monkeypatch.setattr(youtube, "find_live_video", fake_find)
    monkeypatch.setattr(youtube, "YT_POLL", 0.05)
    monkeypatch.setitem(sys.modules, "pytchat", types.SimpleNamespace(create=lambda **kw: FakeChat()))
    statuses, msgs = [], []
    real_sleep = asyncio.sleep

    async def fast_sleep(s):
        await real_sleep(min(s, 0.05))

    async def run():
        monkeypatch.setattr(youtube.asyncio, "sleep", fast_sleep)
        task = asyncio.ensure_future(
            youtube.run_youtube("@examplechannel", msgs.append, lambda p, t, **kw: statuses.append(t.format(**kw)))
        )
        await real_sleep(0.6)
        task.cancel()

    asyncio.run(run())
    assert any(s.startswith("not live") for s in statuses)
    assert "live: Test stream" in statuses
    assert "stream ended – waiting for the next one" in statuses
    assert msgs[0].channel == "abcdefghijk" and msgs[0].user == "Viewer"
