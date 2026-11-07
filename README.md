# MultiChat

Twitch-, Kick- und YouTube-Livechat kombiniert in einem Fenster – mit Video-Kacheln zum Mitschauen, Emotes, Filter, Login zum Schreiben und Moderieren und einem OBS-Overlay.

## Installation

Für die Video-Kacheln brauchst du **libmpv** und für YouTube-Videos eine **JavaScript-Laufzeit** (Deno oder Node.js):

```bash
# Ubuntu/Debian
sudo apt install libmpv2 nodejs
# Fedora (siehe auch unten: RPM Fusion für Twitch/Kick)
sudo dnf install mpv-libs nodejs fuse-libs xcb-util-cursor
# Arch
sudo pacman -S mpv nodejs
```

Chat, Moderation und Overlay funktionieren auch ohne beides.

### Fedora: vollständiges FFmpeg (für Twitch und Kick)

Fedora liefert nur `ffmpeg-free` ohne vollwertigen H.264-Decoder. Twitch und Kick senden H.264, die Kacheln blieben sonst schwarz. Mit RPM Fusion tauschst du es aus:

```bash
sudo dnf install \
  https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
sudo dnf swap ffmpeg-free ffmpeg --allowerasing
```

Optional für Hardware-Dekodierung: AMD `sudo dnf swap mesa-va-drivers mesa-va-drivers-freeworld`, Intel `sudo dnf install intel-media-driver`.

```bash
git clone https://github.com/Corrgan/multichat.git
cd multichat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## AppImage bauen

```bash
cd multichat
./build-appimage.sh
./MultiChat-x86_64.AppImage
```

Das Skript legt eine eigene Python-Umgebung in `build-appimage/` an, bündelt die App mit PyInstaller und packt sie mit `appimagetool` (wird automatisch geladen). Ergebnis: eine einzelne Datei mit ca. 90 MB.

- **libmpv** kommt standardmäßig vom System (siehe Installation). Das ist klein und passt sicher zu deinen Grafiktreibern. Mit `MPV_BUNDLE=1 ./build-appimage.sh` wird libmpv samt FFmpeg mitgebündelt (~270 MB, läuft dann ohne installiertes mpv).

- Die AppImage läuft auf Systemen mit **mindestens der glibc-Version des Build-Rechners**. Also auf der ältesten Distro bauen, die du unterstützen willst.
- Falls FUSE fehlt (`dlopen(): error loading libfuse.so.2`): `sudo apt install libfuse2` bzw. `libfuse2t64`, oder mit `--appimage-extract-and-run` starten.
- Ins Startmenü bekommst du sie z. B. mit [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) oder AppImageLauncher.

## Benutzung

- **Twitch:** Kanalname, z. B. `kanalname`
- **Kick:** Kanalname (Slug), z. B. `kanalname`
- **YouTube:** Kanal, z. B. `@kanalname`, `https://www.youtube.com/@kanalname` oder eine `UC…`-Kanal-ID. Die App prüft jede Minute, ob der Kanal live ist, verbindet sich automatisch mit dem laufenden Stream und wartet nach Stream-Ende auf den nächsten. Eine Video-URL/-ID geht weiterhin, wenn du einen bestimmten Stream willst.
- **Anzeigen:** Häkchen blenden Plattformen in der Ansicht ein/aus.

### Video-Kacheln

Links der Chat, rechts eine Video-Kachel pro eingetragenem Kanal. Sie starten mit „Verbinden“.

- **Ton:** Immer nur eine Kachel ist laut (blauer Rahmen). Klick ins Video oder aufs Lautsprecher-Symbol schaltet den Ton dorthin, ein zweiter Klick macht alles stumm.
- **Qualität** pro Kachel: 360p, 480p (Standard), 720p oder Quelle. Drei Streams in 480p brauchen zusammen etwa 5–8 Mbit/s.
- **Offline:** Die Kachel zeigt „offline“ und prüft jede Minute erneut. Nach Stream-Ende sucht sie automatisch weiter; bei YouTube findet sie so auch den nächsten Livestream des Kanals.
- **Neu laden** (Pfeil-Symbol), falls ein Stream hängt.
- **Ansicht → Video-Kacheln** blendet die Spalte aus und stoppt die Streams (spart Bandbreite). Den Trenner zwischen Chat und Videos kannst du verschieben.
- Die Videos laufen ein paar Sekunden hinter dem Live-Signal, wie im Browser-Player.
- Twitch und YouTube werden über `yt-dlp` geladen, Kick direkt über die Kick-API. Wenn eine Plattform etwas ändert, hilft oft `pip install -U yt-dlp` (bzw. AppImage neu bauen).
- **Klick auf einen Namen** öffnet das Menü: erwähnen, Text kopieren, Nachricht löschen, Timeout (1 Min./10 Min./1 Std.), Bannen.
- **Sendezeile unten:** an eine Plattform oder an „Alle“ angemeldeten schreiben.
- Einstellungen: `~/.config/multichat/config.json`, Logins: `~/.config/multichat/tokens.json` (beide nur für dich lesbar).

