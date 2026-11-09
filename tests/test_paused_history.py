# SPDX-License-Identifier: GPL-3.0-or-later
"""A paused reading passage is a bounded message model, not just old HTML/pixels."""
import time

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent, QTextCursor
from PyQt6.QtNetwork import QNetworkAccessManager
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMenu, QMessageBox

from test_gui import app, window as base_window
from multichat.chat.models import ChatMessage, Deletion
from multichat.ui import main_window
from multichat.ui.main_window import MAX_MESSAGES
from test_image_limits import ControlledReply


@pytest.fixture
def window(base_window):
    store = base_window.accounts["twitch"].store
    original = store.all()
    yield base_window
    store.replace_all(original)


@pytest.fixture(autouse=True)
def no_remote_images(monkeypatch):
    monkeypatch.setattr(QNetworkAccessManager, "get", lambda self, request: ControlledReply(self, request))
    monkeypatch.setattr(main_window, "save_config", lambda *_: None)  # this module must not leak font/filter changes


def settle(app, w):
    QTest.qWait(200)  # exercise the real coalesced render timer and Qt delivery
    assert not w._render_timer.isActive()


def burst(w, user, prefix, channel="chan", platform="twitch", count=MAX_MESSAGES):
    messages = [ChatMessage(platform, channel, user, f"{prefix} {i}", user_id=user.lower(),
                            msg_id=f"{prefix}-{i}", source=f"{platform}:{channel}") for i in range(count)]
    for message in messages:
        w.add_message(message)
    w._flush_messages()
    return messages


def paused_passage(w, app):
    w.accounts["twitch"].store.replace_all({})
    w.refresh_accounts()
    w.panels[0].rows["twitch"].edit.setText("chan")
    old = burst(w, "Alice", "OLD")
    w.show()
    settle(app, w)
    w.view.verticalScrollBar().setValue(0)
    assert not w.view.follow
    new = burst(w, "Bob", "NEW")
    assert w.view.held and all(m.user == "Bob" for m in w.messages.values())
    assert old[0].uid not in w.messages
    return old, new


def test_bulk_moderation_and_full_rebuild_preserve_paused_passage_then_catch_up(window, app):
    w = window
    old, new = paused_passage(w, app)
    anchor = w.view._read_anchor()
    # Exact HTML N03 trigger: all new (not currently displayed) Bob lines are deleted.
    w.bridge.deletion.emit(Deletion("twitch", user_id="bob", source="twitch:chan"))
    settle(app, w)
    assert all(m.deleted for m in new) and all(not m.deleted for m in old)
    assert "Alice: OLD 0" in w.view.document().firstBlock().text()
    assert w.view._read_anchor()[:3] == anchor[:3] and not w.view.follow and w.view.held
    # Other automatic reasons for a full rebuild must use the same pinned model.
    w.render_all()
    settle(app, w)
    assert "Alice: OLD 0" in w.view.document().firstBlock().text()
    # Deletion also reaches old lines no longer in live history or per-user history.
    assert old[0] not in w.mod.history.of(old[0])
    w.bridge.deletion.emit(Deletion("twitch", user_id="alice", source="twitch:chan"))
    settle(app, w)
    assert all(m.deleted for m in old)
    assert "Alice: OLD 0 (deleted)" in w.view.document().firstBlock().text()
    assert w.view._read_anchor()[:3] == anchor[:3]
    assert len(w.view.retained_messages) == MAX_MESSAGES
    assert len(list(w.known_messages())) == MAX_MESSAGES * 2
    w.view.jump.click()
    settle(app, w)
    assert w.view.follow and not w.view.held and w.view.unseen == 0
    assert "Bob: NEW 0 (deleted)" in w.view.document().firstBlock().text()
    assert set(w.view.retained_messages) == {m.uid for m in new}
    assert w.message_by_uid(old[0].uid) is None


def test_old_visible_name_and_delete_menu_work_after_both_histories_trim_it(window, app, monkeypatch):
    from multichat.ui.moderation import store
    monkeypatch.setattr(store, "HISTORY_USERS", 1)  # same production eviction policy, reached sooner
    w = window
    old, _new = paused_passage(w, app)
    acc = w.accounts["twitch"]
    acc.store.update("twitch", access_token="TEST", expires_at=9e12, user_id="me", user_name="Me")
    w._native_emotes["twitch:chan"] = {}
    w.mod.state.set("twitch", "chan", True)
    w.refresh_accounts()
    settle(app, w)
    original = old[0]
    assert not w.mod.history.of(original)
    assert w.message_by_uid(original.uid) is original
    # Real name hit testing emits the original block UID through MainWindow's handler.
    view = w.view
    block = view.document().firstBlock()
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + block.text().index("Alice") + 1)
    position = view.cursorRect(cursor).center()
    assert view.message_at(position).path() == str(original.uid)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert w.mod.selected.user_id == "alice" and w.mod.selected.source == "twitch:chan"
    assert view.selected == ("twitch", "alice")
    # Both histories evicted Alice; the action bar still finds the newest pinned message.
    assert w.mod.last_deletable(original) is old[-1]
    calls = []

    async def delete(message):
        calls.append((message.uid, message.msg_id, message.source))

    monkeypatch.setattr(acc, "delete", delete)
    monkeypatch.setattr(QMenu, "exec", lambda menu, *_: next(a for a in menu.actions()
                                                           if a.text() == "Delete message").trigger())
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position, view.viewport().mapToGlobal(position))
    app.sendEvent(view.viewport(), event)
    end = time.monotonic() + 3
    while not original.deleted and time.monotonic() < end:
        app.processEvents()
        QTest.qWait(5)
    settle(app, w)
    assert calls == [(original.uid, "OLD-0", "twitch:chan")]
    assert original.deleted and "OLD 0 (deleted)" in view.document().firstBlock().text()
    bar = w.panels[0].action_bar
    assert bar.delete.isEnabled()
    bar.delete.click()
    end = time.monotonic() + 3
    while not old[-1].deleted and time.monotonic() < end:
        app.processEvents()
        QTest.qWait(5)
    settle(app, w)
    assert old[-1].deleted and calls[-1] == (old[-1].uid, "OLD-999", "twitch:chan")


