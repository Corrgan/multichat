# SPDX-License-Identifier: GPL-3.0-or-later
"""Secret storage: keyring, migration from the old files, fallback."""
import json

from multichat.accounts.store import FileBackend, KeyringBackend, SecretStore


class FakeKeyring:
    def __init__(self, fail_writes=False):
        self.items, self.fail_writes = {}, fail_writes

    def get_password(self, service, key):
        return self.items.get((service, key))

    def set_password(self, service, key, value):
        if self.fail_writes:
            raise RuntimeError("wallet closed")
        self.items[(service, key)] = value

    def delete_password(self, service, key):
        self.items.pop((service, key))


def test_file_backend_is_private(tmp_path):
    s = SecretStore("tokens", FileBackend(tmp_path))
    s.update("twitch", access_token="x")
    path = tmp_path / "tokens.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert json.loads(path.read_text()) == {"twitch": {"access_token": "x"}}
    assert SecretStore("tokens", FileBackend(tmp_path)).get("twitch") == {"access_token": "x"}


def test_migrates_file_into_keyring(tmp_path):
    files = FileBackend(tmp_path)
    files.save("apps", {"kick": {"client_id": "cid", "client_secret": "sec"}})
    kr = FakeKeyring()
    s = SecretStore("apps", KeyringBackend(kr), files)
    assert s.get("kick")["client_secret"] == "sec"
    assert not (tmp_path / "apps.json").exists()
    assert json.loads(kr.items[("multichat", "apps")])["kick"]["client_id"] == "cid"
    s.delete("kick")
    assert json.loads(kr.items[("multichat", "apps")]) == {}


def test_falls_back_to_file_when_keyring_fails(tmp_path):
    files = FileBackend(tmp_path)
    s = SecretStore("tokens", KeyringBackend(FakeKeyring(fail_writes=True)), files)
    s.update("youtube", access_token="y")  # must not lose the login
    assert json.loads((tmp_path / "tokens.json").read_text())["youtube"]["access_token"] == "y"
    assert s.backend_name == "file"


def test_private_file_stays_private_and_never_follows_a_symlink(tmp_path):
    """A left-over temp file with wide permissions, or a symlink at its name, must not end
    up as (or redirect) the settings/token file."""
    import os

    import pytest

    from multichat.core.config import write_private

    target = tmp_path / "tokens.json"
    stale = tmp_path / ".tokens.json.tmp"
    stale.write_text("old")
    stale.chmod(0o644)
    write_private(target, {"a": 1})
    assert target.stat().st_mode & 0o777 == 0o600 and json.loads(target.read_text()) == {"a": 1}

    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    os.symlink(victim, stale)
    with pytest.raises(OSError):
        write_private(target, {"a": 2})
    assert victim.read_text() == "untouched" and json.loads(target.read_text()) == {"a": 1}
