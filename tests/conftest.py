# SPDX-License-Identifier: GPL-3.0-or-later
"""Test setup: isolated config/log directories and a headless Qt platform."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="multichat-tests-"))
for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = str(_tmp / var.lower())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MULTICHAT_SECRETS"] = "file"  # never touch the real keyring from tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
