# SPDX-License-Identifier: GPL-3.0-or-later
"""A request started by one account must never be retried with another account's token."""
import asyncio
import json
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest
from PyQt6.QtCore import QUrl

from multichat.accounts import oauth
from multichat.accounts.store import SecretStore
from multichat.chat.models import ChatMessage
from test_moderation_context import app, window, wait_for


def prepare_kick_login(w, monkeypatch, token_request=None):
    """Replace browser/platform boundaries, retain the real OAuth callback and login logic."""
    acc = w.accounts["kick"]
    w.apps.replace("kick", {"client_id": "SYNTHETIC_ID", "client_secret": "SYNTHETIC_SECRET"})

    def browser(url):
        state = parse_qs(urlparse(url).query)["state"][0]
        with urlopen(f"{oauth.REDIRECT_URI}?code=TEST_CODE&state={state}", timeout=3) as response:
            assert response.status == 200
        return True

    async def tokens(data):
        assert data["grant_type"] == "authorization_code"
        return {"access_token": "AT_B", "refresh_token": "RT_B", "expires_at": time.time() + 3600}

    async def whoami():
        return {"user_id": "B", "user_name": "B"}

    monkeypatch.setattr(oauth.webbrowser, "open", browser)
    monkeypatch.setattr(acc, "_token_request", token_request or tokens)
    monkeypatch.setattr(acc, "whoami", whoami)
    return acc


def install_kick_a(w):
    acc = w.accounts["kick"]
    acc.store.replace("kick", {"access_token": "AT_A", "refresh_token": "RT_A",
                                "expires_at": time.time() + 3600, "user_id": "A", "user_name": "A"})
    w.refresh_accounts()
    return acc


def test_kick_timeout_401_after_logout_and_ui_login_cannot_retry_as_new_account(window, app, monkeypatch):
    w = window
    w.panels[0].rows["kick"].edit.setText("alpha")
    acc = install_kick_a(w)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Reply:
        def __init__(self, method, url, headers, **kwargs):
            self.url, self.kwargs = url, kwargs
            self.authorization = headers["Authorization"]
            self.status = 200

        async def __aenter__(self):
            calls.append((self.url, self.authorization, self.kwargs))
            if self.url.endswith("/moderation/bans") and self.authorization == "Bearer AT_A":
                entered.set()
                await asyncio.to_thread(release.wait, 5)
                self.status = 401
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            if self.url.endswith("/channels"):
                return json.dumps({"data": [{"broadcaster_user_id": 555}]})
            if self.status == 401:
                return json.dumps({"message": "old account token rejected"})
            return ""

    class Session:
        def request(self, method, url, **kwargs):
            return Reply(method, url, **kwargs)

    monkeypatch.setattr(acc, "_session", lambda: Session())
    target = ChatMessage("kick", "alpha", "Target", "message", user_id="88", msg_id="target-message", source="kick:alpha")
    w.add_message(target)
    w._flush_messages()
    w.on_anchor(QUrl(f"msg:{target.uid}"))
    w.panels[0].action_bar.timeout.menu().actions()[0].trigger()
    try:
        assert entered.wait(3)
        pending = next(f for name, f in w.action_futures if name == "KickAccount.timeout")
        w.logout("kick")
        prepare_kick_login(w, monkeypatch)
        w._fill_accounts_menu()
        login = next(a for a in w.accounts_menu.actions() if a.text() == "Log in to Kick …")
        login.trigger()
        wait_for(app, lambda: acc.user_id == "B" and w.statusBar().currentMessage().startswith("Logged in to Kick"))
    finally:
        release.set()
    with pytest.raises(oauth.ApiError):
        pending.result(3)
    app.processEvents()
    moderation = [(auth, kwargs) for url, auth, kwargs in calls if url.endswith("/moderation/bans")]
    assert moderation == [("Bearer AT_A", {"json": {"broadcaster_user_id": 555, "user_id": 88, "duration": 1}})]
    assert acc.user_id == "B" and acc.tokens["access_token"] == "AT_B"
    assert not target.deleted  # no local success callback for the rejected old-account action


@pytest.mark.parametrize("old_error", [False, True])
def test_internal_refresh_started_during_reauthentication_cannot_replace_or_delete_committed_login(
    window, monkeypatch, old_error
):
    """Defensive internal reauthentication: the normal logged-in menu only offers logout.

    This covers a refresh that snapshots A *after* B's login has started, so bumping
    the generation only at login start would still allow a late A failure to delete B.
    """
    w = window
    acc = install_kick_a(w)
    login_entered, login_release = threading.Event(), threading.Event()
    refresh_entered, refresh_release = threading.Event(), threading.Event()

    async def tokens(data):
        if data["grant_type"] == "authorization_code":
            login_entered.set()
            await asyncio.to_thread(login_release.wait, 5)
            return {"access_token": "AT_B", "refresh_token": "RT_B", "expires_at": time.time() + 3600}
        assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "RT_A"
        refresh_entered.set()
        await asyncio.to_thread(refresh_release.wait, 5)
        if old_error:
            raise oauth.ApiError("old refresh token rejected", status=400)
        return {"access_token": "REFRESHED_A", "refresh_token": "REFRESHED_RT_A", "expires_at": time.time() + 3600}

    prepare_kick_login(w, monkeypatch, token_request=tokens)
    login = w.run(acc.login(), quiet=True)
    try:
        assert login_entered.wait(3)
        refresh = w.run(acc._refresh("AT_A"), quiet=True)
        assert refresh_entered.wait(3)
        login_release.set()
        login.result(3)
        assert acc.user_id == "B" and acc.tokens["access_token"] == "AT_B"
    finally:
        login_release.set()
        refresh_release.set()
    with pytest.raises(oauth.ApiError):
        refresh.result(3)
    assert acc.user_id == "B" and acc.tokens["access_token"] == "AT_B"
    fresh = SecretStore("tokens", acc.store.backend, acc.store.files)
    assert fresh.get("kick")["access_token"] == "AT_B"
