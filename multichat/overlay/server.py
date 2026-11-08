# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""OBS overlay: local web server that OBS adds as a browser source.

http://127.0.0.1:<port>/            → transparent chat page
http://127.0.0.1:<port>/ws          → WebSocket that delivers new messages

URL parameters of the page:
  fade=30           fade out messages after 30 s (0 = never)
  max=40            maximum number of visible messages
  size=22           font size in px
  platforms=twitch,kick   show only these platforms
  badges=0          hide the platform badges
"""
from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import WSMsgType, web

from ..chat import alerts
from ..chat.models import ChatMessage, Deletion
from ..core.constants import FAVICONS
from ..core.paths import resource

log = logging.getLogger("multichat.overlay")

MAX_CLIENTS = 16
MAX_BACKLOG = 500  # messages queued for a slow client before it is disconnected





def message_payload(m: ChatMessage, alerts_cfg: dict | None = None) -> dict:
    return {
        "event": m.event,
        "headline": alerts.headline(m) if m.event else "",
        "alert": bool(m.event) and alerts.is_alert(m, alerts_cfg or alerts.DEFAULT_ALERTS),
        "type": "msg",
        "platform": m.platform,
        "user": m.user,
        "color": m.color,
        "msg_id": m.msg_id,
        "user_id": m.user_id,
        "parts": [{"t": k, "v": v, "a": a} for k, v, a in m.parts],
    }


def deletion_payload(e: Deletion) -> dict:
    return {"type": "del", "platform": e.platform, "msg_id": e.msg_id, "user_id": e.user_id, "all": e.clear_all}


class OverlayServer:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.clients: dict[web.WebSocketResponse, asyncio.Queue] = {}
        self._page: str | None = None
        self.runner: web.AppRunner | None = None
        self.port: int | None = None

    @property
    def running(self) -> bool:
        return self.runner is not None

    async def start(self, port: int) -> None:
        if self.runner:
            return
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", port).start()
        except OSError:
            await runner.cleanup()
            raise
        self.runner, self.port = runner, port

    async def stop(self) -> None:
        for ws in list(self.clients):
            await ws.close()
        self.clients.clear()
        if self.runner:
            await self.runner.cleanup()
        self.runner = None

    def _allowed(self, request: web.Request) -> bool:
        """Only this machine: the Host header stops DNS rebinding, the Origin header stops
        other websites in the browser from reading the chat over the WebSocket."""
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        origin = request.headers.get("Origin")
        if request.host not in hosts or (origin is not None and origin not in {f"http://{h}" for h in hosts}):
            log.warning("Overlay: rejected request (Host %r, Origin %r)", request.host, origin)
            return False
        return True

    async def _index(self, request: web.Request) -> web.Response:
        if not self._allowed(request):
            return web.Response(status=403, text="forbidden")
        if self._page is None:
            self._page = resource("overlay.html").read_text(encoding="utf-8").replace("__FAVICONS__", json.dumps(FAVICONS))
        return web.Response(text=self._page, content_type="text/html",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                     # the page only talks to this server and shows https images
                                     "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                     "style-src 'unsafe-inline'; img-src https: data:; "
                                     f"connect-src 'self' ws://127.0.0.1:{self.port} ws://localhost:{self.port}"})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        if not self._allowed(request):
            return web.Response(status=403, text="forbidden")
        if len(self.clients) >= MAX_CLIENTS:
            return web.Response(status=503, text="too many overlay clients")
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=4096)  # the page never sends anything big
        await ws.prepare(request)
        queue: asyncio.Queue = asyncio.Queue(MAX_BACKLOG)
        self.clients[ws] = queue
        writer = asyncio.ensure_future(self._writer(ws, queue))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.pop(ws, None)
            writer.cancel()
        return ws

    async def _writer(self, ws: web.WebSocketResponse, queue: asyncio.Queue) -> None:
        """One writer per client keeps the order of the messages."""
        try:
            while True:
                await ws.send_str(await queue.get())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 – client gone
            self.clients.pop(ws, None)

    def publish(self, payload: dict) -> None:
        """Thread-safe: may be called from the Qt thread."""
        if self.runner:
            self.loop.call_soon_threadsafe(self._broadcast, json.dumps(payload))

    def _broadcast(self, data: str) -> None:
        for ws, queue in list(self.clients.items()):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:  # the browser source stopped reading
                log.warning("Overlay: client too slow – disconnecting it")
                self.clients.pop(ws, None)
                asyncio.ensure_future(ws.close())
