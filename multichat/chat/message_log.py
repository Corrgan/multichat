# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""The bounded chat history behind all groups – Qt-free.

Each group keeps its newest `per_group` messages; a message stays while at least one of its
groups still counts it among those (groups sharing a channel never cost each other lines).
Indexes by message ID, user ID and emote URL let deletions and image updates find their
lines without walking the whole history (with 10 busy groups that walk cost ~12 ms every
100 messages and ~3 ms per deletion on the UI thread)."""
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Hashable, Iterable

from .models import ChatMessage, Deletion


class MessageLog:
    def __init__(self, per_group: int) -> None:
        self.per_group = per_group
        self.messages: OrderedDict[int, ChatMessage] = OrderedDict()
        self._groups: dict[Hashable, deque[int]] = {}
        self._count: dict[int, int] = {}  # uid → number of groups that still count it
        self._by_msg: dict[tuple[str, str], set[int]] = {}
        self._by_user: dict[tuple[str, str], set[int]] = {}
        self._by_image: dict[str, set[int]] = {}

    def __len__(self) -> int:
        return len(self.messages)

    def add(self, msg: ChatMessage, groups: Iterable[Hashable]) -> None:
        """Stores a message for these groups; a group's oldest message beyond per_group is
        released, and dropped once no group counts it any more."""
        uid = msg.uid
        count = 0
        for g in groups:
            q = self._groups.get(g)
            if q is None:
                q = self._groups[g] = deque()
            q.append(uid)
            count += 1
            if len(q) > self.per_group:
                self._release(q.popleft())
        if not count:
            return  # no group shows it
        self._count[uid] = self._count.get(uid, 0) + count
        if uid not in self.messages:
            self.messages[uid] = msg
            self._index(msg)

    def _release(self, uid: int) -> None:
        left = self._count.get(uid, 0) - 1
        if left > 0:
            self._count[uid] = left
            return
        self._count.pop(uid, None)
        msg = self.messages.pop(uid, None)
        if msg is not None:
            self._unindex(msg)

    def regroup(self, groups_for: Callable[[ChatMessage], Iterable[Hashable]]) -> None:
        """The groups' channels changed: recount the history under the new routing (only on
        a configuration change). Messages no group shows any more are dropped."""
        old = list(self.messages.values())
        self.clear()
        for msg in old:
            self.add(msg, groups_for(msg))

    def clear(self) -> None:
        self.messages.clear()
        self._groups.clear()
        self._count.clear()
        self._by_msg.clear()
        self._by_user.clear()
        self._by_image.clear()

    # ---- indexes ---------------------------------------------------------- #
    def _keys(self, msg: ChatMessage):
        if msg.msg_id:
            yield self._by_msg, (msg.platform, msg.msg_id)
        if msg.user_id:
            yield self._by_user, (msg.platform, msg.user_id)
        for kind, value, _alt in msg.parts:
            if kind != "text":
                yield self._by_image, value

    def _index(self, msg: ChatMessage) -> None:
        for index, key in self._keys(msg):
            index.setdefault(key, set()).add(msg.uid)

    def _unindex(self, msg: ChatMessage) -> None:
        for index, key in self._keys(msg):
            uids = index.get(key)
            if uids is not None:
                uids.discard(msg.uid)
                if not uids:
                    del index[key]

    def _lookup(self, uids) -> list[ChatMessage]:
        # a message removed directly from `messages` may still be listed: skip it
        return [self.messages[u] for u in sorted(uids or ()) if u in self.messages]

    def for_deletion(self, ev: Deletion) -> list[ChatMessage]:
        """Candidates a deletion may hit (the caller checks source and deleted state)."""
        if ev.clear_all:
            return [m for m in self.messages.values() if m.platform == ev.platform]
        found = set()
        if ev.msg_id:
            found |= self._by_msg.get((ev.platform, ev.msg_id), set())
        if ev.user_id:
            found |= self._by_user.get((ev.platform, ev.user_id), set())
        return self._lookup(found)

    def with_image(self, url: str) -> list[ChatMessage]:
        return self._lookup(self._by_image.get(url))
