# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Twitch polls, predictions, hype trains and the other events of twitch_hermes_events.py
for every connected channel, read the way the Twitch website does for a viewer who isn't
logged in: its event feed "Hermes" over
WebSocket with the website's client ID, the channel IDs from its GraphQL API. Both are
undocumented and may change or close at any time – then these lines are just missing,
the chat itself doesn't depend on them.

EventSub (twitch_eventsub.py) brings the same for your own channel when you're logged in;
whatever it covers is left out here, so nothing shows twice. One connection serves all
channels; the lines are made in twitch_hermes_events.py."""
from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import aiohttp
import websockets

from .models import Backoff, ChatMessage
from .twitch_events import WANTED, remember_room
from .twitch_hermes_events import TOPICS, HermesEvents

log = logging.getLogger("multichat.chat")

WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # the Twitch website's own
HERMES_URL = f"wss://hermes.twitch.tv/v1?clientId={WEB_CLIENT_ID}"
GQL_URL = "https://gql.twitch.tv/gql"
GQL_CHANNEL_ID = "query($login: String!) { user(login: $login) { id } }"
GQL_CHANNEL_NAME = "query($id: ID!) { user(id: $id) { login displayName } }"
NAMES_PER_ROUND = 5  # shared chat channel names looked up per POLL
MAX_UNNAMED = 500  # failed name lookups remembered
POLL = 5.0  # seconds between looks at the connected channels
KEEPALIVE_BUFFER = 5.0  # seconds on top of the announced keepalive before the connection counts as dead
WELCOME_TIMEOUT = 10.0
DEFAULT_KEEPALIVE = 15
CLOSE_TIMEOUT = 0.5  # don't hold up the shutdown waiting for the closing handshake
# kind → the EventSub subscription that brings it for your own channel
EVENTSUB_TYPES = {"poll": "channel.poll.begin", "prediction": "channel.prediction.begin",
                  "hype_train": "channel.hype_train.begin",
                  "redemption": "channel.channel_points_custom_reward_redemption.add"}


class HermesClient:
    """channels() → {source key: channel login} of the connected Twitch chats; emit(event,
    source key) hands a ChatMessage to the window; covered(login, EventSub type) tells
    whether EventSub brings that already; session() → the aiohttp session for the IDs."""

    def __init__(self, channels: Callable[[], dict[str, str]], emit: Callable,
                 covered: Callable[[str, str], bool], session: Callable, connect=None, lookup=None,
                 lookup_name=None) -> None:
        self.channels = channels
        self.emit = emit
        self.covered = covered
        self.session = session
        self.connect = connect or functools.partial(websockets.connect, close_timeout=CLOSE_TIMEOUT)
        self.lookup = lookup or self._lookup
        self.lookup_name = lookup_name or self._lookup_name
        self.unnamed: set[str] = set()  # shared chat channel IDs whose name lookup failed (not asked again)
        self.ids: dict[str, str] = {}  # login → channel ID
        self.warned: set[str] = set()  # logins whose ID lookup failed (logged once)

    async def run(self) -> None:
        backoff = Backoff()
        while True:
            try:
                if self.channels():
                    await self._session(backoff)
                else:
                    await asyncio.sleep(POLL)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Twitch Hermes: %s", e,
                            exc_info=not isinstance(e, (ValueError, ConnectionError, OSError, aiohttp.ClientError)))
                await asyncio.sleep(backoff.next())

    async def _session(self, backoff: Backoff) -> None:
        """One connection until it ends (returns: no channel left, or Twitch asked to
        reconnect; raises: it failed or went silent → run() waits, then reconnects)."""
        ws = await self.connect(HERMES_URL)
        try:
            keepalive = await self._welcome(ws)
            backoff.connected()
            events = HermesEvents()
            subs: dict[str, tuple[str, str]] = {}  # subscription ID → (login, kind)
            joined: set[str] = set()
            last, due = time.monotonic(), 0.0
            while True:
                if time.monotonic() >= due:
                    logins = set(self.channels().values())
                    if not logins:
                        return
                    await self._subscribe(ws, sorted(logins - joined), subs, joined)
                    await self._name_rooms()
                    due = time.monotonic() + POLL
                try:
                    raw = await asyncio.wait_for(ws.recv(), POLL)
                except asyncio.TimeoutError:
                    raw = None
                now = time.monotonic()
                if raw is not None:
                    last = now
                    if not self._handle(raw, subs, events):
                        return
                elif now - last > keepalive + KEEPALIVE_BUFFER:
                    raise ConnectionError("no keepalive from Twitch Hermes – reconnecting")
        finally:
            await _close(ws)

    async def _welcome(self, ws) -> float:
        msg = json.loads(await asyncio.wait_for(ws.recv(), WELCOME_TIMEOUT))
        if not isinstance(msg, dict) or msg.get("type") != "welcome":
            raise ValueError("Hermes: no welcome message")
        return _num((msg.get("welcome") or {}).get("keepaliveSec")) or DEFAULT_KEEPALIVE

    async def _subscribe(self, ws, logins: list[str], subs: dict[str, tuple[str, str]], joined: set[str]) -> None:
        """Subscribes the channels not done yet; one whose ID can't be found is tried again
        in the next round. A channel that was disconnected keeps its subscriptions until the
        connection ends (its lines are dropped in _handle)."""
        for login in logins:
            try:
                cid = self.ids.get(login) or await self.lookup(login)
            except Exception as e:  # noqa: BLE001 – network, HTTP or unexpected answer: next round
                if login not in self.warned:
                    self.warned.add(login)
                    log.info("Twitch Hermes: no channel ID for %s (%s)", login, e)
                continue
            self.ids[login] = cid
            for topic, kind in TOPICS.items():
                sid = uuid.uuid4().hex
                subs[sid] = (login, kind)
                await ws.send(json.dumps({
                    "type": "subscribe", "id": uuid.uuid4().hex,
                    "subscribe": {"id": sid, "type": "pubsub", "pubsub": {"topic": f"{topic}.{cid}"}},
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                }))
            joined.add(login)

    def _handle(self, raw, subs: dict[str, tuple[str, str]], events: HermesEvents) -> bool:
        """One message; False when Twitch asks to reconnect."""
        try:
            msg = json.loads(raw)
            kind = msg["type"]
        except (ValueError, TypeError, KeyError):
            log.debug("Twitch Hermes: unreadable message")
            return True
        if kind == "reconnect":
            log.info("Twitch Hermes: reconnect requested")
            return False
        if kind == "subscribeResponse":
            result = msg.get("subscribeResponse") or {}
            if result.get("result") != "ok":
                log.info("Twitch Hermes: subscription refused (%s)", result.get("error") or result.get("result"))
            return True
        note = msg.get("notification") if kind == "notification" else None
        if not isinstance(note, dict) or note.get("type") != "pubsub":
            return True
        target = subs.get(str((note.get("subscription") or {}).get("id")))
        try:
            payload = json.loads(note.get("pubsub") or "")
        except (ValueError, TypeError):
            payload = None
        if not target or not isinstance(payload, dict):
            return True
        login, what = target
        if what in EVENTSUB_TYPES and self.covered(login, EVENTSUB_TYPES[what]):
            return True
        text = events.text(what, payload)
        if not text:
            return True
        for key, channel in self.channels().items():
            if channel == login:
                self.emit(ChatMessage(platform="twitch", channel=channel, user="Twitch", text="",
                                      event="notice", event_text=text), key)
        return True

    async def _name_rooms(self) -> None:
        """Shared chat: the names of the other channels of a session, for "Shared chat:
        <channel>" (twitch_events.shared_from asks for them). A few per round; one whose
        lookup fails is left without a name."""
        for room_id in [r for r in WANTED if r in self.unnamed]:
            del WANTED[room_id]  # asked again by a later line: still no name, and it must not fill WANTED up
        for room_id in list(WANTED)[:NAMES_PER_ROUND]:
            WANTED.pop(room_id, None)
            try:
                remember_room(room_id, await self.lookup_name(room_id))
            except Exception as e:  # noqa: BLE001 – network, HTTP or unexpected answer
                if len(self.unnamed) >= MAX_UNNAMED:
                    self.unnamed.clear()  # a later round may ask once more
                self.unnamed.add(room_id)
                log.info("Twitch Hermes: no channel name for %s (%s)", room_id, e)

    async def _lookup_name(self, room_id: str) -> str:
        body = {"query": GQL_CHANNEL_NAME, "variables": {"id": room_id}}
        async with self.session().post(GQL_URL, json=body, headers={"Client-Id": WEB_CLIENT_ID},
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                raise ValueError(f"HTTP {r.status}")
            data = await r.json(content_type=None)
        user = (((data or {}).get("data") or {}).get("user") or {})
        name = user.get("displayName") or user.get("login")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("unknown channel")
        return name

    async def _lookup(self, login: str) -> str:
        body = {"query": GQL_CHANNEL_ID, "variables": {"login": login}}
        async with self.session().post(GQL_URL, json=body, headers={"Client-Id": WEB_CLIENT_ID},
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                raise ValueError(f"HTTP {r.status}")
            data = await r.json(content_type=None)
        cid = str((((data or {}).get("data") or {}).get("user") or {}).get("id") or "")
        if not cid.isascii() or not cid.isdigit():
            raise ValueError("unknown channel")
        return cid


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _close(ws) -> None:
    with contextlib.suppress(Exception):  # already gone
        await ws.close()
