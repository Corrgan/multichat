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
        self.logged_in, self.fail, self.asked = logged_in, fail, []
        self._live = live if isinstance(live, dict) else {name: "" for name in live}

    async def live_streams(self, logins):
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


# ---- how long the stream has been running ------------------------------------ #
def test_start_times_are_read_from_what_the_platforms_send():
    import time
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(microsecond=0)
    two_hours = now - timedelta(hours=2)
    for text in (two_hours.strftime("%Y-%m-%dT%H:%M:%SZ"),  # Twitch
                 two_hours.strftime("%Y-%m-%d %H:%M:%S"),  # Kick: UTC without saying so
                 two_hours.isoformat(),  # YouTube: with offset
                 two_hours.astimezone(timezone(timedelta(hours=1))).isoformat()):  # same moment, local offset
        assert abs(live.started(text) - (time.time() - 7200)) < 5, text
    for text in ("", None, 123, "yesterday", "2019-01-01T00:00:00Z",  # weeks ago: not a running stream
                 (now + timedelta(days=1)).isoformat()):  # in the future
        assert live.started(text) is None, text


def test_check_reports_since_when_a_channel_is_live(monkeypatch):
    import time
    from datetime import datetime, timedelta, timezone

    def ago(minutes, fmt="%Y-%m-%dT%H:%M:%SZ"):
        return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(fmt)

    monkeypatch.setattr(live, "fetch_kick_json", lambda slug: {
        "examplekick": {"livestream": {"id": 1, "created_at": ago(30, "%Y-%m-%d %H:%M:%S")}},
        "nostartkick": {"livestream": {"id": 2}}, "quietkick": {"livestream": None}}[slug])
    monkeypatch.setattr(live, "live_now", lambda url: ("vid12345678", "title") if "examplelive" in url else None)
    monkeypatch.setitem(live.LIVE_STARTED, "https://www.youtube.com/@examplelive", ago(90))
    monkeypatch.setitem(live.LIVE_STARTED, "https://www.youtube.com/@quiet", ago(5))  # stale: it is not live
    targets = [("twitch:examplechan", "twitch", "examplechan"), ("twitch:nostart", "twitch", "nostart"),
               ("twitch:quietchan", "twitch", "quietchan"), ("kick:examplekick", "kick", "examplekick"),
               ("kick:nostartkick", "kick", "nostartkick"), ("kick:quietkick", "kick", "quietkick"),
               ("youtube:@examplelive", "youtube", "@examplelive"), ("youtube:@quiet", "youtube", "@quiet")]
    result = asyncio.run(live.check(targets, FakeTwitch(live={"examplechan": ago(125), "nostart": ""})))
    assert [key for key, state in result.items() if state] == [
        "twitch:examplechan", "twitch:nostart", "kick:examplekick", "kick:nostartkick", "youtube:@examplelive"]
    minutes = {key: round((time.time() - at) / 60) for key, at in result.since.items()}
    assert minutes == {"twitch:examplechan": 125, "kick:examplekick": 30, "youtube:@examplelive": 90}


def test_twitch_live_streams_carry_the_start(tmp_path):
    from multichat.accounts import FileBackend, SecretStore, TwitchAccount

    store = SecretStore("tokens", FileBackend(tmp_path))
    acc = TwitchAccount(store, lambda p: ("CID", "SECRET"), lambda: None)
    store.replace("twitch", {"access_token": "x", "expires_at": 9e12, "user_id": "42", "user_name": "Me"})

    async def fake_request(method, url, params=None, **kw):
        return {"data": [{"user_login": "ExampleChan", "type": "live", "started_at": "2026-11-10T21:04:11Z"},
                         {"user_login": "nostart", "type": "live"}, "junk", {"type": "live"}]}

    acc.request = fake_request
    assert asyncio.run(acc.live_streams(["examplechan", "nostart", "quiet"])) == {
        "examplechan": "2026-11-10T21:04:11Z", "nostart": ""}
    assert asyncio.run(acc.live_channels(["examplechan", "nostart"])) == {"examplechan", "nostart"}


def test_tab_shows_how_long_the_streamer_has_been_live(window, app, monkeypatch):  # noqa: F811
    """One channel's answer is enough for the group; the tooltips say it per channel."""
    import time

    from multichat.ui.groups import uptime_text

    assert [uptime_text(s) for s in (0, 59, 7 * 60, 83 * 60, 12 * 3600 + 5 * 60)] == ["0:00", "0:00", "0:07", "1:23", "12:05"]
    w = window
    watch = w.live_watch
    panel = w.panels[0]
    panel.name.setText("Main")
    panel.rows["twitch"].edit.setText("examplechan")
    panel.rows["kick"].edit.setText("examplekick")
    panel.rows["youtube"].edit.setText("@examplelive")
    now = time.time()

    async def check(targets, twitch):
        result = live.Result()
        result.update({key: True for key, _p, _v in targets})
        result.since.update({"kick:examplekick": now - 30 * 60, "youtube:@examplelive": now - 83 * 60})
        return result

    monkeypatch.setattr(live_watch.live, "check", check)
    watch.check()
    assert spin(app, lambda: len(watch.live) == 3)
    # Twitch is live but did not say since when: the next channel that knows it is used
    assert w.tabs.tabText(0) == "Main · 0:30"
    tip = w.tabs.tabToolTip(0)
    assert "Twitch · examplechan," in tip and "Kick · examplekick (0:30 h)" in tip and "(1:23 h)" in tip
    assert "0:30" in panel.rows["kick"].live.toolTip() and panel.rows["twitch"].live.toolTip() == "Live now"
    # a minute later the display has counted on (the watch's clock refreshes it)
    watch.since["kick:examplekick"] -= 60
    watch._clock.timeout.emit()
    assert w.tabs.tabText(0) == "Main · 0:31"
    # unread messages still show next to it
    panel.unread = 3
    w._update_titles()
    assert w.tabs.tabText(0) == "Main · 0:31 (3)"
    panel.unread = 0
    # offline again: no time, no dot
    async def offline(targets, twitch):
        result = live.Result()
        result.update({key: False for key, _p, _v in targets})
        return result

    monkeypatch.setattr(live_watch.live, "check", offline)
    watch._due.clear()
    watch.check()
    assert spin(app, lambda: not any(watch.live.values()) and not watch._busy)  # each platform answers on its own
    assert w.tabs.tabText(0) == "Main" and not watch.since and w.tabs.tabIcon(0).isNull()


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
