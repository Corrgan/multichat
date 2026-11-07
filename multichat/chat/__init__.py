# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Chat readers for Twitch, Kick and YouTube (read-only, no login).

Each reader runs as an asyncio task and calls `emit(event)` with either a
`ChatMessage` or a `Deletion` (message deleted / user banned). Status updates go to
`status(platform, template, **values)` with an English template; the UI translates it.
"""
from .emotes import EmoteStore
from .kick import run_kick
from .models import ChatMessage, Deletion
from .twitch import run_twitch
from .youtube import run_youtube

__all__ = ["ChatMessage", "Deletion", "EmoteStore", "run_kick", "run_twitch", "run_youtube"]
