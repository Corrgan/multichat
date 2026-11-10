# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Settings in ~/.config/multichat/config.json."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .constants import PLATFORMS

# defaults for the alert highlighting (multichat.chat.alerts imports them from here)
DEFAULT_ALERTS = {
    "sub": True, "gift": True, "raid": True, "bits": True, "superchat": True, "member": True,
    "min_bits": 1, "min_gift": 1, "min_raid": 1, "min_superchat": 0,
}

CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "multichat"
CONFIG = CONF_DIR / "config.json"
OVERLAY_PORT = 17564


def write_private(path: Path, data: dict) -> None:
    """Writes JSON readable only by the user – via a temporary file, so a crash or a full
    disk while saving never leaves a half-written file behind."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    # O_NOFOLLOW: never write through a symlink left at the temporary name. fchmod: the mode
    # argument only applies when the file is created – a left-over temp file with wider
    # permissions would otherwise become the settings/token file as it is.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (AttributeError, OSError):  # not on this platform/filesystem
        pass
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
        "chat_font": 14,
        "usercard_docked": False,
        "groups": clean_groups(None, {}),  # [{"name": str, "channels": [{"platform", "channel", "video"}]}]
        "active_group": 0,
        "side_by_side": False,
        "alerts": dict(DEFAULT_ALERTS),
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
    cfg["groups"] = clean_groups(data.get("groups"), cfg["channels"])
    cfg["active_group"] = max(0, min(len(cfg["groups"]) - 1, pick(data.get("active_group"), 0)))
    cfg["side_by_side"] = pick(data.get("side_by_side"), False)
    for key in ("on_top", "extra_emotes", "debug_log", "check_updates"):
        cfg[key] = pick(data.get(key), cfg[key])
    cfg["language"] = pick(data.get("language"), "auto") or "auto"
    cfg["chat_font"] = max(9, min(40, pick(data.get("chat_font"), 14)))
    cfg["usercard_docked"] = pick(data.get("usercard_docked"), False)
    al = data.get("alerts") if isinstance(data.get("alerts"), dict) else {}
    for k, default in DEFAULT_ALERTS.items():
        value = pick(al.get(k), default)
        cfg["alerts"][k] = max(0, min(1_000_000, value)) if k.startswith("min_") else value
    ov = data.get("overlay") if isinstance(data.get("overlay"), dict) else {}
    cfg["overlay"]["enabled"] = pick(ov.get("enabled"), False)
    port = pick(ov.get("port"), OVERLAY_PORT)
    cfg["overlay"]["port"] = port if 1024 <= port <= 65535 else OVERLAY_PORT
    vid = data.get("video") if isinstance(data.get("video"), dict) else {}
    v = cfg["video"]
    v["enabled"] = pick(vid.get("enabled"), True)
    # per platform ("twitch") or per channel ("twitch:somechannel")
    v["quality"] = _newest({k: q for k, q in pick(vid.get("quality"), {}).items() if _video_key(k) and isinstance(q, str)})
    v["volume"] = _newest({k: max(0, min(100, n)) for k, n in pick(vid.get("volume"), {}).items()
                           if _video_key(k) and isinstance(n, int) and not isinstance(n, bool)})
    v["audio"] = vid.get("audio") if _video_key(vid.get("audio")) else None
    sizes = vid.get("sizes")
    if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(n, int) and n >= 0 for n in sizes):
        v["sizes"] = sizes
    return cfg


MAX_GROUPS = 10
MAX_PER_PLATFORM = 1  # channels per platform in one group (more channels: more groups)
MAX_CHANNEL_LEN = 200  # YouTube video URLs are the longest entries


MAX_REMEMBERED = 100  # channels whose video quality/volume is kept


def _newest(d: dict) -> dict:
    """The last MAX_REMEMBERED entries (newer ones are written later) – every channel ever
    entered would otherwise stay in the settings file."""
    return dict(list(d.items())[-MAX_REMEMBERED:])


def _video_key(k) -> bool:
    if not isinstance(k, str):
        return False
    platform, _sep, channel = k.partition(":")
    return platform in PLATFORMS and len(channel) <= MAX_CHANNEL_LEN and not any(c.isspace() for c in channel)


def clean_groups(raw, old_channels: dict) -> list[dict]:
    """Validated channel groups. Older settings had one channel per platform – they become
    the first group. There is always at least one group."""
    groups = []
    for g in raw if isinstance(raw, list) else []:
        if not isinstance(g, dict) or len(groups) >= MAX_GROUPS:
            continue
        name = g.get("name") if isinstance(g.get("name"), str) else ""
        channels, count = [], {p: 0 for p in PLATFORMS}
        for c in g.get("channels") if isinstance(g.get("channels"), list) else []:
            if not isinstance(c, dict) or c.get("platform") not in PLATFORMS:
                continue
            value = c.get("channel") if isinstance(c.get("channel"), str) else ""
            value = value.strip()[:MAX_CHANNEL_LEN]
            if count[c["platform"]] >= MAX_PER_PLATFORM:
                continue
            count[c["platform"]] += 1
            channels.append({"platform": c["platform"], "channel": value,
                             "video": c.get("video") if isinstance(c.get("video"), bool) else True})
        groups.append({"name": name.strip()[:40], "channels": channels})  # "" → "Group N" in the UI language
    if not groups:  # first start or settings from before groups existed
        groups.append({
            "name": "",
            "channels": [{"platform": p, "channel": (old_channels.get(p) or "").strip()[:MAX_CHANNEL_LEN], "video": True}
                         for p in PLATFORMS],
        })
    return groups


def save_config(cfg: dict) -> None:
    write_private(CONFIG, {k: v for k, v in cfg.items() if not k.startswith("_")})


def load_config_apps() -> dict:
    """Developer apps from config.json of older versions (now in the secret store)."""
    try:
        return dict(json.loads(CONFIG.read_text()).get("apps") or {})
    except Exception:  # noqa: BLE001
        return {}
