# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline release diagnostics. No config, keyring, account or GUI is initialized."""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import platform
import sys


def file_evidence(path) -> dict:
    source = Path(path)
    result = {"path": str(source)}
    if source.is_file():
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        result["sha256"] = digest.hexdigest()
    else:
        result["error"] = "file is not accessible in this runtime"
    return result


def collect() -> dict:
    from .. import __version__

    result = {"schema": 1, "application": {"name": "MultiChat", "version": __version__},
              "probe_policy": "host-video-preloaded-v1", "video_loaded": False,
              "python": platform.python_version(), "platform": platform.platform(),
              "frozen": bool(getattr(sys, "frozen", False)), "components": [], "loaded_files": [], "errors": []}

    def probe(name, action):
        try:
            versions, paths = action()
            result["components"].append({"name": name, "versions": versions,
                                         "files": [file_evidence(path) for path in paths]})
        except Exception as error:  # diagnostics stay explicit and machine readable
            result["errors"].append({"component": name, "error": f"{type(error).__name__}: {error}"})

    def openssl():
        import ssl
        import _ssl

        return {"openssl": ssl.OPENSSL_VERSION}, [_ssl.__file__]

    def cryptography():
        from cryptography.hazmat.bindings import _rust
        from cryptography.hazmat.bindings.openssl.binding import Binding

        binding = Binding()
        return {"openssl": binding.ffi.string(binding.lib.OpenSSL_version(0)).decode()}, [_rust.__file__]

    def curl():
        import curl_cffi
        from curl_cffi import _wrapper, ffi, lib

        return {"curl_cffi": curl_cffi.__version__, "curl": ffi.string(lib.curl_version()).decode()}, [_wrapper.__file__]

    def xml():
        from lxml import etree

        version = lambda value: ".".join(map(str, value))
        return {"lxml": version(etree.LXML_VERSION), "libxml2": version(etree.LIBXML_VERSION),
                "libxslt": version(etree.LIBXSLT_VERSION)}, [etree.__file__]

    def qt():
        from PyQt6 import QtCore

        return {"qt": QtCore.qVersion(), "pyqt": QtCore.PYQT_VERSION_STR}, [QtCore.__file__]

    # Mirror the application case where video is loaded before a stream resolver.
    # Otherwise a static XML wheel could look corrected in isolation while globally
    # loaded host libraries interpose older symbols on its later import.
    try:
        from ..video.player import load_mpv, MPV_ERROR

        result["video_loaded"] = bool(load_mpv())
        if not result["video_loaded"]:
            result["errors"].append({"component": "mpv", "error": MPV_ERROR or "application libmpv loader failed"})
    except Exception as error:
        result["errors"].append({"component": "mpv", "error": str(error)})
    probe("python-ssl", openssl)
    probe("cryptography", cryptography)
    probe("curl-cffi", curl)
    probe("lxml", xml)
    probe("qt", qt)
    # Loading these libraries does not construct a player, decode data or contact a service.
    # Record the exact mapped objects so host mpv/FFmpeg are distinguished from shipped ones.
    for name in ("mpv", "avutil", "avformat", "avcodec"):
        path = ctypes.util.find_library(name)
        if not path:
            result["errors"].append({"component": name, "error": "system library was not found"})
            continue
        try:
            library = ctypes.CDLL(path)
            if name == "avutil":
                library.av_version_info.restype = ctypes.c_char_p
                result["components"].append({"name": "ffmpeg", "versions": {"ffmpeg": library.av_version_info().decode()},
                                             "library": path, "files": []})
        except OSError as error:
            result["errors"].append({"component": name, "error": str(error)})
    maps = Path("/proc/self/maps")
    if maps.is_file():
        paths = set()
        for line in maps.read_text(encoding="utf-8").splitlines():
            parts = line.split(None, 5)
            if len(parts) == 6 and parts[5].startswith("/"):
                paths.add(parts[5].removesuffix(" (deleted)"))
        for path in sorted(paths):
            try:
                with open(path, "rb") as stream:
                    if stream.read(4) != b"\x7fELF":
                        continue
                result["loaded_files"].append(file_evidence(path))
            except OSError as error:
                result["errors"].append({"component": "loaded-file", "path": path, "error": str(error)})
        # Associate FFmpeg's textual version with the exact loaded avutil object.
        for component in result["components"]:
            if component["name"] == "ffmpeg":
                component["files"] = [item for item in result["loaded_files"] if "libavutil.so" in Path(item["path"]).name]
    return result


def main() -> None:
    print(json.dumps(collect(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
