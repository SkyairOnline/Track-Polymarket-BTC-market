"""
Monitors trades by a specific wallet using the Polymarket Data API.
CLOB /trades requires auth; Data API /activity is public and returns all needed fields.
"""

import asyncio
import logging
from typing import Callable, Optional, Set
from datetime import datetime, timezone
import aiohttp

DATA_BASE = "https://data-api.polymarket.com"
TRADER_ADDRESS = "0x7347d3291b321d2ee8d43d24aff244e7fb8c3d35"
POLL_INTERVAL = 15  # seconds

log = logging.getLogger(__name__)


class TraderMonitor:
    def __init__(self, callback: Callable):
        self._callback = callback  # async fn(trade_row: dict)
        self._condition_id: Optional[str] = None
        self._market_slug: Optional[str] = None
        self._seen: Set[str] = set()
        self._running = False

    def set_market(self, condition_id: str, market_slug: str, up_token_id: str = "", down_token_id: str = "") -> None:
        self._condition_id = condition_id
        self._market_slug = market_slug
        log.info(f"[Trader] Watching condition_id={condition_id[:16]}... slug={market_slug}")

    async def run(self) -> None:
        self._running = True
        async with aiohttp.ClientSession() as session:
            while self._running:
                if self._condition_id:
                    await self._poll(session)
                await asyncio.sleep(POLL_INTERVAL)

    def stop(self) -> None:
        self._running = False

    async def _poll(self, session: aiohttp.ClientSession) -> None:
        try:
            params = {"user": TRADER_ADDRESS, "limit": 100}
            async with session.get(
                f"{DATA_BASE}/activity",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    log.warning(f"[Trader] activity API returned {r.status}")
                    return
                activities = await r.json()
        except Exception as e:
            log.warning(f"[Trader] poll error: {e}")
            return

        if not isinstance(activities, list):
            log.warning(f"[Trader] unexpected response shape: {type(activities)}")
            return

        new_count = 0
        for act in activities:
            # Only process actual trades for the current market
            if act.get("type") != "TRADE":
                continue
            if act.get("conditionId") != self._condition_id:
                continue

            tx = act.get("transactionHash", "")
            if not tx or tx in self._seen:
                continue
            self._seen.add(tx)

            # Normalise into a clean row dict
            ts_unix = act.get("timestamp", 0)
            ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc).isoformat()

            trade_row = {
                "market_slug": self._market_slug,
                "ts": ts,
                "side": act.get("side", "BUY"),           # "BUY" or "SELL"
                "outcome": act.get("outcome", "Unknown"),  # "Up", "Down", "Yes", "No"
                "price": float(act.get("price", 0)),
                "size": float(act.get("usdcSize", act.get("size", 0))),  # prefer USDC size
                "transaction_hash": tx,
                "role": "activity",  # data API doesn't distinguish maker/taker
            }
            await self._callback(trade_row)
            new_count += 1

        if new_count:
            log.info(f"[Trader] {new_count} new trade(s) recorded in {self._market_slug}")
        else:
            log.debug(f"[Trader] polled — no new trades in current market")
