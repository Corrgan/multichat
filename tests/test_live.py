# SPDX-License-Identifier: GPL-3.0-or-later
"""Live indicator: which entered channels are live, shown on the rows and the group tabs."""
import asyncio

from PyQt6.QtTest import QTest

from multichat.chat import live
from multichat.ui import live_watch
from test_gui import app, window  # noqa: F401 – shared fixtures
from test_window_lifecycle import spin


class FakeTwitch:
    def __init__(self, logged_in=True, live=(), fail=False):
        self.logged_in, self._live, self.fail, self.asked = logged_in, set(live), fail, []

    async def live_channels(self, logins):
        self.asked.append(list(logins))
        if self.fail:
            raise RuntimeError("Helix down")
        return self._live


def test_check_asks_each_platform_its_own_way(monkeypatch):
    kick = {"examplekick": {"livestream": {"id": 1}}, "quietkick": {"livestream": None}}

    def fetch_kick_json(slug):
        if slug == "nosuchkick":
            raise ValueError("Kick channel not found")
        if slug == "blockedkick":
            raise RuntimeError("Kick API HTTP 403 (blocked by Cloudflare)")
        return kick[slug]

    monkeypatch.setattr(live, "fetch_kick_json", fetch_kick_json)
    monkeypatch.setattr(live, "live_now", lambda url: ("vid12345678", "title") if url.endswith("@examplelive") else None)
    targets = [("twitch:examplechan", "twitch", "examplechan"), ("twitch:quietchan", "twitch", "quietchan"),
               ("kick:examplekick", "kick", "examplekick"), ("kick:quietkick", "kick", "quietkick"),
               ("kick:nosuchkick", "kick", "nosuchkick"), ("kick:blockedkick", "kick", "blockedkick"),
               ("youtube:@examplelive", "youtube", "@examplelive"), ("youtube:@quiet", "youtube", "@quiet"),
               ("youtube:dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ")]
    twitch = FakeTwitch(live={"examplechan"})
    assert asyncio.run(live.check(targets, twitch)) == {
        "twitch:examplechan": True, "twitch:quietchan": False,
        "kick:examplekick": True, "kick:quietkick": False,
        "kick:nosuchkick": False,  # no such channel: not live
        "kick:blockedkick": None,  # lookup failed: unknown
        "youtube:@examplelive": True, "youtube:@quiet": False,
        "youtube:dQw4w9WgXcQ": None,  # a single video has no "channel is live"
    }
    assert twitch.asked == [["examplechan", "quietchan"]]  # one request for all Twitch channels
    # without the Twitch login (or when Helix fails) Twitch stays unknown, the others still work
    for account in (FakeTwitch(logged_in=False), FakeTwitch(fail=True), None):
        result = asyncio.run(live.check(targets[:3], account))
        assert result == {"twitch:examplechan": None, "twitch:quietchan": None, "kick:examplekick": True}


def test_twitch_live_channels_is_one_request_per_hundred(tmp_path):
    from multichat.accounts import FileBackend, SecretStore, TwitchAccount

    store = SecretStore("tokens", FileBackend(tmp_path))
    acc = TwitchAccount(store, lambda p: ("CID", "SECRET"), lambda: None)
    store.replace("twitch", {"access_token": "x", "expires_at": 9e12, "user_id": "42", "user_name": "Me"})
    calls = []

    async def fake_request(method, url, params=None, **kw):
        assert method == "GET" and url.endswith("/helix/streams")
        logins = [v for k, v in params if k == "user_login"]
        calls.append(logins)
        return {"data": [{"user_login": name, "type": "live"} for name in logins if name.endswith("7")]
                + [{"user_login": "rerun7x", "type": ""}]}

    acc.request = fake_request
    names = [f"Chan{i}" for i in range(150)] + ["chan7"]  # upper/lower case and a duplicate
    result = asyncio.run(acc.live_channels(names))
    assert [len(c) for c in calls] == [100, 50] and all(n == n.lower() for c in calls for n in c)
    assert result == {f"chan{i}" for i in range(150) if str(i).endswith("7")}


