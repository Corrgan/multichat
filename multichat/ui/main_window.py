# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window: channel inputs, chat, menus, video column."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path

import aiohttp

from PyQt6.QtCore import QObject, QProcess, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QActionGroup, QCursor, QDesktopServices, QImageReader
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from ..accounts import ApiError, KickAccount, SecretStore, TwitchAccount, YouTubeAccount
from ..accounts.store import choose_backend
from ..chat import ChatMessage, Deletion, EmoteStore, run_kick, run_twitch, run_youtube
from ..core import i18n
from ..core.config import CONF_DIR, CONFIG, OVERLAY_PORT, load_config, load_config_apps, write_private
from ..core.constants import BADGES, LOGIN_DOCS, NAMES, PLATFORMS
from ..core.i18n import _
from ..core.logsetup import LOG_DIR, LOG_FILE, asyncio_exception_handler, set_debug, tail
from ..overlay.server import OverlayServer, deletion_payload, message_payload
from ..video.tiles import VideoPanel
from .chat_view import ChatView
from .dialogs import AboutDialog, AppsDialog
from .widgets import CopyLabel

log = logging.getLogger("multichat")


TIMEOUTS = [(60, "Timeout 1 min"), (600, "Timeout 10 min"), (3600, "Timeout 1 h")]
MAX_MESSAGES = 1000


class Bridge(QObject):
    """Safely moves events from the asyncio thread into the Qt thread."""

    message = pyqtSignal(object)
    deletion = pyqtSignal(object)
    status = pyqtSignal(str, str, int)  # platform, text, connection generation
    result = pyqtSignal(object, object)


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

        backend, files = choose_backend(CONF_DIR)
        store = SecretStore("tokens", backend, files)
        self.apps = SecretStore("apps", backend, files)  # client IDs and secrets of the developer apps
        if self.config.pop("apps", None):  # older versions kept them in config.json
            legacy = load_config_apps()
            if legacy and not self.apps.all():
                self.apps.replace_all(legacy)
            write_private(CONFIG, self.config)
        log.info("Secrets: stored in %s", backend.name)
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
        app = self.apps.get(platform)
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
        name = getattr(coro, "__qualname__", "action")  # English name for the log (the label is translated)
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(lambda f: self.bridge.result.emit((then, label, on_error, name), f))
        return fut

    def _on_result(self, meta, fut) -> None:
        then, label, on_error, name = meta
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc:
            log.error("%s failed: %s", name, exc, exc_info=exc)
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

    def msg_html(self, m: ChatMessage) -> str:
        return self.view.message_html(m)

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
            self.run(self.send_to(p, channel, text), label=_("Sent to {platform}").format(platform=NAMES[p]))
            sent = True
        if sent:
            self.input.clear()

    async def send_to(self, platform: str, channel: str, text: str) -> None:
        try:
            await self.accounts[platform].send(channel, text)
        except ApiError as e:
            if e.status != 403:
                raise
            # the platforms only say "Forbidden" – explain the usual causes
            raise ApiError(
                _(
                    "{platform} rejected the message ({error}). The chat may be restricted (followers or "
                    "subscribers only, slow mode, verified accounts only) or the developer app lacks the "
                    "permission to send."
                ).format(platform=NAMES[platform], error=e),
                status=403,
            ) from e

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
        dlg = AppsDialog(self, self.apps.all())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.apps.replace_all(dlg.values())

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


def restart_app(win: QMainWindow) -> None:
    """Start a new instance (AppImage, frozen binary or source) and close this one."""
    if os.environ.get("APPIMAGE"):
        prog, args = os.environ["APPIMAGE"], []
    elif getattr(sys, "frozen", False):
        prog, args = sys.executable, []
    else:
        prog, args = sys.executable, ["-m", "multichat"]
    log.info("Restarting: %s %s", prog, " ".join(args))
    win.close()
    # from the directory that contains the package, so `-m multichat` also works from a source checkout
    QProcess.startDetached(prog, args, str(Path(__file__).resolve().parents[2]))
    QApplication.quit()
