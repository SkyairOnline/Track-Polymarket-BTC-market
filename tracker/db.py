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


def insert_snapshot(market_slug: str, ts: str, up_ask: float, down_ask: float, source: str) -> None:
    try:
        _client.table("price_snapshots").insert({
            "market_slug": market_slug,
            "ts": ts,
            "up_best_ask": up_ask,
            "down_best_ask": down_ask,
            "source": source,
        }).execute()
    except Exception as e:
        log.error(f"[DB] insert_snapshot failed: {e}")


def insert_anomaly(market_slug: str, ts: str, threshold: int, side: str, price: float, count: int) -> None:
    try:
        _client.table("anomaly_events").insert({
            "market_slug": market_slug,
            "ts": ts,
            "threshold": threshold,
            "side": side,
            "price": price,
            "count": count,
        }).execute()
    except Exception as e:
        log.error(f"[DB] insert_anomaly failed: {e}")


def update_market_counts(slug: str, counts: dict) -> None:
    """Update running anomaly counts on the active market (called on every anomaly event)."""
    try:
        _client.table("markets").update({
            "anomaly_60": counts[60],
            "anomaly_70": counts[70],
            "anomaly_80": counts[80],
            "anomaly_90": counts[90],
        }).eq("slug", slug).execute()
    except Exception as e:
        log.error(f"[DB] update_market_counts failed: {e}")


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


def update_market_final(slug: str, counts: dict, active: bool = False) -> None:
    try:
        _client.table("markets").update({
            "anomaly_60": counts[60],
            "anomaly_70": counts[70],
            "anomaly_80": counts[80],
            "anomaly_90": counts[90],
            "active": active,
        }).eq("slug", slug).execute()
    except Exception as e:
        log.error(f"[DB] update_market_final failed: {e}")
