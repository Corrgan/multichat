#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Removes system libraries from the PyInstaller bundle that every desktop provides.

PyInstaller copies the build machine's libraries (Ubuntu 22.04). Libraries such as
libstdc++, freetype, X11 or OpenSSL must come from the host instead:
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

HOST_LIBS = [
    # compiler runtime
    "libstdc++", "libgcc_s",
    # X11 / xcb core (the xcb-util libraries Qt needs are kept – often not installed)
    "libX11", "libX11-xcb", "libXau", "libXdmcp", "libXext", "libXfixes", "libXi", "libXinerama",
    "libXrandr", "libXrender", "libXcursor", "libXcomposite", "libXdamage", "libbsd", "libmd",
    "libxcb", "libxcb-render", "libxcb-shm", "libxcb-xfixes", "libxcb-xkb", "libxcb-randr",
    "libxcb-shape", "libxcb-sync", "libxcb-glx", "libxkbcommon",
    # fonts and text
    "libfontconfig", "libfreetype", "libharfbuzz", "libgraphite2", "libfribidi", "libexpat",
    "libpng16", "libz", "libbrotlicommon", "libbrotlidec",
    # GLib, D-Bus, systemd and friends
    "libglib-2.0", "libgobject-2.0", "libgio-2.0", "libgmodule-2.0", "libgthread-2.0", "libpcre2-8",
    "libmount", "libblkid", "libselinux", "libdbus-1", "libsystemd", "libcap", "libgcrypt",
    "libgpg-error", "liblz4", "liblzma", "libzstd",
    # TLS / Kerberos (host OpenSSL → host CA certificates)
    "libssl", "libcrypto", "libgssapi_krb5", "libkrb5", "libk5crypto", "libkrb5support",
    "libkeyutils", "libcom_err",
    # GTK stack (only needed by the GTK3 theme plugin, which is removed)
    "libgtk-3", "libgdk-3", "libgdk_pixbuf-2.0", "libatk-1.0", "libatk-bridge-2.0", "libatspi",
    "libcairo", "libcairo-gobject", "libpango-1.0", "libpangocairo-1.0", "libpangoft2-1.0",
    "libpixman-1", "libepoxy", "libthai", "libdatrie", "libjpeg",
]
PLUGINS = ["PyQt6/Qt6/plugins/platformthemes/libqgtk3.so"]


def main(root: Path) -> None:
    pattern = re.compile(r"^(%s)\.so(\.[0-9.]+)?$" % "|".join(re.escape(n) for n in HOST_LIBS))
    removed = []
    for f in sorted(root.iterdir()):
        if pattern.match(f.name):
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
