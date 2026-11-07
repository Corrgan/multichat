# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Video tiles: one tile per platform in the right-hand column."""
from __future__ import annotations

import asyncio
import logging
import os
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from ..core.i18n import _
from ..ui.widgets import CopyLabel
from . import player
from .player import DEFAULT_UA, MpvWidget, load_mpv
from .streams import DEFAULT_QUALITY, QUALITIES, Offline, normalize_quality, resolve

log = logging.getLogger("multichat.video")


OFFLINE_RETRY = 60
ERROR_RETRY = 30
ENDED_RETRY = 10


class VideoTile(QFrame):
    audio_requested = pyqtSignal(object)
    quality_changed = pyqtSignal()

    def __init__(self, platform: str, name: str, color: str, quality: str, runner) -> None:
        super().__init__()
        self.platform = platform
        self.runner = runner
        self.value = ""
        self.quality = normalize_quality(quality)
        self.gen = 0
        self.state = "idle"
        self.muted = True
        self._closer = None  # stops the running Streamlink data stream
        self._ads = None
        self._title = ""
        self.setFrameShape(QFrame.Shape.StyledPanel)

        style = self.style()
        self.icon_on = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self.icon_off = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted)

        title = QLabel(f"<b style='color:{color}'>{name}</b>")
        self.status = CopyLabel("–")
        self.status.setStyleSheet("color:#888")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.qbox = QComboBox()
        for key, _height, label in QUALITIES:
            self.qbox.addItem(_(label), key)
        self.qbox.setCurrentIndex(max(0, self.qbox.findData(self.quality)))
        self.qbox.setToolTip(_("Quality"))
        self.qbox.currentIndexChanged.connect(lambda _i: self._on_quality(self.qbox.currentData()))
        self.audio_btn = QToolButton()
        self.audio_btn.setIcon(self.icon_off)
        self.audio_btn.setToolTip(_("Sound of this tile on/off (the others are muted)"))
        self.audio_btn.clicked.connect(lambda: self.audio_requested.emit(self))
        reload_btn = QToolButton()
        reload_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        reload_btn.setToolTip(_("Reload stream"))
        reload_btn.clicked.connect(self.start)
        self.icon_stop = style.standardIcon(QStyle.StandardPixmap.SP_MediaStop)
        self.icon_play = style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self.stop_btn = QToolButton()
        self.stop_btn.setIcon(self.icon_stop)
        self.stop_btn.setToolTip(_("Stop / restart this stream"))
        self.stop_btn.clicked.connect(self.toggle_stop)

        head = QHBoxLayout()
        head.setContentsMargins(8, 4, 6, 2)
        head.setSpacing(8)
        head.addWidget(title)
        head.addWidget(self.status, 1)
        head.addWidget(self.qbox)
        head.addWidget(self.audio_btn)
        head.addWidget(reload_btn)
        head.addWidget(self.stop_btn)

        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setStyleSheet("background:#000; color:#777; font-size:13px")
        self.video = MpvWidget(platform, ytdl=platform == "youtube")
        self.video.clicked.connect(lambda: self.audio_requested.emit(self))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.message)
        self.stack.addWidget(self.video)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addWidget(self.stack, 1)

        self.retry = QTimer(self, singleShot=True)
        self.retry.timeout.connect(self.start)
        self.watch = QTimer(self, interval=5000)
        self.watch.timeout.connect(self._check)
        self._show_message(_("Not connected yet"))

    # ---- Control ---------------------------------------------------------- #
    def set_source(self, value: str) -> None:
        value = value.strip()
        self.setVisible(bool(value))
        if value != self.value or self.state in ("idle", "offline", "ended"):
            self.value = value
            self.start() if value else self.stop()

    def _release(self) -> None:
        """Stop the running Streamlink data stream (and close pipes that were never loaded)."""
        if self._closer:
            try:
                self._closer()
            except Exception:  # noqa: BLE001
                pass
        self._closer = None
        self._ads = None
        pending = self.video._pending
        if pending and pending[0].startswith("fdclose://"):
            try:
                os.close(int(pending[0].split("//", 1)[1]))
            except (OSError, ValueError):
                pass
        self.video._pending = None

    def start(self) -> None:
        self.retry.stop()
        self.watch.stop()
        self._release()
        self.gen += 1
        if not self.value:
            return
        gen = self.gen
        self.state = "resolving"
        self.stop_btn.setIcon(self.icon_stop)
        self.set_status(_("looking for stream …"))
        if self.stack.currentIndex() == 0:
            self._show_message(_("Looking for stream …"))
        self.runner(self._resolve(), then=lambda res: self._resolved(gen, res))

    def stop(self) -> None:
        self.gen += 1
        self.retry.stop()
        self.watch.stop()
        self.state = "idle"
        self._release()
        try:
            self.video.player.command("stop")
        except Exception:  # noqa: BLE001
            pass
        self.stop_btn.setIcon(self.icon_play)
        self.set_status(_("stopped"))
        self._show_message(_("Stopped – ▶ starts the stream again"))
        log.info("Video %s: stopped", self.platform)

    def toggle_stop(self) -> None:
        if self.state == "idle":
            self.start()
        else:
            self.stop()

    async def _resolve(self):
        try:
            return "ok", await asyncio.to_thread(resolve, self.platform, self.value, self.quality)
        except Offline:
            return "offline", None
        except Exception as e:  # noqa: BLE001
            log.warning("Video %s %r: %s", self.platform, self.value, e, exc_info=True)
            return "error", str(e)

    def _resolved(self, gen: int, res) -> None:
        kind, data = res
        if gen != self.gen:  # restarted in the meantime → discard the result
            if kind == "ok" and data.closer:
                data.closer()
                if data.pipe_fd is not None:
                    try:
                        os.close(data.pipe_fd)
                    except OSError:
                        pass
            return
        log.info("Video %s: %s%s", self.platform, kind, f" – {data}" if kind == "error" else "")
        if kind == "ok":
            p = self.video.player
            headers = {k: v for k, v in (data.headers or {}).items() if k.lower() not in ("accept-encoding",)}
            p["user-agent"] = headers.pop("User-Agent", DEFAULT_UA)
            p["http-header-fields"] = [f"{k}: {v}" for k, v in headers.items() if "," not in v]
            opts = {"audio_file": data.audio_url} if data.audio_url else {}
            if data.ytdl_format:
                p["ytdl-format"] = data.ytdl_format
            self.stack.setCurrentIndex(1)
            self._closer, self._ads, self._title = data.closer, data.ads, data.title
            self.video.play(data.url, **opts)
            self.state = "playing"
            self.set_status(_("live: {title}").format(title=data.title) if data.title else _("live"))
            self.watch.start()
            return
        delay = OFFLINE_RETRY if kind == "offline" else ERROR_RETRY
        nxt = time.strftime("%H:%M", time.localtime(time.time() + delay))
        text = _("offline") if kind == "offline" else _("Error: {error}").format(error=data)
        self.state = "offline"
        self.set_status(text)
        self._show_message(f"{text}\n\n" + _("next attempt at {time}").format(time=nxt))
        self.retry.start(delay * 1000)

    def _check(self) -> None:
        if self.state != "playing":
            return
        if self._ads is not None:  # Twitch: show skipped ads instead of a silent still image
            ad_text = _("live – skipping ad break, back in a moment …")
            if self._ads.active:
                if self.status.full != ad_text:
                    log.info("Video %s: skipping ad break", self.platform)
                self.set_status(ad_text)
            elif self.status.full == ad_text:
                self.set_status(_("live: {title}").format(title=self._title) if self._title else _("live"))
        try:
            idle = self.video.player.idle_active
        except Exception:  # noqa: BLE001
            idle = True
        if idle:
            self.watch.stop()
            err = self.video.last_error
            if err:  # yt-dlp (via mpv) failed
                self.state = "offline"
                nxt = time.strftime("%H:%M", time.localtime(time.time() + ERROR_RETRY))
                log.warning("Video %s: yt-dlp/mpv error: %s", self.platform, err)
                self.set_status(_("Error: {error}").format(error=err))
                self._show_message(_("Error: {error}").format(error=err) + "\n\n" + _("next attempt at {time}").format(time=nxt))
                self.retry.start(ERROR_RETRY * 1000)
                return
            self.state = "ended"
            log.info("Video %s: playback ended", self.platform)
            self.set_status(_("stream ended"))
            self._show_message(_("Stream ended – checking again …"))
            self.retry.start(ENDED_RETRY * 1000)

    def _on_quality(self, q: str) -> None:
        self.quality = q
        self.quality_changed.emit()
        if self.value:
            self.start()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        try:
            self.video.player.mute = muted
        except Exception:  # noqa: BLE001
            pass
        self.audio_btn.setIcon(self.icon_off if muted else self.icon_on)
        self.setStyleSheet("" if muted else "VideoTile { border: 2px solid #4a9eff; }")

    def set_status(self, text: str) -> None:
        self.status.set_full(text)

    def _show_message(self, text: str) -> None:
        self.message.setText(text)
        self.stack.setCurrentIndex(0)

    def shutdown(self) -> None:
        self.gen += 1
        self.retry.stop()
        self.watch.stop()
        self._release()
        self.video.shutdown()


