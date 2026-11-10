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
    # normalised roles: "broadcaster", "moderator", "vip", "subscriber" (also YouTube members), "verified"
    badges: tuple = ()
    # alert-like events (see alerts.py): "sub", "gift", "raid", "bits", "superchat", "member"
    event: str = ""
    event_value: float = 0  # months, gifted subs, raid viewers, bits, Super Chat amount
    event_text: str = ""  # tier, recipient, "€5.00", "Kicks" …
    # which connection it came from: "platform:channel" as entered (set by the UI per reader,
    # needed because one platform can have several channels – YouTube's channel is a video ID)
    source: str = ""

    def __post_init__(self) -> None:
        # Everything here comes from a platform's servers. They limit lengths themselves
        # (200-500 characters), but nothing downstream (chat view, overlay, history, notes)
        # should depend on that: clamp once, here.
        self.user = _CONTROL_RE.sub("", _SPOOF_RE.sub("", str(self.user)))[:MAX_USER].strip() or "?"
        self.text = _clean_text(self.text)[:MAX_TEXT]
        self.event_text = _clean_text(self.event_text)[:MAX_EVENT_TEXT]
        if self.parts:
            self.parts = _clean_parts(self.parts)
        if not self.parts and (self.text or not self.event):  # an event may come without a message
            self.parts = [("text", self.text, "")]
        # name colours come from the platforms; only accept #RRGGBB (ends up in HTML/CSS)
        if self.color and not _COLOR_RE.fullmatch(self.color):
            self.color = None


MAX_USER = 100  # characters of a display name (YouTube allows 100)
MAX_TEXT = 2000  # characters of one message (the platforms allow 200-500)
MAX_EVENT_TEXT = 300
MAX_PARTS = 600  # text pieces and emotes of one message
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # control characters (not tab/newline)
# direction overrides/isolates: in a name they can make it read as another name
_SPOOF_RE = re.compile("[\u202a-\u202e\u2066-\u2069\r\n\t]")


def _clean_text(value) -> str:
    return _CONTROL_RE.sub("", value if isinstance(value, str) else str(value or ""))


def _clean_parts(parts) -> list:
    """Parts as (kind, value, alt) string triples, within MAX_PARTS and MAX_TEXT in total."""
    out, room = [], MAX_TEXT
    for part in list(parts)[:MAX_PARTS]:
        try:
            kind, value, alt = part
        except (TypeError, ValueError):
            continue
        if kind == "text":
            value = _clean_text(value)[:room]
            room -= len(value)
            if value:
                out.append(("text", value, ""))
        elif kind == "emote" and isinstance(value, str) and len(value) <= 2048:
            alt = _clean_text(alt)[:100]
            room -= len(alt)
            out.append(("emote", value, alt))
        if room <= 0:
            break
    return out


ROLE_NAMES = {
    # Twitch badge set names, Kick badge types, YouTube icon types
    "broadcaster": "broadcaster", "owner": "broadcaster",
    "moderator": "moderator", "lead_moderator": "moderator",
    "vip": "vip",
    "subscriber": "subscriber", "founder": "subscriber", "sub_gifter": None, "member": "subscriber",
    "verified": "verified", "partner": "verified",
}


def roles(names) -> tuple:
    """Platform badge names → sorted, de-duplicated roles (unknown badges are dropped)."""
    out = {ROLE_NAMES.get(str(n).lower()) for n in names}
    out.discard(None)
    return tuple(sorted(out))


class TTLCache:
    """Thread-safe answers for a few seconds. Chat and video tile of one channel ask the
    platform the same question at nearly the same time (Kick channel info, "is the YouTube
    channel live?") – one request instead of two, fewer chances to be rate-limited."""

    def __init__(self, ttl: float, size: int = 64) -> None:
        import threading

        self.ttl, self.size = ttl, size
        self._data: dict = {}
        self._lock = threading.Lock()
        self._busy: dict = {}  # key → lock held while that key is being loaded

    def get(self, key):
        """(True, value) if fresh, else (False, None)."""
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > time.monotonic():
                return True, hit[1]
            return False, None

    def fetch(self, key, load: Callable):
        """The cached answer, or load() it – once: whoever asks while it loads waits for that
        answer instead of sending the same request (chat and video tile start together).
        Errors are not cached. Blocking: call from a thread."""
        import threading

        with self._lock:
            busy = self._busy.get(key)
            if busy is None:
                if len(self._busy) > 4 * self.size:  # forget idle keys
                    self._busy = {k: v for k, v in self._busy.items() if v.locked()}
                busy = self._busy[key] = threading.Lock()
        with busy:
            fresh, value = self.get(key)
            if fresh:
                return value
            value = load()
            self.put(key, value)
            return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def put(self, key, value) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self.ttl, value)
            while len(self._data) > self.size:
                self._data.pop(next(iter(self._data)))


def user_key(m: "ChatMessage") -> str:
    """Stable key for a chatter: platform user ID, else the lower-case name."""
    return m.user_id or m.user.lower()


@dataclass
class Deletion:
    platform: str
    msg_id: str | None = None
    user_id: str | None = None
    clear_all: bool = False
    source: str = ""  # like ChatMessage.source – a ban in one channel must not touch another


def source_key(platform: str, channel: str) -> str:
    """"twitch:somechannel" – identifies one connection."""
    return f"{platform}:{channel}"


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
