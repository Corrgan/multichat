# SPDX-License-Identifier: GPL-3.0-or-later
"""Packaging regressions using real distribution metadata and exported bytes."""
import csv
import importlib.metadata
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _script(filename):
    spec = importlib.util.spec_from_file_location(filename, ROOT / "packaging" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _distribution(site, name, files):
    metadata = site / f"{name}-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\nLicense-Expression: MIT\n", encoding="utf-8",
    )
    records = []
    for path, content in files.items():
        destination = metadata / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        records.append([destination.relative_to(site).as_posix(), "", ""])
    with (metadata / "RECORD").open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(records)
    return importlib.metadata.PathDistribution(metadata)


def test_license_export_preserves_distinct_nested_texts_and_legacy_file(tmp_path):
    collector = _script("collect-licenses.py")
    package = _distribution(tmp_path / "site", "example", {
        "licenses/src/mldsa-native/LICENSE": b"first author and license\n",
        "licenses/src/mlkem-native/LICENSE": b"second author and license\n",
        "LICENSE.rst": b"legacy license\n",
        "unrelated.txt": b"not a license\n",
    })
    output = tmp_path / "export"
    records = collector.collect_licenses(output, [package])
    assert (output / "example/licenses/src/mldsa-native/LICENSE").read_bytes() == b"first author and license\n"
    assert (output / "example/licenses/src/mlkem-native/LICENSE").read_bytes() == b"second author and license\n"
    assert (output / "example/LICENSE.rst").read_bytes() == b"legacy license\n"
    assert len(records) == 3
    inventory = json.loads((output / "license-inventory.json").read_text(encoding="utf-8"))
    assert inventory["files"] == records
    assert len({entry["sha256"] for entry in records}) == 3
    assert all(entry["license"] == "MIT" for entry in records)


def test_license_export_refuses_different_contents_at_existing_destination(tmp_path):
    collector = _script("collect-licenses.py")
    package = _distribution(tmp_path / "site", "example", {"licenses/LICENSE": b"original\n"})
    output = tmp_path / "export"
    collector.collect_licenses(output, [package])
    source = tmp_path / "site/example-1.0.dist-info/licenses/LICENSE"
    source.write_bytes(b"changed without a new destination\n")
    with pytest.raises(ValueError, match="overwrite"):
        collector.collect_licenses(output, [package])
    assert (output / "example/licenses/LICENSE").read_bytes() == b"original\n"


def test_native_inventory_matches_current_manifest_without_claiming_appimage_contains_ffmpeg(tmp_path):
    audit = _script("audit-dependencies.py")
    pins = audit.native_pins((ROOT / "packaging/flatpak/io.github.Corrgan.MultiChat.yml").read_text(encoding="utf-8"))
    assert {pin["name"] for pin in pins} >= {
        "luajit", "libass", "ffmpeg", "libplacebo", "hwdata", "libdisplay-info", "mpv", "deno",
    }
    assert len([pin for pin in pins if "commit" in pin]) == 5
    package = _distribution(tmp_path / "site", "example", {"LICENSE": b"license"})
    flatpak = audit.make_bom([package], pins, "2026-11-09T12:00:00Z", "flatpak")
    appimage = audit.make_bom([package], pins, "2026-11-09T12:00:00Z", "appimage")
    assert flatpak["bomFormat"] == "CycloneDX" and flatpak["specVersion"] == "1.6"
    assert any(component["name"] == "ffmpeg" for component in flatpak["components"])
    assert [component["name"] for component in appimage["components"]] == ["example"]
    assert appimage["components"][0]["purl"] == "pkg:pypi/example@1.0"
    assert "not fully" in flatpak["metadata"]["properties"][0]["value"]


def test_advisory_report_keeps_native_hits_visible_without_equating_them_with_python_hits(tmp_path):
    audit = _script("audit-dependencies.py")
    python = [{"name": "example", "version": "1.0"}]
    native = [{"name": "ffmpeg", "commit": "a" * 40}]
    queries = [{"package": {"ecosystem": "PyPI", "name": "example"}, "version": "1.0"}, {"commit": "a" * 40}]
    results = [{}, {"vulns": [{"id": "CVE-TEST-native"}]}]
    assert audit.write_reports(tmp_path, python, native, results, queries, "2026-11-09T12:00:00Z") is False
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "CVE-TEST-native" in summary
    assert "not a security clearance" in summary
    results[0] = {"vulns": [{"id": "PYSEC-TEST"}]}
    assert audit.write_reports(tmp_path, python, native, results, queries, "2026-11-09T12:00:00Z") is True


@pytest.mark.parametrize("response", [{"results": []}, {"results": [{"next_page_token": "more"}]}])
def test_incomplete_advisory_responses_fail_instead_of_reporting_clean(monkeypatch, response):
    audit = _script("audit-dependencies.py")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(response).encode()

    monkeypatch.setattr(audit.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="incomplete"):
        audit.query_osv([{"commit": "a" * 40}])


def test_failed_advisory_refresh_removes_previous_clean_report(tmp_path, monkeypatch):
    audit = _script("audit-dependencies.py")
    (tmp_path / "summary.md").write_text("old clean result", encoding="utf-8")
    (tmp_path / "osv.json").write_text("{}", encoding="utf-8")

    def offline(queries):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(audit, "query_osv", offline)
    monkeypatch.setattr(audit.sys, "argv", ["audit-dependencies.py", "--output", str(tmp_path)])
    with pytest.raises(RuntimeError, match="network unavailable"):
        audit.main()
    assert not (tmp_path / "summary.md").exists()
    assert not (tmp_path / "osv.json").exists()
    assert (tmp_path / "inventory.cdx.json").is_file()


@pytest.mark.parametrize("tag,expected", [
    # the project's release rule: vX.Y with Y a single digit or ending in 0
    ("v0.80", True), ("v0.90", True), ("v1.6", True), ("v2.0", True), ("v0.100", True),
    ("v0.81", False), ("v0.25", False), ("v0.80.1", False), ("v1.2.3", False),
    ("v0.80.1-rc1", False), ("v1", False), ("v1.2.3.4", False), ("v1.2;echo x", False),
])
def test_release_gate_follows_the_release_rule_and_rejects_unexpected_tags(tag, expected):
    source = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    expression = re.search(r'elif \[\[ "\$TAG" =~ (.+) \]\]; then', source).group(1)
    assert bool(re.fullmatch(expression, tag)) is expected
    assert "EXTRA_RELEASES: v0.25" in source  # the one listed exception

