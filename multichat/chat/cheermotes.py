# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Cheermotes: the animated gems and emotes of a Twitch message with bits ("Cheer100").

Twitch serves them from one address pattern: the cheermote's name, the tier the amount
falls into and the size. That covers Twitch's own cheermotes without a login or a request;
a channel's custom cheermote is not known here and stays text. If an image cannot be
loaded the chat shows the word instead, as for any emote.
"""
from __future__ import annotations

import re

from .models import Part

URL = "https://d3aqoihi2n8ty8.cloudfront.net/actions/{name}/dark/animated/{tier}/2.gif"
TIERS = (10000, 5000, 1000, 100, 1)  # the picture changes at these amounts
# Twitch's own cheermotes (lower case, as in the address)
NAMES = frozenset((
    "cheer", "doodlecheer", "biblethump", "cheerwhal", "corgo", "uni", "showlove", "party", "seemsgood",
    "pride", "kappa", "frankerz", "heyguys", "dansgame", "elegiggle", "trihard", "kreygasm", "4head",
    "swiftrage", "notlikethis", "failfish", "vohiyo", "pjsalt", "mrdestructoid", "bday", "ripcheer",
    "shamrock", "bitboss", "streamlabs", "muxy", "holidaycheer", "goal", "anon", "charity",
))
_WORD = re.compile(r"^([A-Za-z0-9]*[A-Za-z])([0-9]{1,7})$")  # ASCII digits only: \d also matches e.g. "١"


def cheermote(word: str) -> tuple[str, int] | None:
    """"Cheer100" → (address of its picture, 100); None if the word is no cheermote."""
    m = _WORD.match(word)
    if not m or m.group(1).lower() not in NAMES:
        return None
    amount = int(m.group(2))
    if amount < 1:
        return None
    tier = next(t for t in TIERS if amount >= t)
    return URL.format(name=m.group(1).lower(), tier=tier), amount


def cheer_parts(parts: list[Part]) -> list[Part]:
    """Text parts of a message with bits → cheermote words become picture + amount."""
    out: list[Part] = []
    for kind, value, alt in parts:
        if kind != "text":
            out.append((kind, value, alt))
            continue
        text = ""
        for piece in re.split(r"(\s+)", value):
            found = cheermote(piece) if piece and not piece.isspace() else None
            if not found:
                text += piece
                continue
            if text:
                out.append(("text", text, ""))
            url, amount = found
            out.append(("emote", url, piece[: len(piece) - len(str(amount))]))
            text = str(amount)  # the amount stays readable next to the picture
        if text:
            out.append(("text", text, ""))
    return out
