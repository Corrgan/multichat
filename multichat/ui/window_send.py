# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: the send line – where a message can go, the emotes offered while
typing, and sending. Mixed into MainWindow."""
from __future__ import annotations

from ..accounts import ApiError
from ..chat import ChatMessage
from ..chat.models import mention_name, user_key
from ..core.constants import NAMES, PLATFORMS
from ..core.i18n import _


MAX_MENTION_NAMES = 500  # chatters offered after "@"


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
            if m.source not in keys or m.user == "?":
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

    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        target = self.target.currentData()
        options = [o for o in self._send_options() if target in (None, o[0])]
        for _key, platform, channel, label in options:
            self.run(self.send_to(platform, channel, text),
                     label=_("Sent to {platform}").format(platform=f"{label} · {channel}"))
        if options:
            self.input.clear()

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
