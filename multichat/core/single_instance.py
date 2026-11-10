# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""One running MultiChat per user.

Two instances would overwrite each other's settings (the last one to close wins), fight
over the overlay port and over the login redirect port. A second start therefore brings
the running window to the front and exits. MULTICHAT_ALLOW_MULTIPLE=1 turns this off.

The instances find each other through a local socket in the user's runtime directory
(for the Flatpak: the directory all instances of the app share).
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("multichat")

_active: "SingleInstance | None" = None  # the one that holds the socket in this process


def release_active() -> None:
    """Frees the socket as soon as the window starts closing: shutting down can take a few
    seconds, and a start during that time (also the app's own restart) must open a window
    instead of asking the closing one to show itself."""
    if _active is not None:
        _active.release()


def socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.environ.get("FLATPAK_ID"):
        base = Path(runtime) / "app" / os.environ["FLATPAK_ID"]  # shared by the app's sandboxes
    elif runtime:
        base = Path(runtime) / "multichat"
    else:
        base = Path(tempfile.gettempdir()) / f"multichat-{os.getuid()}"
    try:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        pass
    return str(base / "instance")  # short: socket paths are limited to about 100 characters


class SingleInstance(QObject):
    """acquire() → True: this is the only instance (raise_requested fires when another
    start asks for the window). False: another instance runs and was asked to show itself."""

    raise_requested = pyqtSignal()

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.path = path or socket_path()
        self.server: QLocalServer | None = None

    def acquire(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.path)
        if probe.waitForConnected(500):
            probe.write(b"show\n")
            probe.waitForBytesWritten(500)
            probe.disconnectFromServer()
            return False
        # nobody answers: a socket file left by a crash would block listen()
        QLocalServer.removeServer(self.path)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # this user only
        if not server.listen(self.path):
            log.warning("Single instance: cannot listen on %s (%s) – continuing without the check",
                        self.path, server.errorString())
            return True
        server.newConnection.connect(self._on_connection)
        self.server = server
        global _active
        _active = self
        return True

    def _on_connection(self) -> None:
        while self.server is not None and self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            # the request carries nothing to parse: any connection means "show yourself"
            self.raise_requested.emit()

    def release(self) -> None:
        global _active
        if _active is self:
            _active = None
        if self.server is not None:
            self.server.close()  # also removes the socket file
            self.server = None
