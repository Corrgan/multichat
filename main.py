#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""MultiChat – Twitch, Kick and YouTube chat in one window.

Reading works without logging in. To send and moderate, log in to the respective
platform under "Accounts". "Overlay" starts a browser source for OBS.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import platform as py_platform
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import aiohttp
from PyQt6.QtCore import QT_VERSION_STR, QObject, QProcess, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QActionGroup, QCursor, QDesktopServices, QIcon, QImage, QImageReader, QPixmap, QTextDocument
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

if getattr(sys, "frozen", False):
    # Bundled app (AppImage/PyInstaller): child processes such as the browser used for
    # the login should use the system libraries, not the bundled ones.
    _orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if _orig is not None:
        os.environ["LD_LIBRARY_PATH"] = _orig
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)

import i18n
from accounts import REDIRECT_URI, KickAccount, TokenStore, TwitchAccount, YouTubeAccount
from clients import ChatMessage, Deletion, run_kick, run_twitch, run_youtube
from emotes import EmoteStore
from i18n import _
from logsetup import (
    APP_VERSION,
    LOG_DIR,
    LOG_FILE,
    asyncio_exception_handler,
    install_qt_handler,
    log_system_info,
    set_debug,
    setup_logging,
    tail,
)
from overlay import FAVICONS, OverlayServer, deletion_payload, message_payload
from video import CopyLabel, VideoPanel

log = logging.getLogger("multichat")

APP_ID = "multichat"  # Wayland app ID – must match multichat.desktop
CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "multichat"
CONFIG = CONF_DIR / "config.json"
TOKENS = CONF_DIR / "tokens.json"

PLATFORMS = ("twitch", "kick", "youtube")
NAMES = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}
BADGES = {"twitch": ("#9146FF", "TW"), "kick": ("#53FC18", "KI"), "youtube": ("#FF0000", "YT")}
DEV_CONSOLES = {
    "twitch": "https://dev.twitch.tv/console/apps",
    "kick": "https://kick.com/settings/developer",
    "youtube": "https://console.cloud.google.com/apis/credentials",
}
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
REPO_URL = "https://github.com/Corrgan/multichat"
LOGIN_DOCS = f"{REPO_URL}/blob/main/docs/LOGIN.md"
TIMEOUTS = [(60, "Timeout 1 min"), (600, "Timeout 10 min"), (3600, "Timeout 1 h")]
MAX_MESSAGES = 1000
EMOTE_H = 22
OVERLAY_PORT = 17564
IMAGE_RES = QTextDocument.ResourceType.ImageResource.value


def write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def app_icon_path() -> Path:
    return i18n.resource_dir() / "multichat.png"


class Bridge(QObject):
    """Safely moves events from the asyncio thread into the Qt thread."""

    message = pyqtSignal(object)
    deletion = pyqtSignal(object)
    status = pyqtSignal(str, str, int)  # platform, text, connection generation
    result = pyqtSignal(object, object)


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


