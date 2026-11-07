# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Names, links and other constants shared across the app."""
from __future__ import annotations

APP_ID = "multichat"  # Wayland app ID – must match multichat.desktop
REPO_URL = "https://github.com/Corrgan/multichat"
LOGIN_DOCS = f"{REPO_URL}/blob/main/docs/LOGIN.md"

PLATFORMS = ("twitch", "kick", "youtube")
NAMES = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}
BADGES = {"twitch": ("#9146FF", "TW"), "kick": ("#53FC18", "KI"), "youtube": ("#FF0000", "YT")}
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

# Platform badges: the sites' favicons, loaded at runtime (as in a browser tab).
# First the site itself, then Google's favicon service; if both fail → text abbreviation.
FAVICONS = {
    "twitch": ["https://www.twitch.tv/favicon.ico", "https://www.google.com/s2/favicons?domain=twitch.tv&sz=32"],
    "kick": ["https://kick.com/favicon.ico", "https://www.google.com/s2/favicons?domain=kick.com&sz=32"],
    "youtube": ["https://www.youtube.com/favicon.ico", "https://www.google.com/s2/favicons?domain=youtube.com&sz=32"],
}