def test_live_channels_show_a_dot_on_the_row_and_the_tab(window, app, monkeypatch):  # noqa: F811
    w = window
    watch = w.live_watch
    assert not watch._tick.isActive()  # tests never look channels up on their own
    answers = {"twitch:examplechan": True, "kick:examplekick": False}
    asked = []

    async def check(targets, twitch):
        asked.append(sorted(key for key, _p, _v in targets))
        return {key: answers.get(key) for key, _p, _v in targets}

    monkeypatch.setattr(live_watch.live, "check", check)
    panel = w.panels[0]
    for n in range(1, len("examplechan") + 1):
        panel.rows["twitch"].edit.setText("examplechan"[:n])  # typing asks nothing by itself
    panel.rows["kick"].edit.setText("examplekick")
    assert asked == [] and not watch.live

    watch.check()
    assert spin(app, lambda: watch.live.get("twitch:examplechan") is True)
    assert spin(app, lambda: len(asked) == 2 and not watch._busy)
    assert sorted(asked) == [["kick:examplekick"], ["twitch:examplechan"]]  # each platform on its own
    assert panel.rows["twitch"].live.text() == "●" and panel.rows["twitch"].live.toolTip()
    assert panel.rows["kick"].live.text() == ""
    assert not w.tabs.tabIcon(0).isNull() and "examplechan" in w.tabs.tabToolTip(0)

    watch.check()  # nothing is due yet
    QTest.qWait(20)
    assert len(asked) == 2

    # the stream ends
    answers["twitch:examplechan"] = False
    watch._due.clear()
    watch.check()
    assert spin(app, lambda: watch.live.get("twitch:examplechan") is False)
    assert panel.rows["twitch"].live.text() == "" and w.tabs.tabIcon(0).isNull() and w.tabs.tabToolTip(0) == ""

    # live again, then the lookups fail: the dot stays for one failure, not for good
    answers["twitch:examplechan"] = True
    watch._due.clear()
    watch.check()
    assert spin(app, lambda: watch.live.get("twitch:examplechan") is True)
    answers["twitch:examplechan"] = None
    for expected in (True, None):
        watch._due.clear()
        before = len(asked)
        watch.check()
        assert spin(app, lambda: len(asked) > before and not watch._busy)
        assert watch.live.get("twitch:examplechan") is expected
    assert w.tabs.tabIcon(0).isNull()
    # … and a channel that keeps failing is asked less and less often
    import time
    assert watch._due["twitch:examplechan"] - time.monotonic() > live_watch.CHECK_SECONDS["twitch"] * 2

    # another channel in the field: the old state is gone at once
    answers["twitch:examplechan"] = True
    watch._due.clear()
    watch._errors.clear()
    watch.check()
    assert spin(app, lambda: watch.live.get("twitch:examplechan") is True)
    panel.rows["twitch"].edit.setText("otherchan")
    assert "twitch:examplechan" not in watch.live
    assert panel.rows["twitch"].live.text() == "" and w.tabs.tabIcon(0).isNull()


def test_a_group_tab_shows_live_when_any_of_its_channels_is(window, app, monkeypatch):  # noqa: F811
    w = window
    w.add_group()
    w.panels[0].rows["twitch"].edit.setText("quietchan")
    w.panels[1].rows["kick"].edit.setText("examplekick")
    w.panels[1].rows["youtube"].edit.setText("@examplelive")

    async def check(targets, twitch):
        return {key: key != "twitch:quietchan" for key, _p, _v in targets}

    monkeypatch.setattr(live_watch.live, "check", check)
    w.live_watch.check()
    assert spin(app, lambda: len(w.live_watch.live) == 3)
    assert w.tabs.tabIcon(0).isNull() and not w.tabs.tabIcon(1).isNull()
    tip = w.tabs.tabToolTip(1)
    assert "Kick · examplekick" in tip and "YouTube · @examplelive" in tip


