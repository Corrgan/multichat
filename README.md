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

AppImage and Flatpak downloads come with SHA256 checksums and a **build provenance attestation** signed by GitHub: it proves the file was built by this repository's workflow from a specific commit. The separate dependency inventories are covered by provenance attestations as well.

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
PYTHON=python3.12 packaging/build-appimage.sh
./MultiChat-x86_64.AppImage
```

The script requires Python 3.12, a C compiler, make, pkg-config, binutils and zlib development headers. It creates its own environment in `build-appimage/` with the exact, hash-checked versions from `packaging/requirements-lock.txt` and builds lxml against the corrected, pinned XML libraries in `packaging/native-sources.json`. It bundles the app with PyInstaller and packs it with a pinned `appimagetool` release (downloaded automatically and verified by checksum). `packaging/prune-bundle.py` removes the build machine's system libraries from it (see above). Source installations support other Python versions separately; an ordinary pip installation does not apply this native XML rebuild.

- **libmpv** is taken from the system by default (see Requirements) – small, and guaranteed to match your graphics drivers. `MPV_BUNDLE=1 packaging/build-appimage.sh` bundles libmpv and FFmpeg (~270 MB, then runs without mpv installed).
- The AppImage runs on systems with **at least the glibc version of the build machine**, so build on the oldest distribution you want to support.
- If FUSE is missing (`dlopen(): error loading libfuse.so.2`): `sudo apt install libfuse2` or `libfuse2t64`, or start it with `--appimage-extract-and-run`.
- To add it to your application menu, use e.g. [Gear Lever](https://flathub.org/apps/it.mijorus.gearlever) or AppImageLauncher.

### Automatic builds (GitHub Actions)

- [`tests.yml`](.github/workflows/tests.yml) runs the hash-checked Python 3.12 test suite on every push and pull request (including Dependabot's), checks that the Flatpak's Python packages match the lock file, and separately tests resolved source dependencies on Python 3.10 and 3.14. Native CI requires libmpv instead of silently passing without it.
- [`build.yml`](.github/workflows/build.yml) builds the AppImage in an Ubuntu 22.04 container and the Flatpak with `flatpak-builder` ([`packaging/flatpak/`](packaging/flatpak)). Both run `--selftest`, `--selftest-net` (an HTTPS request) and `--selftest-video` (decodes a short clip with libmpv); the AppImage additionally in Fedora, Debian and Arch containers.
- [`flatpak-upstream.yml`](.github/workflows/flatpak-upstream.yml) compares the Flatpak's self-built libraries (mpv, FFmpeg …) with Flathub's mpv package every week and opens a reminder for version differences. An incomplete comparison fails the check.
- [`dependency-triage.yml`](.github/workflows/dependency-triage.yml) queries OSV for the Python lock and native Git pins, retaining exact queries and advisory results. Builds measure the actual extracted artifacts, publish CycloneDX inventories and preserve nested license files. License or evidence errors fail the build; parts the inventory cannot attribute yet are reported as coverage gaps and do not block a release – see [`packaging/SECURITY.md`](packaging/SECURITY.md).


- **Manual:** Actions → *Build* → *Run workflow* → AppImage and Flatpak are attached to the run as artifacts (14 days).
- **Release:** bump `__version__` in `multichat/__init__.py` (and add the release to `packaging/io.github.Corrgan.MultiChat.metainfo.xml`), commit, then `git tag v<version> && git push origin v<version>` – the workflow creates the release with both files and checksums. If the tag doesn't match `__version__` or the tagged commit isn't on `main`, it stops with an error – tag only on `main`.
- **Which tags create a release:** only `vX.Y` where the second number is a single digit or ends in 0 – e.g. `v0.10`, `v0.20`, `v1.6`, `v2.0`. Intermediate versions such as `v0.11`, `v0.15` or `v0.10.1` don't trigger a build; one-off exceptions are listed in `EXTRA_RELEASES` in the workflow (currently `v0.25`, the first release with the Flatpak).
- Branch and release-tag protection is prepared in [`.github/rulesets/`](.github/rulesets); a repository administrator activates it once, see [`docs/REPOSITORY_RULES.md`](docs/REPOSITORY_RULES.md).

## Project layout

```
multichat/            the application (Python package)
  app.py              start-up, --selftest, --yt-dlp
  core/               configuration, paths, logging, translations
  chat/               chat readers per platform, 7TV/BTTV/FFZ emotes
  accounts/           logins (OAuth), sending and moderating per platform
  video/              stream lookup (Streamlink, yt-dlp, Kick API) and the mpv video tiles
  overlay/            local web server for the OBS overlay
  ui/                 main window (main_window.py plus its parts window_*.py), chat view, dialogs
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
- **Everything Twitch puts into the chat is shown:** besides messages (also those of bots) and subs, gifts, raids and bits these are announcements, watch streaks and other notices in Twitch's own words, the room's notices (slow mode …), and a label on messages bought with channel points, sent with a Power-up or written by a first-time chatter. A channel point reward without a message is not sent to chat programs by Twitch and cannot be shown. Kick: redeemed rewards, pinned messages, polls and a label on replies (read from Kick's undocumented website events, as far as they look as expected). YouTube: all its other chat lines – chat mode changes, redeemed gift memberships, YouTube's own hints.
- **Live indicator:** a red dot next to a channel and on its group's tab shows that the channel is live right now – also while it is not connected, so you see at a glance where something is happening. Twitch is checked every 30 seconds (one request for all channels; needs the Twitch login), Kick and YouTube every two minutes, without a login. Twitch's own answer lags the start of a stream by a minute or more; a channel whose video already plays in a tile gets its dot at once. `MULTICHAT_NO_LIVE_CHECK=1` turns the lookups off. Next to the group's name the tab shows how long the streamer has been live (e.g. `1:23`), taken from the first channel of the group whose platform says when the stream started; the tooltips give it per channel.
- **Channel groups:** channels that belong together (e.g. one streamer on Twitch, Kick and YouTube) form a group – one channel per platform. For a second channel of the same platform, add another group. Each group has its own tab with its chat and its videos; **+** left of the tabs (or **View → New channel group**) adds a group. The name field renames it; left empty, the group is named after its first channel (Twitch, then Kick, then YouTube). **View → Groups side by side** shows all groups next to each other, each with its videos: chat 1 | videos 1 | chat 2 | videos 2. Tabs of hidden groups count new messages – `(@3)` in orange means someone mentioned you. Drag a tab to put the groups in another order.
- Every channel has its own connect button; **Connect** / **Disconnect** at the top of a group handle all its channels, **View → Connect all groups / Disconnect everything** all of them.
- **Videos** belong to their group: the camera box of a channel decides whether it gets a tile. Only one tile in the whole window plays sound – click a tile to give it the sound. Quality and volume are remembered per channel. With the quality **Automatic** a tile adapts by itself: it starts at 720p (lower for a small tile), steps down when the video keeps stalling and tries the next level up again after a few smooth minutes – only as far as the tile can show it. Each change reloads the stream (a short interruption), and a step up that fails is retried less and less often. The list shows the current level, e.g. “Automatic (480p)”.
- **Low video delay** (View menu, on by default): the chat arrives at once, a video seconds later. With this on, the tiles start closer to the live moment (Twitch in its low latency mode, Kick and YouTube one segment nearer) and play a little faster – a few percent, with the pitch corrected – whenever more is buffered than needed, e.g. after the video had to wait for data. Less buffer means less reserve: turn it off if the videos stall often.
- **Sending** goes to the active group: pick one of its channels or “All in this group”.
- **Name suggestions:** type `@` and a letter – the people who wrote in the chat you are sending to are listed, whoever wrote last first; ↑/↓ choose, Tab/Enter/click insert `@name`. On Twitch, names shown in another script are inserted as the account name, which is what Twitch recognises.
- **Emote suggestions:** type `:` and two letters (`:kap`) – a list with the matching emotes appears; ↑/↓ choose, Tab/Enter/click insert, Esc closes. A complete `:kappa:` becomes `Kappa` right away. It offers your Twitch emotes for that channel (global, subscriptions, follower emotes) and the channel's 7TV/BTTV/FFZ emotes. Logins from before 0.80 only get global and channel emotes – log in to Twitch again for all of yours.
- Symbols before a name show the user's role: 🎥 broadcaster, 🛡 moderator, 💎 VIP, ★ subscriber/member, ✔ verified.
- **Mentions:** lines that name the account you are logged in with on that platform – with or without @, as a whole word – are highlighted in red; your own messages are not.
- **Click on a name** to select the user. The **action bar** above that group's chat then works on that user: user card, mention, delete their last message, timeout (1 min / 10 min / 1 h), ban, unban.
- **Right-click a name** for the menu: user card, mention, copy text and the same moderation actions.
- **User card** (👤): roles, account age, followage (Twitch, Kick), subscription months (Kick), ban status (Kick), the user's recent messages, your moderation actions and your own notes. It opens as a window; the 📌 button docks it next to the chat (remembered). Notes and the action log are stored only on this computer in `~/.config/multichat/modnotes.json`.
- **Moderation context:** the selected user and card retain their channel even when that user writes in another channel. The card, action bar and ban confirmation show the target channel; opening a different channel's message changes it explicitly. Cross-channel history labels each message's channel.
- **Moderator check:** MultiChat asks Twitch which channels you moderate; on Kick and YouTube it knows your own channel. In addition, the badge on your own chat messages counts. Where you are known **not** to be a moderator, the moderation actions are hidden.
- **Alerts:** subs, resubs, gifted subs, raids/hosts, bits/Kicks, Super Chats and memberships are shown with a headline and highlighted in the chat and in the OBS overlay. **View → Alerts …** turns each kind on or off and sets minimum values (e.g. bits from 100). The alert settings of the streamer (StreamElements, Streamlabs …) can't be read by other apps, so set values that match them. Events below the minimum are still shown, without highlight. Follows aren't part of the chat and are not shown.
- **View → Chat font size** (Ctrl + / Ctrl − / Ctrl 0) makes the chat text and emotes larger, e.g. to read it from further away. Only the chat changes, not the rest of the window.
- **Input line at the bottom:** send to one platform or to all you're logged in to.
- Settings: `~/.config/multichat/config.json` (readable only by you).
- Logins and the client secrets of your developer apps are stored in the system keyring (KWallet, GNOME Keyring, KeePassXC … via the Secret Service API). Without one, they go to `tokens.json` / `apps.json` next to the settings, readable only by you. `MULTICHAT_SECRETS=file` forces the files; the log and `--selftest` show which storage is used. Data from older versions is moved into the keyring automatically.
- `MULTICHAT_SECRETS=keyring` prevents plaintext fallback. If the wallet cannot save a logout, the app removes the in-memory session and warns that the account may return after restarting; it does not report a durable logout as successful.
- Chat ingestion is bounded before GUI delivery and processed fairly across sources. When overloaded, dropped events are counted and reported; debug statistics separately count lost moderation events, which can leave the local display incomplete. Temporary image failures retry after a delay instead of hiding the emote until restart.
- Scrolling up pauses incoming lines without replacing the passage you are reading, including during bulk moderation updates. Visible messages still receive deletions and updates. Return to the bottom with the jump button to catch up with current chat.

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
| `group` | only this channel group (1 = first tab) – **Overlay → Copy overlay URL for the active group** | `group=2` |
| `badges` | hide platform icons | `badges=0` |
| `alerts` | no highlight for alerts (subs, bits, raids …) | `alerts=0` |

Example: `http://127.0.0.1:17564/?fade=30&size=24&platforms=twitch,youtube`

Deleted messages and banned users disappear from the overlay as well – only in the channel where they were deleted or banned.

With several channel groups, the overlay shows all of them; add one browser source per group with `group=` to keep them apart. The number is the group's position in the tabs, so it changes when a group in front of it is removed or the tabs are put in another order.

The overlay only answers requests from this computer (`127.0.0.1` / `localhost`); other websites open in your browser cannot read the chat through it. Use the URL as copied – addresses such as the machine's LAN IP are rejected.

## Troubleshooting / log

- **Help → Copy log** puts the last 300 lines on the clipboard, ready to paste into a chat or an issue.
- **Help → Open log file / Open log folder**. The file is `~/.local/state/multichat/multichat.log` – for the Flatpak `~/.var/app/io.github.Corrgan.MultiChat/.local/state/multichat/multichat.log` (rotated, max. 3 × 1 MB).
- **Help → Verbose log (debug)** additionally records every HTTP response, Kick WebSocket events, stream lookups and mpv messages. Reproduce the problem afterwards, then copy the log.
- Right-click a status text (next to the channel fields or in a video tile) → **Copy status** copies the full text.
- Tokens, secrets and login codes are redacted in the log automatically.
- If the app crashes, `coredumpctl info multichat` (Fedora, systemd) shows a backtrace that helps with debugging.
- MultiChat runs once per user: starting it again brings the running window to the front (two windows would overwrite each other's settings and compete for the overlay and login ports). `MULTICHAT_ALLOW_MULTIPLE=1` allows a second window anyway.
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
  - large or complex images and animations may use a static frame or text fallback to keep chat responsive
  - if Qt lacks the WebP plugin (some distribution packages), the app uses PNG/GIF; otherwise install `qt6-image-formats-plugins` or use the pip package
- On Wayland, **Always on top** may be ignored depending on the compositor. The video tiles work on X11 and Wayland.

## Authors

See [`AUTHORS.md`](AUTHORS.md).

## License

MultiChat is free software under the **GNU General Public License v3.0 or later** (GPL-3.0-or-later), see [`LICENSE`](LICENSE).

The libraries, tools and services used, with their licenses, are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

MultiChat is an independent project and is not affiliated with Twitch, Kick, YouTube/Google, 7TV, BetterTTV or FrankerFaceZ. The names are trademarks of their respective owners.
