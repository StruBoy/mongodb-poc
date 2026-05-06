# PoC 4: Polymorphic Citizen Services Case Management — Implementation Plan

**Customer Segment:** Government & Public Sector
**Time Budget:** 3.5 hours
**Cluster Tier:** Atlas M0 (free) is sufficient
**Total Cost (afternoon):** ~USD 0 (free tier)

---

## What you're building

A citizen services portal where wildly different case types — business permits, building permits, complaints, benefit applications, marriage registrations — all live in a single MongoDB collection with type-specific schemas. A unified citizen view shows their entire interaction history across all case types. Sensitive PII fields are encrypted at the field level. An admin can add a brand new case type without any schema migration.

## What it demonstrates

- **Polymorphism without pain**: heterogeneous documents in one collection, queryable together
- **Field-level encryption** (Queryable Encryption) for PII compliance
- **Unified search** across case types via Atlas Search
- **Schema-on-read agility**: new service types added without migrations or downtime

## The demo moment

Two parts. First, a citizen logs in and sees their five different cases — marriage registration, business permit, noise complaint, disability benefit, building permit — in one chronological timeline despite each having a totally different structure. Second, an officer adds a new "EV Charging Station Permit" case type by inserting a single document. It's instantly searchable. No migration. No deployment. No project plan.

---

## Prerequisites

- Atlas account
- Python 3.11+
- Optional: `mongocryptd` and `libmongocrypt` for native CSFLE — for the PoC we use Atlas's automatic Queryable Encryption which simplifies setup considerably

---

## Phase 1: Atlas Setup (30 min)

### 1.1 Provision the cluster

1. Create project `poc-citizen-services`
2. Build cluster: M0 (free), AWS, Singapore region
3. Wait ~5 minutes

### 1.2 Configure access

- Database user `pocuser` with `readWriteAnyDatabase`
- Network access from your IP

### 1.3 Create the database

```javascript
use citizen_demo
db.createCollection("cases")
db.createCollection("citizens")
```

### 1.4 Define the Atlas Search index

`citizen_demo.cases`, name `cases_search_idx` — note the dynamic mapping, which is the whole point:

```json
{
  "mappings": {
    "dynamic": true,
    "fields": {
      "case_type": { "type": "token" },
      "status": { "type": "token" },
      "citizen_id": { "type": "token" },
      "created_at": { "type": "date" }
    }
  }
}
```

The `"dynamic": true` setting is what makes this work — Atlas Search will index every field that appears in any document, regardless of which case type it came from. This is the technical detail that demonstrates the polymorphism advantage.

---

## Phase 2: Project Setup & Polymorphic Data (60 min)

### 2.1 Project scaffolding

```bash
mkdir poc-citizen-services && cd poc-citizen-services
python -m venv venv && source venv/bin/activate
pip install pymongo faker streamlit python-dotenv pandas cryptography
```

`.env`:

```
MONGODB_URI=mongodb+srv://pocuser:<password>@<cluster>.mongodb.net/
ENCRYPTION_KEY=<generate via: python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())">
```

### 2.2 Database helper (`src/db.py`)

```python
import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
_client = None

def get_client():
    global _client
    if _client is None:
        _client = MongoClient(os.environ["MONGODB_URI"])
    return _client

def get_db():
    return get_client()["citizen_demo"]
```

### 2.3 Application-level field encryption (`src/crypto.py`)

For demo simplicity we use AES-GCM at the application layer. For production, use MongoDB Queryable Encryption (CSFLE) — flag this distinction during the demo. The behavior shown to the audience (encrypted-at-rest PII, decrypted only by authorized callers) is the same.

