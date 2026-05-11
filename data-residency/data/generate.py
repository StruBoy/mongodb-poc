"""Seed residency_demo with 100 customers + ~75 orders each, all in US zone.

Customers are spread across ~35 cities in 30+ countries so the natural-region
fanout has visibly distinct AMER / EMEA / APAC populations on the world map.
Orders are uniformly distributed across the last year, with realistic SKU /
qty / price ranges. All initial documents have `location: "US"` regardless of
the customer's business country — that's the starting state for the demo.

Deterministic: random.seed(42).

Run from the project root (data-residency/) AFTER:
    python -m scripts.create_db_index
Then:
    python -m data.generate
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from src.db import get_db

random.seed(42)

# All seed data starts in the US zone. The on-disk `location` field is the
# ISO country code "US". Atlas Global Writes maps "US" to the US zone.
# After a "Migrate to natural regions" run, each customer's `location` is
# updated to their actual business country code (CA, DE, JP, etc.).
INITIAL_LOCATION = "US"
NUM_CUSTOMERS = 100
ORDERS_PER_CUSTOMER_RANGE = (50, 100)

# (country_code, city, lat, lon, weight) — weight controls how often this
# city is sampled. Roughly: ~33 customers in AMER, ~33 in EMEA, ~34 in APAC
# after natural-region fanout, so weights inside each region don't need to
# be perfectly balanced.
CITIES = [
    # ------ AMER (US zone naturally) ------
    ("US", "New York",       40.7128,  -74.0060, 6),
    ("US", "San Francisco",  37.7749, -122.4194, 5),
    ("US", "Chicago",        41.8781,  -87.6298, 4),
    ("US", "Austin",         30.2672,  -97.7431, 3),
    ("US", "Seattle",        47.6062, -122.3321, 3),
    ("US", "Miami",          25.7617,  -80.1918, 2),
    ("US", "Boston",         42.3601,  -71.0589, 2),
    ("CA", "Toronto",        43.6532,  -79.3832, 3),
    ("CA", "Vancouver",      49.2827, -123.1207, 2),
    ("MX", "Mexico City",    19.4326,  -99.1332, 2),
    ("BR", "São Paulo",     -23.5505,  -46.6333, 3),
    ("AR", "Buenos Aires",  -34.6037,  -58.3816, 1),
    ("CL", "Santiago",      -33.4489,  -70.6693, 1),
    ("CO", "Bogotá",          4.7110,  -74.0721, 1),

    # ------ EMEA (EU zone naturally) ------
    ("GB", "London",         51.5074,   -0.1278, 5),
    ("DE", "Berlin",         52.5200,   13.4050, 4),
    ("FR", "Paris",          48.8566,    2.3522, 4),
    ("ES", "Madrid",         40.4168,   -3.7038, 3),
    ("IT", "Milan",          45.4642,    9.1900, 3),
    ("NL", "Amsterdam",      52.3676,    4.9041, 3),
    ("IE", "Dublin",         53.3498,   -6.2603, 2),
    ("CH", "Zurich",         47.3769,    8.5417, 2),
    ("SE", "Stockholm",      59.3293,   18.0686, 2),
    ("PL", "Warsaw",         52.2297,   21.0122, 1),
    ("AT", "Vienna",         48.2082,   16.3738, 1),
    ("PT", "Lisbon",         38.7223,   -9.1393, 1),
    ("AE", "Dubai",          25.2048,   55.2708, 2),
    ("ZA", "Johannesburg",  -26.2041,   28.0473, 1),
    ("NG", "Lagos",           6.5244,    3.3792, 1),
    ("KE", "Nairobi",        -1.2921,   36.8219, 1),
    ("IL", "Tel Aviv",       32.0853,   34.7818, 1),
    ("TR", "Istanbul",       41.0082,   28.9784, 1),

    # ------ APAC (APAC zone naturally) ------
    ("SG", "Singapore",       1.3521,  103.8198, 4),
    ("JP", "Tokyo",          35.6762,  139.6503, 5),
    ("AU", "Sydney",        -33.8688,  151.2093, 4),
    ("AU", "Melbourne",     -37.8136,  144.9631, 2),
    ("NZ", "Auckland",      -36.8485,  174.7633, 1),
    ("KR", "Seoul",          37.5665,  126.9780, 3),
    ("HK", "Hong Kong",      22.3193,  114.1694, 3),
    ("TW", "Taipei",         25.0330,  121.5654, 2),
    ("IN", "Mumbai",         19.0760,   72.8777, 4),
    ("IN", "Bangalore",      12.9716,   77.5946, 3),
    ("ID", "Jakarta",        -6.2088,  106.8456, 2),
    ("MY", "Kuala Lumpur",    3.1390,  101.6869, 2),
    ("TH", "Bangkok",        13.7563,  100.5018, 2),
    ("VN", "Ho Chi Minh",    10.8231,  106.6297, 1),
    ("PH", "Manila",         14.5995,  120.9842, 2),
]

# Industry / company-name building blocks. Random combos generate ~5K unique
# company names from a finite vocabulary, which is plenty for 100 customers.
INDUSTRIES = [
    "Financial Services", "Logistics", "Manufacturing", "Retail",
    "Healthcare", "Technology", "Media", "Energy", "Consumer Goods",
    "Telecommunications", "Education", "Hospitality",
]
NAME_PREFIXES = [
    "Global", "Pacific", "Atlas", "Meridian", "Northern", "Summit",
    "Vanguard", "Apex", "Horizon", "Coastal", "Continental", "Beacon",
    "Frontier", "Pinnacle", "Cascade", "Alpine", "Cardinal", "Onyx",
]
NAME_CORES = [
    "Trading", "Holdings", "Industries", "Partners", "Logistics", "Group",
    "Capital", "Networks", "Systems", "Dynamics", "Solutions", "Works",
    "Enterprises", "Ventures", "Co", "Labs",
]
NAME_SUFFIXES = ["Inc", "Ltd", "GmbH", "SA", "Pte Ltd", "AG", "K.K.", "Pty Ltd"]

SKUS = [f"SKU-{c}-{n:03d}" for c in ("A", "B", "C", "D", "E") for n in range(1, 41)]


def weighted_pick_city():
    weights = [c[4] for c in CITIES]
    return random.choices(CITIES, weights=weights, k=1)[0]


def make_company_name() -> str:
    prefix = random.choice(NAME_PREFIXES)
    core = random.choice(NAME_CORES)
    suffix = random.choice(NAME_SUFFIXES)
    return f"{prefix} {core} {suffix}"


def jitter(value: float, scale: float) -> float:
    """Spread customers a little around city centres so the map doesn't pile dots."""
    return value + random.uniform(-scale, scale)


