import json
import os
import random

from anthropic import Anthropic
from dotenv import load_dotenv
from faker import Faker

from src.db import get_db
from src.embed import embed_texts, product_to_text

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
fake = Faker()
random.seed(42)

CATEGORIES = {
    "footwear": [
        ("trail running shoe", 80, 280),
        ("marathon racing shoe", 150, 350),
        ("casual sneaker", 60, 180),
        ("hiking boot", 120, 350),
        ("dress shoe", 100, 400),
    ],
    "electronics": [
        ("noise-cancelling headphones", 100, 600),
        ("wireless earbuds", 50, 400),
        ("smart watch", 150, 800),
        ("portable speaker", 40, 300),
        ("e-reader", 120, 400),
    ],
    "home": [
        ("espresso machine", 200, 1500),
        ("air purifier", 100, 800),
        ("kitchen knife set", 80, 500),
        ("memory foam pillow", 30, 150),
        ("standing desk", 250, 1200),
    ],
    "sports": [
        ("yoga mat", 25, 150),
        ("resistance band set", 20, 80),
        ("road bike helmet", 60, 350),
        ("fitness tracker", 80, 400),
        ("compression tights", 40, 180),
    ],
}

BRANDS = {
    "footwear": ["TrailMaster", "PaceForge", "StridePro", "TerraVibe", "RunCadence"],
    "electronics": ["SoundCrest", "NovaWave", "AudioPath", "Lumiq", "EchoForm"],
    "home": ["KitchenAxis", "HomeNest", "PureLine", "VeritasGoods", "Quietude"],
    "sports": ["FlexCore", "AthleteOne", "VeloPath", "PulseGear", "MotionWright"],
}


def generate_description_batch(items: list[tuple]) -> list[str]:
    """Use Claude to generate realistic product descriptions in one batch call."""
    prompt = (
        "Generate a realistic product description (40-60 words) for each of these products. "
        "Return as a JSON array of strings, one description per product, in the same order. "
        "Descriptions should be evocative but not use the exact product type name. "
        "Return ONLY the JSON array, no other text.\n\n"
    )
    for i, (title, brand, category) in enumerate(items, 1):
        prompt += f"{i}. {brand} {title} ({category})\n"

    response = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_catalog(target_count: int = 2500):
    items = []
    per_subtype = target_count // (len(CATEGORIES) * 5)
    for category, products in CATEGORIES.items():
        for product_type, min_price, max_price in products:
            for _ in range(per_subtype):
                brand = random.choice(BRANDS[category])
                model = fake.bothify(text="??-####").upper()
                items.append({
                    "title": f"{brand} {model}",
                    "_product_type": product_type,
                    "brand": brand,
                    "category": category,
                    "price": round(random.uniform(min_price, max_price), 2),
                    "rating": round(random.uniform(3.5, 5.0), 1),
                    "review_count": random.randint(5, 2000),
                })
    return items


def main():
    db = get_db()
    db.products.delete_many({})

    print("Generating catalog skeleton...")
    items = generate_catalog(target_count=2500)
    print(f"Generated {len(items)} product records.")

    print("Generating descriptions via Claude...")
    for i in range(0, len(items), 25):
        batch = items[i:i + 25]
        tuples = [(it["_product_type"], it["brand"], it["category"]) for it in batch]
        try:
            descriptions = generate_description_batch(tuples)
            for it, desc in zip(batch, descriptions):
                it["description"] = desc
        except Exception as e:
            print(f"  Batch {i} failed: {e}; using fallback.")
            for it in batch:
                it["description"] = f"Premium {it['_product_type']} from {it['brand']}."
        for it in batch:
            it.pop("_product_type", None)
            it.setdefault("description", f"Premium product from {it['brand']}.")
        if i % 100 == 0:
            print(f"  ...{i}/{len(items)}")

    print("Embedding descriptions via Voyage AI...")
    for i in range(0, len(items), 50):
        batch = items[i:i + 50]
        texts = [product_to_text(it) for it in batch]
        embeddings = embed_texts(texts, input_type="document")
        for it, emb in zip(batch, embeddings):
            it["embedding"] = emb

    db.products.insert_many(items)
    print(f"Inserted {len(items)} products.")


if __name__ == "__main__":
    main()
