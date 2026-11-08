# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Twitch login, sending and moderating (Helix API)."""
from __future__ import annotations

import logging

from ..chat.models import ChatMessage, twitch_channel
from ..core.i18n import _
from .oauth import Account, ApiError

log = logging.getLogger("multichat.accounts")


class TwitchAccount(Account):
    platform = "twitch"
    name = "Twitch"
    auth_url = "https://id.twitch.tv/oauth2/authorize"
    token_url = "https://id.twitch.tv/oauth2/token"
    # moderator:read:followers → followage on the user card,
    # user:read:moderated_channels → where you are a moderator (older logins lack both)
    scopes = (
        "user:write:chat moderator:manage:banned_users moderator:manage:chat_messages "
        "moderator:read:followers user:read:moderated_channels"
    )
    pkce = False
    API = "https://api.twitch.tv/helix"

    def extra_headers(self) -> dict:
        return {"Client-Id": self.creds("twitch")[0]}

    async def whoami(self) -> dict:
        u = (await self.request("GET", f"{self.API}/users"))["data"][0]
        return {"user_id": u["id"], "user_name": u["display_name"]}

    async def broadcaster_id(self, channel: str) -> str:
        login = twitch_channel(channel)
        if login not in self._cache:
            data = (await self.request("GET", f"{self.API}/users", params={"login": login}))["data"]
            if not data:
                raise ApiError(_("Twitch channel “{channel}” not found").format(channel=login))
            self._cache[login] = data[0]["id"]
        return self._cache[login]

    async def send(self, channel: str, text: str) -> None:
        bid = await self.broadcaster_id(channel)
        res = await self.request(
            "POST", f"{self.API}/chat/messages", json={"broadcaster_id": bid, "sender_id": self.user_id, "message": text}
        )
        info = (res or {}).get("data", [{}])[0]
        if info.get("is_sent") is False:
            raise ApiError(_("Twitch dropped the message: {reason}").format(reason=(info.get("drop_reason") or {}).get("message", "")))

    def _mod_params(self, bid: str) -> dict:
        return {"broadcaster_id": bid, "moderator_id": self.user_id}

    async def delete(self, msg: ChatMessage) -> None:
        bid = await self.broadcaster_id(msg.channel)
        await self.request("DELETE", f"{self.API}/moderation/chat", params={**self._mod_params(bid), "message_id": msg.msg_id})

    async def timeout(self, msg: ChatMessage, seconds: int) -> None:
        bid = await self.broadcaster_id(msg.channel)
        await self.request(
            "POST",
            f"{self.API}/moderation/bans",
            params=self._mod_params(bid),
            json={"data": {"user_id": msg.user_id, "duration": seconds}},
        )

    async def ban(self, msg: ChatMessage) -> None:
        bid = await self.broadcaster_id(msg.channel)
        await self.request(
            "POST", f"{self.API}/moderation/bans", params=self._mod_params(bid), json={"data": {"user_id": msg.user_id}}
        )

    async def unban(self, msg: ChatMessage) -> None:
        bid = await self.broadcaster_id(msg.channel)
        await self.request("DELETE", f"{self.API}/moderation/bans", params={**self._mod_params(bid), "user_id": msg.user_id})

    async def is_moderator(self, channel: str) -> bool | None:
        login = twitch_channel(channel)
        if login == self.user_name.lower():
            return True
        key = ("moderated", self.user_id)
        if key not in self._cache:
            channels, cursor = set(), None
            try:
                for _page in range(10):  # 100 per page
                    params = {"user_id": self.user_id, "first": "100", **({"after": cursor} if cursor else {})}
                    res = await self.request("GET", f"{self.API}/moderation/channels", params=params)
                    channels |= {str(c.get("broadcaster_login", "")).lower() for c in res.get("data") or [] if isinstance(c, dict)}
                    cursor = (res.get("pagination") or {}).get("cursor")
                    if not cursor:
                        break
            except ApiError as e:
                if e.status in (401, 403):  # login from before this scope existed
                    log.info("Twitch: can't list moderated channels (%s) – log in again to enable", e)
                    return None
                raise
            self._cache[key] = channels
        return login in self._cache[key]

    async def user_info(self, msg: ChatMessage) -> dict:
        info: dict = {}
        if not msg.user_id:
            return info
        users = (await self.request("GET", f"{self.API}/users", params={"id": msg.user_id}) or {}).get("data") or []
        if users:
            info["created"] = users[0].get("created_at")
        try:
            bid = await self.broadcaster_id(msg.channel)
            res = await self.request("GET", f"{self.API}/channels/followers",
                                     params={"broadcaster_id": bid, "user_id": msg.user_id})
            info["following_since"] = (res.get("data") or [{}])[0].get("followed_at") or ""
        except ApiError as e:  # not a moderator there, or login without the scope
            log.debug("Twitch followage unavailable: %s", e)
        return info
