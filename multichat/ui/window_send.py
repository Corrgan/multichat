# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: the send line – where a message can go, the emotes offered while
typing, and sending. Mixed into MainWindow."""
from __future__ import annotations

import re

from ..accounts import ApiError
from ..chat import ChatMessage
from ..chat.models import mention_name, user_key
from ..core.constants import NAMES, PLATFORMS
from ..core.i18n import _


MAX_MENTION_NAMES = 500  # chatters offered after "@"
MENTION = re.compile(r"(?<![\w@])@(\w(?:[\w.\-]*\w)?)")  # an "@name" as typed (names with spaces: see route_mentions)


def route_mentions(text: str, seen: dict[str, set[str]]) -> tuple[set[str], set[str]]:
    """Where the "@" mentions in text should go: (send targets of the names found in seen,
    lower-case mentions found nowhere). seen maps a lower-case name to the targets (source keys)
    that person wrote in. Known names are matched as a whole, so "@Shown Name" (YouTube) works."""
    low = text.lower()
    targets: set[str] = set()
    covered: list[tuple[int, int]] = []
    for name in sorted((n for n in seen if f"@{n}" in low), key=len, reverse=True):  # "@anna lena" before "@anna"
        for m in re.finditer(rf"@{re.escape(name)}(?!\w)", low):
            if not any(a <= m.start() < b for a, b in covered):
                covered.append((m.start(), m.end()))
                targets |= seen[name]
    unknown = {m.group(1) for m in MENTION.finditer(low) if not any(a <= m.start() < b for a, b in covered)}
    return targets, unknown


class SendMixin:
    """Sending part of MainWindow (expects target, input, send_btn, accounts, emotes and
    the _native_* emote state from it)."""

    def mention(self, msg: ChatMessage) -> None:
        if self.target.isEnabled():
            idx = self.target.findData(msg.source)
            if idx >= 0:
                self.target.setCurrentIndex(idx)
        self.input.setText(f"@{mention_name(msg)} {self.input.text()}")
        self.input.setFocus()

    def _send_options(self) -> list[tuple[str, str, str, str]]:
        """(key, platform, channel, label) of the active group's channels you are logged in for."""
        return [(r.key(), r.platform, r.value(), NAMES[r.platform]) for r in self.active_panel().all_rows()
                if r.key() and self.accounts[r.platform].logged_in]

    def _refresh_targets(self) -> None:
        if not hasattr(self, "target"):
            return
        current = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        options = self._send_options()
        if len(options) > 1:
            self.target.addItem(_("All in this group"), None)
        for key, _p, _c, label in options:
            self.target.addItem(label, key)
        idx = self.target.findData(current)
        if idx >= 0:
            self.target.setCurrentIndex(idx)
        self.target.blockSignals(False)
        logged = any(self.accounts[p].logged_in for p in PLATFORMS)
        enabled = bool(options)
        for w in (self.target, self.input, self.send_btn):
            w.setEnabled(enabled)
        if enabled:
            self.input.setPlaceholderText(_("Send a message … (: for emotes, @ for names)"))
            self._load_send_emotes()
        elif logged:
            self.input.setPlaceholderText(_("Enter a channel of a platform you are logged in to"))
        else:
            self.input.setPlaceholderText(_("Log in under “Accounts” to send and moderate"))

    def _send_emotes(self) -> dict[str, str]:
        """Emotes for the suggestions: the ones usable in the chosen send target(s) – your
        Twitch emotes there, plus 7TV/BTTV/FFZ of the channel and global ones."""
        target = self.target.currentData()
        out: dict[str, str] = {}
        for key, platform, channel, _label in self._send_options():
            if target in (None, key):
                out.update(self.emotes.available(platform, channel))
                out.update(self._native_emotes.get(key, {}))
        return out

    def _send_mentions(self) -> list[tuple[str, str]]:
        """(shown, name to mention) of the people who wrote in the chosen send target(s),
        whoever wrote last first – for the "@" suggestions. Without yourself."""
        target = self.target.currentData()
        keys = {key for key, _p, _c, _label in self._send_options() if target in (None, key)}
        seen, names, out = set(), set(), []
        for m in reversed(self.messages.values()):
            if m.source not in keys or m.user == "?" or (m.event == "notice" and not m.parts and not m.user_id):
                continue
            who = (m.platform, user_key(m))
            if who in seen:
                continue
            seen.add(who)
            if self.mod._is_own(self.accounts[m.platform], m):
                continue
            mention = mention_name(m)
            if mention.lower() in names:  # the same name on two platforms: one entry
                continue
            names.add(mention.lower())
            out.append((m.user if mention == m.user else f"{m.user} ({mention})", mention))
            if len(out) >= MAX_MENTION_NAMES:
                break
        return out

    def _load_send_emotes(self) -> None:
        """Fetches the Twitch emotes you can use in the active group's Twitch channel (once).
        Only for a connected channel: the send options follow the channel field while it
        is typed, and every prefix ("e", "ex", "exa" …) would be looked up otherwise –
        a dozen requests each and a warning for names that don't exist."""
        for key, platform, channel, _label in self._send_options():
            if (platform != "twitch" or key not in self.readers
                    or key in self._native_emotes or key in self._native_loading):
                continue
            self._native_loading.add(key)
            generation = self.accounts["twitch"].session_gen

            def current(generation=generation) -> bool:
                return generation == self._native_session == self.accounts["twitch"].session_gen

            def done(result, key=key, current=current) -> None:
                if not current():
                    return
                self._native_loading.discard(key)
                emotes, complete = result
                self._native_emotes[key] = dict(emotes)
                if not complete and not self._native_hint:
                    self._native_hint = True
                    self.statusBar().showMessage(
                        _("Log in to Twitch again to get suggestions for all your emotes (subscriptions included)"), 10000)

            def failed(key=key, current=current) -> None:
                if not current():
                    return
                self._native_loading.discard(key)
                self._native_emotes[key] = {}  # not again until the next login

            self.run(self.accounts["twitch"].usable_emotes(channel), then=done, on_error=failed, quiet=True)

    def _mention_seen(self, keys: set[str]) -> dict[str, set[str]]:
        """Lower-case names (shown and account name) → the send targets among keys where that
        person wrote – from the chat history, like the "@" suggestions."""
        seen: dict[str, set[str]] = {}
        for m in self.messages.values():
            if m.source not in keys or m.user == "?" or (m.event == "notice" and not m.parts and not m.user_id):
                continue
            for name in {m.user.lower(), mention_name(m).lower()}:
                seen.setdefault(name, set()).add(m.source)
        return seen

    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        target = self.target.currentData()
        options = [o for o in self._send_options() if target in (None, o[0])]
        if not options:
            return
        self.input.clear()
        if target is None and len(options) > 1 and "@" in text:
            # "All in this group" with "@name": only where the mentioned people are
            targets, unknown = route_mentions(text, self._mention_seen({o[0] for o in options}))
            twitch = [o for o in options if o[1] == "twitch"]
            if unknown and twitch:
                self.run(self._twitch_mention_targets(unknown, twitch),
                         then=lambda found: self._send_routed(text, options, targets | found[0], found[1]),
                         on_error=lambda: self._send_routed(text, options, targets, unknown), quiet=True)
                return
            self._send_routed(text, options, targets, unknown)
            return
        self._send_all(text, options)

    async def _twitch_mention_targets(self, unknown: set[str], twitch: list) -> tuple[set[str], set[str]]:
        """Looks the mentions not in the history up in the Twitch channels' chatter lists →
        (targets where they are, mentions still unknown)."""
        found: set[str] = set()
        left = set(unknown)
        for key, _p, channel, _label in twitch:
            chatters = await self.accounts["twitch"].chatters(channel)
            if chatters:
                hits = left & chatters
                if hits:
                    found.add(key)
                left -= hits
        return found, left

    def _send_routed(self, text: str, options: list, targets: set[str], unknown: set[str]) -> None:
        """Sends to the targets of the mentions – to all options if a mention is unknown
        (better everywhere than nowhere) or no mention matched."""
        routed = [o for o in options if o[0] in targets]
        self._send_all(text, options if unknown or not routed else routed)

    def _send_all(self, text: str, options: list) -> None:
        for _key, platform, channel, label in options:
            self.run(self.send_to(platform, channel, text),
                     label=_("Sent to {platform}").format(platform=f"{label} · {channel}"))

    async def send_to(self, platform: str, channel: str, text: str) -> None:
        try:
            await self.accounts[platform].send(channel, text)
        except ApiError as e:
            if e.status != 403:
                raise
            # the platforms only say "Forbidden" – explain the usual causes
            raise ApiError(
                _(
                    "{platform} rejected the message ({error}). The chat may be restricted (followers or "
                    "subscribers only, slow mode, verified accounts only) or the developer app lacks the "
                    "permission to send."
                ).format(platform=NAMES[platform], error=e),
                status=403,
            ) from e
