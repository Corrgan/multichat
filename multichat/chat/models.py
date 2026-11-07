# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Common data types of the chat readers."""
from __future__ import annotations

import itertools
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


def with_extra_emotes(emotes, platform: str, channel: str, parts: list[Part]) -> list[Part]:
    """Insert 7TV/BTTV/FFZ emotes if an EmoteStore was given."""
    return emotes.apply(platform, channel, parts) if emotes else parts
