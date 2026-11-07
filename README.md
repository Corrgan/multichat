# MultiChat

Twitch, Kick and YouTube live chat combined in one window for Linux – with video tiles to watch along, emotes, filters, login for sending and moderating, and an OBS overlay.

## Download

Ready-made AppImages are available under [Releases](https://github.com/Corrgan/multichat/releases) – built automatically by GitHub Actions, with a SHA256 checksum. They run on systems with glibc ≥ 2.35 (e.g. Ubuntu 22.04+, Debian 12+, Fedora 36+, current Arch).

```bash
chmod +x MultiChat-x86_64.AppImage
sha256sum -c MultiChat-x86_64.AppImage.sha256   # optional: verify the download
./MultiChat-x86_64.AppImage
```

`./MultiChat-x86_64.AppImage --selftest` checks that all components (Streamlink, yt-dlp, libmpv …) can be loaded.

## Requirements

The video tiles need **libmpv**, YouTube video additionally needs a **JavaScript runtime** (Node.js or Deno):

```bash
# Ubuntu/Debian
sudo apt install libmpv2 nodejs
# Fedora (see below: RPM Fusion for Twitch/Kick)
sudo dnf install mpv-libs nodejs fuse-libs xcb-util-cursor
# Arch
sudo pacman -S mpv nodejs
```

Chat, moderation and the overlay also work without them.

### Fedora: full FFmpeg (for Twitch and Kick)

Fedora only ships `ffmpeg-free`, which lacks a full H.264 decoder. Twitch and Kick send H.264, so the tiles would stay black. Swap it via RPM Fusion:

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

The script creates its own Python environment in `build-appimage/` with the exact, hash-checked versions from `packaging/requirements-lock.txt`, bundles the app with PyInstaller and packs it with a pinned `appimagetool` release (downloaded automatically and verified by checksum). The result is a single file of about 105 MB. `PYTHON=python3.12 packaging/build-appimage.sh` selects a specific Python interpreter.

- **libmpv** is taken from the system by default (see Requirements) – small, and guaranteed to match your graphics drivers. `MPV_BUNDLE=1 packaging/build-appimage.sh` bundles libmpv and FFmpeg (~270 MB, then runs without mpv installed).
- The AppImage runs on systems with **at least the glibc version of the build machine**, so build on the oldest distribution you want to support.
- If FUSE is missing (`dlopen(): error loading libfuse.so.2`): `sudo apt install libfuse2` or `libfuse2t64`, or start it with `--appimage-extract-and-run`.
- To add it to your application menu, use e.g. [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) or AppImageLauncher.

### Automatic builds (GitHub Actions)

The workflow [`.github/workflows/appimage.yml`](.github/workflows/appimage.yml) builds the AppImage in an Ubuntu 22.04 container and tests it with `--selftest`:

- **Manual:** Actions → *AppImage* → *Run workflow* → the AppImage is attached to the run as an artifact (14 days).
- **Release:** bump `__version__` in `multichat/__init__.py`, commit, then `git tag v<version> && git push origin v<version>` – the workflow creates the release with the AppImage and checksum. If the tag doesn't match `__version__`, it stops with an error.
- **Which tags create a release:** only `vX.Y` where the second number is a single digit or ends in 0 – e.g. `v0.10`, `v0.20`, `v1.6`, `v2.0`. Intermediate versions such as `v0.11`, `v0.15` or `v0.10.1` don't trigger a build.

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
packaging/            AppImage build script, .desktop file
tests/                automated tests (pytest)
docs/                 additional documentation
```

## Usage

- **Twitch:** channel name, e.g. `channelname`
- **Kick:** channel name (slug), e.g. `channelname`
- **YouTube:** channel, e.g. `@channelname`, `https://www.youtube.com/@channelname` or a `UC…` channel ID. The app checks every minute whether the channel is live, connects to the running stream automatically and waits for the next one after the stream ends. A video URL/ID still works if you want a specific stream.
- **Connect / Disconnect** starts or stops all chats and video tiles.
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
- **Help → Open log file / Open log folder**. The file is `~/.local/state/multichat/multichat.log` (rotated, max. 3 × 1 MB).
- **Help → Verbose log (debug)** additionally records every HTTP response, Kick WebSocket events, stream lookups and mpv messages. Reproduce the problem afterwards, then copy the log.
- Right-click a status text (next to the channel fields or in a video tile) → **Copy status** copies the full text.
- Tokens, secrets and login codes are redacted in the log automatically.
- If the app crashes, `coredumpctl info multichat` (Fedora, systemd) shows a backtrace that helps with debugging.
- Hardware decoding problems with the videos: start with `MULTICHAT_HWDEC=no`, e.g. `MULTICHAT_HWDEC=no ./MultiChat-x86_64.AppImage`.

**YouTube video stays black / "DNS blocker":** Pi-hole, AdGuard Home & co. often block `manifest.googlevideo.com`, but YouTube serves its live-stream playlists from there (no ads). The tile reports this explicitly. Fix: allow it in the blocker, e.g. Pi-hole `pihole allow manifest.googlevideo.com`, AdGuard Home `@@||manifest.googlevideo.com^`, then `resolvectl flush-caches`. Check with `getent ahosts manifest.googlevideo.com` – it must show a real IP, not `0.0.0.0`.

## Notes

- **Twitch** chat is read via anonymous IRC; sending and moderating use the official Helix API.
- **Kick** rotated the Pusher key for its chat in October 2026. The app therefore asks Kick for the current credentials when connecting (`web.kick.com/api/v1/realtime/connection`) and automatically tries the next key if one is rejected.
- **Kick** chat is read via the unofficial website endpoints (chatroom ID from `kick.com/api/v2`, behind Cloudflare, hence `curl_cffi`). Sending and moderating use the official Kick API. If Kick changes its website, reading may break.
- **YouTube** chat is read via `pytchat` (unofficial; run `pip install -U pytchat` if needed), sending and moderating use the official YouTube Data API.
- **YouTube video:** the app finds the running live stream via the channel page; mpv plays it via yt-dlp (mpv's `ytdl_hook`, which also handles separate video/audio tracks and DASH live streams). yt-dlp is included in the app – `MultiChat-x86_64.AppImage --yt-dlp …` behaves like the yt-dlp program. For YouTube, yt-dlp needs Node.js or Deno.
- Emotes: Twitch, Kick and YouTube emotes are shown as images, plus **7TV, BetterTTV and FrankerFaceZ** (menu **View → 7TV / BTTV / FFZ emotes**):
  - global sets apply to all three platforms
  - channel emotes: Twitch channels get 7TV + BTTV + FFZ, Kick channels 7TV (if the streamer has linked a 7TV account)
  - for identical names: channel before global, 7TV before BTTV before FFZ
  - channel emotes are loaded when connecting, so the first seconds of chat may still show text
  - animated emotes are still images in the app window and animated in the OBS overlay
  - if Qt lacks the WebP plugin (some distribution packages), the app uses PNG/GIF; otherwise install `qt6-image-formats-plugins` or use the pip package
- On Wayland, **Always on top** may be ignored depending on the compositor. The video tiles work on X11 and Wayland.
- If the app doesn't start on X11 ("xcb platform plugin"): `sudo apt install libxcb-cursor0`.

## License

MultiChat is free software under the **GNU General Public License v3.0 or later** (GPL-3.0-or-later), see [`LICENSE`](LICENSE).

The libraries, tools and services used, with their licenses, are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

MultiChat is an independent project and is not affiliated with Twitch, Kick, YouTube/Google, 7TV, BetterTTV or FrankerFaceZ. The names are trademarks of their respective owners.
