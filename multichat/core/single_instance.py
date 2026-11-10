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

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

from PyQt6.QtCore import QLockFile, QObject, QStandardPaths, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("multichat")

_active: "SingleInstance | None" = None  # the one that holds the socket in this process
LOCK_WAIT_MS = 3000  # for a process that holds the lock and is still creating its socket


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


class ProcessLock:
    """A lock file that belongs to this process for as long as it lives.

    take() → True: ours. False: a living process holds it. None: locking is not possible.

    On Linux this is flock(): the kernel releases it when the process ends, however it
    ends, and it works between the Flatpak's sandboxes. Qt's QLockFile decides by the
    process ID written into the file whether its owner still lives – every Flatpak
    sandbox counts its processes from 1, so after a crash the next start found "its" ID
    alive (it was looking at itself) and could never take the lock again. Windows has
    neither flock nor that problem and uses QLockFile."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: int | None = None
        self._qt: QLockFile | None = None

    def take(self) -> bool | None:
        if self._fd is not None or self._qt is not None:
            return True
        if os.name == "nt" or fcntl is None:
            lock = QLockFile(self.path)
            lock.setStaleLockTime(0)  # an instance may run for days: never expire by age
            if lock.tryLock(0):
                self._qt = lock
                return True
            return False if lock.error() == QLockFile.LockError.LockFailedError else None
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except OSError:
            os.close(fd)
            return None
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # the file stays: removing it would race with the next start
            self._fd = None
        if self._qt is not None:
            self._qt.unlock()
            self._qt = None


class SingleInstance(QObject):
    """acquire() → True: this is the only instance (raise_requested fires when another
    start asks for the window). False: another instance runs and was asked to show itself."""

    raise_requested = pyqtSignal()

    def __init__(self, path: str | None = None) -> None:
        super().__init__()
        self.path = socket_path() if path is None else path
        self.server: QLocalServer | None = None
        self._lock: ProcessLock | None = None

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
            return True  # no safe place for the socket: start as if there were no check
        # The lock closes the gap between "nobody answers" and "my socket listens": two
        # starts at the same moment both found nobody, and the second removed the first
        # one's socket. Whoever holds the lock is the instance (or about to be).
        lock = ProcessLock(self.path + ".lock")
        deadline = time.monotonic() + LOCK_WAIT_MS / 1000
        state = lock.take()
        while state is False:  # a living process holds it
            if self._notify_existing(100):
                return False
            if time.monotonic() >= deadline:
                # Must not happen (the holder listens within milliseconds). The check is a
                # convenience – an app that does not start at all would be the worse failure.
                log.warning("Single instance: the holder of %s does not answer – starting without the check",
                            self.path)
                return True
            time.sleep(0.05)
            state = lock.take()
        if state is None:
            log.warning("Single instance: cannot lock %s.lock – two starts at the same moment may both open",
                        self.path)
        try:
            # also recognises an instance from before the lock existed
            if self._notify_existing():
                return False
            QLocalServer.removeServer(self.path)  # nobody answers: left behind by a crash
            server = QLocalServer(self)
            server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)  # this user only
            if not server.listen(self.path):
                log.warning("Single instance: cannot listen on %s (%s) – continuing without the check",
                            self.path, server.errorString())
                server.deleteLater()
                return True
            server.newConnection.connect(self._on_connection)
            self.server, self._lock = server, lock  # held until the socket is closed
            global _active
            _active = self
            return True
        finally:
            if self._lock is not lock:
                lock.release()

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
            self._lock.release()
            self._lock = None
