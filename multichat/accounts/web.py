# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Voting in other channels' polls and predictions – with the websites' own login.

The platforms have no API for viewers to vote (their official APIs only let a broadcaster
start and end polls). Their websites vote through internal, undocumented interfaces with
the website login, not with an app login. So this takes the website login over from a
browser on this computer (read through yt-dlp's cookie reader, like yt-dlp's
--cookies-from-browser) and sends what the website sends when you click. Off by default:
the platforms don't allow it in their terms, and it breaks whenever a website changes.

Seen in Nov 2026 (requests recorded from the websites while voting):
  Kick     POST kick.com/api/v2/channels/<slug>/polls/vote        {"id": <option index>}
           POST kick.com/api/v2/channels/<slug>/predictions/vote  {"outcome_id", "amount"} (from the website code)
  Twitch   gql.twitch.tv ChannelPollContext_VoteInPoll (persisted query), MakePrediction (query text)
  YouTube  youtubei/v1/live_chat/send_live_chat_vote {"params"} – the params per option only come
           with a chat read logged in (get_live_chat), so the chat page is fetched again for a vote
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from urllib.parse import unquote

import aiohttp

from ..core.constants import APP_ID, BROWSER_UA
from ..core.i18n import _
from .oauth import ApiError

log = logging.getLogger("multichat.accounts")

BROWSERS = ("firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera")
# the cookies a vote needs, per platform (domain suffix, names; None = all of that domain)
COOKIES = {
    "twitch": (".twitch.tv", {"auth-token", "unique_id", "login", "persistent"}),
    "kick": ("kick.com", None),
    "youtube": (".youtube.com", None),
}
LOGIN_COOKIE = {"twitch": "auth-token", "kick": "session_token", "youtube": "SAPISID"}
# where yt-dlp looks for Firefox inside the Flatpak sandbox (its XDG config home is the sandbox's own)
FLATPAK_FIREFOX = ("~/.mozilla/firefox", "~/.var/app/org.mozilla.firefox")

TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_WEB_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # the Twitch website's own client ID
VOTE_IN_POLL = "1280e27b0f3c7ae60b5714bd569771ea50635778473182e6e959e2dcfcc16e3c"
MAKE_PREDICTION = ("mutation MakePrediction($input: MakePredictionInput!) {\n"
                   "  makePrediction(input: $input) {\n    error {\n      code\n    }\n  }\n}")
YOUTUBE = "https://www.youtube.com"


class NotLoggedIn(ApiError):
    pass


def _twitch_reason(code: str) -> str:
    """Twitch's error codes for votes and bets (from the website code) → what to show."""
    reasons = {
        "EVENT_MANAGER": _("you manage this prediction (your own channel) – Twitch doesn't let you bet"),
        "EVENT_NOT_ACTIVE": _("the bets are closed"),
        "NOT_ENOUGH_POINTS": _("not enough channel points"),
        "MAX_POINTS_PER_EVENT": _("more points than allowed on one prediction"),
        "MULTIPLE_OUTCOMES": _("you already bet on another outcome"),
        "MUST_ACCEPT_TOS": _("accept the predictions terms on the Twitch website once first"),
        "RATE_LIMITED": _("too many at once – try again in a moment"),
        "REGION_LOCKED": _("not available in your region"),
        "FORBIDDEN": _("not allowed in this channel"),
    }
    return reasons.get(code, code or "?")


def no_profile(browser: str) -> str:
    """Why the browser profile wasn't found – in the Flatpak, what to allow (the sandbox hides it)."""
    if not os.environ.get("FLATPAK_ID"):
        return _("No {browser} profile found on this computer").format(browser=browser.capitalize())
    if browser != "firefox":
        # yt-dlp looks for these under the sandbox's own ~/.config, so a permission alone doesn't help
        return _("As a Flatpak, MultiChat can only take the login over from Firefox. "
                 "Use Firefox or the AppImage for {browser}.").format(browser=browser.capitalize())
    command = "flatpak override --user " + " ".join(f"--filesystem={d}:ro" for d in FLATPAK_FIREFOX) + " " + APP_ID
    return _("The Flatpak may not read the Firefox profile. Allow it once in a terminal (read-only), "
             "then restart MultiChat:\n\n{command}").format(command=command)


def read_browser_cookies(browser: str, platform: str) -> dict[str, str]:
    """The platform's cookies from a browser profile on this computer (blocking: run in a thread)."""
    from yt_dlp.cookies import extract_cookies_from_browser

    domain, names = COOKIES[platform]
    try:
        jar = extract_cookies_from_browser(browser)
    except FileNotFoundError as e:  # yt-dlp: "could not find firefox cookies database in …"
        log.info("Web login %s: %s", browser, e)
        raise NotLoggedIn(no_profile(browser)) from e
    now = time.time()
    found = {}
    for c in jar:
        host = c.domain.lstrip(".")
        site = domain.lstrip(".")  # "kick.com" must not take "sidekick.com"
        if (host == site or host.endswith("." + site)) and (names is None or c.name in names) \
                and not (c.expires and c.expires < now):
            found[c.name] = c.value
    return found


