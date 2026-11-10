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
import stat
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QLockFile, QObject, QStandardPaths, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("multichat")

_active: "SingleInstance | None" = None  # the one that holds the socket in this process
LOCK_WAIT_MS = 3000  # allow another process to finish creating its server


def release_active() -> None:
    """Frees the socket as soon as the window starts closing: shutting down can take a few
    seconds, and a start during that time (also the app's own restart) must open a window
    instead of asking the closing one to show itself."""
    if _active is not None:
        _active.release()


def socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if os.name == "nt":
        # Qt uses a named pipe on Windows. Keep its name and the acquisition lock in
        # the user's cache directory; POSIX user IDs and mode bits do not exist here.
        cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
        if not cache:
            return ""
        base = Path(cache) / "multichat"
    elif runtime and os.environ.get("FLATPAK_ID"):
        base = Path(runtime) / "app" / os.environ["FLATPAK_ID"]  # shared by the app's sandboxes
    elif runtime:
        base = Path(runtime) / "multichat"
    else:
        base = Path(tempfile.gettempdir()) / f"multichat-{os.getuid()}"
    try:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = base.lstat()
        # Without a runtime directory the socket lives under /tmp, where another user of
        # this machine could have created the directory first and answer in our place.
        if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and
                (info.st_uid != os.getuid() or info.st_mode & 0o022)):
            log.warning("Single instance: %s is not a private directory of this user – check skipped", base)
            return ""
    except OSError:
        return ""
    return str(base / "instance")  # short: socket paths are limited to about 100 characters


class SingleInstance(QObject):
    """acquire() → True: this is the only instance (raise_requested fires when another
    start asks for the window). False: another instance runs and was asked to show itself."""

    raise_requested = pyqtSignal()

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.path = socket_path() if path is None else path
        self.server: QLocalServer | None = None
        self._lock: QLockFile | None = None

    def _notify_existing(self, timeout_ms: int = 500) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.path)
        if probe.waitForConnected(timeout_ms):
            probe.write(b"show\n")
            probe.waitForBytesWritten(timeout_ms)
            probe.disconnectFromServer()
            return True
        return False

    def acquire(self) -> bool:
        if not self.path or self.server is not None:
            return True
        lock = QLockFile(self.path + ".lock")
        # A live instance may run for days: never expire its lock by age. Qt still
        # recovers a lock whose owning process died (including after a crash).
        lock.setStaleLockTime(0)
        deadline = time.monotonic() + LOCK_WAIT_MS / 1000
        while not lock.tryLock(0):
            if lock.error() != QLockFile.LockError.LockFailedError:
                log.warning("Single instance: cannot lock %s (%s); no second window opened",
                            self.path, lock.error())
                return False
            if self._notify_existing(100):
                return False
            if time.monotonic() >= deadline:
                log.warning("Single instance: another process holds %s but does not answer; no second window opened",
                            self.path)
                return False
            # A contender must not remove the socket while its owner is starting.
            if lock.tryLock(100):
                break
        try:
            # Also recognize an instance from before the acquisition lock existed.
            if self._notify_existing():
                return False
            QLocalServer.removeServer(self.path)  # only the lock owner may clean up a stale socket
            server = QLocalServer(self)
            server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # this user only
            if not server.listen(self.path):
                log.warning("Single instance: cannot listen on %s (%s); no second window opened",
                            self.path, server.errorString())
                server.deleteLater()
                return False
            server.newConnection.connect(self._on_connection)
            self.server, self._lock = server, lock  # hold ownership until the socket is closed
            global _active
            _active = self
            return True
        finally:
            if self._lock is not lock:
                lock.unlock()

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
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None
