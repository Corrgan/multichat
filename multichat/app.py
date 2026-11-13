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
from .core.tls import ensure_ca_certificates, openssl_info  # noqa: E402

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
    print("OK      icon" if app_icon_path().is_file() else "MISSING app icon")
    try:
        import ssl
    except ImportError as e:
        print(f"MISSING OpenSSL: {e}")
        sys.exit(1)
    print(f"OK      OpenSSL: {openssl_info()}")
    source = ensure_ca_certificates()

    ctx = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    loaded = ctx.cert_store_stats().get("x509_ca", 0)
    lazy_dir = bool(paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath))
    if loaded or lazy_dir:
        print(f"OK      TLS certificates: {source} ({loaded or 'directory'})")
    else:
        ok = False
        print(f"MISSING TLS certificates (OpenSSL looks in {paths.openssl_cafile} / {paths.openssl_capath})")
    from .accounts.store import system_keyring

    kr = system_keyring()
    print(f"OK      secrets: keyring ({type(kr).__name__})" if kr else "OK      secrets: files (no system keyring)")
    sys.exit(0 if ok else 1)


def selftest_video(path: str) -> None:
    """`multichat --selftest-video FILE`: decodes a video with libmpv (no window) – checks
    that libmpv and the system's codecs really work together, not only that they load."""
    import time as _time

    from .video import player

    if not player.load_mpv():
        print(f"FAILED  libmpv: {player.MPV_ERROR}")
        sys.exit(1)
    m = player.mpv.MPV(vo="null", ao="null", hwdec="no", terminal=False, idle=True)
    try:
        m.play(path)
        deadline = _time.monotonic() + 20
        frames = 0
        while _time.monotonic() < deadline:
            frames = m.estimated_frame_number or 0
            if frames >= 10:
                break
            _time.sleep(0.2)
        params = m.video_params or {}
    finally:
        m.terminate()
    if frames >= 10:
        print(f"OK      video decoded: {frames} frames, {params.get('w')}x{params.get('h')} ({params.get('pixelformat')})")
        sys.exit(0)
    print(f"FAILED  video: only {frames} frames decoded from {path}")
    sys.exit(1)


def selftest_net(url: str = "https://api.github.com/") -> None:
    """`multichat --selftest-net [URL]`: one HTTPS request with the app's own network stack –
    checks that TLS and the CA certificates work on this system."""
    print(f"OK      TLS certificates: {ensure_ca_certificates()}")
    import asyncio

    import aiohttp

    async def fetch() -> int:
        async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=20)) as s:
            async with s.get(url, headers={"User-Agent": f"MultiChat/{APP_VERSION}"}) as r:
                return r.status

    try:
        status = asyncio.run(fetch())
    except Exception as e:  # noqa: BLE001
        print(f"FAILED  {url}: {e}")
        sys.exit(1)
    print(f"OK      {url} → HTTP {status}")
    sys.exit(0)


def bring_to_front(win) -> None:
    """Another start asked for the window (see core/single_instance.py)."""
    from PyQt6.QtCore import Qt

    log.info("MultiChat was started again: showing this window")
    if win.isMinimized():
        win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized)
    win.show()
    win.raise_()
    win.activateWindow()  # Wayland compositors may only flash the task bar entry – that is their rule
    from PyQt6.QtWidgets import QApplication

    QApplication.alert(win)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--component-probe":
        from .core.component_probe import main as component_probe

        component_probe()
        return
    ensure_ca_certificates()  # before anything creates an SSL context
    if len(sys.argv) > 1 and sys.argv[1] == "--yt-dlp":
        run_ytdlp(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest-net":
        selftest_net(*sys.argv[2:3])
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest-video":
        selftest_video(sys.argv[2])
    config = load_config()
    setup_logging(config["debug_log"])
    QApplication.setDesktopFileName(APP_ID)  # Wayland app ID → KDE/GNOME find io.github.Corrgan.MultiChat.desktop
    app = QApplication(sys.argv)
    app.setApplicationName("MultiChat")
    app.setApplicationVersion(APP_VERSION)
    if app_icon_path().is_file():
        app.setWindowIcon(QIcon(str(app_icon_path())))  # window icon, also without a .desktop file
    lang = i18n.set_language(config["language"])
    i18n.install_qt_translator(app)
    install_qt_handler()
    log_system_info()
    ca_source = ensure_ca_certificates()
    if ca_source == "none found":
        log.warning("TLS certificates: none found – HTTPS will fail; set SSL_CERT_FILE to a CA bundle")
    else:
        log.info("TLS certificates: %s", ca_source)
    log.info("OpenSSL: %s", openssl_info())
    for warning in config.get("_warnings", []):
        log.warning("Settings: %s", warning)
    log.info("Language: %s (setting: %s)", lang, config["language"])
    instance = None
    if not os.environ.get("MULTICHAT_ALLOW_MULTIPLE"):
        from .core.single_instance import SingleInstance

        instance = SingleInstance()
        if not instance.acquire():
            log.info("MultiChat is already running – its window was brought to the front; this start ends "
                     "(MULTICHAT_ALLOW_MULTIPLE=1 allows a second window)")
            sys.exit(0)
    from .ui.main_window import MainWindow

    win = MainWindow(config)
    if instance is not None:
        instance.raise_requested.connect(lambda: bring_to_front(win))
        app.aboutToQuit.connect(instance.release)
    win.show()
    sys.exit(app.exec())
