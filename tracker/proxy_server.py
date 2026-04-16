"""
Coinbase BTC price proxy — runs on Railway alongside the tracker.

Fetches from Coinbase Exchange (globally accessible) and transforms
responses into the format the dashboard expects.

GET  /proxy/klines   → Coinbase candles as kline array [openTime_ms, open, high, low, close, vol, ...]
WS   /proxy/ws       → Coinbase ticker stream as {p: price, T: timestamp_ms}
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import aiohttp
from aiohttp import web, WSMsgType

log = logging.getLogger(__name__)

_COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
_COINBASE_WS      = "wss://ws-feed.exchange.coinbase.com"
_COINBASE_SUB     = json.dumps({
    "type": "subscribe",
    "product_ids": ["BTC-USD"],
    "channels": ["ticker"],
})

_CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


async def _klines(req: web.Request) -> web.Response:
    """
    Accepts: startTime (ms), endTime (ms, optional), limit (optional), symbol, interval (ignored).
    Returns: kline array — [openTime_ms, open, high, low, close, volume, closeTime_ms, ...]
    Coinbase candle: [time_sec, low, high, open, close, volume] newest-first.
    """
    q        = req.rel_url.query
    start_ms = int(q.get("startTime", 0))
    limit    = int(q.get("limit", 6))
    end_ms   = int(q["endTime"]) if "endTime" in q else start_ms + limit * 60 * 1000

    async with aiohttp.ClientSession() as s:
        async with s.get(
            _COINBASE_CANDLES,
            params={"granularity": 60, "start": _ms_to_iso(start_ms), "end": _ms_to_iso(end_ms)},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            candles = await r.json()

    # Guard: Coinbase returns a dict on error (e.g. {"message": "..."})
    if not isinstance(candles, list) or not candles:
        return web.Response(text="[]", content_type="application/json", headers=_CORS)

    candles.sort(key=lambda c: c[0])  # oldest-first

    # Map to kline format: [openTime_ms, open, high, low, close, volume, closeTime_ms, ...]
    # Coinbase indices:      0=time,      3=open, 2=high, 1=low, 4=close, 5=volume
    klines = [
        [
            c[0] * 1000,         # openTime ms
            str(c[3]),           # open
            str(c[2]),           # high
            str(c[1]),           # low
            str(c[4]),           # close
            str(c[5]),           # volume
            c[0] * 1000 + 59999, # closeTime ms
            "0", 0, "0", "0", "0",
        ]
        for c in candles
    ]

    return web.Response(text=json.dumps(klines), content_type="application/json", headers=_CORS)


async def _ws(req: web.Request) -> web.WebSocketResponse:
    """
    Bridges browser ↔ Coinbase ticker WebSocket.
    Each message forwarded to browser: {p: "price_str", T: timestamp_ms}
    """
    browser = web.WebSocketResponse()
    await browser.prepare(req)

    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(_COINBASE_WS) as cb:
                await cb.send_str(_COINBASE_SUB)

                async def pump():
                    async for msg in cb:
                        if browser.closed:
                            break
                        if msg.type == WSMsgType.TEXT:
                            d = json.loads(msg.data)
                            if d.get("type") == "ticker" and "price" in d:
                                t = d["time"].rstrip("Z").split(".")[0]
                                ts_ms = int(datetime.fromisoformat(t + "+00:00").timestamp() * 1000)
                                await browser.send_str(json.dumps({"p": d["price"], "T": ts_ms}))
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
