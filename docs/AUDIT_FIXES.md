# Audit corrections

Implemented after the independent review of main `fcfa5f5` / release v0.80.
These source changes do not modify the already published v0.80 artifacts.

- F01: moderation selection, cards and delete-last keep their source/channel;
  the target channel is visible. Cross-channel history remains available.
- F02: a thread-aware account lock protects session checks together with token
  commits/deletion. Login identity completion and stale error cleanup use the
  same generation guard. Old API responses/retries cannot repeat an action under
  a new account. Network waits stay outside the lock.
- F03: secret-store mutations report persistence success. Explicit logout always
  clears the in-memory session; failed persistence produces a warning and keeps
  strict keyring mode free of plaintext fallback.
- F04: a bounded per-source mailbox coalesces Qt wakeups and drains fair batches.
  It preserves each source's message/deletion order, counts discarded events,
  and separately counts moderation-event loss under deletion-only overload.
  Targeted deletions never turn into a source clear; moderation wakes Qt promptly.
- F05: line updates compact when needed, preserving the displayed document and
  its message/character reading anchor even during a scroll pause.
- F06 and N01: account generations invalidate native emote requests, moderator
  responses and user-card information. Old success/error replies cannot overwrite
  the next account's caches or permissions display.
- F07: completion converts Qt UTF-16 positions and replaces through Qt, retaining
  suffixes, the cursor and undo history. Real send-path regressions are covered.
- F08: license export preserves nested paths, rejects differing overwrites and
  writes an inventory with file hashes. PyCryptodome notices were corrected.
- F10: numeric patch tags are accepted; native CI prerequisites are enforced;
  source-compatibility jobs complement the pinned release environment. OSV triage
  and partial CycloneDX inventories are automated.
- N02: negative image caching has a size bound and retry deadline; recovery
  updates existing fallback lines with bounded retry work.

Remaining follow-up: new Linux package builds and Python compatibility jobs must
run in CI; this Windows validation cannot prove GPU/decoder integration. Native
FFmpeg pins are unchanged pending reviewed fixes/updates; current reachability
assessments are recorded in [packaging/SECURITY.md](../packaging/SECURITY.md).
Inventories are explicitly partial, including build-environment distributions,
not a complete binary SBOM. Repository branch protection requires administrative
configuration and is not changed by these files.

## Validation of the first correction batch on 2026-11-09

