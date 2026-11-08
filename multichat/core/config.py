# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Settings in ~/.config/multichat/config.json."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .constants import PLATFORMS

CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "multichat"
CONFIG = CONF_DIR / "config.json"
OVERLAY_PORT = 17564


def write_private(path: Path, data: dict) -> None:
    """Writes JSON readable only by the user – via a temporary file, so a crash or a full
    disk while saving never leaves a half-written file behind."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _backup_broken(reason: str) -> str:
    """Keeps an unreadable config.json as config.json.bad-<time> instead of overwriting it."""
    dest = CONFIG.with_name(f"config.json.bad-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        CONFIG.replace(dest)
    except OSError:
        return f"{CONFIG} is unreadable ({reason})"
    return f"{CONFIG} was unreadable ({reason}) – kept as {dest.name}, starting with defaults"


def load_config() -> dict:
    """Settings with defaults. Unknown or wrongly typed values are ignored, so a damaged or
    hand-edited file never stops the app; a file that isn't JSON at all is kept as a backup.
    Problems are reported in cfg["_warnings"] (logged once logging is set up)."""
    cfg = {
        "channels": {p: "" for p in PLATFORMS},
        "show": {p: True for p in PLATFORMS},
        "on_top": False,
        "extra_emotes": True,
        "debug_log": False,
        "check_updates": True,
        "language": "auto",
        "video": {"enabled": True, "quality": {}, "volume": {}, "audio": None, "sizes": [440, 860]},
        "overlay": {"enabled": False, "port": OVERLAY_PORT},
        "_warnings": [],
    }
    if not CONFIG.exists():
        return cfg
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        cfg["_warnings"].append(_backup_broken(str(e)))
        return cfg

    def pick(src, default):
        """src if it has the type of default (bool/int/str/list/dict), else default."""
        if isinstance(default, bool):
            return src if isinstance(src, bool) else default
        if isinstance(default, int):
            return src if isinstance(src, int) and not isinstance(src, bool) else default
        return src if isinstance(src, type(default)) else default

    for p in PLATFORMS:  # migrate the format of the first version
        if isinstance(data.get(p), str):
            cfg["channels"][p] = data[p]
    if data.get("apps"):
        cfg["apps"] = True  # marker: older version kept the developer apps here (see load_config_apps)
    for p in PLATFORMS:
        cfg["channels"][p] = pick((data.get("channels") or {}).get(p) if isinstance(data.get("channels"), dict) else None, cfg["channels"][p])
        cfg["show"][p] = pick((data.get("show") or {}).get(p) if isinstance(data.get("show"), dict) else None, True)
    for key in ("on_top", "extra_emotes", "debug_log", "check_updates"):
        cfg[key] = pick(data.get(key), cfg[key])
    cfg["language"] = pick(data.get("language"), "auto") or "auto"
    ov = data.get("overlay") if isinstance(data.get("overlay"), dict) else {}
    cfg["overlay"]["enabled"] = pick(ov.get("enabled"), False)
    port = pick(ov.get("port"), OVERLAY_PORT)
    cfg["overlay"]["port"] = port if 1024 <= port <= 65535 else OVERLAY_PORT
    vid = data.get("video") if isinstance(data.get("video"), dict) else {}
    v = cfg["video"]
    v["enabled"] = pick(vid.get("enabled"), True)
    v["quality"] = {k: q for k, q in pick(vid.get("quality"), {}).items() if k in PLATFORMS and isinstance(q, str)}
    v["volume"] = {k: max(0, min(100, n)) for k, n in pick(vid.get("volume"), {}).items()
                   if k in PLATFORMS and isinstance(n, int) and not isinstance(n, bool)}
    v["audio"] = vid.get("audio") if vid.get("audio") in PLATFORMS else None
    sizes = vid.get("sizes")
    if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(n, int) and n >= 0 for n in sizes):
        v["sizes"] = sizes
    return cfg


def save_config(cfg: dict) -> None:
    write_private(CONFIG, {k: v for k, v in cfg.items() if not k.startswith("_")})


def load_config_apps() -> dict:
    """Developer apps from config.json of older versions (now in the secret store)."""
    try:
        return dict(json.loads(CONFIG.read_text()).get("apps") or {})
    except Exception:  # noqa: BLE001
        return {}