Lesen funktioniert ohne Login. Zum Schreiben und Moderieren musst du angemeldet sein **und** auf dem Kanal Mod-Rechte haben (oder es ist dein eigener Kanal).

## Login einrichten (einmalig)

Jede Plattform verlangt eine eigene, kostenlose Entwickler-App. Bei allen drei trägst du als Redirect-/Callback-URL genau ein:

```
http://localhost:17563/callback
```

Dann in MultiChat unter **Konten → Entwickler-Apps einrichten …** Client-ID und Secret eintragen und **Konten → Bei … anmelden** klicken. Der Browser öffnet sich, du bestätigst, fertig.

### Twitch
1. https://dev.twitch.tv/console/apps → „Register Your Application“
2. OAuth Redirect URL: `http://localhost:17563/callback`, Kategorie: „Chat Bot“, Client-Typ: **Confidential**
3. Client-ID kopieren, „New Secret“ erzeugen

### Kick
1. https://kick.com/settings/developer → App erstellen (2FA muss aktiv sein)
2. Redirect URL: `http://localhost:17563/callback`
3. Scopes anhaken: *user:read, channel:read, chat:write, moderation:ban, moderation:chat_message:manage*
4. Client-ID und Secret kopieren

### YouTube (Google)
1. https://console.cloud.google.com → Projekt anlegen → „YouTube Data API v3“ aktivieren
2. „OAuth-Zustimmungsbildschirm“ einrichten (Typ *Extern*, dich selbst als Testnutzer eintragen)
3. Anmeldedaten → „OAuth-Client-ID“ → Typ **Webanwendung**, autorisierte Weiterleitungs-URI: `http://localhost:17563/callback`
4. Client-ID und Secret kopieren

Hinweis: Solange die Google-App im Testmodus ist, läuft der Login nach 7 Tagen ab, dann einfach neu anmelden. Senden und Moderieren über die YouTube-API kostet Kontingent (Standard 10 000 Einheiten/Tag, eine Nachricht ≈ 200), für normale Moderation reicht das.

## OBS-Overlay

1. **Overlay → OBS-Overlay aktiv** anhaken
2. **Overlay → Overlay-URL kopieren**
3. In OBS: Quelle hinzufügen → **Browser** → URL einfügen, Breite/Höhe z. B. 500 × 800

Die URL lässt sich anpassen:

| Parameter | Bedeutung | Beispiel |
|---|---|---|
| `fade` | Nachrichten nach X Sekunden ausblenden (0 = nie) | `fade=30` |
| `size` | Schriftgröße in px | `size=24` |
| `max` | maximale Anzahl Nachrichten | `max=20` |
| `platforms` | nur bestimmte Plattformen | `platforms=twitch,kick` |
| `badges` | Plattform-Kürzel aus | `badges=0` |

Beispiel: `http://127.0.0.1:17564/?fade=30&size=24&platforms=twitch,youtube`

Gelöschte Nachrichten und gebannte Nutzer verschwinden auch aus dem Overlay.

## Fehlersuche / Log

- **Hilfe → Log kopieren** legt die letzten 300 Zeilen in die Zwischenablage, zum Einfügen in einen Chat oder ein Issue.
- **Hilfe → Logdatei öffnen** / **Log-Ordner öffnen**. Die Datei liegt unter `~/.local/state/multichat/multichat.log` (rotiert, max. 3 × 1 MB).
- **Hilfe → Ausführliches Log (Debug)** schreibt zusätzlich jede HTTP-Antwort, Kick-WebSocket-Ereignisse, Stream-Suche und mpv-Meldungen mit. Danach das Problem nachstellen und das Log kopieren.
- Rechtsklick auf einen Statustext (neben den Kanalfeldern oder in einer Video-Kachel) → **Status kopieren** kopiert den vollständigen Text.
- Tokens, Secrets und Login-Codes werden im Log automatisch geschwärzt.

