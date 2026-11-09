# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Main window part: connecting and disconnecting the chats (one reader per channel,
keyed "platform:channel"). Mixed into MainWindow."""
from __future__ import annotations

import asyncio
import logging

from ..chat import run_kick, run_twitch, run_youtube
from ..chat.models import source_key
from ..core.i18n import _
from .groups import GroupPanel

log = logging.getLogger("multichat")


class ConnectionsMixin:
    """Connection part of MainWindow (expects readers, conn_gen, loop, bridge, panels … from it)."""

    def _stop_reader(self, key: str) -> None:
        self.conn_gen[key] = self.conn_gen.get(key, 0) + 1  # ignore status messages of the old connection
        fut = self.readers.pop(key, None)
        if fut:
            fut.cancel()

    def _emit(self, ev, key: str) -> None:
        if self._closing:
            return
        ev.source = key
        wakeup = self._incoming.put(ev)
        if wakeup:
            self.bridge.incoming.emit(wakeup == 2)

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
        if self._closing:
            return
        if gen == self.conn_gen.get(key):  # drop messages from a connection that was already closed
            self.set_status(key, text)

    def set_status(self, key: str, text: str) -> None:
        for panel in self.panels:
            for row in panel.rows_for(key):
                row.status.set_full(text)
