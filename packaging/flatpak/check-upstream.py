#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare all monitored native versions with Flathub's mpv manifest.

Exit 0: complete comparison, same versions. Exit 1: complete comparison, different
versions (not necessarily newer). Exit 2: unknown because input/network parsing failed.
Unsupported formats fail closed; this stdlib checker is not a general YAML parser.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Callable

OURS = Path(__file__).resolve().parent / "io.github.Corrgan.MultiChat.yml"
API = "https://api.github.com/repos/flathub/io.mpv.Mpv"
RAW = "https://raw.githubusercontent.com/flathub/io.mpv.Mpv"
MODULES = ("luajit", "libass", "ffmpeg", "libplacebo", "hwdata", "libdisplay-info", "mpv", "deno")
GIT_ORIGINS = {
    "luajit": {"https://github.com/openresty/luajit2.git"},
    "libass": {"https://github.com/libass/libass.git"},
    "ffmpeg": {"https://github.com/FFmpeg/FFmpeg.git", "https://git.ffmpeg.org/ffmpeg.git"},
    "libplacebo": {"https://code.videolan.org/videolan/libplacebo.git"},
    "mpv": {"https://github.com/mpv-player/mpv.git"},
}
ARCHIVE_URLS = {
    "hwdata": r"https://github\.com/vcrhonek/hwdata/archive/refs/tags/v(\d+(?:\.\d+)+)\.tar\.gz",
    "libdisplay-info": (
        r"https://gitlab\.freedesktop\.org/emersion/libdisplay-info/-/archive/"
        r"(\d+(?:\.\d+)+)/libdisplay-info-\1\.tar\.gz"
    ),
    "deno": (
        r"https://github\.com/denoland/deno/releases/download/v(\d+(?:\.\d+)+)/"
        r"deno-(?:x86_64|aarch64)-unknown-linux-gnu\.zip"
    ),
}
LUAJIT_INCLUDE = "shared-modules/luajit/luajit.json"
MAX_RESPONSE = 1024 * 1024


class ManifestError(ValueError):
    pass


def _scalar(value: str) -> str:
    value = re.sub(r"\s+#.*$", "", value).strip()
    if value.startswith(("'", '"')):
        if len(value) < 2 or value[-1] != value[0]:
            raise ManifestError("Unterminated quoted scalar")
        value = value[1:-1]
    if not value or re.search(r"[\s'\"{}[\]&*!|>]", value):
        raise ManifestError(f"Unsupported scalar: {value!r}")
    return value


def _json(text: str):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ManifestError(f"Duplicate JSON key: {key}")
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=unique)


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "MultiChat-native-version-check",
                                                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if getattr(response, "status", 200) != 200:
            raise ManifestError("Upstream response was not HTTP 200")
        data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ManifestError("Upstream response exceeds the size limit")
    return data.decode("utf-8")


def _sources(block: list[str]) -> list[dict[str, str]]:
    sections = [i for i, line in enumerate(block) if re.fullmatch(r"    sources:\s*(?:#.*)?", line)]
    if len(sections) != 1:
        raise ManifestError("Expected one ordinary sources list")
    entries: list[dict[str, str]] = []
    for line in block[sections[0] + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) <= 4:
            break
        first = re.fullmatch(r"      - ([A-Za-z][\w-]*):(?:[ \t]+(.*))?", line)
        field = first or re.fullmatch(r"        ([A-Za-z][\w-]*):(?:[ \t]+(.*))?", line)
        if first:
            entries.append({})
        if field:
            if not entries:
                raise ManifestError("Source field outside a list entry")
            key, value = field.groups()
            if key in {"type", "url", "tag", "commit", "sha256", "path"}:
                if key in entries[-1]:
                    raise ManifestError(f"Duplicate source field: {key}")
                entries[-1][key] = _scalar(value)
        elif re.match(r"      \S|        [^ ]", line):
            raise ManifestError("Unsupported sources structure")
    if not entries:
        raise ManifestError("Empty sources list")
    return entries


def _version(name: str, sources: list[dict]) -> str:
    candidates = []
    for source in sources:
        kind = source.get("type")
        if kind == "git":
            if source.get("url") not in GIT_ORIGINS.get(name, set()):
                raise ManifestError(f"{name}: unrecognized Git origin")
            if not re.fullmatch(r"[0-9a-f]{40}", source.get("commit", "")):
                raise ManifestError(f"{name}: missing or invalid Git commit")
            tag = source.get("tag", "")
            if not re.fullmatch(r"[vn]?\d+(?:\.\d+)+(?:[-+._][A-Za-z0-9]+)*", tag):
                raise ManifestError(f"{name}: missing or unsupported version tag")
            candidates.append(tag)
        elif kind in {"archive", "file"}:
            if "url" not in source:
                if kind == "file" and "path" in source:
                    continue  # e.g. mpv's local metainfo
                raise ManifestError(f"{name}: source lacks a URL")
            match = re.fullmatch(ARCHIVE_URLS.get(name, r"(?!)"), source["url"])
            if not match or not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
                raise ManifestError(f"{name}: unrecognized or unpinned download")
            candidates.append(match.group(1))
        elif kind == "patch" and "path" in source and "url" not in source:
            continue  # Local reviewed backports do not change the declared upstream version.
        elif kind != "shell":
            raise ManifestError(f"{name}: unsupported source type")
    if not candidates or len(set(candidates)) != 1:
        raise ManifestError(f"{name}: missing or conflicting version pins")
    if name != "deno" and len(candidates) != 1:
        raise ManifestError(f"{name}: multiple primary sources")
    return candidates[0]


