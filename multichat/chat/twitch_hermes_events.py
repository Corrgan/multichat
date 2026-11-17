# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""What the Twitch website's own event feed (Hermes, see twitch_hermes.py) means in the
chat: polls, predictions and hype trains of any channel, as one line of text each – the
same lines EventSub gives for your own channel. The payloads are undocumented: they look
like the old PubSub ones (seen for hype trains, assumed for polls and predictions), and
whatever doesn't look as expected is left out ("")."""
from __future__ import annotations

from collections import OrderedDict

from ..core.i18n import _
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
)

# Hermes topic (without ".<channel ID>") → kind
TOPICS = {"polls": "poll", "predictions-channel-v1": "prediction", "hype-train-events-v2": "hype_train"}
REMEMBER = 200  # predictions/hype trains whose state is kept (every bet and every sub resends them)


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

    def text(self, kind: str, msg) -> str:
        if not isinstance(msg, dict):
            return ""
        if kind == "poll":
            return poll_text(msg)
        if kind == "prediction":
            return self.prediction_text(msg)
        if kind == "hype_train":
            return self.hype_train_text(msg)
        return ""

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
