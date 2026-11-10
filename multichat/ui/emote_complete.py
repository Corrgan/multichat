# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Emote suggestions in the send line, like on the Twitch website.

Typing ":" and at least two letters opens a list of matching emotes (with their images);
↑/↓ choose, Tab/Enter/click insert the emote's code followed by a space, Esc closes. A
complete ":code:" is replaced right away when there is such an emote.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QCompleter, QLineEdit

from .image_io import ImageTransfers, raster_reader, read_scaled

MIN_CHARS = 2  # letters after ":" before suggestions appear
MAX_SHOWN = 40
MAX_ICONS = 600
MAX_ICON_ACTIVE = 6
MAX_ICON_WAITING = 64
MAX_ICON_BYTES = 2_000_000
URL_ROLE = Qt.ItemDataRole.UserRole + 1
_QUERY = re.compile(r"(?:^|(?<=\s)):([\w\-]{%d,})$" % MIN_CHARS)  # ":kap" right before the cursor
_DONE = re.compile(r"(?:^|(?<=\s)):([\w\-]+):$")  # ":kappa:" right before the cursor


def _python_index(text: str, qt_position: int) -> int:
    """Qt positions count UTF-16 code units; Python slices count Unicode codepoints."""
    return len(text.encode("utf-16-le", errors="surrogatepass")[:qt_position * 2]
               .decode("utf-16-le", errors="surrogatepass"))


def _qt_position(text: str, python_index: int) -> int:
    return len(text[:python_index].encode("utf-16-le", errors="surrogatepass")) // 2


def rank(query: str, names, limit: int = MAX_SHOWN) -> list[str]:
    """Names starting with the query first (shortest first), then names containing it."""
    q = query.lower()
    starts, contains = [], []
    for name in names:
        low = name.lower()
        if low.startswith(q):
            starts.append(name)
        elif q in low:
            contains.append(name)
    starts.sort(key=lambda n: (len(n), n.lower()))
    contains.sort(key=lambda n: (len(n), n.lower()))
    return (starts + contains)[:limit]