class WebLogins:
    """The website logins taken over from a browser (in the secret store "web") and the votes."""

    def __init__(self, store, session) -> None:
        self.store = store
        self._session = session  # () → aiohttp.ClientSession (the window's)
        self._twitch_user: dict[str, str] = {}

    def cookies(self, platform: str) -> dict:
        return self.store.get(platform).get("cookies") or {}

    def has(self, platform: str) -> bool:
        return bool(self.cookies(platform).get(LOGIN_COOKIE[platform]))

    def name(self, platform: str) -> str:
        return self.store.get(platform).get("name", "")

    def take_over(self, platform: str, browser: str) -> str:
        """Reads the login from the browser and keeps it → what to show (blocking)."""
        found = read_browser_cookies(browser, platform)
        if not found.get(LOGIN_COOKIE[platform]):
            raise NotLoggedIn(_("Not logged in to {platform} in {browser} (or the browser profile wasn't found)")
                              .format(platform=platform.capitalize(), browser=browser.capitalize()))
        name = unquote(found.get("login", "")) if platform == "twitch" else ""
        self.store.replace(platform, {"cookies": found, "browser": browser, "name": name, "taken": int(time.time())})
        self._twitch_user.clear()
        return name or browser.capitalize()

    def forget(self, platform: str) -> None:
        self.store.delete(platform)
        self._twitch_user.clear()

    # ---- Kick ------------------------------------------------------------- #
    def _kick_post(self, path: str, body: dict) -> dict:
        """kick.com sits behind Cloudflare: like the reader, through curl_cffi with a browser fingerprint."""
        from curl_cffi import requests as cf  # type: ignore

        jar = self.cookies("kick")
        if not jar.get("session_token"):
            raise NotLoggedIn(_("No Kick website login – take it over under Accounts"))
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": "https://kick.com",
                   "Referer": "https://kick.com/", "X-App-Platform": "web",
                   "Authorization": "Bearer " + unquote(jar["session_token"])}
        if jar.get("XSRF-TOKEN"):
            headers["X-XSRF-TOKEN"] = unquote(jar["XSRF-TOKEN"])
        r = cf.post(f"https://kick.com/api/v2{path}", json=body, headers=headers, cookies=jar,
                    impersonate="chrome", timeout=15)
        try:
            data = r.json()
        except ValueError:
            data = {}
        status = (data.get("status") or {}) if isinstance(data, dict) else {}
        if r.status_code != 200 or status.get("error"):
            raise ApiError(f"Kick ({r.status_code}): {status.get('message') or r.text[:200]}", status=r.status_code)
        return data

    async def kick_poll(self, slug: str, option: int) -> None:
        await asyncio.to_thread(self._kick_post, f"/channels/{slug}/polls/vote", {"id": option})

    async def kick_prediction(self, slug: str, outcome: str, amount: int) -> None:
        await asyncio.to_thread(self._kick_post, f"/channels/{slug}/predictions/vote",
                                {"outcome_id": outcome, "amount": amount})

    # ---- Twitch ----------------------------------------------------------- #
    async def _gql(self, body, integrity: str = "") -> list:
        jar = self.cookies("twitch")
        if not jar.get("auth-token"):
            raise NotLoggedIn(_("No Twitch website login – take it over under Accounts"))
        headers = {"Client-Id": TWITCH_WEB_CLIENT, "Authorization": "OAuth " + jar["auth-token"],
                   "Content-Type": "text/plain;charset=UTF-8", "User-Agent": BROWSER_UA,
                   "Origin": "https://www.twitch.tv", "Referer": "https://www.twitch.tv/"}
        if jar.get("unique_id"):
            headers["X-Device-Id"] = jar["unique_id"]
        if integrity:
            headers["Client-Integrity"] = integrity
        async with self._session().post(TWITCH_GQL, data=json.dumps(body), headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
            text = await r.text()
            if r.status != 200:
                raise ApiError(f"Twitch ({r.status}): {text[:200]}", status=r.status)
        res = json.loads(text)
        res = res if isinstance(res, list) else [res]
        for x in res:
            for err in (x.get("errors") or []) if isinstance(x, dict) else []:
                msg = str(err.get("message") if isinstance(err, dict) else err)
                if "integrity" in msg.lower():
                    raise ApiError(_("Twitch only takes this vote from its website (integrity check) – "
                                     "vote in the browser"), status=403)
                raise ApiError(f"Twitch: {msg[:200]}")
        return res

    async def twitch_user_id(self) -> str:
        if "id" not in self._twitch_user:
            res = await self._gql({"query": "query { currentUser { id login } }"})
            user = ((res[0].get("data") or {}).get("currentUser") or {}) if res else {}
            if not user.get("id"):
                raise NotLoggedIn(_("The Twitch website login has expired – take it over again"))
            self._twitch_user.update(id=str(user["id"]), login=str(user.get("login") or ""))
        return self._twitch_user["id"]

    async def twitch_poll(self, poll_id: str, choice_id: str) -> None:
        user = await self.twitch_user_id()
        res = await self._gql([{"operationName": "ChannelPollContext_VoteInPoll", "variables": {"input": {
            "pollID": poll_id, "choiceID": choice_id, "userID": user, "voteID": uuid.uuid4().hex, "tokens": None}},
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": VOTE_IN_POLL}}}])
        error = (((res[0].get("data") or {}).get("voteInPoll") or {}).get("error") or {}) if res else {}
        if error:
            raise ApiError(_("Twitch refused the vote: {reason}").format(reason=_twitch_reason(error.get("code"))))

    async def twitch_prediction(self, event_id: str, outcome_id: str, points: int) -> None:
        res = await self._gql({"operationName": "MakePrediction", "query": MAKE_PREDICTION, "variables": {"input": {
            "eventID": event_id, "outcomeID": outcome_id, "points": points, "transactionID": uuid.uuid4().hex}}})
        error = (((res[0].get("data") or {}).get("makePrediction") or {}).get("error") or {}) if res else {}
        if error:
            raise ApiError(_("Twitch refused the prediction: {reason}").format(reason=_twitch_reason(error.get("code"))))

    # ---- YouTube ---------------------------------------------------------- #
    def _youtube_headers(self) -> dict:
        jar = self.cookies("youtube")
        sapisid = jar.get("SAPISID") or jar.get("__Secure-3PAPISID")
        if not sapisid:
            raise NotLoggedIn(_("No YouTube website login – take it over under Accounts"))
        ts = str(int(time.time()))
        digest = hashlib.sha1(f"{ts} {sapisid} {YOUTUBE}".encode()).hexdigest()
        return {"Authorization": f"SAPISIDHASH {ts}_{digest}", "X-Origin": YOUTUBE, "Origin": YOUTUBE,
                "X-Goog-AuthUser": "0", "User-Agent": BROWSER_UA, "Content-Type": "application/json",
                "Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())}

    async def youtube_poll(self, video_id: str, poll_id: str, option: int) -> None:
        """Reads the live chat logged in (only then the poll's options carry their vote params) and votes."""
        from ..chat import youtube_innertube as yti

        headers = self._youtube_headers()
        async with self._session().get(yti.LIVE_CHAT_URL, params={"is_popout": "1", "v": video_id},
                                       headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
            page = await r.text()
        context, token, _actions = yti.parse_page(page)
        # a running poll sits in the chat's action panel of the page data, else in the next chat batch
        params = vote_params(yti._find_json(page, "ytInitialData"), poll_id)
        if not params and token:
            async with self._session().post(yti.GET_LIVE_CHAT_URL, params={"prettyPrint": "false"}, headers=headers,
                                            json={"context": context, "continuation": token},
                                            timeout=aiohttp.ClientTimeout(total=20)) as r:
                params = vote_params(await r.json(content_type=None), poll_id)
        if not params:
            raise ApiError(_("YouTube didn't offer this poll for voting (ended, or the login has expired)"))
        if not 0 <= option < len(params):
            raise ApiError(_("This poll option doesn't exist"))
        async with self._session().post(f"{YOUTUBE}/youtubei/v1/live_chat/send_live_chat_vote",
                                        params={"prettyPrint": "false"}, headers=self._youtube_headers(),
                                        json={"context": context, "params": params[option]},
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                raise ApiError(f"YouTube ({r.status}): {(await r.text())[:200]}", status=r.status)


def vote_params(data, poll_id: str) -> list[str]:
    """The vote params of each option of a poll anywhere in a logged-in chat's data ([] if not there)."""
    def polls(o):
        if isinstance(o, dict):
            if "pollRenderer" in o and isinstance(o["pollRenderer"], dict):
                yield o["pollRenderer"]
            for v in o.values():
                yield from polls(v)
        elif isinstance(o, list):
            for v in o:
                yield from polls(v)

    for p in polls(data):
        if p.get("liveChatPollId") != poll_id:
            continue
        params = []
        for c in p.get("choices") or []:
            ep = (c.get("selectServiceEndpoint") or {}).get("sendLiveChatVoteEndpoint") or {} if isinstance(c, dict) else {}
            params.append(ep.get("params") if isinstance(ep.get("params"), str) else "")
        if params and all(params):
            return params
    return []
