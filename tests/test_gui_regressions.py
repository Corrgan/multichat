# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression paths through the real selection, paused rendering and send controls."""
import asyncio

from PyQt6.QtCore import QPoint, Qt, QUrl
from PyQt6.QtGui import QIcon, QImage, QInputMethodEvent
from PyQt6.QtTest import QTest

from test_gui import app, window  # reusable offline application fixtures
from multichat.chat.models import ChatMessage
from multichat.core.constants import FAVICONS
from multichat.ui import chat_view
from multichat.ui.emote_complete import EmoteCompleter


def test_selection_compacts_during_pause_without_replacing_the_reading_passage(window, app, monkeypatch):
    w = window
    w.accounts["twitch"].store.replace_all({})
    monkeypatch.setattr(chat_view, "COMPACT_AFTER", 300)
    w.panels[0].rows["twitch"].edit.setText("chan")
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    for urls in FAVICONS.values():
        for url in urls:
            w.view._store(url, image)
    messages = [ChatMessage("twitch", "chan", f"user{i}", "line " * 20, user_id=str(i),
                            source="twitch:chan") for i in range(100)]
    for m in messages:
        w.add_message(m)
    w._flush_messages()
    w._render_timer.stop()
    w._rerender = set()
    w.show()
    app.processEvents()
    w._render_timer.stop()
    w._rerender = set()
    v = w.view
    bar = v.verticalScrollBar()
    bar.setValue(bar.maximum() // 2 + 11)
    assert not v.follow
    anchor = v._read_anchor()
    text = v.toPlainText()
    # Model a passage retained in the paused document but already trimmed from shared history.
    top_uid = v.cursorForPosition(QPoint(0, 0)).block().userState()
    w.messages.pop(top_uid)
    compacted = []
    original = v.compact

    def compact():
        compacted.append(True)
        original()

    monkeypatch.setattr(v, "compact", compact)
    for i in range(160):
        w.on_anchor(QUrl(f"msg:{messages[i % 2].uid}"))
    assert compacted and not v.needs_compaction
    assert v.toPlainText() == text and v._read_anchor()[:3] == anchor[:3]
    assert not v.follow and top_uid not in w.messages


def test_unicode_completion_preserves_the_text_sent_by_main_window(window, app, monkeypatch):
    w = window
    w.accounts["twitch"].store.replace_all({})
    w.panels[0].rows["twitch"].edit.setText("chan")
    w.accounts["twitch"].store.update("twitch", access_token="test", expires_at=9e12,
                                       user_name="Me", user_id="42")
    w._native_emotes["twitch:chan"] = {"Kappa": "https://e.invalid/kappa"}
    monkeypatch.setattr(EmoteCompleter, "_icon", lambda *_: QIcon())
    w.refresh_accounts()
    sent = []

    async def send_to(platform, channel, text):
        sent.append((platform, channel, text))

    monkeypatch.setattr(w, "send_to", send_to)
    monkeypatch.setattr(w, "run", lambda coro, **kwargs: asyncio.run(coro))
    w.show()
    w.input.setFocus()
    event = QInputMethodEvent()
    event.setCommitString("😀🚀")
    app.sendEvent(w.input, event)
    QTest.keyClicks(w.input, " :kap")
    assert w.emote_complete.visible()
    QTest.keyClick(w.emote_complete.completer.popup(), Qt.Key.Key_Tab)
    QTest.keyClicks(w.input, "hello")
    QTest.keyClick(w.input, Qt.Key.Key_Return)
    assert sent == [("twitch", "chan", "😀🚀 Kappa hello")]
    w.logout("twitch")


def test_at_suggestions_come_from_the_chat_you_send_to(window, app, monkeypatch):
    """Names after "@": the people who wrote in the send target, most recent first, without
    yourself; Twitch names shown in another script are mentioned by their account name."""
    from multichat.chat.models import ChatMessage

    w = window
    w.accounts["twitch"].store.replace_all({})
    w.panels[0].rows["twitch"].edit.setText("chan")
    w.panels[0].rows["kick"].edit.setText("examplekick")
    w.accounts["twitch"].store.update("twitch", access_token="test", expires_at=9e12, user_name="Me", user_id="42")
    w.accounts["kick"].store.update("kick", access_token="test", expires_at=9e12, user_name="MeOnKick", user_id="7")
    w.refresh_accounts()

    def say(platform, channel, user, user_id, **kw):
        m = ChatMessage(platform, channel, user, "hello", user_id=user_id, source=f"{platform}:{channel}", **kw)
        w.add_message(m)
        w._flush_messages()  # one at a time: the mailbox takes turns between channels

    say("twitch", "chan", "Alice", "1", login="alice")
    say("twitch", "chan", "表示名", "2", login="examplelogin")
    say("twitch", "otherchan", "Elsewhere", "3", login="elsewhere")  # not a channel of this group
    say("kick", "examplekick", "KickUser", "4")
    say("twitch", "chan", "Me", "42", login="me")  # yourself
    say("kick", "examplekick", "alice", "5")  # same name on the other platform
    say("twitch", "chan", "Bob", "6", login="bob")
    say("twitch", "chan", "Alice", "1", login="alice")  # wrote again: moves to the front

    w.target.setCurrentIndex(w.target.findData(None))  # all channels of the group
    assert w._send_mentions() == [("Alice", "Alice"), ("Bob", "Bob"), ("KickUser", "KickUser"),
                                  ("表示名 (examplelogin)", "examplelogin")]
    w.target.setCurrentIndex(w.target.findData("kick:examplekick"))
    assert w._send_mentions() == [("alice", "alice"), ("KickUser", "KickUser")]

    w.target.setCurrentIndex(w.target.findData("twitch:chan"))
    sent = []

    async def send_to(platform, channel, text):
        sent.append((platform, channel, text))

    monkeypatch.setattr(w, "send_to", send_to)
    monkeypatch.setattr(w, "run", lambda coro, **kwargs: asyncio.run(coro))
    w.show()
    w.input.setFocus()
    QTest.keyClicks(w.input, "@exa")
    assert w.emote_complete.visible()
    QTest.keyClick(w.emote_complete.completer.popup(), Qt.Key.Key_Return)
    QTest.keyClicks(w.input, "hi")
    QTest.keyClick(w.input, Qt.Key.Key_Return)
    assert sent == [("twitch", "chan", "@examplelogin hi")]
    w.logout("twitch")
    w.logout("kick")


def test_dragging_a_tab_reorders_the_groups(window, app):
    """The tab bar is movable; the groups follow in everything that goes by their order."""
    w = window
    for name in ("One", "Two", "Three"):
        panel = w.panels[0] if name == "One" else w.add_group()
        panel.name.setText(name)
        panel.rows["twitch"].edit.setText(f"chan{name.lower()}")
    assert w.tabs.isMovable()
    first, second, third = w.panels
    w.tabs.setCurrentIndex(0)
    third.unread = 4
    w._update_titles()

    w.tabs.moveTab(0, 2)  # what dragging the first tab to the end does
    assert w.panels == [second, third, first]
    assert [w.tabs.tabText(i) for i in range(3)] == ["Two", "Three (4)", "One"]
    assert w.tabs.currentIndex() == 2 and w.active_panel() is first  # the dragged tab stays the active one
    assert first.isVisibleTo(w) and not second.isVisibleTo(w)
    assert [w.side.indexOf(p) for p in w.panels] == [0, 1, 2]  # side by side shows the same order
    # saved in the new order, and the overlay's group numbers follow the tabs
    w.save_config()
    assert [g["name"] for g in w.config["groups"]] == ["Two", "Three", "One"]
    from types import SimpleNamespace

    def numbers(source):  # as computed for the overlay's "group=N"
        shown = w.panels_for(SimpleNamespace(source=source))
        return [i + 1 for i, pn in enumerate(w.panels) if pn in shown]

    assert numbers("twitch:chanone") == [3] and numbers("twitch:chantwo") == [1]

    w.tabs.moveTab(1, 0)
    assert w.panels == [third, second, first] and w.tabs.tabText(0) == "Three (4)"
    # a group added afterwards still goes to the end, removing one keeps the order
    fourth = w.add_group()
    assert w.panels[-1] is fourth
    w.remove_group(fourth)
    assert w.panels == [third, second, first]
