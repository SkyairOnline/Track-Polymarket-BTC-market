"""
Monitors trades by a specific wallet address on Polymarket.
Polls the CLOB API every 15s for new trades in the current market.
"""

import asyncio
import logging
from typing import Callable, Set, Optional
import aiohttp
from config import CLOB_BASE

log = logging.getLogger(__name__)

TRADER_ADDRESS = "0x7347d3291b321d2ee8d43d24aff244e7fb8c3d35"
POLL_INTERVAL = 15  # seconds


class TraderMonitor:
    def __init__(self, callback: Callable):
        self._callback = callback  # async fn(trade, role, outcome)
        self._condition_id: Optional[str] = None
        self._up_token_id: Optional[str] = None
        self._down_token_id: Optional[str] = None
        self._seen: Set[str] = set()
        self._running = False

    def set_market(self, condition_id: str, up_token_id: str, down_token_id: str) -> None:
        self._condition_id = condition_id
        self._up_token_id = up_token_id
        self._down_token_id = down_token_id
        # Don't clear _seen — prevents reprocessing trades from previous markets

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
        for token_id in (self._up_token_id, self._down_token_id):
            if not token_id:
                continue
            for addr_field in ("maker_address", "taker_address"):
                trades = await self._fetch(session, addr_field, token_id)
                for trade in trades:
                    tx = trade.get("transaction_hash") or trade.get("id", "")
                    if not tx or tx in self._seen:
                        continue
                    if trade.get("market") != self._condition_id:
                        continue
                    self._seen.add(tx)
                    role = "maker" if addr_field == "maker_address" else "taker"
                    # Determine outcome from which token was traded
                    outcome = "Up" if trade.get("asset_id") == self._up_token_id else "Down"
                    await self._callback(trade, role, outcome)

    async def _fetch(self, session: aiohttp.ClientSession, addr_field: str, token_id: str) -> list:
        try:
            params = {addr_field: TRADER_ADDRESS, "asset_id": token_id, "limit": 50}
            async with session.get(
                f"{CLOB_BASE}/trades",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json()
                return data.get("data", []) if isinstance(data, dict) else (data or [])
        except Exception as e:
            log.warning(f"[Trader] fetch error ({addr_field}): {e}")
            return []
