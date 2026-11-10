# SPDX-License-Identifier: GPL-3.0-or-later
"""Real widget actions keep their channel; late account replies cannot change permissions."""
import asyncio
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication, QMessageBox

from multichat.accounts import oauth
from multichat.chat.models import ChatMessage
from multichat.core import i18n
from multichat.core.config import clean_groups, load_config
from multichat.ui import main_window


def wait_for(app, predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "expected Qt/worker result did not arrive"


def deliver_result(app, future):
    # MainWindow registers its Bridge delivery callback first; wait for it, then drain Qt.
    delivered = threading.Event()
    future.add_done_callback(lambda _f: delivered.set())
    future.result(3)
    assert delivered.wait(3)
    app.processEvents()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    monkeypatch.setattr(main_window, "CONF_DIR", tmp_path)
    monkeypatch.setattr(main_window, "load_mpv", lambda: None)  # no decoder needed for moderation widgets
    i18n.set_language("en")
    config = load_config()
    config.update(groups=clean_groups(None, {}), active_group=0, side_by_side=False,
                  extra_emotes=False, check_updates=False)
    config["video"]["enabled"] = False
    config["overlay"]["enabled"] = False
    w = main_window.MainWindow(config)
    w.show()
    app.processEvents()
    w.api_calls, w.action_futures = [], []
    original_run = w.run

    def record_run(coro, **kwargs):
        name = coro.__qualname__
        future = original_run(coro, **kwargs)
        w.action_futures.append((name, future))
        return future

    monkeypatch.setattr(w, "run", record_run)

    async def api(method, url, **kwargs):
        w.api_calls.append((method, url, kwargs))
        if url.endswith("/users"):
            channel = kwargs.get("params", {}).get("login", "target")
            return {"data": [{"id": f"bid_{channel}", "created_at": "2020-01-01T00:00:00Z"}]}
        if url.endswith("/moderation/channels"):
            return {"data": [{"broadcaster_login": "alpha"}, {"broadcaster_login": "beta"}]}
        if url.endswith("/channels/followers"):
            year = "2022" if kwargs["params"]["broadcaster_id"] == "bid_alpha" else "2024"
            return {"data": [{"followed_at": f"{year}-01-01T00:00:00Z"}]}
        if "emotes" in url:
            return {"data": []}
        return None

    monkeypatch.setattr(w.accounts["twitch"], "request", api)
    yield w
    w.close()
    app.processEvents()
    i18n.set_language("en")


def login_a(w):
    w.accounts["twitch"].store.replace("twitch", {
        "access_token": "AT_A", "refresh_token": "RT_A", "expires_at": time.time() + 3600,
        "user_id": "A", "user_name": "A",
    })
    w.refresh_accounts()


def login_b_via_menu(w, app, monkeypatch):
    """Actual logged-out menu -> OAuth browser callback; only external boundaries replaced."""
    acc = w.accounts["twitch"]
    w.apps.replace("twitch", {"client_id": "FAKE_ID", "client_secret": "FAKE_SECRET"})

    def browser(url):
        import urllib.request

        state = parse_qs(urlparse(url).query)["state"][0]
        with urllib.request.urlopen(f"{oauth.REDIRECT_URI}?code=TEST_CODE&state={state}", timeout=3) as response:
            assert response.status == 200
        return True

    async def tokens(data):
        assert data["grant_type"] == "authorization_code"
        return {"access_token": "AT_B", "refresh_token": "RT_B", "expires_at": time.time() + 3600}

    async def identity():
        return {"user_id": "B", "user_name": "B"}

    monkeypatch.setattr(oauth.webbrowser, "open", browser)
    monkeypatch.setattr(acc, "_token_request", tokens)
    monkeypatch.setattr(acc, "whoami", identity)
    w._fill_accounts_menu()
    action = next(a for a in w.accounts_menu.actions() if a.text() == "Log in to Twitch …")
    action.trigger()
    wait_for(app, lambda: acc.user_id == "B" and w.statusBar().currentMessage().startswith("Logged in to Twitch"))


def message(channel, text, msg_id):
    return ChatMessage("twitch", channel, "Target", text, user_id="target", msg_id=msg_id,
                       source=f"twitch:{channel}")


@pytest.mark.parametrize("new_channel_before_card", [False, True])
@pytest.mark.parametrize("action", ["timeout", "ban"])
def test_real_card_actions_keep_the_selected_channel(window, app, monkeypatch, new_channel_before_card, action):
    w, acc = window, window.accounts["twitch"]
    w.panels[0].rows["twitch"].edit.setText("alpha")
    other = w.add_group()
    other.rows["twitch"].edit.setText("beta")
    w.tabs.setCurrentIndex(0)
    login_a(w)
    first, second = message("alpha", "alpha first", "a1"), message("beta", "beta newest", "b1")
    w.add_message(first)
    w._flush_messages()
    w.on_anchor(QUrl(f"msg:{first.uid}"))
    if new_channel_before_card:
        w.add_message(second)
        w._flush_messages()
    w.panels[0].action_bar.card.click()
    card = w.mod.card
    assert card and card.msg is first
    if not new_channel_before_card:
        w.add_message(second)
        w._flush_messages()
    app.processEvents()
    assert card.msg is first and w.mod.selected is first
    assert "alpha" in card.title.text() and "alpha" in card.windowTitle()
    assert "alpha" in w.panels[0].action_bar.label.text()
    assert "beta" in card.history.toPlainText()  # history stays cross-channel, with channel labels

    newer = message("alpha", "alpha newer", "a2")
    w.add_message(newer)
    w._flush_messages()
    assert card.msg is newer and w.mod.selected is newer
    card.delete.click()  # delete-last stays within alpha too
    wait_for(app, lambda: newer.deleted)
    deletion = [kw for method, url, kw in w.api_calls if method == "DELETE" and url.endswith("/moderation/chat")]
    assert deletion[-1]["params"]["message_id"] == "a2"
    assert deletion[-1]["params"]["broadcaster_id"] == "bid_alpha"
    assert not second.deleted

    if action == "ban":
        prompts = []

        def confirm(_parent, _title, text):
            prompts.append(text)
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", confirm)
        card.ban.click()
        assert "alpha" in prompts[-1]
    else:
        card.timeout.menu().actions()[0].trigger()
    wait_for(app, lambda: first.deleted)
    moderation = [kw for _, url, kw in w.api_calls if url.endswith("/moderation/bans")]
    assert moderation[-1]["params"]["broadcaster_id"] == "bid_alpha"
    assert not second.deleted
    # An explicit user selection can switch the card to beta, including channel-specific facts.
    w.on_anchor(QUrl(f"msg:{second.uid}"))
    assert card.msg is second and "beta" in card.title.text()
    wait_for(app, lambda: card.info_labels["following"].text().startswith("2024"))


def test_old_moderator_answer_does_not_enable_new_account_actions(window, app, monkeypatch):
    w, acc = window, window.accounts["twitch"]
    w.panels[0].rows["twitch"].edit.setText("alpha")
    login_a(w)
    reached, release = threading.Event(), threading.Event()
    queries = []
    base_api = acc.request

    async def api(method, url, **kwargs):
        if url.endswith("/moderation/channels"):
            user = kwargs["params"]["user_id"]
            queries.append(user)
            if user == "A":
                reached.set()
                await asyncio.to_thread(release.wait, 5)
                return {"data": [{"broadcaster_login": "alpha"}]}
            assert user == "B"
            return {"data": []}
        return await base_api(method, url, **kwargs)

    monkeypatch.setattr(acc, "request", api)
    first, second = message("alpha", "first", "a1"), message("alpha", "second", "a2")
    w.add_message(first)
    w._flush_messages()
    try:
        assert reached.wait(3)
        old = next(f for name, f in w.action_futures if "ModActions._ask" in name)
        w.logout("twitch")
        login_b_via_menu(w, app, monkeypatch)
        w.add_message(second)
        w._flush_messages()
        w.on_anchor(QUrl(f"msg:{second.uid}"))
        wait_for(app, lambda: queries == ["A", "B"] and w.mod.is_mod(second) is False)
        assert w.panels[0].action_bar.timeout.isHidden()
    finally:
        release.set()
    deliver_result(app, old)
    assert w.mod.is_mod(second) is False
    assert w.panels[0].action_bar.timeout.isHidden()


@pytest.mark.parametrize("old_error", [False, True])
def test_old_user_info_reply_cannot_restore_previous_account_card(window, app, monkeypatch, old_error):
    w, acc = window, window.accounts["twitch"]
    w.panels[0].rows["twitch"].edit.setText("alpha")
    login_a(w)
    reached, release = threading.Event(), threading.Event()
    base_api = acc.request

    async def api(method, url, **kwargs):
        if url.endswith("/users") and kwargs.get("params", {}).get("id") == "target":
            user = acc.user_id
            if user == "A":
                reached.set()
                await asyncio.to_thread(release.wait, 5)
                if old_error:
                    raise oauth.ApiError("old account request failed", status=403)
            year = "2000" if user == "A" else "2025"
            return {"data": [{"id": "target", "created_at": f"{year}-01-01T00:00:00Z"}]}
        return await base_api(method, url, **kwargs)

    monkeypatch.setattr(acc, "request", api)
    first = message("alpha", "first", "a1")
    w.add_message(first)
    w._flush_messages()
    w.on_anchor(QUrl(f"msg:{first.uid}"))
    w.panels[0].action_bar.card.click()
    card = w.mod.card
    try:
        assert reached.wait(3)
        old = next(f for name, f in w.action_futures if "ModActions.user_info" in name)
        w.logout("twitch")
        assert card.info_labels["created"].text() == "log in for more"
        login_b_via_menu(w, app, monkeypatch)
        wait_for(app, lambda: card.info_labels["created"].text().startswith("2025"))
    finally:
        release.set()
    deliver_result(app, old)
    assert card.info_labels["created"].text().startswith("2025")
    assert w.mod._info and all(info.get("created", "").startswith("2025") for _, info in w.mod._info.values())