def make_customer(cust_id: str, created_at: datetime) -> dict:
    country, city, lat, lon, _w = weighted_pick_city()
    return {
        "_id": cust_id,
        "name": make_company_name(),
        "industry": random.choice(INDUSTRIES),
        "country": country,
        "city": city,
        "biz_lat": round(jitter(lat, 0.5), 4),
        "biz_lon": round(jitter(lon, 0.5), 4),
        "location": INITIAL_LOCATION,
        "created_at": created_at,
    }


def make_order(cust_id: str, location: str, placed_at: datetime) -> dict:
    qty = random.randint(1, 50)
    unit_price = round(random.uniform(2.5, 950.0), 2)
    return {
        "customer_id": cust_id,
        "location": location,
        "sku": random.choice(SKUS),
        "qty": qty,
        "unit_price_usd": unit_price,
        "total_usd": round(qty * unit_price, 2),
        "placed_at": placed_at,
    }


def main():
    db = get_db()

    # Clear any existing data (keep the chunk pre-splits — those live in config).
    print("Clearing existing customers + orders…")
    db.customers.delete_many({})
    db.orders.delete_many({})

    now = datetime.now(timezone.utc)

    # Customers were "created" some time in the past 2 years.
    customers = []
    for i in range(1, NUM_CUSTOMERS + 1):
        cust_id = f"cust-{i:03d}"
        days_ago = random.randint(60, 730)
        created_at = now - timedelta(days=days_ago)
        customers.append(make_customer(cust_id, created_at))

    print(f"Inserting {len(customers)} customers (all in {INITIAL_LOCATION} zone)…")
    db.customers.insert_many(customers)

    # Orders — placed any time in the last 365 days, ~50–100 per customer.
    orders = []
    for c in customers:
        n = random.randint(*ORDERS_PER_CUSTOMER_RANGE)
        for _ in range(n):
            days_ago = random.randint(1, 365)
            seconds_jitter = random.randint(0, 86400)
            placed_at = now - timedelta(days=days_ago, seconds=seconds_jitter)
            orders.append(make_order(c["_id"], INITIAL_LOCATION, placed_at))

    print(f"Inserting {len(orders):,} orders (all in {INITIAL_LOCATION} zone)…")
    # Bulk insert in chunks to avoid the BSON 16MB limit on a single insert_many
    BATCH = 1000
    for i in range(0, len(orders), BATCH):
        db.orders.insert_many(orders[i:i + BATCH])

    # Quick country distribution summary
    by_country: dict[str, int] = {}
    for c in customers:
        by_country[c["country"]] = by_country.get(c["country"], 0) + 1
    print("\nCustomers by country:")
    for cc in sorted(by_country, key=lambda k: -by_country[k]):
        print(f"  {cc:3s}  {by_country[cc]:3d}")

    print(f"\nTotal: {len(customers)} customers + {len(orders):,} orders.")
    print("All data is currently in the 'US' zone. Run the Streamlit app to demo migration.")


if __name__ == "__main__":
    main()
