"""Zones, regions, and country -> zone mappings for the residency demo.

ZONE_NAMES — the three values used as the `location` shard-key field on
`customers` and `orders`. They MUST match the Zone names configured in the
Atlas Global Cluster UI (Cluster → ⋯ → Edit Configuration → Global Writes
Configuration). Any drift here means writes go to the wrong shard or fail
zone validation.

ZONE_REGION_INFO — display metadata used by the Streamlit map: the data-centre
city, lat/lon, and AWS region for each zone. Lat/lon are used to draw lines
from each customer's business location to their data-centre dot.

ZONE_FOR_COUNTRY — flat dict mapping ISO-3166 alpha-2 country codes to one of
the three zones. Used by `data.generate` to assign business addresses, and by
the "Migrate to natural regions" button to compute the target zone for each
customer.
"""
from __future__ import annotations

ZONE_NAMES = ["US", "EU", "APAC"]

# Atlas Global Writes requires location codes to be valid ISO 3166-1 alpha-2
# country codes. The `location` field stores a real country code for every
# document — typically the customer's business country. Atlas's Global Writes
# zone mapping then routes documents to the right zone based on the country
# code (every country we use must be mapped to its zone in Atlas).
#
# When a customer is moved to a zone other than their natural one (cross-zone
# override), we pick the zone's representative country code instead of the
# customer's own country, since the customer's country wouldn't route to the
# target zone.
ZONE_REPRESENTATIVE_CODE = {
    "US": "US",  # United States
    "EU": "DE",  # Germany
    "APAC": "SG",  # Singapore
}


def representative_code_for_zone(zone: str) -> str:
    """The ISO country code used as the default location for a cross-zone
    override of a customer whose own country doesn't fall in the target zone."""
    return ZONE_REPRESENTATIVE_CODE[zone]


ZONE_REGION_INFO = {
    "US": {
        "city": "Virginia",
        "cloud_provider": "AWS",
        "cloud_region": "us-east-1",
        "lat": 37.5407,
        "lon": -77.4360,
        "color": "#1f77b4",  # blue
    },
    "EU": {
        "city": "Frankfurt",
        "cloud_provider": "Azure",
        "cloud_region": "germanywestcentral",
        "lat": 50.1109,
        "lon": 8.6821,
        "color": "#2ca02c",  # green
    },
    "APAC": {
        "city": "Singapore",
        "cloud_provider": "GCP",
        "cloud_region": "asia-southeast1",
        "lat": 1.3521,
        "lon": 103.8198,
        "color": "#ff7f0e",  # orange
    },
}

ZONE_FOR_COUNTRY = {
    # AMER -> US zone
    "US": "US",
    "CA": "US",
    "MX": "US",
    "BR": "US",
    "AR": "US",
    "CL": "US",
    "CO": "US",
    "PE": "US",
    # EMEA -> EU zone
    "GB": "EU",
    "DE": "EU",
    "FR": "EU",
    "ES": "EU",
    "IT": "EU",
    "NL": "EU",
    "SE": "EU",
    "IE": "EU",
    "PL": "EU",
    "CH": "EU",
    "AT": "EU",
    "BE": "EU",
    "DK": "EU",
    "FI": "EU",
    "NO": "EU",
    "PT": "EU",
    "GR": "EU",
    "CZ": "EU",
    "RO": "EU",
    "ZA": "EU",
    "AE": "EU",
    "IL": "EU",
    "TR": "EU",
    "NG": "EU",
    "KE": "EU",
    "EG": "EU",
    # APAC -> APAC zone
    "SG": "APAC",
    "JP": "APAC",
    "AU": "APAC",
    "NZ": "APAC",
    "IN": "APAC",
    "ID": "APAC",
    "MY": "APAC",
    "TH": "APAC",
    "VN": "APAC",
    "PH": "APAC",
    "KR": "APAC",
    "HK": "APAC",
    "TW": "APAC",
    "BD": "APAC",
    "PK": "APAC",
}


def zone_for_country(country_code: str) -> str:
    """Look up zone for a country code; default to US for unknown codes."""
    return ZONE_FOR_COUNTRY.get(country_code, "US")


# A document's `location` field IS an ISO country code, so the zone of any
# document is just `zone_for_country` of its location value. Alias the two
# for callers that want to read intent rather than mechanics.
zone_for_location_code = zone_for_country
