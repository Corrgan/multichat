# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the real updater entry point; replace only local input/HTTP boundaries."""
import importlib.util
import io
import json
import re
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCAL = (ROOT / "packaging/flatpak/io.github.Corrgan.MultiChat.yml").read_text(encoding="utf-8")
REMOTE = LOCAL.replace("  - python-deps.json\n", "")
PARENT = "a" * 40
SUBMODULE = "b" * 40


def checker():
    spec = importlib.util.spec_from_file_location("upstream_check", ROOT / "packaging/flatpak/check-upstream.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http(monkeypatch, module, manifest=REMOTE, *, error=None, submodule=None, included=None):
    calls = []

    def urlopen(request, timeout):
        url = request.full_url
        calls.append(url)
        assert timeout == 30
        if error:
            raise error
        if url == f"{module.API}/git/ref/heads/master":
            body = json.dumps({"object": {"type": "commit", "sha": PARENT}}).encode()
        elif url == f"{module.RAW}/{PARENT}/io.mpv.Mpv.yml":
            body = manifest.encode() if isinstance(manifest, str) else manifest
        elif url == f"{module.API}/contents/shared-modules?ref={PARENT}":
            body = json.dumps(submodule or {
                "type": "submodule", "sha": SUBMODULE,
                "submodule_git_url": "https://github.com/flathub/shared-modules.git",
            }).encode()
        elif url == f"https://raw.githubusercontent.com/flathub/shared-modules/{SUBMODULE}/luajit/luajit.json":
            body = json.dumps(included).encode()
        else:
            pytest.fail(f"Unexpected/unpinned HTTP request: {url}")
        return io.BytesIO(body)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    return calls


@pytest.mark.parametrize("body", [
    "", "modules: []\n", "modules:\n", '{"modules": {"ffmpeg": {"tag": "n9.0.3"}}}',
    "modules:\n  - name: ffmpeg\n    sources:\n      - type: git\n"
    "        url: https://github.com/FFmpeg/FFmpeg.git\n"
    "        tag: n9.0.2\n        commit: " + "c" * 40 + "\n",
    b"\xff\xfe",
])
def test_incomplete_or_restructured_remote_is_unknown_not_success(body, monkeypatch, capsys):
    module = checker()
    http(monkeypatch, module, body)
    assert module.main() == 2
    out = capsys.readouterr()
    assert "UNKNOWN" in out.err
    assert "match" not in out.out.lower()


def test_complete_same_versions_succeed(monkeypatch, capsys):
    module = checker()
    calls = http(monkeypatch, module)
    assert module.main() == 0
    out = capsys.readouterr()
    assert "All monitored native library versions match" in out.out
    assert not out.err and len(calls) == 2


def test_complete_difference_reports_versions_without_claiming_theirs_is_newer(monkeypatch, capsys):
    module = checker()
    # An older declared upstream version is still a difference, not evidence we are behind.
    remote = REMOTE.replace("tag: n9.0.2", "tag: n8.0.1")
    http(monkeypatch, module, remote)
    assert module.main() == 1
    out = capsys.readouterr()
    assert "ffmpeg" in out.out and "n9.0.2" in out.out and "n8.0.1" in out.out
    assert "match" not in out.out.lower() and "newer" not in out.out.lower()
    assert not out.err


@pytest.mark.parametrize("error", [
    HTTPError("https://example.invalid", 503, "unavailable", {}, None),
    TimeoutError("slow upstream"),
])
def test_network_error_is_unknown(monkeypatch, capsys, error):
    module = checker()
    http(monkeypatch, module, error=error)
    assert module.main() == 2
    out = capsys.readouterr()
    assert "UNKNOWN" in out.err and not out.out


@pytest.mark.parametrize("body", [
    REMOTE + "\n" + re.search(r"  - name: luajit\b.*?(?=\n  - )", REMOTE, re.S).group(),
    REMOTE.replace("tag: n9.0.2", "tag: n9.0.2\n        tag: n9.0.3"),
    REMOTE.replace("https://github.com/libass/libass.git", "https://example.invalid/libass.git"),
    REMOTE.replace("        commit: 946fcce07b6dcd0331c8cc609192aeff5e1924f8\n", ""),
    REMOTE + "\n  - unknown-module.json\n",
    REMOTE.replace("        tag: n9.0.2\n", "        x-checker-data:\n          tag: n9.0.2\n"),
    REMOTE.replace("- name: ffmpeg", "- name:ffmpeg"),
    REMOTE.replace("tag: n9.0.2", "tag:n9.0.2"),
])
def test_duplicate_unknown_or_unpinned_sources_never_report_match(body, monkeypatch, capsys):
    module = checker()
    http(monkeypatch, module, body)
    assert module.main() == 2
    out = capsys.readouterr()
    assert "UNKNOWN" in out.err and not out.out


@pytest.mark.parametrize("body", ["modules: []\n", LOCAL + "\n" + re.search(
    r"  - name: luajit\b.*?(?=\n  - )", LOCAL, re.S).group()])
def test_local_manifest_is_also_validated_before_network(body, tmp_path, monkeypatch, capsys):
    module = checker()
    path = tmp_path / "manifest.yml"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(module, "OURS", path)
    calls = http(monkeypatch, module)
    assert module.main() == 2
    assert not calls
    assert "UNKNOWN" in capsys.readouterr().err


def test_missing_local_manifest_is_unknown(tmp_path, monkeypatch, capsys):
    module = checker()
    monkeypatch.setattr(module, "OURS", tmp_path / "absent.yml")
    calls = http(monkeypatch, module)
    assert module.main() == 2 and not calls
    assert "UNKNOWN" in capsys.readouterr().err


def test_real_submodule_shape_is_resolved_at_its_parent_commit(monkeypatch, capsys):
    module = checker()
    block = re.search(r"  - name: luajit\b.*?(?=\n  - )", REMOTE, re.S).group()
    tag = re.search(r"        tag: (\S+)", block).group(1)
    commit = re.search(r"        commit: (\S+)", block).group(1)
    remote = REMOTE.replace(block, "  - shared-modules/luajit/luajit.json\n")
    calls = http(monkeypatch, module, remote, included={
        "name": "luajit", "sources": [{
            "type": "git", "url": "https://github.com/openresty/luajit2.git", "tag": tag, "commit": commit,
            "x-checker-data": {"tag-pattern": "^v.*$"},
        }],
    })
    assert module.main() == 0
    assert len(calls) == 4
    assert PARENT in calls[1] and PARENT in calls[2] and SUBMODULE in calls[3]
    assert "match" in capsys.readouterr().out


def test_submodule_provenance_mismatch_is_unknown_and_not_followed(monkeypatch, capsys):
    module = checker()
    block = re.search(r"  - name: luajit\b.*?(?=\n  - )", REMOTE, re.S).group()
    remote = REMOTE.replace(block, "  - shared-modules/luajit/luajit.json\n")
    calls = http(monkeypatch, module, remote, submodule={
        "type": "submodule", "sha": SUBMODULE, "submodule_git_url": "https://example.invalid/untrusted.git",
    })
    assert module.main() == 2 and len(calls) == 3
    assert "provenance" in capsys.readouterr().err


def test_conflicting_architecture_versions_are_unknown(monkeypatch, capsys):
    module = checker()
    addition = (
        "\n      - type: file\n"
        "        url: https://github.com/denoland/deno/releases/download/v2.9.9/deno-aarch64-unknown-linux-gnu.zip\n"
        "        sha256: " + "c" * 64 + "\n"
    )
    remote = REMOTE.replace("        dest-filename: deno.zip", "        dest-filename: deno.zip" + addition)
    http(monkeypatch, module, remote)
    assert module.main() == 2
    assert "conflicting" in capsys.readouterr().err

def test_nested_monitored_duplicate_is_unknown(monkeypatch, capsys):
    module = checker()
    remote = REMOTE.replace("  - name: luajit", "  - name: luajit", 1)
    remote += "\n  - name: helper\n    modules:\n      - name: 'ffmpeg' # unexpected duplicate\n"
    http(monkeypatch, module, remote)
    assert module.main() == 2
    assert "Nested" in capsys.readouterr().err


def workflow_script(step):
    import textwrap

    source = (ROOT / ".github/workflows/flatpak-upstream.yml").read_text(encoding="utf-8")
    block = re.search(r"      - name: " + re.escape(step) + r"\n(.*?)(?=\n      -|\Z)", source, re.S).group(1)
    return textwrap.dedent(block.split("        run: |\n", 1)[1])


def bash():
    import os
    import shutil

    executable = Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else None
    if executable and executable.is_file():
        return str(executable)
    executable = shutil.which("bash")
    if not executable:
        pytest.skip("Bash is required to exercise the GitHub workflow shell")
    return executable


@pytest.mark.parametrize("code,status,success", [(0, "match", True), (1, "different", True), (2, "unknown", False)])
def test_workflow_separates_unknown_from_real_version_difference(tmp_path, code, status, success):
    import os
    import subprocess

    output = tmp_path / "output"
    script = f"python3() {{ return {code}; }}\n" + workflow_script("Compare with Flathub's io.mpv.Mpv")
    result = subprocess.run(
        [bash(), "--noprofile", "--norc", "-e", "-c", script], cwd=tmp_path,
        env=os.environ | {"GITHUB_OUTPUT": output.as_posix()}, capture_output=True, text=True,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    assert output.read_text(encoding="utf-8").strip() == f"result={status}"
    source = (ROOT / ".github/workflows/flatpak-upstream.yml").read_text(encoding="utf-8")
    assert "if: steps.check.outputs.result == 'different'" in source


@pytest.mark.parametrize("response,exit_code,success,creates", [
    ("", 7, False, False), ("not-a-number", 0, False, False),
    ("1", 0, True, False), ("0", 0, True, True),
])
def test_issue_lookup_errors_do_not_masquerade_as_existing_issue(tmp_path, response, exit_code, success, creates):
    import os
    import subprocess

    calls = tmp_path / "calls"
    (tmp_path / "report.md").write_text("fixture version differences\n", encoding="utf-8")
    script = (
        "gh() {\n"
        '  if [ "$1 $2" = "issue list" ]; then\n'
        f"    printf '%s' '{response}'\n    return {exit_code}\n"
        "  fi\n"
        '  printf "%s\\n" "$*" >> "$TEST_GH_CALLS"\n'
        "}\n"
    ) + workflow_script("Open a reminder issue")
    result = subprocess.run(
        [bash(), "--noprofile", "--norc", "-e", "-c", script], cwd=tmp_path,
        env=os.environ | {"GITHUB_REPOSITORY": "test/repo", "TITLE": "fixture reminder",
                          "TEST_GH_CALLS": calls.as_posix()}, capture_output=True, text=True,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    assert calls.exists() is creates
    if not success:
        assert "already exists" not in result.stdout


