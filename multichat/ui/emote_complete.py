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

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QUrl
from PyQt6.QtGui import QIcon, QImage, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QCompleter, QLineEdit

MIN_CHARS = 2  # letters after ":" before suggestions appear
MAX_SHOWN = 40
MAX_ICONS = 600
URL_ROLE = Qt.ItemDataRole.UserRole + 1
_QUERY = re.compile(r"(?:^|(?<=\s)):([\w\-]{%d,})$" % MIN_CHARS)  # ":kap" right before the cursor
_DONE = re.compile(r"(?:^|(?<=\s)):([\w\-]+):$")  # ":kappa:" right before the cursor


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
        self.edit = edit
        self.provider = provider
        self.model = QStandardItemModel(self)
        self.completer = QCompleter(self)
        self.completer.setModel(self.model)
        self.completer.setWidget(edit)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setMaxVisibleItems(8)
        self.completer.activated[str].connect(self.insert)
        popup = self.completer.popup()
        popup.setIconSize(QSize(28, 28))
        popup.setUniformItemSizes(True)
        # Installed after QCompleter's own filter, so it sees the keys first: QCompleter
        # would pass Enter on to the line edit (→ the message is sent with ":po" in it) and
        # only then insert the emote.
        popup.installEventFilter(self)
        self.icons: OrderedDict[str, QIcon] = OrderedDict()
        self._loading: set[str] = set()
        self._net = QNetworkAccessManager(self)
        self._net.setRedirectPolicy(QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._span: tuple[int, int] | None = None  # (start, end) of the ":query" being completed
        edit.textEdited.connect(self._edited)

    # ---- typing ------------------------------------------------------------ #
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            index = self.completer.popup().currentIndex()
            if index.isValid():
                self.insert(index.data())
                return True  # consumed: picks the emote, doesn't send
        return super().eventFilter(obj, event)

    def _edited(self, _text: str = "") -> None:
        text, pos = self.edit.text(), self.edit.cursorPosition()
        before = text[:pos]
        done = _DONE.search(before)
        if done:  # ":kappa:" → "Kappa"
            emotes = self.provider()
            code = next((c for c in emotes if c.lower() == done.group(1).lower()), None)
            if code:
                self._span = (done.start(), pos)
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
        self.completer.popup().hide()

    def visible(self) -> bool:
        return self.completer.popup().isVisible()

    def insert(self, code: str) -> None:
        """Replaces the ":query" with the emote code and a space."""
        if self._span is None:
            return
        start, end = self._span
        text = self.edit.text()
        new = text[:start] + code + " " + text[end:].lstrip(" ")
        self.edit.setText(new)
        self.edit.setCursorPosition(start + len(code) + 1)
        self._span = None
        self.completer.popup().hide()

    # ---- images ------------------------------------------------------------- #
    def _icon(self, url: str) -> QIcon:
        icon = self.icons.get(url)
        if icon is not None:
            self.icons.move_to_end(url)
            return icon
        if url.startswith("https://") and url not in self._loading and len(self._loading) < 60:
            self._loading.add(url)
            reply = self._net.get(QNetworkRequest(QUrl(url)))
            reply.finished.connect(lambda r=reply, u=url: self._loaded(r, u))
        return QIcon()

    def _loaded(self, reply: QNetworkReply, url: str) -> None:
        self._loading.discard(url)
        data = bytes(reply.readAll()) if reply.error() == QNetworkReply.NetworkError.NoError else b""
        reply.deleteLater()
        img = QImage()
        if not data or len(data) > 2_000_000 or not img.loadFromData(data) or img.isNull():
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
