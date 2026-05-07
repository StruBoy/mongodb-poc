"""End-to-end smoke test for the iot-telemetry PoC.

Exercises the four analytics queries plus the failure-injection path. Assumes
data/stream_telemetry.py is running in another terminal — without a live stream
the recency-dependent assertions all fail honestly.

Run from the project root (iot-telemetry/):
    python -m scripts.smoke_test

Exits 0 on success, 2 on any per-step failure. Always cleans up its own
control_failures rows so the dashboard opens green.
"""
import sys
import time
from datetime import datetime, timedelta, timezone

from src.analytics import (
    degraded_towers,
    regional_health,
    throughput_timeline,
    write_throughput_estimate,
)
from src.db import get_db

INJECTION_TARGET_REGION = "West"
INJECTION_COUNT = 3
INJECTION_WAIT_SECONDS = 8         # cycles needed for degraded values to dominate the window
PACKET_LOSS_THRESHOLD = 5.0
ANALYTICS_WINDOW = 30


def step(n, title):
    print(f"\n--- Step {n}: {title} ---")


def main():
    db = get_db()
    failures: list[str] = []

    # --- 1. Stream is alive ---
    step(1, "Stream is producing events")
    rate = write_throughput_estimate(window_seconds=10)
    if rate < 100:
        print(
            f"   [FAIL] Only {rate:.0f} events/sec in the last 10s. "
            "Start the streamer in another terminal: python -m data.stream_telemetry"
        )
        sys.exit(2)
    print(f"   [OK] {rate:.0f} events/sec observed (last 10s)")

    # --- 2. Regional health returns one row per district ---
    step(2, "Regional health (3 districts)")
    rh = regional_health(window_seconds=ANALYTICS_WINDOW)
    regions = {r["region"] for r in rh}
    if regions != {"Central", "East", "West"}:
        print(f"   [FAIL] Expected districts Central/East/West, got {sorted(regions)}")
        failures.append("regional_health")
    else:
        print(f"   [OK] All three districts reporting")
        for r in rh:
            print(
                f"     {r['region']:8} signal={r['avg_signal_dbm']:.1f}dBm "
                f"loss={r['avg_packet_loss']:.2f}% "
                f"tput={r['avg_throughput_mbps']:.0f}Mbps "
                f"towers={r['tower_count']}"
            )

    # --- 3. Throughput timeline ---
    step(3, "Throughput timeline (60s window)")
    tl = throughput_timeline(window_seconds=60)
    if not tl:
        print("   [FAIL] Empty timeline — ts bucketing or window broken")
        failures.append("timeline_empty")
    else:
        seconds = {row["ts"] for row in tl}
        regions = {row["region"] for row in tl}
        print(f"   [OK] {len(tl)} (region, second) buckets across {len(seconds)} seconds and {len(regions)} regions")

    # --- 4. Baseline: nothing is degraded ---
    step(4, "Baseline degraded-tower count is low")
    pre = degraded_towers(window_seconds=ANALYTICS_WINDOW, packet_loss_threshold=PACKET_LOSS_THRESHOLD)
    print(f"   [info] {len(pre)} towers above threshold before injection")

    # --- 5. Inject 3 failures in West ---
    step(5, f"Inject {INJECTION_COUNT} failures in {INJECTION_TARGET_REGION}")
    targets = list(db.towers.find(
        {"region": INJECTION_TARGET_REGION},
        {"_id": 1},
    ).limit(INJECTION_COUNT))
    target_ids = [t["_id"] for t in targets]

    db.control_failures.delete_many({})  # clean slate
    db.control_failures.insert_many([
        {"_id": tid, "injected_at": datetime.now(timezone.utc), "kind": "smoke_test"}
        for tid in target_ids
    ])
    print(f"   [OK] Injected: {target_ids}")

    # --- 6. Wait, then confirm those towers cross threshold ---
    step(6, f"Wait {INJECTION_WAIT_SECONDS}s then re-check degraded list")
    time.sleep(INJECTION_WAIT_SECONDS)

    post = degraded_towers(
        window_seconds=INJECTION_WAIT_SECONDS,
        packet_loss_threshold=PACKET_LOSS_THRESHOLD,
        min_samples=3,
    )
    flagged_ids = {t["_id"] for t in post}
    expected = set(target_ids)
    hit = expected & flagged_ids

    if hit == expected:
        print(f"   [OK] All {len(expected)} injected towers crossed threshold:")
        for t in post:
            if t["_id"] in expected:
                print(f"     {t['_id']}  loss={t['avg_packet_loss']:.1f}% temp={t['avg_temp_c']:.1f}C samples={t['samples']}")
    else:
        print(f"   [FAIL] Expected {sorted(expected)} flagged, got {sorted(flagged_ids & expected)}")
        print(f"          Full degraded list ({len(post)} towers): {sorted(flagged_ids)}")
        failures.append("injection_propagation")

    # --- 7. Cleanup ---
    step(7, "Clear failures")
    db.control_failures.delete_many({})
    remaining = db.control_failures.count_documents({})
    if remaining:
        print(f"   [FAIL] {remaining} control rows remain")
        failures.append("cleanup")
    else:
        print(f"   [OK] control_failures is empty")

    print()
    if failures:
        print(f"[FAIL] {len(failures)} step(s) failed: {', '.join(failures)}")
        sys.exit(2)
    print("[OK] Smoke test passed. Stream → analytics → injection → cleanup all green.")


if __name__ == "__main__":
    main()
