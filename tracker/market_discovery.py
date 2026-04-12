import time
import logging
import aiohttp
from dataclasses import dataclass
from typing import Optional
from config import GAMMA_BASE, WINDOW_SECONDS

log = logging.getLogger(__name__)


@dataclass
class Market:
    slug: str
    condition_id: str
    up_token_id: str
    down_token_id: str
    window_ts: int
    end_time: str  # ISO 8601


def current_window_ts() -> int:
    t = int(time.time())
    return t - (t % WINDOW_SECONDS)


def build_slug(window_ts: int) -> str:
    return f"btc-updown-5m-{window_ts}"


async def fetch_market(slug: str) -> Optional[Market]:
    url = f"{GAMMA_BASE}/markets"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"slug": slug}, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    log.warning(f"[Discovery] GET {url}?slug={slug} returned {r.status}")
                    return None
                data = await r.json()
    except Exception as e:
        log.error(f"[Discovery] fetch_market error for {slug}: {e}")
        return None

    if not data:
        return None

    m = data[0] if isinstance(data, list) else data

    # clobTokenIds can be a JSON string or an actual list
    token_ids = m.get("clobTokenIds") or m.get("clob_token_ids") or []
    if isinstance(token_ids, str):
        import json
        try:
            token_ids = json.loads(token_ids)
        except Exception:
            token_ids = []

    if len(token_ids) < 2:
        log.warning(f"[Discovery] {slug} has fewer than 2 clobTokenIds: {token_ids}")
        return None

    end_time = m.get("endDate") or m.get("end_date") or ""
    if end_time and not end_time.endswith("Z") and "+" not in end_time:
        end_time += "Z"

    return Market(
        slug=m.get("slug", slug),
        condition_id=m.get("conditionId") or m.get("condition_id", ""),
        up_token_id=token_ids[0],
        down_token_id=token_ids[1],
        window_ts=int(slug.split("-")[-1]),
        end_time=end_time,
    )


async def discover_current() -> Optional[Market]:
    wts = current_window_ts()
    for offset in (0, -WINDOW_SECONDS):
        market = await fetch_market(build_slug(wts + offset))
        if market:
            return market
    log.error("[Discovery] Could not find current market")
    return None


async def discover_next(current_wts: int) -> Optional[Market]:
    return await fetch_market(build_slug(current_wts + WINDOW_SECONDS))
