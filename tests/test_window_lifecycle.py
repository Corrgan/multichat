# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Qt/account/reader lifecycles, with only external boundaries replaced."""
import asyncio
import threading
import time
from concurrent.futures import Future
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent

from multichat.accounts import oauth
from multichat.accounts.store import FileBackend, KeyringBackend, SecretStore
from multichat.chat.models import ChatMessage, Deletion
from test_gui import app, window  # shared real MainWindow fixture; no live readers


def spin(app, predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def install_account(w, user="A", expires_at=9e12):
    acc = w.accounts["twitch"]
    acc.store.replace("twitch", {"access_token": f"AT_{user}", "refresh_token": f"RT_{user}",
                                 "user_id": user, "user_name": user, "expires_at": expires_at})
    return acc


def prepare_login(w, monkeypatch, user="B"):
    acc = w.accounts["twitch"]
    w.apps.replace("twitch", {"client_id": "SYNTHETIC", "client_secret": "SYNTHETIC"})
    def browser(url):
        state = parse_qs(urlparse(url).query)["state"][0]
        with urlopen(f"{oauth.REDIRECT_URI}?code=TEST&state={state}", timeout=3) as response:
            assert response.status == 200
        return True
    async def tokens(data):
        return {"access_token": f"AT_{user}", "refresh_token": f"RT_{user}", "expires_at": 9e12}
    async def whoami():
        return {"user_id": user, "user_name": user}
    monkeypatch.setattr(oauth.webbrowser, "open", browser)
    monkeypatch.setattr(acc, "_token_request", tokens)
    monkeypatch.setattr(acc, "whoami", whoami)


def test_reader_burst_has_one_qt_wakeup_and_preserves_quiet_source(window, app):
    w = window
    w.panels[0].rows["twitch"].edit.setText("quiet")
    w.add_group().rows["twitch"].edit.setText("busy")
    wakeups = []
    w.bridge.incoming.connect(lambda: wakeups.append(True))
    async def emit():
        for channel, count in (("quiet", 5), ("busy", 1500)):
            for i in range(count):
                w._emit(ChatMessage("twitch", channel, "User", str(i)), f"twitch:{channel}")
    asyncio.run_coroutine_threadsafe(emit(), w.loop).result(3)
    assert len(w._incoming) == 1005 and w._incoming.dropped == 500
    QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
    assert wakeups == [True]
    w._flush_messages()
    w._flush_messages()
    assert len([m for m in w.messages.values() if m.channel == "quiet"]) == 5
    assert w._msg_count == 1005 and w._perf_stats()["dropped events"] == 500
    assert "500" in w.statusBar().currentMessage()


def test_reader_deletion_cannot_overtake_queued_message(window):
    w = window
    old = ChatMessage("twitch", "chan", "User", "before", user_id="user")
    new = ChatMessage("twitch", "chan", "User", "after", user_id="user")
    for event in (old, Deletion("twitch", user_id="user"), new):
        w._emit(event, "twitch:chan")
    w._flush_messages()
    assert old.deleted and not new.deleted


def test_logout_and_refresh_commit_are_one_transaction(window, monkeypatch, tmp_path):
    w = window
    acc = install_account(w, expires_at=0)
    reached, release = threading.Event(), threading.Event()
    original_update = acc.store.update
    def held_update(platform, **values):
        if values.get("access_token") == "NEW":
            reached.set()
            assert release.wait(3)
        return original_update(platform, **values)
    async def token(data):
        return {"access_token": "NEW", "refresh_token": "NEW_RT", "expires_at": 9e12}
    monkeypatch.setattr(acc.store, "update", held_update)
    monkeypatch.setattr(acc, "_token_request", token)
    future = w.run(acc._refresh("AT_A"), quiet=True)
    assert reached.wait(3)
    # The worker pauses after its check at the real store boundary. A separate
    # helper releases it even when Qt correctly waits for that atomic commit.
    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        w.logout("twitch")  # real UI thread; must leave the subsequent state empty
        future.result(3)
        assert not acc.logged_in
        assert SecretStore("tokens", acc.store.backend, acc.store.files).get("twitch") == {}
    finally:
        release.set()
        timer.join()


def test_strict_keyring_logout_reports_persistence_failure(window, tmp_path):
    class Wallet:
        broken = False
        data = {}
        def get_password(self, service, key):
            return self.data.get((service, key))
        def set_password(self, service, key, value):
            if self.broken:
                raise RuntimeError("wallet locked")
            self.data[service, key] = value
    w = window
    wallet = Wallet()
    backend = KeyringBackend(wallet)
    backend.strict = True
    files = FileBackend(tmp_path)
    w.accounts["twitch"].store = SecretStore("tokens", backend, files)
    acc = install_account(w)
    wallet.broken = True
    w.logout("twitch")
    assert not acc.logged_in and not files.path("tokens").exists()
    assert "could not save" in w.statusBar().currentMessage()
    wallet.broken = False
    assert SecretStore("tokens", backend, files).get("twitch")["access_token"] == "AT_A"


@pytest.mark.parametrize("error", [False, True])
def test_old_emote_response_cannot_replace_new_login_request(window, app, monkeypatch, error):
    w = window
    acc = install_account(w)
    entered, release = threading.Event(), threading.Event()
    requests = []
    async def usable(channel):
        who = acc.user_id
        requests.append(who)
        if who == "A":
            entered.set()
            await asyncio.to_thread(release.wait, 3)
            if error:
                raise oauth.ApiError("old failure")
        return [(f"{who}_emote", f"https://example.invalid/{who}")], True
    monkeypatch.setattr(acc, "usable_emotes", usable)
    w.panels[0].rows["twitch"].edit.setText("chan")
    w.readers["twitch:chan"] = Future()  # emotes are loaded for connected channels
    w.refresh_accounts()
    assert entered.wait(3)
    try:
        w.logout("twitch")
        prepare_login(w, monkeypatch)
        w.login("twitch")
        assert spin(app, lambda: "B_emote" in w._native_emotes.get("twitch:chan", {}))
        release.set()
        assert spin(app, lambda: not w._native_loading)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), w.loop).result(2)
        app.processEvents()
        assert requests == ["A", "B"]
        assert set(w._send_emotes()) == {"B_emote"}
    finally:
        release.set()


def test_typing_a_channel_name_does_not_look_up_emotes_for_every_prefix(window, app, monkeypatch):
    """The send options follow the channel field while it is typed. Loading your Twitch
    emotes for each prefix cost about a hundred requests per name and logged warnings
    for the prefixes that are no channels."""
    w = window
    acc = install_account(w)
    asked = []

    async def usable(channel):
        asked.append(channel)
        return [("Kappa", "https://example.invalid/kappa")], True

    monkeypatch.setattr(acc, "usable_emotes", usable)
    w.refresh_accounts()
    edit = w.panels[0].rows["twitch"].edit
    for n in range(1, len("examplechan") + 1):
        edit.setText("examplechan"[:n])
    app.processEvents()
    assert asked == [] and not w._native_loading
    w.readers["twitch:examplechan"] = Future()  # connected
    w._after_connection_change()
    assert spin(app, lambda: "Kappa" in w._native_emotes.get("twitch:examplechan", {}))
    w._after_connection_change()  # once per channel
    assert asked == ["examplechan"]
