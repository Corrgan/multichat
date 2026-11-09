# SPDX-License-Identifier: GPL-3.0-or-later
"""Test setup: isolated config/log directories and a headless Qt platform."""
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

_tmp = Path(tempfile.mkdtemp(prefix="multichat-tests-"))
for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = str(_tmp / var.lower())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MULTICHAT_SECRETS"] = "file"  # never touch the real keyring from tests
os.environ["MULTICHAT_NO_UPDATE_CHECK"] = "1"  # no requests to GitHub from tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _session_qapplication():
    """Keep Qt's application/event dispatcher alive across GUI test modules.

    Module-local app fixtures reuse this instance. Without a session owner,
    their teardown can destroy QApplication while deferred network callbacks
    and QObject wrappers from an earlier module still exist.
    """
    from PyQt6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(autouse=True)
def _local_http_only(monkeypatch):
    """GUI background account queries must never use platform APIs in a test."""
    import aiohttp
    request = aiohttp.ClientSession._request
    async def local_request(self, method, url, *args, **kwargs):
        if urlsplit(str(url)).hostname not in ("127.0.0.1", "localhost", "::1"):
            raise aiohttp.ClientConnectionError("External HTTP is disabled in tests")
        return await request(self, method, url, *args, **kwargs)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", local_request)


@pytest.fixture(autouse=True)
def _fresh_shared_answers():
    """Kick/YouTube answers are shared for a few seconds – not between tests."""
    from multichat.chat import kick, youtube

    kick._channel_cache.clear()
    youtube.LIVE_CACHE.clear()
    yield
