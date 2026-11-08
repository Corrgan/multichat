# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Alert-like chat events (subs, gifts, raids, bits, Super Chats, memberships).

The readers set ChatMessage.event/event_value/event_text; this module turns them into a
headline and decides – by the user's own settings – which ones are highlighted. What the
streamer configured as alerts in StreamElements/Streamlabs isn't visible to other apps,
so minimum values (e.g. "bits from 100") stand in for it.
"""
from __future__ import annotations

import re

from ..core.config import DEFAULT_ALERTS  # noqa: F401 – re-exported
from ..core.i18n import _
from .models import ChatMessage

EVENTS = ("sub", "gift", "raid", "bits", "superchat", "member")
# background of the highlighted line, colour of the headline
COLORS = {
    "sub": ("#2a1f45", "#bf94ff"), "gift": ("#2a1f45", "#bf94ff"), "member": ("#173322", "#5fd38d"),
    "raid": ("#3a2a10", "#ffb84d"), "bits": ("#381c3c", "#e88ef0"), "superchat": ("#3a2a10", "#ffcc4d"),
}
_NUMBER_RE = re.compile(r"\d[\d.,\s]*")


def parse_amount(text: str) -> float:
    """"€5.00" / "$1,000.00" / "5,00 €" / "¥1,000" → number (currency ignored)."""
    m = _NUMBER_RE.search(text or "")
    if not m:
        return 0.0
    num = re.sub(r"\s", "", m.group()).rstrip(".,")
    if "," in num and "." in num:  # the later one is the decimal mark
        num = num.replace(",", "") if num.rfind(".") > num.rfind(",") else num.replace(".", "").replace(",", ".")
    elif "," in num:  # "5,00" → decimal; "1,000" → thousands
        num = num.replace(",", ".") if len(num.rsplit(",", 1)[1]) != 3 else num.replace(",", "")
    try:
        return float(num)
    except ValueError:
        return 0.0


def _n(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def headline(m: ChatMessage) -> str:
    """Translated one-liner for an event, "" for normal messages."""
    user, v, t = m.user, m.event_value, m.event_text
    if m.event == "sub":
        text = (_("{user} resubscribed ({months} months)").format(user=user, months=_n(v)) if v > 1
                else _("{user} subscribed").format(user=user))
        return f"{text} · {t}" if t else text
    if m.event == "gift":
        if v <= 1 and t:
            return _("{user} gifted a sub to {recipient}").format(user=user, recipient=t)
        return _("{user} gifted {n} subs").format(user=user, n=_n(max(v, 1)))
    if m.event == "raid":
        return _("{user} is raiding with {n} viewers").format(user=user, n=_n(v))
    if m.event == "bits":
        if t:  # Kick: "Kicks"
            return _("{user} sent {n} {currency}").format(user=user, n=_n(v), currency=t)
        return _("{user} cheered {n} bits").format(user=user, n=_n(v))
    if m.event == "superchat":
        return _("{user} sent a Super Chat: {amount}").format(user=user, amount=t or _n(v))
    if m.event == "member":
        return (_("{user} has been a member for {months} months").format(user=user, months=_n(v)) if v > 1
                else _("{user} became a member").format(user=user))
    return ""


def is_alert(m: ChatMessage, cfg: dict) -> bool:
    """Highlight this event? Disabled kinds and values below the minimum are shown normally."""
    if m.event not in EVENTS or not cfg.get(m.event, True):
        return False
    minimum = {"bits": "min_bits", "gift": "min_gift", "raid": "min_raid", "superchat": "min_superchat"}.get(m.event)
    return not minimum or m.event_value >= (cfg.get(minimum) or 0)
