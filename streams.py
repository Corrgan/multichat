"""Findet die abspielbare Video-URL eines Livestreams (blockierend → in einem Thread aufrufen).

- Twitch:  yt-dlp
- Kick:    `playback_url` aus der Kick-Website-API (gleiche Abfrage wie der Chat)
- YouTube: yt-dlp (Kanal → …/live, oder direkt eine Video-ID)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from clients import _fetch_kick_json, youtube_target

log = logging.getLogger("multichat.streams")

QUALITIES = [("360p", 360), ("480p", 480), ("720p", 720), ("Quelle", 0)]
DEFAULT_QUALITY = "480p"
_OFFLINE_HINTS = (
    "offline",
    "not currently live",
    "is not live",
    "will begin",
    "premieres in",
    "live event will",
    "does not exist",
)


class Offline(Exception):
    pass


@dataclass
class Stream:
    url: str
    title: str = ""
    audio_url: str | None = None
    headers: dict = field(default_factory=dict)
    ytdl_format: str | None = None  # gesetzt → mpv lädt die URL selbst über yt-dlp (ytdl_hook)


def quality_height(name: str) -> int:
    return dict(QUALITIES).get(name, 480)


def pick_hls_variant(master: str, base_url: str, max_h: int) -> str:
    """Wählt aus einer HLS-Master-Playlist die beste Variante ≤ max_h (0 = beste)."""
    lines = master.splitlines()
    variants = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        res = re.search(r"RESOLUTION=\d+x(\d+)", line)
        bw = re.search(r"[^-]BANDWIDTH=(\d+)", line)
        uri = next((x.strip() for x in lines[i + 1 :] if x.strip() and not x.startswith("#")), None)
        if uri:
            variants.append((int(res.group(1)) if res else 0, int(bw.group(1)) if bw else 0, urljoin(base_url, uri)))
    if not variants:
        return base_url
    fitting = [v for v in variants if not max_h or v[0] <= max_h] or [min(variants)]
    return max(fitting)[2]


class _YtdlpLog:
    """Leitet yt-dlp-Meldungen ins Log und merkt sich Warnungen."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)


def _js_missing(texts: list[str]) -> bool:
    return any("javascript runtime" in t.lower() or "js runtime" in t.lower() for t in texts)


