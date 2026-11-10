# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""YouTube live chat read directly from YouTube's web client API ("InnerTube").

This is what the chat in the browser does: the live chat page contains the first batch of
messages and a continuation token; get_live_chat returns new messages plus the next token
and how long to wait. No API key or login is needed and no API quota is used.

Unlike pytchat this also sees deleted messages and banned users. If YouTube changes
something and this reader fails, run_youtube falls back to pytchat (see youtube.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict

import aiohttp

from .alerts import parse_amount
from .models import ChatMessage, Deletion, Emit, Part, roles

log = logging.getLogger("multichat.chat")

LIVE_CHAT_URL = "https://www.youtube.com/live_chat"
GET_LIVE_CHAT_URL = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
MIN_WAIT, MAX_WAIT = 1.0, 10.0  # seconds between polls (YouTube suggests a value)


class InnertubeError(RuntimeError):
    """The page or a response doesn't look as expected – the caller may fall back to pytchat."""


class ChatEnded(Exception):
    """No continuation any more: the stream (or its chat) has ended."""


def _find_json(page: str, marker: str) -> dict:
    """The JSON object that follows `marker` in the page (e.g. 'ytInitialData')."""
    i = page.find(marker)
    if i < 0:
        raise InnertubeError(f"{marker} not found on the live chat page")
    start = page.find("{", i)
    try:
        obj, _end = json.JSONDecoder().raw_decode(page, start)
    except ValueError as e:
        raise InnertubeError(f"{marker} is not valid JSON: {e}") from e
    return obj


def _cfg(page: str, key: str) -> str | None:
    m = re.search(r'"%s"\s*:\s*"([^"]+)"' % re.escape(key), page)
    return m.group(1) if m else None


# what a response in an unexpected shape raises while it is taken apart
_SHAPE_ERRORS = (KeyError, IndexError, TypeError, AttributeError, ValueError)


def _continuation(conts: list) -> tuple[str, float]:
    """→ (token, seconds to wait) from a `continuations` list."""
    for c in conts or []:
        for kind in ("invalidationContinuationData", "timedContinuationData", "reloadContinuationData"):
            data = c.get(kind)
            if data and data.get("continuation"):
                try:
                    wait = int(data.get("timeoutMs") or 5000) / 1000
                except (TypeError, ValueError):
                    wait = 5.0
                return str(data["continuation"]), wait
    raise ChatEnded


def parse_page(page: str) -> tuple[dict, str, list]:
    """Live chat page → (client context, continuation, initial actions)."""
    data = _find_json(page, "ytInitialData")
    renderer = (data.get("contents") or {}).get("liveChatRenderer")
    if not renderer:
        # e.g. "chat is disabled for this live stream" or the stream is over
        msg = (data.get("contents") or {}).get("messageRenderer", {})
        text = "".join(r.get("text", "") for r in (msg.get("text") or {}).get("runs", []))
        raise ChatEnded(text or "no live chat on this page")
    # prefer "Live chat" (all messages) over the default "Top chat"
    token = None
    try:
        items = renderer["header"]["liveChatHeaderRenderer"]["viewSelector"]["sortFilterSubMenuRenderer"]["subMenuItems"]
        token = items[-1]["continuation"]["reloadContinuationData"]["continuation"]
    except (KeyError, IndexError, TypeError):
        pass
    if not token:
        token, _wait = _continuation(renderer.get("continuations"))
    version = _cfg(page, "INNERTUBE_CLIENT_VERSION") or _cfg(page, "clientVersion")
    if not version:
        raise InnertubeError("client version not found on the live chat page")
    context = {"client": {"clientName": "WEB", "clientVersion": version, "hl": "en", "gl": "US"}}
    visitor = _cfg(page, "VISITOR_DATA")
    if visitor:
        context["client"]["visitorData"] = visitor
    return context, token, renderer.get("actions") or []


def _text(obj: dict | None) -> str:
    if not obj:
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    return "".join(r.get("text", "") for r in obj.get("runs", []))


def runs_to_parts(runs: list) -> list[Part]:
    parts: list[Part] = []
    for r in runs or []:
        if "text" in r:
            parts.append(("text", r["text"], ""))
        elif "emoji" in r:
            e = r["emoji"]
            name = (e.get("shortcuts") or [e.get("emojiId", "")])[0]
            thumbs = (e.get("image") or {}).get("thumbnails") or []
            url = thumbs[-1].get("url", "") if thumbs else ""
            if e.get("isCustomEmoji") and url.startswith("https://"):
                parts.append(("emote", url, name))
            else:  # standard emoji: the character itself
                parts.append(("text", e.get("emojiId") or name, ""))
    return parts


def _badges(r: dict) -> tuple:
    names = []
    for b in r.get("authorBadges") or []:
        badge = b.get("liveChatAuthorBadgeRenderer") or {}
        icon = (badge.get("icon") or {}).get("iconType")
        names.append(icon if icon else ("member" if badge.get("customThumbnail") else ""))
    return roles(names)


_RENDERERS = (
    "liveChatTextMessageRenderer", "liveChatPaidMessageRenderer", "liveChatMembershipItemRenderer",
    "liveChatPaidStickerRenderer", "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer",
)


def parse_actions(actions: list, video_id: str) -> list:
    """InnerTube actions → ChatMessage/Deletion events (unknown kinds are skipped)."""
    events: list = []
    for a in actions:
        a = a.get("replayChatItemAction", {}).get("actions", [{}])[0] if "replayChatItemAction" in a else a
        if "addChatItemAction" in a:
            item = a["addChatItemAction"].get("item") or {}
            kind = next((k for k in item if k in _RENDERERS), None)
            r = item.get(kind) if kind else None
            if not r or not r.get("id"):
                continue
            parts = runs_to_parts((r.get("message") or {}).get("runs"))
            event, value, etext = "", 0.0, ""
            if kind in ("liveChatPaidMessageRenderer", "liveChatPaidStickerRenderer"):  # Super Chat / Sticker
                etext = _text(r.get("purchaseAmountText"))
                event, value = "superchat", parse_amount(etext)
            elif kind == "liveChatMembershipItemRenderer":  # new member or milestone
                event = "member"
                m = re.search(r"(\d+)", _text(r.get("headerPrimaryText")))  # "Member for 12 months"
                value = float(m.group(1)) if m else 1.0
            elif kind == "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer":  # gifted memberships
                header = (r.get("header") or {}).get("liveChatSponsorshipsHeaderRenderer") or {}
                r = {**r, **header}  # author name/ID/badges live in the header
                m = re.search(r"(\d+)", _text(header.get("primaryText")))  # "Gifted 5 … memberships"
                event, value = "gift", float(m.group(1)) if m else 1.0
            if not parts and not event:
                continue
            events.append(ChatMessage(
                platform="youtube",
                channel=video_id,
                user=_text(r.get("authorName")) or "?",
                text="".join(p[1] if p[0] == "text" else p[2] for p in parts),
                parts=parts,
                msg_id=r["id"],
                user_id=r.get("authorExternalChannelId"),
                badges=_badges(r),
                event=event, event_value=value, event_text=etext,
            ))
        elif "markChatItemAsDeletedAction" in a:
            mid = a["markChatItemAsDeletedAction"].get("targetItemId")
            if mid:
                events.append(Deletion("youtube", msg_id=mid))
        elif "markChatItemsByAuthorAsDeletedAction" in a:
            uid = a["markChatItemsByAuthorAsDeletedAction"].get("externalChannelId")
            if uid:
                events.append(Deletion("youtube", user_id=uid))
    return events


class InnertubeChat:
    """Reads one live chat until it ends. Raises InnertubeError if YouTube's format changed."""

    def __init__(self, video_id: str, headers: dict, cookies: dict) -> None:
        self.video_id = video_id
        self.headers = headers
        self.cookies = cookies
        self.seen: OrderedDict[str, None] = OrderedDict()

    def _new(self, events: list) -> list:
        out = []
        for ev in events:
            mid = getattr(ev, "msg_id", None) if isinstance(ev, ChatMessage) else None
            if mid:
                if mid in self.seen:
                    continue
                self.seen[mid] = None
                while len(self.seen) > 5000:
                    self.seen.popitem(last=False)
            out.append(ev)
        return out

    async def run(self, emit: Emit, on_connected=None, emotes_apply=None) -> None:
        async with aiohttp.ClientSession(
            trust_env=True, headers=self.headers, cookies=self.cookies, timeout=aiohttp.ClientTimeout(total=20)
        ) as s:
            async with s.get(LIVE_CHAT_URL, params={"is_popout": "1", "v": self.video_id}) as r:
                if r.status != 200:
                    raise InnertubeError(f"live chat page HTTP {r.status}")
                page = await r.text()
            # A page or response that no longer has the expected shape must end up as
            # InnertubeError: that is what makes run_youtube fall back to pytchat. A bare
            # KeyError/TypeError would only be retried with this same reader forever.
            try:
                context, token, initial = parse_page(page)
                first = self._new(parse_actions(initial, self.video_id))
            except _SHAPE_ERRORS as e:
                raise InnertubeError(f"unexpected live chat page: {type(e).__name__}: {e}") from e
            if on_connected:
                on_connected()
            for ev in first:
                emit(emotes_apply(ev) if emotes_apply else ev)
            failures = 0
            while True:
                try:
                    async with s.post(GET_LIVE_CHAT_URL, params={"prettyPrint": "false"},
                                      json={"context": context, "continuation": token}) as r:
                        if r.status != 200:
                            raise InnertubeError(f"get_live_chat HTTP {r.status}")
                        try:
                            data = await r.json(content_type=None)
                        except ValueError as e:
                            raise InnertubeError(f"get_live_chat did not return JSON: {e}") from e
                    try:
                        cont = (data.get("continuationContents") or {}).get("liveChatContinuation")
                        if cont is None:
                            raise ChatEnded
                        token, wait = _continuation(cont.get("continuations"))
                        events = self._new(parse_actions(cont.get("actions") or [], self.video_id))
                    except _SHAPE_ERRORS as e:
                        raise InnertubeError(f"unexpected get_live_chat response: {type(e).__name__}: {e}") from e
                    failures = 0
                except (aiohttp.ClientError, asyncio.TimeoutError, InnertubeError) as e:
                    failures += 1
                    if failures >= 5:
                        raise InnertubeError(f"get_live_chat keeps failing: {e}") from e
                    log.debug("YouTube InnerTube poll failed (%s), retrying", e)
                    await asyncio.sleep(3)
                    continue
                for ev in events:
                    emit(emotes_apply(ev) if emotes_apply else ev)
                await asyncio.sleep(min(MAX_WAIT, max(MIN_WAIT, wait)))
