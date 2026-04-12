import asyncio
import json
import logging
from typing import Callable, Optional, Tuple
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed
from config import WS_URL, CLOB_BASE, WS_PING_INTERVAL, REST_POLL_INTERVAL

log = logging.getLogger(__name__)


class PriceTracker:
    """
    Tracks best ask AND best bid for UP and DOWN tokens via WebSocket.
    Falls back to REST polling on disconnect, reconnects in background.
    Callback: async fn(up_ask, up_bid, down_ask, down_bid, source)
    """

    def __init__(self, up_token_id: str, down_token_id: str, callback: Callable):
        self._up_id = up_token_id
        self._down_id = down_token_id
        self._callback = callback
        self._running = False
        self._up_ask:  Optional[float] = None
        self._up_bid:  Optional[float] = None
        self._down_ask: Optional[float] = None
        self._down_bid: Optional[float] = None
        self._switch_tokens: Optional[Tuple[str, str]] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self):
        self._running = True
        while self._running:
            try:
                await self._ws_session()
            except Exception as e:
                log.warning(f"[WS] session ended ({e}), falling back to REST for 10s")
                await self._rest_fallback(duration=10)

    def stop(self):
        self._running = False

    async def switch_market(self, up_id: str, down_id: str):
        self._switch_tokens = (up_id, down_id)
        if self._ws and not self._ws.closed:
            await self._ws.close()

    # ------------------------------------------------------------------
    # WebSocket session
    # ------------------------------------------------------------------

    async def _ws_session(self):
        if self._switch_tokens:
            self._up_id, self._down_id = self._switch_tokens
            self._switch_tokens = None
            self._up_ask = self._up_bid = self._down_ask = self._down_bid = None
            log.info(f"[WS] Switched tokens -> up={self._up_id[:12]}... down={self._down_id[:12]}...")

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
            await self._process_event(msg)

    async def _process_event(self, event: dict):
        etype = event.get("event_type") or event.get("type") or ""
        asset_id = event.get("asset_id") or event.get("token_id") or ""

        if etype == "book":
            ask = _best_ask(event.get("asks", []))
            # WS book events only have asks, no bids — fetch bids via REST async
            await self._update(asset_id, ask=ask, bid=None, source="websocket")
            # Queue a REST /book call to get the full orderbook with bids
            asyncio.create_task(self._fetch_bids_for_token(asset_id))

        elif etype in ("price_change", "best_bid_ask", "tick"):
            ask = event.get("best_ask")
            bid = event.get("best_bid")
            # Fallback: parse from nested orderbook arrays if fields absent
            if ask is None:
                ask = _best_ask(event.get("asks", []))
            if bid is None:
                bid = _best_bid(event.get("bids", []))
            await self._update(
                asset_id,
                ask=float(ask) if ask is not None else None,
                bid=float(bid) if bid is not None else None,
                source="websocket",
            )

    async def _fetch_bids_for_token(self, token_id: str):
        """Fetch full orderbook from REST /book to get bid side."""
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
                    bid = _best_bid(data.get("bids", []))
                    if bid is not None:
                        await self._update(token_id, ask=None, bid=bid, source="rest_book")
        except Exception:
            pass  # Silent fail — bids are nice-to-have

    async def _update(self, asset_id: str, ask: Optional[float], bid: Optional[float], source: str):
        if asset_id == self._up_id:
            if ask is not None: self._up_ask = ask
            if bid is not None: self._up_bid = bid
        elif asset_id == self._down_id:
            if ask is not None: self._down_ask = ask
            if bid is not None: self._down_bid = bid
        else:
            return  # unrelated token

        # Fire callback only once all four values are known
        if None not in (self._up_ask, self._up_bid, self._down_ask, self._down_bid):
            await self._callback(
                self._up_ask, self._up_bid,
                self._down_ask, self._down_bid,
                source,
            )

    # ------------------------------------------------------------------
    # REST fallback
    # ------------------------------------------------------------------

    async def _rest_fallback(self, duration: float = float("inf")):
        log.info("[REST] Starting fallback polling")
        deadline = asyncio.get_event_loop().time() + duration
        async with aiohttp.ClientSession() as session:
            while self._running and not self._switch_tokens:
                if asyncio.get_event_loop().time() >= deadline:
                    break
                try:
                    up_ask  = await _rest_price(session, self._up_id,   "sell")
                    up_bid  = await _rest_price(session, self._up_id,   "buy")
                    dn_ask  = await _rest_price(session, self._down_id, "sell")
                    dn_bid  = await _rest_price(session, self._down_id, "buy")
                    if None not in (up_ask, up_bid, dn_ask, dn_bid):
                        self._up_ask, self._up_bid = up_ask, up_bid
                        self._down_ask, self._down_bid = dn_ask, dn_bid
                        await self._callback(up_ask, up_bid, dn_ask, dn_bid, "rest")
                except Exception as e:
                    log.warning(f"[REST] poll error: {e}")
                await asyncio.sleep(REST_POLL_INTERVAL)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _best_ask(asks: list) -> Optional[float]:
    """Lowest ask price with positive size."""
    try:
        prices = [float(a["price"]) for a in asks if float(a.get("size", 0)) > 0]
        return min(prices) if prices else None
    except Exception:
        return None


def _best_bid(bids: list) -> Optional[float]:
    """Highest bid price with positive size."""
    try:
        prices = [float(b["price"]) for b in bids if float(b.get("size", 0)) > 0]
        return max(prices) if prices else None
    except Exception:
        return None


async def _rest_price(session: aiohttp.ClientSession, token_id: str, side: str) -> Optional[float]:
    """side: 'sell' for best ask, 'buy' for best bid."""
    async with session.get(
        f"{CLOB_BASE}/price",
        params={"token_id": token_id, "side": side},
        timeout=aiohttp.ClientTimeout(total=5),
    ) as r:
        if r.status != 200:
            return None
        data = await r.json()
        price = data.get("price")
        return float(price) if price is not None else None
