#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventory installed Python packages/direct native pins and query their OSV coverage.

The inventory is deliberately partial: it cannot identify native libraries embedded
in wheels, Flatpak runtime/BaseApp contents, or host packages from Python metadata.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COVERAGE = (
    "Queries cover the explicitly listed PyPI versions/direct native source commits and, "
    "when actual artifact inventories are supplied, hash-bound measured native versions "
    "mapped to OSS-Fuzz. Unmapped components are listed separately. "
    "OSV coverage, static/vendored composition, runtime/BaseApp and host libraries "
    "are not proven complete; native matches still require build/backport/path triage."
)


def native_pins(manifest: str) -> list[dict]:
    pins = []
    for block in re.split(r"\n  - name:\s*", manifest)[1:]:
        name = block.split("\n", 1)[0].split("#", 1)[0].strip()
        if name == "multichat":
            continue
        for index, source in enumerate(re.split(r"\n      - type:\s*", block)[1:]):
            url = re.search(r"\burl:\s*(https://[^\s#]+)", source)
            local = re.search(r"\bpath:\s*['\"]?([^\s'\"]+)", source)
            if not url and not local:
                continue
            pin = {"name": name, "source_index": index, "type": source.splitlines()[0].strip()}
            if url:
                pin["url"] = url.group(1)
            elif local:
                pin["path"] = local.group(1)
                location = ROOT / "packaging/flatpak" / pin["path"]
                if location.is_file():
                    import hashlib
                    pin["sha256"] = hashlib.sha256(location.read_bytes()).hexdigest()
            for field in ("tag", "commit", "sha256"):
                match = re.search(rf"\b{field}:\s*['\"]?([^\s'\"]+)", source)
                if match:
                    pin[field] = match.group(1)
            pins.append(pin)
    return pins


def lock_pins(lock: str) -> list[dict]:
    return [{"name": match.group(1), "version": match.group(2)}
            for match in re.finditer(r"^([\w.-]+)==([^\s;\\]+)", lock, re.M)]


