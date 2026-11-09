# SPDX-License-Identifier: GPL-3.0-or-later
"""Native source integrity and optional real compiler/runtime regressions."""
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper():
    spec = importlib.util.spec_from_file_location("secure_lxml", ROOT / "packaging/build-secure-lxml.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_five_ffmpeg_advisories_are_applied_by_manifest_with_verified_patch_bytes():
    directory = ROOT / "packaging/patches/ffmpeg"
    plan = json.loads((directory / "backports.json").read_text(encoding="utf-8"))
    manifest = (ROOT / "packaging/flatpak/io.github.Corrgan.MultiChat.yml").read_text(encoding="utf-8")
    assert {patch["cve"] for patch in plan["patches"]} == {
        "CVE-2026-107675", "CVE-2026-107676", "CVE-2026-107677", "CVE-2026-107678", "CVE-2026-107696",
    }
    for patch in plan["patches"]:
        contents = (directory / patch["path"]).read_bytes()
        assert hashlib.sha256(contents).hexdigest() == patch["sha256"]
        assert patch["upstream_commit"].encode() in contents
        assert f"path: ../patches/ffmpeg/{patch['path']}" in manifest
    assert "python3 test-native-ffmpeg.py" in manifest
    assert "--disable-libssh" in manifest


def test_flatpak_installs_corrected_lxml_before_dependencies_and_does_not_offer_old_wheels():
    manifest = (ROOT / "packaging/flatpak/io.github.Corrgan.MultiChat.yml").read_text(encoding="utf-8")
    assert manifest.index("- name: secure-lxml") < manifest.index("- python-deps.json")
    sources = json.loads((ROOT / "packaging/flatpak/python-deps.json").read_text(encoding="utf-8"))["sources"]
    lxml = [source for source in sources if source["url"].rsplit("/", 1)[-1].startswith("lxml-")]
    assert len(lxml) == 1 and lxml[0]["url"].endswith(".tar.gz")
    native = json.loads((ROOT / "packaging/native-sources.json").read_text(encoding="utf-8"))["sources"]
    pinned_lxml = next(pin for pin in native if pin["name"] == "lxml")
    assert lxml[0]["sha256"] == pinned_lxml["sha256"]
    for pin in native:
        assert pin["url"] in manifest and pin["sha256"] in manifest


def test_corrupt_offline_source_is_rejected_without_any_fallback(tmp_path, monkeypatch):
    module = helper()
    pin = {"filename": "source.tar.xz", "sha256": "0" * 64, "url": "https://example.invalid/"}
    (tmp_path / pin["filename"]).write_bytes(b"corrupt")
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: pytest.fail("No network fallback is allowed"))
    with pytest.raises(ValueError, match="corrupt offline source"):
        module.source_archive(pin, tmp_path, offline=True)


def test_source_archive_cannot_write_outside_its_build_directory(tmp_path):
    module = helper()
    archive = tmp_path / "malicious.tar"
    with tarfile.open(archive, "w") as output:
        member = tarfile.TarInfo("../escape")
        member.size = 4
        output.addfile(member, io.BytesIO(b"oops"))
    with pytest.raises(ValueError):
        module.unpack(archive, tmp_path / "build")
    assert not (tmp_path / "escape").exists()


def test_native_wheel_records_licenses_hashes_and_original_read_permissions(tmp_path):
    module = helper()
    wheel = tmp_path / "lxml-6.1.3-test.whl"
    metadata = "lxml-6.1.3.dist-info"
    with zipfile.ZipFile(wheel, "w") as output:
        entry = zipfile.ZipInfo("lxml/etree.so")
        entry.external_attr = 0o100755 << 16
        output.writestr(entry, b"fake native bytes")
        output.writestr(f"{metadata}/METADATA", b"Name: lxml\nVersion: 6.1.3\n")
        output.writestr(f"{metadata}/RECORD", b"")
    sources = {}
    for name in ("libxml2", "libxslt"):
        source = tmp_path / name
        source.mkdir()
        (source / "COPYING").write_text(f"{name} copyright and license\n", encoding="utf-8")
        sources[name] = source
    module.augment_wheel(wheel, sources, [{"name": "test", "sha256": "a" * 64}], {"libxml2": [2, 15, 4]})
    with zipfile.ZipFile(wheel) as output:
        assert (output.getinfo("lxml/etree.so").external_attr >> 16) & 0o777 == 0o755
        assert (output.getinfo(f"{metadata}/multichat-native-build.json").external_attr >> 16) & 0o777 == 0o644
        records = list(csv.reader(io.StringIO(output.read(f"{metadata}/RECORD").decode())))
        for name, checksum, size in records:
            if checksum:
                contents = output.read(name)
                assert checksum == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).decode().rstrip("=")
                assert int(size) == len(contents)
        assert f"{metadata}/licenses/multichat-native/libxml2/COPYING" in output.namelist()
        assert f"{metadata}/licenses/multichat-native/libxslt/COPYING" in output.namelist()


