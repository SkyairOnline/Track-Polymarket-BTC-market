"""
BTC price proxy — runs on Railway alongside the tracker.

Pulls from Coinbase Exchange (no geo-restrictions) and transforms the
response into the Binance format the dashboard already expects, so the
dashboard code needs zero changes.

GET  /proxy/klines   → Coinbase candles, returned as Binance kline array
WS   /proxy/ws       → Coinbase ticker, emitted as Binance aggTrade {p, T}
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
    Dashboard sends Binance params: startTime (ms), endTime (ms), limit, symbol, interval.
    We convert to Coinbase params, fetch, then return data in Binance kline format:
      [openTime_ms, open, high, low, close, volume, closeTime_ms, ...]
    Coinbase returns: [[time_sec, low, high, open, close, volume], ...] newest-first.
    """
    q = req.rel_url.query
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

    # Sort oldest-first (Coinbase returns newest-first)
    candles.sort(key=lambda c: c[0])

    # Binance kline: [openTime_ms, open, high, low, close, volume, closeTime_ms, ...]
    binance = [
        [
            c[0] * 1000,          # openTime ms
            str(c[3]),            # open
            str(c[2]),            # high
            str(c[1]),            # low
            str(c[4]),            # close
            str(c[5]),            # volume
            c[0] * 1000 + 59999,  # closeTime ms
            "0", 0, "0", "0", "0",
        ]
        for c in candles
    ]

    return web.Response(text=json.dumps(binance), content_type="application/json", headers=_CORS)


async def _ws(req: web.Request) -> web.WebSocketResponse:
    """
    Bridges browser ↔ Coinbase ticker WebSocket.
    Converts Coinbase ticker messages to Binance aggTrade format: {p, T}
    so the dashboard's existing ws.onmessage handler works unchanged.
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
                                # Parse Coinbase time: "2024-01-01T00:00:00.000000Z"
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
