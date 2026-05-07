"""Seed the telco_demo.towers collection with 1,000 cell towers across Singapore.

Three districts (the dashboard renders one panel per district):
    Central — CBD, Marina Bay, Orchard
    East    — Tampines, Changi, Bedok
    West    — Jurong, Clementi, Bukit Timah

Tower IDs are district-prefixed (e.g. TWR-CEN-0001) so the demo audience can
read them without a lookup. Coordinates cluster around realistic district
centers with a spread that keeps towers within Singapore's land mass.

Run from the project root (iot-telemetry/):
    python -m data.generate_fleet
"""
import random

from src.db import get_db

random.seed(42)

# Centers and per-district config. Singapore is ~50km E–W, ~25km N–S, so a
# spread of 0.03–0.04° (~3–4km) keeps towers within recognisable district
# bounds and avoids visible overlap on a map.
DISTRICTS = {
    "Central": {
        "code": "CEN",
        "center": (1.285, 103.852),   # Marina Bay
        "spread_lat": 0.025,
        "spread_lon": 0.030,
        "count": 350,
        "small_cell_ratio": 0.45,     # CBD has more small cells
    },
    "East": {
        "code": "EST",
        "center": (1.353, 103.945),   # Tampines
        "spread_lat": 0.030,
        "spread_lon": 0.040,
        "count": 300,
        "small_cell_ratio": 0.20,
    },
    "West": {
        "code": "WST",
        "center": (1.340, 103.730),   # Jurong East
        "spread_lat": 0.030,
        "spread_lon": 0.045,
        "count": 350,
        "small_cell_ratio": 0.25,
    },
}

FREQUENCY_BANDS = ["700MHz", "1800MHz", "2600MHz", "3500MHz"]
CAPACITY_OPTIONS_MACRO = [1000, 2000, 5000]
CAPACITY_OPTIONS_SMALL = [500, 1000]


def generate_fleet():
    db = get_db()
    db.towers.delete_many({})

    fleet = []
    for region_name, cfg in DISTRICTS.items():
        for i in range(1, cfg["count"] + 1):
            lat = cfg["center"][0] + random.uniform(-cfg["spread_lat"], cfg["spread_lat"])
            lon = cfg["center"][1] + random.uniform(-cfg["spread_lon"], cfg["spread_lon"])

            is_small_cell = random.random() < cfg["small_cell_ratio"]
            tower_type = "small_cell" if is_small_cell else "macro"
            capacity = random.choice(
                CAPACITY_OPTIONS_SMALL if is_small_cell else CAPACITY_OPTIONS_MACRO
            )

            tower_id = f"TWR-{cfg['code']}-{i:04d}"
            fleet.append({
                "_id": tower_id,
                "region": region_name,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "type": tower_type,
                "frequency_band": random.choice(FREQUENCY_BANDS),
                "max_capacity_subscribers": capacity,
                "installed_year": random.randint(2015, 2024),
            })

    db.towers.insert_many(fleet)

    print(f"Inserted {len(fleet)} towers across {len(DISTRICTS)} districts:")
    for region_name in DISTRICTS:
        sub = [t for t in fleet if t["region"] == region_name]
        macro = sum(1 for t in sub if t["type"] == "macro")
        small = sum(1 for t in sub if t["type"] == "small_cell")
        print(f"  {region_name}: {len(sub)} towers ({macro} macro, {small} small_cell)")


if __name__ == "__main__":
    generate_fleet()
