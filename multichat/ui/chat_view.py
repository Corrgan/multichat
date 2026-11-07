# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Chat view: renders messages as HTML and loads emote images asynchronously."""
from __future__ import annotations

import html
import time

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QImage, QTextDocument
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QTextBrowser,
)

from ..chat.models import ChatMessage
from ..core.constants import BADGES, BROWSER_UA, FAVICONS
from ..core.i18n import _

EMOTE_H = 22
IMAGE_RES = QTextDocument.ResourceType.ImageResource.value


class ChatView(QTextBrowser):
    """Chat view that loads emote images asynchronously."""

    rerender_needed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.setStyleSheet("QTextBrowser { background:#18181b; color:#efeff1; font-size:14px; border:none; }")
        self.nam = QNetworkAccessManager(self)
        self.cache: dict[str, QImage] = {}
        self.pending: set[str] = set()
        self.failed: set[str] = set()
        self.placeholder = QImage(EMOTE_H, EMOTE_H, QImage.Format.Format_ARGB32)
        self.placeholder.fill(Qt.GlobalColor.transparent)

    def loadResource(self, rtype, url: QUrl):  # noqa: N802
        if int(getattr(rtype, "value", rtype)) == IMAGE_RES and url.scheme() in ("http", "https"):
            key = url.toString()
            if key in self.cache:
                return self.cache[key]
            if key not in self.pending and key not in self.failed:
                self.pending.add(key)
                req = QNetworkRequest(url)
                req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, BROWSER_UA)
                reply = self.nam.get(req)
                reply.finished.connect(lambda r=reply, k=key: self._loaded(r, k))
            return self.placeholder
        return super().loadResource(rtype, url)

    def _loaded(self, reply: QNetworkReply, key: str) -> None:
        self.pending.discard(key)
        img = QImage()
        ok = reply.error() == QNetworkReply.NetworkError.NoError and img.loadFromData(reply.readAll())
        reply.deleteLater()
        if not ok:
            self.failed.add(key)
            self.rerender_needed.emit()
            return
        self.cache[key] = img
        doc = self.document()
        doc.addResource(IMAGE_RES, QUrl(key), img)
        doc.markContentsDirty(0, doc.characterCount())

    # ---- Rendering ------------------------------------------------------- #
    def _part_html(self, part) -> str:
        kind, value, alt = part
        if kind == "emote" and value not in self.failed:
            return f"<img src='{html.escape(value, quote=True)}' height='{EMOTE_H}' align='middle'>"
        return html.escape(alt if kind == "emote" else value)

    def message_html(self, m: ChatMessage) -> str:
        color, tag = BADGES[m.platform]
        name_color = html.escape(m.color or "#b9a3e3", quote=True)
        ts = time.strftime("%H:%M", time.localtime(m.timestamp))
        body = "".join(self._part_html(p) for p in m.parts)
        if m.deleted:
            body = f"<s style='color:#666'>{body}</s> <i style='color:#666'>({html.escape(_('deleted'))})</i>"
        icon = next((u for u in FAVICONS.get(m.platform, []) if u not in self.failed), None)
        badge = (
            f"<img src='{html.escape(icon, quote=True)}' width='16' height='16' align='middle'>"
            if icon
            else f"<span style='background:{color};color:#000;font-size:11px'>&nbsp;{tag}&nbsp;</span>"
        )
        return (
            f"<span style='color:#888'>{ts}</span> "
            f"{badge} "
            f"<a href='msg:{m.uid}' style='color:{name_color};font-weight:bold;text-decoration:none'>"
            f"{html.escape(m.user)}</a>: {body}"
        )