def test_paused_model_survives_filters_and_font_but_is_removed_on_source_change(window, app):
    w = window
    old, _new = paused_passage(w, app)
    v = w.view
    w.filters["twitch"].setChecked(False)
    settle(app, w)
    assert v.toPlainText() == "" and len(v.retained_messages) == MAX_MESSAGES and not v.follow
    w.filters["twitch"].setChecked(True)
    settle(app, w)
    assert "Alice: OLD 0" in v.document().firstBlock().text()
    v.verticalScrollBar().setValue(v.verticalScrollBar().maximum() // 2 + 7)
    anchor = v._read_anchor()
    w.change_font(3)
    settle(app, w)
    assert not v.follow and v._read_anchor()[0] == anchor[0]
    block = v.document().firstBlock()
    while block.isValid() and block.userState() != anchor[0]:
        block = block.next()
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + min(anchor[1], block.length() - 1))
    assert v.cursorRect(cursor).top() == anchor[2]
    # A changed channel removes its model immediately, with a fresh follow state.
    w.panels[0].rows["twitch"].edit.setText("replacement")
    settle(app, w)
    assert not v.retained_messages and v.toPlainText() == "" and v.follow and not v.held
    assert w.message_by_uid(old[0].uid) is None
    burst(w, "Carol", "REPLACEMENT", channel="replacement", count=5)
    settle(app, w)
    assert "Carol: REPLACEMENT 0" in v.toPlainText() and "OLD" not in v.toPlainText()
    w.clear_chat()
    assert not v.retained_messages and not list(w.known_messages()) and v.follow


def test_overlapping_groups_keep_independent_paused_models_and_reuse_drops_references(window, app, monkeypatch):
    w = window
    w.accounts["twitch"].store.replace_all({})
    first = w.panels[0]
    first.rows["twitch"].edit.setText("chan")
    second = w.add_group()
    second.rows["twitch"].edit.setText("chan")
    old = burst(w, "Alice", "OLD")
    w.tabs.setCurrentIndex(0)
    w.show()
    settle(app, w)
    first.view.verticalScrollBar().setValue(0)
    assert not first.view.follow and second.view.follow
    new = burst(w, "Bob", "NEW")
    w.render_all()
    settle(app, w)
    assert set(first.view.retained_messages) == {m.uid for m in old}
    assert set(second.view.retained_messages) == {m.uid for m in new}
    w.apply_deletion(Deletion("twitch", user_id="alice", source="twitch:chan"))
    settle(app, w)
    assert "OLD 0 (deleted)" in first.view.toPlainText()
    assert "NEW 0 (deleted)" not in second.view.toPlainText()
    assert len(list(w.known_messages())) == MAX_MESSAGES * 2
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    w.remove_group(first)
    assert not first.view.retained_messages
    reused = w.add_group()
    assert reused is first and reused.view.follow and not reused.view.retained_messages
    assert reused.view.toPlainText() == "" and w.message_by_uid(old[0].uid) is None


def test_resuming_also_catches_up_messages_hidden_by_a_filter(window, app):
    w = window
    w.accounts["twitch"].store.replace_all({})
    w.refresh_accounts()
    w.panels[0].rows["twitch"].edit.setText("chan")
    old = burst(w, "Alice", "OLD", count=100)
    w.show()
    settle(app, w)
    w.view.verticalScrollBar().setValue(0)
    assert not w.view.follow
    w.filters["twitch"].setChecked(False)
    settle(app, w)
    new = burst(w, "Bob", "NEW", count=100)
    assert not w.view.held  # no matching lines arrived while this platform was hidden
    w.filters["twitch"].setChecked(True)
    settle(app, w)
    assert set(w.view.retained_messages) == {m.uid for m in old}
    w.view.jump.click()
    settle(app, w)
    assert w.view.follow
    assert set(w.view.retained_messages) == {m.uid for m in old + new}
    assert "Bob: NEW 99" in w.view.toPlainText()


def test_long_pause_does_not_accumulate_additional_message_models(window, app):
    w = window
    old, _new = paused_passage(w, app)
    latest = []
    for i in range(4):
        latest = burst(w, "Bob", f"LATER-{i}")
    assert len(w.messages) == MAX_MESSAGES
    assert len(w.view.retained_messages) == MAX_MESSAGES
    assert set(w.view.retained_messages) == {m.uid for m in old}
    assert {m.uid for m in w.known_messages()} == {m.uid for m in old + latest}
    assert w.view.document().blockCount() == MAX_MESSAGES
    w.render_all()
    settle(app, w)
    assert "Alice: OLD 0" in w.view.document().firstBlock().text()
