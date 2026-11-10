# SPDX-License-Identifier: GPL-3.0-or-later
"""Emote suggestions while typing (":kap" → Kappa)."""
import asyncio

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QInputMethodEvent
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


def input_unicode(edit, text):
    event = QInputMethodEvent()
    event.setCommitString(text)
    QApplication.sendEvent(edit, event)


@pytest.mark.parametrize("prefix", ["😀 ", "😀🚀 ", "hello 😀 "])
def test_unicode_completion_keeps_qt_cursor_and_following_text(app, monkeypatch, prefix):
    monkeypatch.setattr(EmoteCompleter, "_icon", lambda *_: QIcon())
    edit, c, sent = make(app)
    input_unicode(edit, prefix)
    QTest.keyClicks(edit, ":kap")
    assert c.visible()
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Tab)
    assert edit.text() == prefix + "Kappa "
    assert edit.cursorPosition() == len(edit.text().encode("utf-16-le")) // 2
    QTest.keyClicks(edit, "hello")
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert sent == [prefix + "Kappa hello"]


def test_unicode_completion_in_middle_preserves_suffix_and_undo(app, monkeypatch):
    monkeypatch.setattr(EmoteCompleter, "_icon", lambda *_: QIcon())
    edit, c, _ = make(app)
    input_unicode(edit, "😀 ")
    QTest.keyClicks(edit, ":ka tail")
    for _ in range(5):
        QTest.keyClick(edit, Qt.Key.Key_Left)
    QTest.keyClicks(edit, "p")
    assert c.visible()
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Tab)
    QTest.keyClicks(edit, "hello ")
    assert edit.text() == "😀 Kappa hello tail"
    for _ in range(2):  # Qt may group the completion and following typing into one undo.
        edit.undo()
        if edit.text() == "😀 :kap tail":
            break
    assert edit.text() == "😀 :kap tail"


def test_completion_does_not_apply_stale_span_after_selection(app, monkeypatch):
    monkeypatch.setattr(EmoteCompleter, "_icon", lambda *_: QIcon())
    edit, c, _ = make(app)
    input_unicode(edit, "😀 ")
    QTest.keyClicks(edit, ":kap")
    assert c.visible()
    edit.selectAll()
    assert not c.visible()
    c.insert("Kappa")
    assert edit.text() == "😀 :kap"
    QTest.keyClicks(edit, "new text")
    assert edit.text() == "new text"


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


# ---- "@" suggestions: the people in the chat --------------------------------- #
NAMES_RECENT = [("Carol", "Carol"), ("alice", "alice"), ("Bob", "Bob"), ("Alina", "Alina"), ("Malice", "Malice")]


def make_mentions(app, names=None):
    from multichat.ui.emote_complete import EmoteCompleter as Completer

    edit = QLineEdit()
    sent = []
    edit.returnPressed.connect(lambda: sent.append(edit.text()))
    c = Completer(edit, lambda: EMOTES, lambda: names if names is not None else NAMES_RECENT)
    edit.show()
    edit.activateWindow()
    edit.setFocus()
    app.processEvents()
    return edit, c, sent


def shown(c):
    return [c.model.item(i).text() for i in range(c.model.rowCount())]


def test_rank_names_keeps_most_recent_first():
    from multichat.ui.emote_complete import rank_names

    assert [n for n, _m in rank_names("al", NAMES_RECENT)] == ["alice", "Alina", "Malice"]  # prefix, then contains
    assert rank_names("zz", NAMES_RECENT) == []
    # a name shown differently from the account is found by both
    twitch = [("表示名 (examplelogin)", "examplelogin")]
    assert rank_names("exa", twitch) == twitch and rank_names("表", twitch) == twitch
    assert rank_names("han", [("@handle", "@handle")]) == [("@handle", "@handle")]  # YouTube handles


def test_at_lists_the_people_in_the_chat_and_inserts_the_name(app):
    edit, c, sent = make_mentions(app)
    QTest.keyClicks(edit, "hi @")
    assert not c.visible()  # "@" alone: nothing yet
    QTest.keyClicks(edit, "a")
    assert c.visible() and shown(c) == ["alice", "Alina", "Carol", "Malice"]
    QTest.keyClicks(edit, "li")
    assert shown(c) == ["alice", "Alina", "Malice"]
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Down)
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Return)
    assert edit.text() == "hi @Alina " and sent == [] and not c.visible()  # picked, not sent
    QTest.keyClicks(edit, "and @bo")
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Tab)
    assert edit.text() == "hi @Alina and @Bob "
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert sent == ["hi @Alina and @Bob "]


def test_at_inserts_the_account_name_and_never_doubles_the_at_sign(app):
    names = [("表示名 (examplelogin)", "examplelogin"), ("@some.handle", "@some.handle"), ("Ünïcödé", "Ünïcödé")]
    edit, c, _sent = make_mentions(app, names)
    QTest.keyClicks(edit, "@exa")
    assert shown(c) == ["表示名 (examplelogin)"]
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Return)
    assert edit.text() == "@examplelogin "  # what Twitch recognises, not the shown name
    QTest.keyClicks(edit, "@some.h")
    assert shown(c) == ["@some.handle"]
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Return)
    assert edit.text() == "@examplelogin @some.handle "
    event = QInputMethodEvent()  # QTest's key clicks only do ASCII
    event.setCommitString("@ün")
    app.sendEvent(edit, event)
    assert shown(c) == ["Ünïcödé"]
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Return)
    assert edit.text() == "@examplelogin @some.handle @Ünïcödé "


def test_at_inside_a_word_and_unknown_names_show_nothing(app):
    edit, c, _sent = make_mentions(app)
    QTest.keyClicks(edit, "mail@al")
    assert not c.visible()  # not a mention
    edit.clear()
    QTest.keyClicks(edit, "@zzz")
    assert not c.visible()
    QTest.keyClick(edit, Qt.Key.Key_Return)  # Enter sends as usual
    edit.clear()
    QTest.keyClicks(edit, "@al")
    assert c.visible()
    QTest.keyClicks(edit, " ")
    assert not c.visible()  # the name was typed out
    edit.clear()
    QTest.keyClicks(edit, ":kap")  # emotes still work next to it
    assert shown(c)[:2] == ["Kappa", "KappaPride"]
    QTest.keyClick(c.completer.popup(), Qt.Key.Key_Return)
    assert edit.text() == "Kappa "
