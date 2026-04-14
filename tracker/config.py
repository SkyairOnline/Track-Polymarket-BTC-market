import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

WINDOW_SECONDS = 300          # 5 minutes
WS_PING_INTERVAL = 10         # seconds between WebSocket PINGs
REST_POLL_INTERVAL = 2        # seconds between REST polls (fallback)
TRANSITION_LEAD_TIME = 35     # seconds before end to pre-fetch next market
TRANSITION_CHECK_INTERVAL = 5 # seconds between transition loop checks
