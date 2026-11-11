# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""YouTube chat items that are no message, Super Chat or membership. Pure functions over
the InnerTube data; youtube_innertube.py reads the chat."""
from __future__ import annotations

from .models import ChatMessage

def _text(obj: dict | None) -> str:
    if not obj:
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    return "".join(r.get("text", "") for r in obj.get("runs", []))


_NOTICE_TEXTS = ("text", "message", "primaryText", "headerPrimaryText", "subtext", "headerSubtext")


# no line for these: a message that is still being checked, and the "Welcome to live chat!
# Remember to guard your privacy …" YouTube sends on every connect
_NO_NOTICE = ("liveChatPlaceholderItemRenderer", "liveChatViewerEngagementMessageRenderer")


def notice(item: dict, video_id: str) -> ChatMessage | None:
    """A chat item that is no message, Super Chat or membership: YouTube's own lines (chat
    mode changed, "welcome to live chat"), a redeemed gift membership, a poll … They are
    read by what they have in common – an author if any, and their texts – instead of one
    by one, so a new kind is shown too. None if there is nothing to show."""
    kind, r = next(((k, v) for k, v in item.items() if isinstance(v, dict)), ("", None))
    if not r or kind in _NO_NOTICE:
        return None
    texts = []
    for key in _NOTICE_TEXTS:
        value = _text(r.get(key)) if isinstance(r.get(key), dict) else ""
        if value.strip() and value.strip() not in texts:
            texts.append(value.strip())
    if not texts:
        return None
    author = _text(r.get("authorName")) if isinstance(r.get("authorName"), dict) else ""
    headline, body = (texts[0], " ".join(texts[1:])) if not author else (" ".join(texts), "")
    return ChatMessage(
        platform="youtube", channel=video_id, user=author or "YouTube", text=body,
        parts=[("text", body, "")] if body else [], msg_id=r.get("id") if isinstance(r.get("id"), str) else None,
        user_id=r.get("authorExternalChannelId") if author else None,
        event="notice", event_text=f"{author} {headline}" if author else headline,
    )
