# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: the way of a chat message from the readers' mailbox into the groups'
chat views – batching, the bounded history, full and partial re-renders, deletions.
Mixed into MainWindow."""
from __future__ import annotations

import logging
import time
from collections import OrderedDict

from ..chat import ChatMessage, Deletion
from ..core.constants import FAVICONS
from ..core.i18n import _
from ..overlay.server import deletion_payload, message_payload
from .groups import GroupPanel
from .window_groups import MAX_MESSAGES

log = logging.getLogger("multichat")

TRIM_STEP = 100  # new messages between two trims of the message list
FLUSH_MS = 100  # incoming messages are shown in batches – busy chats send hundreds per second


class MessagesMixin:
    """Message part of MainWindow (expects messages, panels, filters, overlay, mod, _incoming
    and the flush/render timers from it)."""

    def msg_html(self, m: ChatMessage) -> str:
        return self.view.message_html(m)

    def known_messages(self):
        """Bounded union of live history and passages still displayed in paused groups."""
        seen = set()
        for messages in (self.messages, *(v.retained_messages for v in self.views())):
            for uid, message in messages.items():
                if uid not in seen:
                    seen.add(uid)
                    yield message

    def message_by_uid(self, uid: int) -> ChatMessage | None:
        message = self.messages.get(uid)
        if message is not None:
            return message
        return next((v.retained_messages[uid] for v in self.views() if uid in v.retained_messages), None)

    def _sync_retained_routes(self) -> None:
        """Remove a departed source from the paused model before it can be rebuilt."""
        for panel in self.panels:
            view = panel.view
            removed = [uid for uid, message in view.retained_messages.items() if panel not in self.panels_for(message)]
            if not removed:
                continue
            for uid in removed:
                del view.retained_messages[uid]
            if not view.retained_messages:
                view.reset_history()  # a replacement source starts following immediately
            self.render_all(panel)

    def add_message(self, msg: ChatMessage) -> None:
        """Queues a message; _flush_messages shows the batch (one layout pass instead of one per message)."""
        if self._closing:
            return
        self._incoming.put(msg)
        self._schedule_flush()

    def _schedule_flush(self, urgent: bool = False) -> None:
        if self._closing:
            return
        if urgent:
            self._flush_timer.stop()
            self._flush_messages()
            return
        if not self._flush_timer.isActive():
            self._flush_timer.start(FLUSH_MS)

    def _flush_messages(self) -> None:
        if self._closing:
            return
        events = self._incoming.take(MAX_MESSAGES)
        if not events:
            return
        if len(self._incoming):
            self._flush_timer.start(0)  # yield to Qt between bounded batches
        if self._incoming.dropped > self._reported_drops and time.monotonic() >= self._loss_report_at:
            dropped = self._incoming.dropped - self._reported_drops
            self._reported_drops = self._incoming.dropped
            self._loss_report_at = time.monotonic() + 5
            log.warning("Chat overloaded: dropped %d events (%d total)", dropped, self._incoming.dropped)
            self.statusBar().showMessage(_("Chat overloaded: {n} events dropped").format(n=dropped), 5000)
        batch = []
        for msg in events:
            if isinstance(msg, Deletion):
                self._apply_deletion(msg)
                continue
            batch.append(msg)
            self.messages[msg.uid] = msg
            if self.overlay.running:
                groups = [i + 1 for i, pn in enumerate(self.panels) if pn in self.panels_for(msg)]
                self.overlay.publish(message_payload(msg, self.view.alerts_cfg, groups))
            self.mod.observe(msg)
        self._msg_count += len(batch)
        if len(self.messages) > self._trim_at:
            self._trim_messages()
        visible = self._visible_panels()
        for panel in self.panels:
            shown = [m for m in batch if self.filters[m.platform].isChecked() and panel in self.panels_for(m)]
            if not shown:
                continue
            if panel not in visible:  # counter on the tab
                panel.unread += len(shown)
                panel.mentions += sum(panel.view.mentions_me(m) for m in shown)
            v = panel.view
            if not v.follow:
                # scrolled up: the view stays as it is (appending would also drop lines at the top and
                # move the text you are reading); everything is shown when you return to the bottom
                v.note_unseen(len(shown))
                v.held = True
                continue
            if self._rerender_due(panel):
                continue  # a full re-render is due anyway
            v.retain_messages(shown, MAX_MESSAGES)
            v.append_many(v.items(shown))  # scrolls along
            if v.needs_compaction:
                self.render_all(panel)  # empties Qt's per-image format list (see COMPACT_AFTER)
        if any(p not in visible for p in self.panels):
            self._update_titles()

    def _trim_messages(self) -> None:
        """Keeps the union of every group's last MAX_MESSAGES messages – what a full re-render
        (filter, font size) shows. A message stays as long as one of its groups still counts
        it among its newest; groups sharing a channel never cost each other newer lines.
        Runs every TRIM_STEP new messages."""
        left = {id(pn): MAX_MESSAGES for pn in self.panels}
        keep = set()
        for uid in reversed(self.messages):  # newest first
            m = self.messages[uid]
            for pn in self.panels_for(m):
                if left.get(id(pn), 0) > 0:
                    left[id(pn)] -= 1
                    keep.add(uid)
            if not any(left.values()):
                break
        if len(keep) < len(self.messages):  # also drops messages no group shows any more
            self.messages = OrderedDict((u, m) for u, m in self.messages.items() if u in keep)
        self._trim_at = len(self.messages) + TRIM_STEP

    def render_all(self, panel: GroupPanel | None = None) -> None:
        """Requests a full re-render (filters, deletions, failed images) of all groups or of
        one; coalesced. Hidden tabs wait until shown; visible groups yield between rebuilds."""
        if self._closing:
            return
        if panel is None:
            self._rerender = None
        elif self._rerender is not None:
            self._rerender.add(panel)
        self._render_timer.start()

    def _rerender_due(self, panel: GroupPanel) -> bool:
        return panel in self._render_pending or (
            self._render_timer.isActive() and (self._rerender is None or panel in self._rerender))

    def _follow_changed(self, panel: GroupPanel, following: bool) -> None:
        if following and (panel.view.held or panel.view.retained_messages):
            # Filters can have hidden all arriving lines, so `held` alone is not enough
            # to know whether the pinned model differs from the current live history.
            panel.view.held = False
            self.render_all(panel)  # only this group missed lines

    def _render_now(self) -> None:
        due, self._rerender = self._rerender, set()
        self._render_pending.update(self.panels if due is None else due)
        self._flush_messages()
        self._render_step()

    def _resume_renders(self) -> None:
        if self._render_pending.intersection(self._visible_panels()):
            self._render_step_timer.start()

    def _render_step(self) -> None:
        """At most one bounded document rebuild per Qt event-loop turn."""
        self._render_pending.intersection_update(self.panels)
        panel = next((p for p in self._visible_panels() if p in self._render_pending), None)
        if panel is None:
            return
        self._render_pending.discard(panel)
        v = panel.view
        if v.follow:
            v.held = False
            messages = [m for m in self.messages.values()
                        if self.filters[m.platform].isChecked() and panel in self.panels_for(m)][-MAX_MESSAGES:]
            v.retain_messages(messages, MAX_MESSAGES, replace=True)
        else:
            messages = [m for m in v.retained_messages.values()
                        if self.filters[m.platform].isChecked() and panel in self.panels_for(m)]
        v.rebuild(v.items(messages))
        self._resume_renders()

    def apply_deletion(self, ev: Deletion) -> None:
        if self._closing:
            return
        self._incoming.put(ev)
        self._flush_messages()  # queued messages/deletions retain per-source order

    def _apply_deletion(self, ev: Deletion) -> None:
        changed = []
        for m in self.known_messages():
            if m.platform != ev.platform or m.deleted:
                continue
            if ev.source and m.source and m.source != ev.source:
                continue  # a ban in one channel doesn't touch another channel of the platform
            if ev.clear_all or (ev.msg_id and m.msg_id == ev.msg_id) or (ev.user_id and m.user_id == ev.user_id):
                m.deleted = True
                changed.append(m)
        self.overlay.publish(deletion_payload(ev))
        self.mod.apply_deletion(ev)
        if changed:
            self.update_lines(changed)
            self.mod.history_changed.emit()

    def _image_changed(self, key: str) -> None:
        """An image failed (its lines show the name instead) or arrived after all."""
        if any(key in urls for urls in FAVICONS.values()):  # platform icon: every line shows it
            self.render_all()
        else:
            self.update_lines([m for m in self.known_messages() if any(p[1] == key for p in m.parts)])

    def update_lines(self, msgs) -> None:
        """Re-renders just these messages' lines (busy chats delete and time out all the time –
        rebuilding the whole view each time made the window stutter)."""
        if self._closing:
            return
        msgs = list(msgs)
        for panel in self.panels:
            if self._rerender_due(panel):
                continue  # a full re-render of this group is due anyway
            shown = [m for m in msgs if self.filters[m.platform].isChecked() and panel in self.panels_for(m)
                     and (panel.view.follow or m.uid in panel.view.retained_messages)]
            if len(shown) > 200:  # e.g. "clear chat": one rebuild is cheaper
                self.render_all(panel)
                continue
            if shown:
                panel.view.update_messages(panel.view.items(shown))
                if panel.view.needs_compaction:
                    panel.view.compact()

    def set_selected_user(self, key) -> None:
        """Highlight of the selected user's name in every group."""
        for v in self.views():
            v.selected = key

    def clear_chat(self) -> None:
        self._incoming.clear()
        self.messages.clear()
        self._trim_at = MAX_MESSAGES
        for v in self.views():
            v.reset_history()