```python
import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

load_dotenv()

_key = base64.b64decode(os.environ["ENCRYPTION_KEY"])
_aes = AESGCM(_key)


def encrypt_field(plaintext: str) -> dict:
    """Encrypt a string field. Returns a document with ciphertext + nonce, both base64-encoded."""
    if plaintext is None:
        return None
    nonce = os.urandom(12)
    ct = _aes.encrypt(nonce, plaintext.encode("utf-8"), None)
    return {
        "_encrypted": True,
        "nonce": base64.b64encode(nonce).decode(),
        "ciphertext": base64.b64encode(ct).decode()
    }


def decrypt_field(field: dict) -> str:
    if field is None or not isinstance(field, dict) or not field.get("_encrypted"):
        return field
    nonce = base64.b64decode(field["nonce"])
    ct = base64.b64decode(field["ciphertext"])
    return _aes.decrypt(nonce, ct, None).decode("utf-8")


# Fields that are always encrypted before write
PII_FIELDS = {"national_id", "dob", "tax_file_number"}


def encrypt_pii(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if k in PII_FIELDS and isinstance(v, str):
            out[k] = encrypt_field(v)
        elif isinstance(v, dict):
            out[k] = encrypt_pii(v)
        else:
            out[k] = v
    return out


def decrypt_pii(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if k in PII_FIELDS and isinstance(v, dict) and v.get("_encrypted"):
            out[k] = decrypt_field(v)
        elif isinstance(v, dict) and not v.get("_encrypted"):
            out[k] = decrypt_pii(v)
        else:
            out[k] = v
    return out
```

### 2.4 Polymorphic data generators (`data/generate.py`)

This is the heart of the PoC — five totally different case shapes in one collection.

