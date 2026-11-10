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
