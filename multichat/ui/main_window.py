# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window: channel groups (chat + videos each), menus, send line."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
from collections import OrderedDict
from pathlib import Path

import aiohttp

from PyQt6.QtCore import QEvent, QObject, QProcess, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QDesktopServices, QImageReader, QKeySequence
from PyQt6.QtGui import QColor
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSplitter, QStyle, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from ..accounts import ApiError, KickAccount, SecretStore, TwitchAccount, YouTubeAccount
from ..accounts.store import choose_backend
from ..chat import ChatMessage, Deletion, EmoteStore, run_kick, run_twitch, run_youtube
from ..chat.models import source_key
from ..core import i18n
from ..core.config import CONF_DIR, MAX_GROUPS, OVERLAY_PORT, clean_groups, load_config, load_config_apps, save_config
from .. import __version__ as APP_VERSION
from ..core.constants import FAVICONS, LOGIN_DOCS, NAMES, PLATFORMS, REPO_SLUG, REPO_URL, version_tuple
from ..core.i18n import _
from ..core.logsetup import LOG_DIR, LOG_FILE, asyncio_exception_handler, set_debug, tail
from ..overlay.server import OverlayServer, deletion_payload, message_payload
from ..video.player import load_mpv
from ..video.tiles import AudioHub, VideoPanel
from .chat_view import DEFAULT_FONT, ChatView
from .groups import GroupPanel, normalize_channel
from .dialogs import AboutDialog, AlertsDialog, AppsDialog
from .moderation import ActionBar, ModActions, ModNotes
from .watchdog import Watchdog

log = logging.getLogger("multichat")


MAX_MESSAGES = 1000
FLUSH_MS = 100  # incoming messages are shown in batches – busy chats send hundreds per second
MAX_INPUT = 500  # longest message the platforms accept


class Bridge(QObject):
    """Safely moves events from the asyncio thread into the Qt thread."""

    message = pyqtSignal(object)
    deletion = pyqtSignal(object)
    status = pyqtSignal(str, str, int)  # source key ("twitch:channel"), text, connection generation
    result = pyqtSignal(object, object)
    login_url = pyqtSignal(str)  # login link to show when no browser could be opened
    update = pyqtSignal(str, str)  # newer version, release page


