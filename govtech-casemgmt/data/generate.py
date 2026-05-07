"""Generate the polymorphic citizen-services dataset.

5,000 citizens and 20,000 cases across five wildly different schemas, all
landing in `citizen_demo.cases` — that single fact is the whole point of the
PoC. Sensitive PII fields (national_id, dob, tax_file_number,
partner_national_id) are encrypted before insert.

The first citizen in the generated set is the "demo citizen": we explicitly
plant one of every case type for them so the citizen-view timeline lights up
all five emojis without depending on the random distribution.

Run from the project root (govtech-casemgmt/):
    python -m data.generate
"""
import random
from datetime import datetime, timedelta, timezone

from faker import Faker

from src.crypto import encrypt_pii
from src.db import get_db

fake = Faker("en_AU")
Faker.seed(42)
random.seed(42)

NUM_CITIZENS = 5_000
NUM_CASES = 20_000

# Australian State / Territory codes weighted roughly by population — keeps the
# data feeling like a real NSW-anchored federal-and-state portal rather than a
# sea of identical postcodes.
NSW_SUBURBS = [
    "Sydney", "Parramatta", "Newcastle", "Wollongong", "Penrith", "Liverpool",
    "Blacktown", "Hornsby", "Bondi", "Manly", "Chatswood", "Hurstville",
    "Bankstown", "Cronulla", "Maroubra", "Randwick", "Ryde", "Strathfield",
    "Burwood", "Campbelltown",
]


def nsw_address():
    return (
        f"{random.randint(1, 350)} {fake.street_name()}, "
        f"{random.choice(NSW_SUBURBS)} NSW {random.randint(2000, 2799)}"
    )


def generate_citizen():
    return {
        "_id": fake.uuid4(),
        "full_name": fake.name(),
        "national_id": fake.bothify(text="???######").upper(),
        "dob": fake.date_of_birth(minimum_age=18, maximum_age=85).isoformat(),
        "address": nsw_address(),
        "email": fake.email(),
        "phone": fake.phone_number(),
        "registered_at": fake.date_time_between(start_date="-10y", end_date="-1y"),
    }


# ---------------------------------------------------------------------------
# Case generators — five totally different schemas under one collection
# ---------------------------------------------------------------------------

def gen_business_permit(citizen_id: str) -> dict:
    return {
        "case_type": "business_permit",
        "citizen_id": citizen_id,
        "business_name": fake.company(),
        "business_type": random.choice(
            ["cafe", "retail", "consulting", "construction", "tech"]
        ),
        "abn": fake.bothify(text="## ### ### ###"),
        "premises_address": nsw_address(),
        "expected_employees": random.randint(1, 50),
        "annual_revenue_estimate_aud": random.randint(50_000, 5_000_000),
        "operating_hours": (
            f"{random.randint(6, 9):02d}:00 - {random.randint(17, 22):02d}:00"
        ),
        "status": random.choice(
            ["pending", "approved", "approved", "approved", "rejected"]
        ),
        "fee_paid_aud": random.choice([0, 200, 500, 1000]),
        "submitted_documents": [
            "company_registration",
            "lease_agreement",
            "insurance_certificate",
        ],
        "created_at": fake.date_time_between(start_date="-3y", end_date="now"),
    }


def gen_building_permit(citizen_id: str) -> dict:
    return {
        "case_type": "building_permit",
        "citizen_id": citizen_id,
        "property_address": nsw_address(),
        "construction_type": random.choice(
            ["new_dwelling", "extension", "renovation", "demolition", "outbuilding"]
        ),
        "estimated_cost_aud": random.randint(20_000, 1_500_000),
        "estimated_duration_weeks": random.randint(2, 52),
        "land_area_sqm": random.randint(200, 2_000),
        "floor_area_sqm": random.randint(50, 500),
        "storeys": random.randint(1, 4),
        "structural_engineer": fake.name(),
        "architect": fake.name(),
        "status": random.choice(
            ["pending", "approved", "approved", "under_review", "rejected"]
        ),
        "inspection_dates": [
            fake.date_between(start_date="-1y", end_date="today").isoformat()
            for _ in range(random.randint(0, 4))
        ],
        "created_at": fake.date_time_between(start_date="-2y", end_date="now"),
    }


def gen_complaint(citizen_id: str) -> dict:
    return {
        "case_type": "complaint",
        "citizen_id": citizen_id,
        "complaint_category": random.choice(
            ["noise", "rubbish", "graffiti", "tree_dispute", "parking", "footpath"]
        ),
        "incident_address": nsw_address(),
        "incident_description": fake.sentence(nb_words=20),
        "incident_time": fake.date_time_between(
            start_date="-1y", end_date="now"
        ).isoformat(),
        "severity": random.choice(["low", "medium", "medium", "high"]),
        "anonymous": random.choice([True, False, False]),
        "photos_attached": random.randint(0, 5),
        "status": random.choice(
            ["open", "investigating", "resolved", "resolved", "closed"]
        ),
        "officer_assigned": fake.name() if random.random() > 0.3 else None,
        "resolution_notes": fake.paragraph() if random.random() > 0.4 else None,
        "created_at": fake.date_time_between(start_date="-1y", end_date="now"),
    }


