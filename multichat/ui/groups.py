# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Channel groups: one panel per group with its channel rows and its own chat view.

A group bundles channels that belong together (e.g. one streamer on Twitch, Kick and
YouTube): one channel per platform. More channels of one platform go into more groups.
Each group has its chat on the left and its own video tiles on the right, so side by side
reads "chat 1 | videos 1 | chat 2 | videos 2".
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSplitter, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

from ..chat.models import kick_channel, source_key, twitch_channel
from ..core.config import MAX_CHANNEL_LEN
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
    """One channel: name field, status, video checkbox, connect button."""

    toggle_requested = pyqtSignal(object)  # self
    changed = pyqtSignal()

    def __init__(self, platform: str, value: str = "", video: bool = True) -> None:
        super().__init__()
        self.platform = platform
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(f"<b style='color:{BADGES[platform][0]}'>{NAMES[platform]}</b>")
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
        for w in (self.label, self.edit, self.status, self.video, self.button):
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
    """Header (name, connect, remove), the channel rows and the group's chat view on the
    left, the group's video tiles (if any) on the right."""

    toggle_row = pyqtSignal(object)  # ChannelRow
    connect_group = pyqtSignal(object)  # self
    disconnect_group = pyqtSignal(object)  # self
    remove_group = pyqtSignal(object)  # self
    renamed = pyqtSignal(object)  # self
    changed = pyqtSignal()  # settings to save
    rows_changed = pyqtSignal(object)  # self – a channel was edited or its video box toggled

    def __init__(self, config: dict, index: int, video: QWidget | None = None) -> None:
        super().__init__()
        self.index = index
        self.unread = 0
        self.mentions = 0

        self.name = QLineEdit(config.get("name", ""))
        self.name.setMaxLength(40)
        self.name.setToolTip(_("Name of this group – empty: the first channel's name"))
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
        rows_layout = QVBoxLayout(self.rows_box)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)
        self.rows: dict[str, ChannelRow] = {}
        for p in PLATFORMS:
            c = next((c for c in config.get("channels", []) if c["platform"] == p), {})
            row = ChannelRow(p, c.get("channel", ""), c.get("video", True))
            row.toggle_requested.connect(self.toggle_row)
            row.changed.connect(self.changed)
            row.changed.connect(self._row_changed)
            row.edit.returnPressed.connect(lambda: self.connect_group.emit(self))
            self.rows[p] = row
            rows_layout.addWidget(row)

        self.view = ChatView()
        self.action_bar: QWidget | None = None
        chat = QWidget()
        chat.setMinimumWidth(320)
        self.chat_lay = lay = QVBoxLayout(chat)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(head)
        lay.addWidget(self.rows_box)
        lay.addWidget(self.view, 1)

        # chat | videos – the video panel stays in this splitter for good (moving an OpenGL
        # widget to another parent makes Qt recreate its context)
        self.video = video
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(chat)
        if video is not None:
            self.split.addWidget(video)
            self.split.setStretchFactor(0, 0)
            self.split.setStretchFactor(1, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.split)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._update_placeholder()

    def set_action_bar(self, bar: QWidget) -> None:
        """The user actions (selected user, moderation) – right above the group's chat."""
        self.action_bar = bar
        self.chat_lay.insertWidget(self.chat_lay.indexOf(self.view), bar)

    # ---- rows ------------------------------------------------------------- #
    def _row_changed(self) -> None:
        self._update_placeholder()
        self.rows_changed.emit(self)

    def _fold(self, shown: bool) -> None:
        self.rows_box.setVisible(shown)
        self.fold.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    def all_rows(self) -> list[ChannelRow]:
        return [self.rows[p] for p in PLATFORMS]

    def rows_for(self, key: str) -> list[ChannelRow]:
        return [r for r in self.all_rows() if r.key() == key]

    def keys(self) -> set[str]:
        return {k for k in (r.key() for r in self.all_rows()) if k}

    # ---- name / config ------------------------------------------------------ #
    def channel_name(self) -> str:
        """The first channel entered (Twitch, then Kick, then YouTube) – the group's name
        when none was typed in."""
        for row in self.all_rows():
            value = row.value()
            if value:
                return value if len(value) <= 28 else value[:27] + "…"
        return ""

    def title(self) -> str:
        return self.name.text().strip() or self.channel_name() or default_group_name(self.index)

    def _update_placeholder(self) -> None:
        self.name.setPlaceholderText(self.channel_name() or default_group_name(self.index))

    def reset(self, index: int) -> None:
        """Empties the group so it can be reused as a new one (see GroupsMixin.remove_group)."""
        self.name.setText("")
        for row in self.all_rows():
            row.edit.setText("")
            row.video.setChecked(True)
            row.status.set_full("–")
        self.view.rebuild([])
        self.view.held = False
        self.view.scroll_to_bottom()  # follows the newest message again, hides "↓ New messages (n)"
        self.view.unseen = 0
        self.view._update_jump()
        self.unread = self.mentions = 0
        self.fold.setChecked(True)
        self.set_index(index)

    def set_index(self, index: int) -> None:
        self.index = index
        self._update_placeholder()

    def to_config(self) -> dict:
        return {"name": self.name.text().strip(), "channels": [r.to_config() for r in self.all_rows() if r.edit.text().strip()]}
