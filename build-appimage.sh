#!/usr/bin/env bash
# Baut MultiChat-<arch>.AppImage aus diesem Ordner.
#
#   ./build-appimage.sh
#
# Voraussetzungen: python3 (mit venv-Modul), curl, ~1 GB freier Platz.
# Die Video-Kacheln brauchen libmpv auf dem Rechner, auf dem die App läuft:
#   Ubuntu/Debian: sudo apt install libmpv2   Fedora: sudo dnf install mpv-libs
#   Arch: sudo pacman -S mpv
# Alles mitbündeln (ohne Systemvoraussetzung, ~270 MB):  MPV_BUNDLE=1 ./build-appimage.sh
# Tipp: Auf einer möglichst alten Distro bauen – die AppImage läuft dann
# auch auf allen neueren (sie braucht mindestens die glibc des Build-Systems).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

ARCH="$(uname -m)"
BUILD="build-appimage"
APPDIR="$BUILD/MultiChat.AppDir"
OUT="MultiChat-$ARCH.AppImage"
TOOL="$BUILD/appimagetool-$ARCH.AppImage"

echo "==> Python-Umgebung"
mkdir -p "$BUILD"
[ -d "$BUILD/venv" ] || python3 -m venv "$BUILD/venv"
# shellcheck disable=SC1091
. "$BUILD/venv/bin/activate"
pip install -q --upgrade pip
pip install -q -r requirements.txt pyinstaller

echo "==> PyInstaller"
# python-mpv wird NICHT analysiert (sonst packt PyInstaller die libmpv des
# Build-Rechners samt ~200 Abhängigkeiten ein), sondern als Quelldatei beigelegt.
MPV_PY="$(python3 -c 'import mpv; print(mpv.__file__)')"
pyinstaller --noconfirm --clean --windowed --name multichat \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" \
  --collect-all pytchat --collect-all curl_cffi --collect-submodules yt_dlp --collect-all yt_dlp_ejs \
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
# Standard: Die AppImage nutzt die libmpv des Systems (klein, passt zu den
# Grafiktreibern). Mit MPV_BUNDLE=1 wird libmpv samt FFmpeg mitgebündelt
# (läuft dann auch ohne installiertes mpv, aber ~270 MB).
LIBMPV=""
if [ "${MPV_BUNDLE:-0}" = "1" ]; then
  echo "==> libmpv bündeln"
  LIBMPV="$(ldconfig -p | awk '/libmpv\.so\.2 /{print $NF; exit}')"
  [ -n "$LIBMPV" ] || { echo "FEHLER: libmpv nicht gefunden (sudo apt install libmpv-dev)"; exit 1; }
else
  echo "==> libmpv: wird vom System genutzt (MPV_BUNDLE=1 zum Mitbündeln)"
fi
if [ -n "$LIBMPV" ]; then
  LDTOOL="$BUILD/linuxdeploy-$ARCH.AppImage"
  if [ ! -x "$LDTOOL" ]; then
    curl -fL -o "$LDTOOL" \
      "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-$ARCH.AppImage"
    chmod +x "$LDTOOL"
  fi
  # In ein eigenes AppDir, damit linuxdeploy die PyInstaller-Dateien nicht anfasst
  MPVDIR="$BUILD/mpv.AppDir"
  rm -rf "$MPVDIR"
  APPIMAGE_EXTRACT_AND_RUN=1 "$LDTOOL" --appdir "$MPVDIR" --library "$LIBMPV" >/dev/null
  mkdir -p "$APPDIR/usr/lib"
  cp -a "$MPVDIR/usr/lib/." "$APPDIR/usr/lib/"
  # Grafik-/Videotreiber-Schnittstellen müssen vom System kommen
  rm -f "$APPDIR"/usr/lib/libva*.so* "$APPDIR"/usr/lib/libvdpau*.so* "$APPDIR"/usr/lib/libOpenCL*.so*
  echo "    $(ls "$APPDIR/usr/lib" | wc -l) Bibliotheken gebündelt"
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
# APPIMAGE_EXTRACT_AND_RUN: funktioniert auch ohne FUSE
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "Fertig: $(pwd)/$OUT"