```python
import random
from datetime import datetime, timedelta
from faker import Faker
from src.db import get_db
from src.crypto import encrypt_pii

fake = Faker("en_AU")
random.seed(42)


def generate_citizen():
    return {
        "_id": fake.uuid4(),
        "full_name": fake.name(),
        "national_id": fake.bothify(text="???######").upper(),
        "dob": fake.date_of_birth(minimum_age=18, maximum_age=85).isoformat(),
        "address": fake.address().replace("\n", ", "),
        "email": fake.email(),
        "phone": fake.phone_number(),
        "registered_at": fake.date_time_between(start_date="-10y", end_date="-1y")
    }


# ============================================================
# Case generators — each has a totally different schema
# ============================================================

def gen_business_permit(citizen_id):
    return {
        "case_type": "business_permit",
        "citizen_id": citizen_id,
        "business_name": fake.company(),
        "business_type": random.choice(["cafe", "retail", "consulting", "construction", "tech"]),
        "abn": fake.bothify(text="## ### ### ###"),
        "premises_address": fake.address().replace("\n", ", "),
        "expected_employees": random.randint(1, 50),
        "annual_revenue_estimate_aud": random.randint(50000, 5000000),
        "operating_hours": f"{random.randint(6,9):02d}:00 - {random.randint(17,22):02d}:00",
        "status": random.choice(["pending", "approved", "approved", "approved", "rejected"]),
        "fee_paid_aud": random.choice([0, 200, 500, 1000]),
        "submitted_documents": ["company_registration", "lease_agreement", "insurance_certificate"],
        "created_at": fake.date_time_between(start_date="-3y", end_date="now")
    }


def gen_building_permit(citizen_id):
    return {
        "case_type": "building_permit",
        "citizen_id": citizen_id,
        "property_address": fake.address().replace("\n", ", "),
        "construction_type": random.choice(["new_dwelling", "extension", "renovation", "demolition", "outbuilding"]),
        "estimated_cost_aud": random.randint(20000, 1500000),
        "estimated_duration_weeks": random.randint(2, 52),
        "land_area_sqm": random.randint(200, 2000),
        "floor_area_sqm": random.randint(50, 500),
        "storeys": random.randint(1, 4),
        "structural_engineer": fake.name(),
        "architect": fake.name(),
        "status": random.choice(["pending", "approved", "approved", "under_review", "rejected"]),
        "inspection_dates": [fake.date_between(start_date="-1y", end_date="today").isoformat() for _ in range(random.randint(0, 4))],
        "created_at": fake.date_time_between(start_date="-2y", end_date="now")
    }


def gen_complaint(citizen_id):
    return {
        "case_type": "complaint",
        "citizen_id": citizen_id,
        "complaint_category": random.choice(["noise", "rubbish", "graffiti", "tree_dispute", "parking", "footpath"]),
        "incident_address": fake.address().replace("\n", ", "),
        "incident_description": fake.sentence(nb_words=20),
        "incident_time": fake.date_time_between(start_date="-1y", end_date="now").isoformat(),
        "severity": random.choice(["low", "medium", "medium", "high"]),
        "anonymous": random.choice([True, False, False]),
        "photos_attached": random.randint(0, 5),
        "status": random.choice(["open", "investigating", "resolved", "resolved", "closed"]),
        "officer_assigned": fake.name() if random.random() > 0.3 else None,
        "resolution_notes": fake.paragraph() if random.random() > 0.4 else None,
        "created_at": fake.date_time_between(start_date="-1y", end_date="now")
    }


def gen_benefit(citizen_id):
    return {
        "case_type": "benefit_application",
        "citizen_id": citizen_id,
        "benefit_program": random.choice(["disability_support", "carer_payment", "youth_allowance", "rent_assistance", "newstart"]),
        "tax_file_number": fake.bothify(text="### ### ###"),  # encrypted before write
        "household_size": random.randint(1, 6),
        "household_income_aud": random.randint(0, 90000),
        "assets_declared_aud": random.randint(0, 200000),
        "medical_assessment_required": random.choice([True, False]),
        "medical_practitioner": fake.name() if random.random() > 0.5 else None,
        "supporting_documents": random.sample(
            ["medicare_card", "bank_statements", "medical_certificate", "lease_agreement", "tax_return"],
            k=random.randint(2, 5)
        ),
        "status": random.choice(["pending_assessment", "approved", "approved", "denied", "additional_info_required"]),
        "monthly_payment_aud": random.choice([0, 0, 380, 520, 780, 1100]),
        "created_at": fake.date_time_between(start_date="-2y", end_date="now")
    }


def gen_marriage(citizen_id):
    return {
        "case_type": "marriage_registration",
        "citizen_id": citizen_id,
        "partner_full_name": fake.name(),
        "partner_national_id": fake.bothify(text="???######").upper(),  # encrypted
        "ceremony_date": fake.date_between(start_date="-5y", end_date="+1y").isoformat(),
        "ceremony_location": fake.city() + ", NSW",
        "celebrant_name": fake.name(),
        "celebrant_registration_id": fake.bothify(text="C-####"),
        "witnesses": [fake.name(), fake.name()],
        "notice_of_intent_filed": fake.date_between(start_date="-6y", end_date="-1m").isoformat(),
        "status": random.choice(["registered", "registered", "pending", "registered"]),
        "certificate_issued": random.choice([True, False]),
        "created_at": fake.date_time_between(start_date="-5y", end_date="now")
    }


GENERATORS = [gen_business_permit, gen_building_permit, gen_complaint, gen_benefit, gen_marriage]


def main():
    db = get_db()
    db.cases.delete_many({})
    db.citizens.delete_many({})

    # Generate 500 citizens
    citizens = [generate_citizen() for _ in range(500)]
    db.citizens.insert_many([encrypt_pii(c) for c in citizens])
    print(f"Inserted {len(citizens)} citizens.")

    # Generate 2000 cases distributed across citizens
    cases = []
    for _ in range(2000):
        citizen = random.choice(citizens)
        gen = random.choice(GENERATORS)
        case = gen(citizen["_id"])
        cases.append(encrypt_pii(case))

    # Make sure at least one citizen has all five case types — for the demo
    demo_citizen = citizens[0]
    print(f"\nDemo citizen for the talk track: {demo_citizen['full_name']} ({demo_citizen['_id']})")
    for gen in GENERATORS:
        cases.append(encrypt_pii(gen(demo_citizen["_id"])))

    db.cases.insert_many(cases)
    print(f"Inserted {len(cases)} cases across 5 case types.")


if __name__ == "__main__":
    main()
```

