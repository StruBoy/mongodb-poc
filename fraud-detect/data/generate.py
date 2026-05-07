"""Generate the fraud-detect corpus and normal transaction stream.

Plan 01 phase 2.4. Produces:
  - fraud_examples (~200 docs): 5 archetypes x 40 examples, each embedded with voyage-3.
    These are the "known fraud" corpus that incoming transactions are scored against.
  - transactions (~10000 docs): unlabeled normal transactions. No embedding — these
    are queried, not part of the vector corpus.

Run from the project root (fraud-detect/):
    python -m data.generate
"""
import random
from datetime import datetime, timedelta

from faker import Faker

from src.db import get_db
from src.embed import embed_texts, transaction_to_text

fake = Faker()
random.seed(42)

MERCHANT_CATEGORIES = [
    "groceries", "fuel", "restaurant", "online_retail",
    "electronics", "travel", "entertainment", "atm_withdrawal",
    "utilities", "subscription",
]

# Five fraud archetypes with descriptive prototypes
FRAUD_ARCHETYPES = {
    "card_testing": {
        "description": "Small-amount transactions in rapid succession to test stolen card validity",
        "amount_range": (1, 10),
        "channel": "online",
        "card_present": False,
        "examples": 400,
    },
    "foreign_cnp": {
        "description": "Card-not-present transaction from a country far from cardholder home",
        "amount_range": (100, 800),
        "channel": "online",
        "card_present": False,
        "examples": 400,
    },
    "account_takeover": {
        "description": "Large purchase at unusual merchant category at unusual hour",
        "amount_range": (500, 3000),
        "channel": "online",
        "card_present": False,
        "examples": 400,
    },
}


def generate_normal_transaction():
    return {
        "tx_id": fake.uuid4(),
        "ts": fake.date_time_between(start_date="-30d", end_date="now"),
        "amount": round(random.lognormvariate(3.5, 0.8), 2),
        "currency": "AUD",
        "merchant_name": fake.company(),
        "merchant_category": random.choice(MERCHANT_CATEGORIES),
        "country": "AU",
        "channel": random.choice(["in_person", "online", "in_person", "in_person"]),
        "card_present": random.choice([True, True, True, False]),
        "hour_of_day": random.choices(range(24), weights=[1] * 7 + [3] * 15 + [1] * 2)[0],
        "distance_from_home_km": round(random.expovariate(1 / 15), 1),
        "label": "normal",
    }


def generate_fraud_example(archetype_name, archetype):
    is_foreign = "foreign" in archetype_name
    is_off_hours = "account" in archetype_name or "merchant" in archetype_name
    return {
        "tx_id": fake.uuid4(),
        "ts": fake.date_time_between(start_date="-90d", end_date="-1d"),
        "amount": round(random.uniform(*archetype["amount_range"]), 2),
        "currency": "AUD",
        "merchant_name": fake.company(),
        "merchant_category": random.choice(MERCHANT_CATEGORIES),
        "country": random.choice(["NG", "RU", "BR"]) if is_foreign else "AU",
        "channel": archetype["channel"],
        "card_present": archetype["card_present"],
        "hour_of_day": random.choice([2, 3, 4, 23]) if is_off_hours else random.randint(0, 23),
        "distance_from_home_km": (
            round(random.uniform(2000, 15000), 1) if is_foreign else round(random.expovariate(1 / 15), 1)
        ),
        "archetype": archetype_name,
        "archetype_description": archetype["description"],
        "label": "fraud",
    }


def main():
    db = get_db()
    db.transactions.delete_many({})
    db.fraud_examples.delete_many({})

    # --- Fraud examples ---
    fraud_examples = []
    for name, arc in FRAUD_ARCHETYPES.items():
        for _ in range(arc["examples"]):
            fraud_examples.append(generate_fraud_example(name, arc))

    print(f"Embedding {len(fraud_examples)} fraud examples in batches of 50...")
    for i in range(0, len(fraud_examples), 50):
        batch = fraud_examples[i:i + 50]
        texts = [transaction_to_text(tx) for tx in batch]
        embeddings = embed_texts(texts, input_type="document")
        for tx, emb in zip(batch, embeddings):
            tx["embedding"] = emb
        print(f"  ...{min(i + 50, len(fraud_examples))}/{len(fraud_examples)}")

    db.fraud_examples.insert_many(fraud_examples)
    print(f"Inserted {len(fraud_examples)} fraud examples into fraud_examples.")

    # --- Normal transactions ---
    target = 100000
    print(f"Generating {target} normal transactions...")
    normal = [generate_normal_transaction() for _ in range(target)]
    print(f"Inserting {len(normal)} normal transactions in batches...")
    for i in range(0, len(normal), 10000):
        db.transactions.insert_many(normal[i:i + 10000])
        print(f"  ...{min(i + 10000, len(normal))}/{len(normal)}")
    print(f"Inserted {len(normal)} normal transactions into transactions.")


if __name__ == "__main__":
    main()
