"""
BTC Price Monitor
-----------------
Polls Binance for live BTC/USDT price every 3 seconds.
On market start, fetches the 5-min kline open as the "price to beat".
Detects divergence: BTC price direction disagrees with market YES/NO probability.

Divergence definitions:
  bearish — BTC dropped below open price, but NO best ask <= 50% (market still expects UP)
  bullish — BTC rose above open price, but YES best ask <= 50% (market still expects DOWN)

Fires on_divergence callback at most once per DIVERGENCE_COOLDOWN seconds per direction.
"""

import asyncio
import aiohttp
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

BINANCE_KLINE_URL  = "https://api.binance.com/api/v3/klines"
BINANCE_PRICE_URL  = "https://api.binance.com/api/v3/ticker/price"
POLL_INTERVAL      = 3    # seconds between price polls
DIVERGENCE_COOLDOWN = 30  # seconds between same-direction alerts


class BtcMonitor:
    def __init__(self, on_divergence):
        # on_divergence: async callable(market_slug, ts, btc_price, price_to_beat,
        #                               direction, up_best_ask, down_best_ask)
        self._on_divergence  = on_divergence
        self._market_slug    = None
        self._window_ts      = None
        self.price_to_beat   = None   # open of current 5-min kline
        self.btc_price       = None   # latest BTC price
        self._last_up_ask    = None
        self._last_down_ask  = None
        self._last_alert: dict[str, datetime] = {}
        self._running        = False

    def set_market(self, market_slug: str, window_ts: int) -> None:
        self._market_slug  = market_slug
        self._window_ts    = window_ts
        self.price_to_beat = None
        self._last_alert   = {}

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                if self._window_ts and self.price_to_beat is None:
                    await self._fetch_price_to_beat()
                await self._fetch_current_price()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error(f"[BTC] poll error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    async def check_divergence(self, up_ask: float, down_ask: float) -> None:
        """Call from on_price() after each price update."""
        if self.btc_price is None or self.price_to_beat is None or not self._market_slug:
            return
        self._last_up_ask   = up_ask
        self._last_down_ask = down_ask

        now = datetime.now(timezone.utc)

        if self.btc_price < self.price_to_beat and down_ask <= 0.50:
            await self._maybe_fire("bearish", now)
        elif self.btc_price > self.price_to_beat and up_ask <= 0.50:
            await self._maybe_fire("bullish", now)

    async def _maybe_fire(self, direction: str, now: datetime) -> None:
        last = self._last_alert.get(direction)
        if last and (now - last).total_seconds() < DIVERGENCE_COOLDOWN:
            return
        self._last_alert[direction] = now
        log.warning(
            f"[BTC] DIVERGENCE [{direction.upper()}] "
            f"BTC=${self.btc_price:,.2f} open=${self.price_to_beat:,.2f} "
            f"YES={self._last_up_ask:.1%} NO={self._last_down_ask:.1%}"
        )
        await self._on_divergence(
            market_slug=self._market_slug,
            ts=now.isoformat(),
            btc_price=self.btc_price,
            price_to_beat=self.price_to_beat,
            direction=direction,
            up_best_ask=self._last_up_ask,
            down_best_ask=self._last_down_ask,
        )

    async def _fetch_price_to_beat(self) -> None:
        start_ms = self._window_ts * 1000
        async with aiohttp.ClientSession() as s:
            async with s.get(
                BINANCE_KLINE_URL,
                params={"symbol": "BTCUSDT", "interval": "5m", "startTime": start_ms, "limit": 1},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
        if data:
            self.price_to_beat = float(data[0][1])  # kline open price
            log.info(f"[BTC] Price to beat (5m open): ${self.price_to_beat:,.2f}")

    async def _fetch_current_price(self) -> None:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                BINANCE_PRICE_URL,
                params={"symbol": "BTCUSDT"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
        self.btc_price = float(data["price"])
