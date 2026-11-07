"""Video-Kacheln: libmpv über die OpenGL-Render-API in Qt (läuft unter X11 und Wayland).

libmpv ist eine Systembibliothek (Ubuntu/Debian: libmpv2, Fedora: mpv-libs,
Arch: mpv). In der AppImage ist sie mitgebündelt.
"""
from __future__ import annotations

import asyncio
import ctypes.util
import logging
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QOpenGLContext
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from streams import DEFAULT_QUALITY, QUALITIES, Offline, resolve

log = logging.getLogger("multichat.video")

OFFLINE_RETRY = 60
ERROR_RETRY = 30
ENDED_RETRY = 10
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

mpv = None
_WRAPPER: str | None = None


def ytdl_wrapper() -> str:
    """Kleines Skript, über das mpv „yt-dlp“ aufruft – es startet diese App mit --yt-dlp.
    So nutzt mpv das mitgelieferte yt-dlp (auch in der AppImage)."""
    global _WRAPPER
    if _WRAPPER:
        return _WRAPPER
    import os
    import shlex

    base = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".cache") / "multichat"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "yt-dlp"
    if getattr(sys, "frozen", False):
        cmd = f"exec {shlex.quote(sys.executable)} --yt-dlp \"$@\""
    else:
        main_py = Path(__file__).resolve().parent / "main.py"
        cmd = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(main_py))} --yt-dlp \"$@\""
    path.write_text(f"#!/bin/sh\n{cmd}\n")
    path.chmod(0o755)
    _WRAPPER = str(path)
    log.debug("yt-dlp-Wrapper für mpv: %s → %s", path, cmd)
    return _WRAPPER
MPV_ERROR = ""


def load_mpv():
    """Lädt python-mpv; in der AppImage die mitgelieferte libmpv."""
    global mpv, MPV_ERROR
    if mpv or MPV_ERROR:
        return mpv
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        for cand in (here.parent / "lib" / "libmpv.so.2", here / "_internal" / "libmpv.so.2"):
            if cand.exists():
                orig = ctypes.util.find_library
                ctypes.util.find_library = lambda n, _o=orig, _c=str(cand): _c if n == "mpv" else _o(n)
                break
    try:
        import mpv as _mpv  # type: ignore

        mpv = _mpv
    except (ImportError, OSError) as e:
        MPV_ERROR = str(e)
        log.warning("libmpv nicht ladbar: %s", e)
    return mpv


class CopyLabel(QLabel):
    """Statuszeile: zeigt evtl. gekürzten Text, Rechtsklick kopiert den vollständigen."""

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
        self.setToolTip(f"{text}\n\nRechtsklick → kopieren" if text and text != "–" else "")

    def _menu(self, pos) -> None:
        from PyQt6.QtWidgets import QApplication, QMenu

        m = QMenu(self)
        m.addAction("Status kopieren").triggered.connect(lambda: QApplication.clipboard().setText(self.full))
        m.exec(self.mapToGlobal(pos))


