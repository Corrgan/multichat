# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Channel groups: one panel per group with its channel rows and its own chat view.

A group bundles channels that belong together (e.g. one streamer on Twitch, Kick and
YouTube). Each platform can have up to MAX_PER_PLATFORM channels per group; the "+"
after a platform's first row adds another one below it.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

from ..chat.models import kick_channel, source_key, twitch_channel
from ..core.config import MAX_CHANNEL_LEN, MAX_PER_PLATFORM
from ..core.constants import BADGES, NAMES, PLATFORMS
from ..core.i18n import _
from . import icons
from .chat_view import ChatView
from .widgets import CopyLabel

def normalize_channel(platform: str, value: str) -> str | None:
    """Channel as the platform expects it (name from a URL, lower case); "" if empty,
    None if invalid. YouTube entries (@handle, URL, video ID) are kept as entered."""
    value = value.strip()
    if not value or platform == "youtube":
        return value[:MAX_CHANNEL_LEN]
    try:
        return twitch_channel(value) if platform == "twitch" else kick_channel(value)
    except ValueError:
        return None


def default_group_name(index: int) -> str:
    return _("Group {n}").format(n=index + 1)


class ChannelRow(QWidget):
    """One channel: name field, status, video checkbox, connect button, +/−."""

    toggle_requested = pyqtSignal(object)  # self
    add_requested = pyqtSignal(str)  # platform
    remove_requested = pyqtSignal(object)  # self
    changed = pyqtSignal()

    def __init__(self, platform: str, first: bool, value: str = "", video: bool = True) -> None:
        super().__init__()
        self.platform = platform
        self.first = first
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(f"<b style='color:{BADGES[platform][0]}'>{NAMES[platform]}</b>" if first else "")
        self.label.setFixedWidth(64)
        self.edit = QLineEdit(value, placeholderText=_("@channel – or video URL") if platform == "youtube"
                              else _("Channel name"))
        self.edit.setMaxLength(MAX_CHANNEL_LEN)
        self.status = CopyLabel("–", max_chars=24)
        self.status.setStyleSheet("color:#888")
        self.status.setFixedWidth(150)
        self.video = QCheckBox()
        self.video.setChecked(video)
        self.video.setToolTip(_("Show this channel's video while the group is active"))
        self.video.setIcon(icons.icon("broadcaster"))  # camera
        self.button = QToolButton()
        self.button.setToolTip(_("Connect / disconnect this channel"))
        self.button.clicked.connect(lambda: self.toggle_requested.emit(self))
        self.extra = QToolButton()
        self.extra.setAutoRaise(True)
        if first:
            self.extra.setText("+")
            self.extra.setToolTip(_("Add another {platform} channel").format(platform=NAMES[platform]))
            self.extra.clicked.connect(lambda: self.add_requested.emit(platform))
        else:
            self.extra.setText("−")
            self.extra.setToolTip(_("Remove this channel"))
            self.extra.clicked.connect(lambda: self.remove_requested.emit(self))
        for w in (self.label, self.edit, self.status, self.video, self.button, self.extra):
            lay.addWidget(w, 1 if w is self.edit else 0)
        self.edit.textChanged.connect(self.changed)
        self.video.toggled.connect(self.changed)

    def value(self) -> str | None:
        return normalize_channel(self.platform, self.edit.text())

    def key(self) -> str:
        """Source key ("twitch:channel"), "" if empty or invalid."""
        v = self.value()
        return source_key(self.platform, v) if v else ""

    def to_config(self) -> dict:
        return {"platform": self.platform, "channel": self.edit.text().strip(), "video": self.video.isChecked()}