def gen_benefit(citizen_id: str) -> dict:
    return {
        "case_type": "benefit_application",
        "citizen_id": citizen_id,
        "benefit_program": random.choice(
            [
                "disability_support",
                "carer_payment",
                "youth_allowance",
                "rent_assistance",
                "newstart",
            ]
        ),
        "tax_file_number": fake.bothify(text="### ### ###"),  # encrypted before write
        "household_size": random.randint(1, 6),
        "household_income_aud": random.randint(0, 90_000),
        "assets_declared_aud": random.randint(0, 200_000),
        "medical_assessment_required": random.choice([True, False]),
        "medical_practitioner": fake.name() if random.random() > 0.5 else None,
        "supporting_documents": random.sample(
            [
                "medicare_card",
                "bank_statements",
                "medical_certificate",
                "lease_agreement",
                "tax_return",
            ],
            k=random.randint(2, 5),
        ),
        "status": random.choice(
            [
                "pending_assessment",
                "approved",
                "approved",
                "denied",
                "additional_info_required",
            ]
        ),
        "monthly_payment_aud": random.choice([0, 0, 380, 520, 780, 1100]),
        "created_at": fake.date_time_between(start_date="-2y", end_date="now"),
    }


def gen_marriage(citizen_id: str) -> dict:
    return {
        "case_type": "marriage_registration",
        "citizen_id": citizen_id,
        "partner_full_name": fake.name(),
        "partner_national_id": fake.bothify(text="???######").upper(),  # encrypted
        "ceremony_date": fake.date_between(
            start_date="-5y", end_date="+1y"
        ).isoformat(),
        "ceremony_location": f"{random.choice(NSW_SUBURBS)}, NSW",
        "celebrant_name": fake.name(),
        "celebrant_registration_id": fake.bothify(text="C-####"),
        "witnesses": [fake.name(), fake.name()],
        "notice_of_intent_filed": fake.date_between(
            start_date="-6y", end_date="-1m"
        ).isoformat(),
        "status": random.choice(
            ["registered", "registered", "pending", "registered"]
        ),
        "certificate_issued": random.choice([True, False]),
        "created_at": fake.date_time_between(start_date="-5y", end_date="now"),
    }


GENERATORS = [
    gen_business_permit,
    gen_building_permit,
    gen_complaint,
    gen_benefit,
    gen_marriage,
]


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def main():
    db = get_db()

    print(f"Clearing existing data...")
    db.cases.delete_many({})
    db.citizens.delete_many({})

    print(f"Generating {NUM_CITIZENS:,} citizens...")
    citizens = [generate_citizen() for _ in range(NUM_CITIZENS)]

    # Batch insert in chunks to keep memory and per-op latency reasonable
    for batch in chunked([encrypt_pii(c) for c in citizens], 1_000):
        db.citizens.insert_many(batch, ordered=False)
    print(f"  inserted {db.citizens.estimated_document_count():,} citizens")

    print(f"\nGenerating {NUM_CASES:,} cases across 5 case types...")
    cases = []
    for _ in range(NUM_CASES):
        citizen = random.choice(citizens)
        gen = random.choice(GENERATORS)
        cases.append(gen(citizen["_id"]))

    # Demo citizen: first in the generated list. Plant one of every case type
    # so the citizen-view timeline shows all five emojis no matter what the
    # random distribution did.
    demo_citizen = citizens[0]
    print(
        f"\n>>> Demo citizen: {demo_citizen['full_name']} "
        f"(_id={demo_citizen['_id']}) <<<"
    )
    print(f"    Planting one of every case type for the demo citizen.")
    for gen in GENERATORS:
        cases.append(gen(demo_citizen["_id"]))

    encrypted = [encrypt_pii(c) for c in cases]
    for batch in chunked(encrypted, 1_000):
        db.cases.insert_many(batch, ordered=False)

    total = db.cases.estimated_document_count()
    print(f"\n  inserted {total:,} cases.")

    # Tiny distribution report so the run feels final
    print("\nCase-type distribution:")
    for row in db.cases.aggregate(
        [
            {"$group": {"_id": "$case_type", "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]
    ):
        print(f"  {row['_id']:<24} {row['n']:>6,}")

    print(f"\nDone. Demo citizen id: {demo_citizen['_id']}")


if __name__ == "__main__":
    main()
