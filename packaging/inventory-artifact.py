#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventory an actual extracted AppImage/Flatpak tree; never substitute the build venv.

Uses Python's standard library and the distribution's readelf/dpkg tools. The JSON
keeps measured file facts, declared source inputs, runtime evidence and unresolved
composition separate. --require-complete fails closed on remaining coverage gaps.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
from urllib.parse import quote

REPORT_NAMES = {"inventory.json", "inventory.cdx.json", "inventory-summary.json", "build-context.json"}
LICENSE_RE = re.compile(r"licen[cs]e|copying|copyright|notice", re.I)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def command(args: list[str]) -> dict:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return {"argv": args, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"argv": args, "returncode": None, "error": str(error)}


def build_context() -> dict:
    """Build tools/OS are provenance, not automatically shipped components."""
    data = {"python": sys.version, "executable": sys.executable,
            "source_snapshot_sha256": os.environ.get("MULTICHAT_SOURCE_SNAPSHOT_SHA256"),
            "github": {key: os.environ[key] for key in ("GITHUB_SHA", "GITHUB_REF", "GITHUB_RUN_ID") if key in os.environ},
            "container_ref": os.environ.get("MULTICHAT_BUILD_CONTAINER", "not supplied"),
            "os_release": Path("/etc/os-release").read_text() if Path("/etc/os-release").is_file() else sys.platform,
            "git": command(["git", "rev-parse", "HEAD"]),
            "tools": [{"name": dist.metadata["Name"], "version": dist.version}
                      for dist in importlib.metadata.distributions()]}
    diff = command(["git", "diff", "--no-ext-diff", "--binary"])
    data["working_tree"] = {"status": command(["git", "status", "--porcelain"]),
                            "tracked_diff_sha256": hashlib.sha256(diff.get("stdout", "").encode()).hexdigest()}
    if shutil.which("dpkg-query"):
        data["distribution_packages"] = command(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\n"])
    elif shutil.which("rpm"):
        data["distribution_packages"] = command(["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n"])
    elif shutil.which("pacman"):
        data["distribution_packages"] = command(["pacman", "-Q"])
    if shutil.which("flatpak"):
        data["flatpak_refs"] = command(["flatpak", "list", "--columns=ref,active,origin"])
        data["flatpak_commits"] = []
        for ref in data["flatpak_refs"].get("stdout", "").splitlines():
            reference = ref.split("\t", 1)[0]
            if reference:
                data["flatpak_commits"].append(command(["flatpak", "info", "--show-commit", reference]))
    return data


def parse_elf(text: str) -> dict:
    return {"needed": sorted(set(re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", text))),
            "soname": next(iter(re.findall(r"\(SONAME\).*?\[([^\]]+)\]", text)), None),
            "build_id": next(iter(re.findall(r"Build ID:\s*([0-9a-fA-F]+)", text)), None),
            "rpath": re.findall(r"\((?:RUNPATH|RPATH)\).*?\[([^\]]+)\]", text)}


def files_in(root: Path, output: Path) -> tuple[list[dict], list[str]]:
    records, gaps = [], []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.parent.resolve() == output.resolve() and path.name in REPORT_NAMES:
            continue
        if path.is_symlink():
            target = path.resolve()
            records.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
            if not target.is_relative_to(root):
                gaps.append(f"external-symlink:{relative}")
            continue
        if not path.is_file():
            continue
        record = {"path": relative, "size": path.stat().st_size, "sha256": digest(path), "kind": "data"}
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic == b"\x7fELF":
            record["kind"] = "ELF"
            result = command(["readelf", "-d", "-n", str(path)])
            if result["returncode"] != 0:
                gaps.append(f"elf-inspection-failed:{relative}")
                record["inspection_error"] = result
            else:
                record.update(parse_elf(result["stdout"]))
        elif magic[:2] == b"MZ":
            record["kind"] = "PE"
            gaps.append(f"pe-import-and-vendored-composition-unresolved:{relative}")
        records.append(record)
    return records, gaps


def safe_child(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes inventory scope: {relative}")
    return path


def license_ledgers(root: Path) -> tuple[list[dict], list[dict], list[str]]:
    licenses, packages, errors = [], [], []
    for ledger in sorted(root.rglob("license-inventory.json")):
        data = json.loads(ledger.read_text(encoding="utf-8"))
        packages.extend(data.get("components", []))
        for item in data.get("files", []):
            path = safe_child(ledger.parent, item["path"])
            entry = {**item, "artifact_path": path.relative_to(root).as_posix()}
            if not path.is_file():
                errors.append(f"missing-license-export:{entry['artifact_path']}")
            elif digest(path) != item["sha256"]:
                errors.append(f"changed-license-export:{entry['artifact_path']}")
            licenses.append(entry)
    for checksums in root.rglob("multichat-native/*/SHA256SUMS"):
        for line in checksums.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
            if not match:
                errors.append(f"invalid-native-license-checksum:{checksums.relative_to(root)}")
                continue
            path = safe_child(checksums.parent, match[2])
            if not path.is_file() or digest(path) != match[1]:
                errors.append(f"missing-or-changed-native-license:{path.relative_to(root)}")
    return licenses, packages, errors


def shipped_python(root: Path) -> list[dict]:
    result = []
    metadata_files = [*root.rglob("*.dist-info/METADATA"), *root.rglob("*.egg-info/PKG-INFO")]
    for metadata in sorted(metadata_files):
        package = importlib.metadata.PathDistribution(metadata.parent)
        paths = []
        for entry in package.files or []:
            path = Path(package.locate_file(entry)).resolve()
            if path.is_relative_to(root) and path.is_file():
                paths.append(path.relative_to(root).as_posix())
        result.append({"name": package.metadata["Name"], "version": package.version,
                       "license": package.metadata.get("License-Expression") or package.metadata.get("License"),
                       "requires": package.metadata.get_all("Requires-Dist") or [],
                       "metadata_path": metadata.relative_to(root).as_posix(), "metadata_sha256": digest(metadata),
                       "artifact_files": paths, "evidence": "metadata physically inside artifact"})
    return result


def secure_lxml_license_alias(root: Path, pins: list[dict], packages: list[dict], licenses: list[dict]) -> dict | None:
    """Bind this module alias to real wheel RECORD files and the exact native source plan.

    This associates license exports with declared build inputs. Runtime probing
    separately establishes the actual loaded binary versions after artifact stripping.
    """
    module_pins = [pin for pin in pins if pin["name"] == "secure-lxml"]
    if not module_pins:
        return None
    builders = [pin for pin in module_pins if Path(pin.get("path", "")).name == "build-secure-lxml.py"]
    expected_sources = {(pin["url"], pin.get("sha256")) for pin in module_pins if "url" in pin}
    if len(builders) != 1 or not builders[0].get("sha256") or len(expected_sources) != 3:
        return None
    for package in packages:
        if package["name"].lower() != "lxml" or not package.get("metadata_path"):
            continue
        metadata = safe_child(root, package["metadata_path"])
        if metadata.name != "METADATA" or not metadata.parent.name.endswith(".dist-info"):
            continue
        distribution = importlib.metadata.PathDistribution(metadata.parent)
        owned = {Path(distribution.locate_file(entry)).resolve(): entry for entry in distribution.files or []}

        def recorded(path):
            path = path.resolve()
            entry = owned.get(path)
            if entry is None or not path.is_relative_to(root) or not path.is_file() or entry.hash is None:
                return None
            checksum = digest(path)
            encoded = base64.urlsafe_b64encode(bytes.fromhex(checksum)).decode("ascii").rstrip("=")
            return checksum if entry.hash.mode == "sha256" and entry.hash.value == encoded else None

        def exported(path, checksum, kind):
            if checksum is None:
                return None
            source = owned[path.resolve()].as_posix()
            for entry in licenses:
                if (entry["package"].lower() == "lxml" and entry.get("kind", "license") == kind and
                        entry.get("source", "").replace("\\", "/") == source and entry.get("sha256") == checksum):
                    target = safe_child(root, entry["artifact_path"])
                    if target.is_file() and digest(target) == checksum:
                        return entry["artifact_path"]
            return None

        provenance = metadata.parent / "multichat-native-build.json"
        provenance_hash = recorded(provenance)
        provenance_export = exported(provenance, provenance_hash, "native-build-provenance")
        if recorded(metadata) is None or provenance_export is None:
            continue
        try:
            document = json.loads(provenance.read_text(encoding="utf-8"))
            sources = document["sources"]
            by_name = {source["name"]: source for source in sources}
            if (len(sources) != 3 or set(by_name) != {"lxml", "libxml2", "libxslt"} or
                    {(source["url"], source["sha256"]) for source in sources} != expected_sources or
                    document.get("builder_sha256") != builders[0]["sha256"] or
                    by_name["lxml"]["version"] != package["version"]):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        native_licenses = []
        for library in ("libxml2", "libxslt"):
            # Require each actual source's primary license, not a nested test NOTICE.
            source = metadata.parent / "licenses/multichat-native" / library / "Copyright"
            checksum = recorded(source)
            destination = exported(source, checksum, "license")
            if destination is None:
                break
            native_licenses.append({"library": library, "source": source.relative_to(root).as_posix(),
                                    "export": destination, "sha256": checksum})
        if len(native_licenses) == 2:
            return {"module": "secure-lxml", "package": "lxml", "version": package["version"],
                    "metadata_path": package["metadata_path"], "metadata_sha256": digest(metadata),
                    "provenance_path": provenance.relative_to(root).as_posix(), "provenance_sha256": provenance_hash,
                    "provenance_export": provenance_export, "source_pins": sources, "licenses": native_licenses,
                    "scope": "shipped wheel RECORD hashes and native build provenance match manifest inputs; binary versions are separately probed"}
    return None


def frozen_archive(root: Path) -> tuple[dict | None, list[str]]:
    binary = root / "usr/bin/multichat"
    if not binary.is_file():
        return None, ["no-frozen-executable-to-verify-analysis-inputs"]
    try:
        import PyInstaller
        from PyInstaller.archive.readers import CArchiveReader

        archive = CArchiveReader(binary)
        modules = {}
        for name, entry in archive.toc.items():
            if entry[-1] == "z":
                embedded = archive.open_embedded_archive(name)
                modules.update({module: {"type": details[0], "offset": details[1], "size": details[2]}
                                for module, details in embedded.toc.items()})
        return {"path": binary.relative_to(root).as_posix(), "sha256": digest(binary),
                "reader": f"PyInstaller {PyInstaller.__version__}", "pyz_modules": modules,
                "members": {name: {"size": value[2], "type": value[-1]} for name, value in archive.toc.items()}}, []
    except Exception as error:
        return None, [f"frozen-archive-inspection-failed:{type(error).__name__}:{error}"]


def upstream_sboms(root: Path, licenses: list[dict]) -> tuple[list[dict], list[str]]:
    """Retain supplied component graphs without pretending every upstream build dependency is shipped."""
    records, gaps = [], []
    for item in licenses:
        if item.get("kind") != "upstream-sbom":
            continue
        path = safe_child(root, item["artifact_path"])
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            records.append({"owner": item["package"], "path": item["artifact_path"], "sha256": item["sha256"],
                            "document": value,
                            "scope": "upstream supplied SBOM; build versus linked components require review"})
        except (OSError, ValueError) as error:
            gaps.append(f"upstream-sbom-unreadable:{item['artifact_path']}:{error}")
    return records, gaps


def runtime_facts(evidence: dict, files: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    by_hash = {}
    for item in files:
        if item.get("sha256"):
            by_hash.setdefault(item["sha256"], []).append(item["path"])
    observed_hashes = {item.get("sha256") for item in evidence.get("loaded_files", []) if item.get("sha256")}
    components, external, gaps = [], [], []
    for item in evidence.get("components", []):
        origins = []
        for source in item.get("files", []):
            matches = by_hash.get(source.get("sha256"), [])
            origins.append({**source, "artifact_matches": matches})
        status = "hash-matched-artifact" if any(origin["artifact_matches"] for origin in origins) else (
            "hash-matched-observed-external-runtime" if origins and all(origin.get("sha256") in observed_hashes for origin in origins)
            else "external-or-unmatched"
        )
        components.append({**item, "files": origins, "evidence_scope": status})
        if status == "external-or-unmatched":
            gaps.append(f"runtime-version-not-bound-to-artifact:{item['name']}")
    for source in evidence.get("loaded_files", []):
        if source.get("sha256") not in by_hash:
            external.append(source)
    for error in evidence.get("errors", []):
        gaps.append(f"runtime-probe-error:{error['component']}")
    return components, external, gaps


def distro_origin(record: dict) -> dict | None:
    """A filename is insufficient: require identical bytes or the same ELF build ID."""
    if not shutil.which("dpkg-query"):
        return None
    search = command(["dpkg-query", "-S", "*/" + Path(record["path"]).name])
    for line in search.get("stdout", "").splitlines():
        if ": /" not in line:
            continue
        owner, source = line.rsplit(": ", 1)
        path = Path(source)
        if not path.is_file():
            continue
        source_hash = digest(path)
        evidence = "identical bytes"
        if source_hash != record["sha256"]:
            if not record.get("build_id"):
                continue
            details = command(["readelf", "-n", str(path)])
            if parse_elf(details.get("stdout", ""))["build_id"] != record["build_id"]:
                continue
            evidence = "same ELF build ID; packaging may have changed RUNPATH"
        version = command(["dpkg-query", "-W", "-f=${Version}", owner])
        return {"kind": "distribution-package", "package": owner, "version": version.get("stdout", "").strip(),
                "source_path": source, "source_sha256": source_hash, "evidence": evidence}
    return None


def native_ownership(root: Path, native: list[dict], packages: list[dict], export: Path | None) -> tuple[list[dict], list[str]]:
    exports, gaps = [], []
    for item in native:
        owners = []
        for package in packages:
            if item["path"] in package.get("artifact_files", []):
                owners.append(package["name"])
            for evidence in package.get("evidence", []) if isinstance(package.get("evidence"), list) else []:
                target = evidence["target"].replace("\\", "/")
                if item["sha256"] == evidence.get("sha256") or item["path"].endswith("/" + target):
                    owners.append(package["name"])
                elif evidence["kind"] == "BOOTLOADER" and evidence.get("build_id") and evidence["build_id"] == item.get("build_id"):
                    owners.append(package["name"])
        if owners:
            item["origin"] = {"kind": "shipped-python-distribution", "packages": sorted(set(owners))}
            continue
        basename = Path(item["path"]).name
        if basename.startswith("libpython") or "/lib-dynload/" in item["path"]:
            library = (Path(sysconfig.get_config_var("LIBDIR")) / str(sysconfig.get_config_var("LDLIBRARY"))
                       if basename.startswith("libpython") else Path(sysconfig.get_config_var("DESTSHARED")) / basename)
            text = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
            matched = library.is_file() and digest(library) == item["sha256"]
            if not matched and library.is_file() and item.get("build_id"):
                source_elf = command(["readelf", "-n", str(library)])
                matched = parse_elf(source_elf.get("stdout", ""))["build_id"] == item["build_id"]
            if matched:
                item["origin"] = {"kind": "python-runtime-build", "version": sys.version.split()[0],
                                  "source_path": str(library), "source_sha256": digest(library)}
                if export is not None and text.is_file():
                    destination = export / "system/CPython/LICENSE.txt"
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(text, destination)
                    exports.append({"package": "CPython", "version": sys.version.split()[0], "kind": "license",
                                    "source": str(text), "path": destination.relative_to(root).as_posix(),
                                    "artifact_path": destination.relative_to(root).as_posix(), "sha256": digest(destination)})
                elif not text.is_file():
                    gaps.append("python-runtime-license-not-found")
                continue
        owner = distro_origin(item)
        if not owner:
            gaps.append(f"native-file-origin-unresolved:{item['path']}")
            continue
        item["origin"] = owner
        if export is None:
            continue
        package = owner["package"].split(":", 1)[0]
        source = Path("/usr/share/doc") / package / "copyright"
        if not source.is_file():
            gaps.append(f"distribution-license-not-found:{owner['package']}")
            continue
        destination = export / "system" / package / "copyright"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        exports.append({"package": owner["package"], "version": owner["version"], "kind": "license",
                        "source": str(source), "path": destination.relative_to(root).as_posix(),
                        "artifact_path": destination.relative_to(root).as_posix(), "sha256": digest(destination)})
    exports = list({item["artifact_path"]: item for item in exports}.values())
    if exports and export is not None:
        system = export / "system"
        ledger = [{**item, "path": safe_child(root, item["artifact_path"]).relative_to(system).as_posix()} for item in exports]
        (system / "license-inventory.json").write_text(json.dumps({"scope": "hash/build-ID matched distribution binaries",
                                                                 "files": ledger}, indent=2) + "\n", encoding="utf-8")
    return exports, gaps


def inventory(root: Path, kind: str, output: Path, runtime: dict | None = None,
              manifest: Path | None = None, artifact: Path | None = None,
              export_system_licenses: Path | None = None) -> dict:
    root, output = root.resolve(), output.resolve()
    if export_system_licenses is not None:
        export_system_licenses = export_system_licenses.resolve()
        if not export_system_licenses.is_relative_to(root):
            raise ValueError("System license exports must remain inside the artifact tree")
    files, gaps = files_in(root, output)
    archive, archive_gaps = frozen_archive(root) if kind == "appimage" else (None, [])
    gaps.extend(archive_gaps)
    licenses, toc_packages, errors = license_ledgers(root)
    python = shipped_python(root)
    by_name = {re.sub(r"[-_.]+", "-", item["name"]).lower(): item for item in python}
    for item in toc_packages:
        key = re.sub(r"[-_.]+", "-", item["name"]).lower()
        if item.get("evidence"):
            evidence = item["evidence"]
            presence = "analysis inputs; binary archive verification unavailable"
            if archive:
                confirmed = []
                for source in evidence:
                    target = source["target"].replace("\\", "/")
                    if source["kind"] == "PYMODULE" and target in archive["pyz_modules"]:
                        confirmed.append(source)
                    elif source["kind"] in {"PYSOURCE", "BOOTLOADER"} and (
                        target in archive["members"] or source["kind"] == "BOOTLOADER"
                    ):
                        confirmed.append(source)
                    elif any(record["path"].endswith("/" + target) for record in files):
                        confirmed.append(source)
                presence = "actual frozen code/file members" if confirmed else "metadata/data or unconfirmed analysis inputs"
                evidence = confirmed
            by_name[key] = {**item, "binary_evidence": evidence, "code_presence": presence,
                            "evidence_scope": "PyInstaller inputs checked against final CArchive/PYZ" if archive else
                                              "PyInstaller analysis inputs only; binary verification incomplete"}
    python = sorted(by_name.values(), key=lambda item: item["name"].lower())
    if not python:
        gaps.append("no-shipped-python-metadata-or-pyinstaller-input-ledger")
    for package in python:
        matches = [item for item in licenses if item["package"].lower() == package["name"].lower()
                   and item.get("kind", "license") == "license"]
        physical = [path for path in package.get("artifact_files", []) if LICENSE_RE.search(Path(path).name)]
        if not matches and not physical:
            errors.append(f"shipped-python-license-not-evidenced:{package['name']}")
    sboms, sbom_gaps = upstream_sboms(root, licenses)
    gaps.extend(sbom_gaps)
    facts, external, runtime_gaps = runtime_facts(runtime or {}, files)
    gaps.extend(runtime_gaps)
    if runtime is None:
        gaps.append("no-runtime-binary-version-probe")
    native = [item for item in files if item["kind"] in {"ELF", "PE"}]
    system_exports, origin_gaps = native_ownership(root, native, python, export_system_licenses)
    gaps.extend(origin_gaps)
    licenses = list({item["artifact_path"]: item for item in [*licenses, *system_exports]}.values())
    paths = {item["path"] for item in files}
    for item in system_exports:
        if item["artifact_path"] not in paths:
            path = safe_child(root, item["artifact_path"])
            files.append({"path": item["artifact_path"], "kind": "data", "size": path.stat().st_size, "sha256": item["sha256"]})
    if system_exports and export_system_licenses is not None:
        ledger = export_system_licenses / "system/license-inventory.json"
        relative = ledger.relative_to(root).as_posix()
        if relative not in paths:
            files.append({"path": relative, "kind": "data", "size": ledger.stat().st_size, "sha256": digest(ledger)})
        else:
            next(item for item in files if item["path"] == relative).update(size=ledger.stat().st_size, sha256=digest(ledger))
    native_sources = []
    native_module_aliases = []
    if manifest:
        text = manifest.read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location("dependency_inputs", Path(__file__).with_name("audit-dependencies.py"))
        inputs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inputs)
        pins = inputs.native_pins(text)
        lxml_alias = secure_lxml_license_alias(root, pins, python, licenses)
        if lxml_alias is not None:
            native_module_aliases.append(lxml_alias)
        for pin in pins:
            name = pin["name"]
            pin["evidence_scope"] = "declared build input, not proof of a linked binary"
            native_sources.append(pin)
            direct_license = any(f"multichat-native/{name}/" in item["path"] and LICENSE_RE.search(Path(item["path"]).name)
                                 for item in files)
            hardened_lxml = name == "secure-lxml" and lxml_alias is not None
            if kind == "flatpak" and not (direct_license or hardened_lxml):
                errors.append(f"native-source-license-export-missing:{name}")
    sonames = {}
    for item in native:
        sonames.setdefault(item.get("soname") or Path(item["path"]).name, []).append(item["path"])
    relationships = []
    for item in native:
        for needed in item.get("needed", []):
            relationships.append({"from": item["path"], "needed": needed, "artifact_candidates": sonames.get(needed, []),
                                  "resolution": "potential artifact candidates" if needed in sonames else "external runtime/host requirement"})
    # ELF metadata/probes do not enumerate arbitrary static libraries or every vendored
    # source. Make this a durable coverage result rather than a misleading clean SBOM.
    if native:
        gaps.append("static-and-vendored-native-composition-not-fully-attested")
    if kind == "flatpak":
        gaps.append("runtime-and-baseapp-transitive-components-require-their-own-sbom")
    native_build_evidence = []
    for item in licenses:
        if item.get("kind") == "native-build-provenance":
            path = safe_child(root, item["artifact_path"])
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                native_build_evidence.append({"package": item["package"], "path": item["artifact_path"],
                                              "sha256": item["sha256"], "document": document})
                if document.get("runtime", {}).get("libxml2") and (
                    (runtime or {}).get("probe_policy") != "host-video-preloaded-v1" or not (runtime or {}).get("video_loaded")
                ):
                    errors.append("native-runtime-probe-missing-host-video-preload")
                for fact in facts:
                    if fact["name"].lower() != item["package"].lower() or fact["evidence_scope"] != "hash-matched-artifact":
                        continue
                    for library in ("libxml2", "libxslt"):
                        expected = document.get("runtime", {}).get(library)
                        measured = fact.get("versions", {}).get(library)
                        if isinstance(expected, list):
                            expected = ".".join(map(str, expected))
                        if expected and measured and expected != measured:
                            errors.append(f"native-runtime-mismatch:{item['package']}:{library}:built={expected}:loaded={measured}")
            except (OSError, ValueError) as error:
                errors.append(f"invalid-native-build-provenance:{path.relative_to(root)}:{error}")
    data = {"schema": 1, "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "artifact": {"kind": kind, "root": str(root)}, "files": files, "python_components": python,
            "native_files": [item["path"] for item in native], "native_source_inputs": native_sources if kind == "flatpak" else [],
            "native_module_aliases": native_module_aliases,
            "runtime_components": facts, "external_loaded_libraries": external, "native_dependencies": relationships,
            "runtime_probe": {key: (runtime or {}).get(key) for key in ("application", "python", "platform", "frozen", "probe_policy", "video_loaded")},
            "native_build_evidence": native_build_evidence,
            "frozen_archive": archive,
            "license_exports": licenses, "upstream_sboms": sboms,
            "coverage": {"complete": not gaps and not errors, "gaps": sorted(set(gaps)), "errors": sorted(set(errors)),
                         "scope": "actual artifact files, shipped metadata, PyInstaller inputs and hash-bound runtime facts",
                         "excluded_self_referential_reports": sorted(REPORT_NAMES)}}
    if artifact:
        data["artifact"].update(filename=artifact.name, size=artifact.stat().st_size, sha256=digest(artifact))
    return data


def cyclonedx(data: dict) -> dict:
    components = []
    refs = {}
    for item in data["files"]:
        if "sha256" not in item:
            continue
        reference = "file:" + quote(item["path"], safe="/")
        refs[item["path"]] = reference
        components.append({"type": "file", "bom-ref": reference, "name": item["path"],
                           "hashes": [{"alg": "SHA-256", "content": item["sha256"]}],
                           "properties": [{"name": "multichat:file-kind", "value": item["kind"]},
                                          {"name": "multichat:origin", "value": json.dumps(item.get("origin", {}))}]})
    for item in data["python_components"]:
        name = re.sub(r"[-_.]+", "-", item["name"]).lower()
        component = {"type": "library", "bom-ref": f"pkg:pypi/{name}@{item['version']}",
                     "purl": f"pkg:pypi/{name}@{item['version']}", "name": item["name"], "version": item["version"],
                     "properties": [{"name": "multichat:evidence", "value": str(item.get("evidence_scope") or item.get("evidence"))}]}
        if item.get("license"):
            # Legacy metadata is free text, not necessarily a valid SPDX expression.
            component["licenses"] = [{"license": {"name": item["license"]}}]
        components.append(component)
    for item in data["runtime_components"]:
        for name, version in item.get("versions", {}).items():
            reference = "measured:" + quote(f"{item['name']}:{name}:{version}", safe="")
            components.append({"type": "library", "bom-ref": reference, "name": name, "version": str(version),
                               "scope": "required" if item["evidence_scope"] == "hash-matched-artifact" else "optional",
                               "properties": [{"name": "multichat:evidence", "value": item["evidence_scope"]},
                                              {"name": "multichat:binary-hashes", "value": json.dumps([f.get("sha256") for f in item["files"]])}]})
    dependencies = {}
    for edge in data["native_dependencies"]:
        dependencies.setdefault(refs[edge["from"]], set()).update(refs[p] for p in edge["artifact_candidates"] if p in refs)
    native_owners = {}
    for item in data["files"]:
        owner = item.get("origin", {})
        if owner.get("kind") != "distribution-package":
            continue
        reference = f"distro:{owner['package']}@{owner['version']}"
        native_owners[reference] = owner
        dependencies.setdefault(reference, set()).add(refs[item["path"]])
    for reference, owner in native_owners.items():
        paths = [item["artifact_path"] for item in data["license_exports"] if item["package"] == owner["package"]]
        components.append({"type": "library", "bom-ref": reference, "name": owner["package"], "version": owner["version"],
                           "properties": [{"name": "multichat:evidence", "value": owner["evidence"]},
                                          {"name": "multichat:license-files", "value": json.dumps(paths)}]})
    package_refs = {re.sub(r"[-_.]+", "-", item["name"]).lower(): f"pkg:pypi/{re.sub(r'[-_.]+', '-', item['name']).lower()}@{item['version']}"
                    for item in data["python_components"]}
    for item in data["python_components"]:
        reference = package_refs[re.sub(r"[-_.]+", "-", item["name"]).lower()]
        targets = dependencies.setdefault(reference, set())
        for requirement in item.get("requires", []):
            match = re.match(r"[\w.-]+", requirement)
            name = re.sub(r"[-_.]+", "-", match.group()).lower() if match else ""
            if name in package_refs:
                targets.add(package_refs[name])  # declared dependency; marker evaluation is kept in the JSON evidence
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "metadata": {"timestamp": data["timestamp"], "properties": [
                {"name": "multichat:coverage-complete", "value": str(data["coverage"]["complete"]).lower()},
                {"name": "multichat:coverage-gaps", "value": json.dumps(data["coverage"]["gaps"])},
                {"name": "multichat:artifact-sha256", "value": data["artifact"].get("sha256", "tree inventory before archiving")}]},
            "components": components,
            "dependencies": [{"ref": ref, "dependsOn": sorted(targets)} for ref, targets in sorted(dependencies.items())]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kind", choices=("appimage", "flatpak"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-evidence", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--artifact-file", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--capture-build-context", action="store_true")
    parser.add_argument("--export-system-licenses", type=Path, help="copy copyright files for hash/build-ID-matched distro binaries before packing")
    options = parser.parse_args()
    runtime = json.loads(options.runtime_evidence.read_text(encoding="utf-8")) if options.runtime_evidence else None
    data = inventory(options.root, options.kind, options.output, runtime, options.manifest, options.artifact_file,
                     options.export_system_licenses)
    options.output.mkdir(parents=True, exist_ok=True)
    for name, value in (("inventory.json", data), ("inventory.cdx.json", cyclonedx(data)),
                        ("inventory-summary.json", {"artifact": data["artifact"], "coverage": data["coverage"],
                                                   "files": len(data["files"]), "python": len(data["python_components"]),
                                                   "native": len(data["native_files"])})):
        (options.output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    if options.capture_build_context:
        (options.output / "build-context.json").write_text(json.dumps(build_context(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["coverage"], indent=2))
    return 1 if data["coverage"]["errors"] or (options.require_complete and not data["coverage"]["complete"]) else 0


if __name__ == "__main__":
    sys.exit(main())