class VideoPanel(QWidget):
    """Right-hand column with one tile per platform."""

    changed = pyqtSignal()

    def __init__(self, platforms: list[tuple[str, str, str]], runner, config: dict) -> None:
        super().__init__()
        self.config = config
        self.tiles: dict[str, VideoTile] = {}
        self.audio: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        if not load_mpv():
            hint = QLabel(
                "<b>" + _("Videos need libmpv.") + "</b><br><br>"
                "Ubuntu/Debian: <code>sudo apt install libmpv2</code><br>"
                "Fedora: <code>sudo dnf install mpv-libs</code><br>"
                "Arch: <code>sudo pacman -S mpv</code><br><br>"
                f"<span style='color:#888'>{player.MPV_ERROR}</span>"
            )
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(hint)
            return

        self.empty = QLabel(_("Enter channels on the left and click “Connect” –\nthe streams will then appear here."))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("color:#888")
        lay.addWidget(self.empty, 1)
        for p, name, color in platforms:
            tile = VideoTile(p, name, color, config.get("quality", {}).get(p, DEFAULT_QUALITY), runner)
            tile.audio_requested.connect(self.toggle_audio)
            tile.quality_changed.connect(self.changed)
            tile.setVisible(False)
            self.tiles[p] = tile
            lay.addWidget(tile, 1)
        self.audio = config.get("audio")

    @property
    def available(self) -> bool:
        return bool(self.tiles)

    def set_sources(self, values: dict[str, str]) -> None:
        for p, tile in self.tiles.items():
            tile.set_source(values.get(p, ""))
        if self.tiles:
            self.empty.setVisible(not any(v.strip() for v in values.values()))
            self._apply_audio()

    def stop_all(self) -> None:
        for tile in self.tiles.values():
            tile.stop()

    def toggle_audio(self, tile: VideoTile) -> None:
        self.audio = None if self.audio == tile.platform else tile.platform
        self._apply_audio()
        self.changed.emit()

    def _apply_audio(self) -> None:
        for p, t in self.tiles.items():
            t.set_muted(p != self.audio)

    def state(self) -> dict:
        return {"quality": {p: t.quality for p, t in self.tiles.items()}, "audio": self.audio}

    def shutdown(self) -> None:
        for tile in self.tiles.values():
            tile.shutdown()
