# CLAUDE.md

MultiChat shows Twitch, Kick and YouTube live chat in one PyQt6 window on Linux, with mpv video tiles,
emotes, moderation/sending via OAuth logins, and a local OBS browser-source overlay. It ships as an
AppImage and a Flatpak, both built by GitHub Actions. License GPL-3.0-or-later; code, comments and commits are English.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'            # runtime deps + pytest (README only shows `pip install -e .`)
multichat                           # or: python -m multichat

python -m pytest -q tests                       # whole suite (conftest sets QT_QPA_PLATFORM=offscreen if unset)
python -m pytest -q tests/test_gui.py -k moderation_menu
MULTICHAT_REQUIRE_MPV=1 python -m pytest -q tests   # fail instead of skip when libmpv is missing (as in CI)
# CI runs under xvfb: xvfb-run -a env QT_QPA_PLATFORM=xcb LIBGL_ALWAYS_SOFTWARE=1 python -m pytest -q tests

ruff check .                        # line-length = 120 (only ruff setting, in pyproject.toml); ruff is not in CI or deps

PYTHON=python3.12 packaging/build-appimage.sh   # needs Python 3.12, gcc, make, pkg-config, binutils, zlib headers;
                                                # output MultiChat-x86_64.AppImage, work dir build-appimage/
MPV_BUNDLE=1 packaging/build-appimage.sh        # bundle libmpv + FFmpeg (~270 MB)
bash packaging/build-flatpak-builder.sh         # builds the pinned flatpak-builder host tool (build.yml then runs
                                                # flatpak-builder on packaging/flatpak/io.github.Corrgan.MultiChat.yml)
./MultiChat-x86_64.AppImage --selftest          # also --selftest-net, --selftest-video FILE, --component-probe
```

Env switches (also honored by the app, not only tests): `MULTICHAT_SECRETS=file|keyring`,
`MULTICHAT_NO_UPDATE_CHECK=1`, `MULTICHAT_NO_LIVE_CHECK=1`, `MULTICHAT_ALLOW_MULTIPLE` (single-instance guard),
`MULTICHAT_TEST_LOOPBACK_IMAGES=1` (tests only: image servers on 127.0.0.1), `MULTICHAT_REQUIRE_MPV=1`,
`MULTICHAT_REQUIRE_SECURE_LXML=1` (CI only).

## Architecture

Package `multichat/`; entry `app.py:main` (also handles `--selftest*`, `--yt-dlp`, `--component-probe`
before creating the QApplication). `packaging/entry.py` is the PyInstaller entry.

- `core/` config (`~/.config/multichat/config.json`), paths, logging, TLS, single-instance, `i18n.py`, `component_probe.py`.
- `chat/` one reader per platform (`twitch.py`, `kick.py`, `youtube.py` + `*_events.py`/`youtube_innertube.py`/
  `youtube_notices.py`), `models.py` (ChatMessage, Deletion), `emotes.py` (7TV/BTTV/FFZ), `cheermotes.py`,
  `alerts.py`, `live.py` (live-indicator polling).
- `accounts/` OAuth logins and send/moderate per platform; `store.py` has `SecretStore` over
  `KeyringBackend` -> `FileBackend` (`tokens.json`/`apps.json`, 0600) chosen by `choose_backend()`.
- `video/` stream lookup (Streamlink, yt-dlp, Kick API), mpv tiles, auto quality, low-delay catch-up.
- `overlay/server.py` aiohttp server for the OBS page (`resources/overlay.html`), WebSocket `/ws`.
- `ui/` `main_window.py` plus its parts `window_connections/groups/messages/send.py`, `chat_view.py`,
  `dialogs.py`, `moderation/`, `watchdog.py`.

Threading: `MainWindow.__init__` starts a private asyncio loop in a daemon thread (`self.loop`, `self.session`
aiohttp). All network work is coroutines in that loop; `MainWindow.run(coro, then=...)` submits via
`run_coroutine_threadsafe` and delivers the result back in the Qt thread. Readers push events across threads
through `Bridge` signals (`message`, `deletion`, `incoming` = coalesced mailbox wakeup, `status`, `result`).
Never touch widgets from a coroutine; emit via the bridge. Shutdown cancels loop tasks, then stops the loop.

i18n: source strings are English, wrapped in `_()` from `multichat/core/i18n.py`, placeholders in `str.format`
style. Translations are `multichat/resources/locales/{de,es,fr,ja,zh_CN}.json` (`{"English": "translated"}`),
missing keys fall back to English; language changes apply after restart. When adding a UI string,
add it to the locale files (`tests/test_i18n.py::test_catalogs` checks the catalogs). Log messages stay English.

## Conventions

- Version lives only in `multichat/__init__.py` (`__version__`); pyproject reads it dynamically. A release also
  needs an entry in `packaging/io.github.Corrgan.MultiChat.metainfo.xml`.
- Release = tag `vX.Y` on `main` matching `__version__`. `build.yml` only builds releases for tags
  `^v[0-9]+\.([0-9]|[0-9]+0)$` (v0.10, v1.6, v2.0) plus `EXTRA_RELEASES` (v0.25); v1.11 or v1.2.1 build nothing.
  The workflow fails if the tag commit is not an ancestor of `origin/main` or the tag != `__version__`.
- Python deps: `packaging/requirements-lock.txt` is hash-locked (`pip install --require-hashes`), generated with
  `uv pip compile ... --python-version 3.12` (command in its header). After changing `pyproject.toml` dependencies
  regenerate it, then `packaging/flatpak/generate-python-deps.py` (python-deps.json); `tests/test_packaging.py` fails if they drift.
- Workflow actions are pinned to commit SHAs; the AppImage is built in an `ubuntu:22.04` container (glibc 2.35).
- Source files start with `# SPDX-License-Identifier: GPL-3.0-or-later` and a copyright line.
- Tests/CI must never reach real services: no external HTTP, no real keyring.

## Test pitfalls

- `tests/conftest.py` creates ONE temp dir per pytest session and points `XDG_CONFIG_HOME/STATE/CACHE` at it, so
  config, tokens and logs leak between tests and modules. The `window` fixture in `test_gui.py` resets `groups`,
  `active_group`, `side_by_side` for that reason; reset any other config you rely on.
- conftest also sets `MULTICHAT_SECRETS=file`, `MULTICHAT_NO_UPDATE_CHECK=1`, `MULTICHAT_NO_LIVE_CHECK=1`,
  `MULTICHAT_TEST_LOOPBACK_IMAGES=1`, keeps one session-wide `QApplication`, blocks `aiohttp` requests to any
  non-loopback host, and clears the Kick/YouTube caches before each test.
- GUI tests use the `window` fixture (`test_gui.py`): a real `MainWindow` (and thus a real asyncio thread) with
  `run_twitch/run_kick/run_youtube` monkeypatched to an idle coroutine and video/extra emotes disabled. It calls
  `i18n.set_language("en")` on teardown.
- Known flaky: `tests/test_gui.py::test_chat_and_moderation_menu` fails rarely. Cause not found yet; suspected
  timing between the Qt thread and the asyncio thread (unverified). Re-run before assuming a regression, and
  don't paper over it with sleeps without finding the race.
- `MULTICHAT_REQUIRE_MPV=1` turns the libmpv-dependent tests from skip into failure; without libmpv they skip locally.
