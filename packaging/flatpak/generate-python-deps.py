#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Writes python-deps.json – the Python packages of the Flatpak as offline sources.

flatpak-builder has no network access while building, so every package is listed
with URL and SHA-256. The versions come from packaging/requirements-lock.txt.
PyQt6 comes from the PyQt BaseApp; build and test tools are left out.

    python3 packaging/flatpak/generate-python-deps.py   (needs network and `packaging`)
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from packaging.markers import Marker
from packaging.utils import parse_wheel_filename

HERE = Path(__file__).resolve().parent
LOCK = HERE.parent / "requirements-lock.txt"
OUT = HERE / "python-deps.json"

# provided by the BaseApp, or only needed to build/test the AppImage
SKIP = {
    "pyqt6", "pyqt6-qt6", "pyqt6-sip", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph",
    "macholib", "pefile", "pytest", "iniconfig", "pluggy", "pygments", "setuptools", "packaging",
}
# The KDE runtime's Python version is not fixed here: offer wheels for several.
PYTHONS = ("312", "313", "314")
ENV = {"implementation_name": "cpython", "sys_platform": "linux", "platform_system": "Linux",
       "platform_machine": "x86_64", "python_full_version": "3.13.0", "python_version": "3.13", "os_name": "posix"}


def wanted(filename: str) -> bool:
    _name, _ver, _build, tags = parse_wheel_filename(filename)
    for t in tags:
        plat_ok = t.platform == "any" or (re.match(r"(many|musl)?linux", t.platform) and t.platform.endswith("x86_64")
                                          and not t.platform.startswith("musllinux"))
        if not plat_ok:
            continue
        if t.interpreter in ("py3", "py2.py3") or t.interpreter.startswith("py3"):
            return True
        if t.abi == "abi3" and t.interpreter.startswith("cp3"):
            return True
        if t.interpreter in {f"cp{v}" for v in PYTHONS} and t.abi == t.interpreter:
            return True
    return False


def main() -> None:
    pins = []
    for line in LOCK.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$", line)
        if not m:
            continue
        name, version, marker = m.group(1).lower(), m.group(2), m.group(3)
        if name in SKIP or (marker and not Marker(marker).evaluate(ENV)):
            continue
        pins.append((name, version))

    sources, names = [], []
    for name, version in pins:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as r:
            files = json.load(r)["urls"]
        wheels = [f for f in files if f["packagetype"] == "bdist_wheel" and wanted(f["filename"])]
        # one wheel per tag set is enough; prefer the newest manylinux tag
        chosen: dict[str, dict] = {}
        for f in wheels:
            key = parse_wheel_filename(f["filename"])[3]
            key = ",".join(sorted({f"{t.interpreter}-{t.abi}" for t in key}))
            chosen.setdefault(key, f)
        picked = list(chosen.values()) or [f for f in files if f["packagetype"] == "sdist"][:1]
        if not picked:
            raise SystemExit(f"no usable file for {name}=={version}")
        for f in picked:
            sources.append({"type": "file", "url": f["url"], "sha256": f["digests"]["sha256"]})
        names.append(name)
        print(f"{name}=={version}: {', '.join(f['filename'] for f in picked)}")

    module = {
        "name": "python-deps",
        "buildsystem": "simple",
        "build-commands": [
            "pip3 install --verbose --exists-action=i --no-index --find-links=file://${PWD} "
            "--prefix=${FLATPAK_DEST} --no-build-isolation " + " ".join(names)
        ],
        "sources": sources,
    }
    OUT.write_text(json.dumps(module, indent=2) + "\n")
    print(f"→ {OUT} ({len(names)} packages, {len(sources)} files)")


if __name__ == "__main__":
    main()
