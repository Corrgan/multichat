# Third-party components and notices

MultiChat itself (all files in this repository) is licensed under the
**GNU General Public License v3.0 or later** (GPL-3.0-or-later), see [`LICENSE`](LICENSE).

MultiChat uses a number of free libraries and tools. Thanks to all of them – their
licenses apply to the respective parts. Because PyQt6 is licensed under GPL-3.0-only,
a built AppImage containing PyQt6 is distributed as a whole under the terms of the GPL-3.0.

The AppImage contains the license texts of all bundled Python packages under
`usr/share/doc/multichat/licenses/`.

## Python libraries (included in the AppImage)

| Package | License | Project |
|---|---|---|
| PyQt6 | GPL-3.0-only | https://www.riverbankcomputing.com/software/pyqt/ |
| PyQt6-Qt6 (Qt 6) | LGPL-3.0-only | https://www.qt.io/ |
| PyQt6-sip | BSD-2-Clause | https://github.com/Python-SIP/sip |
| python-mpv (`mpv`) | GPL-2.0-or-later or LGPL-2.1-or-later | https://github.com/jaseg/python-mpv |
| streamlink | BSD-2-Clause | https://github.com/streamlink/streamlink |
| yt-dlp | Unlicense | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | Unlicense AND MIT AND ISC | https://github.com/yt-dlp/ejs |
| pytchat | MIT | https://github.com/taizan-hokuto/pytchat |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| aiohttp | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| aiohappyeyeballs | PSF-2.0 | https://github.com/aio-libs/aiohappyeyeballs |
| aiosignal | Apache-2.0 | https://github.com/aio-libs/aiosignal |
| frozenlist | Apache-2.0 | https://github.com/aio-libs/frozenlist |
| multidict | Apache-2.0 | https://github.com/aio-libs/multidict |
| propcache | Apache-2.0 | https://github.com/aio-libs/propcache |
| yarl | Apache-2.0 | https://github.com/aio-libs/yarl |
| attrs | MIT | https://www.attrs.org/ |
| curl_cffi | MIT | https://github.com/lexiforest/curl_cffi |
| cffi | MIT-0 | https://cffi.readthedocs.io/ |
| pycparser | BSD-3-Clause | https://github.com/eliben/pycparser |
| requests | Apache-2.0 | https://requests.readthedocs.io |
| urllib3 | MIT | https://urllib3.readthedocs.io |
| charset-normalizer | MIT | https://github.com/jawah/charset_normalizer |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| keyring | MIT | https://github.com/jaraco/keyring |
| SecretStorage | BSD-3-Clause | https://github.com/mitya57/secretstorage |
| jeepney | MIT | https://gitlab.com/takluyver/jeepney |
| cryptography | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| jaraco.classes, jaraco.context, jaraco.functools | MIT | https://github.com/jaraco |
| more-itertools | MIT | https://github.com/more-itertools/more-itertools |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| httpx | BSD-3-Clause | https://www.python-httpx.org |
| httpcore | BSD-3-Clause | https://www.encode.io/httpcore |
| h11 | MIT | https://github.com/python-hyper/h11 |
| h2 | MIT | https://github.com/python-hyper/h2 |
| hpack | MIT | https://github.com/python-hyper/hpack |
| hyperframe | MIT | https://github.com/python-hyper/hyperframe |
| anyio | MIT | https://github.com/agronholm/anyio |
| typing_extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| brotli | MIT | https://github.com/google/brotli |
| pycryptodomex | BSD-2-Clause, partly public domain | https://www.pycryptodome.org |
| mutagen | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |
| isodate | BSD-3-Clause | https://github.com/gweis/isodate |
| lxml | BSD-3-Clause | https://lxml.de/ |
| pycountry | LGPL-2.1-only | https://github.com/pycountry/pycountry |
| pycryptodome | BSD-2-Clause, partly public domain | https://www.pycryptodome.org |
| PySocks | BSD-3-Clause | https://github.com/Anorov/PySocks |
| trio | MIT OR Apache-2.0 | https://github.com/python-trio/trio |
| trio-websocket | MIT | https://github.com/python-trio/trio-websocket |
| outcome | MIT OR Apache-2.0 | https://github.com/python-trio/outcome |
| sniffio | MIT OR Apache-2.0 | https://github.com/python-trio/sniffio |
| sortedcontainers | Apache-2.0 | https://github.com/grantjenks/python-sortedcontainers |
| wsproto | MIT | https://github.com/python-hyper/wsproto |
| websocket-client | Apache-2.0 | https://github.com/websocket-client/websocket-client |

