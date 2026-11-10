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