def test_twitch_is_asked_more_often_and_does_not_wait_for_the_others(window, app, monkeypatch):  # noqa: F811
    """A streamer who goes live should show up soon: Twitch (one request for all channels)
    is asked every 30 s, and its answer does not wait for slow Kick or YouTube lookups."""
    import time

    assert live_watch.CHECK_SECONDS["twitch"] <= 30 < live_watch.CHECK_SECONDS["kick"]
    w = window
    watch = w.live_watch
    w.panels[0].rows["twitch"].edit.setText("examplechan")
    w.panels[0].rows["kick"].edit.setText("examplekick")
    release = asyncio.Event()
    asked = []

    async def check(targets, twitch):
        keys = [key for key, _p, _v in targets]
        asked.append(keys)
        if keys == ["kick:examplekick"]:
            await asyncio.wait_for(release.wait(), 5)  # Kick hangs
        return {key: True for key in keys}

    monkeypatch.setattr(live_watch.live, "check", check)
    watch.check()
    assert spin(app, lambda: watch.live.get("twitch:examplechan") is True)  # although Kick has not answered
    assert "kick" in watch._busy and "twitch" not in watch._busy and "kick:examplekick" not in watch.live
    # the next rounds ask Twitch again and leave the hanging Kick lookup alone …
    watch._due.clear()
    watch.check()
    assert spin(app, lambda: asked.count(["twitch:examplechan"]) == 2)
    assert asked.count(["kick:examplekick"]) == 1
    # … until it has been silent for too long: then it no longer blocks a new attempt
    watch._busy["kick"] = time.monotonic() - live_watch.BUSY_SECONDS - 1
    watch.check()
    assert spin(app, lambda: asked.count(["kick:examplekick"]) == 2)
    w.loop.call_soon_threadsafe(release.set)
    assert spin(app, lambda: watch.live.get("kick:examplekick") is True and not watch._busy)


def test_a_playing_video_shows_live_at_once(window, app, monkeypatch):  # noqa: F811
    """The platform's "is live" answer lags behind the start of a stream. A video tile that
    already plays is proof enough – and a late "not live" must not take the dot away again."""
    import time
    from types import SimpleNamespace

    w = window
    watch = w.live_watch
    panel = w.panels[0]
    panel.rows["twitch"].edit.setText("examplechan")

    async def check(targets, twitch):
        return {key: False for key, _p, _v in targets}  # Twitch has not noticed yet

    monkeypatch.setattr(live_watch.live, "check", check)
    # what the watch reads from the video tiles (a stand-in: no player in the tests)
    tile = SimpleNamespace(key="twitch:examplechan", state="playing")
    other = SimpleNamespace(key="kick:examplekick", state="offline")
    fake = SimpleNamespace(panels=[SimpleNamespace(video=SimpleNamespace(tiles={"twitch": tile, "kick": other})),
                                   SimpleNamespace(video=None)])
    assert live_watch.LiveWatch._playing(SimpleNamespace(win=fake)) == {"twitch:examplechan"}
    monkeypatch.setattr(watch, "_playing", lambda: {t.key for t in (tile, other) if t.state == "playing"})
    watch.check()
    assert watch.live.get("twitch:examplechan") is True and not w.tabs.tabIcon(0).isNull()
    assert spin(app, lambda: not watch._busy)
    assert watch.live.get("twitch:examplechan") is True  # the lagging "not live" is ignored
    # the video stops; once nothing vouches for the stream any more the lookups decide again
    tile.state = "ended"
    watch._hold["twitch:examplechan"] = time.monotonic() - 1
    watch._due.clear()
    watch.check()
    assert spin(app, lambda: watch.live.get("twitch:examplechan") is False)
    assert w.tabs.tabIcon(0).isNull()
