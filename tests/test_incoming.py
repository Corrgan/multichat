# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded ingestion, fairness and moderation ordering before Qt delivery."""
from concurrent.futures import ThreadPoolExecutor

from multichat.chat.models import ChatMessage, Deletion
from multichat.ui.incoming import IncomingEvents


def message(source, index):
    return ChatMessage("twitch", source, "User", str(index), source=f"twitch:{source}", msg_id=str(index))


def test_burst_is_bounded_and_quiet_source_survives():
    inbox = IncomingEvents(1000, 10000)
    wakeups = sum(inbox.put(message("quiet", i)) for i in range(5))
    wakeups += sum(inbox.put(message("busy", i)) for i in range(1500))
    assert wakeups == 1 and len(inbox) == 1005 and inbox.dropped == 500
    batch = inbox.take(1000)
    assert sum(m.channel == "quiet" for m in batch) == 5
    assert [int(m.text) for m in batch if m.channel == "busy"] == list(range(500, 1495))
    assert [int(m.text) for m in inbox.take(1000)] == list(range(1495, 1500))
    assert inbox.put(message("quiet", 6))  # draining re-arms exactly one wakeup


def test_global_capacity_discards_from_largest_backlog():
    inbox = IncomingEvents(10, 6)
    for i in range(6):
        inbox.put(message("busy", i))
    inbox.put(message("quiet", 99))
    assert len(inbox) == 6 and inbox.dropped == 1
    assert [m.text for m in inbox.take(2)] == ["1", "99"]


def test_messages_and_deletions_keep_source_order():
    inbox = IncomingEvents(3, 8)
    inbox.put(message("chan", 0))
    deletion = Deletion("twitch", msg_id="0", source="twitch:chan")
    inbox.put(deletion)
    inbox.put(message("chan", 1))
    inbox.put(message("chan", 2))  # discard old message, never its deletion
    assert inbox.take(3)[0] is deletion
    for i in range(20):
        inbox.put(Deletion("twitch", msg_id=str(i), source="twitch:chan"))
    events = inbox.take(8)
    assert all(isinstance(e, Deletion) and not e.clear_all for e in events)
    assert [e.msg_id for e in events] == ["17", "18", "19"]
    assert inbox.dropped_deletions == 17 and len(events) == 3


def test_moderation_upgrades_one_pending_normal_wakeup():
    inbox = IncomingEvents(4, 8)
    assert inbox.put(message("chan", 0)) == 1
    assert inbox.put(message("chan", 1)) == 0
    assert inbox.put(Deletion("twitch", msg_id="0", source="twitch:chan")) == 2
    assert inbox.put(Deletion("twitch", msg_id="1", source="twitch:chan")) == 0
    inbox.take(8)
    assert inbox.put(Deletion("twitch", msg_id="2", source="twitch:chan")) == 2


def test_concurrent_producers_have_single_pending_wakeup():
    inbox = IncomingEvents(100, 400)
    def produce(source):
        return sum(inbox.put(message(source, i)) for i in range(150))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(produce, ("a", "b", "c", "d"))) == 1
    assert len(inbox) == 400 and inbox.dropped == 200
    assert {m.channel for m in inbox.take(4)} == {"a", "b", "c", "d"}
