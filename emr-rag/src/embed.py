import os

import voyageai
from dotenv import load_dotenv

load_dotenv()

vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    result = vo.embed(texts, model="voyage-3", input_type=input_type)
    return result.embeddings


def visit_to_text(visit: dict) -> str:
    """Format a visit doc as the embedding input.

    Includes the specialty and date so semantic search picks up specialty
    queries and time-based phrasings without needing extra filter logic.
    """
    visit_date = visit.get("visit_date")
    date_str = visit_date.date().isoformat() if hasattr(visit_date, "date") else str(visit_date)
    specialty = visit.get("specialty", "unknown")
    cc = visit.get("chief_complaint", "")
    body = visit.get("body", "")
    return f"{specialty} visit on {date_str}\nChief complaint: {cc}\n\n{body}"
