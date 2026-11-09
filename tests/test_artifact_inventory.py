# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline evidence, shipped scope and fail-closed license/coverage gates."""
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


def distribution(site, name, license_file=True):
    metadata = site / f"{name}-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\nLicense-Expression: MIT\nLicense-File: licenses/LICENSE\n",
        encoding="utf-8",
    )
    module = site / name / "__init__.py"
    module.parent.mkdir()
    module.write_text("VALUE = 1\n", encoding="utf-8")
    paths = [metadata / "METADATA", module]
    if license_file:
        license_path = metadata / "licenses/LICENSE"
        license_path.parent.mkdir()
        license_path.write_text(f"License for {name}\n", encoding="utf-8")
        paths.append(license_path)
    with (metadata / "RECORD").open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([[path.relative_to(site).as_posix(), "", ""] for path in paths])
    return importlib.metadata.PathDistribution(metadata), module


def test_pyinstaller_license_inventory_excludes_unshipped_build_environment(tmp_path):
    collector = script("collect-licenses.py")
    used, module = distribution(tmp_path / "venv", "shipped")
    build_tool, _ = distribution(tmp_path / "venv", "buildtool")
    toc = tmp_path / "Analysis-00.toc"
    toc.write_text(repr([("shipped", str(module), "PYMODULE")]), encoding="utf-8")
    root = tmp_path / "AppDir"
    licenses = root / "share/licenses"
    collector.collect_licenses(licenses, [used, build_tool], collector.toc_inputs(toc))
    ledger = json.loads((licenses / "license-inventory.json").read_text(encoding="utf-8"))
    assert [package["name"] for package in ledger["components"]] == ["shipped"]
    assert ledger["components"][0]["evidence"][0]["kind"] == "PYMODULE"
    inventory = script("inventory-artifact.py").inventory(root, "appimage", tmp_path / "reports")
    assert [package["name"] for package in inventory["python_components"]] == ["shipped"]
    assert not inventory["coverage"]["errors"]
    assert "buildtool" not in json.dumps(inventory)


def test_missing_declared_wheel_license_cannot_produce_successful_export(tmp_path):
    collector = script("collect-licenses.py")
    package, _ = distribution(tmp_path / "site", "broken", license_file=False)
    with pytest.raises(ValueError, match="Declared license file missing"):
        collector.collect_licenses(tmp_path / "out", [package])


@pytest.mark.parametrize("layout", ["pep639", "legacy"])
def test_declared_license_is_found_in_own_metadata_without_record_entry(tmp_path, layout):
    package, module = distribution(tmp_path / "site", "example")
    metadata = Path(package._path)
    text = (metadata / "METADATA").read_text().replace("License-File: licenses/LICENSE", "License-File: LICENSE")
    (metadata / "METADATA").write_text(text)
    if layout == "legacy":
        (metadata / "licenses/LICENSE").rename(metadata / "LICENSE")
    with (metadata / "RECORD").open("w", newline="") as stream:
        csv.writer(stream).writerow([module.relative_to(metadata.parent).as_posix(), "", ""])
    records = script("collect-licenses.py").collect_licenses(tmp_path / "out", [package])
    assert len(records) == 1
    assert records[0]["source"] == str(Path(metadata.name) / ("licenses/LICENSE" if layout == "pep639" else "LICENSE"))
    assert (tmp_path / "out" / records[0]["path"]).read_text() == "License for example\n"


def test_vendored_license_cannot_satisfy_missing_own_declaration(tmp_path):
    package, _ = distribution(tmp_path / "site", "broken", license_file=False)
    metadata = Path(package._path)
    (metadata / "METADATA").write_text((metadata / "METADATA").read_text().replace("licenses/LICENSE", "LICENSE"))
    vendor = metadata.parent / "broken/_vendor/other-1.0.dist-info/LICENSE"
    vendor.parent.mkdir(parents=True)
    vendor.write_text("A different package's license\n")
    with (metadata / "RECORD").open("a", newline="") as stream:
        csv.writer(stream).writerow([vendor.relative_to(metadata.parent).as_posix(), "", ""])
    with pytest.raises(ValueError, match="Declared license file missing"):
        script("collect-licenses.py").collect_licenses(tmp_path / "out", [package])


