# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""YouTube login, sending and moderating (YouTube Data API)."""
from __future__ import annotations

import logging
import time

from ..chat.models import ChatMessage
from ..core.i18n import _
from .oauth import Account, ApiError

log = logging.getLogger("multichat.accounts")
from ..chat.youtube import find_live_video, youtube_target


class YouTubeAccount(Account):
    platform = "youtube"
    name = "YouTube"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = "https://www.googleapis.com/auth/youtube.force-ssl"
    auth_extra = {"access_type": "offline", "prompt": "consent"}
    API = "https://www.googleapis.com/youtube/v3"

    async def whoami(self) -> dict:
        items = (await self.request("GET", f"{self.API}/channels", params={"part": "snippet", "mine": "true"})).get(
            "items"
        ) or []
        if not items:
            return {"user_id": "", "user_name": "(no channel)"}
        return {"user_id": items[0]["id"], "user_name": items[0]["snippet"]["title"]}

    async def current_video(self, value: str) -> str:
        """The video ID directly, or for a channel input the running live stream (cached 60 s)."""
        kind, target = youtube_target(value)
        if kind == "video":
            return target
        hit = self._cache.get(("live", target))
        if hit and hit[1] > time.time():
            return hit[0]
        found = await find_live_video(target)
        if not found:
            raise ApiError(_("The YouTube channel is not live right now"))
        self._cache[("live", target)] = (found[0], time.time() + 60)
        return found[0]

    async def live_chat_id(self, video: str) -> str:
        vid = await self.current_video(video)
        if vid not in self._cache:
            items = (
                await self.request("GET", f"{self.API}/videos", params={"part": "liveStreamingDetails", "id": vid})
            ).get("items") or []
            chat_id = items and (items[0].get("liveStreamingDetails") or {}).get("activeLiveChatId")
            if not chat_id:
                raise ApiError(_("No active live chat for this video"))
            self._cache[vid] = chat_id
        return self._cache[vid]

    async def send(self, channel: str, text: str) -> None:
        chat_id = await self.live_chat_id(channel)
        await self.request(
            "POST",
            f"{self.API}/liveChat/messages",
            params={"part": "snippet"},
            json={
                "snippet": {
                    "liveChatId": chat_id,
                    "type": "textMessageEvent",
                    "textMessageDetails": {"messageText": text},
                }
            },
        )

    async def delete(self, msg: ChatMessage) -> None:
        await self.request("DELETE", f"{self.API}/liveChat/messages", params={"id": msg.msg_id})

    async def _ban(self, msg: ChatMessage, seconds: int | None) -> None:
        chat_id = await self.live_chat_id(msg.channel)
        snippet = {
            "liveChatId": chat_id,
            "type": "temporary" if seconds else "permanent",
            "bannedUserDetails": {"channelId": msg.user_id},
        }
        if seconds:
            snippet["banDurationSeconds"] = seconds
        await self.request("POST", f"{self.API}/liveChat/bans", params={"part": "snippet"}, json={"snippet": snippet})

    async def timeout(self, msg: ChatMessage, seconds: int) -> None:
        await self._ban(msg, seconds)

    async def ban(self, msg: ChatMessage) -> None:
        await self._ban(msg, None)
