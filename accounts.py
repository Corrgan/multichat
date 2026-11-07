# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Login (OAuth in the browser) plus sending and moderating per platform.

Each platform needs its own developer app (client ID + secret), created by the user.
REDIRECT_URI is entered as its redirect URL; for the login the app briefly starts a
local web server that receives the login code.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from clients import ChatMessage, find_live_video, youtube_target
from i18n import _

log = logging.getLogger("multichat.accounts")

REDIRECT_PORT = 17563
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
LOGIN_TIMEOUT = 300

SUCCESS_HTML = """<!doctype html><meta charset="utf-8"><title>MultiChat</title>
<body style="font-family:sans-serif;background:#18181b;color:#efeff1;display:grid;
place-items:center;height:100vh;margin:0"><div style="text-align:center">
<h2>{title}</h2><p>{text}</p>
</div></body>"""


def success_html() -> str:
    return SUCCESS_HTML.format(
        title=_("Logged in ✓"), text=_("You can close this window and return to MultiChat.")
    )


class ApiError(Exception):
    pass


def _short_error(text: str) -> str:
    try:
        body = json.loads(text)
    except Exception:  # noqa: BLE001
        return text[:200]
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("message") or json.dumps(err)[:200]
    return body.get("message") or body.get("error_description") or str(err or text)[:200]


