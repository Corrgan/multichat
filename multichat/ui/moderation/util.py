# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Small helpers for the user card: role names, profile links, dates."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from PyQt6.QtCore import QUrl

from ...chat.models import ChatMessage
from ...core.i18n import _


def role_label(role: str) -> str:
    return {
        "broadcaster": _("Broadcaster"), "moderator": _("Moderator"), "vip": "VIP",
        "subscriber": _("Subscriber / member"), "verified": _("Verified"),
    }.get(role, role)


def profile_url(m: ChatMessage) -> QUrl | None:
    """Link to the user's page – built from a fixed prefix, the name is URL-quoted."""
    if m.platform == "twitch":
        return QUrl("https://www.twitch.tv/" + quote(m.user.lower(), safe=""))
    if m.platform == "kick":
        return QUrl("https://kick.com/" + quote(m.user.lower(), safe=""))
    if m.platform == "youtube" and m.user_id:
        return QUrl("https://www.youtube.com/channel/" + quote(m.user_id, safe=""))
    return None


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def duration_text(days: int) -> str:
    if days < 60:
        return _("{n} days").format(n=days)
    if days < 730:
        return _("{n} months").format(n=days // 30)
    return _("{n} years").format(n=days // 365)


def since_text(value, now: datetime | None = None) -> str:
    """ISO time → "2021-03-04 (3 years)"; "" if unknown."""
    dt = _parse_time(value)
    if not dt:
        return ""
    now = now or datetime.now(timezone.utc)
    try:
        days = max(0, (now - dt).days)
        return f"{dt.astimezone().strftime('%Y-%m-%d')} ({duration_text(days)})"
    except (OverflowError, ValueError, OSError):  # nonsense dates from an API
        return ""