def _ytdlp(url: str, max_h: int) -> Stream:
    import yt_dlp  # type: ignore

    muxed = "[vcodec!=none][acodec!=none]"
    fmt = f"best[height<={max_h}]{muxed}/best{muxed}/best" if max_h else f"best{muxed}/best"
    ylog = _YtdlpLog()
    opts = {
        "quiet": True,
        "noplaylist": True,
        "format": fmt,
        "skip_download": True,
        "logger": ylog,
        # YouTube braucht eine JS-Laufzeit; jede installierte zulassen
        "js_runtimes": {"deno": {}, "node": {}, "bun": {}, "quickjs": {}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        low = msg.lower()
        if any(h in low for h in _OFFLINE_HINTS):
            raise Offline("offline") from e
        if _js_missing([msg, *ylog.warnings]):
            raise RuntimeError("keine JavaScript-Laufzeit gefunden – Node.js oder Deno installieren") from e
        raise RuntimeError(re.sub(r"^ERROR:\s*(\[[\w:]+\]\s*)?", "", msg).strip()[:160]) from e
    if info.get("_type") == "playlist":
        raise Offline("offline")
    title = info.get("title") or ""
    if info.get("requested_formats"):
        v, a = info["requested_formats"][:2]
        return Stream(v["url"], title, a["url"], v.get("http_headers") or {})
    return Stream(info["url"], title, None, info.get("http_headers") or {})


def _http_get(url: str, **kw):
    try:
        from curl_cffi import requests as cf  # type: ignore

        return cf.get(url, impersonate="chrome", timeout=15, **kw)
    except ImportError:
        import requests

        return requests.get(url, timeout=15, **kw)


def ytdl_format(max_h: int) -> str:
    """Formatwahl für mpvs ytdl_hook: getrennte Spuren erlaubt (YouTube-Live liefert oft nur DASH)."""
    if max_h:
        return f"bv*[height<={max_h}]+ba/b[height<={max_h}]/bv*+ba/b"
    return "bv*+ba/b"


_SINKHOLES = {"0.0.0.0", "::", "127.0.0.1", "::1"}


def check_dns(host: str) -> None:
    """Erkennt DNS-Blocker (Pi-hole, AdGuard …), die einen Host auf 0.0.0.0 umleiten."""
    import socket

    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except OSError as e:
        log.warning("DNS: %s nicht auflösbar: %s", host, e)
        raise RuntimeError(f"{host} ist nicht auflösbar (DNS)") from e
    log.debug("DNS %s → %s", host, ", ".join(sorted(addrs)))
    if addrs and addrs <= _SINKHOLES:
        log.warning("DNS: %s wird auf %s umgeleitet – DNS-Blocker?", host, ", ".join(sorted(addrs)))
        raise RuntimeError(f"{host} wird von deinem DNS-Blocker gesperrt (→ {', '.join(sorted(addrs))})")


def _youtube(value: str, max_h: int) -> Stream:
    """Findet das Live-Video über die Kanalseite; abspielen übernimmt mpv mit yt-dlp."""
    from clients import YT_COOKIES, YT_HEADERS, parse_live_page

    kind, target = youtube_target(value)
    check_dns("manifest.googlevideo.com")
    if kind == "video":
        return Stream(f"https://www.youtube.com/watch?v={target}", "", ytdl_format=ytdl_format(max_h))
    page_url = target.rstrip("/") + "/live"
    r = _http_get(page_url, headers=YT_HEADERS, cookies=YT_COOKIES)
    if r.status_code == 404:
        raise RuntimeError("YouTube-Kanal nicht gefunden")
    if r.status_code != 200:
        raise RuntimeError(f"YouTube-Seite HTTP {r.status_code}")
    live = parse_live_page(r.text)
    log.debug("YouTube %s: live=%s", page_url, live)
    if not live:
        raise Offline("offline")
    vid, title = live
    log.info("YouTube: Live-Video %s (%s) – mpv lädt es über yt-dlp", vid, title)
    return Stream(f"https://www.youtube.com/watch?v={vid}", title, ytdl_format=ytdl_format(max_h))


def _kick(slug: str, max_h: int) -> Stream:
    data = _fetch_kick_json(slug.lower().strip())
    live = data.get("livestream")
    url = data.get("playback_url")
    if not live or not url:
        log.debug("Kick %s: offline (livestream=%s, playback_url=%s)", slug, bool(live), bool(url))
        raise Offline("offline")
    try:
        from curl_cffi import requests as cf  # type: ignore

        r = cf.get(url, impersonate="chrome", timeout=15)
    except ImportError:
        import requests

        r = requests.get(url, timeout=15)
    if r.status_code != 200:
        log.warning("Kick-Playlist HTTP %s: %s", r.status_code, r.text[:200])
        raise RuntimeError(f"Kick-Playlist HTTP {r.status_code}")
    chosen = pick_hls_variant(r.text, url, max_h)
    log.info("Kick %s: Variante %s", slug, chosen.split("?")[0][-60:])
    return Stream(chosen, live.get("session_title") or "")


def resolve(platform: str, value: str, quality: str) -> Stream:
    """Liefert den Stream oder wirft Offline / RuntimeError."""
    max_h = quality_height(quality)
    value = value.strip()
    log.debug("Suche Stream: %s %r (%s)", platform, value, quality)
    if platform == "twitch":
        return _ytdlp(f"https://www.twitch.tv/{value.lower().lstrip('#')}", max_h)
    if platform == "kick":
        return _kick(value, max_h)
    return _youtube(value, max_h)
