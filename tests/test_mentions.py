# SPDX-License-Identifier: GPL-3.0-or-later
"""The mention action addresses the account and retains the existing send-line text; "@" mentions
sent to "All in this group" only go where the mentioned people are."""
import asyncio
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QComboBox, QLineEdit

from multichat.chat.models import ChatMessage
from multichat.accounts import ApiError, FileBackend, SecretStore, TwitchAccount
from multichat.ui.window_send import SendMixin, route_mentions


@pytest.mark.parametrize("platform,shown,login,expected", [
    ("twitch", "DisplayName", "real_login", "real_login"),
    ("twitch", "Streamer", "streamer", "Streamer"),
    ("youtube", "Shown Name", "", "Shown Name"),
])
def test_mention_action_uses_the_account_name_and_selects_its_channel(platform, shown, login, expected):
    target, edit = QComboBox(), QLineEdit("existing message")
    target.addItem("Other", "twitch:other")
    target.addItem("Selected channel", f"{platform}:channel")
    window = SimpleNamespace(target=target, input=edit)
    message = ChatMessage(platform, "channel", shown, "hi", login=login, source=f"{platform}:channel")
    try:
        SendMixin.mention(window, message)
        assert edit.text() == f"@{expected} existing message"
        assert target.currentData() == message.source
    finally:
        edit.close()
        target.close()


SEEN = {"anna lena": {"youtube:c"}, "anna": {"twitch:c"}, "bob": {"kick:c", "twitch:c"}}


@pytest.mark.parametrize("text,targets,unknown", [
    ("@Anna Lena hi", {"youtube:c"}, set()),  # a name with a space as a whole, not "@anna"
    ("@anna hi", {"twitch:c"}, set()),
    ("@annabelle hi", set(), {"annabelle"}),  # "@anna" is not a prefix match
    ("thanks @Bob.", {"kick:c", "twitch:c"}, set()),  # one person on two platforms → both
    ("@anna and @bob", {"twitch:c", "kick:c"}, set()),
    ("@anna @carl-x!", {"twitch:c"}, {"carl-x"}),
    ("mail me: a@b.de", set(), set()),  # no mention
])
def test_route_mentions(text, targets, unknown):
    assert route_mentions(text, SEEN) == (targets, unknown)


OPTIONS = [("twitch:c", "twitch", "c", "Twitch"), ("kick:c", "kick", "c", "Kick"), ("youtube:c", "youtube", "c", "YouTube")]


class _Window(SendMixin):
    """The parts of MainWindow that sending needs; records where messages went."""

    def __init__(self, messages, chatters=None, chatters_fail=False):
        self.target, self.input = QComboBox(), QLineEdit()
        self.target.addItem("All", None)
        self.messages = {i: m for i, m in enumerate(messages)}
        self.sent: list[str] = []

        async def chatters_of(channel):
            if chatters_fail:
                raise ApiError("boom", status=500)
            return chatters

        self.accounts = {"twitch": SimpleNamespace(chatters=chatters_of)}

    def _send_options(self):
        return OPTIONS

    async def send_to(self, platform, channel, text):
        self.sent.append(platform)

    def run(self, coro, then=None, label=None, on_error=None, quiet=False):
        try:
            result = asyncio.run(coro)
        except Exception:  # noqa: BLE001
            if on_error:
                on_error()
            return
        if then:
            then(result)

    def close(self):
        self.target.close()
        self.input.close()


def _msg(platform, user, login=""):
    return ChatMessage(platform, "c", user, "hello", login=login, source=f"{platform}:c")


@pytest.mark.parametrize("text,chatters,fail,expected", [
    ("@NightOwl hi", None, False, ["youtube"]),  # seen in the history → only there
    ("@nightowl @kicker gg", None, False, ["kick", "youtube"]),  # two people, two platforms
    ("@lurker hi", {"lurker"}, False, ["twitch"]),  # only in Twitch's chatter list
    ("@stranger hi", {"lurker"}, False, ["twitch", "kick", "youtube"]),  # unknown → everywhere
    ("@stranger hi", None, False, ["twitch", "kick", "youtube"]),  # Twitch doesn't tell → everywhere
    ("@lurker hi", None, True, ["twitch", "kick", "youtube"]),  # the lookup fails → everywhere
    ("no mention", None, False, ["twitch", "kick", "youtube"]),
])
def test_send_to_all_routes_mentions(text, chatters, fail, expected):
    window = _Window([_msg("youtube", "NightOwl"), _msg("kick", "Kicker"), _msg("twitch", "Shown", login="real_login")],
                     chatters=chatters, chatters_fail=fail)
    try:
        window.input.setText(text)
        window.send()
        assert sorted(window.sent) == sorted(expected)
        assert window.input.text() == ""
    finally:
        window.close()


def test_send_to_one_channel_ignores_mentions():
    window = _Window([_msg("youtube", "NightOwl")])
    try:
        window.target.addItem("Twitch", "twitch:c")
        window.target.setCurrentIndex(1)
        window.input.setText("@NightOwl hi")
        window.send()
        assert window.sent == ["twitch"]
    finally:
        window.close()


def test_twitch_chatters(tmp_path):
    """All pages of Get Chatters, reused for a minute; a refusal (no moderator, old login) → None, also cached."""

    async def run():
        acc = TwitchAccount(SecretStore("tokens", FileBackend(tmp_path)), lambda p: ("CID", "SECRET"), lambda: None)
        acc._cache["c"] = "123"  # broadcaster ID
        acc.store.replace("twitch", {"access_token": "T", "user_id": "9", "user_name": "Me"})
        calls = []

        async def request(method, url, params=None, **kw):
            calls.append(params)
            if params.get("broadcaster_id") == "403":
                raise ApiError("forbidden", status=403)
            if "after" not in params:
                return {"data": [{"user_login": "Alice"}], "pagination": {"cursor": "next"}}
            return {"data": [{"user_login": "bob"}], "pagination": {}}

        acc.request = request
        assert await acc.chatters("c") == {"alice", "bob"}
        assert calls[0]["moderator_id"] == "9" and calls[1]["after"] == "next"
        assert await acc.chatters("C") == {"alice", "bob"} and len(calls) == 2  # cached
        acc._cache["forbidden"] = "403"
        assert await acc.chatters("forbidden") is None
        assert await acc.chatters("forbidden") is None and len(calls) == 3  # the refusal is cached too

    asyncio.run(run())
