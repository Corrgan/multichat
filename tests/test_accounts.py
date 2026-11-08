# SPDX-License-Identifier: GPL-3.0-or-later
"""OAuth login, token refresh, sending and moderating against a fake API server."""
import asyncio
import json
import time
from pathlib import Path

import aiohttp
from aiohttp import web

from multichat.accounts import FileBackend, KickAccount, SecretStore, TwitchAccount, oauth
from multichat.chat.models import ChatMessage


def test_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(TwitchAccount, "token_url", "http://127.0.0.1:18999/token")
    monkeypatch.setattr(KickAccount, "token_url", "http://127.0.0.1:18999/token")
    monkeypatch.setattr(TwitchAccount, "API", "http://127.0.0.1:18999/helix")
    monkeypatch.setattr(KickAccount, "API", "http://127.0.0.1:18999/kick")
    asyncio.run(_run(tmp_path))



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

async def _run(tmp_dir: Path):
    app = web.Application()
    app.router.add_post("/token", fake_token)
    app.router.add_route("*", "/{tail:.*}", fake_api)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18999).start()
    session = aiohttp.ClientSession()
    store = SecretStore("tokens", FileBackend(tmp_dir))
    tmp = tmp_dir / "tokens.json"
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


def test_new_login_replaces_a_stuck_one(tmp_path, monkeypatch):
    """The platform showed an error page and never redirected back: a second attempt must not
    fail with "port in use" – it cancels the waiting one."""
    from multichat.accounts import YouTubeAccount

    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: True)  # the browser never comes back

    async def run():
        session = aiohttp.ClientSession()
        store = SecretStore("tokens", FileBackend(tmp_path))
        acc = YouTubeAccount(store, lambda p: ("CID", "SECRET"), lambda: session)
        first = asyncio.ensure_future(acc.login())
        await asyncio.sleep(0.3)
        second = asyncio.ensure_future(acc.login())
        await asyncio.sleep(0.3)
        assert first.cancelled()
        assert not second.done()  # listening on the port, waiting for the browser
        second.cancel()
        await asyncio.wait([second])
        await session.close()

    asyncio.run(run())


def test_logout_during_refresh_stays_logged_out(tmp_path):
    """The refresh answer arrives after the logout – it must not write tokens back."""
    from multichat.accounts import TwitchAccount

    async def run():
        gate = asyncio.Event()
        store = SecretStore("tokens", FileBackend(tmp_path))
        acc = TwitchAccount(store, lambda p: ("CID", "SECRET"), lambda: None)
        store.replace("twitch", {"access_token": "OLD", "refresh_token": "RT", "expires_at": 0, "user_name": "Me"})

        async def slow_token_request(data):
            await gate.wait()
            return {"access_token": "NEW", "refresh_token": "RT2", "expires_at": time.time() + 3600}

        acc._token_request = slow_token_request
        refresh = asyncio.ensure_future(acc._refresh("OLD"))
        await asyncio.sleep(0.05)
        acc.logout()
        gate.set()
        try:
            await refresh
        except Exception:  # noqa: BLE001 – "session expired" is fine
            pass
        assert not acc.logged_in and store.get("twitch") == {}
        assert SecretStore("tokens", FileBackend(tmp_path)).get("twitch") == {}  # also on disk

    asyncio.run(run())


class _FlakyKeyring:
    """Keyring that works, then fails to save (wallet closed) – entries stay readable."""

    def __init__(self):
        self.items, self.broken = {}, False

    def load(self, key):
        raw = self.items.get(key)
        return json.loads(raw) if raw else None

    def save(self, key, data):
        if self.broken:
            raise RuntimeError("wallet closed")
        self.items[key] = json.dumps(data)

    def delete(self, key):
        if self.broken:
            raise RuntimeError("wallet closed")
        self.items.pop(key, None)

    name = "keyring (test)"
    strict = False


def test_logout_while_keyring_fails_is_not_undone(tmp_path):
    kr = _FlakyKeyring()
    store = SecretStore("tokens", kr, FileBackend(tmp_path))
    store.update("twitch", access_token="AT", user_name="Me")
    kr.broken = True
    store.delete("twitch")  # logout: lands in the file, the keyring still has the old entry
    assert kr.load("tokens") == {"twitch": {"access_token": "AT", "user_name": "Me"}}
    kr.broken = False  # next start: keyring works again
    again = SecretStore("tokens", kr, FileBackend(tmp_path))
    assert again.get("twitch") == {}  # the newer file wins – still logged out
    assert kr.load("tokens") == {} and not (tmp_path / "tokens.json").exists()


def test_keyring_only_mode_never_writes_files(tmp_path, monkeypatch):
    from multichat.accounts import store as st

    monkeypatch.setenv("MULTICHAT_SECRETS", "keyring")
    monkeypatch.setattr(st, "system_keyring", lambda: None)
    backend, files = st.choose_backend(tmp_path)
    s = SecretStore("tokens", backend, files)
    s.update("twitch", access_token="AT")
    assert s.get("twitch") == {"access_token": "AT"} and not list(tmp_path.iterdir())
    kr = _FlakyKeyring()
    kr.strict, kr.broken = True, True
    s2 = SecretStore("tokens", kr, FileBackend(tmp_path))
    s2.update("twitch", access_token="AT")  # keyring fails: not saved, but no file either
    assert not list(tmp_path.iterdir())