class GroupPanel(QWidget):
    """Header (name, connect, remove), the channel rows and the group's chat view."""

    toggle_row = pyqtSignal(object)  # ChannelRow
    connect_group = pyqtSignal(object)  # self
    disconnect_group = pyqtSignal(object)  # self
    remove_group = pyqtSignal(object)  # self
    renamed = pyqtSignal(object)  # self
    changed = pyqtSignal()  # settings to save
    rows_changed = pyqtSignal(object)  # self – a row was added/removed or its text changed

    def __init__(self, config: dict, index: int) -> None:
        super().__init__()
        self.index = index
        self.unread = 0
        self.mentions = 0
        self.rows: dict[str, list[ChannelRow]] = {p: [] for p in PLATFORMS}

        self.name = QLineEdit(config.get("name", ""), placeholderText=default_group_name(index))
        self.name.setMaxLength(40)
        self.name.setToolTip(_("Name of this group"))
        self.name.textChanged.connect(lambda: (self.renamed.emit(self), self.changed.emit()))
        self.fold = QToolButton()
        self.fold.setCheckable(True)
        self.fold.setChecked(True)
        self.fold.setToolTip(_("Show / hide the channel list"))
        self.fold.setArrowType(Qt.ArrowType.DownArrow)
        self.fold.toggled.connect(self._fold)
        self.connect_btn = QPushButton(_("Connect"))
        self.connect_btn.clicked.connect(lambda: self.connect_group.emit(self))
        self.disconnect_btn = QPushButton(_("Disconnect"))
        self.disconnect_btn.setToolTip(_("Stop this group's chats and videos"))
        self.disconnect_btn.clicked.connect(lambda: self.disconnect_group.emit(self))
        self.remove_btn = QToolButton()
        self.remove_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.remove_btn.setToolTip(_("Remove this group"))
        self.remove_btn.setAutoRaise(True)
        self.remove_btn.clicked.connect(lambda: self.remove_group.emit(self))
        head = QHBoxLayout()
        head.addWidget(self.fold)
        head.addWidget(self.name, 1)
        head.addWidget(self.connect_btn)
        head.addWidget(self.disconnect_btn)
        head.addWidget(self.remove_btn)

        self.rows_box = QWidget()
        self.rows_layout = QGridLayout(self.rows_box)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setVerticalSpacing(2)
        entries = {p: [c for c in config.get("channels", []) if c["platform"] == p] for p in PLATFORMS}
        for p in PLATFORMS:
            for i, c in enumerate(entries[p] or [{"channel": "", "video": True}]):
                self._make_row(p, c["channel"], c.get("video", True) if i == 0 else c.get("video", False))
        self._relayout()

        self.view = ChatView()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(head)
        lay.addWidget(self.rows_box)
        lay.addWidget(self.view, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- rows ------------------------------------------------------------- #
    def _make_row(self, platform: str, value: str = "", video: bool = False) -> ChannelRow:
        row = ChannelRow(platform, not self.rows[platform], value, video)
        row.toggle_requested.connect(self.toggle_row)
        row.add_requested.connect(self.add_row)
        row.remove_requested.connect(self.remove_row)
        row.changed.connect(self.changed)
        row.changed.connect(lambda: self.rows_changed.emit(self))
        row.edit.returnPressed.connect(lambda: self.connect_group.emit(self))
        self.rows[platform].append(row)
        return row

    def add_row(self, platform: str) -> ChannelRow | None:
        if len(self.rows[platform]) >= MAX_PER_PLATFORM:
            return None
        row = self._make_row(platform)
        self._relayout()
        row.edit.setFocus()
        self.changed.emit()
        self.rows_changed.emit(self)
        return row

    def remove_row(self, row: ChannelRow) -> None:
        if row.first:
            return
        self.rows[row.platform].remove(row)
        row.setParent(None)
        row.deleteLater()
        self._relayout()
        self.changed.emit()
        self.rows_changed.emit(self)

    def _relayout(self) -> None:
        while self.rows_layout.count():
            self.rows_layout.takeAt(0)
        n = 0
        for p in PLATFORMS:
            for row in self.rows[p]:
                self.rows_layout.addWidget(row, n, 0)
                n += 1
            self.rows[p][0].extra.setEnabled(len(self.rows[p]) < MAX_PER_PLATFORM)

    def _fold(self, shown: bool) -> None:
        self.rows_box.setVisible(shown)
        self.fold.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    def all_rows(self) -> list[ChannelRow]:
        return [r for p in PLATFORMS for r in self.rows[p]]

    def rows_for(self, key: str) -> list[ChannelRow]:
        return [r for r in self.all_rows() if r.key() == key]

    def keys(self) -> set[str]:
        return {k for k in (r.key() for r in self.all_rows()) if k}

    # ---- name / config ------------------------------------------------------ #
    def title(self) -> str:
        return self.name.text().strip() or default_group_name(self.index)

    def set_index(self, index: int) -> None:
        self.index = index
        self.name.setPlaceholderText(default_group_name(index))

    def to_config(self) -> dict:
        return {"name": self.name.text().strip(), "channels": [r.to_config() for r in self.all_rows() if r.edit.text().strip()]}
