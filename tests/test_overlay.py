# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio

import aiohttp

from multichat.chat.kick import kick_parts
from multichat.chat.models import ChatMessage
from multichat.overlay.server import OverlayServer, message_payload

PORT = 18998


def test_overlay_page_and_websocket():
    async def run():
        ov = OverlayServer(asyncio.get_running_loop())
        await ov.start(PORT)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{PORT}/") as r:
                    assert r.status == 200 and "MultiChat Overlay" in await r.text()
                async with s.ws_connect(f"http://127.0.0.1:{PORT}/ws") as ws:
                    await asyncio.sleep(0.1)
                    parts = kick_parts("hi [emote:1:KEKW]")
                    ov.publish(message_payload(ChatMessage("kick", "c", "Bob", "hi", parts=parts)))
                    m = await asyncio.wait_for(ws.receive_json(), 3)
                    assert m["user"] == "Bob" and m["parts"][1]["t"] == "emote"
        finally:
            await ov.stop()

    asyncio.run(run())


def test_overlay_rejects_foreign_origin_and_host():
    async def run():
        ov = OverlayServer(asyncio.get_running_loop())
        await ov.start(PORT)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{PORT}/", headers={"Host": "evil.example:80"}) as r:
                    assert r.status == 403  # DNS rebinding
                async with s.get(f"http://127.0.0.1:{PORT}/ws", headers={"Origin": "https://evil.example"}) as r:
                    assert r.status == 403  # other website in the browser
                async with s.ws_connect(f"http://localhost:{PORT}/ws", origin=f"http://localhost:{PORT}") as ws:
                    assert not ws.closed  # the overlay page itself (OBS) is fine
        finally:
            await ov.stop()

    asyncio.run(run())
