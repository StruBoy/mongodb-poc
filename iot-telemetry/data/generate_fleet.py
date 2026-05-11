"""Seed the telco_demo.towers collection with 1,000 cell towers spread across
mainland Singapore, partitioned into three distinct geographic zones.

Mainland Singapore is approximated by 22 boundary vertices. Two of those
vertices on the north coast and two on the south coast double as the endpoints
of the inland partition lines that separate West / Central / East:

    SINGAPORE_VERTICES[6]  = Sembawang        (W↔C boundary, north end)
    SINGAPORE_VERTICES[19] = Pasir Panjang    (W↔C boundary, south end)
    SINGAPORE_VERTICES[8]  = Sengkang North   (C↔E boundary, north end)
    SINGAPORE_VERTICES[15] = East Coast Park  (C↔E boundary, south end)

The three district polygons share these edges, so together they tile the
island with no overlap and no gaps. Each district is a contiguous,
geographically distinct zone:

    West    — Tuas, Jurong, Bukit Timah, Bukit Batok / Panjang, Choa Chu Kang,
              Clementi, NUS, Pasir Panjang, Lim Chu Kang, Kranji, Woodlands,
              Sembawang
    Central — CBD, Marina Bay, Orchard, Bishan, Toa Payoh, Ang Mo Kio,
              Geylang, Marine Parade, Yishun, Khatib
    East    — Bedok, Tampines, Pasir Ris, Changi, Hougang, Sengkang, Punggol

Tower IDs stay district-prefixed (TWR-CEN-####, TWR-EST-####, TWR-WST-####)
and counts are 333 / 333 / 334 (West / East / Central) to match
scripts/verify_data.py.

Run from the project root (iot-telemetry/):
    python -m data.generate_fleet
"""
import random

from src.db import get_db

random.seed(42)

# Mainland Singapore vertices, clockwise from Tuas SW. Indices 6, 19 are the
# W↔C partition endpoints; indices 8, 15 are the C↔E endpoints. The polygon
# is intentionally a coarse approximation — accuracy is ~500m, well below the
# dashboard's zoom level. Outlying islands (Sentosa, Pulau Ubin, Pulau Tekong,
# Jurong Island) are not included.
SINGAPORE_VERTICES = [
    (1.310, 103.635),  #  0  Tuas SW
    (1.345, 103.640),  #  1  Tuas NW edge
    (1.385, 103.660),  #  2  Tuas peninsula north
    (1.420, 103.700),  #  3  Lim Chu Kang
    (1.430, 103.730),  #  4  Sungei Buloh
    (1.445, 103.770),  #  5  Woodlands North
    (1.460, 103.825),  #  6  Sembawang          ← W↔C boundary, NORTH end
    (1.450, 103.862),  #  7  Khatib
    (1.430, 103.880),  #  8  Sengkang North     ← C↔E boundary, NORTH end
    (1.420, 103.910),  #  9  Punggol Settlement
    (1.400, 103.960),  # 10  Pasir Ris north coast
    (1.380, 104.000),  # 11  Changi Village
    (1.360, 104.020),  # 12  Changi airport east
    (1.310, 104.005),  # 13  Changi south
    (1.295, 103.965),  # 14  Bedok / ECP east
    (1.290, 103.945),  # 15  East Coast Park    ← C↔E boundary, SOUTH end
    (1.280, 103.890),  # 16  Mountbatten / Marine Parade
    (1.272, 103.855),  # 17  Marina Bay
    (1.275, 103.825),  # 18  Pasir Panjang east
    (1.275, 103.795),  # 19  Pasir Panjang      ← W↔C boundary, SOUTH end
    (1.290, 103.760),  # 20  West Coast Park
    (1.305, 103.700),  # 21  Joo Koon
]


def _ring(indices: list[int]) -> list[tuple[float, float]]:
    return [SINGAPORE_VERTICES[i] for i in indices]


