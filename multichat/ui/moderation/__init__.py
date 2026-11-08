# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Moderation: selected user, action bar above the chat, user card, notes.

- Clicking a name selects that user; the action bar then works on the selection.
- Whether you may moderate a channel is asked from the platform (Twitch lists the
  channels you moderate; Kick/YouTube only know your own channel) and also read from
  the badges on your own chat messages. Mod actions are hidden where you are known
  not to be a moderator.
- Notes and a log of your moderation actions are kept locally per user
  (~/.config/multichat/modnotes.json, readable only by you).

store.py holds the data (no widgets), actions.py the logic, widgets.py the UI.
"""
from .actions import TIMEOUTS, ModActions
from .store import ModNotes, ModState, UserHistory
from .util import profile_url, role_label, since_text
from .widgets import ActionBar, UserCard

__all__ = ["TIMEOUTS", "ActionBar", "ModActions", "ModNotes", "ModState", "UserCard", "UserHistory",
           "profile_url", "role_label", "since_text"]