Run it:

```bash
python -m data.generate
```

Save the demo citizen ID printed at the end — you'll use it during the demo.

---

## Phase 3: Service Layer (45 min)

### 3.1 Case service (`src/services.py`)

```python
from datetime import datetime
from src.db import get_db
from src.crypto import encrypt_pii, decrypt_pii


def get_citizen(citizen_id: str) -> dict:
    db = get_db()
    doc = db.citizens.find_one({"_id": citizen_id})
    return decrypt_pii(doc) if doc else None


def get_cases_for_citizen(citizen_id: str) -> list:
    db = get_db()
    docs = list(db.cases.find({"citizen_id": citizen_id}).sort("created_at", -1))
    return [decrypt_pii(d) for d in docs]


def search_cases(query: str, limit: int = 20) -> list:
    db = get_db()
    pipeline = [
        {
            "$search": {
                "index": "cases_search_idx",
                "text": {
                    "query": query,
                    "path": {"wildcard": "*"}
                }
            }
        },
        {"$limit": limit},
        {"$project": {"score": {"$meta": "searchScore"}, "embedding": 0}}
    ]
    docs = list(db.cases.aggregate(pipeline))
    return [decrypt_pii(d) for d in docs]


def case_summary_by_type() -> dict:
    db = get_db()
    pipeline = [
        {"$group": {
            "_id": "$case_type",
            "count": {"$sum": 1},
            "open": {"$sum": {"$cond": [{"$in": ["$status", ["pending", "open", "investigating", "under_review", "pending_assessment"]]}, 1, 0]}}
        }},
        {"$sort": {"_id": 1}}
    ]
    return list(db.cases.aggregate(pipeline))


def add_new_case_type(case_type: str, citizen_id: str, custom_fields: dict) -> str:
    """Demonstrates: a brand new case type can be added with no migration."""
    db = get_db()
    case = {
        "case_type": case_type,
        "citizen_id": citizen_id,
        "status": "pending",
        "created_at": datetime.utcnow(),
        **custom_fields
    }
    encrypted = encrypt_pii(case)
    result = db.cases.insert_one(encrypted)
    return str(result.inserted_id)
```

---

## Phase 4: Streamlit UI (60 min)

### 4.1 Dual-portal interface (`app.py`)

