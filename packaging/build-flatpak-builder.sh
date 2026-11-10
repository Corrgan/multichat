#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the pinned host tool, including modern appstreamcli support, outside the app.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
BUILD="${FLATPAK_BUILDER_BUILD_DIR:-$PWD/build-flatpak-builder}"
mkdir -p "$BUILD"
BUILD="$(cd "$BUILD" && pwd)"
PREFIX="${FLATPAK_BUILDER_PREFIX:-$BUILD/tooling}"
mkdir -p "$PREFIX"
PREFIX="$(cd "$PREFIX" && pwd)"
ARCHIVE="$BUILD/flatpak-builder-1.4.12.tar.xz"
SHA256=8d5b791f7a8449d6a8ebcff0c8d6abd5335113f56703bf5777ee4c7db4bda378
if ! printf '%s  %s\n' "$SHA256" "$ARCHIVE" | sha256sum --check --status 2>/dev/null; then
    curl --fail --location --retry 4 --connect-timeout 30 --max-time 240 \
        https://github.com/flatpak/flatpak-builder/releases/download/1.4.12/flatpak-builder-1.4.12.tar.xz \
        --output "$ARCHIVE.part"
    printf '%s  %s\n' "$SHA256" "$ARCHIVE.part" | sha256sum --check -
    mv "$ARCHIVE.part" "$ARCHIVE"
fi
# A fresh tree prevents stale objects being reused across toolchain revisions.
WORK="$(mktemp -d "$BUILD/build-XXXXXX")"
mkdir "$WORK/source"
tar -xf "$ARCHIVE" --strip-components=1 -C "$WORK/source"
meson setup "$WORK/build" "$WORK/source" --prefix="$PREFIX" \
    --wrap-mode=nodownload -Ddocs=disabled -Dtests=false
meson compile -C "$WORK/build" -j4
meson install -C "$WORK/build"
test "$("$PREFIX/bin/flatpak-builder" --version)" = "flatpak-builder-1.4.12"
sha256sum "$PREFIX/bin/flatpak-builder" > "$BUILD/builder.sha256"
"$PREFIX/bin/flatpak-builder" --version
