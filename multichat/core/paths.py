# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Paths to bundled resources (also inside the AppImage)."""
from __future__ import annotations

from pathlib import Path

# multichat/resources – PyInstaller keeps the same layout below sys._MEIPASS
RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def resource(name: str) -> Path:
    return RESOURCES / name


def app_icon_path() -> Path:
    return resource("multichat.png")
