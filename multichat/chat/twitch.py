# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Twitch chat: anonymous IRC over WebSocket (read-only)."""
from __future__ import annotations

import asyncio
import random
import logging

import websockets

from .models import Backoff, ChatMessage, Deletion, Emit, Part, Status, roles, twitch_channel, with_extra_emotes as _extra

from .cheermotes import cheer_parts
from .twitch_events import _int, message_note, moderation_notice, twitch_event  # noqa: F401 – twitch_event is imported from here elsewhere

log = logging.getLogger("multichat.chat")


TWITCH_EMOTE_URL = "https://static-cdn.jtvnw.net/emoticons/v2/{}/default/dark/2.0"


def _parse_tags(raw: str) -> dict[str, str]:
    out = {}
    for part in raw.split(";"):
        k, _, v = part.partition("=")
        out[k] = v.replace("\\s", " ").replace("\\:", ";").replace("\\\\", "\\")
    return out


def parse_irc(line: str) -> tuple[dict[str, str], str, str, list[str]]:
    """Splits an IRC line into (tags, prefix, command, params)."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw, _, line = line[1:].partition(" ")
        tags = _parse_tags(raw)
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, trailing = line.split(" :", 1)
    elif line.startswith(":"):
        line, trailing = "", line[1:]
    params = line.split()
    cmd = params.pop(0) if params else ""
    if trailing is not None:
        params.append(trailing)
    return tags, prefix, cmd, params


def twitch_parts(text: str, emotes: str | None) -> list[Part]:
    """Converts the `emotes` tag (e.g. "25:0-4,6-10/1902:12-16") into parts."""
    if not emotes:
        return [("text", text, "")]
    spans = []
    for entry in emotes.split("/"):
        eid, _, positions = entry.partition(":")
        if not (eid.isascii() and eid.replace("_", "").isalnum()):  # IDs are digits or emotesv2_<hex>; isalnum() alone accepts "²"
            continue
        for rng in positions.split(","):
            a, sep, b = rng.partition("-")
            if sep and a.isdigit() and b.isdigit() and int(a) <= int(b) < len(text):
                spans.append((int(a), int(b), eid))
    spans.sort()
    out: list[Part] = []
    i = 0
    for a, b, eid in spans:
        if a < i:
            continue
        if a > i:
            out.append(("text", text[i:a], ""))
        out.append(("emote", TWITCH_EMOTE_URL.format(eid), text[a : b + 1]))
        i = b + 1
    if i < len(text):
        out.append(("text", text[i:], ""))
    return out


async def run_twitch(channel: str, emit: Emit, status: Status, emotes=None) -> None:
    channel = twitch_channel(channel)
    backoff = Backoff()
    while True:
        try:
            status("twitch", "connecting …")
            async with websockets.connect("wss://irc-ws.chat.twitch.tv:443") as ws:
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send("PASS SCHMOOPIIE")
                await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                await ws.send(f"JOIN #{channel}")
                status("twitch", "connected (#{channel})", channel=channel)
                backoff.connected()
                async for raw in ws:
                    for line in str(raw).split("\r\n"):
                        if not line:
                            continue
                        tags, prefix, cmd, params = parse_irc(line)
                        if emotes and tags.get("room-id"):
                            emotes.schedule_channel("twitch", channel, tags["room-id"])
                        if cmd == "PING":
                            await ws.send("PONG :" + (params[-1] if params else "tmi.twitch.tv"))
                        elif cmd == "PRIVMSG" and len(params) >= 2:
                            text = params[1]
                            if text.startswith("\x01ACTION "):
                                text = text[8:].rstrip("\x01")
                            parts = twitch_parts(text, tags.get("emotes"))
                            if tags.get("bits"):
                                event = {"event": "bits", "event_value": _int(tags["bits"])}
                                parts = cheer_parts(parts)
                            elif note := message_note(tags):
                                event = {"event": "notice", "event_text": note}
                            else:
                                event = {}
                            emit(
                                ChatMessage(
                                    platform="twitch",
                                    channel=channel,
                                    user=tags.get("display-name") or prefix.split("!")[0],
                                    login=prefix.split("!")[0],
                                    text=text,
                                    parts=_extra(emotes, "twitch", channel, parts),
                                    color=tags.get("color") or None,
                                    msg_id=tags.get("id"),
                                    user_id=tags.get("user-id"),
                                    badges=roles(b.split("/")[0] for b in (tags.get("badges") or "").split(",") if b),
                                    **event,
                                )
                            )
                        elif cmd == "USERNOTICE" and (ev := twitch_event(tags)):
                            text = params[1] if len(params) >= 2 else ""  # e.g. the resub message
                            emit(
                                ChatMessage(
                                    platform="twitch",
                                    channel=channel,
                                    user=tags.get("msg-param-displayName") or tags.get("display-name")
                                    or tags.get("login") or "?",
                                    login=tags.get("login") or "",
                                    text=text,
                                    parts=_extra(emotes, "twitch", channel, twitch_parts(text, tags.get("emotes")))
                                    if text else [],
                                    color=tags.get("color") or None,
                                    msg_id=tags.get("id"),
                                    user_id=tags.get("user-id"),
                                    badges=roles(b.split("/")[0] for b in (tags.get("badges") or "").split(",") if b),
                                    event=ev[0], event_value=ev[1], event_text=ev[2],
                                )
                            )
                        elif cmd == "NOTICE" and params and params[-1].strip():
                            # what Twitch itself says in the chat: slow mode on, followers only …
                            emit(ChatMessage(platform="twitch", channel=channel, user="Twitch", text="",
                                             event="notice", event_text=params[-1].strip()))
                        elif cmd == "CLEARMSG":
                            emit(Deletion("twitch", msg_id=tags.get("target-msg-id")))
                        elif cmd == "CLEARCHAT":
                            target = tags.get("target-user-id")
                            emit(Deletion("twitch", user_id=target) if target else Deletion("twitch", clear_all=True))
                            emit(ChatMessage(platform="twitch", channel=channel, user="Twitch", text="", event="notice",
                                             event_text=moderation_notice(tags, params[-1] if len(params) >= 2 else "")))
                        elif cmd == "RECONNECT":
                            raise ConnectionError("Twitch requested a reconnect")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("Twitch chat #%s: %s", channel, e, exc_info=not isinstance(e, ValueError))
            delay = backoff.next()
            status("twitch", "error: {error} – retrying in {seconds} s", error=e, seconds=delay)
            await asyncio.sleep(delay)
