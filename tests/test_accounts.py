# SPDX-License-Identifier: GPL-3.0-or-later
"""OAuth login, token refresh, sending and moderating against a fake API server."""
import asyncio
import json
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from multichat.accounts import KickAccount, TokenStore, TwitchAccount, oauth
from multichat.chat.models import ChatMessage


def test_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(TwitchAccount, "token_url", "http://127.0.0.1:18999/token")
    monkeypatch.setattr(KickAccount, "token_url", "http://127.0.0.1:18999/token")
    monkeypatch.setattr(TwitchAccount, "API", "http://127.0.0.1:18999/helix")
    monkeypatch.setattr(KickAccount, "API", "http://127.0.0.1:18999/kick")
    asyncio.run(_run(tmp_path / "tokens.json"))



calls = []
async def fake_token(req):
    d = dict(await req.post()); calls.append(("token", d))
    return web.json_response({"access_token": "AT-" + d["grant_type"], "refresh_token": "RT", "expires_in": 3600})
async def fake_api(req):
    body = await req.text()
    calls.append((req.method, req.path, dict(req.query), body, req.headers.get("Authorization"), req.headers.get("Client-Id")))
    if req.headers.get("Authorization") == "Bearer EXPIRED":
        return web.json_response({"message": "invalid token"}, status=401)
    if req.path.endswith("/users") and "login" in req.query:
        return web.json_response({"data": [{"id": "999"}]})
    if req.path == "/helix/users":
        return web.json_response({"data": [{"id": "1", "display_name": "TestUser"}]})
    if req.path == "/kick/users":
        return web.json_response({"data": [{"user_id": 7, "name": "testuser"}]})
    if req.path == "/kick/channels":
        return web.json_response({"data": [{"broadcaster_user_id": 555}]})
    if req.path == "/helix/chat/messages":
        return web.json_response({"data": [{"is_sent": True}]})
    return web.Response(status=204)

async def _run(tmp: Path):
    app = web.Application()
    app.router.add_post("/token", fake_token)
    app.router.add_route("*", "/{tail:.*}", fake_api)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18999).start()
    base = "http://127.0.0.1:18999"
    session = aiohttp.ClientSession()
    store = TokenStore(tmp)
    creds = lambda p: ("CID", "SECRET")

    # simulate the browser: call the callback with code + state
    def fake_open(url):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(url).query)
        assert q["redirect_uri"][0] == oauth.REDIRECT_URI
        fake_open.q = q
        import urllib.request
        time.sleep(0.2)
        urllib.request.urlopen(f"{oauth.REDIRECT_URI}?code=THECODE&state={q['state'][0]}").read()
        return True
    oauth.webbrowser.open = fake_open


    tw = TwitchAccount(store, creds, lambda: session)
    who = await tw.login()
    assert who == {"user_id": "1", "user_name": "TestUser"}, who
    assert calls[0][1]["code"] == "THECODE" and "code_verifier" not in calls[0][1]
    assert tw.logged_in and tw.user_name == "TestUser"
    msg = ChatMessage("twitch", "examplechan", "Troll", "spam", msg_id="m1", user_id="666")
    await tw.send("examplechan", "hello")
    await tw.delete(msg); await tw.timeout(msg, 600); await tw.ban(msg)
    api = [c for c in calls if c[0] != "token"]
    send = [c for c in api if c[1] == "/helix/chat/messages"][0]
    assert json.loads(send[3]) == {"broadcaster_id": "999", "sender_id": "1", "message": "hello"} and send[5] == "CID"
    dele = [c for c in api if c[1] == "/helix/moderation/chat"][0]
    assert dele[0] == "DELETE" and dele[2] == {"broadcaster_id": "999", "moderator_id": "1", "message_id": "m1"}
    bans = [c for c in api if c[1] == "/helix/moderation/bans"]
    assert json.loads(bans[0][3]) == {"data": {"user_id": "666", "duration": 600}} and json.loads(bans[1][3]) == {"data": {"user_id": "666"}}

    # 401 → refresh → retry
    store.update("twitch", access_token="EXPIRED")
    await tw.send("examplechan", "again")
    assert store.get("twitch")["access_token"] == "AT-refresh_token"

    kk = KickAccount(store, creds, lambda: session)
    who = await kk.login()
    tok = [c for c in calls if c[0] == "token"][-1][1]
    assert "code_verifier" in tok and fake_open.q["code_challenge_method"] == ["S256"]
    assert who == {"user_id": 7, "user_name": "testuser"}
    km = ChatMessage("kick", "examplekick", "Troll", "x", msg_id="uuid-1", user_id="88")
    await kk.send("examplekick", "moin"); await kk.delete(km); await kk.timeout(km, 600); await kk.ban(km)
    ks = [c for c in calls if c[0] != "token" and c[1].startswith("/kick")]
    assert any(c[1] == "/kick/chat/uuid-1" and c[0] == "DELETE" for c in ks)
    kb = [json.loads(c[3]) for c in ks if c[1] == "/kick/moderation/bans"]
    assert kb == [{"broadcaster_user_id": 555, "user_id": 88, "duration": 10}, {"broadcaster_user_id": 555, "user_id": 88}], kb
    assert oct(tmp.stat().st_mode & 0o777) == "0o600"

    await session.close()
    await runner.cleanup()