```python
import streamlit as st
from datetime import datetime
import json
from src.services import (
    get_citizen, get_cases_for_citizen,
    search_cases, case_summary_by_type, add_new_case_type
)
from src.db import get_db

st.set_page_config(page_title="Citizen Services Portal", layout="wide")
st.title("🏛️ Citizen Services Portal")
st.caption("MongoDB Atlas: polymorphic case data, field-level encryption, unified search")

mode = st.sidebar.radio("View as", ["Citizen", "Government Officer", "Encryption Inspector"])

# ============================================================
# CITIZEN VIEW
# ============================================================
if mode == "Citizen":
    db = get_db()
    citizens = list(db.citizens.find().limit(20))
    citizen_options = {f"{i+1}. (encrypted record)": c["_id"] for i, c in enumerate(citizens)}
    selected = st.selectbox("Select citizen account", list(citizen_options.keys()))
    citizen_id = citizen_options[selected]

    citizen = get_citizen(citizen_id)
    st.subheader(f"Welcome, {citizen['full_name']}")
    st.caption(f"Citizen ID: {citizen_id}")

    cases = get_cases_for_citizen(citizen_id)
    if not cases:
        st.info("No cases on file.")
    else:
        st.markdown(f"### Your case history ({len(cases)} cases across {len(set(c['case_type'] for c in cases))} types)")
        for case in cases:
            type_emoji = {
                "business_permit": "🏢",
                "building_permit": "🏗️",
                "complaint": "📢",
                "benefit_application": "💰",
                "marriage_registration": "💍"
            }.get(case["case_type"], "📄")

            with st.expander(
                f"{type_emoji} **{case['case_type'].replace('_', ' ').title()}** — "
                f"{case['status']} — {case['created_at'].strftime('%Y-%m-%d')}"
            ):
                # Render the case-type-specific fields naturally
                display_doc = {k: v for k, v in case.items() if k not in {"_id", "citizen_id", "embedding"}}
                st.json(display_doc, expanded=True)

# ============================================================
# OFFICER VIEW
# ============================================================
elif mode == "Government Officer":
    st.subheader("📊 Case dashboard")
    summary = case_summary_by_type()
    cols = st.columns(len(summary))
    for col, row in zip(cols, summary):
        col.metric(row["_id"].replace("_", " ").title(), row["count"], f"{row['open']} open")

    st.markdown("---")

    st.subheader("🔍 Cross-case search")
    st.caption("Search every case type from a single index. Try: 'noise', 'cafe', 'extension', 'disability'.")
    q = st.text_input("Search query")
    if q:
        results = search_cases(q, limit=10)
        st.write(f"Found {len(results)} matches across {len(set(r['case_type'] for r in results))} case types.")
        for r in results:
            with st.container(border=True):
                st.markdown(f"**{r['case_type'].replace('_', ' ').title()}** — relevance: `{r['score']:.3f}`")
                st.json({k: v for k, v in r.items() if k not in {"_id", "score", "embedding"}}, expanded=False)

    st.markdown("---")

    st.subheader("➕ Add a new case type (no migration)")
    st.caption("Demonstrates the polymorphism advantage: define a new service in seconds.")
    new_type = st.text_input("New case type identifier", value="ev_charging_station_permit")
    new_fields_json = st.text_area("Custom fields (JSON)", value=json.dumps({
        "premises_address": "42 Sample St, Sydney NSW 2000",
        "charger_count": 4,
        "kw_per_charger": 50,
        "grid_capacity_check_passed": True,
        "estimated_install_cost_aud": 85000,
        "expected_completion_date": "2026-09-15"
    }, indent=2), height=200)

    db = get_db()
    sample_citizen = db.citizens.find_one()
    if st.button("Insert new case type", type="primary"):
        try:
            fields = json.loads(new_fields_json)
            case_id = add_new_case_type(new_type, sample_citizen["_id"], fields)
            st.success(f"✅ Inserted new case type `{new_type}` with id `{case_id}`. No migration. No downtime.")
            st.balloons()
        except Exception as e:
            st.error(f"Failed: {e}")

# ============================================================
# ENCRYPTION INSPECTOR
# ============================================================
else:
    st.subheader("🔐 Field-level encryption inspector")
    st.caption("Shows what an unauthorized reader would see in MongoDB directly. Compare to the citizen view.")

    db = get_db()
    raw_citizen = db.citizens.find_one()
    raw_case = db.cases.find_one({"case_type": "benefit_application"})

    st.markdown("### Citizen record (raw, encrypted PII fields)")
    st.json({k: v for k, v in raw_citizen.items() if k != "embedding"})

    st.markdown("### Benefit application (raw, encrypted tax_file_number)")
    if raw_case:
        st.json({k: v for k, v in raw_case.items() if k not in {"embedding"}})

    st.info("Notice: `national_id`, `dob`, and `tax_file_number` show as objects with ciphertext and nonce — not as plaintext. Only authorized application code with the encryption key can decrypt them.")
```

### 4.2 Launch

```bash
streamlit run app.py
```

---

## Phase 5: Demo Polish (30 min)

