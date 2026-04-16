"""
Binance BTC price proxy — runs on Railway alongside the tracker.

Railway can reach Binance; the dashboard (running in Indonesia) cannot.
This proxy simply forwards requests so the dashboard never calls Binance directly.

GET  /proxy/klines   → api.binance.com/api/v3/klines  (query params passed through)
WS   /proxy/ws       → stream.binance.com btcusdt@aggTrade
"""

import asyncio
import logging
import os

import aiohttp
from aiohttp import web, WSMsgType

log = logging.getLogger(__name__)

_BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
_BINANCE_WS     = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"

_CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


async def _klines(req: web.Request) -> web.Response:
    async with aiohttp.ClientSession() as s:
        async with s.get(
            _BINANCE_KLINES,
            params=dict(req.rel_url.query),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            return web.Response(
                text=await r.text(),
                content_type="application/json",
                headers=_CORS,
            )


async def _ws(req: web.Request) -> web.WebSocketResponse:
    browser = web.WebSocketResponse()
    await browser.prepare(req)

    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(_BINANCE_WS) as binance:

                async def pump():
                    async for msg in binance:
                        if browser.closed:
                            break
                        if msg.type == WSMsgType.TEXT:
                            await browser.send_str(msg.data)
                        elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                            break

                task = asyncio.ensure_future(pump())
                try:
                    async for _ in browser:
                        pass
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    except Exception as e:
        log.warning(f"[Proxy WS] {e}")

    return browser


async def run(shutdown: asyncio.Event) -> None:
    port = int(os.environ.get("PORT", 8080))

    app = web.Application()
    app.router.add_get("/proxy/klines", _klines)
    app.router.add_get("/proxy/ws",     _ws)
    app.router.add_route("OPTIONS", "/proxy/klines",
                         lambda r: web.Response(headers=_CORS))

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info(f"Proxy listening on :{port}")

    await shutdown.wait()
    await runner.cleanup()
    log.info("Proxy stopped.")
