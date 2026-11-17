# SPDX-License-Identifier: GPL-3.0-or-later
"""Limits on what the platforms send."""


def test_messages_are_clamped_and_cleaned():
    """Platform data is limited once, centrally – nothing downstream relies on the platforms' own limits."""
    from multichat.chat.models import MAX_PARTS, MAX_TEXT, MAX_USER, ChatMessage

    m = ChatMessage("twitch", "c", "A" * 500, "x" * 100_000)
    assert len(m.user) == MAX_USER and len(m.text) == MAX_TEXT
    assert sum(len(p[1]) for p in m.parts) == MAX_TEXT
    spoof = ChatMessage("twitch", "c", "‮resu‬\x00\n", "hi\x00\x07 there\nnext")
    assert spoof.user == "resu" and spoof.text == "hi there\nnext"  # no direction override, no control characters
    assert ChatMessage("twitch", "c", "\x00", "hi").user == "?"
    many = ChatMessage("kick", "c", "u", "t", parts=[("emote", "https://e/x.png", "E")] * 5000 + ["junk", ("text", 5, "")])
    assert len(many.parts) <= MAX_PARTS and all(len(p) == 3 for p in many.parts)
    ok = ChatMessage("youtube", "c", "Zoë 日本", "héllo 👋", parts=[("text", "héllo ", ""), ("emote", "https://e/w.png", ":wave:")])
    assert ok.user == "Zoë 日本" and ok.parts == [("text", "héllo ", ""), ("emote", "https://e/w.png", ":wave:")]
    event = ChatMessage("twitch", "c", "u", "", event="raid", event_value=5, event_text="x" * 1000)
    assert event.parts == [] and len(event.event_text) == 300


def test_ttl_cache_loads_once_for_parallel_askers():
    """Chat and video tile ask at the same moment – one request, both get its answer."""
    import threading
    import time

    from multichat.chat.models import TTLCache

    cache, calls, start = TTLCache(30), [], threading.Barrier(8)

    def load():
        calls.append(1)
        time.sleep(0.2)  # the others arrive while this one loads
        return "live"

    results = []

    def ask():
        start.wait()
        results.append(cache.fetch("chan", load))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert results == ["live"] * 8


def test_ttl_cache_does_not_keep_errors_and_expires(monkeypatch):
    import pytest

    from multichat.chat import models

    now = [1000.0]
    monkeypatch.setattr(models.time, "monotonic", lambda: now[0])
    cache = models.TTLCache(30)

    def broken():
        raise RuntimeError("HTTP 503")

    with pytest.raises(RuntimeError):
        cache.fetch("chan", broken)
    assert cache.get("chan") == (False, None)  # the next ask tries again
    assert cache.fetch("chan", lambda: "a") == "a"
    assert cache.fetch("chan", lambda: "b") == "a"  # fresh
    now[0] += 31
    assert cache.fetch("chan", lambda: "b") == "b"  # expired → loaded again


def test_ttl_cache_is_bounded():
    from multichat.chat.models import TTLCache

    cache = TTLCache(30, size=3)
    for k in "abcd":
        cache.put(k, k)
    assert cache.get("a") == (False, None)  # oldest dropped
    assert [cache.get(k) for k in "bcd"] == [(True, "b"), (True, "c"), (True, "d")]
