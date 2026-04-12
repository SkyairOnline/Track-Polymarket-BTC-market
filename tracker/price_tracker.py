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
    Tracks best ask prices for UP and DOWN tokens via WebSocket.
    Falls back to REST polling on disconnect, reconnects in background.
    """

    def __init__(self, up_token_id: str, down_token_id: str, callback: Callable):
        self._up_id = up_token_id
        self._down_id = down_token_id
        self._callback = callback  # async fn(up_ask, down_ask, source)
        self._running = False
        self._up_ask: Optional[float] = None
        self._down_ask: Optional[float] = None
        # Set when switch_market() is called
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
        """Signal the tracker to subscribe to new token IDs after the current market closes."""
        self._switch_tokens = (up_id, down_id)
        # Close the WS connection so _ws_session restarts with new tokens
        if self._ws and not self._ws.closed:
            await self._ws.close()

    # ------------------------------------------------------------------
    # WebSocket session
    # ------------------------------------------------------------------

    async def _ws_session(self):
        # Apply any pending market switch before connecting
        if self._switch_tokens:
            self._up_id, self._down_id = self._switch_tokens
            self._switch_tokens = None
            self._up_ask = None
            self._down_ask = None
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
                    # Check for pending market switch mid-session
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
            asks = event.get("asks", [])
            best_ask = _min_ask(asks)
            if best_ask is not None:
                await self._update(asset_id, best_ask, "websocket")

        elif etype in ("price_change", "best_bid_ask", "tick"):
            # price_change events from Polymarket include best_ask directly
            best_ask = event.get("best_ask")
            if best_ask is not None:
                await self._update(asset_id, float(best_ask), "websocket")
            else:
                # Some events nest price data
                asks = event.get("asks", [])
                best_ask = _min_ask(asks)
                if best_ask is not None:
                    await self._update(asset_id, best_ask, "websocket")

    async def _update(self, asset_id: str, price: float, source: str):
        if asset_id == self._up_id:
            self._up_ask = price
        elif asset_id == self._down_id:
            self._down_ask = price
        else:
            return  # unrelated token

        if self._up_ask is not None and self._down_ask is not None:
            await self._callback(self._up_ask, self._down_ask, source)

    # ------------------------------------------------------------------
    # REST fallback
    # ------------------------------------------------------------------

    async def _rest_fallback(self, duration: float = float("inf")):
        """Poll REST until `duration` seconds have elapsed or WS reconnects."""
        log.info("[REST] Starting fallback polling")
        deadline = asyncio.get_event_loop().time() + duration
        async with aiohttp.ClientSession() as session:
            while self._running and not self._switch_tokens:
                if asyncio.get_event_loop().time() >= deadline:
                    break
                try:
                    up = await _rest_price(session, self._up_id)
                    down = await _rest_price(session, self._down_id)
                    if up is not None and down is not None:
                        self._up_ask = up
                        self._down_ask = down
                        await self._callback(up, down, "rest")
                except Exception as e:
                    log.warning(f"[REST] poll error: {e}")
                await asyncio.sleep(REST_POLL_INTERVAL)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _min_ask(asks: list) -> Optional[float]:
    """Return the lowest ask price from an orderbook asks array."""
    try:
        prices = [float(a["price"]) for a in asks if float(a.get("size", 0)) > 0]
        return min(prices) if prices else None
    except Exception:
        return None


async def _rest_price(session: aiohttp.ClientSession, token_id: str) -> Optional[float]:
    url = f"{CLOB_BASE}/price"
    async with session.get(url, params={"token_id": token_id, "side": "sell"},
                           timeout=aiohttp.ClientTimeout(total=5)) as r:
        if r.status != 200:
            return None
        data = await r.json()
        price = data.get("price")
        return float(price) if price is not None else None
