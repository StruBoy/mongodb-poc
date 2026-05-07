import os

import voyageai
from dotenv import load_dotenv

load_dotenv()

vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    result = vo.embed(texts, model="voyage-3", input_type=input_type)
    return result.embeddings


def _amount_phrase(amount: float) -> str:
    if amount < 20:
        return "tiny amount"
    if amount < 200:
        return "modest amount"
    if amount < 1000:
        return "significant amount"
    if amount < 3000:
        return "large amount"
    return "very large amount"


def _hour_phrase(hour: int) -> str:
    if hour <= 5 or hour == 23:
        return "middle of the night"
    if hour <= 11:
        return "morning"
    if hour <= 17:
        return "afternoon"
    return "evening"


def _distance_phrase(km: float) -> str:
    if km < 30:
        return "near home"
    if km < 200:
        return "home region"
    if km < 2000:
        return "distant within country"
    return "thousands of km from home"


def _country_phrase(country: str) -> str:
    if country == "AU":
        return "domestic AU"
    return f"foreign {country}"


def _channel_phrase(channel: str, card_present: bool) -> str:
    if channel == "online" or not card_present:
        return "online card not present"
    return "in person card present"


def transaction_to_text(tx: dict) -> str:
    """Render a transaction as a tag-style risk description with raw values.

    Each tag combines a discriminative bin label with the raw numeric value. The
    bin gives voyage-3 a semantic anchor; the raw value makes each example
    slightly unique so two transactions with similar features but different
    magnitudes don't collapse to identical embeddings.
    """
    parts = [
        f"{_amount_phrase(tx['amount'])} of {tx['amount']:.0f} dollars",
        _channel_phrase(tx["channel"], tx["card_present"]),
        _country_phrase(tx["country"]),
        f"{_distance_phrase(tx['distance_from_home_km'])} {tx['distance_from_home_km']:.0f} km",
        f"{_hour_phrase(tx['hour_of_day'])} at {tx['hour_of_day']:02d}:00",
    ]
    return ". ".join(parts) + "."
