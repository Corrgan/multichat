# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
import time

from multichat.chat.message_log import MessageLog
from multichat.chat.models import ChatMessage, Deletion


def msg(text, user_id="", msg_id="", platform="twitch", parts=None):
    m = ChatMessage(platform, "chan", "User", text, msg_id=msg_id, user_id=user_id, parts=parts or [])
    return m


def test_each_group_keeps_its_newest():
    log = MessageLog(3)
    quiet = [msg(f"q{i}") for i in range(2)]
    for m in quiet:
        log.add(m, ["quiet"])
    for i in range(10):
        log.add(msg(f"b{i}"), ["busy"])
    texts = [m.text for m in log.messages.values()]
    assert texts == ["q0", "q1", "b7", "b8", "b9"]


def test_a_shared_message_stays_while_one_group_counts_it():
    log = MessageLog(2)
    shared = msg("shared")
    log.add(shared, ["a", "b"])
    log.add(msg("a1"), ["a"])
    log.add(msg("a2"), ["a"])  # a no longer counts it, b still does
    assert shared.uid in log.messages
    log.add(msg("b1"), ["b"])
    log.add(msg("b2"), ["b"])
    assert shared.uid not in log.messages and len(log) == 4


def test_no_group_no_storage():
    log = MessageLog(5)
    log.add(msg("x"), [])
    assert len(log) == 0


def test_indexes_find_and_forget():
    log = MessageLog(2)
    a = msg("a", user_id="7", msg_id="m1", parts=[("emote", "https://cdn/e.png", "E")])
    b = msg("b", user_id="7", msg_id="m2")
    c = msg("c", user_id="8", msg_id="m3", platform="kick")
    for m in (a, b, c):
        log.add(m, ["g"])
    assert log.for_deletion(Deletion("twitch", user_id="7")) == [b]  # a was pushed out
    assert log.with_image("https://cdn/e.png") == []
    assert log.for_deletion(Deletion("kick", msg_id="m3")) == [c]
    assert log.for_deletion(Deletion("twitch", msg_id="m3")) == []  # other platform
    assert log.for_deletion(Deletion("kick", clear_all=True)) == [c]
    assert not log._by_image and ("twitch", "m1") not in log._by_msg


def test_directly_removed_message_is_skipped():
    log = MessageLog(5)
    a = msg("a", user_id="7")
    log.add(a, ["g"])
    log.messages.pop(a.uid)
    assert log.for_deletion(Deletion("twitch", user_id="7")) == []


def test_regroup_counts_under_new_routing():
    log = MessageLog(2)
    ms = [msg(f"m{i}") for i in range(4)]
    for m in ms:
        log.add(m, ["a"])
    log.regroup(lambda m: ["a", "b"] if m.text != "m3" else [])
    assert [m.text for m in log.messages.values()] == ["m2"]  # m0, m1 were already pushed out


def test_lookups_do_not_scale_with_history():
    log = MessageLog(1000)
    for g in range(10):
        for i in range(1000):
            log.add(msg(f"{g}-{i}", user_id=str(i)), [g])
    start = time.perf_counter()
    for _ in range(200):
        log.for_deletion(Deletion("twitch", msg_id="nope"))
    assert time.perf_counter() - start < 0.05
