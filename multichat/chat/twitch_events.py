# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""What Twitch's IRC lines mean beyond "someone wrote something": subs, gifts, raids,
announcements and other notices, labels for special messages, timeouts and bans.
Pure functions over the IRC tags; twitch.py reads the socket and builds the messages."""
from __future__ import annotations

from ..core.i18n import _

SUB_PLANS = {"Prime": "Prime", "1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def twitch_event(tags: dict[str, str]) -> tuple[str, float, str] | None:
    """USERNOTICE tags → (event, value, text). Subs, gifts and raids are events of their
    own; everything else Twitch announces in the chat (announcements, watch streaks, bits
    badges, charity donations, whatever comes next) is a "notice" with Twitch's own wording.

    A community gift ("gifted 5 subs") is followed by one subgift notice per recipient:
    both are shown – the total, then who got one. The single ones carry
    msg-param-community-gift-id and a value of 0, so the gifts are not counted twice
    (alert thresholds, overlay)."""
    kind = tags.get("msg-id", "")
    plan = SUB_PLANS.get(tags.get("msg-param-sub-plan", ""), "")
    if kind in ("sub", "resub"):
        return "sub", _int(tags.get("msg-param-cumulative-months")) or 1, plan
    if kind in ("giftpaidupgrade", "anongiftpaidupgrade", "primepaidupgrade"):
        return "sub", 1, plan
    if kind in ("subgift", "anonsubgift"):
        recipient = tags.get("msg-param-recipient-display-name", "")
        if tags.get("msg-param-community-gift-id"):
            return "gift", 0, recipient
        return "gift", 1, recipient
    if kind in ("submysterygift", "anonsubmysterygift"):
        return "gift", _int(tags.get("msg-param-mass-gift-count")) or 1, ""
    if kind == "raid":
        return "raid", _int(tags.get("msg-param-viewerCount")), ""
    if kind == "announcement":
        return "notice", 0, _("Announcement")
    return "notice", 0, tags.get("system-msg") or kind.replace("-", " ").replace("_", " ") or _("Notice")


def message_note(tags: dict[str, str]) -> str:
    """What sets a normal chat message apart on Twitch ("" for most): bought with channel
    points, sent with a Power-up, the first one of a new chatter."""
    kind = tags.get("msg-id", "")
    if kind == "highlighted-message":
        return _("Highlighted message (channel points)")
    if tags.get("custom-reward-id"):
        return _("Channel points reward")
    if kind in ("animated-message", "gigantified-emote-message"):
        return _("Power-up")
    if kind == "user-intro" or tags.get("first-msg") == "1":
        return _("First message")
    return ""


def moderation_notice(tags: dict[str, str], login: str) -> str:
    """CLEARCHAT → the line Twitch shows for it: a timeout, a ban, or the cleared chat."""
    if not tags.get("target-user-id"):
        return _("The chat was cleared")
    seconds = _int(tags.get("ban-duration"))
    if seconds:
        return _("{user} was timed out for {duration}").format(user=login or "?", duration=duration_text(seconds))
    return _("{user} was banned").format(user=login or "?")


def duration_text(seconds: int) -> str:
    """600 → "10 min", 45 → "45 s", 7200 → "2 h", 172800 → "2 d"."""
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size} {unit}"
    return f"{seconds} s"
