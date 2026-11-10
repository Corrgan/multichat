# SPDX-License-Identifier: GPL-3.0-or-later
"""A second start shows the running window instead of opening another one."""
import os

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
    assert os.stat(path).st_mode & 0o077 == 0  # only this user can connect

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


def test_socket_lives_in_the_runtime_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert single_instance.socket_path().startswith(str(tmp_path / "multichat"))
    monkeypatch.setenv("FLATPAK_ID", "io.github.Corrgan.MultiChat")  # sandboxes share this directory
    assert single_instance.socket_path().startswith(str(tmp_path / "app" / "io.github.Corrgan.MultiChat"))


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
