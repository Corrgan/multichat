#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
# Builds MultiChat-<arch>.AppImage from this directory.
#
#   ./build-appimage.sh
#
# Requirements: python3 (with the venv module), curl, ~1 GB of free space.
# The video tiles need libmpv on the machine the app runs on:
#   Ubuntu/Debian: sudo apt install libmpv2   Fedora: sudo dnf install mpv-libs
#   Arch: sudo pacman -S mpv
# Bundle everything (no system requirement, ~270 MB):  MPV_BUNDLE=1 ./build-appimage.sh
# Tip: build on the oldest distribution you want to support – the AppImage then runs
# on all newer ones too (it needs at least the glibc of the build system).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

ARCH="$(uname -m)"
BUILD="build-appimage"
APPDIR="$BUILD/MultiChat.AppDir"
OUT="MultiChat-$ARCH.AppImage"
TOOL="$BUILD/appimagetool-$ARCH.AppImage"

echo "==> Python environment"
mkdir -p "$BUILD"
# PYTHON=python3.12 ./build-appimage.sh uses a specific interpreter (e.g. in CI)
[ -d "$BUILD/venv" ] || "${PYTHON:-python3}" -m venv "$BUILD/venv"
# shellcheck disable=SC1091
. "$BUILD/venv/bin/activate"
pip install -q --upgrade pip
pip install -q -r requirements.txt pyinstaller

echo "==> PyInstaller"
# python-mpv is NOT analysed (otherwise PyInstaller would bundle the build machine's
# libmpv with ~200 dependencies) but added as a plain source file.
MPV_PY="$(python3 -c 'import mpv; print(mpv.__file__)')"
pyinstaller --noconfirm --clean --windowed --name multichat \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
  --collect-all pytchat --collect-all curl_cffi --collect-submodules yt_dlp --collect-all yt_dlp_ejs --collect-all streamlink \
  --hidden-import PyQt6.QtNetwork --hidden-import PyQt6.QtOpenGLWidgets \
  --exclude-module mpv --add-data "$MPV_PY:." \
  --hidden-import queue --hidden-import concurrent.futures --hidden-import ctypes.util \
  main.py

echo "==> AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$BUILD/dist/multichat/." "$APPDIR/usr/bin/"
cp multichat.desktop "$APPDIR/"
cp multichat.desktop "$APPDIR/usr/share/applications/"
cp multichat.png "$APPDIR/"
cp multichat.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
ln -sf multichat.png "$APPDIR/.DirIcon"

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
  if [ ! -x "$LDTOOL" ]; then
    curl -fL -o "$LDTOOL" \
      "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-$ARCH.AppImage"
    chmod +x "$LDTOOL"
  fi
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
if [ ! -x "$TOOL" ]; then
  curl -fL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
fi
# APPIMAGE_EXTRACT_AND_RUN: works without FUSE as well
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "Done: $(pwd)/$OUT"