def versions(manifest: str, include: Callable[[str], dict] | None = None,
             ignored_includes: tuple[str, ...] = ()) -> dict[str, str]:
    """Extract every monitored module or raise instead of silently comparing a subset."""
    lines = manifest.splitlines()
    if any("\t" in line[:len(line) - len(line.lstrip())] for line in lines):
        raise ManifestError("Tab indentation is unsupported")
    roots = [i for i, line in enumerate(lines) if re.fullmatch(r"modules:\s*(?:#.*)?", line)]
    if len(roots) != 1:
        raise ManifestError("Expected one top-level modules list")
    blocks: list[list[str]] = []
    for line in lines[roots[0] + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            if blocks:
                blocks[-1].append(line)
            continue
        if not line.startswith(" "):
            break
        if line.startswith("  - "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
        else:
            raise ManifestError("Unsupported modules structure")
    out = {}
    for block in blocks:
        for line in block[1:]:
            nested = re.fullmatch(r"\s+- name:\s*(.*)", line)
            if nested and _scalar(nested.group(1)) in MODULES:
                raise ManifestError("Nested monitored modules are unsupported")
        first = block[0][4:].strip()
        name_field = re.fullmatch(r"name:\s+(.+)", first)
        if name_field:
            name = _scalar(name_field.group(1))
            if name not in MODULES:
                continue
            if any(re.match(r"    name:", line) for line in block[1:]):
                raise ManifestError(f"{name}: duplicate module name field")
            sources = _sources(block)
        else:
            path = _scalar(first)
            if path in ignored_includes:
                continue
            if path != LUAJIT_INCLUDE or include is None:
                raise ManifestError(f"Unsupported module include: {path}")
            module = include(path)
            if not isinstance(module, dict) or module.get("name") != "luajit" or not isinstance(module.get("sources"), list):
                raise ManifestError("Unexpected LuaJIT module content")
            name, sources = module["name"], module["sources"]
            if not sources or not all(isinstance(source, dict) for source in sources):
                raise ManifestError("Invalid included sources")
        if name in out:
            raise ManifestError(f"Duplicate monitored module: {name}")
        out[name] = _version(name, sources)
    missing = set(MODULES) - out.keys()
    if missing:
        raise ManifestError("Missing monitored modules: " + ", ".join(sorted(missing)))
    return out


def upstream_versions() -> dict[str, str]:
    # Resolve one parent commit so the manifest and submodule descriptor agree.
    reference = _json(_get(f"{API}/git/ref/heads/master"))
    parent = reference.get("object", {})
    revision = parent.get("sha", "")
    if parent.get("type") != "commit" or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ManifestError("Invalid upstream branch reference")

    def include(path: str) -> dict:
        descriptor = _json(_get(f"{API}/contents/shared-modules?ref={revision}"))
        subrevision = descriptor.get("sha", "")
        if (descriptor.get("type") != "submodule"
                or descriptor.get("submodule_git_url") != "https://github.com/flathub/shared-modules.git"
                or not re.fullmatch(r"[0-9a-f]{40}", subrevision)):
            raise ManifestError("Unrecognized shared-modules provenance")
        return _json(_get(f"https://raw.githubusercontent.com/flathub/shared-modules/{subrevision}/luajit/luajit.json"))

    return versions(_get(f"{RAW}/{revision}/io.mpv.Mpv.yml"), include=include)


def main() -> int:
    try:
        ours = versions(OURS.read_text(encoding="utf-8"), ignored_includes=("python-deps.json",))
        theirs = upstream_versions()
    except (OSError, ValueError, TypeError, AttributeError) as error:
        print(f"Native library comparison UNKNOWN: {error}", file=sys.stderr)
        return 2
    different = [(name, ours[name], theirs[name]) for name in MODULES if ours[name] != theirs[name]]
    for name, local, remote in different:
        print(f"- **{name}**: we use {local!r}, Flathub's mpv uses {remote!r}")
    if not different:
        print("All monitored native library versions match Flathub's io.mpv.Mpv.")
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
