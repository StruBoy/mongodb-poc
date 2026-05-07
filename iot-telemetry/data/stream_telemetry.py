"""Async telemetry generator — the throughput half of the demo.

Every cycle (~1 second by default) emits one telemetry event per tower in the
fleet, with healthy or degraded metrics depending on the failure-injection
state. State lives in telco_demo.control_failures (one document per failing
tower) — set by the Streamlit sidebar, read here at the start of every cycle,
so the demo operator and the streamer never share a temp file.

motor (async pymongo) + chunked insert_many keeps the writes parallel without
blowing through the M10 connection pool.

Run from the project root (iot-telemetry/):
    python -m data.stream_telemetry          # default 1000 events/sec
    python -m data.stream_telemetry --rate 2000
"""
import argparse
import asyncio
import random
import sys
import time
from datetime import datetime, timezone

from src.db import get_async_db, get_async_client


CHUNK_SIZE = 500
DEFAULT_TARGET_RATE = 1000


def baseline_metrics(tower):
    """Healthy telemetry for a tower — narrow Gaussians around nominal values."""
    capacity = tower["max_capacity_subscribers"]
    return {
        "signal_strength_dbm": round(random.gauss(-65, 4), 2),
        "packet_loss_pct": round(max(0.0, random.gauss(0.5, 0.3)), 3),
        "throughput_mbps": round(random.gauss(450, 50), 1),
        "temperature_c": round(random.gauss(38, 3), 1),
        "active_subscribers": random.randint(int(capacity * 0.30), int(capacity * 0.80)),
    }


def degraded_metrics(tower):
    """Telemetry pattern for a failing tower — spread chosen so the dashboard
    crosses every alert threshold visibly within ~5 seconds of injection."""
    capacity = tower["max_capacity_subscribers"]
    return {
        "signal_strength_dbm": round(random.gauss(-95, 5), 2),
        "packet_loss_pct": round(max(0.0, random.gauss(15.0, 3.0)), 3),
        "throughput_mbps": round(max(0.0, random.gauss(80, 20)), 1),
        "temperature_c": round(random.gauss(72, 4), 1),
        "active_subscribers": random.randint(int(capacity * 0.05), int(capacity * 0.20)),
    }


async def fetch_failure_set(db) -> set:
    """Return the set of tower IDs currently flagged as failing."""
    cursor = db.control_failures.find({}, {"_id": 1})
    return {doc["_id"] async for doc in cursor}


async def stream_loop(target_rate: int):
    db = get_async_db()
    towers = await db.towers.find().to_list(length=None)
    if not towers:
        print("ERROR: telco_demo.towers is empty. Run: python -m data.generate_fleet", flush=True)
        sys.exit(1)

    print(
        f"Streaming telemetry for {len(towers)} towers at ~{target_rate} events/sec. "
        "Ctrl+C to stop.",
        flush=True,
    )

    cycle = 0
    cycle_target_seconds = max(0.05, len(towers) / target_rate)

    while True:
        cycle += 1
        cycle_start = time.perf_counter()

        failure_set = await fetch_failure_set(db)
        now = datetime.now(timezone.utc)

        events = []
        for tower in towers:
            metrics = degraded_metrics(tower) if tower["_id"] in failure_set else baseline_metrics(tower)
            events.append({
                "ts": now,
                "meta": {
                    "tower_id": tower["_id"],
                    "region": tower["region"],
                    "type": tower["type"],
                },
                **metrics,
            })

        chunks = [events[i:i + CHUNK_SIZE] for i in range(0, len(events), CHUNK_SIZE)]
        await asyncio.gather(*[db.telemetry.insert_many(chunk) for chunk in chunks])

        elapsed = time.perf_counter() - cycle_start
        if cycle % 5 == 0:
            failing = len(failure_set)
            print(
                f"  cycle {cycle:>5}  inserted={len(events)}  "
                f"failing={failing}  elapsed={elapsed*1000:.0f}ms  ts={now.isoformat()}",
                flush=True,
            )

        sleep_for = cycle_target_seconds - elapsed
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)


def main():
    parser = argparse.ArgumentParser(description="Stream synthetic telemetry into telco_demo.telemetry.")
    parser.add_argument("--rate", type=int, default=DEFAULT_TARGET_RATE,
                        help="Target events per second across the fleet (default 1000).")
    args = parser.parse_args()

    try:
        asyncio.run(stream_loop(target_rate=args.rate))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        client = get_async_client()
        client.close()


if __name__ == "__main__":
    main()