1. Confirm the demo citizen (the first citizen in your generated set) has all five case types
2. Test the cross-case search with several terms — make sure results return across multiple types
3. Practice the "add new case type" sequence so it flows smoothly

---

## Demo Script

**[1 min] The structural problem.**
"In every government IT environment we walk into, citizen data lives across dozens of systems — one for permits, one for benefits, one for complaints, one for registrations. Each has its own database, its own schema, its own deployment cycle. When a citizen calls and asks 'what's the status of my interaction with my council,' there is no good answer because there is no unified view."

**[2 min] The citizen view.**
Switch to Citizen mode. "This is one MongoDB collection. It contains case data for everyone in the city. Watch what happens when this citizen logs in."

Show the timeline. "Five completely different case types — a marriage registration from 2019, a business permit from 2022, a noise complaint from 2023, a disability benefit from 2024, a building permit from 2025. Each has a totally different shape. The marriage record has a celebrant ID. The benefit application has household income. The complaint has a severity field. They all live together. They are all queryable together."

**[1 min] The officer search.**
Switch to Officer mode. "Type 'noise' — we get complaints. Type 'cafe' — we get business permits. Type 'extension' — we get building permits. One search index, every case type. No federation, no ETL."

**[1 min] The migration moment.**
"Now, the city council just announced a new permit category — EV charging stations. In every other system you've worked with, this would be a project. Schema change, migration window, regression testing, deployment. Here it's this." Click the insert button. "It's there. It's searchable. The citizen view will display it correctly. Nothing was migrated."

**[30 sec] The encryption point.**
Switch to Encryption Inspector mode. "And before anyone in your security team asks: this is what the data actually looks like in the database. National IDs and tax file numbers are encrypted at the field level. Only authorized application code holding the key can decrypt them. The auditor's first question — how is sensitive PII protected — has a clear answer."

**[30 sec] Land the architectural point.**
"This isn't about MongoDB being clever. It's about the document model removing a constraint that forces every other system to fragment citizen data across silos. When the schema can vary per record, you stop needing a separate system per service."

---

## Troubleshooting

**Atlas Search returns no results.**
The dynamic mapping needs the index to be "Active." Check Atlas UI → Atlas Search and confirm. If status is "Stale" or "Building," wait 1-2 minutes.

**"No module named 'cryptography'" error.**
Install via `pip install cryptography`. Required for `cryptography.hazmat`.

**`encrypt_pii` raises on nested encrypted dicts during re-decryption.**
Ensure the `_encrypted` flag check in `decrypt_pii` runs before recursing. The recursion guard prevents double-decryption.

**Demo citizen has only one or two case types.**
The data generator picks randomly. Re-run with `random.seed(42)` set, or manually insert all five types for the demo citizen at the bottom of `generate.py`.

---

## Cleanup

```bash
# Pause or terminate the Atlas cluster
deactivate
rm -rf poc-citizen-services
```

---

## Variations

- **Add audit logging**: every `decrypt_pii` call logs which user accessed which field, demonstrating compliance with auditor expectations
- **Role-based decryption**: introduce a `role` parameter to `decrypt_pii` that omits certain fields based on the caller's role
- **Add Queryable Encryption** (production-grade): replace the application-layer AES-GCM with MongoDB's native Queryable Encryption, which allows equality and range queries on encrypted fields
- **Multi-tenancy**: add a `council_id` field and demonstrate tenant isolation across multiple local government areas in one cluster

---

## Note on Queryable Encryption

The PoC uses application-level AES-GCM for simplicity. In production, MongoDB Queryable Encryption is the right answer — it's a built-in feature that lets you encrypt fields automatically and run equality, prefix, and range queries against the encrypted data without ever decrypting it on the server. Setup involves a key vault, automatic encryption rules in the connection config, and `mongocryptd`. Mention this distinction during the demo: the architectural pattern shown here is correct, but in production the database itself does the encryption work.
