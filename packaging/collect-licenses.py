#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy distribution license files without flattening their relative paths."""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import Iterable

SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "packaging"}
LICENSE_NAME = re.compile(r"(LICEN[CS]E|COPYING|COPYRIGHT|NOTICE|AUTHORS)", re.I)
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def relative_path(value: str) -> Path:
    """Metadata and relocation paths must stay inside their explicitly selected root."""
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or ":" in parts[0] or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe license path: {value}")
    return Path(*parts)


def relocated_license(package, declared: str, mappings: dict | None, root: Path | None):
    """A downstream layout exception is bound to one version, declaration, path and hash."""
    if not mappings:
        return None
    normalized_name = re.sub(r"[-_.]+", "-", package.metadata["Name"]).lower()
    matches = [item for item in mappings["mappings"]
               if re.sub(r"[-_.]+", "-", item["name"]).lower() == normalized_name
               and item["version"] == package.version and item["license_file"] == declared]
    if not matches:
        return None
    if len(matches) != 1 or root is None:
        raise ValueError(f"Ambiguous relocation or missing artifact root: {normalized_name}/{declared}")
    mapping = matches[0]
    source = (root / relative_path(mapping["source"])).resolve()
    if not source.is_relative_to(root.resolve()):
        raise ValueError(f"Relocated license escapes artifact root: {source}")
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != mapping["sha256"]:
        raise ValueError(f"Relocated license missing or hash mismatch: {source}")
    return source, mapping


def source_license_headers(package, mappings: dict | None, root: Path | None):
    """Export only reviewed original headers from hash-bound RECORD-owned sources."""
    if not mappings:
        return
    name = re.sub(r"[-_.]+", "-", package.metadata["Name"]).lower()
    named = [item for item in mappings.get("source_headers", [])
             if re.sub(r"[-_.]+", "-", item["name"]).lower() == name]
    if not named:
        return
    selected = [item for item in named if item["version"] == package.version]
    if named and not selected:
        raise ValueError(f"Source license version mismatch: {name}=={package.version}")
    owned = {entry.as_posix(): entry for entry in package.files or []}
    for mapping in selected:
        relative = relative_path(mapping["source"])
        entry = owned.get(relative.as_posix())
        if entry is None or not package.read_text("RECORD"):
            raise ValueError(f"Source license is not RECORD-owned by {name}: {relative}")
        source = Path(package.locate_file(entry)).resolve()
        if root is None or not source.is_relative_to(root.resolve()):
            raise ValueError(f"Source license escapes or lacks artifact root: {source}")
        if not source.is_file():
            raise ValueError(f"Source license file missing: {source}")
        data = source.read_bytes()
        digest = hashlib.sha256(data).digest()
        if digest.hex() != mapping["source_sha256"]:
            raise ValueError(f"Source license full-file hash mismatch: {source}")
        record_hash = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        if entry.hash is None or entry.hash.mode != "sha256" or entry.hash.value != record_hash:
            raise ValueError(f"Source license RECORD hash mismatch: {source}")
        lines = data.splitlines(keepends=True)
        bounds = mapping["lines"]
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                any(type(value) is not int for value in bounds) or not 1 <= bounds[0] <= bounds[1] <= len(lines)):
            raise ValueError(f"Invalid source license line range: {source}")
        header = b"".join(lines[bounds[0] - 1:bounds[1]])
        if hashlib.sha256(header).hexdigest() != mapping["header_sha256"]:
            raise ValueError(f"Source license header hash mismatch: {source}")
        yield source, entry, relative, header, mapping


def toc_inputs(path: Path) -> dict[str, list[dict]]:
    """Actual PyInstaller analysis inputs, including modules stored inside its PYZ archive."""
    inputs: dict[str, list[dict]] = {}

    def visit(value):
        if isinstance(value, (tuple, list)):
            if len(value) >= 3 and all(isinstance(item, str) for item in value[:3]):
                target, source, kind = value[:3]
                if kind in {"PYMODULE", "PYSOURCE", "EXTENSION", "BINARY", "DATA"}:
                    inputs.setdefault(str(Path(source).resolve()), []).append({"target": target, "kind": kind})
            for item in value:
                visit(item)

    visit(ast.literal_eval(path.read_text(encoding="utf-8")))
    if not inputs:
        raise ValueError("PyInstaller analysis contains no module/file inputs")
    return inputs


