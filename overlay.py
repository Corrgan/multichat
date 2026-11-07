# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""OBS-Overlay: lokaler Webserver, den OBS als Browserquelle einbindet.

http://127.0.0.1:<port>/            → transparente Chat-Seite
http://127.0.0.1:<port>/ws          → WebSocket, über den neue Nachrichten kommen

URL-Parameter der Seite:
  fade=30           Nachrichten nach 30 s ausblenden (0 = nie)
  max=40            maximale Anzahl sichtbarer Nachrichten
  size=22           Schriftgröße in px
  platforms=twitch,kick   nur diese Plattformen zeigen
  badges=0          Plattform-Kürzel ausblenden
"""
from __future__ import annotations

import asyncio
import json

from aiohttp import WSMsgType, web

from clients import ChatMessage, Deletion

# Plattform-Symbole: die Favicons der Seiten, zur Laufzeit geladen (wie im Browser-Tab).
# Erst die Seite selbst, dann Googles Favicon-Dienst; schlägt beides fehl → Text-Kürzel.
FAVICONS = {
    "twitch": ["https://www.twitch.tv/favicon.ico", "https://www.google.com/s2/favicons?domain=twitch.tv&sz=32"],
    "kick": ["https://kick.com/favicon.ico", "https://www.google.com/s2/favicons?domain=kick.com&sz=32"],
    "youtube": ["https://www.youtube.com/favicon.ico", "https://www.google.com/s2/favicons?domain=youtube.com&sz=32"],
}

OVERLAY_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>MultiChat Overlay</title>
<style>
:root{--size:20px}
html,body{margin:0;background:transparent;overflow:hidden}
body{font:600 var(--size)/1.4 "Inter","Segoe UI","Noto Sans",sans-serif;color:#fff;
  text-shadow:0 1px 2px #000,0 0 6px rgba(0,0,0,.8)}
#chat{position:fixed;left:0;right:0;bottom:0;padding:10px;display:flex;flex-direction:column}
.m{margin:2px 0;overflow-wrap:anywhere;animation:in .25s ease-out;transition:opacity .8s}
.m.out{opacity:0}
.b{display:inline-block;min-width:1.6em;text-align:center;padding:0 .3em;margin-right:.35em;
  border-radius:4px;font-size:.62em;line-height:1.5;color:#000;text-shadow:none;vertical-align:.15em}
.u{margin-right:.15em}
img.e{height:1.5em;vertical-align:middle;margin:-.25em 0}
img.pi{width:1em;height:1em;vertical-align:-.12em;margin-right:.35em;filter:drop-shadow(0 0 2px rgba(0,0,0,.7))}
@keyframes in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
</style></head><body><div id="chat"></div>
<script>
const q=new URLSearchParams(location.search);
const fade=+q.get("fade")||0, max=+q.get("max")||40;
const show=(q.get("platforms")||"twitch,kick,youtube").split(",");
const badges=q.get("badges")!=="0";
if(q.get("size"))document.documentElement.style.setProperty("--size",q.get("size")+"px");
const B={twitch:["#9146FF","TW"],kick:["#53FC18","KI"],youtube:["#FF0000","YT"]};
const ICONS=__FAVICONS__;
function badge(p){
  const urls=[...(ICONS[p]||[])];
  const txt=()=>{const b=document.createElement("span");b.className="b";
    b.style.background=B[p][0];b.textContent=B[p][1];return b;};
  if(!urls.length)return txt();
  const i=new Image();i.className="pi";i.alt=B[p][1];
  i.onerror=()=>{urls.length?(i.src=urls.shift()):i.replaceWith(txt());};
  i.src=urls.shift();return i;
}
const chat=document.getElementById("chat");
function add(m){
  if(!show.includes(m.platform))return;
  const d=document.createElement("div");d.className="m";
  d.dataset.p=m.platform;d.dataset.mid=m.msg_id||"";d.dataset.uid=m.user_id||"";
  if(badges){d.append(badge(m.platform));}
  const u=document.createElement("span");u.className="u";u.style.color=m.color||"#b9a3e3";
  u.textContent=m.user+":";d.append(u," ");
  for(const p of m.parts){
    if(p.t==="emote"){const i=new Image();i.className="e";i.src=p.v;i.alt=p.a;
      i.onerror=()=>i.replaceWith(p.a);d.append(i);}
    else d.append(p.v);
  }
  chat.append(d);
  while(chat.children.length>max)chat.firstChild.remove();
  if(fade>0)setTimeout(()=>{d.classList.add("out");setTimeout(()=>d.remove(),900)},fade*1000);
}
function del(e){
  for(const d of [...chat.children]){
    if(d.dataset.p!==e.platform)continue;
    if(e.all||(e.msg_id&&d.dataset.mid===e.msg_id)||(e.user_id&&d.dataset.uid===e.user_id))d.remove();
  }
}
function connect(){
  const ws=new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage=ev=>{const e=JSON.parse(ev.data);e.type==="msg"?add(e):del(e);};
  ws.onclose=()=>setTimeout(connect,2000);
}
connect();
</script></body></html>"""


def message_payload(m: ChatMessage) -> dict:
    return {
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
        self.clients: set[web.WebSocketResponse] = set()
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

    async def _index(self, request: web.Request) -> web.Response:
        page = OVERLAY_HTML.replace("__FAVICONS__", json.dumps(FAVICONS))
        return web.Response(text=page, content_type="text/html")

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.clients.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
        return ws

    def publish(self, payload: dict) -> None:
        """Thread-sicher: darf aus dem Qt-Thread aufgerufen werden."""
        if self.runner:
            self.loop.call_soon_threadsafe(self._broadcast, json.dumps(payload))

    def _broadcast(self, data: str) -> None:
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, data))

    async def _send(self, ws: web.WebSocketResponse, data: str) -> None:
        try:
            await ws.send_str(data)
        except Exception:  # noqa: BLE001
            self.clients.discard(ws)
