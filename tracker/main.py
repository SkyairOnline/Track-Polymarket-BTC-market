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
from db import insert_market, insert_snapshot, insert_anomaly, update_market_counts, update_market_final

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
_shutdown = asyncio.Event()

# Throttle snapshots: only write to DB when price changes
_last_up: float = None
_last_down: float = None


async def on_price(up_ask: float, down_ask: float, source: str):
    global _last_up, _last_down

    # Skip duplicate readings to avoid DB spam
    if up_ask == _last_up and down_ask == _last_down:
        return
    _last_up, _last_down = up_ask, down_ask

    ts = datetime.now(timezone.utc).isoformat()

    # Write snapshot (non-blocking via thread)
    asyncio.get_event_loop().run_in_executor(
        None, insert_snapshot, _market.slug, ts, up_ask, down_ask, source
    )

    # Check anomalies (in-memory, instant)
    events = _calc.check(up_ask, down_ask)
    for e in events:
        log.info(
            f"  ANOMALY [{e.threshold}¢]  {e.side:4s} @ {e.price:.3f}"
            f"  (count={e.count})"
        )
        asyncio.get_event_loop().run_in_executor(
            None, insert_anomaly, _market.slug, ts, e.threshold, e.side, e.price, e.count
        )
    if events:
        counts = _calc.get_counts()
        asyncio.get_event_loop().run_in_executor(
            None, update_market_counts, _market.slug, counts
        )

    # Live price line
    counts = _calc.get_counts()
    sides = _calc.get_last_sides()
    log.info(
        f"  [{source[:2].upper()}]  UP={up_ask:.3f}  DOWN={down_ask:.3f}"
        f"  | a60={counts[60]} a70={counts[70]} a80={counts[80]} a90={counts[90]}"
        f"  | sides={sides}"
    )


async def transition_loop():
    global _market, _last_up, _last_down

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
                _last_up = _last_down = None
                insert_market(next_market)
                await _tracker.switch_market(next_market.up_token_id, next_market.down_token_id)
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


def _handle_shutdown(signum, frame):
    log.info("\nShutdown signal received — saving final state...")
    _shutdown.set()
    if _calc and _market:
        counts = _calc.get_counts()
        update_market_final(_market.slug, counts, active=True)  # keep active=True, still recording
        log.info(f"Saved counts: {counts}")
    if _tracker:
        _tracker.stop()


async def main():
    global _market, _tracker

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

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info("Connecting to Polymarket WebSocket...")

    try:
        await asyncio.gather(
            _tracker.run(),
            transition_loop(),
        )
    except asyncio.CancelledError:
        pass

    log.info("Tracker stopped.")


if __name__ == "__main__":
    asyncio.run(main())
