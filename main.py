#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""MultiChat – Twitch, Kick und YouTube Chat in einem Fenster.

Lesen geht ohne Login. Zum Schreiben und Moderieren meldest du dich unter
„Konten“ bei der jeweiligen Plattform an. Unter „Overlay“ startest du eine
Browserquelle für OBS.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import aiohttp
from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices, QImage, QImageReader, QTextDocument
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
    # Gebündelte App (AppImage/PyInstaller): Kindprozesse wie der Browser für den
    # Login sollen die Bibliotheken des Systems nutzen, nicht die gebündelten.
    _orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if _orig is not None:
        os.environ["LD_LIBRARY_PATH"] = _orig
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)

from accounts import REDIRECT_URI, KickAccount, TokenStore, TwitchAccount, YouTubeAccount
from clients import ChatMessage, Deletion, run_kick, run_twitch, run_youtube
from emotes import EmoteStore
from logsetup import (
    LOG_DIR,
    LOG_FILE,
    asyncio_exception_handler,
    install_qt_handler,
    log_system_info,
    set_debug,
    setup_logging,
    tail,
)
from overlay import OverlayServer, deletion_payload, message_payload
from video import CopyLabel, VideoPanel

log = logging.getLogger("multichat")

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
TIMEOUTS = [("Timeout 1 Min.", 60), ("Timeout 10 Min.", 600), ("Timeout 1 Std.", 3600)]
MAX_MESSAGES = 1000
EMOTE_H = 22
OVERLAY_PORT = 17564
IMAGE_RES = QTextDocument.ResourceType.ImageResource.value


def write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


class Bridge(QObject):
    """Bringt Ereignisse aus dem asyncio-Thread sicher in den Qt-Thread."""

    message = pyqtSignal(object)
    deletion = pyqtSignal(object)
    status = pyqtSignal(str, str, int)  # Plattform, Text, Verbindungs-Generation
    result = pyqtSignal(object, object)


