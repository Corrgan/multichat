# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""What Twitch's IRC lines mean beyond "someone wrote something": subs, gifts, raids,
announcements and other notices, labels for special messages, timeouts and bans.
Pure functions over the IRC tags (plus the channel names of shared chats, ROOMS);
twitch.py reads the socket and builds the messages."""
from __future__ import annotations

from collections import OrderedDict

from ..core.i18n import _

SUB_PLANS = {"Prime": "Prime", "1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3"}
MAX_ROOMS = 500
MAX_REPLY = 80  # characters of the quoted message a reply answers

# channel ID → display name, for "Shared chat: <channel>": the connected channels tell their
# own (IRC room-id), the website's event feed the other channels of a shared chat session
ROOMS: OrderedDict[str, str] = OrderedDict()
# channel IDs seen in a shared chat whose name isn't known yet: the event feed looks them up
WANTED: OrderedDict[str, bool] = OrderedDict()


def remember_room(room_id, name) -> None:
    room_id, name = str(room_id or ""), str(name or "").strip()
    if not room_id or not name:
        return
    ROOMS[room_id] = name
    ROOMS.move_to_end(room_id)
    WANTED.pop(room_id, None)
    while len(ROOMS) > MAX_ROOMS:
        ROOMS.popitem(last=False)


def shared_from(tags: dict[str, str]) -> str | None:
    """Shared chat: a line written in another channel of the session arrives in this one
    too (source-room-id ≠ room-id) → that channel's name ("" if unknown); None for this
    channel's own lines."""
    source = tags.get("source-room-id")
    if not source or source == tags.get("room-id"):
        return None
    name = ROOMS.get(source, "")
    if not name and source.isascii() and source.isdigit() and len(WANTED) < MAX_ROOMS:
        WANTED[source] = True
    return name


def shared_note(channel: str) -> str:
    return _("Shared chat: {channel}").format(channel=channel) if channel else _("Shared chat")


def shared_id(tags: dict[str, str]) -> str:
    """The ID one message of a shared chat has in every channel of the session ("" outside one)."""
    return tags.get("source-id", "") if tags.get("source-room-id") else ""


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
    partner = shared_from(tags)
    if partner is not None or kind == "sharedchatnotice":
        # a sub, gift or raid of another channel of a shared chat: shown, but not as an alert of this one
        text = tags.get("system-msg") or tags.get("source-msg-id", "").replace("-", " ").replace("_", " ")
        return "notice", 0, " · ".join(t for t in (shared_note(partner or ""), text) if t)
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
    """What sets a normal chat message apart on Twitch ("" for most): written in another
    channel of a shared chat, bought with channel points, sent with a Power-up, the first
    one of a new chatter, an answer to another message."""
    notes = []
    partner = shared_from(tags)
    if partner is not None:
        notes.append(shared_note(partner))
    kind = tags.get("msg-id", "")
    if kind == "highlighted-message":
        notes.append(_("Highlighted message (channel points)"))
    elif tags.get("custom-reward-id"):
        notes.append(_("Channel points reward"))
    elif kind in ("animated-message", "gigantified-emote-message"):
        notes.append(_("Power-up"))
    elif kind == "user-intro" or tags.get("first-msg") == "1":
        notes.append(_("First message"))
    if parent := tags.get("reply-parent-display-name") or tags.get("reply-parent-user-login"):
        body = " ".join(tags.get("reply-parent-msg-body", "").split())
        if len(body) > MAX_REPLY:
            body = body[:MAX_REPLY - 1].rstrip() + "…"
        notes.append(_("Reply to {user}: {text}").format(user=parent, text=body) if body
                     else _("Reply to {user}").format(user=parent))
    return " · ".join(notes)


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
