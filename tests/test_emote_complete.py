# SPDX-License-Identifier: GPL-3.0-or-later
"""Emote suggestions while typing (":kap" → Kappa)."""
import asyncio

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from multichat.ui.emote_complete import EmoteCompleter, rank

EMOTES = {"Kappa": "https://e/1", "KappaPride": "https://e/2", "Keepo": "https://e/3", "PogChamp": "https://e/4",
          "exampleKappaHype": "https://e/5", "catJAM": "https://e/6"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_rank_prefix_first_then_contains():
    assert rank("kap", EMOTES) == ["Kappa", "KappaPride", "exampleKappaHype"]
    assert rank("jam", EMOTES) == ["catJAM"]
    assert rank("zz", EMOTES) == []


def make(app):
    edit = QLineEdit()
    sent = []
    edit.returnPressed.connect(lambda: sent.append(edit.text()))
    c = EmoteCompleter(edit, lambda: EMOTES)
    edit.show()
    edit.activateWindow()
    edit.setFocus()
    app.processEvents()
    return edit, c, sent


def test_suggestions_while_typing(app):
    edit, c, _sent = make(app)
    QTest.keyClicks(edit, "hi :k")
    assert not c.visible()  # one letter: nothing yet
    QTest.keyClicks(edit, "ap")
    assert c.visible() and [c.model.item(i).text() for i in range(c.model.rowCount())][:2] == ["Kappa", "KappaPride"]
    c.insert("KappaPride")
    assert edit.text() == "hi KappaPride " and not c.visible()


def test_enter_picks_the_emote_without_sending(app):
    edit, c, sent = make(app)
    QTest.keyClicks(edit, "gg :pog")
    assert c.visible()
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Return)
    assert edit.text() == "gg PogChamp " and sent == []  # picked, not sent
    QTest.keyClicks(edit, ":kap")
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Tab)
    assert edit.text() == "gg PogChamp Kappa " and sent == []
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert sent == ["gg PogChamp Kappa "]


def test_full_code_with_colons_is_replaced(app):
    edit, c, _sent = make(app)
    QTest.keyClicks(edit, "look :catjam:")
    assert edit.text() == "look catJAM "
    QTest.keyClicks(edit, ":nothing:")
    assert edit.text() == "look catJAM :nothing:"  # unknown: stays as typed


def test_no_suggestions_inside_words_or_urls(app):
    edit, c, _sent = make(app)
    QTest.keyClicks(edit, "https://kappa")
    assert not c.visible()
    edit.clear()
    QTest.keyClicks(edit, "time 12:kap")
    assert not c.visible()


def test_twitch_usable_emotes_pages_and_fallback(tmp_path):
    from multichat.accounts import ApiError, FileBackend, SecretStore, TwitchAccount

    store = SecretStore("tokens", FileBackend(tmp_path))
    acc = TwitchAccount(store, lambda p: ("CID", "SECRET"), lambda: None)
    store.replace("twitch", {"access_token": "x", "expires_at": 9e12, "user_id": "42", "user_name": "Me"})
    template = "https://static-cdn.jtvnw.net/emoticons/v2/{{id}}/{{format}}/{{theme_mode}}/{{scale}}"
    calls = []

    async def fake_request(method, url, params=None, **kw):
        calls.append((url.rsplit("/helix", 1)[1], dict(params or {})))
        if url.endswith("/users"):
            return {"data": [{"id": "7"}]}
        if url.endswith("/chat/emotes/user"):
            if not (params or {}).get("after"):
                return {"data": [{"id": "25", "name": "Kappa"}], "template": template, "pagination": {"cursor": "c1"}}
            return {"data": [{"id": "e2", "name": "exampleSub"}], "template": template, "pagination": {}}
        raise AssertionError(url)

    acc.request = fake_request
    emotes, complete = asyncio.run(acc.usable_emotes("examplechan"))
    assert complete and dict(emotes) == {
        "Kappa": "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/1.0",
        "exampleSub": "https://static-cdn.jtvnw.net/emoticons/v2/e2/default/dark/1.0"}
    assert calls[1][1] == {"user_id": "42", "broadcaster_id": "7"}

    async def old_login(method, url, params=None, **kw):  # login without user:read:emotes
        if url.endswith("/users"):
            return {"data": [{"id": "7"}]}
        if url.endswith("/chat/emotes/user"):
            raise ApiError("Missing scope: user:read:emotes", 401)
        if url.endswith("/chat/emotes/global"):
            return {"data": [{"id": "25", "name": "Kappa", "images": {"url_1x": "https://e/kappa"}}]}
        return {"data": [{"id": "9", "name": "exampleChan", "images": {"url_1x": "https://e/chan"}}]}

    acc._cache.clear()
    acc.request = old_login
    emotes, complete = asyncio.run(acc.usable_emotes("examplechan"))
    assert not complete and dict(emotes) == {"Kappa": "https://e/kappa", "exampleChan": "https://e/chan"}