class ChatView(QTextBrowser):
    """Chat-Ansicht, die Emote-Bilder asynchron nachlädt."""

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
                req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "Mozilla/5.0 MultiChat")
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
    """Client-ID und Secret der eigenen Entwickler-Apps eintragen."""

    def __init__(self, parent: QWidget, apps: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Entwickler-Apps")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Zum Anmelden brauchst du pro Plattform eine eigene, kostenlose Entwickler-App. "
            f"Trage dort als Redirect-URL genau <b>{REDIRECT_URI}</b> ein und kopiere "
            "Client-ID und Secret hierher. Details stehen in der README."
        )
        intro.setWordWrap(True)
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
            form.addRow("Konsole:", link)
            form.addRow("Client-ID:", cid)
            form.addRow("Client-Secret:", sec)
            self.fields[p] = (cid, sec)
            lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {p: {"client_id": c.text().strip(), "client_secret": s.text().strip()} for p, (c, s) in self.fields.items()}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MultiChat")
        self.resize(520, 780)
        self.config = self.load_config()

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
        self.conn_gen = 0  # erhöht sich bei jedem Verbinden/Trennen
        self.messages: OrderedDict[int, ChatMessage] = OrderedDict()

        self._build_ui()
        self._build_menus()
        self.apply_config()
        self.refresh_accounts()

    # ---- Konfiguration --------------------------------------------------- #
    def load_config(self) -> dict:
        cfg = {
            "channels": {p: "" for p in PLATFORMS},
            "show": {p: True for p in PLATFORMS},
            "on_top": False,
            "extra_emotes": True,
            "debug_log": False,
            "video": {"enabled": True, "quality": {}, "audio": None, "sizes": [440, 860]},
            "overlay": {"enabled": False, "port": OVERLAY_PORT},
            "apps": {},
        }
        try:
            data = json.loads(CONFIG.read_text())
        except Exception:  # noqa: BLE001
            return cfg
        for p in PLATFORMS:  # Format der ersten Version übernehmen
            if isinstance(data.get(p), str):
                cfg["channels"][p] = data[p]
        for key in ("channels", "show", "overlay", "apps", "video"):
            cfg[key].update(data.get(key) or {})
        cfg["on_top"] = bool(data.get("on_top", False))
        cfg["extra_emotes"] = bool(data.get("extra_emotes", True))
        cfg["debug_log"] = bool(data.get("debug_log", False))
        return cfg

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
        self.emote_action.setChecked(self.config["extra_emotes"])  # lädt bei „an“ die globalen Sets
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

    # ---- Aufbau ---------------------------------------------------------- #
    def _build_ui(self) -> None:
        self.inputs = {
            "twitch": QLineEdit(placeholderText="Kanalname"),
            "kick": QLineEdit(placeholderText="Kanalname"),
            "youtube": QLineEdit(placeholderText="@Kanal – oder Video-URL"),
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

        self.connect_btn = QPushButton("Verbinden")
        self.connect_btn.clicked.connect(self.connect_all)
        self.disconnect_btn = QPushButton("Trennen")
        self.disconnect_btn.setToolTip("Alle Chats und Videos stoppen")
        self.disconnect_btn.clicked.connect(self.disconnect_all)
        self.disconnect_btn.setEnabled(False)
        conn_row = QHBoxLayout()
        conn_row.addWidget(self.connect_btn, 3)
        conn_row.addWidget(self.disconnect_btn, 1)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Anzeigen:"))
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
        self.send_btn = QPushButton("Senden")
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

        # rechts: Video-Kacheln
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
        self.statusBar().showMessage("Tipp: Klick auf einen Namen öffnet das Moderationsmenü.", 8000)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        view = mb.addMenu("Ansicht")
        view.addAction("Chat leeren").triggered.connect(self.clear_chat)
        self.top_action = view.addAction("Immer im Vordergrund")
        self.top_action.setCheckable(True)
        self.top_action.toggled.connect(self.toggle_on_top)
        self.emote_action = view.addAction("7TV-/BTTV-/FFZ-Emotes")
        self.emote_action.setCheckable(True)
        self.emote_action.toggled.connect(self.toggle_emotes)
        self.video_action = view.addAction("Video-Kacheln")
        self.video_action.setCheckable(True)
        self.video_action.toggled.connect(self.toggle_video)

        self.accounts_menu = mb.addMenu("Konten")
        self.accounts_menu.aboutToShow.connect(self._fill_accounts_menu)
        self._fill_accounts_menu()

        ov = mb.addMenu("Overlay")
        self.overlay_action = ov.addAction("OBS-Overlay aktiv")
        self.overlay_action.setCheckable(True)
        self.overlay_action.toggled.connect(self.toggle_overlay)
        ov.addAction("Overlay-URL kopieren").triggered.connect(self.copy_overlay_url)

        hlp = mb.addMenu("Hilfe")
        hlp.addAction("Log kopieren (letzte 300 Zeilen)").triggered.connect(self.copy_log)
        hlp.addAction("Logdatei öffnen").triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_FILE))))
        hlp.addAction("Log-Ordner öffnen").triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR))))
        self.debug_action = hlp.addAction("Ausführliches Log (Debug)")
        self.debug_action.setCheckable(True)
        self.debug_action.toggled.connect(self.toggle_debug)

    def _fill_accounts_menu(self) -> None:
        m = self.accounts_menu
        m.clear()
        for p in PLATFORMS:
            acc = self.accounts[p]
            if acc.logged_in:
                a = m.addAction(f"{NAMES[p]}: {acc.user_name} – abmelden")
                a.triggered.connect(lambda _=False, p=p: self.logout(p))
            else:
                a = m.addAction(f"Bei {NAMES[p]} anmelden …")
                a.triggered.connect(lambda _=False, p=p: self.login(p))
        m.addSeparator()
        m.addAction("Entwickler-Apps einrichten …").triggered.connect(self.edit_apps)

    # ---- Hilfen ---------------------------------------------------------- #
    def run(self, coro, then=None, label: str | None = None, on_error=None):
        """Coroutine im asyncio-Thread starten; Ergebnis kommt im Qt-Thread an."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(lambda f: self.bridge.result.emit((then, label, on_error), f))
        return fut

    def _on_result(self, meta, fut) -> None:
        then, label, on_error = meta
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc:
            log.error("%s fehlgeschlagen: %s", label or "Aktion", exc, exc_info=exc)
            self.statusBar().showMessage(f"Fehler: {exc}", 12000)
            if on_error:
                on_error()
            return
        if label:
            self.statusBar().showMessage(f"{label} ✓", 6000)
        if then:
            then(fut.result())

    def copy_log(self) -> None:
        QApplication.clipboard().setText(tail(300))
        self.statusBar().showMessage(f"Log kopiert (Tokens/Secrets geschwärzt) – Datei: {LOG_FILE}", 10000)

    def toggle_debug(self, on: bool) -> None:
        set_debug(on)
        self.config["debug_log"] = on
        log.info("Ausführliches Log %s", "an" if on else "aus")

    def toggle_emotes(self, on: bool) -> None:
        self.emotes.enabled = on
        if on and not self.emotes.global_loaded:
            def loaded(counts: dict) -> None:
                info = ", ".join(f"{k.upper()} {v}" for k, v in counts.items()) or "keine erreichbar"
                self.statusBar().showMessage(f"Globale Emotes geladen: {info}", 6000)

            self.run(self.emotes.load_global(), then=loaded)

    def toggle_video(self, on: bool) -> None:
        if not hasattr(self, "video"):
            return
        if on:
            self.video.setVisible(True)
            if self.width() < 900:
                self.resize(1300, max(self.height(), 820))
            if self.readers:  # schon verbunden → Streams gleich starten
                self.video.set_sources(self._channels())
        else:
            self.config["video"]["sizes"] = self.splitter.sizes()
            self.video.stop_all()  # spart Bandbreite
            self.video.setVisible(False)

    def _channels(self) -> dict[str, str]:
        return {p: e.text().strip() for p, e in self.inputs.items()}

    def toggle_on_top(self, on: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()

    # ---- Verbinden ------------------------------------------------------- #
    def _stop_readers(self) -> None:
        self.conn_gen += 1  # Statusmeldungen der alten Verbindungen ab jetzt ignorieren
        for t in self.readers:
            t.cancel()
        self.readers.clear()

    def connect_all(self) -> None:
        self._stop_readers()
        self.save_config()
        gen = self.conn_gen

        def emit(ev) -> None:
            (self.bridge.message if isinstance(ev, ChatMessage) else self.bridge.deletion).emit(ev)

        def status(platform: str, text: str) -> None:
            log.info("[%s] %s", platform, text)
            self.bridge.status.emit(platform, text, gen)

        runners = {"twitch": run_twitch, "kick": run_kick, "youtube": run_youtube}
        for p, edit in self.inputs.items():
            value = edit.text().strip()
            if not value:
                self.set_status(p, "–")
                continue
            coro = runners[p](value, emit, status, self.emotes)
            self.readers.append(asyncio.run_coroutine_threadsafe(coro, self.loop))
        self.connect_btn.setText("Neu verbinden")
        self.disconnect_btn.setEnabled(bool(self.readers))
        if self.video_action.isChecked():
            self.video.set_sources(self._channels())

    def disconnect_all(self) -> None:
        self._stop_readers()
        self.video.stop_all()
        for p in PLATFORMS:
            self.set_status(p, "getrennt" if self.inputs[p].text().strip() else "–")
        self.connect_btn.setText("Verbinden")
        self.disconnect_btn.setEnabled(False)
        log.info("Alle Verbindungen getrennt")
        self.statusBar().showMessage("Getrennt – Chats und Videos gestoppt", 5000)

    def _reader_status(self, platform: str, text: str, gen: int) -> None:
        if gen == self.conn_gen:  # Meldungen aus einer schon getrennten Verbindung verwerfen
            self.set_status(platform, text)

    def set_status(self, platform: str, text: str) -> None:
        self.status_labels[platform].set_full(text)

    # ---- Anzeige --------------------------------------------------------- #
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
            body = f"<s style='color:#666'>{body}</s> <i style='color:#666'>(gelöscht)</i>"
        return (
            f"<span style='color:#888'>{ts}</span> "
            f"<span style='background:{color};color:#000;font-size:11px'>&nbsp;{tag}&nbsp;</span> "
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
        menu.addAction("Erwähnen (@)").triggered.connect(lambda: self.mention(msg))
        menu.addAction("Text kopieren").triggered.connect(lambda: QApplication.clipboard().setText(msg.text))
        menu.addSeparator()
        if not acc.logged_in:
            menu.addAction(f"Zum Moderieren bei {NAMES[msg.platform]} anmelden …").triggered.connect(
                lambda: self.login(msg.platform)
            )
        else:
            a = menu.addAction("Nachricht löschen")
            a.setEnabled(bool(msg.msg_id) and not msg.deleted)
            a.triggered.connect(lambda: self.moderate(msg, "delete"))
            for label, secs in TIMEOUTS:
                a = menu.addAction(label)
                a.setEnabled(bool(msg.user_id))
                a.triggered.connect(lambda _=False, s=secs: self.moderate(msg, "timeout", s))
            a = menu.addAction("Bannen …")
            a.setEnabled(bool(msg.user_id))
            a.triggered.connect(lambda: self.moderate(msg, "ban"))
        menu.exec(QCursor.pos())

    def moderate(self, msg: ChatMessage, action: str, seconds: int = 0) -> None:
        acc = self.accounts[msg.platform]
        if action == "delete":
            coro = acc.delete(msg)
            ev = Deletion(msg.platform, msg_id=msg.msg_id)
            label = "Nachricht gelöscht"
        elif action == "timeout":
            coro = acc.timeout(msg, seconds)
            ev = Deletion(msg.platform, user_id=msg.user_id)
            label = f"Timeout für {msg.user} ({seconds // 60} Min.)"
        else:
            answer = QMessageBox.question(self, "Bannen", f"{msg.user} auf {NAMES[msg.platform]} dauerhaft bannen?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            coro = acc.ban(msg)
            ev = Deletion(msg.platform, user_id=msg.user_id)
            label = f"{msg.user} gebannt"
        self.run(coro, then=lambda _: self.apply_deletion(ev), label=label)

    def mention(self, msg: ChatMessage) -> None:
        if self.target.isEnabled():
            idx = self.target.findData(msg.platform)
            if idx >= 0:
                self.target.setCurrentIndex(idx)
        self.input.setText(f"@{msg.user} {self.input.text()}")
        self.input.setFocus()

    # ---- Senden ---------------------------------------------------------- #
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
                self.statusBar().showMessage(f"Kein {NAMES[p]}-Kanal eingetragen", 6000)
                continue
            self.run(self.accounts[p].send(channel, text), label=f"An {NAMES[p]} gesendet")
            sent = True
        if sent:
            self.input.clear()

    # ---- Konten ---------------------------------------------------------- #
    def refresh_accounts(self) -> None:
        current = self.target.currentData()
        self.target.clear()
        logged = [p for p in PLATFORMS if self.accounts[p].logged_in]
        if len(logged) > 1:
            self.target.addItem("Alle", None)
        for p in logged:
            self.target.addItem(NAMES[p], p)
        idx = self.target.findData(current)
        if idx >= 0:
            self.target.setCurrentIndex(idx)
        enabled = bool(logged)
        for w in (self.target, self.input, self.send_btn):
            w.setEnabled(enabled)
        self.input.setPlaceholderText(
            "Nachricht senden …" if enabled else "Zum Schreiben und Moderieren unter „Konten“ anmelden"
        )

    def login(self, platform: str) -> None:
        cid, secret = self.app_creds(platform)
        if not cid or not secret:
            QMessageBox.information(
                self,
                "Entwickler-App fehlt",
                f"Für den {NAMES[platform]}-Login brauchst du zuerst Client-ID und Secret einer eigenen Entwickler-App.",
            )
            self.edit_apps()
            if not all(self.app_creds(platform)):
                return
        self.statusBar().showMessage(f"Browser geöffnet – bitte bei {NAMES[platform]} anmelden …", 30000)
        self.run(
            self.accounts[platform].login(),
            then=lambda who: self.refresh_accounts(),
            label=f"Bei {NAMES[platform]} angemeldet",
        )

    def logout(self, platform: str) -> None:
        self.accounts[platform].logout()
        self.refresh_accounts()
        self.statusBar().showMessage(f"Von {NAMES[platform]} abgemeldet", 5000)

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
                then=lambda _: self.statusBar().showMessage(
                    f"Overlay läuft: {self.overlay_url()} – in OBS als Browserquelle hinzufügen", 15000
                ),
                on_error=lambda: self.overlay_action.setChecked(False),
            )
        else:
            self.run(self.overlay.stop())

    def copy_overlay_url(self) -> None:
        QApplication.clipboard().setText(self.overlay_url())
        hint = "" if self.overlay.running else " (Overlay ist noch aus – unter „Overlay“ aktivieren)"
        self.statusBar().showMessage(f"Kopiert: {self.overlay_url()}{hint}", 8000)

    # ---- Beenden --------------------------------------------------------- #
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


def run_ytdlp(args: list[str]) -> None:
    """`multichat --yt-dlp …` verhält sich wie das yt-dlp-Programm (für mpvs ytdl_hook)."""
    import yt_dlp  # type: ignore

    if not any(a.startswith("--js-runtimes") for a in args):
        for rt in ("deno", "node", "bun", "quickjs"):
            args = ["--js-runtimes", rt, *args]
    sys.exit(yt_dlp.main(args))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--yt-dlp":
        run_ytdlp(sys.argv[2:])
    try:
        debug = bool(json.loads(CONFIG.read_text()).get("debug_log"))
    except Exception:  # noqa: BLE001
        debug = False
    setup_logging(debug)
    app = QApplication(sys.argv)
    app.setApplicationName("MultiChat")
    install_qt_handler()
    log_system_info()
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