@pytest.mark.parametrize("metadata_location", [None, "project"])
def test_missing_metadata_root_cannot_use_project_working_directory_license(tmp_path, monkeypatch, metadata_location):
    package, _ = distribution(tmp_path / "site", "broken", license_file=False)
    metadata = Path(package._path)
    (metadata / "METADATA").write_text((metadata / "METADATA").read_text().replace("licenses/LICENSE", "LICENSE"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "LICENSE").write_text("The application's license, not broken's license\n")
    monkeypatch.chdir(project)

    class NoOwnMetadata:
        metadata = package.metadata
        version = package.version
        files = []

    missing_metadata = NoOwnMetadata()
    if metadata_location is not None:
        missing_metadata._path = project
    with pytest.raises(ValueError, match="Declared license file missing"):
        script("collect-licenses.py").collect_licenses(tmp_path / "out", [missing_metadata])


def relocated_fixture(tmp_path):
    root = tmp_path / "app"
    package, _ = distribution(root / "lib/python3.13/site-packages", "PyQt6_sip", license_file=False)
    metadata = Path(package._path)
    (metadata / "METADATA").write_text((metadata / "METADATA").read_text().replace("licenses/LICENSE", "LICENSE"))
    legacy = metadata.with_name("PyQt6_sip-1.0-py3.13.egg-info")
    metadata.rename(legacy)
    (legacy / "METADATA").rename(legacy / "PKG-INFO")
    (legacy / "RECORD").unlink()
    package = importlib.metadata.PathDistribution(legacy)
    source = "share/licenses/com.riverbankcomputing.PyQt.BaseApp/pyqt-sip/LICENSE"
    original = root / source
    original.parent.mkdir(parents=True)
    original.write_bytes(b"An original BSD-2-Clause license fixture\n")
    mapping = {"schema": 1, "mappings": [{"name": "PyQt6-sip", "version": "1.0", "license_file": "LICENSE",
                                          "source": source, "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}]}
    return root, package, original, mapping


def test_exact_version_and_hash_bound_baseapp_license_layout_is_exported(tmp_path):
    root, package, original, mapping = relocated_fixture(tmp_path)
    output = tmp_path / "out"
    records = script("collect-licenses.py").collect_licenses(output, [package], relocated=mapping, artifact_root=root)
    assert len(records) == 1
    assert records[0]["path"] == "PyQt6_sip/licenses/LICENSE"
    assert records[0]["source"] == str(original)
    assert records[0]["relocated_license"] == mapping["mappings"][0]
    assert (output / records[0]["path"]).read_bytes() == original.read_bytes()


def test_legacy_baseapp_egg_metadata_is_an_actual_shipped_component(tmp_path):
    root, package, _, mapping = relocated_fixture(tmp_path)
    script("collect-licenses.py").collect_licenses(root / "share/licenses/python", [package],
                                                  relocated=mapping, artifact_root=root)
    data = script("inventory-artifact.py").inventory(root, "flatpak", tmp_path / "report")
    assert len(data["python_components"]) == 1
    component = data["python_components"][0]
    assert component["name"] == "PyQt6_sip"
    assert component["version"] == "1.0"
    assert component["metadata_path"].endswith(".egg-info/PKG-INFO")
    assert component["metadata_sha256"]
    assert component["artifact_files"] == []  # Absent RECORD does not establish file ownership.
    assert not data["coverage"]["errors"]


@pytest.mark.parametrize("change", ["version", "hash", "escape"])
def test_relocated_license_cannot_bypass_version_hash_or_root_binding(tmp_path, change):
    root, package, original, mapping = relocated_fixture(tmp_path)
    if change == "version":
        mapping["mappings"][0]["version"] = "2.0"
        expected = "Declared license file missing"
    elif change == "hash":
        original.write_bytes(b"A different license text\n")
        expected = "hash mismatch"
    else:
        mapping["mappings"][0]["source"] = "../outside/LICENSE"
        expected = "Unsafe license path"
    with pytest.raises(ValueError, match=expected):
        script("collect-licenses.py").collect_licenses(tmp_path / "out", [package], relocated=mapping, artifact_root=root)


def test_missing_declared_licenses_are_reported_together(tmp_path):
    first, _ = distribution(tmp_path / "site", "first", license_file=False)
    second, _ = distribution(tmp_path / "site", "second", license_file=False)
    with pytest.raises(ValueError) as caught:
        script("collect-licenses.py").collect_licenses(tmp_path / "out", [first, second])
    assert "first==1.0/licenses/LICENSE" in str(caught.value)
    assert "second==1.0/licenses/LICENSE" in str(caught.value)


def test_vendored_metadata_licenses_keep_their_full_package_identity(tmp_path):
    collector = script("collect-licenses.py")
    site = tmp_path / "site"
    package, module = distribution(site, "bundle")
    vendors = {
        "bundle/_vendor/first-1.0.dist-info/LICENSE": b"first nested license",
        "bundle/_vendor/second-2.0.dist-info/LICENSE": b"different nested license",
        "bundle/COPYING.txt": b"license stored alongside package code, outside dist-info",
    }
    record = site / "bundle-1.0.dist-info/RECORD"
    with record.open("a", encoding="utf-8", newline="") as stream:
        for relative, content in vendors.items():
            destination = site / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            csv.writer(stream).writerow([relative, "", ""])
    toc = tmp_path / "Analysis-00.toc"
    toc.write_text(repr([("bundle", str(module), "PYMODULE")]), encoding="utf-8")
    output = tmp_path / "licenses"
    records = collector.collect_licenses(output, [package], collector.toc_inputs(toc))
    assert len(records) == 4
    for relative, content in vendors.items():
        prefix = "vendored" if ".dist-info/" in relative else "package-files"
        assert (output / "bundle" / prefix / relative).read_bytes() == content
    assert (output / "bundle/licenses/LICENSE").read_text() == "License for bundle\n"


@pytest.mark.parametrize("change", ["delete", "tamper"])
def test_final_artifact_license_export_is_verified_and_gate_fails(tmp_path, monkeypatch, change):
    collector = script("collect-licenses.py")
    package, _ = distribution(tmp_path / "site", "example")
    root = tmp_path / "AppDir"
    collector.collect_licenses(root / "licenses", [package])
    exported = root / "licenses/example/licenses/LICENSE"
    if change == "delete":
        exported.unlink()
    else:
        exported.write_bytes(b"different license")
    scanner = script("inventory-artifact.py")
    output = tmp_path / "report"
    monkeypatch.setattr(scanner.sys, "argv", ["inventory-artifact.py", "--root", str(root), "--kind", "appimage", "--output", str(output)])
    assert scanner.main() == 1
    data = json.loads((output / "inventory.json").read_text(encoding="utf-8"))
    assert any(f"{'missing' if change == 'delete' else 'changed'}-license-export:" in error for error in data["coverage"]["errors"])


def test_license_ledger_path_cannot_escape_its_export_scope(tmp_path):
    root = tmp_path / "AppDir"
    licenses = root / "licenses"
    licenses.mkdir(parents=True)
    (licenses / "license-inventory.json").write_text(json.dumps({"files": [{"path": "../../outside", "sha256": "0" * 64}]}))
    with pytest.raises(ValueError, match="escapes inventory scope"):
        script("inventory-artifact.py").inventory(root, "appimage", tmp_path / "report")


def test_actual_shipped_metadata_is_used_instead_of_installed_environment(tmp_path):
    root = tmp_path / "Flatpak"
    distribution(root / "lib/python3.12/site-packages", "actually_shipped")
    data = script("inventory-artifact.py").inventory(root, "flatpak", tmp_path / "report")
    assert [item["name"] for item in data["python_components"]] == ["actually_shipped"]
    assert not data["coverage"]["errors"]
    assert data["python_components"][0]["metadata_sha256"]


def test_elf_graph_runtime_versions_and_unknown_composition_stay_distinct(tmp_path, monkeypatch):
    scanner = script("inventory-artifact.py")
    root = tmp_path / "AppDir"
    root.mkdir()
    first, second = root / "module.so", root / "libshared.so.1"
    first.write_bytes(b"\x7fELFsynthetic-python-extension")
    second.write_bytes(b"\x7fELFsynthetic-shared-library")

    def readelf(args):
        text = (" 0 (NEEDED) Shared library: [libshared.so.1]\n Build ID: aaaa\n" if args[-1].endswith("module.so") else
                " 0 (SONAME) Library soname: [libshared.so.1]\n 0 (NEEDED) Shared library: [libc.so.6]\n Build ID: bbbb\n")
        return {"returncode": 0, "stdout": text, "stderr": "", "argv": args}

    monkeypatch.setattr(scanner, "command", readelf)
    monkeypatch.setattr(scanner.shutil, "which", lambda _name: None)
    runtime = {"components": [{"name": "lxml", "versions": {"libxml2": "2.15.4"},
                                "files": [{"path": "/actual-run/module.so", "sha256": scanner.digest(first)}]}]}
    data = scanner.inventory(root, "appimage", tmp_path / "report", runtime)
    assert data["runtime_components"][0]["evidence_scope"] == "hash-matched-artifact"
    assert {edge["needed"] for edge in data["native_dependencies"]} == {"libshared.so.1", "libc.so.6"}
    shared = next(edge for edge in data["native_dependencies"] if edge["needed"] == "libshared.so.1")
    assert shared["artifact_candidates"] == ["libshared.so.1"]
    external = next(edge for edge in data["native_dependencies"] if edge["needed"] == "libc.so.6")
    assert external["resolution"] == "external runtime/host requirement"
    assert not data["coverage"]["complete"]
    assert "static-and-vendored-native-composition-not-fully-attested" in data["coverage"]["gaps"]
    bom = scanner.cyclonedx(data)
    assert {component["name"] for component in bom["components"]} == {"module.so", "libshared.so.1", "libxml2"}
    assert bom["dependencies"] == [{"ref": "file:libshared.so.1", "dependsOn": []},
                                   {"ref": "file:module.so", "dependsOn": ["file:libshared.so.1"]}]


def test_runtime_version_without_artifact_hash_match_is_not_called_shipped(tmp_path):
    root = tmp_path / "AppDir"
    root.mkdir()
    runtime = {"components": [{"name": "crypto", "versions": {"openssl": "99.0"},
                                "files": [{"path": "/build/venv/crypto.so", "sha256": "a" * 64}]}]}
    data = script("inventory-artifact.py").inventory(root, "appimage", tmp_path / "out", runtime)
    assert data["runtime_components"][0]["evidence_scope"] == "external-or-unmatched"
    assert "runtime-version-not-bound-to-artifact:crypto" in data["coverage"]["gaps"]


def test_require_complete_rejects_a_partial_inventory(tmp_path, monkeypatch):
    root = tmp_path / "AppDir"
    root.mkdir()
    scanner = script("inventory-artifact.py")
    monkeypatch.setattr(scanner.sys, "argv", ["inventory-artifact.py", "--root", str(root), "--kind", "appimage",
                                              "--output", str(tmp_path / "report"), "--require-complete"])
    assert scanner.main() == 1
    summary = json.loads((tmp_path / "report/inventory-summary.json").read_text())
    assert summary["coverage"]["complete"] is False


def test_native_provenance_cannot_hide_an_older_library_selected_at_runtime(tmp_path):
    collector, scanner = script("collect-licenses.py"), script("inventory-artifact.py")
    site = tmp_path / "site"
    package, _ = distribution(site, "lxml")
    provenance = site / "lxml-1.0.dist-info/multichat-native-build.json"
    provenance.write_text(json.dumps({"runtime": {"libxml2": [2, 15, 4], "libxslt": [1, 1, 45]}}))
    with (site / "lxml-1.0.dist-info/RECORD").open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow([provenance.relative_to(site).as_posix(), "", ""])
    root = tmp_path / "AppDir"
    collector.collect_licenses(root / "licenses", [package])
    extension = root / "etree.so"
    extension.write_bytes(b"fixture native extension")
    runtime = {"components": [{"name": "lxml", "versions": {"libxml2": "2.9.14", "libxslt": "1.1.45"},
                                "files": [{"path": "/actual/runtime/etree.so", "sha256": scanner.digest(extension)}]}]}
    data = scanner.inventory(root, "appimage", tmp_path / "report", runtime)
    assert "native-runtime-mismatch:lxml:libxml2:built=2.15.4:loaded=2.9.14" in data["coverage"]["errors"]


def test_component_probe_loads_video_before_measuring_xml(monkeypatch, tmp_path):
    import sys
    import types
    from multichat.core import component_probe
    from multichat.video import player

    loaded = []
    module_file = tmp_path / "fixture-extension"
    module_file.write_bytes(b"fixture")

    class Etree:
        __file__ = str(module_file)

        def __getattr__(self, name):
            assert loaded == ["mpv"], "static XML was measured before host video libraries loaded"
            return {"LXML_VERSION": (6, 1, 3), "LIBXML_VERSION": (2, 15, 4), "LIBXSLT_VERSION": (1, 1, 45)}[name]

    monkeypatch.setattr(player, "load_mpv", lambda: loaded.append("mpv") or True)
    monkeypatch.setattr(component_probe.ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setitem(sys.modules, "lxml", types.SimpleNamespace(etree=Etree()))
    monkeypatch.setitem(sys.modules, "cryptography", None)
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    result = component_probe.collect()
    xml = next(item for item in result["components"] if item["name"] == "lxml")
    assert xml["versions"]["libxml2"] == "2.15.4"


def test_component_probe_cli_does_not_open_config_keyring_or_gui(monkeypatch, capsys):
    from multichat import app
    from multichat.core import component_probe

    def forbidden(*args, **kwargs):
        raise AssertionError("component diagnostics must not initialize GUI/config")

    monkeypatch.setattr(app, "load_config", forbidden)
    monkeypatch.setattr(app, "QApplication", forbidden)
    monkeypatch.setattr(component_probe, "collect", lambda: {"schema": 1, "components": []})
    monkeypatch.setattr(app.sys, "argv", ["multichat", "--component-probe"])
    app.main()
    assert json.loads(capsys.readouterr().out) == {"schema": 1, "components": []}


def test_native_license_checksum_gate_rejects_changed_export(tmp_path):
    scanner = script("inventory-artifact.py")
    root = tmp_path / "app"
    licenses = root / "share/licenses/multichat-native/example"
    licenses.mkdir(parents=True)
    text = licenses / "LICENSE"
    text.write_bytes(b"verified original license")
    (licenses / "SHA256SUMS").write_text(f"{scanner.digest(text)}  ./LICENSE\n", encoding="utf-8")
    text.write_bytes(b"truncated or replaced license")
    data = scanner.inventory(root, "flatpak", tmp_path / "report")
    assert any(error.startswith("missing-or-changed-native-license:") for error in data["coverage"]["errors"])


def test_published_artifact_queries_include_measured_vendored_versions_without_build_venv(tmp_path):
    audit = script("audit-dependencies.py")
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({
        "schema": 1, "files": [], "coverage": {"complete": False},
        "python_components": [{"name": "lxml", "version": "6.1.3"}],
        "native_source_inputs": [{"name": "ffmpeg", "commit": "a" * 40}],
        "runtime_components": [
            {"name": "lxml", "evidence_scope": "hash-matched-artifact", "versions": {"libxml2": "2.15.4", "lxml": "6.1.3"}},
            {"name": "build-only-crypto", "evidence_scope": "external-or-unmatched", "versions": {"openssl": "99.0.0"}},
        ],
    }), encoding="utf-8")
    python, native, measured, skipped = audit.artifact_queries([path])
    assert python == [{"name": "lxml", "version": "6.1.3"}]
    assert native == [{"name": "ffmpeg", "commit": "a" * 40}]
    assert [(item["name"], item["version"]) for item in measured] == [("libxml2", "2.15.4")]
    assert any("build-only-crypto" in item for item in skipped)
    assert any("no configured native" in item for item in skipped)


def test_native_source_urls_and_hashes_are_paired_per_source_not_per_module():
    audit = script("audit-dependencies.py")
    pins = audit.native_pins("""modules:
  - name: ffmpeg
    sources:
      - type: git
        url: https://example.invalid/ffmpeg.git
        commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      - type: file
        url: https://example.invalid/fix.patch
        sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""")
    assert len(pins) == 2
    assert pins[0]["commit"] == "a" * 40 and "sha256" not in pins[0]
    assert pins[1]["sha256"] == "b" * 64 and "commit" not in pins[1]
