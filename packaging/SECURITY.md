# Native fixes, artifact inventory and advisory coverage

Linux release builds use hash-checked Python inputs, verified native source archives
and reviewed FFmpeg backports. The actual AppDir and the installed/extracted release
artifacts are inventoried, rather than treating the whole build virtualenv as the
delivered application. PyInstaller's selected files, ELF NEEDED/build IDs, frozen
runtime component probes, exported license hashes and build context are recorded in
JSON/CycloneDX sidecars. Flatpak runtime/SDK/BaseApp refs and relevant host-package
inputs are separate context.

The Flatpak workflow builds host flatpak-builder 1.4.12 from its verified release
archive using packaging/build-flatpak-builder.sh. This supports the SDK's current
appstreamcli composer without disabling metadata validation. Its source hash is
pinned separately from application components. The version also includes the
[upstream Git-hook correction](https://github.com/flatpak/flatpak-builder/security/advisories/GHSA-j5p8-jgjc-f3xv);
the current manifest does not use the affected use-git-am patch option.

The inventory is still explicit about its limits: an unidentified static/vendored
component or missing runtime SBOM is a coverage gap, not a clean security result.
License and evidence errors fail the build and thereby block a release. Unresolved
coverage gaps are reported with every build and published as part of the (then
explicitly partial) inventory; they do not block a release. Provenance covers the final
artifact and its inventory. A passing component test or an OSV query without a match
does not establish complete vulnerability coverage.

License declarations are resolved only inside the distribution's own real
dist-info/egg-info metadata directory, including PEP 639's licenses directory.
The PyQt BaseApp relocates PyQt6-sip 13.13.0's original LICENSE into its shared
license directory. packaging/flatpak/baseapp-licenses.json binds that exception
to the exact distribution version, declared filename, BaseApp path and SHA256.
The exported ledger retains this provenance; another version, missing original
or changed text fails the gate. A different package's vendored license cannot
satisfy the declaration. Legacy egg-info is also included in the artifact's
Python component inventory, without inventing file ownership when RECORD is absent.
PLY 3.11's complete original BSD license headers are exported byte-for-byte from
its RECORD-owned lex.py and yacc.py, with exact version, full-file hash, line range
and header hash bindings. COPYRIGHT files are collected alongside LICENSE and
NOTICE files, including the libxml2/libxslt texts retained inside the lxml wheel.

## FFmpeg n9.0.2 with seven reviewed upstream backports

The stable source remains commit 946fcce07b6dcd0331c8cc609192aeff5e1924f8; there was
no newer 9.x stable release when checked on 2026-11-09. The Flatpak now applies all
seven patches in packaging/patches/ffmpeg, with upstream origins and exact patch
hashes recorded in backports.json:

| Advisory | Applied correction |
|---|---|
| [CVE-2026-107675](https://osv.dev/vulnerability/CVE-2026-107675) | [2b822b7f](https://github.com/FFmpeg/FFmpeg/commit/2b822b7fb6ed195546bc9fd9bdb794e98222bdce): verify the SSH host key before authentication. libssh is also explicitly disabled in this build. |
| [CVE-2026-107676](https://osv.dev/vulnerability/CVE-2026-107676) | [2c2f6e96](https://github.com/FFmpeg/FFmpeg/commit/2c2f6e96e31795ae95a8f8323a493ffbc493111f): serialize color-saturation fields independently of tone mapping, eliminating the unwritten payload bytes. |
| [CVE-2026-107677](https://osv.dev/vulnerability/CVE-2026-107677) | [ed27bfcb](https://github.com/FFmpeg/FFmpeg/commit/ed27bfcbbbc0872c0195eaf4dd2c0c93b9f1778e): terminate empty-search av_strireplace calls. FFmpeg's optional libxml2/DASH feature is also explicitly disabled. |
| [CVE-2026-107678](https://osv.dev/vulnerability/CVE-2026-107678) | [969c7eb0](https://github.com/FFmpeg/FFmpeg/commit/969c7eb091989a664cb7bf66a1f02051cc46cb52), [d1434518](https://github.com/FFmpeg/FFmpeg/commit/d1434518fbd03789e3e5dc0657a7a9f1123ce074), [71403a21](https://github.com/FFmpeg/FFmpeg/commit/71403a21b02a92b925e26e609e9bd3e48df451cf): iterative freeing plus linear serialized append and the MOV consumer. Both stack exhaustion and repeated quadratic pssh parsing are addressed. |
| [CVE-2026-107696](https://osv.dev/vulnerability/CVE-2026-107696) | [f5d4fd18](https://github.com/FFmpeg/FFmpeg/commit/f5d4fd186c68913240c4f6e9cda98443e1518b34): bound RTSP redirects. |

The additive encryption helper and its libavformat consumer are built together;
upstream API-history/library-version-only edits were omitted from that backport.
The patches apply to the exact pinned baseline. A bare OSV query of the baseline
commit can still report the five CVEs: interpreting that result requires the
applied patch hashes and the final build provenance, not silent suppression.

packaging/tests/test-native-ffmpeg.py compiles a client against the real libraries
and runs bounded, loopback-only regressions. The Flatpak FFmpeg post-install step
runs it before accepting the module. Tests cover empty-search termination,
deterministic HDR output, a long init-info list, MOV demuxing of 10,000 pssh boxes
with preserved record count, and an RTSP server that redirects to itself.

Independent old-versus-fixed Linux builds reproduced the old timeout, stack
exhaustion and unwritten-byte behavior; the patched builds passed. Controls use a
512 KiB child stack, a 256 MiB address-space bound and 2–3 second timeouts. Those
are controlled component tests, not a measured frequency of attacks against
ordinary platform streams. The existing avutil encryption-info and avstring tests
also pass. The disabled libssh path was source-reviewed, not exercised against a
real SFTP server.

## Corrected libxml2/libxslt in Linux lxml builds

PyPI's latest lxml remained 6.1.3 on 2026-11-09, and its ordinary Linux wheel
embedded libxml2 2.14.6. Merely retaining or renaming that wheel would not fix
[CVE-2026-86140](https://github.com/CVEProject/cvelistV5/blob/main/cves/2026/86xxx/CVE-2026-86140.json)
or [CVE-2026-86144](https://github.com/CVEProject/cvelistV5/blob/main/cves/2026/86xxx/CVE-2026-86144.json).
Both primary records identify libxml2 versions before 2.15.4 as affected; their
actual upstream fixes were verified.

packaging/build-secure-lxml.py therefore builds the existing lxml source release
against official, SHA256-verified libxml2 2.15.4 and libxslt/libexslt 1.1.45 sources
from native-sources.json. It uses static PIC libraries, without implicit
static-deps downloads, so AppImage pruning cannot replace the fixed XML libraries
with older host copies. It verifies actual imported native versions and rejects
ELF dependencies on shared libxml2/libxslt/libexslt.

Static XML archive symbols are hidden with `--exclude-libs,ALL`, including data
symbols. This prevents an older host library loaded globally by libmpv from
interposing the corrected functions or version globals. The helper deliberately
preloads host XML libraries with RTLD_GLOBAL before importing lxml, then verifies
native versions, XML/XPath/XSLT and objectify. The artifact diagnostic also loads
the application's video library before querying XML versions; a build/runtime
version mismatch fails the artifact gate.

The resulting wheel includes native source pins, builder hash and native license
texts under dist-info, with a regenerated RECORD. Optional cached reuse requires
matching current source pins and builder hash, a matching wheel hash and embedded
metadata, and another real runtime/ELF check. A corrupt or offline-missing source
never falls back to a binary wheel.

The helper has been built and exercised with Linux CPython 3.12: native versions
2.15.4/1.1.45, XML/XPath/XSLT smoke tests, and no host XML-library ELF dependencies.
The Flatpak runs the helper offline before python-deps; its generator offers only
the lxml source archive. Native sources and compiler/runtime context remain
visible in artifact evidence. Native licenses are exported with preserved paths
and checked hashes.

This fixes the configured Linux release build path. It does not repair an already
published v0.80 artifact, an arbitrary upstream wheel installed using pip, or a
Windows developer environment. Ordinary source installations must explicitly use
the hardened helper on Linux to obtain this native replacement. Windows source
testing is compatibility evidence and must not be reported as a native CVE fix.

## Host libraries, monitoring and Python compatibility

The standard AppImage still uses the user's host libmpv/FFmpeg. Flatpak backports
do not update those external libraries. Keep host packages patched; the optional
MPV_BUNDLE=1 build needs its own component/patch assessment and inventory.

The weekly advisory workflow queries exact lockfile PyPI versions and direct
native Git pins. Native commit matches need build/patch/path triage, and runtime
or embedded-library coverage is not inferred from a PyPI result alone. The
Flathub comparison separately requires every monitored module, resolves the known
LuaJIT submodule at its parent commit, rejects ambiguous/unrecognized inputs, and
distinguishes complete matches, version differences and unknown/errors. A
difference does not imply that Flathub's version is newer.

The release lock targets Python 3.12. Source compatibility jobs may resolve other
compatible dependency versions on Python 3.10/3.14; that is separate from the
hash-checked release path and from native-security evidence.