# District polygons are built by walking the Singapore vertex list along the
# coast for the outer edge and along the partition line for the inner edge.
DISTRICTS = {
    "West": {
        "code": "WST",
        # Tuas SW → up west coast → across north to Sembawang → south down the
        # W↔C partition line to Pasir Panjang → west along south coast → back.
        "polygon": _ring([0, 1, 2, 3, 4, 5, 6, 19, 20, 21]),
        "count": 333,
        "small_cell_ratio": 0.25,
    },
    "Central": {
        "code": "CEN",
        # Sembawang → east along north coast to Sengkang North → south down
        # C↔E partition to East Coast Park → west along south coast to Pasir
        # Panjang → north up W↔C partition back to Sembawang.
        "polygon": _ring([6, 7, 8, 15, 16, 17, 18, 19]),
        "count": 334,
        "small_cell_ratio": 0.45,    # CBD has the most small cells
    },
    "East": {
        "code": "EST",
        # Sengkang North → east through Punggol/Pasir Ris/Changi → south to
        # East Coast Park → north up C↔E partition back to Sengkang.
        "polygon": _ring([8, 9, 10, 11, 12, 13, 14, 15]),
        "count": 333,
        "small_cell_ratio": 0.20,
    },
}

FREQUENCY_BANDS = ["700MHz", "1800MHz", "2600MHz", "3500MHz"]
CAPACITY_OPTIONS_MACRO = [1000, 2000, 5000]
CAPACITY_OPTIONS_SMALL = [500, 1000]


def point_in_polygon(lat: float, lon: float, polygon: list[tuple[float, float]]) -> bool:
    """Standard ray-casting test. Polygon is a list of (lat, lon) vertices."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]
        crosses = ((lat_i > lat) != (lat_j > lat)) and (
            lon < (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i + 1e-12) + lon_i
        )
        if crosses:
            inside = not inside
        j = i
    return inside


def _bbox(polygon):
    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]
    return min(lats), max(lats), min(lons), max(lons)


def sample_towers_for_district(cfg: dict) -> list[tuple[float, float]]:
    """Rejection-sample `cfg['count']` (lat, lon) pairs inside the district's
    own polygon. Each district polygon is already inside Singapore, so no
    extra island-level check is needed."""
    polygon = cfg["polygon"]
    lat_min, lat_max, lon_min, lon_max = _bbox(polygon)

    accepted: list[tuple[float, float]] = []
    attempts = 0
    while len(accepted) < cfg["count"]:
        attempts += 1
        lat = random.uniform(lat_min, lat_max)
        lon = random.uniform(lon_min, lon_max)
        if point_in_polygon(lat, lon, polygon):
            accepted.append((lat, lon))
        if attempts > cfg["count"] * 1000:
            raise RuntimeError(
                f"Rejection sampling looped past {attempts} attempts for "
                f"{cfg['code']} — district polygon may be malformed."
            )
    return accepted


def generate_fleet():
    db = get_db()
    db.towers.delete_many({})

    fleet = []
    for region_name, cfg in DISTRICTS.items():
        coords = sample_towers_for_district(cfg)
        for i, (lat, lon) in enumerate(coords, start=1):
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

    print(f"Inserted {len(fleet)} towers across {len(DISTRICTS)} distinct Singapore zones:")
    for region_name, cfg in DISTRICTS.items():
        sub = [t for t in fleet if t["region"] == region_name]
        macro = sum(1 for t in sub if t["type"] == "macro")
        small = sum(1 for t in sub if t["type"] == "small_cell")
        lats = [t["lat"] for t in sub]
        lons = [t["lon"] for t in sub]
        print(
            f"  {region_name:8} ({cfg['code']}): {len(sub):4} towers "
            f"({macro:3} macro, {small:3} small_cell)  "
            f"lat[{min(lats):.3f}–{max(lats):.3f}] lon[{min(lons):.3f}–{max(lons):.3f}]"
        )


if __name__ == "__main__":
    generate_fleet()
