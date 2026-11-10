# SPDX-License-Identifier: GPL-3.0-or-later
"""A second start shows the running window instead of opening another one."""
import os
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QLockFile, QStandardPaths
from PyQt6.QtWidgets import QApplication

from multichat.core import single_instance
from multichat.core.single_instance import SingleInstance


def _app():
    return QApplication.instance() or QApplication([])


def test_second_instance_asks_the_first_to_show_itself(tmp_path):
    app = _app()
    path = str(tmp_path / "instance.sock")
    first = SingleInstance(path)
    asked = []
    first.raise_requested.connect(lambda: asked.append(1))
    assert first.acquire() is True
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o077 == 0  # Windows uses a user-restricted named pipe

    second = SingleInstance(path)
    assert second.acquire() is False  # another one runs
    for _n in range(50):
        app.processEvents()
        if asked:
            break
    assert asked == [1]

    first.release()
    third = SingleInstance(path)
    assert third.acquire() is True  # free again after the first one closed
    third.release()


def test_closing_window_frees_the_socket_at_once(tmp_path):
    """Shutdown takes a moment; a start in that time (or the app's own restart) must not
    be sent to the window that is going away."""
    _app()
    path = str(tmp_path / "instance.sock")
    first = SingleInstance(path)
    assert first.acquire() is True
    single_instance.release_active()  # what MainWindow.closeEvent calls
    assert not os.path.exists(path)
    second = SingleInstance(path)
    assert second.acquire() is True
    second.release()
    single_instance.release_active()  # nothing held: harmless


def test_stale_socket_of_a_crashed_instance_does_not_block(tmp_path):
    _app()
    path = tmp_path / "instance.sock"
    path.write_text("")  # left behind, nobody listens
    inst = SingleInstance(str(path))
    assert inst.acquire() is True
    inst.release()


