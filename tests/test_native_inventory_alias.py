# SPDX-License-Identifier: GPL-3.0-or-later
"""secure-lxml licenses must belong to the actual shipped, matching lxml wheel."""
import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_record(metadata):
    with (metadata / "RECORD").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        for path in sorted(metadata.rglob("*")):
            if not path.is_file() or path.name == "RECORD":
                continue
            data = path.read_bytes()
            checksum = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
            writer.writerow([path.relative_to(metadata.parent).as_posix(), "sha256=" + checksum, len(data)])


@pytest.fixture
def shipped_lxml(tmp_path):
    root = tmp_path / "app"
    metadata = root / "lib/python3.13/site-packages/lxml-6.1.3.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.4\nName: lxml\nVersion: 6.1.3\nLicense: BSD-3-Clause\n")
    (metadata / "LICENSE.txt").write_bytes(b"Original lxml license fixture\n")
    for library in ("libxml2", "libxslt"):
        license_path = metadata / "licenses/multichat-native" / library / "Copyright"
        license_path.parent.mkdir(parents=True)
        license_path.write_bytes(f"Original {library} copyright and license fixture\n".encode())
    sources = json.loads((ROOT / "packaging/native-sources.json").read_text(encoding="utf-8"))["sources"]
    document = {"sources": sources,
                "builder_sha256": hashlib.sha256((ROOT / "packaging/build-secure-lxml.py").read_bytes()).hexdigest(),
                "runtime": {"libxml2": [2, 15, 4], "libxslt": [1, 1, 45],
                            "elf_sha256": "0" * 64}}  # Pre-strip ELF hashes do not establish license ownership.
    provenance = metadata / "multichat-native-build.json"
    provenance.write_text(json.dumps(document), encoding="utf-8")
    write_record(metadata)
    manifest = tmp_path / "manifest.yml"
    text = "modules:\n  - name: secure-lxml\n    sources:\n      - type: file\n        path: ../build-secure-lxml.py\n"
    for source in sources:
        text += f"      - type: file\n        url: {source['url']}\n        sha256: {source['sha256']}\n"
    manifest.write_text(text, encoding="utf-8")
    return root, metadata, provenance, document, manifest


def exported_inventory(tmp_path, root, metadata, manifest):
    package = importlib.metadata.PathDistribution(metadata)
    output = root / "share/licenses/python"
    script("collect-licenses.py").collect_licenses(output, [package])
    runtime = {"probe_policy": "host-video-preloaded-v1", "video_loaded": True, "components": []}
    return script("inventory-artifact.py").inventory(root, "flatpak", tmp_path / "report", runtime, manifest)


def test_alias_uses_real_wheel_record_provenance_exact_pins_and_native_license_exports(tmp_path, shipped_lxml):
    root, metadata, provenance, document, manifest = shipped_lxml
    data = exported_inventory(tmp_path, root, metadata, manifest)
    assert not data["coverage"]["errors"]
    alias, = data["native_module_aliases"]
    assert alias["module"] == "secure-lxml" and alias["package"] == "lxml" and alias["version"] == "6.1.3"
    assert alias["source_pins"] == document["sources"]
    assert alias["provenance_path"] == provenance.relative_to(root).as_posix()
    assert alias["provenance_sha256"] == hashlib.sha256(provenance.read_bytes()).hexdigest()
    assert {item["library"] for item in alias["licenses"]} == {"libxml2", "libxslt"}
    for item in alias["licenses"]:
        assert hashlib.sha256((root / item["source"]).read_bytes()).hexdigest() == item["sha256"]
        assert (root / item["source"]).read_bytes() == (root / item["export"]).read_bytes()


@pytest.mark.parametrize("change", ["source_pin", "builder", "unrecorded_provenance", "missing_provenance", "license_record", "notice_only"])
def test_alias_rejects_unbound_pins_metadata_or_primary_licenses(tmp_path, shipped_lxml, change):
    root, metadata, provenance, document, manifest = shipped_lxml
    if change in {"source_pin", "builder"}:
        if change == "source_pin":
            document["sources"][0]["sha256"] = "0" * 64
        else:
            document["builder_sha256"] = "0" * 64
        provenance.write_text(json.dumps(document), encoding="utf-8")
        write_record(metadata)  # Valid wheel hashes cannot replace matching build source evidence.
    elif change == "unrecorded_provenance":
        provenance.write_text(json.dumps(document) + "\n", encoding="utf-8")
    elif change == "missing_provenance":
        provenance.unlink()
    elif change == "license_record":
        path = metadata / "licenses/multichat-native/libxslt/Copyright"
        path.write_bytes(b"Changed after the wheel RECORD was produced\n")
    else:
        for library in ("libxml2", "libxslt"):
            directory = metadata / "licenses/multichat-native" / library
            (directory / "Copyright").unlink()
            (directory / "NOTICE").write_bytes(b"A nested component notice is not the library's primary license\n")
        write_record(metadata)
    data = exported_inventory(tmp_path, root, metadata, manifest)
    assert "native-source-license-export-missing:secure-lxml" in data["coverage"]["errors"]
    assert data["native_module_aliases"] == []


def test_alias_rejects_a_changed_export_even_when_original_wheel_text_is_intact(tmp_path, shipped_lxml):
    root, metadata, _provenance, _document, manifest = shipped_lxml
    exported_inventory(tmp_path, root, metadata, manifest)
    (root / "share/licenses/python/lxml/licenses/multichat-native/libxslt/Copyright").write_bytes(b"changed export\n")
    runtime = {"probe_policy": "host-video-preloaded-v1", "video_loaded": True, "components": []}
    data = script("inventory-artifact.py").inventory(root, "flatpak", tmp_path / "second-report", runtime, manifest)
    assert "native-source-license-export-missing:secure-lxml" in data["coverage"]["errors"]
    assert any(error.startswith("changed-license-export:") for error in data["coverage"]["errors"])
    assert data["native_module_aliases"] == []
