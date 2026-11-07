# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Storage for login tokens."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class TokenStore:
    """Tokens in ~/.config/multichat/tokens.json (readable only by you, 0600)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            self.data = {}

    def get(self, platform: str) -> dict:
        with self.lock:
            return dict(self.data.get(platform) or {})

    def update(self, platform: str, **values) -> None:
        with self.lock:
            self.data.setdefault(platform, {}).update(values)
            self._save()

    def replace(self, platform: str, values: dict) -> None:
        with self.lock:
            self.data[platform] = dict(values)
            self._save()

    def delete(self, platform: str) -> None:
        with self.lock:
            self.data.pop(platform, None)
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)
