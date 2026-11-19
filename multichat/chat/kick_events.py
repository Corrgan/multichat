# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""What Kick's website events mean beyond chat messages: subs, gifts, hosts, Kicks,
redeemed rewards, pinned messages, polls, bans. Pure functions over the event data;
kick.py reads the socket and builds the messages. The events are undocumented."""
from __future__ import annotations

import logging
from collections import OrderedDict

from ..core.i18n import _
from .twitch_events import duration_text

log = logging.getLogger("multichat.chat")

def kick_event(name: str, data: dict) -> tuple[str, str, float, str] | None:
    """Kick's subscription, gift, host and Kicks events → (user, event, value, text).
    These are unofficial website events; unknown ones are logged in the verbose log."""
    def num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    if name.endswith("\\SubscriptionEvent") or name == "SubscriptionEvent":
        return str(data.get("username") or "?"), "sub", num(data.get("months")) or 1, ""
    if name.endswith("GiftedSubscriptionsEvent"):
        gifted = data.get("gifted_usernames") or []
        n = len(gifted) if isinstance(gifted, list) else 0
        text = str(gifted[0]) if n == 1 else ""
        return str(data.get("gifter_username") or "?"), "gift", n or 1, text
    if name.endswith("StreamHostEvent"):
        return str(data.get("host_username") or "?"), "raid", num(data.get("number_viewers")), ""
    if name.endswith("KicksGifted"):
        sender = data.get("sender") or {}
        gift = data.get("gift") or {}
        return str(sender.get("username") or "?"), "bits", num(gift.get("amount")), "Kicks"
    if not name.endswith(("ChatMessageEvent", "MessageDeletedEvent", "UserBannedEvent", "ChatroomClearEvent",
                          "RewardRedeemedEvent", "PinnedMessageCreatedEvent", "PollUpdateEvent")):
        log.debug("Kick event %s: %s", name, str(data)[:300])
    return None


_polls_shown: OrderedDict[tuple[str, str], None] = OrderedDict()  # a poll sends an update per vote
_predictions_shown: OrderedDict[tuple[str, str], None] = OrderedDict()  # (prediction ID, state) – an update per bet


def _first(store: OrderedDict, key) -> bool:
    """True the first time `key` is seen (the last 50 are remembered)."""
    if key in store:
        return False
    store[key] = None
    while len(store) > 50:
        store.popitem(last=False)
    return True


def kick_notice(name: str, data: dict, slug: str) -> tuple[str, str, str] | None:
    """What else Kick shows in its chat → (user, headline, message): redeemed rewards,
    pinned messages, polls, predictions (start and result), goals reached. These website events are undocumented – anything that does
    not look as expected is left to the verbose log (kick_event) instead of guessed at."""
    def text(value) -> str:
        return value.strip() if isinstance(value, str) else ""

    if name.endswith("RewardRedeemedEvent"):
        user, reward = text(data.get("username")), text(data.get("reward_title"))
        if user and reward:
            return user, _("{user} redeemed {reward}").format(user=user, reward=reward), text(data.get("user_input"))
    elif name.endswith("PinnedMessageCreatedEvent"):
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
        user, content = text(sender.get("username")), text(message.get("content"))
        if user and content:
            return user, _("Pinned message"), content
    elif name.endswith("PollUpdateEvent"):
        poll = data.get("poll") if isinstance(data.get("poll"), dict) else {}
        title = text(poll.get("title"))
        options = [text(o.get("label")) for o in poll.get("options") or [] if isinstance(o, dict)]
        options = [o for o in options if o]
        if title and (slug, title) not in _polls_shown:
            _polls_shown[(slug, title)] = None
            while len(_polls_shown) > 50:
                _polls_shown.popitem(last=False)
            return "Kick", _("Poll: {title}").format(title=title), " · ".join(options)
    elif name.endswith(("PredictionCreated", "PredictionUpdated")):  # predictions-channel-<channel ID>
        p = data.get("prediction") if isinstance(data.get("prediction"), dict) else {}
        pid, title, state = text(p.get("id")), text(p.get("title")) or "?", text(p.get("state")).upper()
        outcomes = {text(o.get("id")): text(o.get("title")) for o in p.get("outcomes") or [] if isinstance(o, dict)}
        # every bet sends an update: one line when it starts (also when MultiChat connects during it) and one at the end
        if pid and state == "ACTIVE" and _first(_predictions_shown, (pid, "ACTIVE")):
            options = " / ".join(o for o in outcomes.values() if o)
            return "Kick", (_("Prediction: {title} – {options}").format(title=title, options=options) if options
                            else _("Prediction: {title}").format(title=title)), ""
        if pid and state == "RESOLVED" and _first(_predictions_shown, (pid, "END")):
            winner = outcomes.get(text(p.get("winning_outcome_id")), "")
            return "Kick", (_("Prediction ended: {title} – winner: {winner}").format(title=title, winner=winner) if winner
                            else _("Prediction ended: {title}").format(title=title)), ""
        if pid and state in ("CANCELLED", "CANCELED") and _first(_predictions_shown, (pid, "END")):
            return "Kick", _("Prediction canceled: {title}").format(title=title), ""
    elif name.endswith("GoalAchievedEvent"):  # channel_<channel ID>
        target = data.get("target_value")
        kind = text(data.get("type")).replace("_", " ")
        what = f"{target} {kind}".strip() if isinstance(target, int) and target > 0 else kind
        if _first(_predictions_shown, (text(data.get("id")) or what, "GOAL")):
            return "Kick", _("Goal reached: {goal}").format(goal=what) if what else _("Goal reached"), ""
    return None


def kick_vote(name: str, data: dict) -> dict | None:
    """The poll or prediction an event starts, for voting from MultiChat (see ChatMessage.vote)."""
    if name.endswith("PollUpdateEvent"):
        poll = data.get("poll") if isinstance(data.get("poll"), dict) else {}
        options = [[str(o.get("id")), str(o.get("label") or "").strip()] for o in poll.get("options") or []
                   if isinstance(o, dict) and isinstance(o.get("id"), int)]
        return {"kind": "poll", "id": "", "options": options} if len(options) >= 2 else None
    if name.endswith(("PredictionCreated", "PredictionUpdated")):
        p = data.get("prediction") if isinstance(data.get("prediction"), dict) else {}
        options = [[str(o.get("id")), str(o.get("title") or "").strip()] for o in p.get("outcomes") or []
                   if isinstance(o, dict) and isinstance(o.get("id"), str) and o["id"]]
        if str(p.get("state") or "").upper() == "ACTIVE" and isinstance(p.get("id"), str) and len(options) >= 2:
            return {"kind": "prediction", "id": p["id"], "options": options}
    return None


def ban_notice(data: dict) -> str:
    """UserBannedEvent → "X was banned" / "X was timed out for 10 min" ("" if no name)."""
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    name = user.get("username") or user.get("slug")
    if not isinstance(name, str) or not name.strip():
        return ""
    minutes = data.get("duration")
    if data.get("permanent") or not isinstance(minutes, (int, float)) or minutes <= 0:
        return _("{user} was banned").format(user=name.strip())
    return _("{user} was timed out for {duration}").format(user=name.strip(), duration=duration_text(int(minutes) * 60))