class MainWindow(QMainWindow):
    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self.setWindowTitle("MultiChat")
        self.resize(520, 780)
        self.config = config or load_config()
        if not self.config.get("groups"):
            self.config["groups"] = clean_groups(None, self.config.get("channels") or {})

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
            save_config(self.config)
        log.info("Secrets: stored in %s", backend.name)
        args = (store, self.app_creds, lambda: self.session)
        self.accounts = {"twitch": TwitchAccount(*args), "kick": KickAccount(*args), "youtube": YouTubeAccount(*args)}
        self.overlay = OverlayServer(self.loop)
        qt_formats = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
        # gif before png: 7TV only offers GIF (not PNG) for animated emotes when WebP is unavailable
        self.emotes = EmoteStore(lambda: self.session, [f for f in ("webp", "gif", "png") if f in qt_formats])
        self.readers: dict = {}  # source key ("twitch:channel") → running chat reader (concurrent future)
        self.conn_gen: dict[str, int] = {}  # increases with every (dis)connect of a channel
        self.panels: list[GroupPanel] = []
        self._route: dict[str, list[GroupPanel]] = {}  # source key → groups that show it
        self.messages: OrderedDict[int, ChatMessage] = OrderedDict()
        self._incoming: list[ChatMessage] = []
        self._flush_timer = QTimer(self, singleShot=True, interval=FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_messages)
        self._render_timer = QTimer(self, singleShot=True, interval=150)  # coalesces full re-renders
        self._render_timer.timeout.connect(self._render_now)

        self.mod = ModActions(self, ModNotes(CONF_DIR / "modnotes.json"))
        self._build_ui()
        self._build_menus()
        self.apply_config()
        self.refresh_accounts()
        self._update_conn_buttons()
        self.bridge.login_url.connect(self._show_login_url)
        self.bridge.update.connect(self._show_update)
        for acc in self.accounts.values():
            acc.show_url = self.bridge.login_url.emit
        if self.config.get("_warnings"):
            self.statusBar().showMessage(
                _("The settings file was damaged – started with defaults (the old file was kept as a backup)"), 15000
            )
        self._msg_count = 0
        self.watchdog = Watchdog(self, self._perf_stats)
        if self.config.get("check_updates", True) and not os.environ.get("MULTICHAT_NO_UPDATE_CHECK"):
            self.run(self._check_update())

    # ---- Configuration --------------------------------------------------- #
    def load_config(self) -> dict:
        return load_config()

    def save_config(self) -> None:
        self.config["groups"] = [panel.to_config() for panel in self.panels]
        self.config["active_group"] = self.panels.index(self.active_panel())
        self.config["side_by_side"] = self.side_action.isChecked()
        first = self.panels[0]  # older versions read this – keeps a downgrade working
        self.config["channels"] = {p: first.rows[p].edit.text().strip() for p in PLATFORMS}
        self.config["show"] = {p: c.isChecked() for p, c in self.filters.items()}
        self.config["on_top"] = self.top_action.isChecked()
        self.config["extra_emotes"] = self.emote_action.isChecked()
        self.config["debug_log"] = self.debug_action.isChecked()
        if self.video_ok:
            for panel in self.panels:
                self.config["video"].update(panel.video.state())
            self.config["video"]["audio"] = self.audio.audio
        self.config["video"]["enabled"] = self.video_action.isChecked()
        save_config(self.config)

    def apply_config(self) -> None:
        for p in PLATFORMS:
            self.filters[p].setChecked(self.config["show"].get(p, True))
        self.top_action.setChecked(self.config["on_top"])
        self.emote_action.setChecked(self.config["extra_emotes"])  # "on" loads the global sets
        self.emotes.enabled = self.config["extra_emotes"]
        self.debug_action.setChecked(self.config["debug_log"])
        for v in self.views():
            v.set_font_px(self.config["chat_font"])
            v.alerts_cfg = self.config["alerts"]
        video_on = bool(self.config["video"].get("enabled", True))
        self.video_action.setChecked(video_on)
        self.toggle_video(video_on, resize=False)
        self.resize(1300 if video_on else 520, 820 if video_on else 780)
        self.tabs.setCurrentIndex(min(self.config.get("active_group", 0), len(self.panels) - 1))
        self.side_action.setChecked(bool(self.config.get("side_by_side")) and len(self.panels) > 1)
        self._show_panels()
        if self.config["overlay"].get("enabled"):
            self.overlay_action.setChecked(True)

    def app_creds(self, platform: str) -> tuple[str, str]:
        app = self.apps.get(platform)
        return app.get("client_id", ""), app.get("client_secret", "")

    async def _make_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=20))

    # ---- Layout ---------------------------------------------------------- #
    def _build_ui(self) -> None:
        self.icon_play = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self.icon_stop = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop)

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

        # groups: all panels sit in one splitter for good – as tabs only the active one is
        # shown, side by side all of them (moving them between widgets would move their
        # OpenGL video tiles to another parent, which makes Qt recreate the GL contexts)
        self.video_ok = bool(load_mpv())
        self.audio = AudioHub(self.config["video"].get("audio"))
        self.audio.changed.connect(self._schedule_save)
        self.side_by_side = False
        self.tabs = QTabBar()
        self.tabs.setDocumentMode(True)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(lambda _i: self._tab_changed())
        add = QToolButton()
        add.setText("+")
        add.setAutoRaise(True)
        add.setToolTip(_("New channel group"))
        add.clicked.connect(lambda: self.add_group())
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.addWidget(add)  # left of the tabs – new groups appear on the right
        tab_row.addWidget(self.tabs)
        tab_row.addStretch(1)
        self.side = QSplitter(Qt.Orientation.Horizontal)
        self.side.setChildrenCollapsible(False)
        self._side_active: GroupPanel | None = None  # side by side: the group clicked last
        QApplication.instance().focusChanged.connect(self._focus_changed)
        for g in self.config["groups"]:
            self._add_panel(g)


        send = QHBoxLayout()
        self.target = QComboBox()
        self.target.setMinimumContentsLength(10)
        self.input = QLineEdit()
        self.input.setMaxLength(MAX_INPUT)
        self.input.returnPressed.connect(self.send)
        self.send_btn = QPushButton(_("Send"))
        self.send_btn.clicked.connect(self.send)
        send.addWidget(self.target)
        send.addWidget(self.input, 1)
        send.addWidget(self.send_btn)

        if self.video_ok:
            # An OpenGL widget present from the start: adding the first one to a window that
            # is already shown makes Qt rebuild the window's rendering – with mpv contexts
            # around, that crashed (seen with the AppImage when a second group was added).
            self._keeper = QOpenGLWidget()
            self._keeper.setFixedSize(1, 1)
            filters.addWidget(self._keeper)

        root = QVBoxLayout()
        root.setContentsMargins(9, 9, 9, 0)
        root.addLayout(filters)
        root.addLayout(tab_row)
        root.addWidget(self.side, 1)
        root.addLayout(send)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)
        self.statusBar().showMessage(_("Tip: click a name to select the user, right-click it for the menu."), 8000)

    # ---- Groups ------------------------------------------------------------ #
    def views(self) -> list[ChatView]:
        return [panel.view for panel in self.panels]

    @property
    def view(self) -> ChatView:
        """The chat view of the active group."""
        return self.active_panel().view

    def active_panel(self) -> GroupPanel:
        if self.side_by_side and self._side_active in self.panels:
            return self._side_active
        i = self.tabs.currentIndex()
        return self.panels[i] if 0 <= i < len(self.panels) else self.panels[0]

    def _visible_panels(self) -> list[GroupPanel]:
        return list(self.panels) if self.side_by_side else [self.active_panel()]

    def _add_panel(self, cfg: dict) -> GroupPanel:
        video = None
        if self.video_ok:
            video = VideoPanel(self.run, self.config["video"], self.audio)
            video.changed.connect(self._schedule_save)
        panel = GroupPanel(cfg, len(self.panels), video)
        panel.set_action_bar(ActionBar(self.mod, lambda m, pn=panel: pn in self.panels_for(m)))
        sizes = self.config["video"].get("sizes") or [440, 860]
        panel.split.setSizes([int(x) for x in sizes[:2]])
        panel.split.splitterMoved.connect(lambda _pos, _i, pn=panel: self._split_moved(pn))
        v = panel.view
        v.document().setMaximumBlockCount(MAX_MESSAGES)
        v.anchorClicked.connect(self.on_anchor)
        v.rerender_needed.connect(self.render_all)
        v.image_failed.connect(self._image_failed)
        v.name_context.connect(self.on_name_context)
        v.follow_changed.connect(lambda following, pn=panel: self._follow_changed(pn, following))
        v.held = False  # messages arrived while scrolled up
        if self.panels:  # same look as the others
            v.set_font_px(self.panels[0].view.font_px)
            v.alerts_cfg = self.panels[0].view.alerts_cfg
            v.selected = self.panels[0].view.selected
        panel.toggle_row.connect(self.toggle_row)
        panel.connect_group.connect(self.connect_group)
        panel.disconnect_group.connect(self.disconnect_group)
        panel.remove_group.connect(self.remove_group)
        panel.renamed.connect(lambda _p: self._update_titles())
        panel.changed.connect(self._schedule_save)
        panel.rows_changed.connect(lambda _p: self._rows_changed())
        if video is not None and hasattr(self, "video_action"):
            video.setVisible(self.video_action.isChecked())
        self.panels.append(panel)
        self.side.addWidget(panel)
        panel.setVisible(self.side_by_side)
        self.tabs.addTab(panel.title())  # may switch the tab → _tab_changed shows the right panel
        self._rows_changed()
        self._update_titles()
        return panel

    def _split_moved(self, panel: GroupPanel) -> None:
        """Chat/video width: the same for all groups (side by side each keeps its own until
        it is changed)."""
        sizes = panel.split.sizes()
        if len(sizes) == 2 and min(sizes) > 0:
            self.config["video"]["sizes"] = sizes
            self._schedule_save()

    def add_group(self) -> GroupPanel | None:
        if len(self.panels) >= MAX_GROUPS:
            self.statusBar().showMessage(_("At most {n} groups").format(n=MAX_GROUPS), 5000)
            return None
        panel = self._add_panel({"name": "", "channels": []})
        self._side_active = panel
        self.tabs.setCurrentIndex(len(self.panels) - 1)
        self.side_action.setEnabled(len(self.panels) > 1)
        if self.side_by_side:
            self._fit_width()
        panel.rows["twitch"].edit.setFocus()
        self.save_config()
        return panel

    def remove_group(self, panel: GroupPanel) -> None:
        if len(self.panels) <= 1:
            return
        if panel.keys() and QMessageBox.question(
            self, _("Remove group"), _("Remove the group “{name}” and its channel list?").format(name=panel.title())
        ) != QMessageBox.StandardButton.Yes:
            return
        self.disconnect_group(panel)
        i = self.panels.index(panel)
        self.panels.remove(panel)
        if panel.video is not None:
            panel.video.shutdown()
        self.tabs.blockSignals(True)
        self.tabs.removeTab(i)
        self.tabs.blockSignals(False)
        panel.hide()
        panel.deleteLater()  # the whole window goes on – the panel only leaves the splitter
        for j, pn in enumerate(self.panels):
            pn.set_index(j)
        if len(self.panels) < 2 and self.side_action.isChecked():
            self.side_action.setChecked(False)
        self.side_action.setEnabled(len(self.panels) > 1)
        self._rows_changed()
        self._show_panels()
        self.save_config()

    def toggle_side_by_side(self, on: bool) -> None:
        """Groups next to each other (chat 1 | videos 1 | chat 2 | videos 2 …), or as tabs."""
        active = self.active_panel() if self.panels else None
        self.side_by_side = bool(on and len(self.panels) > 1)
        self._side_active = active
        if not self.side_by_side and active in self.panels:
            self.tabs.setCurrentIndex(self.panels.index(active))
        self._show_panels()
        if self.side_by_side:
            self._fit_width()

    def _fit_width(self) -> None:
        per = 900 if self.video_ok and self.video_action.isChecked() else 420
        screen = self.screen().availableGeometry().width() if self.screen() else 1920
        want = min(screen, per * len(self.panels))
        if self.width() < want:
            self.resize(want, self.height())

    def _tab_changed(self) -> None:
        if not self.side_by_side:
            self._show_panels()

    def _show_panels(self) -> None:
        """Tabs: only the active group. Side by side: all groups."""
        if not self.panels:
            return
        self.tabs.setVisible(not self.side_by_side)
        active = self.active_panel()
        for panel in self.panels:
            panel.setVisible(self.side_by_side or panel is active)
        self._active_changed()

    def _focus_changed(self, _old, new) -> None:
        if not self.side_by_side or new is None:
            return
        for panel in self.panels:
            if panel.isAncestorOf(new) and panel is not self._side_active:
                self._side_active = panel
                self._active_changed()
                break

    def _active_changed(self) -> None:
        """Another group is active: its videos are shown, the send line targets it."""
        if not self.panels:
            return
        for panel in self._visible_panels():
            panel.unread = panel.mentions = 0
        self._update_titles()
        self._refresh_targets()
        self._update_video()
        for v in self.views():
            v.update_animations()  # hidden tabs pause their animations

    def _update_titles(self) -> None:
        for i, panel in enumerate(self.panels):
            text = panel.title()
            if panel.unread:
                text += f" ({'@' if panel.mentions else ''}{min(panel.unread, 999)})"
            if i < self.tabs.count():
                self.tabs.setTabText(i, text)
                self.tabs.setTabTextColor(i, QColor("#ff9a3c") if panel.mentions else QColor())
            panel.remove_btn.setVisible(len(self.panels) > 1)

    def _rows_changed(self) -> None:
        """A channel was edited: routing, titles, buttons and send targets follow."""
        self._route = {}
        for panel in self.panels:
            for key in panel.keys():
                self._route.setdefault(key, []).append(panel)
            if panel.video is not None:
                panel.video.forget(panel.keys())  # tiles of channels the group doesn't have any more
        self._update_titles()  # an unnamed group is named after its first channel
        for panel in self.panels:
            if panel.action_bar is not None:
                panel.action_bar.refresh()  # the selected user may belong to another group now
        self._update_conn_buttons()
        self._refresh_targets()
        self._update_video()  # e.g. a video box was ticked

    def _schedule_save(self) -> None:
        if not hasattr(self, "_save_timer"):
            self._save_timer = QTimer(self, singleShot=True, interval=800)
            self._save_timer.timeout.connect(self.save_config)
        self._save_timer.start()

    def panels_for(self, msg) -> list[GroupPanel]:
        """Groups that show this message (messages without a known source go to the first group)."""
        return self._route.get(msg.source) or [self.panels[0]]

    def _build_menus(self) -> None:
        mb = self.menuBar()
        view = mb.addMenu(_("View"))
        view.addAction(_("Clear chat")).triggered.connect(self.clear_chat)
        view.addAction(_("New channel group")).triggered.connect(lambda: self.add_group())
        self.side_action = view.addAction(_("Groups side by side"))
        self.side_action.setCheckable(True)
        self.side_action.setEnabled(len(self.config["groups"]) > 1)
        self.side_action.toggled.connect(self.toggle_side_by_side)
        view.addAction(_("Connect all groups")).triggered.connect(self.connect_all)
        view.addAction(_("Disconnect everything")).triggered.connect(self.disconnect_all)
        view.addSeparator()
        self.top_action = view.addAction(_("Always on top"))
        self.top_action.setCheckable(True)
        self.top_action.toggled.connect(self.toggle_on_top)
        self.emote_action = view.addAction(_("7TV / BTTV / FFZ emotes"))
        self.emote_action.setCheckable(True)
        self.emote_action.toggled.connect(self.toggle_emotes)
        self.video_action = view.addAction(_("Video tiles"))
        self.video_action.setCheckable(True)
        self.video_action.toggled.connect(self.toggle_video)
        view.addAction(_("Alerts …")).triggered.connect(self.edit_alerts)
        font = view.addMenu(_("Chat font size"))
        for label, keys, step in ((_("Larger"), ("Ctrl++", "Ctrl+="), 2), (_("Smaller"), ("Ctrl+-",), -2),
                                  (_("Default size"), ("Ctrl+0",), 0)):
            a = font.addAction(label)
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.triggered.connect(lambda _checked=False, s=step: self.change_font(s))

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
        self.update_action = hlp.addAction(_("Check for new versions at start"))
        self.update_action.setCheckable(True)
        self.update_action.setChecked(bool(self.config.get("check_updates", True)))
        self.update_action.toggled.connect(self._toggle_update_check)
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
        fut.add_done_callback(lambda f: self._deliver(then, label, on_error, name, f))
        return fut

    def _deliver(self, then, label, on_error, name, fut) -> None:
        try:
            self.bridge.result.emit((then, label, on_error, name), fut)
        except RuntimeError:  # window already closed – nobody left to tell
            pass

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
            self.refresh_accounts()  # e.g. logged out after a rejected refresh token
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

    def change_font(self, step: int) -> None:
        px = self.view.font_px + step if step else DEFAULT_FONT
        for v in self.views():
            v.set_font_px(px)
        self.config["chat_font"] = self.view.font_px
        self.statusBar().showMessage(_("Chat font size: {px} px").format(px=self.view.font_px), 3000)

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

    def toggle_video(self, on: bool, resize: bool = True) -> None:
        for panel in self.panels:
            if panel.video is None:
                continue
            if not on:
                panel.video.set_entries([], shown=False)
                panel.video.stop_all()  # saves bandwidth
            panel.video.setVisible(on)
        if on:
            if resize and self.width() < 900:
                self.resize(1300, max(self.height(), 820))
            self._update_video()  # already connected → start the streams right away

    def _video_entries(self, panel: GroupPanel) -> list[tuple[str, str, str]]:
        """The group's connected channels with the video box ticked."""
        entries, seen = [], set()
        for row in panel.all_rows():
            key = row.key()
            if key and key in self.readers and row.video.isChecked() and key not in seen:
                seen.add(key)
                entries.append((key, row.platform, row.value()))
        return entries

    def _update_video(self) -> None:
        """Each group shows its own videos while it is on screen; hidden groups' tiles keep
        running muted for a while (see VideoPanel.set_entries)."""
        if not hasattr(self, "video_action") or not self.video_action.isChecked():
            return
        visible = self._visible_panels()
        for panel in self.panels:
            if panel.video is not None:
                panel.video.set_entries(self._video_entries(panel), shown=panel in visible)
                panel.video.stop_hidden(set(self.readers))  # disconnected channels: stop right away

    normalize_channel = staticmethod(normalize_channel)

    def toggle_on_top(self, on: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()

    # ---- Connecting ------------------------------------------------------ #
    def _stop_reader(self, key: str) -> None:
        self.conn_gen[key] = self.conn_gen.get(key, 0) + 1  # ignore status messages of the old connection
        fut = self.readers.pop(key, None)
        if fut:
            fut.cancel()

    def _emit(self, ev, key: str) -> None:
        ev.source = key
        (self.bridge.message if isinstance(ev, ChatMessage) else self.bridge.deletion).emit(ev)

    def connect_key(self, platform: str, value: str) -> None:
        """(Re)connects one channel's chat."""
        key = source_key(platform, value)
        self._stop_reader(key)
        gen = self.conn_gen[key]

        def status(_platform: str, template: str, **kw) -> None:
            # readers pass an English template – logged in English, shown translated
            log.info("[%s] %s", key, template.format(**kw))
            self.bridge.status.emit(key, _(template).format(**kw), gen)

        runners = {"twitch": run_twitch, "kick": run_kick, "youtube": run_youtube}
        coro = runners[platform](value, lambda ev: self._emit(ev, key), status, self.emotes)
        self.readers[key] = asyncio.run_coroutine_threadsafe(coro, self.loop)

    def disconnect_key(self, key: str) -> None:
        if key not in self.readers:
            return
        self._stop_reader(key)
        self.set_status(key, _("disconnected"))
        log.info("%s: connection closed", key)

    def _connect_row(self, row) -> None:
        value = row.value()
        if value is None:
            row.status.set_full(_("invalid channel name"))
        elif not value:
            row.status.set_full("–")
        else:
            self.connect_key(row.platform, value)

    def toggle_row(self, row) -> None:
        key = row.key()
        if key and key in self.readers:
            self.disconnect_key(key)
        else:
            self.save_config()
            self._connect_row(row)
        self._after_connection_change()

    def connect_group(self, panel: GroupPanel) -> None:
        self.save_config()
        for row in panel.all_rows():
            self._connect_row(row)
        self._after_connection_change()

    def disconnect_group(self, panel: GroupPanel) -> None:
        """Stops the group's channels – except those another group shows as well."""
        others = {k for pn in self.panels if pn is not panel for k in pn.keys()}
        for key in panel.keys() - others:
            self.disconnect_key(key)
        self._after_connection_change()

    def connect_all(self) -> None:
        self.save_config()
        for panel in self.panels:
            for row in panel.all_rows():
                self._connect_row(row)
        self._after_connection_change()

    def disconnect_all(self) -> None:
        for key in list(self.readers):
            self._stop_reader(key)
            self.set_status(key, _("disconnected"))
        for panel in self.panels:
            if panel.video is not None:
                panel.video.stop_all()
        self._update_video()
        self._update_conn_buttons()
        log.info("All connections closed")
        self.statusBar().showMessage(_("Disconnected – chats and videos stopped"), 5000)

    def _after_connection_change(self) -> None:
        self._update_video()
        self._update_conn_buttons()
        self._refresh_targets()

    def _update_conn_buttons(self) -> None:
        for panel in self.panels:
            keys = panel.keys()
            on = bool(keys & self.readers.keys())
            panel.connect_btn.setText(_("Reconnect") if on else _("Connect"))
            panel.connect_btn.setEnabled(bool(keys))
            panel.disconnect_btn.setEnabled(on)
            for row in panel.all_rows():
                key = row.key()
                connected = bool(key) and key in self.readers
                row.button.setIcon(self.icon_stop if connected else self.icon_play)
                row.button.setEnabled(connected or bool(row.edit.text().strip()))

    def _reader_status(self, key: str, text: str, gen: int) -> None:
        if gen == self.conn_gen.get(key):  # drop messages from a connection that was already closed
            self.set_status(key, text)

    def set_status(self, key: str, text: str) -> None:
        for panel in self.panels:
            for row in panel.rows_for(key):
                row.status.set_full(text)

    def msg_html(self, m: ChatMessage) -> str:
        return self.view.message_html(m)

    def add_message(self, msg: ChatMessage) -> None:
        """Queues a message; _flush_messages shows the batch (one layout pass instead of one per message)."""
        self._incoming.append(msg)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_messages(self) -> None:
        batch, self._incoming = self._incoming[-MAX_MESSAGES:], []
        if not batch:
            return
        self._msg_count += len(batch)
        for msg in batch:
            self.messages[msg.uid] = msg
            self.overlay.publish(message_payload(msg, self.view.alerts_cfg))
            self.mod.observe(msg)
        while len(self.messages) > MAX_MESSAGES * len(self.panels):
            self.messages.popitem(last=False)
        visible = self._visible_panels()
        own = [f"@{a.user_name.lower()}" for a in self.accounts.values() if a.logged_in and a.user_name]
        for panel in self.panels:
            shown = [m for m in batch if self.filters[m.platform].isChecked() and panel in self.panels_for(m)]
            if not shown:
                continue
            if panel not in visible:  # counter on the tab
                panel.unread += len(shown)
                panel.mentions += sum(any(o in m.text.lower() for o in own) for m in shown) if own else 0
            v = panel.view
            if not v.follow:
                # scrolled up: the view stays as it is (appending would also drop lines at the top and
                # move the text you are reading); everything is shown when you return to the bottom
                v.note_unseen(len(shown))
                v.held = True
                continue
            if self._render_timer.isActive():
                continue  # a full re-render is due anyway
            v.append_many(v.items(shown))  # scrolls along
        if any(p not in visible for p in self.panels):
            self._update_titles()

    def render_all(self) -> None:
        """Requests a full re-render (filters, deletions, failed images); coalesced."""
        self._render_timer.start()

    def _follow_changed(self, panel: GroupPanel, following: bool) -> None:
        if following and panel.view.held:  # back at the bottom: show what arrived meanwhile
            panel.view.held = False
            self.render_all()

    def _render_now(self) -> None:
        self._flush_messages()
        for panel in self.panels:
            v = panel.view
            if v.follow:
                v.held = False
            v.rebuild(v.items(
                m for m in self.messages.values() if self.filters[m.platform].isChecked() and panel in self.panels_for(m)
            ))

    def apply_deletion(self, ev: Deletion) -> None:
        self._flush_messages()  # the message to delete may still be queued
        changed = []
        for m in self.messages.values():
            if m.platform != ev.platform or m.deleted:
                continue
            if ev.source and m.source and m.source != ev.source:
                continue  # a ban in one channel doesn't touch another channel of the platform
            if ev.clear_all or (ev.msg_id and m.msg_id == ev.msg_id) or (ev.user_id and m.user_id == ev.user_id):
                m.deleted = True
                changed.append(m)
        self.overlay.publish(deletion_payload(ev))
        self.mod.apply_deletion(ev)
        if changed:
            self.update_lines(changed)
            self.mod.history_changed.emit()

    def _perf_stats(self) -> dict:
        """For the verbose log's minute summary (see watchdog.py)."""
        n, self._msg_count = self._msg_count, 0
        movies = [m for v in self.views() for m, _b in v.movies.values()]
        stats = {
            "messages/s": round(n / 60, 1),
            "chat lines": sum(v.document().blockCount() for v in self.views()),
            "animations playing": sum(m.state() == m.MovieState.Running for m in movies),
            "of": len(movies),
        }
        for i, panel in enumerate(self.panels):
            for key, (frames, ms) in (panel.video.frame_counts() if panel.video is not None else {}).items():
                name = key if i == 0 else f"{key} (group {i + 1})"
                stats[f"{name} fps"] = round(frames / 60, 1)
                stats[f"{name} ms/frame"] = round(ms / frames, 1)
        return stats

    def _image_failed(self, key: str) -> None:
        if any(key in urls for urls in FAVICONS.values()):  # platform icon: every line shows it
            self.render_all()
        else:
            self.update_lines([m for m in self.messages.values() if any(p[1] == key for p in m.parts)])

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            for v in self.views():
                v.update_animations()  # minimised: animations pause

    def update_lines(self, msgs) -> None:
        """Re-renders just these messages' lines (busy chats delete and time out all the time –
        rebuilding the whole view each time made the window stutter)."""
        if self._render_timer.isActive():
            return  # a full re-render is due anyway
        for panel in self.panels:
            shown = [m for m in msgs if self.filters[m.platform].isChecked() and panel in self.panels_for(m)]
            if len(shown) > 200:  # e.g. "clear chat": one rebuild is cheaper
                self.render_all()
                return
            if shown:
                panel.view.update_messages(panel.view.items(shown))

    def set_selected_user(self, key) -> None:
        """Highlight of the selected user's name in every group."""
        for v in self.views():
            v.selected = key

    def clear_chat(self) -> None:
        self._incoming.clear()
        self.messages.clear()
        for v in self.views():
            v.rebuild([])
            v.scroll_to_bottom()

    # ---- Moderation (logic in moderation/actions.py) ---------------------- #
    def on_anchor(self, url: QUrl) -> None:
        """Left click on a name: select the user (the action bar and user card follow)."""
        msg = self.mod.anchor_url(url)
        if msg:
            self.mod.select(msg)

    def on_name_context(self, url: QUrl) -> None:
        """Right click on a name: select the user and open the menu."""
        msg = self.mod.anchor_url(url)
        if msg:
            self.mod.select(msg)
            self.mod.menu(msg)

    def mention(self, msg: ChatMessage) -> None:
        if self.target.isEnabled():
            idx = self.target.findData(msg.source)
            if idx >= 0:
                self.target.setCurrentIndex(idx)
        self.input.setText(f"@{msg.user} {self.input.text()}")
        self.input.setFocus()

    # ---- Sending --------------------------------------------------------- #
    def _send_options(self) -> list[tuple[str, str, str, str]]:
        """(key, platform, channel, label) of the active group's channels you are logged in for."""
        return [(r.key(), r.platform, r.value(), NAMES[r.platform]) for r in self.active_panel().all_rows()
                if r.key() and self.accounts[r.platform].logged_in]

    def _refresh_targets(self) -> None:
        if not hasattr(self, "target"):
            return
        current = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        options = self._send_options()
        if len(options) > 1:
            self.target.addItem(_("All in this group"), None)
        for key, _p, _c, label in options:
            self.target.addItem(label, key)
        idx = self.target.findData(current)
        if idx >= 0:
            self.target.setCurrentIndex(idx)
        self.target.blockSignals(False)
        logged = any(self.accounts[p].logged_in for p in PLATFORMS)
        enabled = bool(options)
        for w in (self.target, self.input, self.send_btn):
            w.setEnabled(enabled)
        if enabled:
            self.input.setPlaceholderText(_("Send a message …"))
        elif logged:
            self.input.setPlaceholderText(_("Enter a channel of a platform you are logged in to"))
        else:
            self.input.setPlaceholderText(_("Log in under “Accounts” to send and moderate"))

    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        target = self.target.currentData()
        options = [o for o in self._send_options() if target in (None, o[0])]
        for _key, platform, channel, label in options:
            self.run(self.send_to(platform, channel, text), label=_("Sent to {platform}").format(platform=label))
        if options:
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
        self._refresh_targets()

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
            then=lambda who: (self.refresh_accounts(), self.mod.account_changed(platform)),
            label=_("Logged in to {platform}").format(platform=NAMES[platform]),
        )

    def _show_login_url(self, url: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(_("Log in"))
        box.setText(_("No browser could be opened. Open this link in your browser to log in:"))
        box.setInformativeText(url)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy = box.addButton(_("Copy link"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(url))
        box.open()

    # ---- Updates --------------------------------------------------------- #
    async def _check_update(self) -> None:
        """Asks GitHub once for the latest release (no data about you is sent besides the request)."""
        url = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"MultiChat/{APP_VERSION}"}
        try:
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    log.info("Update check: HTTP %s", r.status)
                    return
                data = await r.json()
        except Exception as e:  # noqa: BLE001 – offline etc.: not worth bothering anyone
            log.info("Update check failed: %s", e)
            return
        latest = str(data.get("tag_name") or "").removeprefix("v")
        if not re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,4}){1,3}", latest):
            log.info("Update check: ignoring unexpected tag %r", latest[:40])
            return
        if version_tuple(latest) > version_tuple(APP_VERSION):
            log.info("Update check: %s available (running %s)", latest, APP_VERSION)
            # the link is built here – a response can never lead to another site
            self.bridge.update.emit(latest, f"{REPO_URL}/releases/tag/v{latest}")
        else:
            log.info("Update check: %s is current", APP_VERSION)

    def _show_update(self, version: str, url: str) -> None:
        text = _("New version {version} available – open download page").format(version=version)
        self.statusBar().showMessage(text, 30000)
        act = QAction(text, self)
        act.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        self.menuBar().addAction(act)  # visible right in the menu bar

    def _toggle_update_check(self, on: bool) -> None:
        self.config["check_updates"] = on
        self.save_config()

    def logout(self, platform: str) -> None:
        self.accounts[platform].logout()
        self.refresh_accounts()
        self.mod.account_changed(platform)
        self.statusBar().showMessage(_("Logged out of {platform}").format(platform=NAMES[platform]), 5000)

    def edit_alerts(self) -> None:
        dlg = AlertsDialog(self, self.config["alerts"])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.config["alerts"] = dlg.values()
            for v in self.views():
                v.alerts_cfg = self.config["alerts"]
            self.save_config()
            self.render_all()

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
        self.mod.notes.save()
        for t in self.readers.values():
            t.cancel()
        for panel in self.panels:
            if panel.video is not None:
                panel.video.shutdown()

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
