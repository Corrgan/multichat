# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: the channel groups – tabs or side by side, routing of messages to
groups, titles and each group's video tiles. Mixed into MainWindow."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QMessageBox

from ..core.config import MAX_GROUPS
from ..core.i18n import _
from ..video.tiles import VideoPanel
from .chat_view import ChatView
from .groups import GroupPanel, normalize_channel
from .moderation import ActionBar

log = logging.getLogger("multichat")

MAX_MESSAGES = 1000  # chat lines per group


class GroupsMixin:
    """Groups part of MainWindow (expects panels, tabs, side, readers, config … from it)."""

    def views(self) -> list[ChatView]:
        return [panel.view for panel in self.panels]

    @property
    def view(self) -> ChatView:
        """The chat view of the active group."""
        return self.active_panel().view

    def active_panel(self) -> GroupPanel:
        if self.side_by_side and self._side_active in self.panels:
            return self._side_active
        i = self.tabs.currentIndex()
        return self.panels[i] if 0 <= i < len(self.panels) else self.panels[0]

    def _visible_panels(self) -> list[GroupPanel]:
        return list(self.panels) if self.side_by_side else [self.active_panel()]

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
        v.anchorClicked.connect(self.on_anchor)
        v.rerender_needed.connect(self.render_all)
        v.image_failed.connect(self._image_failed)
        v.name_context.connect(self.on_name_context)
        v.follow_changed.connect(lambda following, pn=panel: self._follow_changed(pn, following))
        v.held = False  # messages arrived while scrolled up
        if self.panels:  # same look as the others
            v.set_font_px(self.panels[0].view.font_px)
            v.alerts_cfg = self.panels[0].view.alerts_cfg
            v.selected = self.panels[0].view.selected
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
        self.tabs.addTab(panel.title())  # may switch the tab → _tab_changed shows the right panel
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
        panel = self._add_panel({"name": "", "channels": []})
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
        if panel.video is not None:
            panel.video.shutdown()
        self.tabs.blockSignals(True)
        self.tabs.removeTab(i)
        self.tabs.blockSignals(False)
        panel.hide()
        panel.deleteLater()  # the whole window goes on – the panel only leaves the splitter
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
        self._side_active = active
        if not self.side_by_side and active in self.panels:
            self.tabs.setCurrentIndex(self.panels.index(active))
        self._show_panels()
        if self.side_by_side:
            self._fit_width()

    def _fit_width(self) -> None:
        per = 900 if self.video_ok and self.video_action.isChecked() else 420
        screen = self.screen().availableGeometry().width() if self.screen() else 1920
        want = min(screen, per * len(self.panels))
        if self.width() < want:
            self.resize(want, self.height())

    def _tab_changed(self) -> None:
        if not self.side_by_side:
            self._show_panels()

    def _show_panels(self) -> None:
        """Tabs: only the active group. Side by side: all groups."""
        if not self.panels:
            return
        self.tabs.setVisible(not self.side_by_side)
        active = self.active_panel()
        for panel in self.panels:
            panel.setVisible(self.side_by_side or panel is active)
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

    def _update_titles(self) -> None:
        for i, panel in enumerate(self.panels):
            text = panel.title()
            if panel.unread:
                text += f" ({'@' if panel.mentions else ''}{min(panel.unread, 999)})"
            if i < self.tabs.count():
                self.tabs.setTabText(i, text)
                self.tabs.setTabTextColor(i, QColor("#ff9a3c") if panel.mentions else QColor())
            panel.remove_btn.setVisible(len(self.panels) > 1)

    def _rows_changed(self) -> None:
        """A channel was edited: routing, titles, buttons and send targets follow."""
        self._route = {}
        for panel in self.panels:
            for key in panel.keys():
                self._route.setdefault(key, []).append(panel)
        for key in [k for k in self.readers if k not in self._route]:
            # the channel was changed or its group removed: its chat would run on unseen
            self._stop_reader(key)
            log.info("%s: connection closed (no group shows it any more)", key)
            if panel.video is not None:
                panel.video.forget(panel.keys())  # tiles of channels the group doesn't have any more
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
            return self._route.get(msg.source, [])
        return [self.panels[0]]

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
