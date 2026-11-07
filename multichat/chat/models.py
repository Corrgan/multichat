# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Common data types of the chat readers."""
from __future__ import annotations

import itertools
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Union

_uid = itertools.count(1)
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")

# A message part: ("text", text, "") or ("emote", image_url, emote_name)
Part = tuple


@dataclass
class ChatMessage:
    platform: str  # "twitch" | "kick" | "youtube"
    channel: str
    user: str
    text: str
    parts: list = field(default_factory=list)
    color: str | None = None
    msg_id: str | None = None  # platform ID (for deleting)
    user_id: str | None = None  # platform ID (for timeout/ban)
    timestamp: float = field(default_factory=time.time)
    uid: int = field(default_factory=lambda: next(_uid))  # local ID
    deleted: bool = False

    def __post_init__(self) -> None:
        if not self.parts:
            self.parts = [("text", self.text, "")]
        # name colours come from the platforms; only accept #RRGGBB (ends up in HTML/CSS)
        if self.color and not _COLOR_RE.fullmatch(self.color):
            self.color = None


@dataclass
class Deletion:
    platform: str
    msg_id: str | None = None
    user_id: str | None = None
    clear_all: bool = False


Event = Union[ChatMessage, Deletion]
Emit = Callable[[Event], None]
Status = Callable[..., None]  # status(platform, template, **values)


class Backoff:
    """Reconnect delays: 5 s, 10 s, 20 s … up to 2 min, with some jitter so several
    clients don't reconnect in lockstep. A connection that held for a while resets it."""

    def __init__(self, first: float = 5, maximum: float = 120, stable: float = 60) -> None:
        self.first, self.maximum, self.stable = first, maximum, stable
        self.failures = 0
        self.started = time.monotonic()

    def connected(self) -> None:
        self.started = time.monotonic()

    def next(self) -> int:
        if time.monotonic() - self.started >= self.stable:
            self.failures = 0
        delay = min(self.maximum, self.first * 2**self.failures)
        self.failures += 1
        self.started = time.monotonic()
        return max(1, round(delay * random.uniform(0.85, 1.15)))


_TWITCH_RE = re.compile(r"^(?:(?:https?://)?(?:www\.|m\.)?twitch\.tv/)?[#@]?([a-z0-9_]{1,25})/?$")
_KICK_RE = re.compile(r"^(?:(?:https?://)?(?:www\.)?kick\.com/)?@?([a-z0-9_-]{1,64})/?$")


def twitch_channel(value: str) -> str:
    """Twitch login from a name, #name or twitch.tv URL; ValueError if invalid."""
    m = _TWITCH_RE.match(value.strip().lower())
    if not m:
        raise ValueError(f"invalid Twitch channel name: {value!r}")
    return m.group(1)


def kick_channel(value: str) -> str:
    """Kick slug from a name or kick.com URL; ValueError if invalid."""
    m = _KICK_RE.match(value.strip().lower())
    if not m:
        raise ValueError(f"invalid Kick channel name: {value!r}")
    return m.group(1)


def with_extra_emotes(emotes, platform: str, channel: str, parts: list[Part]) -> list[Part]:
    """Insert 7TV/BTTV/FFZ emotes if an EmoteStore was given."""
    return emotes.apply(platform, channel, parts) if emotes else parts