class AppsDialog(QDialog):
    """Enter client ID and secret of the user's own developer apps."""

    def __init__(self, parent: QWidget, apps: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Developer apps"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel(
            _(
                "To log in you need your own free developer app per platform. Enter exactly "
                "<b>{redirect}</b> as its redirect URL and copy the client ID and secret here."
            ).format(redirect=REDIRECT_URI)
            + f" <a href='{LOGIN_DOCS}'>{_('Step-by-step guide with all options')}</a>"
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        lay.addWidget(intro)
        self.fields: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        for p in PLATFORMS:
            box = QGroupBox(NAMES[p])
            form = QFormLayout(box)
            link = QLabel(f"<a href='{DEV_CONSOLES[p]}'>{DEV_CONSOLES[p]}</a>")
            link.setOpenExternalLinks(True)
            cid = QLineEdit(apps.get(p, {}).get("client_id", ""))
            sec = QLineEdit(apps.get(p, {}).get("client_secret", ""))
            sec.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(_("Console:"), link)
            form.addRow(_("Client ID:"), cid)
            form.addRow(_("Client secret:"), sec)
            self.fields[p] = (cid, sec)
            lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {p: {"client_id": c.text().strip(), "client_secret": s.text().strip()} for p, (c, s) in self.fields.items()}


class AboutDialog(QDialog):
    """Help → About MultiChat."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("About MultiChat"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        icon = QLabel()
        pix = QPixmap(str(app_icon_path()))
        if not pix.isNull():
            icon.setPixmap(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        head.addWidget(icon)
        title = QLabel(
            f"<h2 style='margin:0'>MultiChat</h2>"
            f"<div>{_('Version {version}').format(version=APP_VERSION)}</div>"
            f"<div style='color:#888'>{_('Twitch, Kick and YouTube chat in one window')}</div>"
        )
        head.addWidget(title, 1)
        lay.addLayout(head)
        license_link = f"<a href='{REPO_URL}/blob/main/LICENSE'>GNU GPL v3.0 or later</a>"
        notices_link = f"<a href='{REPO_URL}/blob/main/THIRD_PARTY_NOTICES.md'>THIRD_PARTY_NOTICES</a>"
        repo_text = REPO_URL.removeprefix("https://")
        body = QLabel(
            f"<p><a href='{REPO_URL}'>{repo_text}</a></p>"
            "<p>" + _("Free software under the {license}.").format(license=license_link) + "<br>"
            + _("Uses third-party libraries under their own licenses – see {notices}.").format(notices=notices_link)
            + "</p>"
            "<p>" + _("Not affiliated with Twitch, Kick, YouTube/Google, 7TV, BetterTTV or FrankerFaceZ.") + "</p>"
            f"<p style='color:#888'>Qt {QT_VERSION_STR} · Python {py_platform.python_version()} · "
            f"{QApplication.platformName()}</p>"
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        lay.addWidget(body)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


class MainWindow(QMainWindow):
    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self.setWindowTitle("MultiChat")
        self.resize(520, 780)
        self.config = config or load_config()

        self.bridge = Bridge()
        self.bridge.message.connect(self.add_message)
        self.bridge.deletion.connect(self.apply_deletion)
        self.bridge.status.connect(self._reader_status)
        self.bridge.result.connect(self._on_result)

        self.loop = asyncio.new_event_loop()
        self.loop.set_exception_handler(asyncio_exception_handler)
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.session = asyncio.run_coroutine_threadsafe(self._make_session(), self.loop).result()

        store = TokenStore(TOKENS)
        args = (store, self.app_creds, lambda: self.session)
        self.accounts = {"twitch": TwitchAccount(*args), "kick": KickAccount(*args), "youtube": YouTubeAccount(*args)}
        self.overlay = OverlayServer(self.loop)
        qt_formats = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
        self.emotes = EmoteStore(lambda: self.session, [f for f in ("webp", "png", "gif") if f in qt_formats])
        self.readers: list = []
        self.conn_gen = 0  # increases with every connect/disconnect
        self.messages: OrderedDict[int, ChatMessage] = OrderedDict()

        self._build_ui()
        self._build_menus()
        self.apply_config()
        self.refresh_accounts()

    # ---- Configuration --------------------------------------------------- #
    def load_config(self) -> dict:
        return load_config()

    def save_config(self) -> None:
        self.config["channels"] = {p: e.text().strip() for p, e in self.inputs.items()}
        self.config["show"] = {p: c.isChecked() for p, c in self.filters.items()}
        self.config["on_top"] = self.top_action.isChecked()
        self.config["extra_emotes"] = self.emote_action.isChecked()
        self.config["debug_log"] = self.debug_action.isChecked()
        if self.video.available:
            self.config["video"].update(self.video.state())
        self.config["video"]["enabled"] = self.video_action.isChecked()
        if self.video.isVisible():
            self.config["video"]["sizes"] = self.splitter.sizes()
        write_private(CONFIG, self.config)

    def apply_config(self) -> None:
        for p in PLATFORMS:
            self.inputs[p].setText(self.config["channels"].get(p, ""))
            self.filters[p].setChecked(self.config["show"].get(p, True))
        self.top_action.setChecked(self.config["on_top"])
        self.emote_action.setChecked(self.config["extra_emotes"])  # "on" loads the global sets
        self.emotes.enabled = self.config["extra_emotes"]
        self.debug_action.setChecked(self.config["debug_log"])
        video_on = bool(self.config["video"].get("enabled", True))
        self.video_action.setChecked(video_on)
        self.video.setVisible(video_on)
        self.resize(1300 if video_on else 520, 820 if video_on else 780)
        sizes = self.config["video"].get("sizes") or [440, 860]
        self.splitter.setSizes([int(s) for s in sizes])
        if self.config["overlay"].get("enabled"):
            self.overlay_action.setChecked(True)

    def app_creds(self, platform: str) -> tuple[str, str]:
        app = self.config["apps"].get(platform) or {}
        return app.get("client_id", ""), app.get("client_secret", "")

    async def _make_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=20))

    # ---- Layout ---------------------------------------------------------- #
    def _build_ui(self) -> None:
        self.inputs = {
            "twitch": QLineEdit(placeholderText=_("Channel name")),
            "kick": QLineEdit(placeholderText=_("Channel name")),
            "youtube": QLineEdit(placeholderText=_("@channel – or video URL")),
        }
        self.status_labels = {p: CopyLabel("–", max_chars=28) for p in PLATFORMS}
        grid = QGridLayout()
        for row, p in enumerate(PLATFORMS):
            grid.addWidget(QLabel(f"<b style='color:{BADGES[p][0]}'>{NAMES[p]}</b>"), row, 0)
            grid.addWidget(self.inputs[p], row, 1)
            lbl = self.status_labels[p]
            lbl.setStyleSheet("color:#888")
            lbl.setFixedWidth(170)
            grid.addWidget(lbl, row, 2)
            self.inputs[p].returnPressed.connect(self.connect_all)
        grid.setColumnStretch(1, 1)

        self.connect_btn = QPushButton(_("Connect"))
        self.connect_btn.clicked.connect(self.connect_all)
        self.disconnect_btn = QPushButton(_("Disconnect"))
        self.disconnect_btn.setToolTip(_("Stop all chats and videos"))
        self.disconnect_btn.clicked.connect(self.disconnect_all)
        self.disconnect_btn.setEnabled(False)
        conn_row = QHBoxLayout()
        conn_row.addWidget(self.connect_btn, 3)
        conn_row.addWidget(self.disconnect_btn, 1)

        filters = QHBoxLayout()
        filters.addWidget(QLabel(_("Show:")))
        self.filters = {}
        for p in PLATFORMS:
            cb = QCheckBox(NAMES[p])
            cb.setChecked(True)
            cb.toggled.connect(self.render_all)
            self.filters[p] = cb
            filters.addWidget(cb)
        filters.addStretch(1)

        self.view = ChatView()
        self.view.document().setMaximumBlockCount(MAX_MESSAGES)
        self.view.anchorClicked.connect(self.on_anchor)
        self.view.rerender_needed.connect(self.render_all)

        send = QHBoxLayout()
        self.target = QComboBox()
        self.input = QLineEdit()
        self.input.returnPressed.connect(self.send)
        self.send_btn = QPushButton(_("Send"))
        self.send_btn.clicked.connect(self.send)
        send.addWidget(self.target)
        send.addWidget(self.input, 1)
        send.addWidget(self.send_btn)

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(grid)
        root.addLayout(conn_row)
        root.addLayout(filters)
        root.addWidget(self.view, 1)
        root.addLayout(send)
        chat = QWidget()
        chat.setLayout(root)
        chat.setMinimumWidth(340)

        # right: video tiles
        self.video = VideoPanel(
            [(p, NAMES[p], BADGES[p][0]) for p in PLATFORMS], self.run, self.config["video"]
        )
        self.video.changed.connect(self.save_config)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(chat)
        self.splitter.addWidget(self.video)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setContentsMargins(9, 9, 9, 0)
        self.setCentralWidget(self.splitter)
        self.statusBar().showMessage(_("Tip: click a name to open the moderation menu."), 8000)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        view = mb.addMenu(_("View"))
        view.addAction(_("Clear chat")).triggered.connect(self.clear_chat)
        self.top_action = view.addAction(_("Always on top"))
        self.top_action.setCheckable(True)
        self.top_action.toggled.connect(self.toggle_on_top)
        self.emote_action = view.addAction(_("7TV / BTTV / FFZ emotes"))
        self.emote_action.setCheckable(True)
        self.emote_action.toggled.connect(self.toggle_emotes)
        self.video_action = view.addAction(_("Video tiles"))
        self.video_action.setCheckable(True)
        self.video_action.toggled.connect(self.toggle_video)

        view.addSeparator()
        lang_menu = view.addMenu(_("Language"))
        group = QActionGroup(self)
        group.setExclusive(True)
        chosen = self.config.get("language", "auto")
        for code, label in [("auto", _("Automatic (system)")), *i18n.LANGUAGES]:
            a = lang_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(code == chosen)
            a.triggered.connect(lambda _checked=False, c=code: self.change_language(c))
            group.addAction(a)
            if code == "auto":
                lang_menu.addSeparator()

        self.accounts_menu = mb.addMenu(_("Accounts"))
        self.accounts_menu.aboutToShow.connect(self._fill_accounts_menu)
        self._fill_accounts_menu()

        ov = mb.addMenu(_("Overlay"))
        self.overlay_action = ov.addAction(_("OBS overlay active"))
        self.overlay_action.setCheckable(True)
        self.overlay_action.toggled.connect(self.toggle_overlay)
        ov.addAction(_("Copy overlay URL")).triggered.connect(self.copy_overlay_url)

        hlp = mb.addMenu(_("Help"))
        hlp.addAction(_("Copy log (last 300 lines)")).triggered.connect(self.copy_log)
        hlp.addAction(_("Open log file")).triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_FILE))))
        hlp.addAction(_("Open log folder")).triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR))))
        self.debug_action = hlp.addAction(_("Verbose log (debug)"))
        self.debug_action.setCheckable(True)
        self.debug_action.toggled.connect(self.toggle_debug)
        hlp.addSeparator()
        hlp.addAction(_("Login setup guide")).triggered.connect(lambda: QDesktopServices.openUrl(QUrl(LOGIN_DOCS)))
        hlp.addAction(_("About MultiChat")).triggered.connect(lambda: AboutDialog(self).exec())

    def _fill_accounts_menu(self) -> None:
        m = self.accounts_menu
        m.clear()
        for p in PLATFORMS:
            acc = self.accounts[p]
            if acc.logged_in:
                a = m.addAction(_("{platform}: {user} – log out").format(platform=NAMES[p], user=acc.user_name))
                a.triggered.connect(lambda _checked=False, p=p: self.logout(p))
            else:
                a = m.addAction(_("Log in to {platform} …").format(platform=NAMES[p]))
                a.triggered.connect(lambda _checked=False, p=p: self.login(p))
        m.addSeparator()
        m.addAction(_("Set up developer apps …")).triggered.connect(self.edit_apps)

    # ---- Helpers --------------------------------------------------------- #
    def run(self, coro, then=None, label: str | None = None, on_error=None):
        """Run a coroutine in the asyncio thread; the result arrives in the Qt thread."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(lambda f: self.bridge.result.emit((then, label, on_error), f))
        return fut

    def _on_result(self, meta, fut) -> None:
        then, label, on_error = meta
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc:
            log.error("%s failed: %s", label or "Action", exc, exc_info=exc)
            self.statusBar().showMessage(_("Error: {error}").format(error=exc), 12000)
            if on_error:
                on_error()
            return
        if label:
            self.statusBar().showMessage(f"{label} ✓", 6000)
        if then:
            then(fut.result())

    def copy_log(self) -> None:
        QApplication.clipboard().setText(tail(300))
        self.statusBar().showMessage(
            _("Log copied (tokens/secrets redacted) – file: {path}").format(path=LOG_FILE), 10000
        )

    def toggle_debug(self, on: bool) -> None:
        set_debug(on)
        self.config["debug_log"] = on
        log.info("Verbose log %s", "on" if on else "off")

    def change_language(self, code: str) -> None:
        if code == self.config.get("language", "auto"):
            return
        self.config["language"] = code
        self.save_config()
        log.info("Language changed to %s", code)
        answer = QMessageBox.question(
            self,
            _("Language"),
            _("The new language takes effect after a restart. Restart MultiChat now?"),
        )
        if answer == QMessageBox.StandardButton.Yes:
            restart_app(self)

    def toggle_emotes(self, on: bool) -> None:
        self.emotes.enabled = on
        if on and not self.emotes.global_loaded:
            def loaded(counts: dict) -> None:
                info = ", ".join(f"{k.upper()} {v}" for k, v in counts.items()) or _("none reachable")
                self.statusBar().showMessage(_("Global emotes loaded: {info}").format(info=info), 6000)

            self.run(self.emotes.load_global(), then=loaded)

    def toggle_video(self, on: bool) -> None:
        if not hasattr(self, "video"):
            return
        if on:
            self.video.setVisible(True)
            if self.width() < 900:
                self.resize(1300, max(self.height(), 820))
            if self.readers:  # already connected → start the streams right away
                self.video.set_sources(self._channels())
        else:
            self.config["video"]["sizes"] = self.splitter.sizes()
            self.video.stop_all()  # saves bandwidth
            self.video.setVisible(False)

    def _channels(self) -> dict[str, str]:
        return {p: e.text().strip() for p, e in self.inputs.items()}

    def toggle_on_top(self, on: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()

    # ---- Connecting ------------------------------------------------------ #
    def _stop_readers(self) -> None:
        self.conn_gen += 1  # ignore status messages from the old connections from now on
        for t in self.readers:
            t.cancel()
        self.readers.clear()

    def connect_all(self) -> None:
        self._stop_readers()
        self.save_config()
        gen = self.conn_gen

        def emit(ev) -> None:
            (self.bridge.message if isinstance(ev, ChatMessage) else self.bridge.deletion).emit(ev)

        def status(platform: str, template: str, **kw) -> None:
            # readers pass an English template – logged in English, shown translated
            log.info("[%s] %s", platform, template.format(**kw))
            self.bridge.status.emit(platform, _(template).format(**kw), gen)

        runners = {"twitch": run_twitch, "kick": run_kick, "youtube": run_youtube}
        for p, edit in self.inputs.items():
            value = edit.text().strip()
            if not value:
                self.set_status(p, "–")
                continue
            coro = runners[p](value, emit, status, self.emotes)
            self.readers.append(asyncio.run_coroutine_threadsafe(coro, self.loop))
        self.connect_btn.setText(_("Reconnect"))
        self.disconnect_btn.setEnabled(bool(self.readers))
        if self.video_action.isChecked():
            self.video.set_sources(self._channels())

    def disconnect_all(self) -> None:
        self._stop_readers()
        self.video.stop_all()
        for p in PLATFORMS:
            self.set_status(p, _("disconnected") if self.inputs[p].text().strip() else "–")
        self.connect_btn.setText(_("Connect"))
        self.disconnect_btn.setEnabled(False)
        log.info("All connections closed")
        self.statusBar().showMessage(_("Disconnected – chats and videos stopped"), 5000)

    def _reader_status(self, platform: str, text: str, gen: int) -> None:
        if gen == self.conn_gen:  # drop messages from a connection that was already closed
            self.set_status(platform, text)

    def set_status(self, platform: str, text: str) -> None:
        self.status_labels[platform].set_full(text)

    # ---- Rendering ------------------------------------------------------- #
    def _part_html(self, part) -> str:
        kind, value, alt = part
        if kind == "emote" and value not in self.view.failed:
            return f"<img src='{html.escape(value, quote=True)}' height='{EMOTE_H}' align='middle'>"
        return html.escape(alt if kind == "emote" else value)

    def msg_html(self, m: ChatMessage) -> str:
        color, tag = BADGES[m.platform]
        name_color = html.escape(m.color or "#b9a3e3", quote=True)
        ts = time.strftime("%H:%M", time.localtime(m.timestamp))
        body = "".join(self._part_html(p) for p in m.parts)
        if m.deleted:
            body = f"<s style='color:#666'>{body}</s> <i style='color:#666'>({html.escape(_('deleted'))})</i>"
        icon = next((u for u in FAVICONS.get(m.platform, []) if u not in self.view.failed), None)
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

    def _at_bottom(self) -> bool:
        bar = self.view.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _scroll_bottom(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def add_message(self, msg: ChatMessage) -> None:
        self.messages[msg.uid] = msg
        while len(self.messages) > MAX_MESSAGES:
            self.messages.popitem(last=False)
        self.overlay.publish(message_payload(msg))
        if self.filters[msg.platform].isChecked():
            at_bottom = self._at_bottom()
            self.view.append(self.msg_html(msg))
            if at_bottom:
                self._scroll_bottom()

    def render_all(self) -> None:
        at_bottom = self._at_bottom()
        self.view.clear()
        for m in self.messages.values():
            if self.filters[m.platform].isChecked():
                self.view.append(self.msg_html(m))
        if at_bottom:
            self._scroll_bottom()

    def apply_deletion(self, ev: Deletion) -> None:
        changed = False
        for m in self.messages.values():
            if m.platform != ev.platform or m.deleted:
                continue
            if ev.clear_all or (ev.msg_id and m.msg_id == ev.msg_id) or (ev.user_id and m.user_id == ev.user_id):
                m.deleted = True
                changed = True
        self.overlay.publish(deletion_payload(ev))
        if changed:
            self.render_all()

    def clear_chat(self) -> None:
        self.messages.clear()
        self.view.clear()

    # ---- Moderation ------------------------------------------------------ #
    def on_anchor(self, url: QUrl) -> None:
        if url.scheme() == "msg":
            try:
                self.message_menu(int(url.path()))
            except ValueError:
                pass

    def message_menu(self, uid: int) -> None:
        msg = self.messages.get(uid)
        if not msg:
            return
        acc = self.accounts[msg.platform]
        menu = QMenu(self)
        menu.addSection(f"{msg.user} · {NAMES[msg.platform]}")
        menu.addAction(_("Mention (@)")).triggered.connect(lambda: self.mention(msg))
        menu.addAction(_("Copy text")).triggered.connect(lambda: QApplication.clipboard().setText(msg.text))
        menu.addSeparator()
        if not acc.logged_in:
            menu.addAction(_("Log in to {platform} to moderate …").format(platform=NAMES[msg.platform])).triggered.connect(
                lambda: self.login(msg.platform)
            )
        else:
            a = menu.addAction(_("Delete message"))
            a.setEnabled(bool(msg.msg_id) and not msg.deleted)
            a.triggered.connect(lambda: self.moderate(msg, "delete"))
            for secs, label in TIMEOUTS:
                a = menu.addAction(_(label))
                a.setEnabled(bool(msg.user_id))
                a.triggered.connect(lambda _checked=False, s=secs: self.moderate(msg, "timeout", s))
            a = menu.addAction(_("Ban …"))
            a.setEnabled(bool(msg.user_id))
            a.triggered.connect(lambda: self.moderate(msg, "ban"))
        menu.exec(QCursor.pos())

    def moderate(self, msg: ChatMessage, action: str, seconds: int = 0) -> None:
        acc = self.accounts[msg.platform]
        if action == "delete":
            coro = acc.delete(msg)
            ev = Deletion(msg.platform, msg_id=msg.msg_id)
            label = _("Message deleted")
        elif action == "timeout":
            coro = acc.timeout(msg, seconds)
            ev = Deletion(msg.platform, user_id=msg.user_id)
            label = _("Timeout for {user} ({minutes} min)").format(user=msg.user, minutes=seconds // 60)
        else:
            answer = QMessageBox.question(
                self,
                _("Ban"),
                _("Permanently ban {user} on {platform}?").format(user=msg.user, platform=NAMES[msg.platform]),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            coro = acc.ban(msg)
            ev = Deletion(msg.platform, user_id=msg.user_id)
            label = _("{user} banned").format(user=msg.user)
        self.run(coro, then=lambda _result: self.apply_deletion(ev), label=label)

    def mention(self, msg: ChatMessage) -> None:
        if self.target.isEnabled():
            idx = self.target.findData(msg.platform)
            if idx >= 0:
                self.target.setCurrentIndex(idx)
        self.input.setText(f"@{msg.user} {self.input.text()}")
        self.input.setFocus()

    # ---- Sending --------------------------------------------------------- #
    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        target = self.target.currentData()
        plats = [target] if target else [p for p in PLATFORMS if self.accounts[p].logged_in]
        sent = False
        for p in plats:
            channel = self.inputs[p].text().strip()
            if not channel:
                self.statusBar().showMessage(_("No {platform} channel entered").format(platform=NAMES[p]), 6000)
                continue
            self.run(self.accounts[p].send(channel, text), label=_("Sent to {platform}").format(platform=NAMES[p]))
            sent = True
        if sent:
            self.input.clear()

    # ---- Accounts -------------------------------------------------------- #
    def refresh_accounts(self) -> None:
        current = self.target.currentData()
        self.target.clear()
        logged = [p for p in PLATFORMS if self.accounts[p].logged_in]
        if len(logged) > 1:
            self.target.addItem(_("All"), None)
        for p in logged:
            self.target.addItem(NAMES[p], p)
        idx = self.target.findData(current)
        if idx >= 0:
            self.target.setCurrentIndex(idx)
        enabled = bool(logged)
        for w in (self.target, self.input, self.send_btn):
            w.setEnabled(enabled)
        self.input.setPlaceholderText(
            _("Send a message …") if enabled else _("Log in under “Accounts” to send and moderate")
        )

    def login(self, platform: str) -> None:
        cid, secret = self.app_creds(platform)
        if not cid or not secret:
            QMessageBox.information(
                self,
                _("Developer app missing"),
                _("To log in to {platform} you first need the client ID and secret of your own developer app.").format(
                    platform=NAMES[platform]
                ),
            )
            self.edit_apps()
            if not all(self.app_creds(platform)):
                return
        self.statusBar().showMessage(
            _("Browser opened – please log in to {platform} …").format(platform=NAMES[platform]), 30000
        )
        self.run(
            self.accounts[platform].login(),
            then=lambda who: self.refresh_accounts(),
            label=_("Logged in to {platform}").format(platform=NAMES[platform]),
        )

    def logout(self, platform: str) -> None:
        self.accounts[platform].logout()
        self.refresh_accounts()
        self.statusBar().showMessage(_("Logged out of {platform}").format(platform=NAMES[platform]), 5000)

    def edit_apps(self) -> None:
        dlg = AppsDialog(self, self.config["apps"])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.config["apps"] = dlg.values()
            self.save_config()

    # ---- Overlay --------------------------------------------------------- #
    def overlay_url(self) -> str:
        return f"http://127.0.0.1:{self.config['overlay'].get('port', OVERLAY_PORT)}/?fade=0&size=20"

    def toggle_overlay(self, on: bool) -> None:
        self.config["overlay"]["enabled"] = on
        if on:
            port = int(self.config["overlay"].get("port", OVERLAY_PORT))
            self.run(
                self.overlay.start(port),
                then=lambda _result: self.statusBar().showMessage(
                    _("Overlay running: {url} – add it in OBS as a browser source").format(url=self.overlay_url()),
                    15000,
                ),
                on_error=lambda: self.overlay_action.setChecked(False),
            )
        else:
            self.run(self.overlay.stop())

    def copy_overlay_url(self) -> None:
        QApplication.clipboard().setText(self.overlay_url())
        hint = "" if self.overlay.running else " " + _("(overlay is still off – enable it under “Overlay”)")
        self.statusBar().showMessage(_("Copied: {url}").format(url=self.overlay_url()) + hint, 8000)

    # ---- Shutdown -------------------------------------------------------- #
    def closeEvent(self, event) -> None:  # noqa: N802
        self.save_config()
        for t in self.readers:
            t.cancel()
        self.video.shutdown()

        async def shutdown() -> None:
            await self.overlay.stop()
            await self.session.close()

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(3)
        except Exception:  # noqa: BLE001
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        super().closeEvent(event)


def load_config() -> dict:
    cfg = {
        "channels": {p: "" for p in PLATFORMS},
        "show": {p: True for p in PLATFORMS},
        "on_top": False,
        "extra_emotes": True,
        "debug_log": False,
        "language": "auto",
        "video": {"enabled": True, "quality": {}, "audio": None, "sizes": [440, 860]},
        "overlay": {"enabled": False, "port": OVERLAY_PORT},
        "apps": {},
    }
    try:
        data = json.loads(CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return cfg
    for p in PLATFORMS:  # migrate the format of the first version
        if isinstance(data.get(p), str):
            cfg["channels"][p] = data[p]
    for key in ("channels", "show", "overlay", "apps", "video"):
        cfg[key].update(data.get(key) or {})
    cfg["on_top"] = bool(data.get("on_top", False))
    cfg["extra_emotes"] = bool(data.get("extra_emotes", True))
    cfg["debug_log"] = bool(data.get("debug_log", False))
    cfg["language"] = str(data.get("language") or "auto")
    return cfg


def restart_app(win: QMainWindow) -> None:
    """Start a new instance (AppImage, frozen binary or source) and close this one."""
    if os.environ.get("APPIMAGE"):
        prog, args = os.environ["APPIMAGE"], []
    elif getattr(sys, "frozen", False):
        prog, args = sys.executable, []
    else:
        prog, args = sys.executable, [str(Path(sys.argv[0]).resolve())]
    log.info("Restarting: %s %s", prog, " ".join(args))
    win.close()
    QProcess.startDetached(prog, args)
    QApplication.quit()


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
    from video import MPV_ERROR, load_mpv

    print("OK      libmpv" if load_mpv() else f"MISSING libmpv: {MPV_ERROR}")
    missing = [
        code for code, _name in i18n.LANGUAGES
        if code != "en" and not (i18n.resource_dir() / "locales" / f"{code}.json").is_file()
    ]
    ok = ok and not missing
    print(f"MISSING locales: {', '.join(missing)}" if missing else f"OK      locales ({len(i18n.LANGUAGES)} languages)")
    print("OK      icon" if app_icon_path().is_file() else "MISSING multichat.png")
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
    win = MainWindow(config)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
