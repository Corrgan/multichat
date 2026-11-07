# SPDX-License-Identifier: GPL-3.0-or-later
"""Robustness: reconnect backoff, single token refresh, Pusher credentials, overlay order."""
import asyncio
import time

import aiohttp
from aiohttp import web

from multichat.accounts import FileBackend, SecretStore, TwitchAccount
from multichat.chat.kick import _find_pusher_creds
from multichat.chat.models import Backoff
from multichat.chat.twitch import twitch_parts
from multichat.overlay.server import OverlayServer


def test_backoff_grows_and_resets(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    b = Backoff(first=5, maximum=120, stable=60)
    delays = [b.next() for _ in range(7)]
    assert 4 <= delays[0] <= 6 and delays[-1] <= 138 and delays[3] > delays[1]
    b.connected()
    now[0] += 61  # the connection held for a minute
    assert b.next() <= 6


def test_pusher_credentials_are_validated():
    assert _find_pusher_creds({"data": {"app_key": "34bf7a0ff419a2a775b9", "cluster": "us2"}}) == ("34bf7a0ff419a2a775b9", "us2")
    assert _find_pusher_creds({"app_key": "34bf7a0ff419a2a775b9", "cluster": "evil.example/x#"}) is None
    assert _find_pusher_creds({"app_key": "../x", "cluster": "us2"}) is None


def test_malformed_twitch_emote_tags_are_ignored():
    assert twitch_parts("hi", "25:0-99") == [("text", "hi", "")]
    assert twitch_parts("hi", "25:x-y,1") == [("text", "hi", "")]
    assert twitch_parts("Kappa", "a.b:0-4") == [("text", "Kappa", "")]


def test_concurrent_requests_refresh_once(tmp_path, monkeypatch):
    refreshes = []

    async def token(req):
        d = dict(await req.post())
        refreshes.append(d["refresh_token"])
        await asyncio.sleep(0.05)
        if d["refresh_token"] != "RT1":  # rotated tokens: the old one is invalid afterwards
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600})

    async def api(req):
        ok = req.headers.get("Authorization") == "Bearer AT2"
        return web.json_response({"data": []}, status=200 if ok else 401)

    async def run():
        app = web.Application()
        app.router.add_post("/token", token)
        app.router.add_get("/api", api)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 18997).start()
        monkeypatch.setattr(TwitchAccount, "token_url", "http://127.0.0.1:18997/token")
        store = SecretStore("tokens", FileBackend(tmp_path))
        store.replace("twitch", {"access_token": "AT1", "refresh_token": "RT1", "expires_at": time.time() - 1})
        async with aiohttp.ClientSession() as s:
            acc = TwitchAccount(store, lambda p: ("CID", "SEC"), lambda: s)
            await asyncio.gather(*(acc.request("GET", "http://127.0.0.1:18997/api") for _ in range(5)))
            assert refreshes == ["RT1"] and acc.logged_in

            store.update("twitch", access_token="AT1", expires_at=time.time() - 1)  # RT2 is unknown to the fake
            try:
                await acc.request("GET", "http://127.0.0.1:18997/api")
                raise AssertionError("expected an error")
            except Exception as e:  # noqa: BLE001
                assert getattr(e, "status", None) == 401
            assert not acc.logged_in  # rejected refresh token → logged out cleanly
        await runner.cleanup()

    asyncio.run(run())


def test_overlay_keeps_order():
    async def run():
        ov = OverlayServer(asyncio.get_running_loop())
        await ov.start(18996)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect("http://127.0.0.1:18996/ws") as ws:
                    await asyncio.sleep(0.05)
                    for i in range(200):
                        ov._broadcast(str(i))
                    got = [(await asyncio.wait_for(ws.receive(), 3)).data for _ in range(200)]
                    assert got == [str(i) for i in range(200)]
        finally:
            await ov.stop()

    asyncio.run(run())
