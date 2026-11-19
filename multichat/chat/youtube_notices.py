# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""YouTube chat items that are no message, Super Chat or membership. Pure functions over
the InnerTube data; youtube_innertube.py reads the chat."""
from __future__ import annotations

from collections import OrderedDict

from ..core.i18n import _
from .models import ChatMessage


def _text(obj: dict | None) -> str:
    if not obj:
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    if isinstance(obj.get("content"), str):  # newer "view model" items, e.g. giftMessageViewModel
        return obj["content"]
    return "".join(r.get("text", "") for r in obj.get("runs", []) if isinstance(r, dict))


_NOTICE_TEXTS = ("text", "message", "primaryText", "headerPrimaryText", "subtext", "headerSubtext")


# no line for these: a message that is still being checked, and the "Welcome to live chat!
# Remember to guard your privacy …" YouTube sends on every connect
_NO_NOTICE = ("liveChatPlaceholderItemRenderer", "liveChatViewerEngagementMessageRenderer")


def notice(item: dict, video_id: str) -> ChatMessage | None:
    """A chat item that is no message, Super Chat or membership: YouTube's own lines (chat
    mode changed, "welcome to live chat"), a redeemed gift membership, a poll … They are
    read by what they have in common – an author if any, and their texts – instead of one
    by one, so a new kind is shown too. None if there is nothing to show."""
    kind, r = next(((k, v) for k, v in item.items() if isinstance(v, dict)), ("", None))
    if not r or kind in _NO_NOTICE:
        return None
    texts = []
    for key in _NOTICE_TEXTS:
        value = _text(r.get(key)) if isinstance(r.get(key), dict) else ""
        if value.strip() and value.strip() not in texts:
            texts.append(value.strip())
    if not texts:
        return None
    author = (_text(r.get("authorName")) if isinstance(r.get("authorName"), dict) else "").strip()
    headline, body = (texts[0], " ".join(texts[1:])) if not author else (" ".join(texts), "")
    return ChatMessage(
        platform="youtube", channel=video_id, user=author or "YouTube", text=body,
        parts=[("text", body, "")] if body else [], msg_id=r.get("id") if isinstance(r.get("id"), str) else None,
        user_id=r.get("authorExternalChannelId") if author and isinstance(r.get("authorExternalChannelId"), str) else None,
        event="notice", event_text=f"{author} {headline}" if author else headline,
    )


# ---- polls and pinned messages (not chat items but commands for the panel/banner above the chat) ---- #
MAX_KNOWN = 50
# (video, poll ID) → (question, choices, percentages of the latest update); (video, banner ID) of pinned messages
_polls: OrderedDict[tuple[str, str], tuple[str, list[str], list[str]]] = OrderedDict()
_pins_shown: OrderedDict[tuple[str, str], None] = OrderedDict()


def _remember(store: OrderedDict, key, value) -> None:
    store[key] = value
    while len(store) > MAX_KNOWN:
        store.popitem(last=False)


def _poll_data(r) -> tuple[str, str, list[str], list[str]] | None:
    """pollRenderer → (poll ID, question, choices, percentages) or None."""
    if not isinstance(r, dict) or not isinstance(r.get("liveChatPollId"), str):
        return None
    header = (r.get("header") or {}).get("pollHeaderRenderer") or {} if isinstance(r.get("header"), dict) else {}
    question = _text(header.get("pollQuestion") if isinstance(header.get("pollQuestion"), dict) else None).strip()
    choices, percent = [], []
    for c in r.get("choices") or []:
        if isinstance(c, dict) and isinstance(c.get("text"), dict):
            choices.append(_text(c["text"]).strip())
            percent.append(_text(c.get("votePercentage")) if isinstance(c.get("votePercentage"), dict) else "")
    return r["liveChatPollId"], question, choices, percent


def _line(video_id: str, headline: str, text: str = "", user: str = "YouTube") -> ChatMessage:
    return ChatMessage(platform="youtube", channel=video_id, user=user, text=text,
                       parts=[("text", text, "")] if text else [], event="notice", event_text=headline)


def chat_command(a: dict, video_id: str) -> ChatMessage | None:
    """YouTube's panel and banner commands → a line: a poll started (once, also when MultiChat
    connects during one), a poll ended (with the last percentages), a pinned message. Poll
    updates (one per vote) only keep the percentages. None for anything else (e.g. the
    auto-generated "Chat summary" banner)."""
    poll = None
    if isinstance(a.get("showLiveChatActionPanelAction"), dict):
        panel = (a["showLiveChatActionPanelAction"].get("panelToShow") or {}).get("liveChatActionPanelRenderer") or {}
        poll = _poll_data((panel.get("contents") or {}).get("pollRenderer") if isinstance(panel, dict) else None)
    elif isinstance(a.get("updateLiveChatPollAction"), dict):
        poll = _poll_data((a["updateLiveChatPollAction"].get("pollToUpdate") or {}).get("pollRenderer"))
    elif isinstance(a.get("addBannerToLiveChatCommand"), dict):
        banner = (a["addBannerToLiveChatCommand"].get("bannerRenderer") or {}).get("liveChatBannerRenderer") or {}
        contents = banner.get("contents") if isinstance(banner, dict) and isinstance(banner.get("contents"), dict) else {}
        if "pollRenderer" in contents:
            poll = _poll_data(contents["pollRenderer"])
        elif isinstance(contents.get("liveChatTextMessageRenderer"), dict):  # "Pinned by …"
            r, key = contents["liveChatTextMessageRenderer"], (video_id, str(banner.get("actionId") or ""))
            author, message = _text(r.get("authorName")).strip(), _text(r.get("message")).strip()
            if author and message and key not in _pins_shown:
                _remember(_pins_shown, key, None)
                return _line(video_id, _("Pinned message"), message, author)
        return None if not poll else _poll_line(poll, video_id)
    else:
        target = ((a.get("closeLiveChatActionPanelAction") or {}).get("targetPanelId")
                  if isinstance(a.get("closeLiveChatActionPanelAction"), dict) else
                  (a.get("removeBannerForLiveChatCommand") or {}).get("targetActionId")
                  if isinstance(a.get("removeBannerForLiveChatCommand"), dict) else None)
        known = _polls.pop((video_id, target), None) if isinstance(target, str) else None
        if not known:  # not a poll, or its end was shown already (panel and banner both close)
            return None
        question, choices, percent = known
        result = " · ".join(f"{c} {p}" if p else c for c, p in zip(choices, percent))
        return _line(video_id, _("Poll ended: {title}").format(title=question or "?"), result)
    return _poll_line(poll, video_id) if poll else None


def _poll_line(poll, video_id: str) -> ChatMessage | None:
    poll_id, question, choices, percent = poll
    key = (video_id, poll_id)
    new = key not in _polls
    old = _polls.get(key)
    _remember(_polls, key, (question or (old[0] if old else ""), choices or (old[1] if old else []),
                            percent if any(percent) else (old[2] if old else [])))
    if not new:
        return None
    title = question or "?"
    return _line(video_id, _("Poll: {title} – {options}").format(title=title, options=" / ".join(choices))
                 if choices else _("Poll: {title}").format(title=title))
