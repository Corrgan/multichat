# Drittkomponenten und Hinweise

MultiChat selbst (alle Dateien in diesem Repository) steht unter der
**GNU General Public License v3.0 oder später** (GPL-3.0-or-later), siehe [`LICENSE`](LICENSE).

MultiChat nutzt eine Reihe freier Bibliotheken und Werkzeuge. Ihnen gilt der Dank,
und ihre Lizenzen gelten jeweils für die betreffenden Teile. Weil PyQt6 unter
GPL-3.0-only steht, wird eine gebaute AppImage, die PyQt6 enthält, insgesamt unter
den Bedingungen der GPL-3.0 weitergegeben.

Die AppImage enthält die Lizenztexte aller gebündelten Python-Pakete unter
`usr/share/doc/multichat/licenses/`.

## Python-Bibliotheken (in der AppImage enthalten)

| Paket | Lizenz | Projekt |
|---|---|---|
| PyQt6 | GPL-3.0-only | https://www.riverbankcomputing.com/software/pyqt/ |
| PyQt6-Qt6 (Qt 6) | LGPL-3.0-only | https://www.qt.io/ |
| PyQt6-sip | BSD-2-Clause | https://github.com/Python-SIP/sip |
| python-mpv (`mpv`) | GPL-2.0-or-later oder LGPL-2.1-or-later | https://github.com/jaseg/python-mpv |
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
| pycryptodomex | BSD-2-Clause, teilweise Public Domain | https://www.pycryptodome.org |
| mutagen | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |

## Weitere Bestandteile der AppImage

| Komponente | Lizenz |
|---|---|
| Python-Laufzeit (CPython) | PSF-2.0 |
| Qt 6 (über PyQt6-Qt6) | LGPL-3.0-only |
| curl-impersonate / libcurl und BoringSSL (in curl_cffi) | MIT bzw. curl-Lizenz, BoringSSL: ISC/OpenSSL |
| PyInstaller-Bootloader | GPL-2.0-or-later mit Bootloader-Ausnahme |
| AppImage-Runtime | MIT |
| weitere Systembibliotheken des Build-Rechners | jeweilige Lizenzen der Bibliotheken |

Wird die AppImage mit `MPV_BUNDLE=1` gebaut, enthält sie zusätzlich **libmpv**
(GPL-2.0-or-later bzw. LGPL-2.1-or-later, je nach Build) und **FFmpeg**
(LGPL-2.1-or-later bzw. GPL-2.0-or-later, je nach Build) samt Abhängigkeiten.

## Zur Laufzeit genutzt, nicht mitgeliefert

| Komponente | Lizenz | Zweck |
|---|---|---|
| [mpv / libmpv](https://mpv.io/) | GPL-2.0-or-later bzw. LGPL-2.1-or-later | Videowiedergabe |
| [FFmpeg](https://ffmpeg.org/) | LGPL-2.1-or-later bzw. GPL-2.0-or-later | Video-Dekodierung (über libmpv) |
| [Node.js](https://nodejs.org/) oder [Deno](https://deno.com/) | MIT | JavaScript-Laufzeit für yt-dlp (YouTube) |

## Werkzeuge beim Bauen

| Werkzeug | Lizenz |
|---|---|
| [PyInstaller](https://pyinstaller.org) | GPL-2.0-or-later mit Bootloader-Ausnahme |
| [appimagetool](https://github.com/AppImage/appimagetool) | MIT |
| [linuxdeploy](https://github.com/linuxdeploy/linuxdeploy) (nur mit `MPV_BUNDLE=1`) | MIT |

## Dienste und Marken

MultiChat ist ein unabhängiges Projekt und steht in keiner Verbindung zu den
folgenden Anbietern; es wird von ihnen weder unterstützt noch geprüft.
Twitch, Kick, YouTube, Google, 7TV, BetterTTV, FrankerFaceZ, OBS Studio und Pusher
sind Namen bzw. Marken ihrer jeweiligen Inhaber und werden nur verwendet, um die
unterstützten Dienste zu benennen.

- **Schnittstellen:** MultiChat verbindet sich mit den Diensten von Twitch, Kick,
  YouTube/Google, 7TV, BetterTTV und FrankerFaceZ. Für deren Nutzung gelten die
  Nutzungsbedingungen der jeweiligen Anbieter. Einige Schnittstellen (Kick-Website,
  YouTube-Livechat und -Video) sind inoffiziell und können sich jederzeit ändern.
- **Inhalte:** Chat-Nachrichten, Emotes, Videos und andere Inhalte werden zur
  Laufzeit von den Diensten geladen und nicht mit MultiChat verteilt. Die Rechte daran
  liegen bei den jeweiligen Urhebern.
- **Login:** Für Login und Moderation werden eigene Entwickler-Apps des Nutzers bei
  den Plattformen verwendet. MultiChat liefert keine Zugangsdaten mit.