## Other components of the AppImage

| Component | License |
|---|---|
| Python runtime (CPython) | PSF-2.0 |
| Qt 6 (via PyQt6-Qt6) | LGPL-3.0-only |
| curl-impersonate / libcurl and BoringSSL (in curl_cffi) | MIT or curl license, BoringSSL: ISC/OpenSSL |
| PyInstaller bootloader | GPL-2.0-or-later with bootloader exception |
| AppImage runtime | MIT |
| other system libraries of the build machine | their respective licenses |

If the AppImage is built with `MPV_BUNDLE=1`, it additionally contains **libmpv**
(GPL-2.0-or-later or LGPL-2.1-or-later, depending on the build) and **FFmpeg**
(LGPL-2.1-or-later or GPL-2.0-or-later, depending on the build) including their dependencies.

## Additional components of the Flatpak

The Flatpak uses the KDE runtime and the PyQt BaseApp from Flathub (Qt, PyQt6, Python and
system libraries under their respective licenses) and builds the following itself:

| Component | License |
|---|---|
| [mpv / libmpv](https://mpv.io/) (GPL build) | GPL-2.0-or-later |
| [FFmpeg](https://ffmpeg.org/) (built with `--enable-gpl --enable-version3`) | GPL-3.0-or-later |
| [libplacebo](https://code.videolan.org/videolan/libplacebo) | LGPL-2.1-or-later |
| [libass](https://github.com/libass/libass) | ISC |
| [LuaJIT](https://github.com/openresty/luajit2) | MIT |
| [libdisplay-info](https://gitlab.freedesktop.org/emersion/libdisplay-info) | MIT |
| [hwdata](https://github.com/vcrhonek/hwdata) | GPL-2.0-or-later |
| [Deno](https://deno.com/) | MIT |

## Used at runtime, not included in the AppImage

| Component | License | Purpose |
|---|---|---|
| [mpv / libmpv](https://mpv.io/) | GPL-2.0-or-later or LGPL-2.1-or-later | video playback |
| [FFmpeg](https://ffmpeg.org/) | LGPL-2.1-or-later or GPL-2.0-or-later | video decoding (via libmpv) |
| [Node.js](https://nodejs.org/) or [Deno](https://deno.com/) | MIT | JavaScript runtime for yt-dlp (YouTube) |

## Build tools

| Tool | License |
|---|---|
| [PyInstaller](https://pyinstaller.org) | GPL-2.0-or-later with bootloader exception |
| [appimagetool](https://github.com/AppImage/appimagetool) | MIT |
| [linuxdeploy](https://github.com/linuxdeploy/linuxdeploy) (only with `MPV_BUNDLE=1`) | MIT |

## Services and trademarks

MultiChat is an independent project and is not affiliated with, endorsed by or reviewed by
the following providers. Twitch, Kick, YouTube, Google, 7TV, BetterTTV, FrankerFaceZ,
OBS Studio and Pusher are names or trademarks of their respective owners and are used only
to identify the supported services.

- **Interfaces:** MultiChat connects to the services of Twitch, Kick, YouTube/Google, 7TV,
  BetterTTV and FrankerFaceZ. Their use is subject to the respective providers' terms of
  service. Some interfaces (Kick website, YouTube live chat and video) are unofficial and
  may change at any time.
- **Content:** chat messages, emotes, videos and other content are loaded from the services
  at runtime and are not distributed with MultiChat. The rights remain with their respective
  creators.
- **Platform icons:** the small icons in front of chat messages are the favicons of the
  respective websites (or from Google's favicon service), which MultiChat loads at runtime
  like a browser to indicate where a message comes from. They are not part of this
  repository or the AppImage and remain trademarks of their owners.
- **Login:** login and moderation use the user's own developer apps on the platforms.
  MultiChat does not ship any credentials.
