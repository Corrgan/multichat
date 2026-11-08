# SPDX-License-Identifier: GPL-3.0-or-later
"""Packaging consistency: the Flatpak's Python packages match the lock file."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location("gen", ROOT / "packaging/flatpak/generate-python-deps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_flatpak_python_deps_match_lock():
    pytest.importorskip("packaging")
    gen = _generator()
    from packaging.markers import Marker

    expected = {}
    for line in gen.LOCK.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$", line)
        if m and m.group(1).lower() not in gen.SKIP and not (m.group(3) and not Marker(m.group(3)).evaluate(gen.ENV)):
            expected[_norm(m.group(1))] = m.group(2)

    module = json.loads(gen.OUT.read_text())
    found = {}
    for src in module["sources"]:
        fn = src["url"].rsplit("/", 1)[1]
        if fn.endswith(".whl"):
            name, version = fn.split("-")[:2]
        else:  # sdist: name-version.tar.gz
            name, version = re.match(r"(.+)-([^-]+)\.(?:tar\.gz|zip)$", fn).groups()
        found[_norm(name)] = version
    assert found == expected, (
        "packaging/flatpak/python-deps.json is out of date – run "
        "python3 packaging/flatpak/generate-python-deps.py"
    )


@pytest.mark.skipif(not shutil.which("ffmpeg") and not (ROOT / "tests/data/testclip.webm").exists(), reason="no clip")
def test_video_selftest():
    from multichat.video import player

    if not player.load_mpv():
        pytest.skip(f"libmpv not available: {player.MPV_ERROR}")
    r = subprocess.run([sys.executable, "-m", "multichat", "--selftest-video", str(ROOT / "tests/data/testclip.webm")],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0 and "OK      video decoded" in r.stdout, r.stdout + r.stderr
