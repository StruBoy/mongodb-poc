"""One-shot probe: succeed if a vector search round-trip works, fail otherwise.

Used by until-loops to wait for Atlas Search (mongot) to come back online.
"""
import sys
from datetime import datetime

try:
    from src.core import score_transaction
    score_transaction({
        "amount": 50.0, "currency": "AUD", "merchant_name": "Ping",
        "merchant_category": "groceries", "country": "AU",
        "channel": "in_person", "card_present": True,
        "hour_of_day": 12, "distance_from_home_km": 5.0,
        "ts": datetime.now(),
    })
    print("up")
except Exception as e:
    print(f"down: {e}", file=sys.stderr)
    sys.exit(1)
