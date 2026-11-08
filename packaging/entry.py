# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Entry script for PyInstaller (the package itself uses relative imports)."""
import ctypes
import os
import sys


def _openssl() -> None:
    """Python's ssl module needs OpenSSL 3 (libssl.so.3). The AppImage uses the system's –
    it knows where the CA certificates are – but some systems only have a newer major
    version (Fedora 45: OpenSSL 4). Then the bundled copy is loaded before anything imports
    ssl; the dynamic linker reuses it for every later libssl.so.3 dependency."""
    try:
        ctypes.CDLL("libssl.so.3")
        return
    except OSError:
        pass
    folder = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "fallback-libs")
    try:
        for lib in ("libcrypto.so.3", "libssl.so.3"):
            ctypes.CDLL(os.path.join(folder, lib), mode=ctypes.RTLD_GLOBAL)
        os.environ["MULTICHAT_OPENSSL"] = "bundled OpenSSL 3 (the system has no libssl.so.3)"
    except OSError as e:
        os.environ["MULTICHAT_OPENSSL"] = f"none: {e}"


_openssl()

from multichat.core.tls import ensure_ca_certificates  # noqa: E402

ensure_ca_certificates()  # before any library builds its SSL context (aiohttp does so on import)

from multichat.app import main  # noqa: E402

main()