**YouTube-Video bleibt schwarz / „DNS-Blocker“:** Pi-hole, AdGuard Home & Co. sperren oft `manifest.googlevideo.com`. Darüber liefert YouTube aber die Livestream-Playlists aus (keine Werbung). Die Kachel meldet das dann ausdrücklich. Lösung: im Blocker freigeben, z. B. Pi-hole `pihole allow manifest.googlevideo.com`, AdGuard Home `@@||manifest.googlevideo.com^`, danach `resolvectl flush-caches`. Prüfen mit `getent ahosts manifest.googlevideo.com` – dort muss eine echte IP stehen, nicht `0.0.0.0`.

## Hinweise

- **Twitch** liest über den anonymen IRC-Zugang, Senden/Moderieren läuft über die offizielle Helix-API.
- **Kick** hat im Oktober 2026 den Pusher-Schlüssel für den Chat gewechselt. Die App fragt die aktuellen Zugangsdaten deshalb beim Verbinden bei Kick ab (`web.kick.com/api/v1/realtime/connection`) und probiert bei Ablehnung automatisch den nächsten Schlüssel.
- **YouTube-Video:** Die App sucht über die Kanalseite den laufenden Livestream; abgespielt wird er von mpv über yt-dlp (mpvs `ytdl_hook`, der auch getrennte Bild-/Tonspuren und DASH-Livestreams kann). yt-dlp ist in der App enthalten – `MultiChat-x86_64.AppImage --yt-dlp …` verhält sich wie das yt-dlp-Programm. Für YouTube braucht yt-dlp Node.js oder Deno.
- **Kick** liest über die inoffiziellen Website-Endpunkte (Chatroom-ID von `kick.com/api/v2`, hinter Cloudflare, daher `curl_cffi`). Senden/Moderieren nutzt die offizielle Kick-API. Wenn Kick die Website ändert, kann das Lesen brechen.
- **YouTube** liest über `pytchat` (inoffiziell, ggf. `pip install -U pytchat`), Senden/Moderieren über die offizielle YouTube Data API.
- Emotes: Twitch-, Kick- und YouTube-eigene Emotes werden als Bilder angezeigt, dazu **7TV, BetterTTV und FrankerFaceZ** (Menü *Ansicht → 7TV-/BTTV-/FFZ-Emotes*):
  - globale Sets gelten für alle drei Plattformen
  - Kanal-Emotes: Twitch-Kanäle bekommen 7TV + BTTV + FFZ, Kick-Kanäle 7TV (falls der Streamer dort einen 7TV-Account verknüpft hat)
  - bei gleichem Namen gilt: Kanal vor global, 7TV vor BTTV vor FFZ
  - Kanal-Emotes werden beim Verbinden nachgeladen, die ersten Sekunden Chat erscheinen daher evtl. noch als Text
  - im App-Fenster sind animierte Emotes Standbilder, im OBS-Overlay bewegen sie sich
  - fehlt Qt das WebP-Plugin (manche Distro-Pakete), nimmt die App PNG/GIF; sonst `sudo apt install qt6-image-formats-plugins` bzw. das pip-Paket nutzen
- Unter Wayland kann „Immer im Vordergrund“ je nach Compositor ignoriert werden. Die Video-Kacheln laufen unter X11 und Wayland.
- Startet die App unter X11 nicht („xcb platform plugin“): `sudo apt install libxcb-cursor0`.

## Lizenz

MultiChat ist freie Software unter der **GNU General Public License v3.0 oder später** (GPL-3.0-or-later), siehe [`LICENSE`](LICENSE).

Verwendete Bibliotheken, Werkzeuge und Dienste mit ihren Lizenzen stehen in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

MultiChat ist ein unabhängiges Projekt und steht in keiner Verbindung zu Twitch, Kick, YouTube/Google, 7TV, BetterTTV oder FrankerFaceZ. Die Namen sind Marken ihrer jeweiligen Inhaber.

