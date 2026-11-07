# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""MultiChat – Twitch, Kick and YouTube chat in one window.

Reading works without logging in. To send and moderate, log in to the respective
platform under "Accounts". "Overlay" starts a browser source for OBS.
"""
from __future__ import annotations

import logging
import os
import sys

from . import __version__ as APP_VERSION

if getattr(sys, "frozen", False):
    # Bundled app (AppImage/PyInstaller): child processes such as the browser used for
    # the login should use the system libraries, not the bundled ones.
    _orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if _orig is not None:
        os.environ["LD_LIBRARY_PATH"] = _orig
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)

from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from .core import i18n  # noqa: E402
from .core.config import load_config  # noqa: E402
from .core.constants import APP_ID  # noqa: E402
from .core.logsetup import install_qt_handler, log_system_info, setup_logging  # noqa: E402
from .core.paths import app_icon_path  # noqa: E402

log = logging.getLogger("multichat")


def run_ytdlp(args: list[str]) -> None:
    """`multichat --yt-dlp …` behaves like the yt-dlp program (for mpv's ytdl_hook)."""
    import yt_dlp  # type: ignore

    if not any(a.startswith("--js-runtimes") for a in args):
        for rt in ("deno", "node", "bun", "quickjs"):
            args = ["--js-runtimes", rt, *args]
    sys.exit(yt_dlp.main(args))


def selftest() -> None:
    """`multichat --selftest`: checks that all components can be loaded (e.g. in the AppImage)."""
    import importlib

    ok = True
    for mod in ("streamlink", "streamlink.plugins.twitch", "yt_dlp", "pytchat", "curl_cffi", "aiohttp", "websockets"):
        try:
            m = importlib.import_module(mod)
            print(f"OK      {mod} {getattr(m, '__version__', '')}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"MISSING {mod}: {e}")
    from .video import player

    print("OK      libmpv" if player.load_mpv() else f"MISSING libmpv: {player.MPV_ERROR}")
    missing = [
        code for code, _name in i18n.LANGUAGES
        if code != "en" and not i18n.locale_file(code).is_file()
    ]
    ok = ok and not missing
    print(f"MISSING locales: {', '.join(missing)}" if missing else f"OK      locales ({len(i18n.LANGUAGES)} languages)")
    print("OK      icon" if app_icon_path().is_file() else "MISSING multichat.png")
    from .accounts.store import system_keyring

    kr = system_keyring()
    print(f"OK      secrets: keyring ({type(kr).__name__})" if kr else "OK      secrets: files (no system keyring)")
    sys.exit(0 if ok else 1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--yt-dlp":
        run_ytdlp(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    config = load_config()
    setup_logging(config["debug_log"])
    QApplication.setDesktopFileName(APP_ID)  # Wayland app ID → KDE/GNOME find multichat.desktop
    app = QApplication(sys.argv)
    app.setApplicationName("MultiChat")
    app.setApplicationVersion(APP_VERSION)
    if app_icon_path().is_file():
        app.setWindowIcon(QIcon(str(app_icon_path())))  # window icon, also without a .desktop file
    lang = i18n.set_language(config["language"])
    i18n.install_qt_translator(app)
    install_qt_handler()
    log_system_info()
    log.info("Language: %s (setting: %s)", lang, config["language"])
    from .ui.main_window import MainWindow

    win = MainWindow(config)
    win.show()
    sys.exit(app.exec())
