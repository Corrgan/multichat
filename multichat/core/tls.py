# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""TLS setup that must happen before any library creates an SSL context.

aiohttp builds its default SSL context when it is imported, so the CA certificate
location has to be settled first – packaging/entry.py and app.main() call
ensure_ca_certificates() before anything else is imported.
"""
from __future__ import annotations

import os

# CA certificates, by distribution (OpenSSL's built-in default depends on where it was built)
CA_BUNDLES = (
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora, RHEL (canonical)
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL (older layout)
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch, SteamOS
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # Alpine, Void
)


def openssl_info() -> str:
    try:
        import ssl
    except ImportError as e:
        return f"unavailable ({e})"
    return f"{ssl.OPENSSL_VERSION} – {os.environ.get('MULTICHAT_OPENSSL', 'from the system')}"


def ensure_ca_certificates() -> str:
    """Makes sure TLS finds the system's CA certificates; returns where they come from.

    OpenSSL looks where it was configured at build time. If that doesn't exist here
    (e.g. a library built on Ubuntu running on Fedora), point it to the system bundle,
    or to certifi's as a last resort."""
    import ssl

    if os.environ.get("SSL_CERT_FILE"):
        return os.environ["SSL_CERT_FILE"]
    paths = ssl.get_default_verify_paths()
    if (paths.cafile and os.path.isfile(paths.cafile)) or (paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath)):
        return paths.cafile or paths.capath
    for cand in CA_BUNDLES:
        if os.path.isfile(cand):
            os.environ["SSL_CERT_FILE"] = cand
            return cand
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
        return certifi.where()
    except ImportError:
        return "none found"