def test_cache_rejects_old_builder_or_changed_source_plan(tmp_path):
    module = helper()
    (tmp_path / "native-build.json").write_text(json.dumps({
        "wheel": "lxml-test.whl", "sha256": "a" * 64, "sources": [], "builder_sha256": "b" * 64,
    }), encoding="utf-8")
    assert module.verified_cache(tmp_path, tmp_path, []) is None


@pytest.mark.skipif(not sys.platform.startswith("linux") or sys.version_info[:2] != (3, 12)
                    or not os.environ.get("MULTICHAT_OLD_LXML_WHEEL"),
                    reason="requires the hash-verified old CPython 3.12 Linux wheel as a negative control")
def test_hardened_native_guard_rejects_the_actual_old_linux_wheel(tmp_path):
    module = helper()
    wheel = Path(os.environ["MULTICHAT_OLD_LXML_WHEEL"])
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == "379f8a75cf6eb7eef0af074b55f49ab73b868388a98de14646abcdfa4564bb11"
    with zipfile.ZipFile(wheel) as bundle:
        bundle.extractall(tmp_path)
    with pytest.raises(subprocess.CalledProcessError) as error:
        module.native_version_check(tmp_path)
    assert "(2, 14, 6)" in error.value.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux") or not os.environ.get("MULTICHAT_REQUIRE_SECURE_LXML"),
                    reason="requires the explicitly hardened Linux release environment")
def test_hardened_runtime_has_corrected_native_versions_and_preserves_parser_flags(tmp_path):
    from lxml import etree

    assert etree.LIBXML_VERSION >= (2, 15, 4)
    assert etree.LIBXSLT_VERSION >= (1, 1, 45)
    parser = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=True)
    document = etree.fromstring(b'<!DOCTYPE root [<!ENTITY e SYSTEM "http://127.0.0.1:9/no-network">]><root>&e;</root>',
                                parser)
    assert document.text is None
    local = tmp_path / "include.xml"
    local.write_text("<included/>", encoding="utf-8")
    root = etree.XML(f'<root xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="{local.as_uri()}"/></root>',
                     parser)
    tree = etree.ElementTree(root)
    tree.xinclude()
    assert root[0].tag == "included"
    # The standalone guard must also test the GUI's load order: mpv may have
    # already exported an older host XML library through RTLD_GLOBAL.
    versions = helper().native_version_check(Path(etree.__file__).parents[1])
    assert versions["libxml2"] >= [2, 15, 4]
    assert versions["libxslt"] >= [1, 1, 45]
    assert versions["host_preload"]  # Linux video test prerequisites include host libxml2


@pytest.mark.skipif(not sys.platform.startswith("linux") or not os.environ.get("MULTICHAT_NATIVE_TEST_ROOT"),
                    reason="requires real old/fixed FFmpeg builds")
def test_real_old_and_fixed_component_behaviors():
    spec = importlib.util.spec_from_file_location("native_ffmpeg", ROOT / "packaging/tests/test-native-ffmpeg.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    root = Path(os.environ["MULTICHAT_NATIVE_TEST_ROOT"])
    old = native.exercise(root / "ffmpeg-old", root / "ffmpeg-old-build", expect_fixed=False)
    fixed = native.exercise(root / "ffmpeg-fixed", root / "ffmpeg-fixed-build")
    assert old["empty-replace"]["timeout"]
    assert old["deep-free"]["returncode"] != 0
    assert old["hdr-initialized"]["returncode"] != 0
    assert old["rtsp"]["timeout"] and old["rtsp"]["requests"] > 9
    assert old["pssh"]["timeout"]
    assert fixed["pssh"]["returncode"] == 0

