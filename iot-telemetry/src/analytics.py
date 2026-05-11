"""Real-time aggregations over telco_demo.telemetry.

These four queries power the dashboard. Each one is a plain MongoDB aggregation
pipeline against the time-series collection — no external streaming tier, no
warehouse, no materialised views. All three rely on the indexes from
scripts/create_db_index.py:
    {meta.tower_id: 1, ts: -1}
    {meta.region: 1, ts: -1}
"""
from datetime import datetime, timedelta, timezone

from src.db import get_db


def regional_health(window_seconds: int = 30) -> list[dict]:
    """Average metrics per district over the last N seconds.

    Returns one row per district with avg signal / loss / throughput / temp
    and a count of distinct towers reporting in the window.
    """
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$meta.region",
            "avg_signal_dbm": {"$avg": "$signal_strength_dbm"},
            "avg_packet_loss": {"$avg": "$packet_loss_pct"},
            "avg_throughput_mbps": {"$avg": "$throughput_mbps"},
            "avg_temp_c": {"$avg": "$temperature_c"},
            "tower_ids": {"$addToSet": "$meta.tower_id"},
        }},
        {"$project": {
            "region": "$_id",
            "_id": 0,
            "avg_signal_dbm": 1,
            "avg_packet_loss": 1,
            "avg_throughput_mbps": 1,
            "avg_temp_c": 1,
            "tower_count": {"$size": "$tower_ids"},
        }},
        {"$sort": {"region": 1}},
    ]
    return list(db.telemetry.aggregate(pipeline))


def degraded_towers(window_seconds: int = 30,
                    packet_loss_threshold: float = 5.0,
                    min_samples: int = 3,
                    limit: int = 20) -> list[dict]:
    """Towers whose recent average packet loss exceeds the threshold."""
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$meta.tower_id",
            "region": {"$first": "$meta.region"},
            "type": {"$first": "$meta.type"},
            "avg_signal_dbm": {"$avg": "$signal_strength_dbm"},
            "avg_packet_loss": {"$avg": "$packet_loss_pct"},
            "avg_throughput_mbps": {"$avg": "$throughput_mbps"},
            "avg_temp_c": {"$avg": "$temperature_c"},
            "samples": {"$sum": 1},
        }},
        {"$match": {
            "avg_packet_loss": {"$gte": packet_loss_threshold},
            "samples": {"$gte": min_samples},
        }},
        {"$sort": {"avg_packet_loss": -1}},
        {"$limit": limit},
    ]
    return list(db.telemetry.aggregate(pipeline))


def throughput_timeline(window_seconds: int = 120) -> list[dict]:
    """Per-second throughput sum, broken down by district.

    Plays as a stacked-or-grouped line chart in the dashboard. The $dateTrunc
    bucketing relies on MongoDB's native time-series support — no app-side
    bucketing required.

    The upper bound on `ts` excludes the bucket the streamer is currently
    writing into. Each cycle lands as two concurrent insert_many(500) chunks
    via asyncio.gather; a query that reads between the two completions sees
    a partial bucket, which on the chart shows as a vertical drop to the
    x-axis. Streamer cycle is ~1s, so a 2s guard always gives the in-flight
    cycle time to fully flush.

    Aggregation is two-stage: first sum per cycle (events from one streamer
    cycle share an identical ts), then average those per-cycle totals into
    second-aligned buckets. A naive $sum over the whole second double-counts
    on the seconds where cycle timing drift lands two cycles in the same
    bucket, which shows up on the chart as a 2x spike.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)
    upper = now - timedelta(seconds=2)
    pipeline = [
        {"$match": {"ts": {"$gte": cutoff, "$lt": upper}}},
        {"$group": {
            "_id": {"ts": "$ts", "region": "$meta.region"},
            "cycle_throughput": {"$sum": "$throughput_mbps"},
            "cycle_tower_count": {"$sum": 1},
        }},
        {"$group": {
            "_id": {
                "second": {"$dateTrunc": {"date": "$_id.ts", "unit": "second"}},
                "region": "$_id.region",
            },
            "total_throughput_mbps": {"$avg": "$cycle_throughput"},
            "tower_count": {"$avg": "$cycle_tower_count"},
        }},
        {"$project": {
            "ts": "$_id.second",
            "region": "$_id.region",
            "total_throughput_mbps": 1,
            "tower_count": 1,
            "_id": 0,
        }},
        {"$sort": {"ts": 1}},
    ]
    return list(db.telemetry.aggregate(pipeline))


def write_throughput_estimate(window_seconds: int = 10) -> float:
    """Observed write rate over the last window, in events/sec.

    Uses count_documents rather than a stage-level $count so we can hit the
    {meta.region: 1, ts: -1} index directly without scanning. Returns 0 if no
    events fell in the window (i.e. the streamer isn't running).
    """
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    count = db.telemetry.count_documents({"ts": {"$gte": cutoff}})
    return count / window_seconds