class TokenStore:
    """Tokens in ~/.config/multichat/tokens.json (readable only by you, 0600)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            self.data = {}

    def get(self, platform: str) -> dict:
        with self.lock:
            return dict(self.data.get(platform) or {})

    def update(self, platform: str, **values) -> None:
        with self.lock:
            self.data.setdefault(platform, {}).update(values)
            self._save()

    def replace(self, platform: str, values: dict) -> None:
        with self.lock:
            self.data[platform] = dict(values)
            self._save()

    def delete(self, platform: str) -> None:
        with self.lock:
            self.data.pop(platform, None)
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)


Creds = Callable[[str], tuple[str, str]]


class Account:
    platform = ""
    name = ""
    auth_url = ""
    token_url = ""
    scopes = ""
    pkce = True
    auth_extra: dict = {}

    def __init__(self, store: TokenStore, creds: Creds, session: Callable[[], aiohttp.ClientSession]) -> None:
        self.store = store
        self.creds = creds
        self._session = session
        self._cache: dict = {}

    # ---- State --------------------------------------------------------- #
    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session()

    @property
    def tokens(self) -> dict:
        return self.store.get(self.platform)

    @property
    def logged_in(self) -> bool:
        return bool(self.tokens.get("access_token"))

    @property
    def user_name(self) -> str:
        return self.tokens.get("user_name", "")

    @property
    def user_id(self) -> str:
        return str(self.tokens.get("user_id", ""))

    def logout(self) -> None:
        self.store.delete(self.platform)
        self._cache.clear()

    def extra_headers(self) -> dict:
        return {}

    # ---- OAuth ---------------------------------------------------------- #
    async def login(self) -> dict:
        cid, secret = self.creds(self.platform)
        if not cid or not secret:
            raise ApiError(_("Client ID/secret missing (Accounts → Set up developer apps)"))
        state = secrets.token_urlsafe(16)
        params = {
            "client_id": cid,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": self.scopes,
            "state": state,
            **self.auth_extra,
        }
        verifier = None
        if self.pkce:
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            params.update(code_challenge=challenge, code_challenge_method="S256")

        loop = asyncio.get_running_loop()
        code_fut: asyncio.Future = loop.create_future()

        async def callback(request: web.Request) -> web.Response:
            q = request.query
            if q.get("state") != state:
                return web.Response(text=_("Invalid login state – please try again."), status=400)
            if not code_fut.done():
                if "code" in q:
                    code_fut.set_result(q["code"])
                else:
                    code_fut.set_exception(ApiError(q.get("error_description") or q.get("error") or _("Login cancelled")))
            return web.Response(text=success_html(), content_type="text/html")

        app = web.Application()
        app.router.add_get("/callback", callback)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            try:
                await web.TCPSite(runner, "localhost", REDIRECT_PORT).start()
            except OSError as e:
                raise ApiError(_("Port {port} in use – is a login already running? ({error})").format(port=REDIRECT_PORT, error=e)) from e
            url = f"{self.auth_url}?{urlencode(params)}"
            await asyncio.to_thread(webbrowser.open, url)
            try:
                code = await asyncio.wait_for(code_fut, LOGIN_TIMEOUT)
            except asyncio.TimeoutError as e:
                raise ApiError(_("Login timed out")) from e
        finally:
            await runner.cleanup()

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": cid,
            "client_secret": secret,
        }
        if verifier:
            data["code_verifier"] = verifier
        self.store.replace(self.platform, await self._token_request(data))
        self._cache.clear()
        try:
            who = await self.whoami()
        except Exception:
            self.store.delete(self.platform)
            raise
        self.store.update(self.platform, **who)
        return who

    async def _token_request(self, data: dict) -> dict:
        async with self.session.post(self.token_url, data=data, headers={"Accept": "application/json"}) as r:
            text = await r.text()
            status = r.status
        if status >= 400:
            log.warning("%s token request (%s) → HTTP %s: %s", self.name, data.get("grant_type"), status, text[:300])
            raise ApiError(_("{platform} login failed ({status}): {error}").format(platform=self.name, status=status, error=_short_error(text)))
        body = json.loads(text)
        out = {
            "access_token": body["access_token"],
            "expires_at": time.time() + float(body.get("expires_in") or 3600),
        }
        if body.get("refresh_token"):
            out["refresh_token"] = body["refresh_token"]
        return out

    async def _refresh(self) -> None:
        rt = self.tokens.get("refresh_token")
        if not rt:
            raise ApiError(_("{platform} session expired – please log in again").format(platform=self.name))
        cid, secret = self.creds(self.platform)
        tok = await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid, "client_secret": secret}
        )
        self.store.update(self.platform, **tok)

    async def request(self, method: str, url: str, *, _retry: bool = True, **kw):
        if not self.logged_in:
            raise ApiError(_("Not logged in to {platform}").format(platform=self.name))
        if self.tokens.get("expires_at", 0) < time.time() + 60:
            await self._refresh()
        extra = kw.pop("headers", {})
        headers = {"Authorization": f"Bearer {self.tokens['access_token']}", **self.extra_headers(), **extra}
        async with self.session.request(method, url, headers=headers, **kw) as r:
            text = await r.text()
            status = r.status
        if status == 401 and _retry:
            await self._refresh()
            return await self.request(method, url, _retry=False, headers=extra, **kw)
        if status >= 400:
            log.warning("%s %s %s → HTTP %s: %s", self.name, method, url.split("?")[0], status, text[:300])
            raise ApiError(f"{self.name} ({status}): {_short_error(text)}")
        log.debug("%s %s %s → HTTP %s", self.name, method, url.split("?")[0], status)
        return json.loads(text) if text.strip() else None

    # ---- implemented by subclasses -------------------------------------- #
    async def whoami(self) -> dict:
        raise NotImplementedError

    async def send(self, channel: str, text: str) -> None:
        raise NotImplementedError

    async def delete(self, msg: ChatMessage) -> None:
        raise NotImplementedError

    async def timeout(self, msg: ChatMessage, seconds: int) -> None:
        raise NotImplementedError

    async def ban(self, msg: ChatMessage) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
class TwitchAccount(Account):
    platform = "twitch"
    name = "Twitch"
    auth_url = "https://id.twitch.tv/oauth2/authorize"
    token_url = "https://id.twitch.tv/oauth2/token"
    scopes = "user:write:chat moderator:manage:banned_users moderator:manage:chat_messages"
    pkce = False
    API = "https://api.twitch.tv/helix"

    def extra_headers(self) -> dict:
        return {"Client-Id": self.creds("twitch")[0]}

    async def whoami(self) -> dict:
        u = (await self.request("GET", f"{self.API}/users"))["data"][0]
        return {"user_id": u["id"], "user_name": u["display_name"]}

    async def broadcaster_id(self, channel: str) -> str:
        login = channel.lower().lstrip("#")
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


# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
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