def artifact_queries(paths: list[Path]) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Use actual release evidence, including measured vendored versions; never the build venv."""
    python, native, runtime, skipped = {}, {}, {}, []
    names = {"openssl": "openssl", "curl": "curl", "libxml2": "libxml2", "libxslt": "libxslt", "ffmpeg": "ffmpeg"}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or "files" not in data or "coverage" not in data:
            raise ValueError(f"Not an artifact inventory: {path}")
        for item in data["python_components"]:
            python[item["name"], item["version"]] = {"name": item["name"], "version": item["version"]}
        for pin in data["native_source_inputs"]:
            if pin.get("commit"):
                native[pin["name"], pin["commit"]] = pin
        for component in data["runtime_components"]:
            if component.get("evidence_scope") not in {"hash-matched-artifact", "hash-matched-observed-external-runtime"}:
                skipped.append(f"{path.name}:{component['name']}: runtime evidence not bound to a binary")
                continue
            for key, raw in component.get("versions", {}).items():
                if key not in names:
                    skipped.append(f"{path.name}:{key}: no configured native version ecosystem mapping")
                    continue
                # curl_version() includes both curl and TLS versions; don't mistake a TLS
                # version for curl's. Other mappings use an explicit library version field.
                match = re.search(r"(?:libcurl/|curl/)([0-9]+\.[0-9]+\.[0-9]+)" if key == "curl" else
                                  r"\b([0-9]+\.[0-9]+\.[0-9]+)\b", str(raw))
                if not match:
                    skipped.append(f"{path.name}:{key}: non-version or vendor revision {raw!r}")
                    continue
                pin = {"name": names[key], "version": match.group(1), "ecosystem": "OSS-Fuzz", "evidence": str(path)}
                runtime[pin["name"], pin["version"]] = pin
    return list(python.values()), list(native.values()), list(runtime.values()), skipped


def make_bom(packages, native: list[dict], timestamp: str, artifact: str = "source") -> dict:
    inventory_scope = (
        f"Python distribution metadata in the {artifact} build/audit environment, including build/test tools. "
        + ("The AppImage's host libmpv/FFmpeg are not included. " if artifact == "appimage" else
           "Direct Flatpak native source pins are included as declared inputs, not verified binary contents. ")
        + "This partial environment/source inventory does not establish the exact shipped component set. "
          "Embedded wheel libraries, transitive native dependencies, runtime/BaseApp and host packages "
          "are not fully inventoried."
    )
    components = []
    for package in packages:
        name = package.metadata["Name"]
        component = {
            "type": "library", "name": name, "version": package.version,
            "purl": f"pkg:pypi/{re.sub(r'[-_.]+', '-', name).lower()}@{package.version}",
            "properties": [{"name": "multichat:evidence", "value": "installed Python distribution metadata"}],
        }
        expression = package.metadata.get("License-Expression")
        if expression:
            component["licenses"] = [{"expression": expression}]
        components.append(component)
    for pin in native if artifact != "appimage" else []:
        component = {
            "type": "library", "name": pin["name"],
            "version": pin.get("tag") or pin.get("commit") or pin.get("sha256", "unspecified"),
            "properties": [{"name": f"multichat:source:{key}", "value": value} for key, value in pin.items()
                           if key not in {"name", "url"} and isinstance(value, str)] +
                          [{"name": "multichat:evidence", "value": "Flatpak manifest source pin; not an installed binary"}],
        }
        if "url" in pin:
            component["externalReferences"] = [{"type": "distribution", "url": pin["url"]}]
        components.append(component)
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
        "metadata": {"timestamp": timestamp,
                     "properties": [{"name": "multichat:coverage", "value": inventory_scope},
                                    {"name": "multichat:artifact", "value": artifact}]},
        "components": sorted(components, key=lambda item: (item["name"].lower(), item["version"])),
    }


def query_osv(queries: list[dict]) -> list[dict]:
    request = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps({"queries": queries}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "MultiChat-dependency-triage"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    results = result.get("results")
    if not isinstance(results, list) or len(results) != len(queries):
        raise RuntimeError("OSV returned an incomplete response; no successful scan can be claimed")
    if any(item.get("next_page_token") for item in results):
        raise RuntimeError("OSV paginated the response; review is incomplete and must be continued")
    return results


def write_reports(output: Path, python: list[dict], native: list[dict], results: list[dict],
                  queries: list[dict], timestamp: str, runtime: list[dict] | None = None,
                  skipped: list[str] | None = None) -> bool:
    if len(results) != len(queries):
        raise ValueError("Every query must have a result")
    output.mkdir(parents=True, exist_ok=True)
    (output / "osv.json").write_text(
        json.dumps({"timestamp": timestamp, "coverage": COVERAGE,
                    "queries": queries, "results": results}, indent=2) + "\n", encoding="utf-8",
    )
    descriptions = [f'PyPI {pin["name"]}=={pin["version"]}' for pin in python]
    descriptions += [f'Native {pin["name"]} @ {pin["commit"]}' for pin in native if "commit" in pin]
    descriptions += [f'Measured native {pin["name"]} {pin["version"]} ({pin["ecosystem"]})' for pin in runtime or []]
    lines = [
        "# Dependency advisory triage", "", f"UTC: {timestamp}", "", COVERAGE, "",
        "A successful workflow means these queries completed. It is not a security clearance.",
        "Native commit matches require build-option, patch and application-path review; "
        "they do not establish an exploitable application vulnerability.",
        "See packaging/SECURITY.md for the current FFmpeg triage and uncovered components.", "",
        "| Query | OSV result |", "|---|---|",
    ]
    python_hits = False
    for i, (description, result) in enumerate(zip(descriptions, results)):
        advisories = result.get("vulns", [])
        if advisories and i < len(python):
            python_hits = True
        links = ", ".join(f'[{item["id"]}](https://osv.dev/vulnerability/{item["id"]})' for item in advisories)
        lines.append(f"| {description} | {links or 'No indexed match returned'} |")
    if skipped:
        lines.extend(["", "Unqueried measured components (not a clean scan):", "", *[f"- {item}" for item in skipped]])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return python_hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dependency-report"))
    parser.add_argument("--artifact", choices=("source", "appimage", "flatpak"), default="source")
    parser.add_argument("--inventory-only", action="store_true", help="write the inventory without network access")
    parser.add_argument("--inventory-file", action="append", type=Path, help="query this actual release inventory instead of installed/build dependencies")
    options = parser.parse_args()
    output = options.output
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    native = native_pins((ROOT / "packaging/flatpak/io.github.Corrgan.MultiChat.yml").read_text(encoding="utf-8"))
    python = lock_pins((ROOT / "packaging/requirements-lock.txt").read_text(encoding="utf-8"))
    runtime, skipped = [], []
    if options.inventory_file:
        python, native, runtime, skipped = artifact_queries(options.inventory_file)
    queries = [{"package": {"ecosystem": "PyPI", "name": pin["name"]}, "version": pin["version"]}
               for pin in python]
    queries += [{"commit": pin["commit"]} for pin in native if "commit" in pin]
    queries += [{"package": {"ecosystem": pin["ecosystem"], "name": pin["name"]}, "version": pin["version"]} for pin in runtime]
    output.mkdir(parents=True, exist_ok=True)
    # A failed refresh must not leave a previous clean summary looking current.
    (output / "summary.md").unlink(missing_ok=True)
    (output / "osv.json").unlink(missing_ok=True)
    if not options.inventory_file:
        (output / "inventory.cdx.json").write_text(
            json.dumps(make_bom(importlib.metadata.distributions(), native, timestamp, options.artifact), indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        (output / "artifact-query-inputs.json").write_text(json.dumps({"inventories": [str(path) for path in options.inventory_file],
                                                                     "python": python, "native": native, "runtime": runtime,
                                                                     "unqueried": skipped}, indent=2) + "\n", encoding="utf-8")
    if options.inventory_only:
        return 0
    results = query_osv(queries)
    python_hits = write_reports(output, python, native, results, queries, timestamp, runtime, skipped)
    print((output / "summary.md").read_text(encoding="utf-8"))
    return 1 if python_hits else 0


if __name__ == "__main__":
    sys.exit(main())

