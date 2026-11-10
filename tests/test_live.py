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
    assert asked == [["kick:examplekick", "twitch:examplechan"]]
    assert panel.rows["twitch"].live.text() == "●" and panel.rows["twitch"].live.toolTip()
    assert panel.rows["kick"].live.text() == ""
    assert not w.tabs.tabIcon(0).isNull() and "examplechan" in w.tabs.tabToolTip(0)

    watch.check()  # nothing is due yet
    QTest.qWait(20)
    assert len(asked) == 1

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
    assert watch._due["twitch:examplechan"] - time.monotonic() > live_watch.CHECK_SECONDS * 2

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
