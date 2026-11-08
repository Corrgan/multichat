#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Removes system libraries from the PyInstaller bundle that every desktop provides.

PyInstaller copies the build machine's libraries (Ubuntu 22.04). Libraries such as
libstdc++, freetype, ncurses, X11 or OpenSSL must come from the host instead:
- the host's libmpv (and other host libraries loaded later) need the host's newer
  versions – an older bundled libstdc++/freetype makes loading libmpv fail;
- the host's OpenSSL knows where the host keeps its CA certificates.
This follows the AppImage "excludelist" idea. The Qt GTK3 theme plugin is removed as
well, so the GTK stack doesn't have to be bundled.

    python3 packaging/prune-bundle.py <dist>/multichat/_internal
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Everything named lib*.so* at the top of the bundle comes from the build machine and is
# removed, except libraries that hosts often lack or ship under another soname:
KEEP = [
    r"libpython3[.0-9]*",  # the interpreter itself
    r"libQt6\w*", r"libicu\w*",  # Qt from the PyQt6 wheel (links into PyQt6/Qt6/lib)
    r"libmpdec",  # _decimal – Fedora ships libmpdec.so.4, Ubuntu 22.04 .so.3
    r"libbz2",  # libbz2.so.1.0 is a Debian soname
    r"libcrypt", r"libnsl", r"libtirpc",  # soname differences between distributions
    r"libxcb-(cursor|icccm|image|keysyms|render-util|util)",  # xcb-util: not always installed
    r"libxkbcommon-x11",
]
# Kept aside instead of deleted: used only if the host has no libssl.so.3 (e.g. Fedora 45 ships
# OpenSSL 4 as libssl.so.4). packaging/entry.py preloads them in that case.
FALLBACK = ["libssl.so.3", "libcrypto.so.3"]
FALLBACK_DIR = "fallback-libs"
PLUGINS = ["PyQt6/Qt6/plugins/platformthemes/libqgtk3.so"]


def main(root: Path) -> None:
    keep = re.compile(r"^(%s)\.so(\.[0-9.]+)?$" % "|".join(KEEP))
    removed = []
    (root / FALLBACK_DIR).mkdir(exist_ok=True)
    for name in FALLBACK:
        if (root / name).is_file():
            (root / name).replace(root / FALLBACK_DIR / name)
    for f in sorted(root.iterdir()):
        is_lib = (
            f.is_file() and not f.is_symlink()  # symlinks point into the bundle (Qt) – keep them
            and f.name.startswith("lib") and ".so" in f.name and ".cpython-" not in f.name
        )
        if is_lib and not keep.match(f.name):
            f.unlink()
            removed.append(f.name)
    for rel in PLUGINS:
        (root / rel).unlink(missing_ok=True)
    print(f"removed {len(removed)} host libraries: {' '.join(removed)}")

    # every remaining ELF file must find its libraries in the bundle or on a normal host
    bundled = {f.name for f in root.rglob("*.so*")}
    host = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout
    missing = set()
    for f in root.rglob("*.so*"):
        if not f.is_file():
            continue
        out = subprocess.run(["readelf", "-d", str(f)], capture_output=True, text=True).stdout
        for need in re.findall(r"NEEDED.*\[(.+)\]", out):
            if need not in bundled and need not in host:
                missing.add(f"{f.name} → {need}")
    if missing:
        print("WARNING: libraries found neither in the bundle nor on this machine:")
        print("\n".join(f"  {m}" for m in sorted(missing)))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