class MpvWidget(QOpenGLWidget):
    clicked = pyqtSignal()
    _frame = pyqtSignal()

    def __init__(self, name: str = "video", ytdl: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(160, 90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        mlog = logging.getLogger(f"multichat.mpv.{name}")
        levels = {"fatal": logging.CRITICAL, "error": logging.ERROR, "warn": logging.WARNING, "info": logging.INFO}

        self.last_error = ""

        def mpv_log(level, component, message):
            if "duplicated MOOV" in message:  # harmlos bei Twitch-Segmenten
                return
            msg = message.rstrip()
            if component == "ytdl_hook" and level in ("error", "fatal") and msg:
                if msg.startswith("ERROR:"):  # die eigentliche yt-dlp-Meldung
                    short = re.sub(r"^ERROR:\s*(\[[\w:]+\]\s*[\w-]+:\s*)?", "", msg)
                    self.last_error = re.sub(r"\s*\(caused by .*$", "", short)[:160]
                elif not self.last_error:  # nur allgemeines „youtube-dl failed“
                    self.last_error = msg
            mlog.log(levels.get(level, logging.DEBUG), "[%s] %s", component, msg)

        extra = {}
        if ytdl:
            extra = {
                "ytdl": True,
                "script_opts": f"ytdl_hook-ytdl_path={ytdl_wrapper()},ytdl_hook-try_ytdl_first=yes",
            }

        self.player = mpv.MPV(
            log_handler=mpv_log,
            vo="libmpv",
            hwdec="auto-safe",
            idle=True,
            keep_open=False,
            osc=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            terminal=False,
            cache=True,
            demuxer_max_bytes="32MiB",
            mute=True,
            user_agent=DEFAULT_UA,
            loglevel="warn",
            **({"ytdl": False} | extra),
        )
        mlog.debug("libmpv %s", getattr(self.player, "mpv_version", "?"))
        self.ctx = None
        self._pending: tuple[str, dict] | None = None
        self._proc = mpv.MpvGlGetProcAddressFn(self._get_proc)
        self._frame.connect(self.update, Qt.ConnectionType.QueuedConnection)

    def play(self, url: str, **opts) -> None:
        """Erst laden, wenn der Render-Kontext steht – sonst spielt mpv nur Ton."""
        self.last_error = ""
        if self.ctx is None:
            self._pending = (url, opts)
        else:
            self.player.loadfile(url, **opts)

    @staticmethod
    def _get_proc(_ctx, name):
        glctx = QOpenGLContext.currentContext()
        if glctx is None:
            return 0
        try:
            return int(glctx.getProcAddress(QByteArray(name)))
        except Exception:  # noqa: BLE001
            return 0

    def initializeGL(self) -> None:  # noqa: N802
        self._free_ctx()
        self.ctx = mpv.MpvRenderContext(self.player, "opengl", opengl_init_params={"get_proc_address": self._proc})
        self.ctx.update_cb = self._frame.emit  # kommt aus dem mpv-Thread → Queued
        self.context().aboutToBeDestroyed.connect(self._free_ctx)
        if self._pending:
            url, opts = self._pending
            self._pending = None
            self.player.loadfile(url, **opts)

    def _free_ctx(self) -> None:
        if self.ctx:
            self.makeCurrent()
            self.ctx.free()
            self.ctx = None
            self.doneCurrent()

    def paintGL(self) -> None:  # noqa: N802
        if self.ctx:
            r = self.devicePixelRatioF()
            self.ctx.render(
                flip_y=True,
                opengl_fbo={"w": int(self.width() * r), "h": int(self.height() * r), "fbo": self.defaultFramebufferObject()},
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()

    def shutdown(self) -> None:
        self._pending = None
        self._free_ctx()
        self.player.terminate()


class VideoTile(QFrame):
    audio_requested = pyqtSignal(object)
    quality_changed = pyqtSignal()

    def __init__(self, platform: str, name: str, color: str, quality: str, runner) -> None:
        super().__init__()
        self.platform = platform
        self.runner = runner
        self.value = ""
        self.quality = quality if quality in dict(QUALITIES) else DEFAULT_QUALITY
        self.gen = 0
        self.state = "idle"
        self.muted = True
        self.setFrameShape(QFrame.Shape.StyledPanel)

        style = self.style()
        self.icon_on = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self.icon_off = style.standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted)

        title = QLabel(f"<b style='color:{color}'>{name}</b>")
        self.status = CopyLabel("–")
        self.status.setStyleSheet("color:#888")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.qbox = QComboBox()
        self.qbox.addItems([q for q, _ in QUALITIES])
        self.qbox.setCurrentText(self.quality)
        self.qbox.setToolTip("Qualität")
        self.qbox.currentTextChanged.connect(self._on_quality)
        self.audio_btn = QToolButton()
        self.audio_btn.setIcon(self.icon_off)
        self.audio_btn.setToolTip("Ton dieser Kachel an/aus (die anderen werden stumm)")
        self.audio_btn.clicked.connect(lambda: self.audio_requested.emit(self))
        reload_btn = QToolButton()
        reload_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        reload_btn.setToolTip("Stream neu laden")
        reload_btn.clicked.connect(self.start)

        head = QHBoxLayout()
        head.setContentsMargins(8, 4, 6, 2)
        head.setSpacing(8)
        head.addWidget(title)
        head.addWidget(self.status, 1)
        head.addWidget(self.qbox)
        head.addWidget(self.audio_btn)
        head.addWidget(reload_btn)

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
        self._show_message("Noch nicht verbunden")

    # ---- Steuerung ------------------------------------------------------- #
    def set_source(self, value: str) -> None:
        value = value.strip()
        self.setVisible(bool(value))
        if value != self.value or self.state in ("idle", "offline", "ended"):
            self.value = value
            self.start() if value else self.stop()

    def start(self) -> None:
        self.retry.stop()
        self.watch.stop()
        self.gen += 1
        if not self.value:
            return
        gen = self.gen
        self.state = "resolving"
        self.set_status("suche Stream …")
        if self.stack.currentIndex() == 0:
            self._show_message("Suche Stream …")
        self.runner(self._resolve(), then=lambda res: self._resolved(gen, res))

    def stop(self) -> None:
        self.gen += 1
        self.retry.stop()
        self.watch.stop()
        self.state = "idle"
        self.video._pending = None
        try:
            self.video.player.command("stop")
        except Exception:  # noqa: BLE001
            pass
        self.set_status("–")
        self._show_message("Gestoppt")

    async def _resolve(self):
        try:
            return "ok", await asyncio.to_thread(resolve, self.platform, self.value, self.quality)
        except Offline:
            return "offline", None
        except Exception as e:  # noqa: BLE001
            log.warning("Video %s %r: %s", self.platform, self.value, e, exc_info=True)
            return "error", str(e)

    def _resolved(self, gen: int, res) -> None:
        if gen != self.gen:
            return  # inzwischen neu gestartet
        kind, data = res
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
            self.video.play(data.url, **opts)
            self.state = "playing"
            self.set_status(f"live: {data.title}" if data.title else "live")
            self.watch.start()
            return
        delay = OFFLINE_RETRY if kind == "offline" else ERROR_RETRY
        nxt = time.strftime("%H:%M", time.localtime(time.time() + delay))
        text = "offline" if kind == "offline" else f"Fehler: {data}"
        self.state = "offline"
        self.set_status(text)
        self._show_message(f"{text}\n\nnächster Versuch {nxt}")
        self.retry.start(delay * 1000)

    def _check(self) -> None:
        if self.state != "playing":
            return
        try:
            idle = self.video.player.idle_active
        except Exception:  # noqa: BLE001
            idle = True
        if idle:
            self.watch.stop()
            err = self.video.last_error
            if err:  # yt-dlp (über mpv) ist gescheitert
                self.state = "offline"
                nxt = time.strftime("%H:%M", time.localtime(time.time() + ERROR_RETRY))
                log.warning("Video %s: yt-dlp/mpv-Fehler: %s", self.platform, err)
                self.set_status(f"Fehler: {err}")
                self._show_message(f"Fehler: {err}\n\nnächster Versuch {nxt}")
                self.retry.start(ERROR_RETRY * 1000)
                return
            self.state = "ended"
            log.info("Video %s: Wiedergabe beendet", self.platform)
            self.set_status("Stream beendet")
            self._show_message("Stream beendet – prüfe erneut …")
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
        self.video.shutdown()


class VideoPanel(QWidget):
    """Rechte Spalte mit einer Kachel pro Plattform."""

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
                "<b>Videos brauchen libmpv.</b><br><br>"
                "Ubuntu/Debian: <code>sudo apt install libmpv2</code><br>"
                "Fedora: <code>sudo dnf install mpv-libs</code><br>"
                "Arch: <code>sudo pacman -S mpv</code><br><br>"
                f"<span style='color:#888'>{MPV_ERROR}</span>"
            )
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(hint)
            return

        self.empty = QLabel("Trag links Kanäle ein und klick „Verbinden“ –\ndann erscheinen hier die Streams.")
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
