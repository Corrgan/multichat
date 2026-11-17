# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Twitch EventSub over WebSocket: what IRC doesn't carry (follows, channel point rewards
without a message, polls, predictions, hype trains, ad breaks) for the channels where you are
the broadcaster or a moderator. IRC stays the chat source; this only adds notice lines.

One session per login serves all those channels (Twitch allows 3 per token); the
subscriptions are created through the Helix API with the session's ID."""
from __future__ import annotations

import asyncio
import collections
import contextlib
import functools
import json
import logging
import time
from collections.abc import Callable

import websockets

from ..accounts.oauth import ApiError
from .models import Backoff, ChatMessage
from .twitch_eventsub_events import SUBSCRIPTIONS, eventsub_text

log = logging.getLogger("multichat.chat")

EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
POLL = 5.0  # seconds between looks at the connected channels and the login
KEEPALIVE_BUFFER = 5.0  # seconds on top of keepalive_timeout_seconds before the session counts as dead
WELCOME_TIMEOUT = 10.0
DEFAULT_KEEPALIVE = 10
SEEN_IDS = 500  # message IDs remembered to drop the duplicates Twitch may deliver
# Twitch's EventSub servers never answer the closing handshake: waiting for it (websockets'
# default: 10 s) held up the window's shutdown, which allows only 3 s for all connections.
CLOSE_TIMEOUT = 0.5


