# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded reader mailbox before Qt's event queue, preserving each source's order."""
from __future__ import annotations

import threading
from collections import OrderedDict, deque

from ..chat.models import ChatMessage, Deletion, Event


class IncomingEvents:
    """Round-robin batches; a busy source cannot evict a quiet source's backlog.

    put() requests one normal wakeup and at most one urgent upgrade. Deletions
    keep their order relative to messages. On overload, messages are discarded
    first; lost moderation events are counted without changing their meaning.
    """

    def __init__(self, per_source: int, capacity: int, max_sources: int = 30) -> None:
        if min(per_source, capacity, max_sources) < 1:
            raise ValueError("mailbox limits must be positive")
        self.per_source, self.capacity, self.max_sources = per_source, capacity, max_sources
        self._queues: OrderedDict[str, deque[Event]] = OrderedDict()
        self._lock = threading.Lock()
        self._size = 0
        self._wake_pending = False
        self._urgent_pending = False
        self._controls = 0
        self.dropped = 0
        self.dropped_deletions = 0

    def __len__(self) -> int:
        with self._lock:
            return self._size

    def _discard(self, key: str) -> bool:
        queue = self._queues[key]
        for index, event in enumerate(queue):
            if isinstance(event, ChatMessage):
                del queue[index]
                self._size -= 1
                self.dropped += 1
                return True
        queue.popleft()
        self._size -= 1
        self._controls -= 1
        self.dropped += 1
        self.dropped_deletions += 1
        return True

    def put(self, event: Event) -> int:
        """0: no wakeup, 1: normal batch, 2: deliver moderation promptly."""
        key = event.source or f"{event.platform}:{getattr(event, 'channel', '')}"
        with self._lock:
            if key not in self._queues:
                if len(self._queues) >= self.max_sources:
                    self.dropped += 1
                    self.dropped_deletions += isinstance(event, Deletion)
                    return 0
                self._queues[key] = deque()
            queue = self._queues[key]
            if len(queue) >= self.per_source:
                self._discard(key)
            if self._size >= self.capacity:
                largest = max(self._queues, key=lambda k: len(self._queues[k]))
                self._discard(largest)
            queue.append(event)
            self._size += 1
            if isinstance(event, Deletion):
                self._controls += 1
                if not self._urgent_pending:
                    self._wake_pending = self._urgent_pending = True
                    return 2
            if self._wake_pending:
                return 0
            self._wake_pending = True
            return 1

    def take(self, limit: int) -> list[Event]:
        with self._lock:
            batch = []
            while self._queues and len(batch) < limit:
                key, queue = self._queues.popitem(last=False)
                if not queue:
                    continue
                event = queue.popleft()
                batch.append(event)
                self._controls -= isinstance(event, Deletion)
                self._size -= 1
                if queue:
                    self._queues[key] = queue
            self._wake_pending = bool(self._size)
            self._urgent_pending = bool(self._controls)
            return batch

    def clear(self) -> None:
        with self._lock:
            self._queues.clear()
            self._size = 0
            self._wake_pending = False
            self._urgent_pending = False
            self._controls = 0
