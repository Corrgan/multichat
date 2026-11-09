# SPDX-License-Identifier: GPL-3.0-or-later
"""Moderation data: bounded history, notes file sanitising, own-message detection."""
import json
import os
from types import SimpleNamespace

import pytest

from multichat.chat.models import ChatMessage, Deletion
from multichat.ui.moderation import ModActions, ModNotes, UserHistory, since_text
from multichat.ui.moderation import store


def test_history_is_bounded_and_marks_deletions(monkeypatch):
    monkeypatch.setattr(store, "HISTORY_TOTAL", 50)
    h = UserHistory()
    for i in range(30):
        h.add(ChatMessage("twitch", "c", f"u{i}", "a", user_id=str(i)))
        h.add(ChatMessage("twitch", "c", f"u{i}", "b", user_id=str(i)))
    assert h.total <= 50 and sum(len(d) for d in h.users.values()) == h.total
    assert ("twitch", "0") not in h.users and ("twitch", "29") in h.users  # least recently active dropped
    assert h.apply_deletion(Deletion("twitch", user_id="29"))
    assert all(m.deleted for m in h.users[("twitch", "29")])


def test_latest_message_keeps_channel_and_connection_context():
    history = UserHistory()
    first = ChatMessage("youtube", "video", "Target", "first", user_id="user", source="youtube:@alpha")
    newer = ChatMessage("youtube", "video", "Target", "newer", user_id="user", source="youtube:@alpha")
    other_source = ChatMessage("youtube", "video", "Target", "other connection", user_id="user", source="youtube:@beta")
    other_channel = ChatMessage("youtube", "other", "Target", "other channel", user_id="user", source="youtube:@alpha")
    for msg in (first, newer, other_source, other_channel):
        history.add(msg)
    assert history.of(first) == [first, newer, other_source, other_channel]
    assert history.in_context(first) == [first, newer]
    assert history.latest(first) is newer
    unseen = ChatMessage("youtube", "unknown", "Target", "not in history", user_id="user")
    assert history.latest(unseen) is unseen


def test_notes_file_is_sanitised(tmp_path):
    path = tmp_path / "modnotes.json"
    path.write_text(json.dumps({
        "twitch:1": {"name": "A", "note": "x" * 10000,
                     "actions": [{"t": "soon", "a": "x"}, {"t": 10**20, "a": "y"}, {"t": 5, "a": "ok"}, "junk"]},
        "bad": "not a dict",
    }))
    notes = ModNotes(path)
    entry = notes.data["twitch:1"]
    assert len(entry["note"]) == 4000 and entry["actions"] == [{"t": 5, "a": "ok"}] and "bad" not in notes.data
    path.write_text("[1, 2]")
    assert ModNotes(path).data == {}


def test_notes_save_only_when_changed(tmp_path):
    notes = ModNotes(tmp_path / "n.json")
    notes.save()
    assert not (tmp_path / "n.json").exists()
    notes.set_note(ChatMessage("kick", "c", "Bob", "hi", user_id="7"), "watch")
    notes.save()
    assert json.loads((tmp_path / "n.json").read_text())["kick:7"]["note"] == "watch"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes are not available on this platform")
def test_notes_file_permissions(tmp_path):
    notes = ModNotes(tmp_path / "n.json")
    notes.set_note(ChatMessage("kick", "c", "Bob", "hi", user_id="7"), "watch")
    notes.save()
    assert (tmp_path / "n.json").stat().st_mode & 0o777 == 0o600


def test_own_message_needs_matching_id():
    acc = SimpleNamespace(user_id="UCme", user_name="Same Name")
    assert ModActions._is_own(acc, ChatMessage("youtube", "v", "Same Name", "x", user_id="UCme"))
    # another channel with the same title is not you
    assert not ModActions._is_own(acc, ChatMessage("youtube", "v", "Same Name", "x", user_id="UCother"))
    acc = SimpleNamespace(user_id="", user_name="")
    assert not ModActions._is_own(acc, ChatMessage("kick", "c", "", "x"))


def test_since_text_survives_odd_dates():
    for value in ("0001-01-01T00:00:00", "9999-12-31T23:59:59", 12345, None, "garbage"):
        assert isinstance(since_text(value), str)  # never raises
    assert since_text(12345) == ""
