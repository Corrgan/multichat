#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
# Builds MultiChat-<arch>.AppImage in the repository root.
#
#   packaging/build-appimage.sh
#
# Requirements: python3 (with the venv module), curl, ~1 GB of free space.
# The video tiles need libmpv on the machine the app runs on:
#   Ubuntu/Debian: sudo apt install libmpv2   Fedora: sudo dnf install mpv-libs
#   Arch: sudo pacman -S mpv
# Bundle everything (no system requirement, ~270 MB):  MPV_BUNDLE=1 packaging/build-appimage.sh
# Tip: build on the oldest distribution you want to support – the AppImage then runs
# on all newer ones too (it needs at least the glibc of the build system).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."  # repository root

ARCH="$(uname -m)"
[ "$ARCH" = "x86_64" ] || { echo "ERROR: only x86_64 is supported (pinned build tools)"; exit 1; }
BUILD="build-appimage"
APPDIR="$BUILD/MultiChat.AppDir"
OUT="MultiChat-$ARCH.AppImage"
TOOL="$BUILD/appimagetool-$ARCH.AppImage"

# Build tools: fixed releases, verified by checksum (x86_64)
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
LINUXDEPLOY_URL="https://github.com/linuxdeploy/linuxdeploy/releases/download/1-alpha-20251107-1/linuxdeploy-x86_64.AppImage"
LINUXDEPLOY_SHA256="c20cd71e3a4e3b80c3483cef793cda3f4e990aca14014d23c544ca3ce1270b4d"
# AppImage runtime (the small program at the start of the AppImage). Without --runtime-file,
# appimagetool would download the moving "continuous" build unverified.
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64"
RUNTIME_SHA256="2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
RUNTIME="$BUILD/runtime-$ARCH"

# download <url> <sha256> <file>: (re)downloads unless the file has the expected checksum
download() {
  if ! echo "$2  $3" | sha256sum -c --status 2>/dev/null; then
    # GitHub's download servers sometimes answer 5xx for a while – retry instead of failing
    curl -fL --retry 8 --retry-all-errors --retry-delay 15 --connect-timeout 30 -o "$3.part" "$1"
    echo "$2  $3.part" | sha256sum -c --quiet || { echo "ERROR: checksum mismatch for $1"; rm -f "$3.part"; exit 1; }
    mv "$3.part" "$3"
  fi
  chmod +x "$3"
}

echo "==> Python environment"
mkdir -p "$BUILD"
# PYTHON=python3.12 packaging/build-appimage.sh uses a specific interpreter (e.g. in CI)
[ -d "$BUILD/venv" ] || "${PYTHON:-python3}" -m venv "$BUILD/venv"
# shellcheck disable=SC1091
. "$BUILD/venv/bin/activate"
pip install -q --upgrade pip
# exact, hash-checked versions of all dependencies and build tools
# (the app itself is taken from the source tree)
pip install -q --require-hashes -r packaging/requirements-lock.txt

echo "==> PyInstaller"
# python-mpv is NOT analysed (otherwise PyInstaller would bundle the build machine's
# libmpv with ~200 dependencies) but added as a plain source file.
MPV_PY="$(python3 -c 'import mpv; print(mpv.__file__)')"
pyinstaller --noconfirm --clean --windowed --name multichat \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
  --collect-all pytchat --collect-all curl_cffi --collect-submodules yt_dlp --collect-all yt_dlp_ejs --collect-all streamlink --collect-all keyring \
  --hidden-import PyQt6.QtNetwork --hidden-import PyQt6.QtOpenGLWidgets \
  --exclude-module mpv --add-data "$MPV_PY:." \
  --paths "$PWD" --add-data "$PWD/multichat/resources:multichat/resources" \
  --hidden-import queue --hidden-import concurrent.futures --hidden-import ctypes.util \
  packaging/entry.py

echo "==> Leaving system libraries to the host"
python3 packaging/prune-bundle.py "$BUILD/dist/multichat/_internal"

echo "==> AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$BUILD/dist/multichat/." "$APPDIR/usr/bin/"
APP_ID="io.github.Corrgan.MultiChat"
cp "packaging/$APP_ID.desktop" "$APPDIR/"
cp "packaging/$APP_ID.desktop" "$APPDIR/usr/share/applications/"
cp "multichat/resources/$APP_ID.png" "$APPDIR/"
cp "multichat/resources/$APP_ID.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
mkdir -p "$APPDIR/usr/share/metainfo"
cp "packaging/$APP_ID.metainfo.xml" "$APPDIR/usr/share/metainfo/"
ln -sf "$APP_ID.png" "$APPDIR/.DirIcon"

echo "==> License texts"
DOC="$APPDIR/usr/share/doc/multichat"
mkdir -p "$DOC/licenses"
cp LICENSE THIRD_PARTY_NOTICES.md README.md docs/LOGIN.md "$DOC/"
# License files of all bundled Python packages (MIT/BSD/Apache require shipping them)
python3 - "$DOC/licenses" <<'PYEOF'
import re, shutil, sys
from importlib.metadata import distributions
from pathlib import Path
out = Path(sys.argv[1])
skip = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "packaging"}
pat = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.I)
n = 0
for d in distributions():
    name = d.metadata["Name"]
    if name.lower() in skip:
        continue
    for f in d.files or []:
        if pat.search(f.name) and ".dist-info" in str(f):
            src = Path(d.locate_file(f))
            if src.is_file():
                dst = out / name / f.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst)
                n += 1
print(f"    {n} license files copied")
PYEOF
# Default: the AppImage uses the system's libmpv (small, matches the graphics drivers).
# With MPV_BUNDLE=1, libmpv and FFmpeg are bundled (runs without mpv installed, but ~270 MB).
LIBMPV=""
if [ "${MPV_BUNDLE:-0}" = "1" ]; then
  echo "==> Bundling libmpv"
  LIBMPV="$(ldconfig -p | awk '/libmpv\.so\.2 /{print $NF; exit}')"
  [ -n "$LIBMPV" ] || { echo "ERROR: libmpv not found (sudo apt install libmpv-dev)"; exit 1; }
else
  echo "==> libmpv: using the system library (MPV_BUNDLE=1 to bundle it)"
fi
if [ -n "$LIBMPV" ]; then
  LDTOOL="$BUILD/linuxdeploy-$ARCH.AppImage"
  download "$LINUXDEPLOY_URL" "$LINUXDEPLOY_SHA256" "$LDTOOL"
  # Use a separate AppDir so linuxdeploy doesn't touch the PyInstaller files
  MPVDIR="$BUILD/mpv.AppDir"
  rm -rf "$MPVDIR"
  APPIMAGE_EXTRACT_AND_RUN=1 "$LDTOOL" --appdir "$MPVDIR" --library "$LIBMPV" >/dev/null
  mkdir -p "$APPDIR/usr/lib"
  cp -a "$MPVDIR/usr/lib/." "$APPDIR/usr/lib/"
  # Graphics/video driver interfaces must come from the system
  rm -f "$APPDIR"/usr/lib/libva*.so* "$APPDIR"/usr/lib/libvdpau*.so* "$APPDIR"/usr/lib/libOpenCL*.so*
  echo "    $(ls "$APPDIR/usr/lib" | wc -l) libraries bundled"
fi

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/multichat" "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "==> appimagetool"
download "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA256" "$TOOL"
download "$RUNTIME_URL" "$RUNTIME_SHA256" "$RUNTIME"
# APPIMAGE_EXTRACT_AND_RUN: works without FUSE as well
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream --runtime-file "$RUNTIME" "$APPDIR" "$OUT"

echo
echo "Done: $(pwd)/$OUT"
