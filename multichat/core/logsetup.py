# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Log file for debugging.

Location: ~/.local/state/multichat/multichat.log (rotated, max. 3 × 1 MB).
Tokens, secrets and login codes are redacted before writing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .. import __version__ as APP_VERSION


LOG_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "multichat"
LOG_FILE = LOG_DIR / "multichat.log"

log = logging.getLogger("multichat")

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[\w.\-~+/=]+"), r"\1***"),
    (re.compile(r"(?i)(oauth:)[\w]+"), r"\1***"),
    (
        re.compile(
            r"(?i)([\"']?(?:access_token|refresh_token|client_secret|code_verifier|id_token|token|secret)"
            r"[\"']?\s*[=:]\s*[\"']?)[^\s\"'&,}]+"
        ),
        r"\1***",
    ),
    (re.compile(r"([?&]code=)[^&\s\"']+"), r"\1***"),  # OAuth code in redirect URLs    # cookies in HTTP headers, e.g. "Cookie: …" or hpack's b'cookie'=b'…'
    (re.compile(r"(?i)(\b(?:set-)?cookie['\"]?\s*[=:,]\s*b?['\"]?)[^'\"\n]+"), r"\1***"),
]


def redact(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.levelname = logging.getLevelName(record.levelno).upper()  # Streamlink renames the levels to lowercase
        return redact(super().format(record))


class PrivateFileHandler(RotatingFileHandler):
    """Rotating log file that stays readable only by the user (also after rotation)."""

    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


def setup_logging(debug: bool = False) -> Path:
    LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:  # the log names channels and accounts – only for this user
        LOG_DIR.chmod(0o700)
        LOG_FILE.touch(mode=0o600, exist_ok=True)
        LOG_FILE.chmod(0o600)
    except OSError:
        pass
    fmt = RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = PrivateFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    set_debug(debug)
    # keep libraries from being too chatty
    for noisy in ("websockets", "aiohttp.access", "urllib3", "asyncio", "httpx", "httpcore", "hpack", "h2", "hyperframe"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def excepthook(etype, value, tb):
        log.critical("Unhandled error", exc_info=(etype, value, tb))

    def thread_excepthook(args):
        log.critical("Unhandled error in thread %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    return LOG_FILE


def set_debug(on: bool) -> None:
    logging.getLogger().setLevel(logging.DEBUG if on else logging.INFO)
    logging.getLogger("multichat").setLevel(logging.DEBUG if on else logging.INFO)


def asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    exc = context.get("exception")
    log.error("asyncio: %s", context.get("message", ""), exc_info=exc if exc else None)


def install_qt_handler() -> None:
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    qlog = logging.getLogger("multichat.qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, _ctx, message):
        if "propagateSizeHints" in message:
            return
        qlog.log(levels.get(mode, logging.INFO), message)

    qInstallMessageHandler(handler)


def log_system_info() -> None:
    def ver(mod: str) -> str:
        try:
            m = __import__(mod)
            v = getattr(m, "__version__", None) or getattr(getattr(m, "version", None), "__version__", None)
            return str(v or "ok")
        except Exception as e:  # noqa: BLE001
            return f"missing ({type(e).__name__})"

    distro = "?"
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                distro = line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    from PyQt6.QtCore import QT_VERSION_STR
    from PyQt6.QtGui import QGuiApplication

    log.info("=" * 60)
    log.info("MultiChat %s started%s", APP_VERSION, " (AppImage)" if os.environ.get("APPIMAGE") else "")
    log.info("System: %s | Kernel %s | Python %s", distro, platform.release(), platform.python_version())
    log.info("Qt %s | platform: %s | session: %s", QT_VERSION_STR, QGuiApplication.platformName(),
             os.environ.get("XDG_SESSION_TYPE", "?"))
    log.info("Modules: websockets %s, aiohttp %s, curl_cffi %s, pytchat %s, yt-dlp %s",
             ver("websockets"), ver("aiohttp"), ver("curl_cffi"), ver("pytchat"), ver("yt_dlp"))
    log.info("JavaScript runtimes (for YouTube video): %s", _js_runtimes())
    log.info("Log: %s", LOG_FILE)


def _js_runtimes() -> str:
    import shutil
    import subprocess

    found = []
    for name, exe in (("deno", "deno"), ("node", "node"), ("bun", "bun"), ("quickjs", "qjs")):
        path = shutil.which(exe)
        if not path:
            continue
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3)
            v = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else "?"
        except Exception:  # noqa: BLE001
            v = "?"
        found.append(f"{name} {v} ({path})")
    return ", ".join(found) or "none found"


def tail(lines: int = 200) -> str:
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError as e:
        return f"(log not readable: {e})"
