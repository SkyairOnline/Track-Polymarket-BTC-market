"""
Debug WebSocket messages to see what fields are being sent.
Run: cd tracker && python3 debug_ws.py
"""
import asyncio
import json
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

async def main():
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        # Subscribe to a test market (use any tokens)
        sub_msg = {
            "type": "market",
            "assets_ids": [
                "31558123108700966577886608152343307463430783094266487106514149654532745611572",  # ETH Down
                "37821824917908613625836367318395386033792488832282809847663104034253138956671",  # BTC Up
            ],
        }
        await ws.send(json.dumps(sub_msg))
        print(f"[WS] Sent subscription: {json.dumps(sub_msg, indent=2)}")

        print("\n[WS] Listening for 30 seconds...\n")
        count = 0
        async for msg in ws:
            if msg == "PONG":
                continue

            count += 1
            try:
                data = json.loads(msg)
            except:
                print(f"[{count}] Could not parse: {msg[:100]}")
                continue

            # WS returns array of events
            if isinstance(data, list):
                for evt in data:
                    etype = evt.get("event_type") or evt.get("type") or "unknown"
                    print(f"\n[{count}] Event type: {etype}")
                    print(json.dumps(evt, indent=2)[:800])
            else:
                etype = data.get("event_type") or data.get("type") or "unknown"
                print(f"\n[{count}] Event type: {etype}")
                print(json.dumps(data, indent=2)[:800])

            if count >= 20:
                break

asyncio.run(main())
