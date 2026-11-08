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
from PyQt6.QtGui import QColor, QImage, QImageReader, QMovie, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QTextBrowser, QToolButton

from ..chat import alerts
from ..chat.models import ChatMessage, user_key
from . import icons
from ..core.constants import BADGES, BROWSER_UA, FAVICONS
from ..core.i18n import _

EMOTE_H = 22  # emote height at the default font size
DEFAULT_FONT = 14
FONT_RANGE = (9, 40)
ROLE_ORDER = ("broadcaster", "moderator", "vip", "subscriber", "verified")
IMAGE_RES = QTextDocument.ResourceType.ImageResource.value
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # emotes are a few KB – anything bigger is not an emote
MAX_CACHED = 1500  # distinct images kept in memory
MAX_ANIMATED = 64  # animations that play at the same time (the most recently shown ones)
FRAME_MS = 50  # repaint rate for animations (20 fps)
RELAYOUT_SCAN = 400  # lines checked for a newly loaded image


def _allowed_url(url: QUrl) -> bool:
    """https only (plain http just for local test servers)."""
    return url.scheme() == "https" or (url.scheme() == "http" and url.host() in ("127.0.0.1", "localhost"))


class ChatView(QTextBrowser):
    """Chat view that loads emote images asynchronously."""

    rerender_needed = pyqtSignal()
    image_failed = pyqtSignal(str)  # an emote that couldn't be loaded – its lines show the name instead
    name_context = pyqtSignal(QUrl)  # right-click on a user name
    follow_changed = pyqtSignal(bool)  # True: sticks to the newest message

    def __init__(self) -> None:
        super().__init__()
        self.setOpenLinks(False)
        self.font_px = DEFAULT_FONT
        self.emote_h = EMOTE_H
        self.selected: tuple[str, str] | None = None  # (platform, user key) of the selected user
        self.alerts_cfg: dict = dict(alerts.DEFAULT_ALERTS)  # which events are highlighted
        self._icons: dict = {}
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
        self._loaded_keys: set[str] = set()  # images loaded since the last relayout
        self._anim = QTimer(self, singleShot=True, interval=200)  # which animations are on screen
        self._anim.timeout.connect(self.update_animations)

        # Auto-scroll: follow the newest message until the user scrolls up; scrolling back to
        # the bottom (or the jump button) follows again. Kept as state instead of checking the
        # position before each append – images loading later make the text taller, and a
        # position check would then wrongly count that as "scrolled up".
        self.follow = True
        self.unseen = 0
        self._programmatic = 0
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)
        bar.rangeChanged.connect(self._on_range)
        self.jump = QToolButton(self.viewport())
        self.jump.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump.setStyleSheet(
            "QToolButton { background:#9147ff; color:white; border:none; border-radius:12px;"
            " padding:4px 14px; font-weight:bold; }"
            "QToolButton:hover { background:#a970ff; }"
        )
        self.jump.clicked.connect(self.scroll_to_bottom)
        self.jump.hide()
        self._update_jump()

    # ---- Auto-scroll ------------------------------------------------------ #
    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _set_follow(self, on: bool) -> None:
        if on:
            self.unseen = 0
        if on != self.follow:
            self.follow = on
            self.follow_changed.emit(on)
        self._update_jump()

    def _on_scroll(self, _value: int) -> None:
        self._anim.start()
        if not self._programmatic:  # moved by the user (wheel, scrollbar, keys)
            self._set_follow(self._at_bottom())

    def _on_range(self, _min: int, _max: int) -> None:
        if self.follow:
            self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        self._programmatic += 1
        try:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())
        finally:
            self._programmatic -= 1
        self._set_follow(True)

    def rebuild(self, htmls: list) -> None:
        """Replaces all messages; keeps following, or keeps the position while scrolled up."""
        bar = self.verticalScrollBar()
        value = bar.value()
        self._programmatic += 1
        try:
            self.clear()
            self.append_many(htmls)
            if not self.follow:
                bar.setValue(value)
        finally:
            self._programmatic -= 1
        if self.follow:
            self.scroll_to_bottom()

    def note_unseen(self, n: int) -> None:
        """Messages that arrived while scrolled up (shown once you return to the bottom)."""
        self.unseen += n
        self._update_jump()

    def _update_jump(self) -> None:
        if self.follow:
            self.jump.hide()
            return
        text = _("New messages") if self.unseen else _("Jump to newest")
        self.jump.setText(f"↓ {text}" + (f" ({self.unseen})" if self.unseen else ""))
        self.jump.adjustSize()
        vp = self.viewport()
        self.jump.move((vp.width() - self.jump.width()) // 2, vp.height() - self.jump.height() - 10)
        self.jump.show()
        self.jump.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.follow:
            self.scroll_to_bottom()
        self._update_jump()

    def loadResource(self, rtype, url: QUrl):  # noqa: N802
        if int(getattr(rtype, "value", rtype)) == IMAGE_RES and url.scheme() == "mcicon":
            # drawn role/alert icons (emoji fonts can't be relied on)
            name = url.path()
            px = max(1, round(self.font_px * 1.1 * self.devicePixelRatioF()))
            key = ("mcicon", name, px)
            if key not in self._icons:
                img = icons.image(name, px) if name in icons.NAMES else self.placeholder
                img.setDevicePixelRatio(self.devicePixelRatioF())
                self._icons[key] = img
            return self._icons[key]
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
            self.image_failed.emit(key)
            return
        self._store(key, img)
        self._loaded_keys.add(key)
        self._relayout.start()
        self._anim.start()

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
        """Marks an image as just shown (least recently shown ones leave the cache first)."""
        if key in self.cache:
            self.cache.move_to_end(key)
            if key in self.movies:
                self._anim.start()

    # ---- What is on screen ------------------------------------------------ #
    @staticmethod
    def _block_images(block) -> set[str]:
        names = set()
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.isImageFormat():
                names.add(fmt.toImageFormat().name())
            it += 1
        return names

    def _visible_images(self) -> set[str]:
        vp = self.viewport()
        block = self.cursorForPosition(vp.rect().topLeft()).block()
        bottom = self.cursorForPosition(vp.rect().bottomRight()).block().blockNumber()
        names: set[str] = set()
        while block.isValid() and block.blockNumber() <= bottom:
            names |= self._block_images(block)
            block = block.next()
        return names

    def update_animations(self) -> None:
        """Only emotes on screen are animated – off-screen ones, or all of them while the window
        is minimised or hidden, are paused (each running animation repaints the chat)."""
        if not self.movies:
            return
        win = self.window()
        active = self.isVisible() and not win.isMinimized()
        visible = self._visible_images() if active else set()
        playing = 0
        for key, (movie, _buf) in self.movies.items():
            run = key in visible and playing < MAX_ANIMATED
            playing += run
            state = movie.state()
            if run and state != QMovie.MovieState.Running:
                if state == QMovie.MovieState.NotRunning:
                    movie.start()
                else:
                    movie.setPaused(False)
            elif not run and state == QMovie.MovieState.Running:
                movie.setPaused(True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._anim.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.update_animations()

    def _do_relayout(self) -> None:
        """Newly loaded images change line heights – lay out just the lines that show them
        (relaying out the whole chat took ~40 ms each time a batch of emotes arrived)."""
        keys, self._loaded_keys = self._loaded_keys, set()
        doc = self.document()
        block = doc.lastBlock()
        for _n in range(RELAYOUT_SCAN):  # images are requested by recent lines
            if not block.isValid():
                break
            if self._block_images(block) & keys:
                doc.markContentsDirty(block.position(), block.length())
            block = block.previous()

    @staticmethod
    def _block_format(bg: str | None) -> QTextBlockFormat:
        fmt = QTextBlockFormat()
        if bg:  # highlighted alert line
            fmt.setBackground(QColor(bg))
            fmt.setTopMargin(3)
            fmt.setBottomMargin(3)
            fmt.setLeftMargin(4)
        return fmt

    def append_many(self, items: list) -> None:
        """Appends several messages in one edit block – one layout pass instead of one per message
        (about twice as fast as append() in a full chat). Items come from items(): (uid, html,
        background or None); a plain HTML string also works. The uid is kept on the text block,
        so update_messages() can later change single lines without rebuilding everything."""
        if not items:
            return
        doc = self.document()
        cur = QTextCursor(doc)
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.beginEditBlock()
        for item in items:
            uid, h, bg = item if isinstance(item, tuple) else (-1, item, None)
            if not doc.isEmpty():
                cur.insertBlock(QTextBlockFormat(), QTextCharFormat())  # no formatting carried over
            cur.insertHtml(h)
            cur.setBlockFormat(self._block_format(bg))
            cur.block().setUserState(uid)
        cur.endEditBlock()

    def update_messages(self, items: list) -> int:
        """Re-renders only the lines of these messages (deleted, selection changed …) instead of
        the whole chat – a full rebuild of 1000 lines blocks the window for a noticeable moment.
        Returns how many lines were updated."""
        wanted = {uid: (h, bg) for uid, h, bg in items}
        if not wanted:
            return 0
        doc = self.document()
        cur = QTextCursor(doc)
        done = 0
        self._programmatic += 1
        cur.beginEditBlock()
        try:
            block = doc.lastBlock()  # changes usually concern recent lines
            while block.isValid() and done < len(wanted):
                hit = wanted.get(block.userState())
                if hit:
                    cur.setPosition(block.position())
                    cur.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
                    cur.removeSelectedText()
                    cur.setCharFormat(QTextCharFormat())
                    cur.insertHtml(hit[0])
                    cur.setBlockFormat(self._block_format(hit[1]))
                    done += 1
                block = block.previous()
        finally:
            cur.endEditBlock()
            self._programmatic -= 1
        if self.follow:
            self.scroll_to_bottom()
        return done

    def items(self, msgs) -> list:
        """Messages → (uid, html, background) for append_many/rebuild/update_messages."""
        out = []
        for m in msgs:
            bg = alerts.COLORS[m.event][0] if m.event and alerts.is_alert(m, self.alerts_cfg) else None
            out.append((m.uid, self.message_html(m), bg))
        return out

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
        self._programmatic += 1
        try:
            self.document().clear()  # also drops the document's image resources
        finally:
            self._programmatic -= 1
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

    def icon_html(self, name: str) -> str:
        h = round(self.font_px * 1.1)
        return f"<img src='mcicon:{name}' width='{h}' height='{h}' align='middle'>"

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
        roles = "".join(self.icon_html(r) for r in ROLE_ORDER if r in m.badges)
        roles = f"{roles}&nbsp;" if roles else ""
        selected = self.selected == (m.platform, user_key(m))
        name_style = f"color:{name_color};font-weight:bold;text-decoration:none"
        if selected:
            name_style += ";background:#3a3a55"
        name = f"<a href='msg:{m.uid}' style='{name_style}'>{html.escape(m.user)}</a>"
        head = f"<span style='color:#888'>{ts}</span> {badge} "
        if m.event:
            title = html.escape(alerts.headline(m))
            if alerts.is_alert(m, self.alerts_cfg):
                title = (f"<span style='color:{alerts.COLORS[m.event][1]};font-weight:bold'>"
                         f"{self.icon_html(m.event)} {title}</span>")
            else:
                title = f"<span style='color:#aaa'>{title}</span>"
            # the name stays clickable (user card, moderation) through the link below or the headline
            if not m.parts:
                return f"{head}<a href='msg:{m.uid}' style='text-decoration:none'>{title}</a>"
            return f"{head}{title}<br>{roles}{name}: {body}"
        return f"{head}{roles}{name}: {body}"