def selected_distributions(packages, inputs: dict | None = None):
    """Never identify shipped packages merely because they are installed in the build venv."""
    for package in packages:
        evidence = []
        if inputs is not None:
            for entry in package.files or []:
                path = Path(package.locate_file(entry)).resolve()
                for item in inputs.get(str(path), []):
                    if path.is_file():
                        record = {**item, "source": str(entry), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        if item["kind"] == "BOOTLOADER" and shutil.which("readelf"):
                            result = subprocess.run(["readelf", "-n", str(path)], capture_output=True, text=True, check=True)
                            match = re.search(r"Build ID:\s*([0-9a-fA-F]+)", result.stdout)
                            if match:
                                record["build_id"] = match.group(1)
                        evidence.append(record)
            if not evidence:
                continue
        yield package, evidence


def collect_licenses(output: Path, packages: Iterable[Distribution] | None = None,
                     inputs: dict | None = None, relocated: dict | None = None,
                     artifact_root: Path | None = None) -> list[dict]:
    """Retain each path below .dist-info and record hashes of the exported files.

    This is a license export inventory, not a complete component SBOM.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    exported: dict[str, dict] = {}
    components = []
    missing = []
    if relocated is not None and (relocated.get("schema") != 1 or not isinstance(relocated.get("mappings"), list)):
        raise ValueError("Unsupported relocated-license mapping schema")
    if relocated is not None and not isinstance(relocated.get("source_headers", []), list):
        raise ValueError("Unsupported source-license mapping schema")
    for package, evidence in selected_distributions(distributions() if packages is None else packages, inputs):
        name = package.metadata["Name"]
        if not name or not PACKAGE_NAME.fullmatch(name):
            raise ValueError(f"Invalid distribution name: {name!r}")
        if inputs is None and packages is None and name.lower() in SKIP:
            continue
        components.append({"name": name, "version": package.version,
                           "license": package.metadata.get("License-Expression") or package.metadata.get("License"),
                           "requires": package.metadata.get_all("Requires-Dist") or [],
                           "evidence": evidence, "licenses": []})
        if evidence and all(item["kind"] == "BOOTLOADER" for item in evidence):
            components[-1]["scope"] = "PyInstaller bootloader fragment; Python build tool is not shipped as a whole"
            components[-1]["declared_build_tool_requires"] = components[-1]["requires"]
            components[-1]["requires"] = []
        raw_metadata_root = getattr(package, "_path", None)
        metadata_root = Path(raw_metadata_root) if raw_metadata_root is not None else None
        if (metadata_root is not None and
                (not metadata_root.is_dir() or not metadata_root.name.endswith((".dist-info", ".egg-info")))):
            metadata_root = None

        def export(source, entry, relative, kind="license", mapping=None, content=None, source_header=None):
            if not relative or any(part in {"", ".", ".."} for part in relative):
                raise ValueError(f"Unsafe license path: {entry}")
            destination = output / name / Path(*relative)
            digest = hashlib.sha256(source.read_bytes() if content is None else content).hexdigest()
            key = destination.relative_to(output).as_posix()
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Different license texts would overwrite {key}")
            if key in exported:
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                shutil.copyfile(source, destination)
            else:
                destination.write_bytes(content)
            exported[key] = {
                "package": name, "version": package.version,
                "license": package.metadata.get("License-Expression") or package.metadata.get("License"),
                "source": str(entry), "path": key, "sha256": digest, "kind": kind,
            }
            if mapping is not None:
                exported[key]["relocated_license"] = mapping
            if source_header is not None:
                exported[key]["source_license_header"] = source_header
            components[-1]["licenses"].append(key)

        for entry in package.files or []:
            is_sbom = any("sbom" in part.lower() for part in entry.parts)
            is_provenance = entry.name == "multichat-native-build.json"
            if not LICENSE_NAME.search(entry.name) and not is_sbom and not is_provenance:
                continue
            dist_info = next((i for i, part in enumerate(entry.parts) if part.endswith(".dist-info")), None)
            metadata_name = metadata_root.name if metadata_root is not None else None
            if dist_info is None:
                relative = ("package-files", *entry.parts)
            elif entry.parts[dist_info] == metadata_name:
                relative = entry.parts[dist_info + 1:]
            else:
                # setuptools and other packages can ship several vendored .dist-info
                # directories. Preserve their identity instead of flattening each LICENSE.
                relative = ("vendored", *entry.parts)
            source = Path(package.locate_file(entry))
            if not source.is_file():
                continue
            export(source, entry, relative,
                   "upstream-sbom" if is_sbom else "native-build-provenance" if is_provenance else "license")
        declared = package.metadata.get_all("License-File") or []
        for license_path in declared:
            relative = relative_path(license_path)
            # PEP 639 installs below .dist-info/licenses; older tools used the
            # metadata directory itself. RECORD/SOURCES.txt can omit either.
            candidates = [metadata_root / "licenses" / relative, metadata_root / relative] if metadata_root is not None else []
            source = next((path for path in candidates if path.is_file()), None)
            if source is not None:
                if not source.resolve().is_relative_to(metadata_root.resolve()):
                    raise ValueError(f"Declared license path escapes distribution metadata: {source}")
                entry = source.relative_to(metadata_root.parent)
                export(source, entry, source.relative_to(metadata_root).parts)
                continue
            mapped = relocated_license(package, license_path, relocated, artifact_root)
            if mapped is not None:
                source, mapping = mapped
                export(source, source, ("licenses", *relative.parts), mapping=mapping)
                continue
            expected = ' or '.join(map(str, candidates)) or 'an own .dist-info/.egg-info directory or explicit relocation'
            missing.append(f"{name}=={package.version}/{license_path} (expected {expected})")
        for source, entry, relative, header, mapping in source_license_headers(package, relocated, artifact_root):
            destination = (*relative.parts[:-1], relative.name + ".LICENSE")
            export(source, entry, ("source-headers", *destination), content=header, source_header=mapping)
    if missing:
        raise ValueError("Declared license file missing from installed distribution: " + "; ".join(missing))
    inventory = sorted(exported.values(), key=lambda item: item["path"])
    (output / "license-inventory.json").write_text(
        json.dumps({"scope": "PyInstaller input distributions" if inputs is not None else "selected Python distribution metadata",
                    "components": components, "files": inventory}, indent=2) + "\n",
        encoding="utf-8",
    )
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--toc", type=Path, help="select only distributions used by this PyInstaller analysis")
    parser.add_argument("--metadata-root", type=Path, action="append", help="discover metadata only under these shipped site paths")
    parser.add_argument("--relocated-licenses", type=Path, help="explicit version/path/hash bindings for downstream license layouts")
    parser.add_argument("--artifact-root", type=Path, help="root containing the mapped downstream license files")
    parser.add_argument("--pyinstaller-bootloader", action="store_true", help="include the actually used Linux PyInstaller bootloader's license")
    options = parser.parse_args()
    packages = list(distributions(path=[str(path) for path in options.metadata_root])) if options.metadata_root else None
    inputs = toc_inputs(options.toc) if options.toc else None
    if options.pyinstaller_bootloader:
        import PyInstaller

        bootloader = Path(PyInstaller.__file__).parent / "bootloader" / PyInstaller.PLATFORM / "run"
        if inputs is None or not bootloader.is_file():
            raise SystemExit("An actual Linux PyInstaller analysis/bootloader is required")
        inputs.setdefault(str(bootloader.resolve()), []).append({"target": "multichat:bootloader", "kind": "BOOTLOADER"})
    relocated = json.loads(options.relocated_licenses.read_text(encoding="utf-8")) if options.relocated_licenses else None
    print(f"    {len(collect_licenses(options.output, packages, inputs, relocated, options.artifact_root))} license/notice/SBOM files copied")

