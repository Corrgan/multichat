# MultiChat

Twitch, Kick and YouTube live chat combined in one window for Linux – with video tiles to watch along, emotes, filters, login for sending and moderating, and an OBS overlay.

## Download

Under [Releases](https://github.com/Corrgan/multichat/releases) there is a **Flatpak** and an **AppImage**, both built automatically by GitHub Actions, with SHA256 checksums.

| | Flatpak | AppImage |
|---|---|---|
| Brings along | everything: Qt, mpv, FFmpeg, Deno for YouTube | Python, Qt and the app – everything else comes from the system |
| You need | Flatpak with Flathub (fetches the KDE runtime once) | a desktop distribution with glibc ≥ 2.35 and OpenSSL 3, plus **libmpv** and **Node.js or Deno** for video (see below) |
| Good for | SteamOS, Bazzite, Fedora Atomic and other immutable systems; anyone who wants no setup | regular distributions (Ubuntu 22.04+, Debian 12+, Fedora 36+, Arch) – uses the system's graphics drivers, fonts, codecs and CA certificates |
| Settings | `~/.var/app/io.github.Corrgan.MultiChat/` | `~/.config/multichat/` |

### Flatpak

```bash
flatpak install --user MultiChat-x86_64.flatpak
flatpak run io.github.Corrgan.MultiChat
```

On many desktops a double-click on the file opens it in the software centre (e.g. Discover). Updates: install the file of the newer release the same way.

### AppImage

```bash
chmod +x MultiChat-x86_64.AppImage
sha256sum -c MultiChat-x86_64.AppImage.sha256   # optional: verify the download
./MultiChat-x86_64.AppImage
```

`./MultiChat-x86_64.AppImage --selftest` checks that all components (Streamlink, yt-dlp, libmpv, translations, CA certificates …) can be loaded; `--selftest-net` makes one HTTPS request, `--selftest-video FILE` decodes a video with libmpv.

### Verifying a download

Every release file comes with a SHA256 checksum and a **build provenance attestation** signed by GitHub: it proves the file was built by this repository's workflow from a specific commit.

```bash
gh attestation verify MultiChat-x86_64.AppImage --repo Corrgan/multichat
```

The AppImage deliberately leaves system libraries (C++ runtime, GLib, fonts, X11/Wayland, OpenSSL …) to the host: the system's libmpv needs the system's versions of them, and the system's OpenSSL knows where the CA certificates are. Every desktop installation has these libraries; only the two packages below usually have to be installed.

## Requirements (AppImage only)

The video tiles need **libmpv**, YouTube video additionally needs a **JavaScript runtime** (Node.js or Deno). Chat, moderation and the overlay work without them.

```bash
# Ubuntu/Debian
sudo apt install libmpv2 nodejs
# Fedora (see below: RPM Fusion for Twitch/Kick)
sudo dnf install mpv-libs nodejs
# Arch
sudo pacman -S mpv nodejs
```

On immutable systems (SteamOS, Fedora Atomic …) libmpv usually can't be installed – use the Flatpak there.

### Fedora: full FFmpeg (AppImage, for Twitch and Kick)

Not needed for the Flatpak. Fedora only ships `ffmpeg-free`, which lacks a full H.264 decoder. Twitch and Kick send H.264, so the tiles would stay black. Swap it via RPM Fusion:

```bash
sudo dnf install \
  https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
sudo dnf swap ffmpeg-free ffmpeg --allowerasing
```

Optional, for hardware decoding: AMD `sudo dnf swap mesa-va-drivers mesa-va-drivers-freeworld`, Intel `sudo dnf install intel-media-driver`.

## Running from source

```bash
git clone https://github.com/Corrgan/multichat.git
cd multichat
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
multichat            # or: python -m multichat
```

## Building the AppImage yourself

```bash
cd multichat
packaging/build-appimage.sh
./MultiChat-x86_64.AppImage
```

The script creates its own Python environment in `build-appimage/` with the exact, hash-checked versions from `packaging/requirements-lock.txt`, bundles the app with PyInstaller and packs it with a pinned `appimagetool` release (downloaded automatically and verified by checksum). The result is a single file of about 100 MB; `packaging/prune-bundle.py` removes the build machine's system libraries from it (see above). `PYTHON=python3.12 packaging/build-appimage.sh` selects a specific Python interpreter.

- **libmpv** is taken from the system by default (see Requirements) – small, and guaranteed to match your graphics drivers. `MPV_BUNDLE=1 packaging/build-appimage.sh` bundles libmpv and FFmpeg (~270 MB, then runs without mpv installed).
- The AppImage runs on systems with **at least the glibc version of the build machine**, so build on the oldest distribution you want to support.
- If FUSE is missing (`dlopen(): error loading libfuse.so.2`): `sudo apt install libfuse2` or `libfuse2t64`, or start it with `--appimage-extract-and-run`.
- To add it to your application menu, use e.g. [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) or AppImageLauncher.

### Automatic builds (GitHub Actions)

- [`tests.yml`](.github/workflows/tests.yml) runs the test suite on every push and pull request (including Dependabot's) and checks that the Flatpak's Python packages match the lock file.
- [`build.yml`](.github/workflows/build.yml) builds the AppImage in an Ubuntu 22.04 container and the Flatpak with `flatpak-builder` ([`packaging/flatpak/`](packaging/flatpak)). Both run `--selftest`, `--selftest-net` (an HTTPS request) and `--selftest-video` (decodes a short clip with libmpv); the AppImage additionally in Fedora, Debian and Arch containers.
- [`flatpak-upstream.yml`](.github/workflows/flatpak-upstream.yml) compares the Flatpak's self-built libraries (mpv, FFmpeg …) with Flathub's mpv package every week and opens an issue when they fall behind.


- **Manual:** Actions → *Build* → *Run workflow* → AppImage and Flatpak are attached to the run as artifacts (14 days).
- **Release:** bump `__version__` in `multichat/__init__.py` (and add the release to `packaging/io.github.Corrgan.MultiChat.metainfo.xml`), commit, then `git tag v<version> && git push origin v<version>` – the workflow creates the release with both files and checksums. If the tag doesn't match `__version__` or the tagged commit isn't on `main`, it stops with an error – tag only on `main`.
- **Which tags create a release:** only `vX.Y` where the second number is a single digit or ends in 0 – e.g. `v0.10`, `v0.20`, `v1.6`, `v2.0`. Intermediate versions such as `v0.11`, `v0.15` or `v0.10.1` don't trigger a build; one-off exceptions are listed in `EXTRA_RELEASES` in the workflow (currently `v0.25`, the first release with the Flatpak).

## Project layout

```
multichat/            the application (Python package)
  app.py              start-up, --selftest, --yt-dlp
  core/               configuration, paths, logging, translations
  chat/               chat readers per platform, 7TV/BTTV/FFZ emotes
  accounts/           logins (OAuth), sending and moderating per platform
  video/              stream lookup (Streamlink, yt-dlp, Kick API) and the mpv video tiles
  overlay/            local web server for the OBS overlay
  ui/                 main window, chat view, dialogs
  resources/          icon, overlay page, translations (locales/)
packaging/            AppImage build script, Flatpak manifest, .desktop and metainfo files
tests/                automated tests (pytest)
docs/                 additional documentation
```

## Usage

- **Twitch:** channel name, e.g. `channelname`
- **Kick:** channel name (slug), e.g. `channelname`
- **YouTube:** channel, e.g. `@channelname`, `https://www.youtube.com/@channelname` or a `UC…` channel ID. The app checks every minute whether the channel is live, connects to the running stream automatically and waits for the next one after the stream ends. A video URL/ID still works if you want a specific stream.
- **Connect / Disconnect** starts or stops all chats and video tiles; the ▶/■ button next to each channel connects or disconnects just that one (chat and video).
- **Show:** checkboxes hide or show platforms in the chat view.
- Each message starts with the platform's icon (the website's favicon, as in a browser tab). If it can't be loaded, a coloured abbreviation (TW/KI/YT) is shown instead.
- **Click on a name** to open the menu: mention, copy text, delete message, timeout (1 min / 10 min / 1 h), ban.
- **Input line at the bottom:** send to one platform or to all you're logged in to.
- Settings: `~/.config/multichat/config.json` (readable only by you).
- Logins and the client secrets of your developer apps are stored in the system keyring (KWallet, GNOME Keyring, KeePassXC … via the Secret Service API). Without one, they go to `tokens.json` / `apps.json` next to the settings, readable only by you. `MULTICHAT_SECRETS=file` forces the files; the log and `--selftest` show which storage is used. Data from older versions is moved into the keyring automatically.

Reading works without logging in. To send and moderate you need to be logged in **and** have moderator rights on the channel (or it's your own channel).

### Language

The interface is available in English, German, French, Spanish, Japanese and Chinese (simplified). By default it follows the system language; **View → Language** switches it (takes effect after a restart). Log messages stay in English.

The French, Spanish, Japanese and Chinese translations were not done by native speakers – corrections are very welcome: edit the file in [`multichat/resources/locales/`](multichat/resources/locales) and open a pull request.

**Help → About MultiChat** shows the version, the project link and the licenses.

### Video tiles

Chat on the left, one video tile per configured channel on the right. They start with **Connect**.

- **Audio:** only one tile plays sound at a time (blue border). Click into the video or on the speaker icon to move the sound there; a second click mutes everything.
- **Quality** per tile: 360p, 480p (default), 720p or Source. Three streams at 480p need about 5–8 Mbit/s in total.
- **Offline:** the tile shows "offline" and checks again every minute. After a stream ends it keeps looking; for YouTube this also finds the channel's next live stream.
- **Sound:** the speaker button (or a click on the video) picks the tile you hear; the slider next to it sets that tile's volume (saved).
- **Reload** (arrow icon) if a stream hangs; **stop/start** (■/▶) pauses just that tile.
- **View → Video tiles** hides the column and stops the streams (saves bandwidth). The splitter between chat and videos can be dragged.
- Videos run a few seconds behind the live signal, just like the browser player.
- **Twitch** plays through [Streamlink](https://streamlink.github.io/) with ad filtering: ad breaks are skipped (the tile then shows *skipping ad break* and the picture pauses briefly) instead of corrupting the picture mid-stream.
- **YouTube** is loaded via `yt-dlp`, **Kick** directly via the Kick API. If a platform changes something, `pip install -U yt-dlp streamlink` (or rebuilding the AppImage) often helps.

## Login setup (one-time)

To send and moderate you need your own free developer app per platform (client ID + secret).
For all three the redirect URL is exactly `http://localhost:17563/callback`.

**➜ Step-by-step guide with every field and option: [docs/LOGIN.md](docs/LOGIN.md)**

In short:

| Platform | Where | Key settings |
|---|---|---|
| Twitch | [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) | Category *Chat Bot*, Client Type **Confidential**, 2FA required |
| Kick | [kick.com/settings/developer](https://kick.com/settings/developer) | Scopes `user:read`, `channel:read`, `chat:write`, `moderation:ban`, `moderation:chat_message:manage`, webhooks off, 2FA required |
| YouTube | [console.cloud.google.com](https://console.cloud.google.com/) | Enable YouTube Data API v3, audience *External* + yourself as test user, OAuth client type **Web application** |

Then in MultiChat: **Accounts → Set up developer apps …** → enter client ID and secret → **Accounts → Log in to …**.

## OBS overlay

1. Tick **Overlay → OBS overlay active**
2. **Overlay → Copy overlay URL**
3. In OBS: add source → **Browser** → paste the URL, width/height e.g. 500 × 800

The URL can be customised:

| Parameter | Meaning | Example |
|---|---|---|
| `fade` | hide messages after X seconds (0 = never) | `fade=30` |
| `size` | font size in px | `size=24` |
| `max` | maximum number of messages | `max=20` |
| `platforms` | only these platforms | `platforms=twitch,kick` |
| `badges` | hide platform icons | `badges=0` |

Example: `http://127.0.0.1:17564/?fade=30&size=24&platforms=twitch,youtube`

Deleted messages and banned users disappear from the overlay as well.

The overlay only answers requests from this computer (`127.0.0.1` / `localhost`); other websites open in your browser cannot read the chat through it. Use the URL as copied – addresses such as the machine's LAN IP are rejected.

## Troubleshooting / log

- **Help → Copy log** puts the last 300 lines on the clipboard, ready to paste into a chat or an issue.
- **Help → Open log file / Open log folder**. The file is `~/.local/state/multichat/multichat.log` – for the Flatpak `~/.var/app/io.github.Corrgan.MultiChat/.local/state/multichat/multichat.log` (rotated, max. 3 × 1 MB).
- **Help → Verbose log (debug)** additionally records every HTTP response, Kick WebSocket events, stream lookups and mpv messages. Reproduce the problem afterwards, then copy the log.
- Right-click a status text (next to the channel fields or in a video tile) → **Copy status** copies the full text.
- Tokens, secrets and login codes are redacted in the log automatically.
- If the app crashes, `coredumpctl info multichat` (Fedora, systemd) shows a backtrace that helps with debugging.
- Hardware decoding problems with the videos: start with `MULTICHAT_HWDEC=no`, e.g. `MULTICHAT_HWDEC=no ./MultiChat-x86_64.AppImage` or `flatpak run --env=MULTICHAT_HWDEC=no io.github.Corrgan.MultiChat`.
- AppImage: "libmpv could not be loaded" is followed by the reason from the dynamic linker. "TLS certificates" in the log shows which CA certificates are used.

**YouTube video stays black / "DNS blocker":** Pi-hole, AdGuard Home & co. often block `manifest.googlevideo.com`, but YouTube serves its live-stream playlists from there (no ads). The tile reports this explicitly. Fix: allow it in the blocker, e.g. Pi-hole `pihole allow manifest.googlevideo.com`, AdGuard Home `@@||manifest.googlevideo.com^`, then `resolvectl flush-caches`. Check with `getent ahosts manifest.googlevideo.com` – it must show a real IP, not `0.0.0.0`.

## Notes

- **Update check:** at start the app asks GitHub once whether a newer release exists (one request to `api.github.com`, nothing else is sent) and shows it in the menu bar. Switch it off under **Help → Check for new versions at start**.
- **Settings file:** if `config.json` is damaged, it is kept as `config.json.bad-<time>` and the app starts with defaults instead of overwriting it; invalid single values are ignored.
- **Twitch** chat is read via anonymous IRC; sending and moderating use the official Helix API.
- **Kick** rotated the Pusher key for its chat in October 2026. The app therefore asks Kick for the current credentials when connecting (`web.kick.com/api/v1/realtime/connection`) and automatically tries the next key if one is rejected.
- **Kick** chat is read via the unofficial website endpoints (chatroom ID from `kick.com/api/v2`, behind Cloudflare, hence `curl_cffi`). Sending and moderating use the official Kick API. If Kick changes its website, reading may break.
- **YouTube** chat is read directly from YouTube's web client API (like the chat in the browser, no API key or quota; also shows deleted messages and bans). If YouTube changes something and this fails, the app falls back to `pytchat` on its own; `MULTICHAT_YT_CHAT=pytchat` or `=innertube` forces one of them. Sending and moderating use the official YouTube Data API.
- **YouTube video:** the app finds the running live stream via the channel page; mpv plays it via yt-dlp (mpv's `ytdl_hook`, which also handles separate video/audio tracks and DASH live streams). yt-dlp is included in the app – `MultiChat-x86_64.AppImage --yt-dlp …` behaves like the yt-dlp program. For YouTube, yt-dlp needs Node.js or Deno (included in the Flatpak).
- Emotes: Twitch, Kick and YouTube emotes are shown as images, plus **7TV, BetterTTV and FrankerFaceZ** (menu **View → 7TV / BTTV / FFZ emotes**):
  - global sets apply to all three platforms
  - channel emotes: Twitch channels get 7TV + BTTV + FFZ, Kick channels 7TV (if the streamer has linked a 7TV account)
  - for identical names: channel before global, 7TV before BTTV before FFZ
  - channel emotes are loaded when connecting, so the first seconds of chat may still show text
  - animated emotes (GIF/WebP) play in the app window and in the OBS overlay
  - if Qt lacks the WebP plugin (some distribution packages), the app uses PNG/GIF; otherwise install `qt6-image-formats-plugins` or use the pip package
- On Wayland, **Always on top** may be ignored depending on the compositor. The video tiles work on X11 and Wayland.

## License

MultiChat is free software under the **GNU General Public License v3.0 or later** (GPL-3.0-or-later), see [`LICENSE`](LICENSE).

The libraries, tools and services used, with their licenses, are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

MultiChat is an independent project and is not affiliated with Twitch, Kick, YouTube/Google, 7TV, BetterTTV or FrankerFaceZ. The names are trademarks of their respective owners.
