# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""The running state of a poll or prediction (PollState) from what each platform sends – for
the box above the chat that shows it like the website does (ui/poll_box.py). The lines in the
chat stay as they are; these are pure functions over the event data, the readers emit the result.

  Twitch   Hermes (website event feed): polls POLL_CREATE/UPDATE/COMPLETE/TERMINATE/ARCHIVE,
           predictions event-created/event-updated; EventSub (your own channel): channel.poll.*,
           channel.prediction.*
  Kick     PollUpdateEvent (one per vote, with the counts), PredictionCreated/PredictionUpdated
  YouTube  showLiveChatActionPanelAction / updateLiveChatPollAction (percentages only),
           closeLiveChatActionPanelAction / removeBannerForLiveChatCommand
"""
from __future__ import annotations

import datetime
import math
import time

from .models import PollState


def _str(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _num(v) -> float:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0  # json reads 1e400 as inf and accepts NaN


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _ts(iso) -> float:
    """ISO time ("2026-11-17T21:32:39.0096Z") → Unix time, 0 if unreadable."""
    try:
        return datetime.datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _options(items, oid, label, value) -> list:
    out = []
    for o in _list(items):
        if isinstance(o, dict) and _str(o.get(label)):
            out.append([str(o.get(oid) if o.get(oid) is not None else len(out)), _str(o.get(label)), _num(value(o))])
    return out


# ---- Twitch ---------------------------------------------------------------- #
_TWITCH_POLL_STATUS = {"ACTIVE": "active", "COMPLETED": "ended", "TERMINATED": "ended", "ARCHIVED": "gone"}
_TWITCH_PRED_STATUS = {"ACTIVE": "active", "LOCKED": "locked", "RESOLVE_PENDING": "locked", "RESOLVED": "ended",
                       "CANCEL_PENDING": "locked", "CANCELED": "ended"}


def twitch_hermes_state(kind: str, msg, channel: str) -> PollState | None:
    """A Hermes message of the polls / predictions-channel-v1 topic → PollState."""
    data = _dict(_dict(msg).get("data"))
    if kind == "poll":
        p = _dict(data.get("poll"))
        status = _TWITCH_POLL_STATUS.get(_str(p.get("status")).upper())
        if not _str(p.get("poll_id")) or not status:
            return None
        start = _ts(p.get("started_at"))
        return PollState("twitch", channel, "poll", _str(p.get("poll_id")), _str(p.get("title")),
                         _options(p.get("choices"), "choice_id", "title", lambda c: _dict(c.get("votes")).get("total")),
                         status, ends_at=start + _num(p.get("duration_seconds")) if start else 0)
    if kind == "prediction":
        e = _dict(data.get("event"))
        status = _TWITCH_PRED_STATUS.get(_str(e.get("status")).upper())
        if not _str(e.get("id")) or not status:
            return None
        start = _ts(e.get("created_at"))
        return PollState("twitch", channel, "prediction", _str(e.get("id")), _str(e.get("title")),
                         _options(e.get("outcomes"), "id", "title", lambda o: o.get("total_points")), status,
                         ends_at=start + _num(e.get("prediction_window_seconds")) if start else 0,
                         winner=_str(e.get("winning_outcome_id")))
    return None


def twitch_eventsub_state(sub_type: str, event, channel: str) -> PollState | None:
    """An EventSub notification channel.poll.* / channel.prediction.* → PollState."""
    e = _dict(event)
    if not _str(e.get("id")):
        return None
    if sub_type.startswith("channel.poll."):
        status = "ended" if sub_type.endswith(".end") else "active"
        if status == "ended" and _str(e.get("status")) == "archived":
            status = "gone"
        return PollState("twitch", channel, "poll", e["id"], _str(e.get("title")),
                         _options(e.get("choices"), "id", "title", lambda c: c.get("votes")), status,
                         ends_at=_ts(e.get("ends_at")))
    if sub_type.startswith("channel.prediction."):
        status = {"begin": "active", "progress": "active", "lock": "locked", "end": "ended"}.get(sub_type.rsplit(".", 1)[-1])
        if not status:
            return None
        return PollState("twitch", channel, "prediction", e["id"], _str(e.get("title")),
                         _options(e.get("outcomes"), "id", "title", lambda o: o.get("channel_points")), status,
                         ends_at=_ts(e.get("locks_at")), winner=_str(e.get("winning_outcome_id")))
    return None


# ---- Kick ------------------------------------------------------------------ #
def kick_state(name: str, data: dict, slug: str) -> PollState | None:
    """PollUpdateEvent (with the counts, on every vote) / PredictionCreated / PredictionUpdated → PollState."""
    if name.endswith("PollUpdateEvent"):
        p = _dict(data.get("poll"))
        if not _str(p.get("title")):
            return None
        left = _num(p.get("remaining"))
        # Kick polls have no ID: the title stands for it (one poll at a time per channel)
        return PollState("kick", slug, "poll", _str(p.get("title")), _str(p.get("title")),
                         _options(p.get("options"), "id", "label", lambda o: o.get("votes")),
                         "active" if left > 0 else "ended", ends_at=time.time() + left if left > 0 else 0)
    if name.endswith(("PredictionCreated", "PredictionUpdated")):
        p = _dict(data.get("prediction"))
        status = {"ACTIVE": "active", "LOCKED": "locked", "RESOLVED": "ended", "CANCELLED": "ended",
                  "CANCELED": "ended"}.get(_str(p.get("state")).upper())
        if not _str(p.get("id")) or not status:
            return None
        start = _ts(p.get("created_at"))
        return PollState("kick", slug, "prediction", p["id"], _str(p.get("title")),
                         _options(p.get("outcomes"), "id", "title", lambda o: o.get("total_vote_amount")), status,
                         ends_at=start + _num(p.get("duration")) if start else 0, winner=_str(p.get("winning_outcome_id")))
    return None


# ---- YouTube --------------------------------------------------------------- #
def youtube_state(a: dict, video_id: str) -> PollState | None:
    """A poll panel/banner action → PollState. YouTube only tells percentages."""
    r = None
    if isinstance(a.get("showLiveChatActionPanelAction"), dict):
        panel = _dict(_dict(a["showLiveChatActionPanelAction"].get("panelToShow")).get("liveChatActionPanelRenderer"))
        r = _dict(_dict(panel.get("contents")).get("pollRenderer"))
    elif isinstance(a.get("updateLiveChatPollAction"), dict):
        r = _dict(_dict(a["updateLiveChatPollAction"].get("pollToUpdate")).get("pollRenderer"))
    elif isinstance(a.get("addBannerToLiveChatCommand"), dict):
        banner = _dict(_dict(a["addBannerToLiveChatCommand"].get("bannerRenderer")).get("liveChatBannerRenderer"))
        r = _dict(_dict(banner.get("contents")).get("pollRenderer"))
    else:
        target = (_dict(a.get("closeLiveChatActionPanelAction")).get("targetPanelId")
                  or _dict(a.get("removeBannerForLiveChatCommand")).get("targetActionId"))
        return PollState("youtube", video_id, "poll", target, "", [], "ended") if _str(target) else None
    if not r or not _str(r.get("liveChatPollId")):
        return None

    def text(o) -> str:
        o = _dict(o)
        return _str(o.get("simpleText")) or "".join(_str(x.get("text")) for x in _list(o.get("runs")) if isinstance(x, dict))

    options = []
    for i, c in enumerate(_list(r.get("choices"))):
        if isinstance(c, dict) and text(c.get("text")):
            options.append([str(i), text(c.get("text")), round(_num(c.get("voteRatio")) * 100)])
    question = text(_dict(_dict(r.get("header")).get("pollHeaderRenderer")).get("pollQuestion"))
    return PollState("youtube", video_id, "poll", r["liveChatPollId"], question, options, "active", percent=True)
