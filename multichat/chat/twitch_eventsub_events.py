# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""What Twitch's EventSub notifications mean in the chat: follows, channel point
redemptions, polls, predictions, hype trains and ad breaks as one line of text each.
Pure functions over the event payload; twitch_eventsub.py reads the socket and builds
the messages. A missing or malformed field never raises: the line is left out ("") or
shows a placeholder."""
from __future__ import annotations

from ..core.i18n import _

# subscription type → (version, needs the broadcaster itself rather than a moderator)
SUBSCRIPTIONS: dict[str, tuple[str, bool]] = {
    "channel.follow": ("2", False),
    "channel.channel_points_custom_reward_redemption.add": ("1", True),
    "channel.poll.begin": ("1", True),
    "channel.poll.end": ("1", True),
    "channel.prediction.begin": ("1", True),
    "channel.prediction.lock": ("1", True),
    "channel.prediction.end": ("1", True),
    "channel.hype_train.begin": ("2", True),
    "channel.hype_train.end": ("2", True),
    "channel.ad_break.begin": ("1", True),
}
MAX_OPTIONS = 10  # a poll has at most 5 choices, a prediction 10 outcomes; don't trust it


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _user(event: dict, prefix: str = "user") -> str:
    return _str(event.get(f"{prefix}_name")) or _str(event.get(f"{prefix}_login"))


def _titles(items) -> list[str]:
    """The titles of the choices/outcomes that have one."""
    if not isinstance(items, list):
        return []
    return [t for i in items[:MAX_OPTIONS] if isinstance(i, dict) and (t := _str(i.get("title")))]


def follow_text(event: dict) -> str:
    user = _user(event)
    return _("{user} followed").format(user=user) if user else ""


def redemption_text(event: dict) -> str:
    """Redemptions with a text input arrive as chat messages over IRC already (they carry
    custom-reward-id): only those without one are a line of their own, so nothing shows twice."""
    if _str(event.get("user_input")):
        return ""
    reward = event.get("reward")
    title = _str(reward.get("title")) if isinstance(reward, dict) else ""
    user = _user(event)
    if not user or not title:
        return ""
    return _("{user} redeemed {reward}").format(user=user, reward=title)


def poll_begin_text(event: dict) -> str:
    title = _str(event.get("title")) or "?"
    options = _titles(event.get("choices"))
    if not options:
        return _("Poll: {title}").format(title=title)
    return _("Poll: {title} – {options}").format(title=title, options=" / ".join(options))


def poll_end_text(event: dict) -> str:
    title = _str(event.get("title")) or "?"
    votes = {}
    for c in (event.get("choices") if isinstance(event.get("choices"), list) else [])[:MAX_OPTIONS]:
        if isinstance(c, dict) and _str(c.get("title")):
            votes[_str(c["title"])] = _int(c.get("votes"))
    top = max(votes.values(), default=0)
    if event.get("status") != "completed" or top <= 0:  # terminated, archived or nobody voted
        return _("Poll ended: {title}").format(title=title)
    winners = " / ".join(t for t, v in votes.items() if v == top)
    return _("Poll ended: {title} – winner: {winner} ({votes} votes)").format(title=title, winner=winners, votes=top)


def prediction_begin_text(event: dict) -> str:
    title = _str(event.get("title")) or "?"
    options = _titles(event.get("outcomes"))
    if not options:
        return _("Prediction: {title}").format(title=title)
    return _("Prediction: {title} – {options}").format(title=title, options=" / ".join(options))


def prediction_lock_text(event: dict) -> str:
    return _("Prediction locked: {title}").format(title=_str(event.get("title")) or "?")


def prediction_end_text(event: dict) -> str:
    title = _str(event.get("title")) or "?"
    winning = event.get("winning_outcome_id")
    outcomes = event.get("outcomes") if isinstance(event.get("outcomes"), list) else []
    winner = next((_str(o.get("title")) for o in outcomes[:MAX_OPTIONS]
                   if isinstance(o, dict) and winning and o.get("id") == winning), "")
    if event.get("status") == "resolved" and winner:
        return _("Prediction ended: {title} – winner: {winner}").format(title=title, winner=winner)
    if event.get("status") == "canceled":
        return _("Prediction canceled: {title}").format(title=title)
    return _("Prediction ended: {title}").format(title=title)


def hype_train_begin_text(event: dict) -> str:
    return _("Hype Train started (level {level})").format(level=_int(event.get("level")) or 1)


def hype_train_end_text(event: dict) -> str:
    return _("Hype Train ended (level {level})").format(level=_int(event.get("level")) or 1)


def ad_break_text(event: dict) -> str:
    seconds = _int(event.get("duration_seconds"))  # the docs' example has it as a string
    return _("Ad break ({n} s)").format(n=seconds) if seconds > 0 else ""


_TEXTS = {
    "channel.follow": follow_text,
    "channel.channel_points_custom_reward_redemption.add": redemption_text,
    "channel.poll.begin": poll_begin_text,
    "channel.poll.end": poll_end_text,
    "channel.prediction.begin": prediction_begin_text,
    "channel.prediction.lock": prediction_lock_text,
    "channel.prediction.end": prediction_end_text,
    "channel.hype_train.begin": hype_train_begin_text,
    "channel.hype_train.end": hype_train_end_text,
    "channel.ad_break.begin": ad_break_text,
}


def eventsub_text(sub_type: str, event) -> str:
    """Notification payload → the line to show ("" for nothing: unknown type, no usable fields,
    a redemption that came as a chat message)."""
    fn = _TEXTS.get(sub_type)
    return fn(event) if fn and isinstance(event, dict) else ""