From the repository root, using the isolated Python 3.12.5 / Qt 6.12 environment:

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest -q -ra tests
```

Result: **152 passed, 4 skipped in 16.46s**. The four skips are two explicit POSIX
file-mode tests on Windows and two native libmpv tests; the corresponding Linux
CI jobs require libmpv and fail if it is unavailable. Permission assertions remain
active on POSIX systems. `pip check` and `git diff --check` also passed.

The tests include real Qt selection/card/menu/send paths, bounded background
ingestion, token-store races, a late 401 after an actual logout and new local
OAuth login, and image recovery. External platform/browser/wallet boundaries are
intercepted; no real moderation action is sent. Two additional account tests are
explicitly internal reauthentication checks rather than normal menu interactions.

Packaging validation additionally preserved both original license hashes from
the pinned PyCryptodome Linux wheel; validated sample inventories against the
official CycloneDX 1.6 schemas; checked workflow YAML, Bash syntax and six release
tag cases; and executed the OSV script with 75 live queries. The result was zero
PyPI matches and the five documented FFmpeg matches, without a blanket claim of
native non-vulnerability. These checks do not replace a complete new Linux build.

## Additional findings from the HTML audit

The HTML audit uses different N identifiers from the first audit. Its N02
(stale moderator answers) corresponds to the first audit's N01 and was already
corrected. Its N04 (late login identity completion) is covered by the session
generation guards. The following additional fixes address HTML N01, N03 and N05:

- **HTML N01:** each pytchat fallback reader receives its own `DefaultProcessor`.
  Its mutable renderers can no longer be shared by concurrent channel readers.
  The regression drives two real GUI connections and library parser/renderer
  threads through both explicit pytchat mode and automatic InnerTube fallback.
- **HTML N03:** each view retains a bounded model of the displayed messages.
  Full updates during a scroll pause rebuild that model, preserving the message
  and character reading anchor. Old displayed messages remain selectable and
  receive relevant deletion/image/selection updates after central-history trim.
  Returning to the bottom catches up from current history; clearing, source
  changes and group reuse also invalidate the retained state appropriately.
- **HTML N05:** the upstream checker requires a complete, unambiguous comparison
  of the expected native modules. Known external module definitions are resolved
  explicitly; unsupported/incomplete responses fail. Exit codes distinguish full
  equality (0), version differences (1), and an unknown/error result (2). The
  workflow creates version reminders only for actual differences.

### Validation after the additional corrections

The complete local suite now reports **192 passed, 4 skipped in 28.31s** with
`python -X utf8 -m pytest -q -ra tests`. The skips remain the same two POSIX
permission checks and two tests requiring native libmpv. `pip check` and
`git diff --check` passed as well.

The added regressions exercise the actual YouTube parsers on two worker threads,
paused passages with 1,000 old and 1,000 new messages, original-message deletion
after history eviction, bounded retention over repeated bursts, and the real
upstream-check entry point with replaced HTTP responses. Workflow shell tests
distinguish status 0/1/2 and reject failed/malformed issue lookups without creating
real issues. External APIs and moderation calls remain intercepted in the tests.

The corrected checker also completed a live comparison. It returned status 1 for
an actual LuaJIT difference: local `v2.1-20260724`, Flathub `v2.1-20260620`.
The report correctly describes a difference without claiming the remote version
is newer. No reminder issue was created during local validation.

## Further resource and lifecycle corrections

Image transfers now share a bounded transport implementation. Each chat view
allows eight active requests and 96 waiting URLs; the completer allows six and
64. These limits apply before creating a Qt request. Waiting hosts receive
round-robin service. Content-Length, progress and streamed bytes enforce the
4 MiB chat / 2 MB icon limits, with a 64 KiB Qt read buffer and a 15-second wall
deadline as well as the transfer timeout. Failed Linux QIODevice reads returning
None are handled without raising through a Qt signal callback.

Only supported raster formats with bounded header geometry are decoded. Limits
cover dimensions, pixels, decoder allocation, GIF frame geometry, declared frame
counts, source-frame pixels, scaled-frame bytes, encoded animation bytes and
aggregate movie surfaces. Unknown or excessive animations use a static first
frame. Movies do not use CacheAll, hidden movies stop, only a bounded visible set
plays, and short frame delays are paced. The positive image cache has a byte
ceiling as well as an entry limit; failed-image notices and retries are batched.

Third-party channel metadata has a 64-channel LRU, a six-hour expiration time,
per-channel/global/aggregate entry budgets, and a streaming JSON response limit.
Eight loads run concurrently with at most 64 pending tasks, including waiting
loads; duplicate scheduling is suppressed before creating tasks. Kick retries
expired or evicted metadata on incoming chat, without requiring a reconnect.

Full redraws handle one visible group per Qt turn and defer hidden groups until
activation. Dirty groups rebuild from the current routed live model or their
paused retained model. Deletions and routing changes remain effective while a
redraw waits. This yields between groups; one bounded document rebuild still
runs on the Qt thread and is not a hard real-time latency guarantee.

Closing a window cancels and awaits tasks belonging to its event loop before
closing HTTP resources and the loop thread. Flush/render/save timers stop, late
results and reader events are ignored, and new coroutines are closed rather than
submitted after shutdown. Tests prohibit external aiohttp platform requests;
loopback integration servers remain available.

Qt image requests also have explicit terminal shutdown: owned connections are
disconnected before aborting replies, queued URLs and partial bodies are cleared,
and view/completion retry and animation timers stop. QObject-bound slots replace
reply-capturing lambda cycles. A real loopback HTTP server holds a partial PNG
response while another URL waits; closing the view/editor now cancels both paths
and releases owners after deferred deletion and garbage collection. The old
implementation fails that same regression with a live reply and waiting URL.
The test session also retains its QApplication for the full session lifetime.

### Actual stress and wallet checks

The synthetic one-minute chat stress uses ten visible groups, continuous input
and repeated full redraws. Windows processed 133,800 generated messages and Linux
133,600, with zero recorded drops and empty queues after draining. Both retained
exactly 1,000 messages/blocks per group. Measured 10 ms heartbeat p99/max gaps were
157/252 ms on Windows and 170/194 ms on Linux. These are observations from this
machine under concurrent build work, with text and preloaded platform icons;
they do not establish multi-hour stability or performance with arbitrary media.

A real GNOME Keyring Secret Service was started on a private D-Bus in a fresh
container HOME. Automatic strict-backend selection, synthetic token write/read,
logout read-back in a new store, file-to-wallet migration and removal of the
migrated file passed. No host wallet or real credentials were used. Locked or
unavailable wallet behavior remains covered by fault injection; a KDE desktop
wallet and physical GPU/Wayland/X11 driver behavior still require their own
environment-specific acceptance checks.

### Repository administration

The prepared main-branch ruleset and administrator activation command are in
REPOSITORY_RULES.md. The connected account has no push/admin rights to
Corrgan/multichat; an actual push dry-run returned HTTP 403. Local corrections do
not activate remote rules or replace previously published binaries.

## Native corrections and final source validation

Seven source-verified FFmpeg backports address the five recorded advisories.
The exact baseline, patch hashes, enabled features and test scope are described
in packaging/SECURITY.md. Independent old/fixed builds provide negative controls
for the empty-search hang, deep-list stack exhaustion, unwritten HDR bytes,
unbounded RTSP redirects and repeated MOV pssh parsing. The patched MOV case
preserves 10,000 records and completes in approximately 7 ms in the component
test; the old case exceeds the three-second bound.

Linux release builds replace the ordinary lxml wheel with a source build using
libxml2 2.15.4 and libxslt 1.1.45. Full-process validation found that static linking
alone allowed host XML symbols, loaded globally by libmpv, to interpose the new
code. The corrected linker flags hide archive symbols, including global data.
A direct comparison of two source builds with the same 2.15.4 inputs reproduced
the old build reporting host 2.9.14 and the corrected build retaining 2.15.4 after
host preloading. XML, XPath, XSLT, objectify and XInclude checks pass. The artifact
probe loads video libraries first and rejects a measured/build-version mismatch.

The final source suite was exercised on Debian 12 with Xvfb, software OpenGL and
real libmpv. No native video tests were silently skipped:

| Environment | Result | Scope of skips |
|---|---|---|
| Linux, pinned Python 3.12 release environment | 276 passed, 0 skipped | Includes old-wheel and old/fixed FFmpeg controls and corrected XML runtime |
| Linux, resolved Python 3.10 source dependencies | 273 passed, 3 skipped | Three hardened-release/native-control tests run separately on 3.12 |
| Linux, resolved Python 3.14 source dependencies | 273 passed, 3 skipped | Same three release-specific controls |
| Windows development environment | 269 passed, 7 skipped | Two POSIX, two native-video and three Linux-release/control tests |

The release job explicitly requires the corrected XML runtime test. Python package
consistency and source syntax checks also pass. Windows and ordinary source pip
installs are not claimed to contain the Linux native security rebuild. AppImage
validation on Debian 12 is not a substitute for the configured Ubuntu 22.04 build
or physical GPU/desktop acceptance. Actual artifact build results are recorded
separately from these source-suite results.

The actual Debian-12 AppImage validation produced SHA256
`c1adf96ea0d99a2dd2cef18a086a06c06d252976597bba59421f4bcb1056cd9a`
(105,138,680 bytes). The archive was re-extracted and its actual CArchive/PYZ,
mapped native files, runtime source hashes and eight shutdown guards checked.
The video-first runtime probe reports libxml2 2.15.4/libxslt 1.1.45 with no errors;
the module selftest, real HTTPS request (HTTP 200) and ten-frame native decoder
test pass. The extracted inventory contains 1,596 files, 66 Python components and
246 native files, with 113 license exports and zero license/evidence errors. One explicit coverage gap
remains: complete attestation of static/vendored native composition. The tag
release policy blocks publication while this gap remains, rather than calling
that inventory complete.

The actual Flatpak was built, composed, exported and installed using the resolved
KDE/PyQt 6.11 SDK/runtime and the corrected native source inputs. Its SHA256 is
`ce95732006f8ba475637d2437b04e8e43b03474886c72b2d9154ac05e82a859d`
(79,626,744 bytes). Imports, HTTPS (HTTP 200), eleven decoded frames and an actual
installed MainWindow show/close under Xvfb/XCB/software GL pass. The four changed
image/shutdown runtime modules match their frozen source hashes. The standard GL
runtime extension is required for this GUI check; a minimal no-related install
without that extension cannot create its GL context.

The extracted Flatpak inventory records 7,475 files, 61 Python distributions and
164 ELF files, with zero license/evidence errors. PLY's complete original license
headers are exported with exact version/source/header hashes; relocated BaseApp
licenses are bound to exact versions, paths and hashes. COPYRIGHT files and
legacy egg-info are included. Twenty-four explicit composition/runtime/origin
coverage gaps remain; the normal verification returns 0 and the separate strict
complete-inventory policy correctly returns 1. Both artifact results are local
validation evidence, not a publication or a complete vendor/runtime SBOM claim.

The repository's host-builder helper pins and verifies flatpak-builder 1.4.12.
Its real source build passed locally; its CI prerequisite packages were checked
against Ubuntu 24.04. No GitHub workflow was run from this unpushed local branch.

## Adjustments made when this work was merged

The audit corrections above were merged as submitted. Four project decisions differ from
the text above and from the pull request; the files in the repository are authoritative:

- **Release tags:** the project's existing rule stays (`vX.Y` with Y a single digit or
  ending in 0, plus the listed exceptions). Patch tags do not build a release.
- **Inventory coverage gaps:** reported with every build and published in the (explicitly
  partial) inventories, but they do not block a tagged release. License and evidence
  errors still fail the build.
- **Repository rules:** `.github/rulesets/` holds two mild rulesets (no deletion or
  force-push of `main`; release tags cannot be moved or deleted) instead of required pull
  requests and status checks. See REPOSITORY_RULES.md for the stricter option.
- **Animated emotes:** decoded frames are cached again (QMovie.CacheAll) within a separate
  byte budget; only animations that do not fit are decoded frame by frame. Measured with
  12 emotes playing, uncached decoding cost about three times the CPU.

Two defects of the merged image code were found in the first test builds and corrected:

- **Images were dropped after arriving.** The bounded transfer's final read asked a
  finished reply for more than it had buffered; Qt answers -1 for that unless it used its
  zero-copy buffer (plain HTTP/1.1 with a Content-Length – the only case the loopback
  tests covered). Every image from a server that compresses, sends chunked or speaks
  HTTP/2 was treated as an I/O error: no emotes or platform icons at all. The transfer
  now reads exactly what is buffered; `tests/test_image_transfer_encodings.py` covers the
  other framings with a real loopback server.
- **Retries re-rendered the chat.** A failed image was retried every 30 seconds by
  rendering its lines again (twice per attempt, for a platform icon every line of every
  group) – an idle chat flickered. Retries now run in the background with a growing
  interval (30 s up to 10 min); lines are rendered again only when an image has arrived.

## Follow-up corrections for v1.0

The follow-up review found four Linux behavior defects, a Windows source-startup
regression, and an existing mention-action inconsistency. They are corrected as follows:

- Automatic video quality retains a stall that crosses the startup/ad grace period,
  counts only the unprotected time, and never steps up while the player is buffering.
  The lowest quality also retains an ongoing stall until the player actually recovers.
- With unknown variants, the controller learns the picture delivered by each quality
  cap. A duplicate intermediate cap is skipped without declaring every higher or lower
  cap unavailable. Channel changes and changed variant lists discard stale observations.
- The single-instance server holds a process lock until its socket closes. Concurrent
  starts notify the owner without removing its live socket; abandoned locks and sockets
  are recovered after a crash. Windows uses its per-user cache and Qt named pipes rather
  than POSIX user IDs. Lock contention does not silently open another window.
- A newly failed image with an earlier retry deadline advances an already running
  timer. Later deadlines do not postpone earlier attempts; retry batches remain bounded.
- The mention action uses the same account-name helper as @-completion, preserving the
  send target and existing text while addressing the real Twitch login when needed.

Regression coverage includes real competing processes, crash recovery, grace-crossing
stalls, duplicate intermediate resolutions, image scheduling, and mention insertion.
POSIX-only socket/permission checks are marked accordingly; JSON replacement and
Windows named-pipe behavior retain their own platform-independent/native coverage.

### Adjustments made when the follow-up was merged

The single-instance lock was changed in two respects; the other corrections were taken
as submitted.

- **The lock is the kernel's (`flock`) on Linux, not `QLockFile`.** `QLockFile` decides
  by the process ID stored in the file whether its owner is alive. Every Flatpak sandbox
  has its own process IDs, starting at 1: after a crash or a hard kill the next start
  found "its" ID alive – it was looking at itself – and could never take the lock again.
  `flock` is released by the kernel when the process ends, however it ends, and works
  between the sandboxes. Windows keeps `QLockFile`. `tests/test_single_instance.py`
  reproduces the case with real PID namespaces.
- **Problems with the lock or the socket do not keep the app from starting.** As
  submitted, a lock that could not be taken, a holder that did not answer or a socket
  that could not be created ended the start with nothing but a log line. Showing one
  window is a convenience; in these cases the app now starts without the check (and
  says so in the log). A second start still never removes the socket of a running
  instance – only the lock holder cleans up.