class EmoteCompleter(QObject):
    """provider() → {code: image URL} of the emotes usable with the current send target."""

    def __init__(self, edit: QLineEdit, provider: Callable[[], dict[str, str]]) -> None:
        super().__init__(edit)
        self._closed = False
        self.edit = edit
        self.provider = provider
        self.model = QStandardItemModel(self)
        self.completer = QCompleter(self)
        self.completer.setModel(self.model)
        self.completer.setWidget(edit)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setMaxVisibleItems(8)
        self._connections = [self.completer.activated[str].connect(self.insert)]
        popup = self.completer.popup()
        popup.setIconSize(QSize(28, 28))
        popup.setUniformItemSizes(True)
        # Installed after QCompleter's own filter, so it sees the keys first: QCompleter
        # would pass Enter on to the line edit (→ the message is sent with ":po" in it) and
        # only then insert the emote.
        popup.installEventFilter(self)
        edit.installEventFilter(self)
        self.icons: OrderedDict[str, QIcon] = OrderedDict()
        self._loading: set[str] = set()
        self._images = ImageTransfers(self, max_active=MAX_ICON_ACTIVE, max_waiting=MAX_ICON_WAITING,
                                      max_bytes=MAX_ICON_BYTES)
        self._net = self._images.nam
        self._connections.extend([self._images.loaded.connect(self._loaded),
                                  self._images.failed.connect(self._loading.discard)])
        self._span: tuple[int, int] | None = None  # (start, end) of the ":query" being completed
        self._span_text = ""
        self._connections.extend([edit.textEdited.connect(self._edited),
                                  edit.cursorPositionChanged.connect(self._cursor_moved),
                                  edit.selectionChanged.connect(self._selection_changed)])

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for connection in self._connections:
            QObject.disconnect(connection)
        self._connections.clear()
        self._images.shutdown()
        self._loading.clear()
        self.hide()
        self.edit.removeEventFilter(self)
        self.completer.popup().removeEventFilter(self)
        self.completer.setWidget(None)
        self.provider = lambda: {}

    # ---- typing ------------------------------------------------------------ #
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.edit:
            if event.type() == QEvent.Type.Close:
                self.shutdown()
            return False
        if self._closed:
            return False
        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            index = self.completer.popup().currentIndex()
            if index.isValid():
                self.insert(index.data())
                return True  # consumed: picks the emote, doesn't send
        return super().eventFilter(obj, event)

    def _edited(self, _text: str = "") -> None:
        if self._closed:
            return
        text = self.edit.text()
        pos = _python_index(text, self.edit.cursorPosition())
        before = text[:pos]
        done = _DONE.search(before)
        if done:  # ":kappa:" → "Kappa"
            emotes = self.provider()
            code = next((c for c in emotes if c.lower() == done.group(1).lower()), None)
            if code:
                self._span = (done.start(), pos)
                self._span_text = text
                self.insert(code)
                return
        m = _QUERY.search(before)
        if not m:
            self.hide()
            return
        emotes = self.provider()
        names = rank(m.group(1), emotes)
        if not names:
            self.hide()
            return
        self._span = (m.start(), pos)
        self._span_text = text
        self.model.clear()
        for name in names:
            item = QStandardItem(name)
            item.setEditable(False)
            url = emotes[name]
            item.setData(url, URL_ROLE)
            item.setIcon(self._icon(url))
            item.setToolTip(name)
            self.model.appendRow(item)
        rect = self.edit.cursorRect()
        rect.setWidth(max(240, self.completer.popup().sizeHintForColumn(0) + 40))
        self.completer.complete(rect)
        self.completer.popup().setCurrentIndex(self.model.index(0, 0))

    def hide(self) -> None:
        self._span = None
        self._span_text = ""
        self.completer.popup().hide()

    def _cursor_moved(self, _old: int, new: int) -> None:
        if self._span and (self.edit.text() != self._span_text
                           or _python_index(self.edit.text(), new) != self._span[1]):
            self.hide()

    def _selection_changed(self) -> None:
        if self.edit.hasSelectedText():
            self.hide()

    def visible(self) -> bool:
        return self.completer.popup().isVisible()

    def insert(self, code: str) -> None:
        """Replaces the ":query" with the emote code and a space."""
        if self._closed or self._span is None:
            return
        start, end = self._span
        text = self.edit.text()
        if (text != self._span_text or self.edit.hasSelectedText()
                or _python_index(text, self.edit.cursorPosition()) != end):
            self.hide()
            return
        # Replace through Qt itself: it keeps the correct UTF-16 cursor and undo history.
        while end < len(text) and text[end] == " ":
            end += 1
        self.hide()
        begin_qt, end_qt = _qt_position(text, start), _qt_position(text, end)
        self.edit.setSelection(begin_qt, end_qt - begin_qt)
        self.edit.insert(code + " ")

    # ---- images ------------------------------------------------------------- #
    def _icon(self, url: str) -> QIcon:
        if self._closed:
            return QIcon()
        icon = self.icons.get(url)
        if icon is not None:
            self.icons.move_to_end(url)
            return icon
        if url.startswith("https://") and url not in self._loading:
            self._loading.add(url)
            if not self._images.request(url):
                self._loading.discard(url)
        return QIcon()

    def _loaded(self, url: str, data) -> None:
        if self._closed:
            return
        self._loading.discard(url)
        checked = raster_reader(data) if 0 < data.size() <= MAX_ICON_BYTES else None
        if checked is None:
            return
        reader, _buffer = checked
        img = read_scaled(reader, reader.size().scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio))
        if img is None:
            return
        icon = QIcon(QPixmap.fromImage(img.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)))
        self.icons[url] = icon
        while len(self.icons) > MAX_ICONS:
            self.icons.popitem(last=False)
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if item is not None and item.data(URL_ROLE) == url:
                item.setIcon(icon)
