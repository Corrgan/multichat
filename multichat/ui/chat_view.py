# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Chat view: renders messages as HTML and loads emote images asynchronously.

Images are scaled to the line height once when they arrive (instead of on every paint),
kept in a bounded cache, and animated GIF/WebP emotes play via QMovie. Repaints and
re-layouts are coalesced with timers, so busy chats stay smooth.
"""
from __future__ import annotations

import html
import time
from collections import OrderedDict

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader, QMovie, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QTextBrowser

from ..chat.models import ChatMessage, user_key
from ..core.constants import BADGES, BROWSER_UA, FAVICONS
from ..core.i18n import _

EMOTE_H = 22  # emote height at the default font size
DEFAULT_FONT = 14
FONT_RANGE = (9, 40)
ROLE_ICONS = {"broadcaster": "🎥", "moderator": "🛡", "vip": "💎", "subscriber": "★", "verified": "✔"}
IMAGE_RES = QTextDocument.ResourceType.ImageResource.value
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # emotes are a few KB – anything bigger is not an emote
MAX_CACHED = 1500  # distinct images kept in memory
MAX_ANIMATED = 64  # animations that play at the same time (the most recently shown ones)
FRAME_MS = 50  # repaint rate for animations (20 fps)


def _allowed_url(url: QUrl) -> bool:
    """https only (plain http just for local test servers)."""
    return url.scheme() == "https" or (url.scheme() == "http" and url.host() in ("127.0.0.1", "localhost"))


class ChatView(QTextBrowser):
    """Chat view that loads emote images asynchronously."""

    rerender_needed = pyqtSignal()
    name_context = pyqtSignal(QUrl)  # right-click on a user name

    def __init__(self) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.font_px = DEFAULT_FONT
        self.emote_h = EMOTE_H
        self.selected: tuple[str, str] | None = None  # (platform, user key) of the selected user
        self._apply_style()
        self.nam = QNetworkAccessManager(self)
        self.nam.setTransferTimeout(15000)
        self.cache: OrderedDict[str, QImage] = OrderedDict()  # LRU: least recently shown first
        self.movies: dict[str, tuple[QMovie, QBuffer]] = {}
        self.pending: set[str] = set()
        self.failed: set[str] = set()
        self.placeholder = QImage(EMOTE_H, EMOTE_H, QImage.Format.Format_ARGB32)  # resized in set_font_px
        self.placeholder.fill(Qt.GlobalColor.transparent)
        self._relayout = QTimer(self, singleShot=True, interval=120)  # new images change line widths
        self._relayout.timeout.connect(self._do_relayout)
        self._repaint = QTimer(self, singleShot=True, interval=FRAME_MS)
        self._repaint.timeout.connect(self.viewport().update)
        self._prune = QTimer(self, singleShot=True, interval=500)
        self._prune.timeout.connect(self._pause_old_movies)

    def loadResource(self, rtype, url: QUrl):  # noqa: N802
        if int(getattr(rtype, "value", rtype)) == IMAGE_RES and url.scheme() in ("http", "https"):
            key = url.toString()
            img = self.cache.get(key)
            if img is not None:
                return img
            if _allowed_url(url) and key not in self.pending and key not in self.failed:
                self.pending.add(key)
                req = QNetworkRequest(url)
                req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, BROWSER_UA)
                reply = self.nam.get(req)
                reply.downloadProgress.connect(lambda got, _total, r=reply: got > MAX_IMAGE_BYTES and r.abort())
                reply.finished.connect(lambda r=reply, k=key: self._loaded(r, k))
            return self.placeholder
        return super().loadResource(rtype, url)

    # ---- Images ---------------------------------------------------------- #
    def _target_size(self, size: QSize) -> QSize:
        """Size in device pixels for the line height (keeps the aspect ratio)."""
        h = max(1, round(self.emote_h * self.devicePixelRatioF()))
        w = max(1, round(size.width() * h / max(1, size.height())))
        return QSize(min(w, h * 6), h)

    def _scaled(self, img: QImage) -> QImage:
        out = img.scaled(self._target_size(img.size()), Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        out.setDevicePixelRatio(self.devicePixelRatioF())
        return out

    def _loaded(self, reply: QNetworkReply, key: str) -> None:
        self.pending.discard(key)
        data = reply.readAll() if reply.error() == QNetworkReply.NetworkError.NoError else QByteArray()
        reply.deleteLater()
        img = self._decode(key, data) if 0 < data.size() <= MAX_IMAGE_BYTES else None
        if img is None:
            self.failed.add(key)
            self.rerender_needed.emit()
            return
        self._store(key, img)
        self._relayout.start()

    def _decode(self, key: str, data: QByteArray) -> QImage | None:
        buf = QBuffer()
        buf.setData(data)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buf)
        animated = reader.supportsAnimation() and reader.imageCount() != 1
        first_size = reader.size()
        buf.seek(0)
        if animated and first_size.isValid():
            movie = QMovie(buf, QByteArray(), self)
            movie.setScaledSize(self._target_size(first_size))
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            if movie.isValid() and movie.jumpToFrame(0):
                movie.frameChanged.connect(lambda _n, k=key: self._next_frame(k))
                self.movies[key] = (movie, buf)
                movie.start()
                frame = movie.currentImage()
                frame.setDevicePixelRatio(self.devicePixelRatioF())
                return frame
            movie.deleteLater()
        img = QImage()
        return self._scaled(img) if img.loadFromData(data) else None

    def _next_frame(self, key: str) -> None:
        entry = self.movies.get(key)
        if not entry:
            return
        frame = entry[0].currentImage()
        frame.setDevicePixelRatio(self.devicePixelRatioF())
        self.cache[key] = frame
        self.document().addResource(IMAGE_RES, QUrl(key), frame)
        if not self._repaint.isActive():
            self._repaint.start()  # same size → a repaint is enough, no re-layout

    def _store(self, key: str, img: QImage) -> None:
        self.cache[key] = img
        self.document().addResource(IMAGE_RES, QUrl(key), img)
        while len(self.cache) > MAX_CACHED:
            old, _img = self.cache.popitem(last=False)
            entry = self.movies.pop(old, None)
            if entry:
                entry[0].stop()
                entry[0].deleteLater()

    def touch(self, key: str) -> None:
        """Marks an image as just shown; only the most recently shown animations keep playing."""
        if key in self.cache:
            self.cache.move_to_end(key)
            entry = self.movies.get(key)
            if entry:
                if entry[0].state() != QMovie.MovieState.Running:
                    entry[0].setPaused(False)
                if not self._prune.isActive():
                    self._prune.start()

    def _pause_old_movies(self) -> None:
        if len(self.movies) <= MAX_ANIMATED:
            return
        recent = list(self.cache.keys())[-MAX_ANIMATED * 4:]
        playing = [k for k in reversed(recent) if k in self.movies][:MAX_ANIMATED]
        for k, (movie, _buf) in self.movies.items():
            if k not in playing and movie.state() == QMovie.MovieState.Running:
                movie.setPaused(True)

    def _do_relayout(self) -> None:
        doc = self.document()
        doc.markContentsDirty(0, doc.characterCount())

    def append_many(self, htmls: list[str]) -> None:
        """Appends several messages in one edit block – one layout pass instead of one per message
        (about twice as fast as append() in a full chat)."""
        if not htmls:
            return
        doc = self.document()
        cur = QTextCursor(doc)
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.beginEditBlock()
        for h in htmls:
            if not doc.isEmpty():
                cur.insertBlock(QTextBlockFormat(), QTextCharFormat())  # no formatting carried over
            cur.insertHtml(h)
        cur.endEditBlock()

    # ---- Font size and selection ----------------------------------------- #
    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"QTextBrowser {{ background:#18181b; color:#efeff1; font-size:{self.font_px}px; border:none; }}"
        )

    def set_font_px(self, px: int) -> None:
        """Chat text size; emotes scale with it. Cached images are rescaled by loading them again."""
        px = max(FONT_RANGE[0], min(FONT_RANGE[1], int(px)))
        if px == self.font_px:
            return
        self.font_px = px
        self.emote_h = round(EMOTE_H * px / DEFAULT_FONT)
        self.placeholder = QImage(self.emote_h, self.emote_h, QImage.Format.Format_ARGB32)
        self.placeholder.fill(Qt.GlobalColor.transparent)
        for movie, _buf in self.movies.values():
            movie.stop()
            movie.deleteLater()
        self.movies.clear()
        self.cache.clear()
        self.document().clear()  # also drops the document's image resources
        self._apply_style()
        self.rerender_needed.emit()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        anchor = self.anchorAt(event.pos())
        if anchor.startswith("msg:"):
            self.name_context.emit(QUrl(anchor))
            return
        super().contextMenuEvent(event)

    # ---- Rendering ------------------------------------------------------- #
    def _part_html(self, part) -> str:
        kind, value, alt = part
        if kind == "emote" and value not in self.failed:
            self.touch(value)
            return f"<img src='{html.escape(value, quote=True)}' height='{self.emote_h}' align='middle'>"
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
        roles = "".join(ROLE_ICONS[r] for r in m.badges if r in ROLE_ICONS)
        roles = f"<span style='color:#aaa'>{roles}</span>&nbsp;" if roles else ""
        selected = self.selected == (m.platform, user_key(m))
        name_style = f"color:{name_color};font-weight:bold;text-decoration:none"
        if selected:
            name_style += ";background:#3a3a55"
        return (
            f"<span style='color:#888'>{ts}</span> "
            f"{badge} {roles}"
            f"<a href='msg:{m.uid}' style='{name_style}'>"
            f"{html.escape(m.user)}</a>: {body}"
        )
