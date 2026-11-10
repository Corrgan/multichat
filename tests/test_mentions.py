# SPDX-License-Identifier: GPL-3.0-or-later
"""The mention action addresses the account and retains the existing send-line text."""
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QComboBox, QLineEdit

from multichat.chat.models import ChatMessage
from multichat.ui.window_send import SendMixin


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
