# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Second Fable review (20.11.2026): one regression test per verified finding."""
import asyncio
import os

import pytest


def test_s3_secret_files_written_like_the_settings(tmp_path, monkeypatch):
    from multichat.accounts import store
    backend = store.FileBackend(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text("{}")
    # a symlink left at the temporary name must not be written through
    os.symlink(target, tmp_path / ".tokens.json.tmp")
    with pytest.raises(OSError):
        backend.save("tokens", {"twitch": {"access_token": "x"}})
    assert target.read_text() == "{}"
    os.unlink(tmp_path / ".tokens.json.tmp")
    # a left-over temp file with a wider mode does not become the token file as it is
    leftover = tmp_path / ".tokens.json.tmp"
    leftover.write_text("")
    leftover.chmod(0o644)
    backend.save("tokens", {"twitch": {"access_token": "x"}})
    assert oct((tmp_path / "tokens.json").stat().st_mode & 0o777) == "0o600"
    assert backend.load("tokens") == {"twitch": {"access_token": "x"}}


def test_p4_overlay_stop_does_not_wait_for_a_frozen_client():
    from multichat.overlay import server

    class Frozen:
        async def close(self):
            await asyncio.sleep(10)

    async def run():
        ov = server.OverlayServer.__new__(server.OverlayServer)
        ov.clients = {Frozen(), Frozen()}
        ov.runner = None
        start = asyncio.get_running_loop().time()
        await ov.stop()
        return asyncio.get_running_loop().time() - start
    assert asyncio.run(run()) < 2
