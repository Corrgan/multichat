# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Small shared widgets."""
from __future__ import annotations


from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMenu,
)

from ..core.i18n import _


class CopyLabel(QLabel):
    """Status line: shows a possibly shortened text; right-click copies the full one."""

    def __init__(self, text: str = "", max_chars: int = 0) -> None:
        super().__init__()
        self.max_chars = max_chars
        self.full = ""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.set_full(text)

    def set_full(self, text: str) -> None:
        self.full = text
        shown = text if not self.max_chars or len(text) <= self.max_chars else text[: self.max_chars - 1] + "…"
        self.setText(shown)
        self.setToolTip(f"{text}\n\n" + _("Right-click → copy") if text and text != "–" else "")

    def _menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction(_("Copy status")).triggered.connect(lambda: QApplication.clipboard().setText(self.full))
        m.exec(self.mapToGlobal(pos))
