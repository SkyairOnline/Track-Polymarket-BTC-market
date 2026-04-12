import asyncio
import json
import logging
from typing import Callable, Optional, Tuple
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed
from config import WS_URL, CLOB_BASE, WS_PING_INTERVAL, REST_POLL_INTERVAL

log = logging.getLogger(__name__)

# Keys stored per token
_BOOK_KEYS = ("best_ask", "worst_ask", "best_bid", "worst_bid")


class PriceTracker:
    """
    Tracks all 4 price points per token: best ask, worst ask, best bid, worst bid.

    Strategy:
      - WebSocket notifies us when the orderbook changes (fast).
      - On each WS event we fire GET /book for that token (source of truth for all 4 values).
      - REST /book fallback when WS is down.

    Callback: async fn(up: dict, down: dict, source: str)
      up/down dicts have keys: best_ask, worst_ask, best_bid, worst_bid
    """

    def __init__(self, up_token_id: str, down_token_id: str, callback: Callable):
        self._up_id = up_token_id
        self._down_id = down_token_id
        self._callback = callback
        self._running = False
        self._switch_tokens: Optional[Tuple[str, str]] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._book: dict = self._empty_book()

    def _empty_book(self) -> dict:
        return {
            self._up_id:   {k: None for k in _BOOK_KEYS},
            self._down_id: {k: None for k in _BOOK_KEYS},
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self):
        self._running = True
        while self._running:
            try:
                await self._ws_session()
            except Exception as e:
                log.warning(f"[WS] session ended ({e}), switching to REST polling")
                await self._rest_fallback(duration=10)

    def stop(self):
        self._running = False

    async def switch_market(self, up_id: str, down_id: str):
        self._switch_tokens = (up_id, down_id)
        if self._ws and not self._ws.closed:
            await self._ws.close()

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def _ws_session(self):
        if self._switch_tokens:
            self._up_id, self._down_id = self._switch_tokens
            self._switch_tokens = None
            self._book = self._empty_book()
            log.info(f"[WS] Switched tokens — up={self._up_id[:12]}... down={self._down_id[:12]}...")

        log.info(f"[WS] Connecting to {WS_URL}")
        async with websockets.connect(WS_URL, ping_interval=None) as ws:
            self._ws = ws
            await ws.send(json.dumps({
                "type": "market",
                "assets_ids": [self._up_id, self._down_id],
            }))
            log.info("[WS] Subscribed to UP + DOWN tokens")

            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if self._switch_tokens:
                        break
                    await self._handle_raw(raw)
            except ConnectionClosed:
                pass
            finally:
                ping_task.cancel()
                self._ws = None

    async def _ping_loop(self, ws):
        while True:
            await asyncio.sleep(WS_PING_INTERVAL)
            try:
                await ws.send("PING")
            except Exception:
                break

    async def _handle_raw(self, raw: str):
        if raw == "PONG":
            return
        try:
            messages = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(messages, dict):
            messages = [messages]
        for msg in messages:
            await self._on_ws_event(msg)

    async def _on_ws_event(self, event: dict):
        """
        WS `book` events carry the full orderbook snapshot — parse directly.
        Other change events (`price_change`, `best_bid_ask`, `tick`) carry no
        full depth; fall back to REST /book for those.
        """
        etype = event.get("event_type") or event.get("type") or ""
        asset_id = event.get("asset_id") or event.get("token_id") or ""

        if asset_id not in (self._up_id, self._down_id):
            return

        if etype == "book":
            asks = event.get("asks") or []
            bids = event.get("bids") or []
            prices = {
                "best_ask":  _best_ask(asks),
                "worst_ask": _worst_ask(asks),
                "best_bid":  _best_bid(bids),
                "worst_bid": _worst_bid(bids),
            }
            if any(v is not None for v in prices.values()):
                await self._update(asset_id, prices, "ws_book")
        elif etype in ("price_change", "best_bid_ask", "tick"):
            asyncio.create_task(self._fetch_book(asset_id))

    # ------------------------------------------------------------------
    # REST /book — primary source for all 4 price points
    # ------------------------------------------------------------------

    async def _fetch_book(self, token_id: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{CLOB_BASE}/book",
                    params={"token_id": token_id},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status != 200:
                        return
                    data = await r.json()

            asks = data.get("asks", [])
            bids = data.get("bids", [])

            prices = {
                "best_ask":  _best_ask(asks),
                "worst_ask": _worst_ask(asks),
                "best_bid":  _best_bid(bids),
                "worst_bid": _worst_bid(bids),
            }

            # Only update if we got at least one real value
            if any(v is not None for v in prices.values()):
                await self._update(token_id, prices, source="rest_book")

        except Exception as e:
            log.warning(f"[Book] fetch error for {token_id[:12]}: {e}")

    async def _update(self, token_id: str, prices: dict, source: str):
        if token_id not in self._book:
            return
        for k, v in prices.items():
            if v is not None:
                self._book[token_id][k] = v

        # Fire as soon as both tokens have at least a best_ask.
        # Bid / worst values may be None (thin market) — store them as NULL in DB.
        up = self._book[self._up_id]
        dn = self._book[self._down_id]
        if up["best_ask"] is not None and dn["best_ask"] is not None:
            await self._callback(dict(up), dict(dn), source)

    # ------------------------------------------------------------------
    # REST fallback (WS down)
    # ------------------------------------------------------------------

    async def _rest_fallback(self, duration: float = float("inf")):
        log.info("[REST] Starting fallback polling")
        deadline = asyncio.get_event_loop().time() + duration
        while self._running and not self._switch_tokens:
            if asyncio.get_event_loop().time() >= deadline:
                break
            await self._fetch_book(self._up_id)
            await self._fetch_book(self._down_id)
            await asyncio.sleep(REST_POLL_INTERVAL)


# ------------------------------------------------------------------
# Helpers — extract price points from orderbook arrays
# ------------------------------------------------------------------

def _prices(entries: list) -> list[float]:
    return [float(e["price"]) for e in entries if float(e.get("size", 0)) > 0]


def _best_ask(asks: list) -> Optional[float]:
    p = _prices(asks)
    return min(p) if p else None


def _worst_ask(asks: list) -> Optional[float]:
    p = _prices(asks)
    return max(p) if p else None


def _best_bid(bids: list) -> Optional[float]:
    p = _prices(bids)
    return max(p) if p else None


def _worst_bid(bids: list) -> Optional[float]:
    p = _prices(bids)
    return min(p) if p else None
