# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Video player: libmpv via the OpenGL render API in Qt (works on X11 and Wayland).

libmpv is a system library (Ubuntu/Debian: libmpv2, Fedora: mpv-libs, Arch: mpv).
It is only bundled in the AppImage when built with MPV_BUNDLE=1.
"""
from __future__ import annotations

import ctypes.util
import logging
import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt, pyqtSignal
from PyQt6.QtGui import QOpenGLContext
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QSizePolicy, QWidget,
)

log = logging.getLogger("multichat.video")

DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

mpv = None





_WRAPPER: str | None = None


def ytdl_wrapper() -> str:
    """Small script mpv runs as "yt-dlp" – it starts this app with --yt-dlp,
    so mpv uses the bundled yt-dlp (in the AppImage as well)."""
    global _WRAPPER
    if _WRAPPER:
        return _WRAPPER
    import os
    import shlex
    import tempfile

    base = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".cache") / "multichat"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    base.chmod(0o700)
    path = base / "yt-dlp"
    if getattr(sys, "frozen", False):
        cmd = f"exec {shlex.quote(sys.executable)} --yt-dlp \"$@\""
    else:
        src = Path(__file__).resolve().parents[2]  # directory that contains the package
        cmd = (
            f"PYTHONPATH={shlex.quote(str(src))}${{PYTHONPATH:+:$PYTHONPATH}} "
            f"exec {shlex.quote(sys.executable)} -m multichat --yt-dlp \"$@\""
        )
    # written to a private temporary file and moved into place atomically
    fd, tmp = tempfile.mkstemp(dir=base, prefix=".yt-dlp-")
    with os.fdopen(fd, "w") as f:
        f.write(f"#!/bin/sh\n{cmd}\n")
    os.chmod(tmp, 0o700)
    os.replace(tmp, path)
    _WRAPPER = str(path)
    log.debug("yt-dlp wrapper for mpv: %s → %s", path, cmd)
    return _WRAPPER
MPV_ERROR = ""


def load_mpv():
    """Loads python-mpv; in the AppImage the bundled libmpv if present."""
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
        log.warning("libmpv could not be loaded: %s", e)
    return mpv


class MpvWidget(QOpenGLWidget):
    clicked = pyqtSignal()
    _frame = pyqtSignal()

    def __init__(self, name: str = "video", ytdl: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(160, 90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        mlog = logging.getLogger(f"multichat.mpv.{name}")
        # ffmpeg has one process-wide log callback, so mpv hands its messages to an
        # arbitrary player – log them under their own name instead of a platform's
        flog = logging.getLogger("multichat.mpv.ffmpeg")
        levels = {"fatal": logging.CRITICAL, "error": logging.ERROR, "warn": logging.WARNING, "info": logging.INFO}

        self.last_error = ""

        def mpv_log(level, component, message):
            if "duplicated MOOV" in message:  # harmless with Twitch segments
                return
            msg = message.rstrip()
            if component == "ytdl_hook" and level in ("error", "fatal") and msg:
                if msg.startswith("ERROR:"):  # the actual yt-dlp message
                    short = re.sub(r"^ERROR:\s*(\[[\w:]+\]\s*[\w-]+:\s*)?", "", msg)
                    self.last_error = re.sub(r"\s*\(caused by .*$", "", short)[:160]
                elif not self.last_error:  # only the generic "youtube-dl failed"
                    self.last_error = msg
            lvl = levels.get(level, logging.DEBUG)
            if component == "libmpv_render" and "OpenGL error INVALID_ENUM" in msg:
                lvl = logging.DEBUG  # common with Mesa when creating textures, harmless
            (flog if component.startswith("ffmpeg") else mlog).log(lvl, "[%s] %s", component, msg)

        extra = {}
        if ytdl:
            extra = {
                "ytdl": True,
                "script_opts": f"ytdl_hook-ytdl_path={ytdl_wrapper()},ytdl_hook-try_ytdl_first=yes",
            }

        self.player = mpv.MPV(
            log_handler=mpv_log,
            vo="libmpv",
            hwdec=os.environ.get("MULTICHAT_HWDEC", "auto-safe"),
            idle=True,
            keep_open=False,
            osc=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            terminal=False,
            cache=True,
            demuxer_max_bytes="32MiB",
            demuxer_max_back_bytes="4MiB",  # no seeking back in live streams – default is 50 MiB per tile
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
        """Load only once the render context exists – otherwise mpv plays audio only."""
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
        self.ctx.update_cb = self._frame.emit  # called from the mpv thread → queued
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