@pytest.mark.skipif(os.name == "nt", reason="XDG runtime paths are specific to POSIX")
def test_socket_lives_in_the_runtime_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.socket_path().startswith(str(tmp_path / "multichat"))
    monkeypatch.setenv("FLATPAK_ID", "io.github.Corrgan.MultiChat")  # sandboxes share this directory
    assert single_instance.socket_path().startswith(str(tmp_path / "app" / "io.github.Corrgan.MultiChat"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and modes do not apply to Windows named pipes")
def test_shared_temp_directory_of_someone_else_is_not_trusted(tmp_path, monkeypatch):
    """Without XDG_RUNTIME_DIR the socket lives under /tmp. A directory there that another
    user could write to (or made first) must not decide whether MultiChat starts."""
    _app()
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setattr(single_instance.tempfile, "gettempdir", lambda: str(tmp_path))
    path = single_instance.socket_path()
    assert path == str(tmp_path / f"multichat-{os.getuid()}" / "instance")  # our own, private: fine
    os.chmod(tmp_path / f"multichat-{os.getuid()}", 0o777)  # writable for everyone
    assert single_instance.socket_path() == ""
    inst = SingleInstance()
    assert inst.acquire() is True and inst.server is None  # starts, without the check
    monkeypatch.setattr(single_instance.os, "getuid", lambda: 4242)  # the directory is someone else's
    os.makedirs(tmp_path / "multichat-4242", mode=0o700)
    monkeypatch.setattr(single_instance.os, "getuid", lambda: 4243)
    monkeypatch.setattr(single_instance.tempfile, "gettempdir", lambda: str(tmp_path))
    (tmp_path / "multichat-4243").symlink_to(tmp_path / "multichat-4242")  # a planted link
    assert single_instance.socket_path() == ""


def test_windows_path_needs_no_posix_user_id(tmp_path, monkeypatch):
    """This namespace intentionally has no getuid; Windows startup must not ask for it."""
    monkeypatch.setattr(single_instance, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda _kind: str(tmp_path))
    assert single_instance.socket_path() == str(tmp_path / "multichat" / "instance")
    _app()
    instance = SingleInstance()
    try:
        assert instance.acquire() is True
        assert instance.server is not None
    finally:
        instance.release()


def test_busy_owner_without_a_server_does_not_allow_another_window(tmp_path, monkeypatch):
    """An owner can be paused between acquiring its lock and creating the socket."""
    _app()
    path = str(tmp_path / "instance.sock")
    owner = QLockFile(path + ".lock")
    owner.setStaleLockTime(0)
    assert owner.tryLock(0)
    monkeypatch.setattr(single_instance, "LOCK_WAIT_MS", 50)
    try:
        contender = SingleInstance(path)
        assert contender.acquire() is False
        assert contender.server is None
        contender.release()
        assert owner.isLocked()
    finally:
        owner.unlock()


_CONCURRENT_START = r'''
import json, sys, time
from pathlib import Path
from PyQt6.QtCore import QCoreApplication
from multichat.core import single_instance as si

directory, role = Path(sys.argv[1]), int(sys.argv[2])
app = QCoreApplication([])
app.setApplicationName('MultiChat')
remove, listen = si.QLocalServer.removeServer, si.QLocalServer.listen

def wait_for(name, seconds=5):
    end = time.monotonic() + seconds
    while not (directory / name).exists():
        if time.monotonic() >= end:
            raise RuntimeError('Timed out waiting for ' + name)
        time.sleep(.005)

def controlled_remove(path):
    if role == 0:
        (directory / 'first-probed').touch()
        wait_for('second-started')
        # The old code lets the second process pass its probe here. The fixed
        # code keeps it outside this critical section while the first starts.
        end = time.monotonic() + .5
        while not (directory / 'second-probed').exists() and time.monotonic() < end:
            time.sleep(.005)
    else:
        (directory / 'second-probed').touch()
        wait_for('first-listening')
    return remove(path)

def controlled_listen(server, path):
    result = listen(server, path)
    if role == 0 and result:
        (directory / 'first-listening').touch()
    return result

si.QLocalServer.removeServer = controlled_remove
si.QLocalServer.listen = controlled_listen
if role == 1:
    wait_for('first-probed')
    (directory / 'second-started').touch()
instance = si.SingleInstance(str(directory / 'instance'))
asked = []
def show_window():
    asked.append(True)
    (directory / 'raised').touch()
instance.raise_requested.connect(show_window)
acquired = instance.acquire()
result = directory / ('result-' + str(role))
temporary = result.with_suffix('.tmp')
temporary.write_text(json.dumps({'acquired': acquired}))
temporary.replace(result)
end = time.monotonic() + 10
while not (directory / 'finish').exists() and time.monotonic() < end:
    app.processEvents()
    time.sleep(.005)
(directory / ('asked-' + str(role))).write_text(json.dumps(asked))
instance.release()
'''


def test_concurrent_starts_keep_one_owner_and_show_its_window():
    """Real processes overlap their probe/remove/listen; the first must keep its socket."""
    # Keep Unix socket names below the kernel's length limit, independently of pytest's path.
    with tempfile.TemporaryDirectory(prefix="mc-race-") as temporary:
        directory = Path(temporary)
        children = [subprocess.Popen([sys.executable, "-c", _CONCURRENT_START, temporary, str(role)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for role in range(2)]
        try:
            end = time.monotonic() + 15
            while not all((directory / f"result-{role}").exists() for role in range(2)):
                assert time.monotonic() < end, "Concurrent startup did not finish"
                assert all(child.poll() is None for child in children), "Startup child exited early"
                time.sleep(.01)
            assert [json.loads((directory / f"result-{role}").read_text())["acquired"]
                    for role in range(2)] == [True, False]
            end = time.monotonic() + 5
            while not (directory / "raised").exists():
                assert time.monotonic() < end, "The owner was not asked to show its window"
                time.sleep(.01)
        finally:
            (directory / "finish").touch()
            failures = []
            for child in children:
                try:
                    _out, errors = child.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    _out, errors = child.communicate()
                if child.returncode:
                    failures.append(errors)
            assert not failures, "\n".join(failures)
        assert json.loads((directory / "asked-0").read_text()) == [True]
        assert not (directory / "second-probed").exists()


def test_crashed_process_lock_and_socket_are_recovered(tmp_path):
    _app()
    path = str(tmp_path / "instance")
    child = r'''
import os, sys
from PyQt6.QtCore import QCoreApplication
from multichat.core.single_instance import SingleInstance
app = QCoreApplication([])
app.setApplicationName('MultiChat')
instance = SingleInstance(sys.argv[1])
os._exit(0 if instance.acquire() else 2)
'''
    subprocess.run([sys.executable, "-c", child, path], check=True, timeout=10)
    replacement = SingleInstance(path)
    try:
        assert replacement.acquire() is True
        assert replacement.server is not None and replacement.server.isListening()
    finally:
        replacement.release()
