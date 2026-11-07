# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Kick login, sending and moderating (official Kick API)."""
from __future__ import annotations

import logging

from ..chat.models import ChatMessage
from ..core.i18n import _
from .oauth import Account, ApiError

log = logging.getLogger("multichat.accounts")


class KickAccount(Account):
    platform = "kick"
    name = "Kick"
    auth_url = "https://id.kick.com/oauth/authorize"
    token_url = "https://id.kick.com/oauth/token"
    scopes = "user:read channel:read chat:write moderation:ban moderation:chat_message:manage"
    API = "https://api.kick.com/public/v1"

    async def whoami(self) -> dict:
        u = (await self.request("GET", f"{self.API}/users"))["data"][0]
        return {"user_id": u["user_id"], "user_name": u["name"]}

    async def broadcaster_id(self, channel: str) -> int:
        slug = channel.lower().strip()
        if slug not in self._cache:
            data = (await self.request("GET", f"{self.API}/channels", params={"slug": slug}))["data"]
            if not data:
                raise ApiError(_("Kick channel “{channel}” not found").format(channel=slug))
            self._cache[slug] = int(data[0]["broadcaster_user_id"])
        return self._cache[slug]

    async def send(self, channel: str, text: str) -> None:
        bid = await self.broadcaster_id(channel)
        await self.request("POST", f"{self.API}/chat", json={"broadcaster_user_id": bid, "content": text, "type": "user"})

    async def delete(self, msg: ChatMessage) -> None:
        await self.request("DELETE", f"{self.API}/chat/{msg.msg_id}")

    async def timeout(self, msg: ChatMessage, seconds: int) -> None:
        bid = await self.broadcaster_id(msg.channel)
        minutes = max(1, min(10080, round(seconds / 60)))  # Kick counts in minutes
        await self.request(
            "POST",
            f"{self.API}/moderation/bans",
            json={"broadcaster_user_id": bid, "user_id": int(msg.user_id), "duration": minutes},
        )

    async def ban(self, msg: ChatMessage) -> None:
        bid = await self.broadcaster_id(msg.channel)
        await self.request(
            "POST", f"{self.API}/moderation/bans", json={"broadcaster_user_id": bid, "user_id": int(msg.user_id)}
        )
