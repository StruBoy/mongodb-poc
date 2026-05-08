import json
import os
import random
import re

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

# Per-category words Claude must avoid in descriptions. The footwear list strips
# the lexical anchors a keyword search for an intent query like "footwear for
# racing 26.2 miles" would latch onto — forcing semantic search to carry that
# query in the demo. Other categories are left unconstrained so the
# "comfortable for long flights" demo still has natural keyword anchors.
BANNED_WORDS = {
    "footwear": [
        "marathon", "race", "racing", "racer",
        "mile", "miles", "26.2", "10K", "5K",
        "long-distance", "long distance",
        "endurance", "ultra", "ultramarathon",
    ],
}


def _banned_words_for(items: list[tuple]) -> list[str]:
    """Union of banned words across the categories present in the batch."""
    banned = set()
    for _product_type, category in items:
        banned.update(BANNED_WORDS.get(category, []))
    return sorted(banned)


# All brand names across all categories. Brand is NOT passed to Claude (so
# descriptions stay brand-agnostic and the semantic-fail brand demo lands), but
# we sanitize the response too as a belt-and-braces measure in case Claude
# coincidentally invents a real brand string.
ALL_BRANDS = {b for blist in BRANDS.values() for b in blist}


def _strip_brand_mentions(text: str) -> str:
    """Remove any brand-name occurrences from a description, case-insensitive."""
    for brand in ALL_BRANDS:
        text = re.sub(re.escape(brand), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _scrub_banned_words(text: str, banned: list[str]) -> str:
    """Strip whole-word occurrences of any banned term from `text`.

    Defense in depth: even with the strict prompt rule, Claude leaks ~2% of the
    time. We strip on word boundaries so substrings like 'mileage' or
    'gracefully' (which BM25 tokenises separately and won't match a query for
    'mile' / 'race') are left intact, while literal matches that would defeat
    the keyword-fail demo are removed.
    """
    if not banned:
        return text
    pattern = r"\b(" + "|".join(re.escape(b) for b in banned) + r")\b"
    text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.])", r"\1", text)
    text = re.sub(r"\.\.+", ".", text)
    return re.sub(r"\s+", " ", text).strip()


def generate_description_batch(items: list[tuple]) -> list[str]:
    """Use Claude to generate realistic product descriptions in one batch call.

    Brand is intentionally NOT included in the prompt — descriptions must stay
    brand-agnostic so that semantic search cannot resolve brand-only queries.
    """
    prompt = (
        "Generate a realistic product description (40-60 words) for each of these products. "
        "Return as a JSON array of strings, one description per product, in the same order. "
        "Descriptions should be evocative but not use the exact product type name. "
        "Do NOT invent or include any brand name in the description. "
    )
    banned = _banned_words_for(items)
    if banned:
        prompt += (
            "Strict rule: do NOT use any of these words or phrases (case-insensitive) "
            f"anywhere in the description: {', '.join(banned)}. "
            "Use roundabout language about pace, cushioning, tempo, propulsion, road feel, etc. "
        )
    prompt += "Return ONLY the JSON array, no other text.\n\n"
    for i, (product_type, category) in enumerate(items, 1):
        prompt += f"{i}. {product_type} ({category})\n"

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
    descriptions = json.loads(text.strip())
    banned = _banned_words_for(items)
    return [_scrub_banned_words(_strip_brand_mentions(d), banned) for d in descriptions]


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
        tuples = [(it["_product_type"], it["category"]) for it in batch]
        try:
            descriptions = generate_description_batch(tuples)
            for it, desc in zip(batch, descriptions):
                it["description"] = desc
        except Exception as e:
            print(f"  Batch {i} failed: {e}; using fallback.")
            for it in batch:
                # Fallback skips _product_type because it can contain banned words
                # (e.g., "marathon racing shoe") that would defeat the keyword-fail demo.
                it["description"] = f"Premium product from {it['brand']}."
        for it in batch:
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
