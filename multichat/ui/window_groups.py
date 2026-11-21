# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: the channel groups – tabs or side by side, routing of messages to
groups, titles and each group's video tiles. Mixed into MainWindow."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QCheckBox, QMessageBox, QTabBar

from ..core.config import MAX_GROUPS
from ..core.constants import NAMES
from ..core.i18n import _
from ..video.tiles import VideoPanel
from .chat_view import ChatView
from .groups import GroupPanel, normalize_channel, uptime_text, viewers_text
from .moderation import ActionBar
from .poll_box import PollBox

log = logging.getLogger("multichat")

MAX_MESSAGES = 1000  # chat lines per group
MAX_SHARED_SEEN = 5000  # shared chat message IDs remembered per group (more than it shows)
_live_icon: QIcon | None = None


def live_icon() -> QIcon:
    """Red dot for the tab of a group with a live channel."""
    global _live_icon
    if _live_icon is None:
        pix = QPixmap(20, 20)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(0, 0, 0, 0))
        painter.setBrush(QColor("#eb0400"))
        painter.drawEllipse(4, 4, 12, 12)
        painter.end()
        _live_icon = QIcon(pix)
    return _live_icon


class GroupsMixin:
    """Groups part of MainWindow (expects panels, tabs, side, readers, config … from it)."""

    def views(self) -> list[ChatView]:
        return [panel.view for panel in self.panels]

    @property
    def view(self) -> ChatView:
        """The chat view of the active group."""
        return self.active_panel().view

    def active_panel(self) -> GroupPanel:
        if self.side_by_side:
            shown = self._side_panels()
            return self._side_active if self._side_active in shown else shown[0]
        i = self.tabs.currentIndex()
        return self.panels[i] if 0 <= i < len(self.panels) else self.panels[0]

    def _visible_panels(self) -> list[GroupPanel]:
        return self._side_panels() if self.side_by_side else [self.active_panel()]

    def _side_panels(self) -> list[GroupPanel]:
        """The groups ticked on their tab – those are shown side by side (all, if none is)."""
        return [pn for pn in self.panels if pn.side] or list(self.panels)

    def _add_tab(self, panel: GroupPanel) -> None:
        """A tab for the group, with a tick box: show it side by side or not."""
        self.tabs.addTab(panel.title())  # may switch the tab → _tab_changed shows the right panel
        tick = QCheckBox()
        tick.setChecked(panel.side)
        tick.setToolTip(_("Show this group side by side"))
        tick.setVisible(self.side_by_side)
        tick.toggled.connect(lambda on, pn=panel, t=tick: self._side_ticked(pn, t, on))
        self.tabs.setTabButton(self.tabs.count() - 1, QTabBar.ButtonPosition.LeftSide, tick)

    def _tab_ticks(self) -> list[QCheckBox]:
        return [b for i in range(self.tabs.count())
                if isinstance(b := self.tabs.tabButton(i, QTabBar.ButtonPosition.LeftSide), QCheckBox)]

    def _side_ticked(self, panel: GroupPanel, tick: QCheckBox, on: bool) -> None:
        if not on and panel.side and sum(pn.side for pn in self.panels) <= 1:
            tick.setChecked(True)  # at least one group stays on screen
            self.statusBar().showMessage(_("At least one group is shown side by side"), 5000)
            return
        panel.side = on
        if self.side_by_side:
            if on:
                self._side_active = panel
            self._show_panels()  # the window keeps its size: the shown groups share it
        self._schedule_save()

    def side_by_side_chosen(self, on: bool) -> None:
        """Side by side switched on in the menu: it starts with the active group only, more
        are added with the tick boxes on the tabs. (Not on start: the saved choice stays.)"""
        if not on or not self.side_by_side:
            return
        i = self.tabs.currentIndex()  # the tab that was selected
        active = self.panels[i] if 0 <= i < len(self.panels) else self.panels[0]
        for i, panel in enumerate(self.panels):
            panel.side = panel is active
            tick = self.tabs.tabButton(i, QTabBar.ButtonPosition.LeftSide)
            if isinstance(tick, QCheckBox):
                tick.blockSignals(True)
                tick.setChecked(panel.side)
                tick.blockSignals(False)
        self._side_active = active
        self._show_panels()
        self._schedule_save()

    def _add_panel(self, cfg: dict) -> GroupPanel:
        video = None
        if self.video_ok:
            video = VideoPanel(self.run, self.config["video"], self.audio)
            video.changed.connect(self._schedule_save)
        panel = GroupPanel(cfg, len(self.panels), video)
        panel.set_action_bar(ActionBar(self.mod, lambda m, pn=panel: pn in self.panels_for(m)))
        sizes = self.config["video"].get("sizes") or [440, 860]
        panel.split.setSizes([int(x) for x in sizes[:2]])
        panel.split.splitterMoved.connect(lambda _pos, _i, pn=panel: self._split_moved(pn))
        v = panel.view
        v.document().setMaximumBlockCount(MAX_MESSAGES)
        v.name_clicked.connect(self.on_anchor)
        v.rerender_needed.connect(lambda pn=panel: self.render_all(pn))  # e.g. its font size changed
        v.image_failed.connect(self._image_changed)
        v.image_restored.connect(self._image_changed)
        v.name_context.connect(self.on_name_context)
        panel.poll_box = PollBox(self.poll_box_vote)  # running polls/predictions, above the chat
        panel.chat_lay.insertWidget(panel.chat_lay.indexOf(panel.action_bar), panel.poll_box)
        v.follow_changed.connect(lambda following, pn=panel: self._follow_changed(pn, following))
        v.held = False  # messages arrived while scrolled up
        if self.panels:  # same look as the others
            v.set_font_px(self.panels[0].view.font_px)
            v.alerts_cfg = self.panels[0].view.alerts_cfg
            v.selected = self.panels[0].view.selected
            v.own = self.panels[0].view.own
        panel.toggle_row.connect(self.toggle_row)
        panel.connect_group.connect(self.connect_group)
        panel.disconnect_group.connect(self.disconnect_group)
        panel.remove_group.connect(self.remove_group)
        panel.renamed.connect(lambda _p: self._update_titles())
        panel.changed.connect(self._schedule_save)
        panel.rows_changed.connect(lambda _p: self._rows_changed())
        if video is not None and hasattr(self, "video_action"):
            video.setVisible(self.video_action.isChecked())
        self.panels.append(panel)
        self.side.addWidget(panel)
        panel.setVisible(self.side_by_side)
        self._add_tab(panel)
        self._rows_changed()
        self._update_titles()
        return panel

    def _reuse_panel(self) -> GroupPanel | None:
        """A removed group, emptied, as the new last group (see remove_group)."""
        if not self._spare:
            return None
        panel = self._spare.pop()
        panel.set_index(len(self.panels))
        first = self.panels[0].view
        panel.view.set_font_px(first.font_px)  # may have changed meanwhile
        panel.view.alerts_cfg = first.alerts_cfg
        panel.view.selected = first.selected
        panel.view.own = first.own
        if panel.video is not None:
            panel.video.setVisible(self.video_action.isChecked())
        self.panels.append(panel)
        self.side.addWidget(panel)  # already in the splitter: only moves to the end (same parent)
        panel.setVisible(self.side_by_side)
        self._add_tab(panel)
        self._rows_changed()
        self._update_titles()
        return panel

    def _split_moved(self, panel: GroupPanel) -> None:
        """Chat/video width: the same for all groups (side by side each keeps its own until
        it is changed)."""
        sizes = panel.split.sizes()
        if len(sizes) == 2 and min(sizes) > 0:
            self.config["video"]["sizes"] = sizes
            self._schedule_save()

    def add_group(self) -> GroupPanel | None:
        if len(self.panels) >= MAX_GROUPS:
            self.statusBar().showMessage(_("At most {n} groups").format(n=MAX_GROUPS), 5000)
            return None
        panel = self._reuse_panel() or self._add_panel({"name": "", "channels": []})
        self._side_active = panel
        self.tabs.setCurrentIndex(len(self.panels) - 1)
        self.side_action.setEnabled(len(self.panels) > 1)
        if self.side_by_side:
            self._fit_width()
        panel.rows["twitch"].edit.setFocus()
        self.save_config()
        return panel

    def remove_group(self, panel: GroupPanel) -> None:
        if len(self.panels) <= 1:
            return
        if panel.keys() and QMessageBox.question(
            self, _("Remove group"), _("Remove the group “{name}” and its channel list?").format(name=panel.title())
        ) != QMessageBox.StandardButton.Yes:
            return
        self.disconnect_group(panel)
        i = self.panels.index(panel)
        self.panels.remove(panel)
        self.tabs.blockSignals(True)
        self.tabs.removeTab(i)
        self.tabs.blockSignals(False)
        # Kept hidden and reused for the next new group: destroying its video players
        # blocked the window for ~0.3 s (GPU decoder teardown on the UI thread).
        panel.hide()
        panel.reset(len(self.panels))
        if panel.video is not None:
            panel.video.set_entries([], shown=False)
            panel.video.forget(set())  # stopped, kept for reuse
        self._spare.append(panel)
        for j, pn in enumerate(self.panels):
            pn.set_index(j)
        if len(self.panels) < 2 and self.side_action.isChecked():
            self.side_action.setChecked(False)
        self.side_action.setEnabled(len(self.panels) > 1)
        self._rows_changed()
        self._show_panels()
        self.save_config()

    def toggle_side_by_side(self, on: bool) -> None:
        """Groups next to each other (chat 1 | videos 1 | chat 2 | videos 2 …), or as tabs."""
        active = self.active_panel() if self.panels else None
        self.side_by_side = bool(on and len(self.panels) > 1)
        self._side_active = active if active is not None and active.side else None
        if not self.side_by_side and active in self.panels:
            self.tabs.setCurrentIndex(self.panels.index(active))
        self._show_panels()
        if self.side_by_side:
            QTimer.singleShot(0, self._fit_width)  # after side_by_side_chosen has picked the groups

    def _fit_width(self) -> None:
        """Side by side switched on: a small window grows a little for the shown groups.
        A maximized or full-screen window is left alone (resizing it made it jump)."""
        if not self.side_by_side or self.isMaximized() or self.isFullScreen():
            return
        per = 900 if self.video_ok and self.video_action.isChecked() else 420
        screen = self.screen().availableGeometry().width() if self.screen() else 1920
        want = min(screen, per * len(self._side_panels()))
        if self.width() < want:
            self.resize(want, self.height())

    def _tab_changed(self) -> None:
        if not self.side_by_side:
            self._show_panels()
            return
        i = self.tabs.currentIndex()  # side by side: a click on a shown group's tab makes it the active one
        if 0 <= i < len(self.panels) and self.panels[i].side and self.panels[i] is not self._side_active:
            self._side_active = self.panels[i]
            self._active_changed()

    def _show_panels(self) -> None:
        """Tabs: only the active group. Side by side: the groups ticked on their tab."""
        if not self.panels:
            return
        for tick in self._tab_ticks():
            tick.setVisible(self.side_by_side)
        active = self.active_panel()
        shown = self._side_panels()
        for panel in self.panels:
            panel.setVisible(panel in shown if self.side_by_side else panel is active)
        self._active_changed()

    def _focus_changed(self, _old, new) -> None:
        if not self.side_by_side or new is None:
            return
        for panel in self.panels:
            if panel.isAncestorOf(new) and panel is not self._side_active:
                self._side_active = panel
                self._active_changed()
                break

    def _active_changed(self) -> None:
        """Another group is active: its videos are shown, the send line targets it."""
        if not self.panels:
            return
        for panel in self._visible_panels():
            panel.unread = panel.mentions = 0
        self._update_titles()
        self._refresh_targets()
        self._update_video()
        for v in self.views():
            v.update_animations()  # hidden tabs pause their animations
        self._resume_renders()

    def _update_titles(self) -> None:
        for i, panel in enumerate(self.panels):
            text = panel.title()
            live = [(r, self._uptime(r.key())) for r in panel.all_rows() if self._is_live(r.key())]
            # how long the streamer has been live: one channel's answer is enough (the rows
            # are in the order Twitch, Kick, YouTube)
            running = next((up for _r, up in live if up is not None), None)
            if running is not None:
                text += f" · {uptime_text(running)}"
            counts = [n for r, _up in live if (n := self._viewers(r.key())) is not None]
            if counts:  # the streamer's audience over all platforms that say
                text += f" · 👥 {viewers_text(sum(counts))}"
            if panel.unread:
                text += f" ({'@' if panel.mentions else ''}{min(panel.unread, 999)})"
            if i < self.tabs.count():
                self.tabs.setTabText(i, text)
                self.tabs.setTabTextColor(i, QColor("#ff9a3c") if panel.mentions else QColor())
                names = [f"{NAMES[r.platform]} · {r.value()}"
                         + (" (" + _("{duration} h").format(duration=uptime_text(up)) + ")" if up is not None else "")
                         + (" · " + _("{n} watching").format(n=f"{n:,}".replace(",", "\u202f"))
                            if (n := self._viewers(r.key())) is not None else "")
                         for r, up in live]
                self.tabs.setTabIcon(i, live_icon() if live else QIcon())
                self.tabs.setTabToolTip(i, _("Live: {channels}").format(channels=", ".join(names)) if live else "")
            panel.remove_btn.setVisible(len(self.panels) > 1)

    def _is_live(self, key: str) -> bool:
        watch = getattr(self, "live_watch", None)
        return bool(key) and watch is not None and bool(watch.live.get(key))

    def _uptime(self, key: str) -> float | None:
        watch = getattr(self, "live_watch", None)
        return watch.uptime(key) if watch is not None and key else None

    def _viewers(self, key: str) -> int | None:
        watch = getattr(self, "live_watch", None)
        return watch.viewer_count(key) if watch is not None and key else None

    def _live_changed(self) -> None:
        """A channel went live or offline, or a minute has passed: the dots next to the
        channels, and the dots and running times on the tabs."""
        for panel in self.panels:
            for row in panel.all_rows():
                row.set_live(self._is_live(row.key()), self._uptime(row.key()))
        self._update_titles()

    def _tab_moved(self, source: int, target: int) -> None:
        """A tab was dragged to another place: the groups follow – in the list (what is
        saved, the order side by side and the overlay's group numbers go by it)."""
        if not (0 <= source < len(self.panels) and 0 <= target < len(self.panels)):
            return
        self.panels.insert(target, self.panels.pop(source))
        for j, panel in enumerate(self.panels):
            panel.set_index(j)
            self.side.insertWidget(j, panel)  # same parent: only its place changes
        self._rows_changed()
        self._show_panels()
        self._schedule_save()

    def _rows_changed(self) -> None:
        """A channel was edited: routing, titles, buttons and send targets follow."""
        if getattr(self, "live_watch", None) is not None:
            self.live_watch.rows_changed()
            for panel in self.panels:
                for row in panel.all_rows():
                    row.set_live(self._is_live(row.key()), self._uptime(row.key()))
        self._route = {}
        for panel in self.panels:
            for key in panel.keys():
                self._route.setdefault(key, []).append(panel)
            if panel.video is not None:
                panel.video.forget(panel.keys())  # tiles of channels the group doesn't have any more
        self.message_log.regroup(self.panels_for)  # history counted under the new routing
        for key in [k for k in self.readers if k not in self._route]:
            # the channel was changed or its group removed: its chat would run on unseen
            self._stop_reader(key)
            log.info("%s: connection closed (no group shows it any more)", key)
        self._sync_retained_routes()
        self._update_titles()  # an unnamed group is named after its first channel
        for panel in self.panels:
            if panel.action_bar is not None:
                panel.action_bar.refresh()  # the selected user may belong to another group now
        self._update_conn_buttons()
        self._refresh_targets()
        self._update_video()  # e.g. a video box was ticked

    def _schedule_save(self) -> None:
        if not hasattr(self, "_save_timer"):
            self._save_timer = QTimer(self, singleShot=True, interval=800)
            self._save_timer.timeout.connect(self.save_config)
        self._save_timer.start()

    def panels_for(self, msg) -> list[GroupPanel]:
        """Groups that show this message. Every reader sets the source; a message without one
        (only possible from code that adds messages directly) goes to the first group."""
        if msg.source:
            panels = self._route.get(msg.source, [])
            if getattr(msg, "shared_id", ""):  # Twitch shared chat: one copy per group, not one per channel
                panels = [pn for pn in panels if pn.shared_seen.get(msg.shared_id, msg.uid) == msg.uid]
            return panels
        return [self.panels[0]]

    def note_shared(self, msg) -> None:
        """Remembers the first copy of a shared chat message in each of its groups (before panels_for)."""
        if not msg.shared_id or not msg.source:
            return
        for panel in self._route.get(msg.source, []):
            seen = panel.shared_seen
            if msg.shared_id not in seen:
                seen[msg.shared_id] = msg.uid
                while len(seen) > MAX_SHARED_SEEN:
                    seen.popitem(last=False)

    def toggle_low_latency(self, on: bool) -> None:
        """View → Low video delay: running streams are opened again with the new setting."""
        self.config["video"]["low_latency"] = on
        for panel in self.panels:
            if panel.video is not None:
                panel.video.set_low_latency(on)

    def toggle_video(self, on: bool, resize: bool = True) -> None:
        for panel in self.panels:
            if panel.video is None:
                continue
            if not on:
                panel.video.set_entries([], shown=False)
                panel.video.stop_all()  # saves bandwidth
            panel.video.setVisible(on)
        if on:
            if resize and self.width() < 900:
                self.resize(1300, max(self.height(), 820))
            self._update_video()  # already connected → start the streams right away

    def _video_entries(self, panel: GroupPanel) -> list[tuple[str, str, str]]:
        """The group's connected channels with the video box ticked."""
        entries, seen = [], set()
        for row in panel.all_rows():
            key = row.key()
            if key and key in self.readers and row.video.isChecked() and key not in seen:
                seen.add(key)
                entries.append((key, row.platform, row.value()))
        return entries

    def _update_video(self) -> None:
        """Each group shows its own videos while it is on screen; hidden groups' tiles keep
        running muted for a while (see VideoPanel.set_entries)."""
        if not hasattr(self, "video_action") or not self.video_action.isChecked():
            return
        visible = self._visible_panels()
        for panel in self.panels:
            if panel.video is not None:
                panel.video.set_entries(self._video_entries(panel), shown=panel in visible)
                panel.video.stop_hidden(set(self.readers))  # disconnected channels: stop right away

    normalize_channel = staticmethod(normalize_channel)
