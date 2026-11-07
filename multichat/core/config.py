# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Settings in ~/.config/multichat/config.json."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .constants import PLATFORMS

CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "multichat"
CONFIG = CONF_DIR / "config.json"
OVERLAY_PORT = 17564


def write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def load_config() -> dict:
    cfg = {
        "channels": {p: "" for p in PLATFORMS},
        "show": {p: True for p in PLATFORMS},
        "on_top": False,
        "extra_emotes": True,
        "debug_log": False,
        "language": "auto",
        "video": {"enabled": True, "quality": {}, "audio": None, "sizes": [440, 860]},
        "overlay": {"enabled": False, "port": OVERLAY_PORT},
    }
    try:
        data = json.loads(CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return cfg
    for p in PLATFORMS:  # migrate the format of the first version
        if isinstance(data.get(p), str):
            cfg["channels"][p] = data[p]
    if data.get("apps"):
        cfg["apps"] = True  # marker: older version kept the developer apps here (see load_config_apps)
    for key in ("channels", "show", "overlay", "video"):
        cfg[key].update(data.get(key) or {})
    cfg["on_top"] = bool(data.get("on_top", False))
    cfg["extra_emotes"] = bool(data.get("extra_emotes", True))
    cfg["debug_log"] = bool(data.get("debug_log", False))
    cfg["language"] = str(data.get("language") or "auto")
    return cfg


def save_config(cfg: dict) -> None:
    write_private(CONFIG, cfg)


def load_config_apps() -> dict:
    """Developer apps from config.json of older versions (now in the secret store)."""
    try:
        return dict(json.loads(CONFIG.read_text()).get("apps") or {})
    except Exception:  # noqa: BLE001
        return {}
