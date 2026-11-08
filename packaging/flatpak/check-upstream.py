#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compares the native libraries of our Flatpak with Flathub's io.mpv.Mpv manifest.

Dependabot doesn't track git tags in Flatpak manifests. Flathub's mpv package is
maintained and updated quickly (security fixes in FFmpeg etc.), so it is a good reference.
Prints the modules whose version differs; exit code 1 if there are any.

    python3 packaging/flatpak/check-upstream.py
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

OURS = Path(__file__).resolve().parent / "io.github.Corrgan.MultiChat.yml"
UPSTREAM = "https://raw.githubusercontent.com/flathub/io.mpv.Mpv/master/io.mpv.Mpv.yml"
MODULES = ("luajit", "libass", "ffmpeg", "libplacebo", "hwdata", "libdisplay-info", "mpv", "deno")


def versions(manifest: str) -> dict[str, str]:
    """module name → tag (git sources) or the version in the archive/file URL."""
    out: dict[str, str] = {}
    blocks = re.split(r"\n\s*- name:\s*", manifest)
    for block in blocks[1:]:
        name = block.split("\n", 1)[0].split("#")[0].strip().strip("'\"")
        if name not in MODULES or name in out:
            continue
        tag = re.search(r"\btag:\s*['\"]?([^\s'\"]+)", block)
        url = re.search(r"\burl:\s*\S*?(?:/v?|-)(\d+(?:\.\d+)+)(?:\.tar|\.zip|/|-)", block)
        if tag:
            out[name] = tag.group(1)
        elif url:
            out[name] = url.group(1)
    return out


def main() -> int:
    ours = versions(OURS.read_text())
    with urllib.request.urlopen(UPSTREAM, timeout=30) as r:
        theirs = versions(r.read().decode())
    behind = [(m, ours[m], theirs[m]) for m in MODULES if m in ours and m in theirs and ours[m] != theirs[m]]
    for m, a, b in behind:
        print(f"- **{m}**: we use `{a}`, Flathub's mpv uses `{b}`")
    if not behind:
        print("All native libraries match Flathub's io.mpv.Mpv.")
    return 1 if behind else 0


if __name__ == "__main__":
    sys.exit(main())
