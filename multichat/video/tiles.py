# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Video tiles in the right-hand column: one per channel of the active group."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import threading
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QSlider, QStackedWidget, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from ..core.constants import BADGES, NAMES
from ..core.i18n import _
from ..ui.widgets import CopyLabel
from . import player
from .player import DEFAULT_UA, MpvWidget, load_mpv
from .streams import DEFAULT_QUALITY, QUALITIES, Offline, normalize_quality, resolve

log = logging.getLogger("multichat.video")


OFFLINE_RETRY = 60
ERROR_RETRY = 30
ENDED_RETRY = 10
HIDDEN_STOP = 300  # seconds a hidden tile (other group) keeps streaming before it is stopped


class VideoTile(QFrame):
    audio_requested = pyqtSignal(object)
    quality_changed = pyqtSignal()  # also emitted when the volume changes (→ settings are saved)

    def __init__(self, platform: str, name: str, color: str, quality: str, runner, volume: int = 100,
                 key: str = "", channel: str = "") -> None:
        super().__init__()
        self.platform = platform
        self.key = key or platform  # "twitch:somechannel"
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
        if channel:
            title.setText(f"<b style='color:{color}'>{name}</b> <span style='color:#aaa'>{html.escape(channel[:32])}</span>")
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
        self.volume = max(0, min(100, int(volume)))
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(self.volume)
        self.vol.setFixedWidth(80)
        self.vol.setToolTip(_("Volume: {percent} %").format(percent=self.volume))
        self.vol.valueChanged.connect(self._on_volume)
        self.vol.sliderReleased.connect(self.quality_changed)  # save once, not on every step
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
        head.addWidget(self.vol)
        head.addWidget(reload_btn)
        head.addWidget(self.stop_btn)

        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setStyleSheet("background:#000; color:#777; font-size:13px")
        self.video = MpvWidget(platform, ytdl=platform == "youtube")
        self._apply_volume()
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
            # closing Streamlink waits for its download threads – in the background, or the
            # window freezes for a moment on every disconnect
            closer = self._closer

            def close() -> None:
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass

            threading.Thread(target=close, name=f"close-{self.platform}", daemon=True).start()
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
            self.video.player.command_async("stop")  # doesn't wait for mpv
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

    def _on_volume(self, value: int) -> None:
        self.volume = value
        self.vol.setToolTip(_("Volume: {percent} %").format(percent=value))
        self._apply_volume()
        if not self.vol.isSliderDown():  # wheel/keyboard: save right away
            self.quality_changed.emit()

    def _apply_volume(self) -> None:
        try:
            self.video.player.volume = self.volume
        except Exception:  # noqa: BLE001
            pass

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
    """Right-hand column(s) with the video tiles.

    Tabs: the active group's channels in one column. Side by side: one column per group,
    in the same order as the chat columns. A tile is created once per channel and kept:
    switching groups only hides and stops tiles (creating and destroying mpv players made
    every switch hang). All tiles stay children of this widget in one grid – moving an
    OpenGL widget to another parent makes Qt recreate its context."""

    changed = pyqtSignal()

    def __init__(self, runner, config: dict) -> None:
        super().__init__()
        self.config = config
        self.runner = runner
        self.tiles: dict[str, VideoTile] = {}
        self.audio: str | None = config.get("audio")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self._ok = bool(load_mpv())
        self._used = (0, 0)  # rows, columns with stretch set

        if not self._ok:
            hint = QLabel(
                "<b>" + _("Videos need libmpv.") + "</b><br><br>"
                "Ubuntu/Debian: <code>sudo apt install libmpv2</code><br>"
                "Fedora: <code>sudo dnf install mpv-libs</code><br>"
                "Arch: <code>sudo pacman -S mpv</code><br><br>"
                f"<span style='color:#888'>{player.MPV_ERROR}</span>"
            )
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(hint, 0, 0)
            return

        self.empty = QLabel(_("Enter channels on the left and click “Connect” –\nthe streams will then appear here."))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("color:#888")
        self.grid.addWidget(self.empty, 0, 0)
        # An OpenGL widget present from the start: adding the first one to a window that is
        # already shown makes Qt rebuild the window's rendering – with mpv contexts around,
        # that crashed (seen with the AppImage when a second group was added).
        self._keeper = QOpenGLWidget(self)
        self._keeper.setFixedSize(1, 1)
        self.grid.addWidget(self._keeper, 99, 99)

    @property
    def available(self) -> bool:
        return self._ok

    def _setting(self, name: str, key: str, platform: str, default):
        values = self.config.get(name) or {}
        return values.get(key, values.get(platform, default))

    def _tile(self, key: str, platform: str, channel: str) -> VideoTile:
        tile = self.tiles.get(key)
        if tile is None:
            tile = VideoTile(platform, NAMES[platform], BADGES[platform][0],
                             self._setting("quality", key, platform, DEFAULT_QUALITY), self.runner,
                             volume=self._setting("volume", key, platform, 100), key=key, channel=channel)
            tile.setParent(self)
            tile.idle_stop = QTimer(tile, singleShot=True)
            tile.idle_stop.timeout.connect(tile.stop)
            tile.audio_requested.connect(self.toggle_audio)
            tile.quality_changed.connect(self._remember)
            self.tiles[key] = tile
        return tile

    def set_columns(self, columns) -> None:
        """columns: one list of (key, platform, channel) per column. Tiles not listed are
        stopped and hidden (kept for later)."""
        if not self._ok:
            return
        wanted: dict[str, tuple[int, int, str, str]] = {}
        for c, entries in enumerate(columns):
            r = 0
            for key, platform, channel in entries:
                if channel and key not in wanted:
                    wanted[key] = (r, c, platform, channel)
                    r += 1
        for key, tile in self.tiles.items():
            if key not in wanted and tile.isVisible():
                # Hidden, not stopped: restarting a stream rebuilds mpv's video pipeline (~0.2 s
                # per tile on the UI thread), so switching back would stutter. Hidden tiles keep
                # streaming muted and undrawn, and stop after HIDDEN_STOP to save bandwidth.
                tile.hide()
                tile.set_muted(True)
                tile.idle_stop.start(HIDDEN_STOP * 1000)
        for key, (r, c, platform, channel) in wanted.items():
            tile = self._tile(key, platform, channel)
            tile.idle_stop.stop()
            pos = self.grid.getItemPosition(self.grid.indexOf(tile)) if self.grid.indexOf(tile) >= 0 else None
            if not pos or pos[:2] != (r, c):
                self.grid.addWidget(tile, r, c)  # same parent – only the position changes
            tile.show()
            tile.set_source(channel)
        rows = max((r + 1 for r, _c, _p, _ch in wanted.values()), default=1)
        cols = max(1, len(columns))
        for i in range(max(rows, self._used[0])):
            self.grid.setRowStretch(i, 1 if i < rows else 0)
        for i in range(max(cols, self._used[1])):
            self.grid.setColumnStretch(i, 1 if i < cols else 0)
        self._used = (rows, cols)
        self.empty.setVisible(not wanted)
        self._apply_audio()

    def set_sources(self, entries) -> None:
        """One column (tab mode): [(key, platform, channel)]."""
        self.set_columns([list(entries)] if entries else [])

    def forget(self, keep: set[str]) -> None:
        """Removes tiles of channels that no group has any more."""
        for key in [k for k in self.tiles if k not in keep]:
            self._remember()
            tile = self.tiles.pop(key)
            self.grid.removeWidget(tile)
            tile.hide()
            tile.shutdown()
            tile.deleteLater()

    def stop_all(self) -> None:
        for tile in self.tiles.values():
            tile.stop()

    def stop_hidden(self, keep: set[str]) -> None:
        """Stops tiles of channels that are no longer connected (instead of letting them run)."""
        for key, tile in self.tiles.items():
            if key not in keep and tile.state != "idle":
                tile.stop()

    def toggle_audio(self, tile: VideoTile) -> None:
        self.audio = None if self.audio == tile.key else tile.key
        self._apply_audio()
        self.changed.emit()

    def _apply_audio(self) -> None:
        for k, t in self.tiles.items():
            t.set_muted(k != self.audio or t.isHidden())  # tiles of other groups stay silent

    def _remember(self) -> None:
        """Keeps quality and volume per channel, also for tiles that are removed later."""
        for key, t in self.tiles.items():
            self.config.setdefault("quality", {})[key] = t.quality
            self.config.setdefault("volume", {})[key] = t.volume
        self.changed.emit()

    def frame_counts(self) -> dict[str, tuple[int, float]]:
        """(frames drawn, ms spent drawing) per tile since the last call (performance log)."""
        out = {}
        for k, t in self.tiles.items():
            video = getattr(t, "video", None)
            if video is not None and getattr(video, "frames", 0):
                out[k] = (video.frames, video.paint_ms)
                video.frames, video.paint_ms = 0, 0.0
        return out

    def state(self) -> dict:
        quality = dict(self.config.get("quality") or {})
        volume = dict(self.config.get("volume") or {})
        for key, t in self.tiles.items():
            quality[key], volume[key] = t.quality, t.volume
        return {"quality": quality, "volume": volume, "audio": self.audio}

    def shutdown(self) -> None:
        for tile in self.tiles.values():
            tile.shutdown()