class EventSubClient:
    """channels() → {source key: channel login} of the connected Twitch chats;
    emit(event, source key) hands a ChatMessage to the window."""

    def __init__(self, account, channels: Callable[[], dict[str, str]], emit: Callable, connect=None) -> None:
        self.account = account
        self.channels = channels
        self.emit = emit
        self.connect = connect or functools.partial(websockets.connect, close_timeout=CLOSE_TIMEOUT)
        self.seen: collections.deque = collections.deque(maxlen=SEEN_IDS)
        self.warned: set[tuple[str, str]] = set()  # (login, type) whose missing scope was logged already

    async def run(self) -> None:
        backoff = Backoff()
        url = EVENTSUB_URL
        while True:
            try:
                if await self._eligible():
                    await self._session(url, backoff)
                else:
                    await asyncio.sleep(POLL)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Twitch EventSub: %s", e, exc_info=not isinstance(e, (ValueError, ConnectionError)))
                await asyncio.sleep(backoff.next())

    async def _eligible(self) -> dict[str, tuple[str, bool]]:
        """{login: (broadcaster ID, you are the broadcaster)} of the connected channels where
        EventSub works for you. Empty when logged out."""
        acc = self.account
        if not acc.logged_in:
            return {}
        out: dict[str, tuple[str, bool]] = {}
        for login in sorted(set(self.channels().values())):
            try:
                own = login == acc.user_name.lower()
                if own or await acc.is_moderator(login):
                    out[login] = (await acc.broadcaster_id(login), own)
            except ApiError as e:
                log.info("Twitch EventSub: skipping %s (%s)", login, e)
        return out

    async def _session(self, url: str, backoff: Backoff) -> None:
        """One EventSub session until it ends (returns: logged out or no channel left;
        raises: the connection failed or went silent → run() reconnects)."""
        acc = self.account
        generation = acc.session_gen
        ws = await self.connect(url)
        try:
            session = await self._welcome(ws)
            backoff.connected()  # only now: a server that refuses us keeps the delay growing
            subscribed: dict[str, set[str]] = {}
            last, due = time.monotonic(), 0.0
            while True:
                if time.monotonic() >= due:  # first round right after the welcome: Twitch wants the subscriptions within 10 s
                    eligible = await self._eligible()
                    if not eligible:
                        return
                    await self._subscribe(session["id"], eligible, subscribed)
                    due = time.monotonic() + POLL
                keepalive = _num(session.get("keepalive_timeout_seconds")) or DEFAULT_KEEPALIVE
                try:
                    raw = await asyncio.wait_for(ws.recv(), POLL)
                except asyncio.TimeoutError:
                    raw = None
                now = time.monotonic()
                if raw is not None:
                    last = now
                    ws, session = await self._handle(raw, ws, session)
                elif now - last > keepalive + KEEPALIVE_BUFFER:
                    raise ConnectionError("no keepalive from Twitch EventSub – reconnecting")
                if not acc.logged_in or acc.session_gen != generation:
                    return
        finally:
            await _close(ws)

    async def _welcome(self, ws) -> dict:
        msg = json.loads(await asyncio.wait_for(ws.recv(), WELCOME_TIMEOUT))
        if (msg.get("metadata") or {}).get("message_type") != "session_welcome":
            raise ValueError("EventSub: no welcome message")
        session = (msg.get("payload") or {}).get("session") or {}
        if not session.get("id"):
            raise ValueError("EventSub: welcome without a session ID")
        return session

    async def _handle(self, raw, ws, session: dict):
        """One message → the (possibly new) socket and session."""
        try:
            msg = json.loads(raw)
            meta, payload = msg["metadata"], msg.get("payload") or {}
            kind = meta["message_type"]
        except (ValueError, TypeError, KeyError):
            log.debug("Twitch EventSub: unreadable message")
            return ws, session
        if kind == "session_reconnect":
            # connect to the new URL first, close the old session only afterwards: no event is lost
            new_ws = await self.connect((payload.get("session") or {}).get("reconnect_url") or EVENTSUB_URL)
            try:
                session = await self._welcome(new_ws)
            except BaseException:
                await _close(new_ws)
                raise
            await _close(ws)
            log.info("Twitch EventSub: moved to a new server")
            return new_ws, session
        if kind == "revocation":
            sub = payload.get("subscription") or {}
            log.warning("Twitch EventSub: subscription %s revoked (%s)", sub.get("type"), sub.get("status"))
        elif kind == "notification":
            mid = meta.get("message_id")
            if mid in self.seen:
                return ws, session
            self.seen.append(mid)
            self._notify(str(meta.get("subscription_type") or ""), payload.get("event"))
        return ws, session

    def _notify(self, sub_type: str, event) -> None:
        text = eventsub_text(sub_type, event)
        login = str(event.get("broadcaster_user_login") or "").lower() if isinstance(event, dict) else ""
        if not text:
            return
        for key, channel in self.channels().items():  # a channel that was disconnected meanwhile gets nothing
            if channel == login:
                self.emit(ChatMessage(platform="twitch", channel=channel, user="Twitch", text="",
                                      event="notice", event_text=text), key)

    async def _subscribe(self, session_id: str, eligible, subscribed: dict[str, set[str]]) -> None:
        acc = self.account
        for login, (bid, own) in eligible.items():
            if login in subscribed:
                continue
            subscribed[login] = done = set()
            for sub_type, (version, broadcaster_only) in SUBSCRIPTIONS.items():
                if broadcaster_only and not own:
                    continue
                condition = {"broadcaster_user_id": bid}
                if sub_type == "channel.follow":
                    condition["moderator_user_id"] = acc.user_id
                body = {"type": sub_type, "version": version, "condition": condition,
                        "transport": {"method": "websocket", "session_id": session_id}}
                try:
                    await acc.request("POST", f"{acc.API}/eventsub/subscriptions", json=body)
                    done.add(sub_type)
                except ApiError as e:
                    if e.status in (401, 403):  # login from before the scope existed
                        if (login, sub_type) not in self.warned:
                            self.warned.add((login, sub_type))
                            log.info("Twitch EventSub: can't subscribe to %s for %s (%s) – log in again to enable",
                                     sub_type, login, e)
                    elif e.status != 409:  # 409: exists already
                        log.warning("Twitch EventSub: %s for %s: %s", sub_type, login, e)


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _close(ws) -> None:
    with contextlib.suppress(Exception):  # already gone
        await ws.close()


async def run_twitch_eventsub(account, channels: Callable[[], dict[str, str]], emit: Callable) -> None:
    """Runs until cancelled; follows the login and the connected channels by itself."""
    await EventSubClient(account, channels, emit).run()
