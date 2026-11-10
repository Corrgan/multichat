#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run bounded component regressions against real FFmpeg libraries; networking is loopback-only."""
from __future__ import annotations

import argparse
import json
import os
import resource
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def compile_client(source: Path, build: Path, output: Path) -> tuple[Path, dict]:
    libraries = []
    directories = []
    for name in ("avformat", "avcodec", "avutil"):
        directory = build / ("lib" + name)
        directories.append(str(directory))
        static = directory / ("lib" + name + ".a")
        if static.is_file():
            libraries.append(str(static))
        else:
            libraries.extend(("-L" + str(directory), "-l" + name))
    binary = output / "native-regressions"
    subprocess.run([os.environ.get("CC", "cc"), "-I" + str(source), "-I" + str(build),
                    str(HERE / "native_ffmpeg_regressions.c"), *libraries, "-lm", "-pthread", "-o", str(binary)],
                   check=True)
    env = os.environ | {"LD_LIBRARY_PATH": os.pathsep.join(directories) +
                         (os.pathsep + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else "")}
    return binary, env


def limits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_STACK, (512 * 1024, 512 * 1024))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))


def command(binary: Path, mode: str, env: dict) -> dict:
    try:
        result = subprocess.run([str(binary), mode], env=env, capture_output=True, text=True,
                                timeout=2, preexec_fn=limits)
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "timeout": False}
    except subprocess.TimeoutExpired:
        return {"timeout": True}


def redirects(binary: Path, env: dict) -> dict:
    stop = threading.Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    url = f"rtsp://127.0.0.1:{listener.getsockname()[1]}/redirect"
    requests = []

    def serve():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(0.5)
                try:
                    data = connection.recv(4096)
                    if not data:
                        continue
                    requests.append(1)
                    connection.sendall(("RTSP/1.0 302 Moved\r\nCSeq: 1\r\n"
                                        f"Location: {url}\r\nContent-Length: 0\r\n\r\n").encode())
                except OSError:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        try:
            result = subprocess.run([str(binary), "rtsp", url], env=env, capture_output=True, text=True, timeout=2)
            report = {"returncode": result.returncode, "stderr": result.stderr, "timeout": False}
        except subprocess.TimeoutExpired:
            report = {"timeout": True}
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=2)
    report["requests"] = len(requests)
    return report


def pssh(binary: Path, env: dict, directory: Path) -> dict:
    count = 10000
    def box(kind: bytes, contents: bytes) -> bytes:
        return struct.pack(">I4s", len(contents) + 8, kind) + contents
    record = box(b"pssh", bytes(4) + bytes.fromhex("00112233445566778899aabbccddeeff") + bytes(4))
    movie = box(b"ftyp", b"isom" + bytes(4) + b"isom") + box(b"moov", box(b"trak", b"") + record * count)
    path = directory / "many-pssh.mp4"
    path.write_bytes(movie)
    start = time.monotonic()
    try:
        result = subprocess.run([str(binary), "pssh", str(path), str(count)], env=env,
                                capture_output=True, text=True, timeout=3, preexec_fn=limits)
        report = {"returncode": result.returncode, "stderr": result.stderr, "timeout": False}
    except subprocess.TimeoutExpired:
        report = {"timeout": True}
    return report | {"count": count, "elapsed_seconds": time.monotonic() - start}


def exercise(source: Path, build: Path, expect_fixed: bool = True) -> dict:
    with tempfile.TemporaryDirectory(prefix="multichat-native-test-") as directory:
        binary, env = compile_client(source.resolve(), build.resolve(), Path(directory))
        report = {mode: command(binary, mode, env) for mode in ("empty-replace", "deep-free", "hdr-initialized")}
        report["rtsp"] = redirects(binary, env)
        report["pssh"] = pssh(binary, env, Path(directory))
    if expect_fixed:
        for mode in ("empty-replace", "deep-free", "hdr-initialized"):
            assert not report[mode]["timeout"] and report[mode]["returncode"] == 0, (mode, report[mode])
        assert not report["rtsp"]["timeout"], report["rtsp"]
        assert report["rtsp"]["returncode"] == 0 and report["rtsp"]["requests"] == 9, report["rtsp"]
        assert "Too many RTSP redirects" in report["rtsp"]["stderr"], report["rtsp"]
        assert not report["pssh"]["timeout"] and report["pssh"]["returncode"] == 0, report["pssh"]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("."))
    parser.add_argument("--build-dir", type=Path, default=Path("."))
    parser.add_argument("--observe-old", action="store_true")
    options = parser.parse_args()
    print(json.dumps(exercise(options.source_dir, options.build_dir, not options.observe_old), indent=2))

