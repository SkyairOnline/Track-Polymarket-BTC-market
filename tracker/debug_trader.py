"""
Run this once to diagnose why trader_trades is empty.
Usage: cd tracker && python debug_trader.py
"""
import asyncio, json
import aiohttp

TRADER = "0x7347d3291b321d2ee8d43d24aff244e7fb8c3d35"
CLOB   = "https://clob.polymarket.com"
DATA   = "https://data-api.polymarket.com"

async def main():
    async with aiohttp.ClientSession() as s:

        # 1. Check data API – all recent activity for this wallet
        print("\n── DATA API: /activity ───────────────────────────────")
        async with s.get(f"{DATA}/activity", params={"user": TRADER, "limit": 10}) as r:
            print(f"Status: {r.status}")
            body = await r.text()
            print(body[:2000])

        # 2. Check CLOB API – trades as maker
        print("\n── CLOB API: /trades?maker_address ──────────────────")
        async with s.get(f"{CLOB}/trades", params={"maker_address": TRADER, "limit": 5}) as r:
            print(f"Status: {r.status}")
            body = await r.text()
            print(body[:2000])

        # 3. Check CLOB API – trades as taker
        print("\n── CLOB API: /trades?taker_address ──────────────────")
        async with s.get(f"{CLOB}/trades", params={"taker_address": TRADER, "limit": 5}) as r:
            print(f"Status: {r.status}")
            body = await r.text()
            print(body[:2000])

asyncio.run(main())
