# SPDX-License-Identifier: GPL-3.0-or-later
"""Original source license headers require reviewed bytes and real package ownership."""
import base64
import copy
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Original PLY 3.11 lex.py lines 1-32, including its exact comment delimiters.
HEADER = b'''# -----------------------------------------------------------------------------
# ply: lex.py
#
# Copyright (C) 2001-2018
# David M. Beazley (Dabeaz LLC)
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# * Redistributions of source code must retain the above copyright notice,
#   this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# * Neither the name of the David Beazley or Dabeaz LLC may be used to
#   endorse or promote products derived from this software without
#  specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# -----------------------------------------------------------------------------
'''


def collector():
    spec = importlib.util.spec_from_file_location("source_license_collector", ROOT / "packaging/collect-licenses.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(metadata, sources, *, omit=None, bad_hash=None):
    with (metadata / "RECORD").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        for relative, path in sources.items():
            if relative == omit:
                continue
            digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest()).decode().rstrip("=")
            writer.writerow([relative, "sha256=" + ("incorrect" if relative == bad_hash else digest), path.stat().st_size])


@pytest.fixture
def source_package(tmp_path):
    root = tmp_path / "app"
    site = root / "lib/python3.13/site-packages"
    metadata = site / "ply-3.11.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.4\nName: ply\nVersion: 3.11\nLicense: BSD\n", encoding="utf-8")
    mappings = json.loads((ROOT / "packaging/flatpak/baseapp-licenses.json").read_text(encoding="utf-8"))
    mappings = {"schema": 1, "mappings": [], "source_headers": copy.deepcopy(mappings["source_headers"])}
    sources = {}
    headers = {}
    for item in mappings["source_headers"]:
        relative = item["source"]
        header = HEADER.replace(b"ply: lex.py", b"ply: " + Path(relative).name.encode())
        assert len(header.splitlines()) == 32
        assert hashlib.sha256(header).hexdigest() == item["header_sha256"]
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header + b"\n__version__ = '3.11'\n")
        item["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        sources[relative], headers[relative] = path, header
    record(metadata, sources)
    return root, importlib.metadata.PathDistribution(metadata), mappings, sources, headers


def test_source_exports_are_exact_original_header_bytes_with_provenance(tmp_path, source_package):
    root, package, mappings, sources, headers = source_package
    output = tmp_path / "licenses"
    exported = collector().collect_licenses(output, [package], relocated=mappings, artifact_root=root)
    assert len(exported) == 2
    for entry in exported:
        source = entry["source"]
        assert entry["kind"] == "license" and entry["package"] == "ply" and entry["version"] == "3.11"
        assert entry["path"] == f"ply/source-headers/{source}.LICENSE"
        assert (output / entry["path"]).read_bytes() == headers[source]
        provenance = entry["source_license_header"]
        assert provenance["source"] == source and provenance["lines"] == [1, 32]
        assert provenance["source_sha256"] == hashlib.sha256(sources[source].read_bytes()).hexdigest()
        assert entry["sha256"] == provenance["header_sha256"]
    ledger = json.loads((output / "license-inventory.json").read_text(encoding="utf-8"))
    assert ledger["files"] == exported
    assert set(ledger["components"][0]["licenses"]) == {entry["path"] for entry in exported}


@pytest.mark.parametrize("change, expected", [
    ("version", "version mismatch"),
    ("source", "full-file hash mismatch"),
    ("header", "header hash mismatch"),
    ("ownership", "not RECORD-owned"),
    ("record_hash", "RECORD hash mismatch"),
    ("line_range", "Invalid source license line range"),
    ("artifact_root", "escapes or lacks artifact root"),
])
def test_source_export_rejects_changed_inputs_and_missing_ownership(tmp_path, source_package, change, expected):
    root, package, mappings, sources, _headers = source_package
    source = sources["ply/lex.py"]
    metadata = Path(package._path)
    if change == "version":
        path = metadata / "METADATA"
        path.write_text(path.read_text().replace("Version: 3.11", "Version: 3.12"))
    elif change == "source":
        source.write_bytes(source.read_bytes() + b"# changed implementation\n")
    elif change == "header":
        source.write_bytes(source.read_bytes().replace(b"All rights reserved.", b"All rights changed."))
        # Even a newly matched source pin and RECORD must retain the reviewed header.
        mappings["source_headers"][0]["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        record(metadata, sources)
    elif change == "ownership":
        record(metadata, sources, omit="ply/lex.py")  # The real file still exists; no glob fallback is allowed.
    elif change == "record_hash":
        record(metadata, sources, bad_hash="ply/lex.py")
    elif change == "line_range":
        mappings["source_headers"][0]["lines"] = [0, 32]
    elif change == "artifact_root":
        root = tmp_path / "unrelated-artifact"
    with pytest.raises(ValueError, match=expected):
        collector().collect_licenses(tmp_path / "licenses", [package], relocated=mappings, artifact_root=root)


def test_source_header_export_does_not_satisfy_a_genuinely_missing_declared_license(tmp_path, source_package):
    root, package, mappings, _sources, _headers = source_package
    metadata = Path(package._path) / "METADATA"
    metadata.write_text(metadata.read_text() + "License-File: LICENSE\n")
    output = tmp_path / "licenses"
    with pytest.raises(ValueError, match="Declared license file missing"):
        collector().collect_licenses(output, [package], relocated=mappings, artifact_root=root)
    assert not (output / "license-inventory.json").exists()


def test_native_copyright_files_are_exported_without_renaming_or_changing_bytes(tmp_path):
    site = tmp_path / "site"
    metadata = site / "lxml-6.1.3.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.4\nName: lxml\nVersion: 6.1.3\nLicense: BSD\n")
    sources = {}
    for library, filename in (("libxml2", "Copyright"), ("libxslt", "copyright.html")):
        source = metadata / "licenses/multichat-native" / library / filename
        source.parent.mkdir(parents=True)
        source.write_bytes(f"Original {library} copyright and license text\n".encode())
        sources[source.relative_to(site).as_posix()] = source
    record(metadata, sources)
    package = importlib.metadata.PathDistribution(metadata)
    output = tmp_path / "licenses"
    exported = collector().collect_licenses(output, [package])
    assert len(exported) == 2
    for entry in exported:
        source = site / entry["source"]
        assert entry["kind"] == "license" and (output / entry["path"]).read_bytes() == source.read_bytes()
