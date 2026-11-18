# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""What the Twitch website's own event feed (Hermes, see twitch_hermes.py) means in the
chat: polls, predictions, hype trains, channel points, outgoing raids, shoutouts, reached
goals, pinned messages, clip previews and shared chat sessions of any channel, as one line
of text each – for polls, predictions, hype trains and channel points the same lines
EventSub gives for your own channel. The payloads are undocumented: they look like the old
PubSub ones (all seen in a night of recording, 17/18 Nov 2026), and whatever doesn't look
as expected is left out ("")."""
from __future__ import annotations

from collections import OrderedDict

from ..core.i18n import _
from .twitch_events import remember_room
from .twitch_eventsub_events import (
    MAX_OPTIONS,
    _int,
    _str,
    hype_train_begin_text,
    hype_train_end_text,
    poll_begin_text,
    poll_end_text,
    prediction_begin_text,
    prediction_end_text,
    prediction_lock_text,
    redemption_text,
)

# Hermes topic (without ".<channel ID>") → kind
TOPICS = {"polls": "poll", "predictions-channel-v1": "prediction", "hype-train-events-v2": "hype_train",
          "community-points-channel-v1": "redemption", "raid": "raid", "shoutout": "shoutout",
          "creator-goals-events-v1": "goal", "pinned-chat-updates-v1": "pin", "stream-chat-room-v1": "embed",
          "shared-chat-channel-v1": "shared_chat"}
REMEMBER = 200  # predictions/hype trains/raids/goals whose state is kept (they are sent again and again)
MAX_PIN = 200  # characters of a pinned message shown


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value[:MAX_OPTIONS] if isinstance(value, list) else []


def _remember(store: OrderedDict, key: str, value) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > REMEMBER:
        store.popitem(last=False)


def poll_text(msg: dict) -> str:
    """POLL_CREATE → the poll and its choices, POLL_COMPLETE/POLL_TERMINATE → the result;
    the vote updates in between and the archiving afterwards show nothing."""
    kind = msg.get("type")
    poll = _dict(_dict(msg.get("data")).get("poll"))
    if not poll:
        return ""
    if kind == "POLL_CREATE":
        return poll_begin_text(poll)
    if kind in ("POLL_COMPLETE", "POLL_TERMINATE"):
        choices = [{"title": c.get("title"), "votes": _dict(c.get("votes")).get("total")}
                   for c in _list(poll.get("choices")) if isinstance(c, dict)]
        status = "completed" if kind == "POLL_COMPLETE" else "terminated"
        return poll_end_text({"title": poll.get("title"), "status": status, "choices": choices})
    return ""


class HermesEvents:
    """Turns the messages into lines. Predictions and hype trains resend their whole state
    on every bet or contribution: only a change of status (prediction) or level (hype train)
    is a line, so this keeps what was shown last – one instance per connection."""

    def __init__(self) -> None:
        self.predictions: OrderedDict[str, str] = OrderedDict()  # prediction ID → last status shown
        self.trains: OrderedDict[str, int] = OrderedDict()  # hype train ID → level reached
        self.once: OrderedDict[str, bool] = OrderedDict()  # raid/goal IDs already shown

    def text(self, kind: str, msg) -> str:
        if not isinstance(msg, dict):
            return ""
        if kind == "poll":
            return poll_text(msg)
        if kind == "prediction":
            return self.prediction_text(msg)
        if kind == "hype_train":
            return self.hype_train_text(msg)
        if kind == "redemption":
            return redemption_line(msg)
        if kind == "raid":
            return self.raid_text(msg)
        if kind == "shoutout":
            return shoutout_text(msg)
        if kind == "goal":
            return self.goal_text(msg)
        if kind == "pin":
            return pin_text(msg)
        if kind == "embed":
            return embed_text(msg)
        if kind == "shared_chat":
            return shared_chat_text(msg)
        return ""

    def _first(self, key: str) -> bool:
        """True the first time key is seen."""
        if not key or key in self.once:
            return False
        _remember(self.once, key, True)
        return True

    def raid_text(self, msg: dict) -> str:
        """raid_go_v2 (sent several times per raid) → "Raiding X with N viewers"; the countdown
        updates before it show nothing."""
        raid = _dict(msg.get("raid"))
        target = _str(raid.get("target_display_name")) or _str(raid.get("target_login"))
        if msg.get("type") != "raid_go_v2" or not target or not self._first("raid:" + _str(raid.get("id"))):
            return ""
        return _("Raiding {channel} with {n} viewers").format(channel=target, n=_int(raid.get("viewer_count")))

    def goal_text(self, msg: dict) -> str:
        """goal_achieved → one line; created, updated and finished goals show nothing."""
        goal = _dict(_dict(msg.get("data")).get("goal"))
        if msg.get("type") != "goal_achieved" or not self._first("goal:" + _str(goal.get("id"))):
            return ""
        what = _str(goal.get("description"))
        if not what:
            target = _int(goal.get("targetContributions"))
            kind = _str(goal.get("contributionType")).lower().replace("_", " ")
            what = f"{target} {kind}".strip() if target else kind
        return _("Goal reached: {goal}").format(goal=what) if what else _("Goal reached")

    def prediction_text(self, msg: dict) -> str:
        event = _dict(_dict(msg.get("data")).get("event"))
        pid, status = _str(event.get("id")), _str(event.get("status")).upper()
        if not pid or self.predictions.get(pid) == status:
            return ""
        texts = {"ACTIVE": prediction_begin_text, "LOCKED": prediction_lock_text,
                 "RESOLVED": lambda e: prediction_end_text({**e, "status": "resolved"}),
                 "CANCELED": lambda e: prediction_end_text({**e, "status": "canceled"})}
        if status not in texts:  # RESOLVE_PENDING, CANCEL_PENDING …: wait for the final state
            return ""
        _remember(self.predictions, pid, status)
        return texts[status](event)

    def hype_train_text(self, msg: dict) -> str:
        """Seen: hype-train-progression (one per contribution, with the level) and hype-train-end
        (without it). A train that shows up at level 1 has just started; one first seen higher
        was running already when the channel was connected and only shows its next levels."""
        kind = _str(msg.get("type"))
        data = _dict(msg.get("data"))
        tid = _str(data.get("id"))
        if not tid:
            return ""
        if kind == "hype-train-end":
            level = self.trains.pop(tid, 0)
            return hype_train_end_text({"level": level}) if level else _("Hype Train ended")
        level = _int(_dict(_dict(data.get("progress")).get("level")).get("value"))
        if level <= 0:
            return ""
        known = self.trains.get(tid)
        if known is not None and level <= known:
            return ""
        _remember(self.trains, tid, level)
        if known is None:
            return hype_train_begin_text({"level": level}) if level == 1 or "start" in kind else ""
        return _("Hype Train level {level}").format(level=level)


def redemption_line(msg: dict) -> str:
    """reward-redeemed → "X redeemed Y"; with a text input it is a chat message over IRC already."""
    red = _dict(_dict(msg.get("data")).get("redemption"))
    if msg.get("type") != "reward-redeemed" or not red:
        return ""
    user = _dict(red.get("user"))
    return redemption_text({"user_name": user.get("display_name"), "user_login": user.get("login"),
                            "user_input": red.get("user_input"), "reward": _dict(red.get("reward"))})


def shoutout_text(msg: dict) -> str:
    data = _dict(msg.get("data"))
    target = _str(data.get("targetUserDisplayName")) or _str(data.get("targetLogin"))
    if msg.get("type") != "create" or not target:
        return ""
    return _("Shoutout to {channel}").format(channel=target)


def pin_text(msg: dict) -> str:
    """pin-message → who pinned what; unpin-message → that it is gone; update-message (only
    a new end time) shows nothing."""
    kind = msg.get("type")
    data = _dict(msg.get("data"))
    if kind == "unpin-message":
        return _("Pinned message removed")
    if kind != "pin-message":
        return ""
    text = " ".join(_str(_dict(_dict(data.get("message")).get("content")).get("text")).split())
    if not text:
        return ""
    if len(text) > MAX_PIN:
        text = text[:MAX_PIN - 1].rstrip() + "…"
    by = _dict(data.get("pinned_by"))
    user = _str(by.get("display_name")) or _str(by.get("login"))
    return (_("{user} pinned: {text}").format(user=user, text=text) if user
            else _("Pinned: {text}").format(text=text))


def embed_text(msg: dict) -> str:
    """chat_rich_embed (Twitch's preview of a link posted in the chat) → the clip's title and
    channel; other room updates show nothing."""
    data = _dict(msg.get("data"))
    title = " ".join(_str(data.get("title")).split())
    if msg.get("type") != "chat_rich_embed" or not title:
        return ""
    clip = _dict(_dict(data.get("twitch_metadata")).get("clip_metadata"))
    channel = _str(clip.get("channel_display_name"))
    if clip:
        return (_("Clip: {title} ({channel})").format(title=title, channel=channel) if channel
                else _("Clip: {title}").format(title=title))
    return _("Link: {title}").format(title=title)


def shared_chat_text(msg: dict) -> str:
    """Every session message names its channels (kept for "Shared chat: <channel>" on the
    lines from the others); started and ended are a line of their own."""
    session = _dict(_dict(msg.get("data")).get("session"))
    names = []
    participants = session.get("participants")
    for p in participants[:MAX_OPTIONS] if isinstance(participants, list) else []:
        if isinstance(p, dict) and (name := _str(p.get("channel_display_name")) or _str(p.get("channel_login"))):
            remember_room(_str(p.get("channel_id")), name)
            if _str(p.get("status")).upper() == "ACTIVE":
                names.append(name)
    kind = msg.get("type")
    if kind == "session-started" and names:
        return _("Shared chat started: {channels}").format(channels=", ".join(names))
    if kind == "session-ended":
        return _("Shared chat ended")
    return ""
