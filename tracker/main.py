"""
Polymarket BTC 5-Min Market Anomaly Tracker
--------------------------------------------
Tracks live YES/NO best ask prices and counts predictability anomalies
at thresholds 60, 70, 80, 90 cents for each 5-minute market window.

Run:
    cd tracker
    python main.py

Stop:
    Ctrl+C  (saves final counts before exiting)
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone, timedelta

from config import TRANSITION_LEAD_TIME, TRANSITION_CHECK_INTERVAL
from market_discovery import discover_current, discover_next, Market
from price_tracker import PriceTracker
from anomaly_calculator import AnomalyCalculator
from db import insert_market, insert_snapshot, insert_anomaly, insert_trader_trade, update_market_counts, update_market_final
from trader_monitor import TraderMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# Mutable globals (simpler than passing through every async layer)
_market: Market = None
_calc = AnomalyCalculator()
_tracker: PriceTracker = None
_trader_monitor: TraderMonitor = None
_shutdown = asyncio.Event()

# Throttle snapshots: only write to DB when price changes
_last_up_ask: float = None
_last_down_ask: float = None


async def on_price(up: dict, down: dict, source: str):
    """
    up/down dicts: best_ask, worst_ask, best_bid, worst_bid
    Anomaly logic runs on best_ask (most conservative / cheapest to buy).
    """
    global _last_up_ask, _last_down_ask

    up_ask   = up["best_ask"]
    down_ask = down["best_ask"]

    # Skip pure duplicates on best ask (bids/worst may still have changed — still record)
    if up_ask == _last_up_ask and down_ask == _last_down_ask:
        return
    _last_up_ask, _last_down_ask = up_ask, down_ask

    ts = datetime.now(timezone.utc).isoformat()

    asyncio.get_event_loop().run_in_executor(
        None, insert_snapshot, _market.slug, ts, up, down, source
    )

    events = _calc.check(up_ask, down_ask)
    for e in events:
        log.info(f"  ANOMALY [{e.threshold}¢]  {e.side:4s} @ {e.price:.3f}  (count={e.count})")
        asyncio.get_event_loop().run_in_executor(
            None, insert_anomaly, _market.slug, ts, e.threshold, e.side, e.price, e.count
        )
    if events:
        asyncio.get_event_loop().run_in_executor(
            None, update_market_counts, _market.slug, _calc.get_counts()
        )

    counts = _calc.get_counts()
    log.info(
        f"  [{source[:4].upper()}]"
        f"  UP  best_ask={up_ask:.3f}  worst_ask={up['worst_ask']:.3f}"
        f"  best_bid={up['best_bid']:.3f}  worst_bid={up['worst_bid']:.3f}"
        f"  | DOWN best_ask={down_ask:.3f}  worst_ask={down['worst_ask']:.3f}"
        f"  best_bid={down['best_bid']:.3f}  worst_bid={down['worst_bid']:.3f}"
        f"  | a60={counts[60]} a70={counts[70]} a80={counts[80]} a90={counts[90]}"
    )


async def transition_loop():
    global _market, _last_up_ask, _last_down_ask

    while not _shutdown.is_set():
        try:
            end = _parse_end_time(_market.end_time)
            now = datetime.now(timezone.utc)
            secs_left = (end - now).total_seconds()

            if secs_left <= TRANSITION_LEAD_TIME:
                log.info(f"Market ending in {secs_left:.0f}s — fetching next market...")
                next_market = await discover_next(_market.window_ts)

                if next_market is None:
                    log.warning("Next market not found yet, retrying in 5s...")
                    await asyncio.sleep(5)
                    continue

                # Wait until exact boundary
                wait = max(0.0, (end - datetime.now(timezone.utc)).total_seconds())
                if wait > 0:
                    log.info(f"Waiting {wait:.1f}s for market boundary...")
                    await asyncio.sleep(wait)

                # Finalise current market
                counts = _calc.get_counts()
                update_market_final(_market.slug, counts, active=False)
                log.info(
                    f"Market CLOSED: {_market.slug}"
                    f"  anomalies: 60={counts[60]} 70={counts[70]}"
                    f" 80={counts[80]} 90={counts[90]}"
                )

                # Switch to next market
                _calc.reset()
                _last_up_ask = _last_down_ask = None
                insert_market(next_market)
                await _tracker.switch_market(next_market.up_token_id, next_market.down_token_id)
                _trader_monitor.set_market(next_market.condition_id, next_market.slug)
                _market = next_market
                log.info(f"Market OPEN: {_market.slug}  (ends {_market.end_time})")

            else:
                await asyncio.sleep(TRANSITION_CHECK_INTERVAL)

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"[Transition] Unexpected error: {e}", exc_info=True)
            await asyncio.sleep(5)


def _parse_end_time(end_time: str) -> datetime:
    # Normalise to offset-aware
    s = end_time.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s += "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Fallback: strip microseconds and try again
        return datetime.fromisoformat(s[:19] + "+00:00")


async def on_trader_trade(trade: dict) -> None:
    log.info(
        f"  TRADER: {trade['side']:4s} {trade['outcome']:4s} "
        f"@ {trade['price']:.3f}  size={trade['size']:.2f} USDC"
    )
    asyncio.get_event_loop().run_in_executor(
        None, insert_trader_trade,
        trade["market_slug"], trade["ts"], trade["side"], trade["outcome"],
        trade["price"], trade["size"], trade["transaction_hash"], trade["role"],
    )


def _handle_shutdown(signum, frame):
    log.info("\nShutdown signal received — saving final state...")
    _shutdown.set()
    if _calc and _market:
        counts = _calc.get_counts()
        update_market_final(_market.slug, counts, active=True)
        log.info(f"Saved counts: {counts}")
    if _tracker:
        _tracker.stop()
    if _trader_monitor:
        _trader_monitor.stop()


async def main():
    global _market, _tracker, _trader_monitor

    log.info("=" * 60)
    log.info("Polymarket BTC 5-Min Anomaly Tracker")
    log.info("=" * 60)

    # Discover current market
    _market = await discover_current()
    if _market is None:
        log.error("Failed to discover current market. Exiting.")
        return

    log.info(f"Market OPEN: {_market.slug}")
    log.info(f"  UP token:   {_market.up_token_id[:20]}...")
    log.info(f"  DOWN token: {_market.down_token_id[:20]}...")
    log.info(f"  Ends:       {_market.end_time}")

    insert_market(_market)

    _tracker = PriceTracker(
        up_token_id=_market.up_token_id,
        down_token_id=_market.down_token_id,
        callback=on_price,
    )

    _trader_monitor = TraderMonitor(callback=on_trader_trade)
    _trader_monitor.set_market(_market.condition_id, _market.slug)
    log.info(f"Trader monitor active for: {_market.condition_id[:16]}...")

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info("Connecting to Polymarket WebSocket...")

    try:
        await asyncio.gather(
            _tracker.run(),
            transition_loop(),
            _trader_monitor.run(),
        )
    except asyncio.CancelledError:
        pass

    log.info("Tracker stopped.")


if __name__ == "__main__":
    asyncio.run(main())
