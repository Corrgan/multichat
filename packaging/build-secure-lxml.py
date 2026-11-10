#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a Linux lxml wheel against pinned, corrected static libxml2/libxslt.

No implicit static-deps download or fallback to an upstream binary wheel is used.
The caller supplies an environment with setuptools' bdist_wheel command, a C
compiler, make, pkg-config, Python headers, binutils and zlib development files.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LICENSE_FILE = re.compile(r"^(?:LICEN[CS]E|COPYING|COPYRIGHT|NOTICE)", re.I)
MAX_ARCHIVE = 64 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_archive(pin: dict, cache: Path, offline: bool = False) -> Path:
    path = cache / pin["filename"]
    if not path.is_file() or digest(path.read_bytes()) != pin["sha256"]:
        if offline:
            raise ValueError(f"Missing or corrupt offline source: {path.name}")
        cache.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(pin["url"], headers={"User-Agent": "MultiChat-native-build"})
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise ValueError(f"Source archive exceeds the size limit: {path.name}")
        if digest(data) != pin["sha256"]:
            raise ValueError(f"Source hash mismatch: {path.name}")
        path.write_bytes(data)
    return path


def unpack(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        roots = {Path(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise ValueError("Source archive must have one top-level directory")
        for member in members:
            path = (destination / member.name).resolve()
            if not path.is_relative_to(destination.resolve()) or not (member.isdir() or member.isfile()):
                raise ValueError(f"Unsupported source archive entry: {member.name}")
        bundle.extractall(destination)
    return destination / roots.pop()


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def native_version_check(python_path: Path) -> dict:
    env = os.environ | {"PYTHONPATH": str(python_path)}
    result = subprocess.run(
        [sys.executable, "-c",
         "import ctypes,ctypes.util,json;"
         "preloaded=[ctypes.CDLL(path,mode=ctypes.RTLD_GLOBAL) "
         "for name in ('xml2','xslt') if (path:=ctypes.util.find_library(name))];"
         "from lxml import etree as e;"
         "assert e.LIBXML_VERSION >= (2,15,4), e.LIBXML_VERSION;"
         "assert e.LIBXSLT_VERSION >= (1,1,45), e.LIBXSLT_VERSION;"
         "assert e.XML('<root><x>ok</x></root>').xpath('string(x)') == 'ok';"
         "t=e.XSLT(e.XML('<xsl:stylesheet xmlns:xsl=\"http://www.w3.org/1999/XSL/Transform\" version=\"1.0\">"
         "<xsl:template match=\"/\"><ok/></xsl:template></xsl:stylesheet>'));"
         "assert t(e.XML('<a/>')).getroot().tag == 'ok';"
         "from lxml import objectify;assert objectify.fromstring(b'<root><n>7</n></root>').n == 7;"
         "print(json.dumps({'libxml2':e.LIBXML_VERSION,'libxslt':e.LIBXSLT_VERSION,"
         "'module':e.__file__,'host_preload':[lib._name for lib in preloaded]}))"],
        env=env, text=True, capture_output=True, check=True,
    )
    versions = json.loads(result.stdout)
    binary = Path(versions["module"])
    needed = subprocess.run(["readelf", "-d", str(binary)], capture_output=True, text=True, check=True).stdout
    if re.search(r"NEEDED.*\[(?:libxml2|libxslt|libexslt)", needed):
        raise ValueError("lxml still depends on a shared/host XML library instead of the pinned static build")
    versions["elf_sha256"] = digest(binary.read_bytes())
    versions["needed"] = re.findall(r"NEEDED.*\[(.+?)\]", needed)
    return versions


def augment_wheel(wheel: Path, sources: dict[str, Path], pins: list[dict], evidence: dict) -> None:
    """Ship native-source evidence/licenses in dist-info and regenerate wheel RECORD."""
    with zipfile.ZipFile(wheel) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel paths")
        contents = {name: bundle.read(name) for name in names if not name.endswith("/")}
        attributes = {name: bundle.getinfo(name) for name in contents}
    metadata = next(name.rsplit("/", 1)[0] for name in contents if name.endswith(".dist-info/METADATA"))
    contents[f"{metadata}/multichat-native-build.json"] = (
        json.dumps({"sources": pins, "runtime": evidence, "builder_sha256": digest(Path(__file__).read_bytes()),
                    "linkage": "static libxml2/libxslt; built from hash-verified source"}, indent=2) + "\n"
    ).encode()
    for name in ("libxml2", "libxslt"):
        found = []
        for path in sorted(sources[name].rglob("*")):
            if path.is_file() and LICENSE_FILE.match(path.name):
                relative = path.relative_to(sources[name]).as_posix()
                contents[f"{metadata}/licenses/multichat-native/{name}/{relative}"] = path.read_bytes()
                found.append(relative)
        if not found:
            raise ValueError(f"No native license texts exported for {name}")
    record_path = f"{metadata}/RECORD"
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(contents.items()):
        if name != record_path:
            writer.writerow([name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="),
                             len(data)])
    writer.writerow([record_path, "", ""])
    contents[record_path] = record.getvalue().encode()
    replacement = wheel.with_suffix(".tmp")
    with zipfile.ZipFile(replacement, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(contents.items()):
            info = attributes.get(name)
            if info is None:
                info = zipfile.ZipInfo(name)
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    replacement.replace(wheel)


def verified_cache(output: Path, work: Path, pins: list[dict]) -> tuple[Path, dict] | None:
    record_path = output / "native-build.json"
    if not record_path.is_file():
        return None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("sources") != pins or record.get("builder_sha256") != digest(Path(__file__).read_bytes()):
            return None
        name = record["wheel"]
        if Path(name).name != name or not name.endswith(".whl"):
            return None
        wheel = output / name
        if not wheel.is_file() or digest(wheel.read_bytes()) != record["sha256"]:
            return None
        with tempfile.TemporaryDirectory(prefix="verify-cache-", dir=work) as directory:
            extracted = Path(directory)
            with zipfile.ZipFile(wheel) as bundle:
                for entry in bundle.infolist():
                    if not (extracted / entry.filename).resolve().is_relative_to(extracted.resolve()):
                        return None
                metadata = next(name for name in bundle.namelist() if name.endswith(".dist-info/multichat-native-build.json"))
                embedded = json.loads(bundle.read(metadata))
                if embedded.get("sources") != pins or embedded.get("builder_sha256") != record["builder_sha256"]:
                    return None
                bundle.extractall(extracted)
            evidence = native_version_check(extracted)
        return wheel, evidence
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError, zipfile.BadZipFile):
        return None


def install_wheel(wheel: Path, work: Path, env: dict, prefix: Path | None) -> None:
    requirement = work / "install-wheel.txt"
    requirement.write_text(f"{wheel.resolve().as_uri()} --hash=sha256:{digest(wheel.read_bytes())}\n")
    command = [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--require-hashes",
               "--force-reinstall", "-r", str(requirement)]
    if prefix:
        command.extend(("--prefix", str(prefix)))
    run(command, work, env)
    site = (Path(sysconfig.get_path("platlib", vars={"base": str(prefix), "platbase": str(prefix)}))
            if prefix else Path(sysconfig.get_path("platlib")))
    native_version_check(site)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-cache", type=Path)
    parser.add_argument("--manifest", type=Path, default=HERE / "native-sources.json")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--install-prefix", type=Path)
    parser.add_argument("--reuse-verified-wheel", action="store_true",
                        help="reuse only matching builder/source pins, wheel hash and a fresh native runtime check")
    options = parser.parse_args()
    if not sys.platform.startswith("linux"):
        raise SystemExit("The hardened release build is Linux-only; ordinary upstream wheels are not verified here")
    pins = json.loads(options.manifest.read_text(encoding="utf-8"))["sources"]
    if {pin["name"] for pin in pins} != {"libxml2", "libxslt", "lxml"}:
        raise ValueError("Unexpected native source plan")
    options.work.mkdir(parents=True, exist_ok=True)
    options.output.mkdir(parents=True, exist_ok=True)
    if options.reuse_verified_wheel:
        cached = verified_cache(options.output.resolve(), options.work.resolve(), pins)
        if cached:
            wheel, evidence = cached
            if options.install or options.install_prefix:
                install_wheel(wheel, options.work.resolve(), os.environ.copy(), options.install_prefix)
            print(json.dumps(evidence, indent=2))
            return 0
    cache = options.source_cache or options.work / "downloads"
    # A fresh build tree prevents stale objects from another source/flag combination.
    build = Path(tempfile.mkdtemp(prefix="build-", dir=options.work.resolve()))
    prefix = build / "prefix"
    env = os.environ | {"CFLAGS": "-O2 -fPIC", "PKG_CONFIG_PATH": str(prefix / "lib/pkgconfig"),
                        "PATH": str(prefix / "bin") + os.pathsep + os.environ["PATH"]}
    sources = {pin["name"]: unpack(source_archive(pin, cache, options.offline), build) for pin in pins}
    for name, extra in (
        ("libxml2", ["--without-python", "--without-zlib", "--without-lzma", "--without-http"]),
        ("libxslt", ["--without-python", "--without-crypto", f"--with-libxml-prefix={prefix}"]),
    ):
        run(["./configure", f"--prefix={prefix}", "--libdir=" + str(prefix / "lib"),
             "--disable-shared", "--enable-static", "--with-pic", "--disable-dependency-tracking", *extra],
            sources[name], env)
        run(["make", "-j2"], sources[name], env)
        run(["make", "install"], sources[name], env)
    env |= {
        # libmpv loads host dependencies globally. Bind vendored XML code and data
        # locally instead of allowing an earlier host libxml2 to interpose them.
        "LDFLAGS": "-Wl,--exclude-libs,ALL",
        "LXML_STATIC_INCLUDE_DIRS": os.pathsep.join((str(prefix / "include"), str(prefix / "include/libxml2"))),
        "LXML_STATIC_LIBRARY_DIRS": str(prefix / "lib"),
        "LXML_STATIC_BINARIES": os.pathsep.join(str(prefix / "lib" / name)
                                               for name in ("libexslt.a", "libxslt.a", "libxml2.a")),
    }
    run([sys.executable, "setup.py", "--static", "--without-cython", "bdist_wheel",
         "--dist-dir", str(options.output.resolve())], sources["lxml"], env)
    wheels = list(options.output.glob(f"lxml-{next(pin['version'] for pin in pins if pin['name']=='lxml')}-"
                                      f"cp{sys.version_info.major}{sys.version_info.minor}-*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one newly built interpreter-specific lxml wheel")
    wheel = wheels[0]
    extracted = build / "verify-wheel"
    with zipfile.ZipFile(wheel) as bundle:
        bundle.extractall(extracted)
    evidence = native_version_check(extracted)
    augment_wheel(wheel, sources, pins, evidence)
    (options.output / "native-build.json").write_text(
        json.dumps({"wheel": wheel.name, "sha256": digest(wheel.read_bytes()),
                    "builder_sha256": digest(Path(__file__).read_bytes()),
                    "runtime": evidence, "sources": pins}, indent=2) + "\n", encoding="utf-8",
    )
    if options.install or options.install_prefix:
        install_wheel(wheel, build, env, options.install_prefix)
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

