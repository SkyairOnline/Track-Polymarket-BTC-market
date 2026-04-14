import logging
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

log = logging.getLogger(__name__)

_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def insert_market(market) -> None:
    try:
        _client.table("markets").upsert({
            "slug": market.slug,
            "condition_id": market.condition_id,
            "up_token_id": market.up_token_id,
            "down_token_id": market.down_token_id,
            "window_ts": market.window_ts,
            "end_time": market.end_time,
            "active": True,
        }, on_conflict="slug").execute()
    except Exception as e:
        log.error(f"[DB] insert_market failed: {e}")


def insert_snapshot(
    market_slug: str, ts: str,
    up: dict, down: dict,
    source: str,
) -> None:
    """up/down dicts: best_ask, worst_ask, best_bid, worst_bid"""
    try:
        _client.table("price_snapshots").insert({
            "market_slug":    market_slug,
            "ts":             ts,
            "up_best_ask":    up.get("best_ask"),
            "up_worst_ask":   up.get("worst_ask"),
            "up_best_bid":    up.get("best_bid"),
            "up_worst_bid":   up.get("worst_bid"),
            "down_best_ask":  down.get("best_ask"),
            "down_worst_ask": down.get("worst_ask"),
            "down_best_bid":  down.get("best_bid"),
            "down_worst_bid": down.get("worst_bid"),
            "source":         source,
        }).execute()
    except Exception as e:
        log.error(f"[DB] insert_snapshot failed: {e}")



def insert_trader_trade(
    market_slug: str, ts: str, side: str, outcome: str,
    price: float, size: float, tx_hash: str, role: str
) -> None:
    try:
        _client.table("trader_trades").upsert({
            "market_slug": market_slug,
            "ts": ts,
            "side": side,
            "outcome": outcome,
            "price": price,
            "size": size,
            "transaction_hash": tx_hash,
            "role": role,
        }, on_conflict="transaction_hash").execute()
    except Exception as e:
        log.error(f"[DB] insert_trader_trade failed: {e}")


def insert_btc_divergence_alert(
    market_slug: str, ts: str, btc_price: float, price_to_beat: float,
    direction: str, up_best_ask: float, down_best_ask: float
) -> None:
    try:
        _client.table("btc_divergence_alerts").insert({
            "market_slug":   market_slug,
            "ts":            ts,
            "btc_price":     btc_price,
            "price_to_beat": price_to_beat,
            "direction":     direction,
            "up_best_ask":   up_best_ask,
            "down_best_ask": down_best_ask,
        }).execute()
    except Exception as e:
        log.error(f"[DB] insert_btc_divergence_alert failed: {e}")


def update_market_final(slug: str, active: bool = False) -> None:
    try:
        _client.table("markets").update({"active": active}).eq("slug", slug).execute()
    except Exception as e:
        log.error(f"[DB] update_market_final failed: {e}")
