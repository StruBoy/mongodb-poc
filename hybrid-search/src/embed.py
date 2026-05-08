import os

import voyageai
from dotenv import load_dotenv

load_dotenv()

vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    result = vo.embed(texts, model="voyage-3", input_type=input_type)
    return result.embeddings


def product_to_text(p: dict) -> str:
    # Brand and title (which begins with the brand) are deliberately excluded so
    # semantic search cannot resolve brand-only queries — that's the keyword
    # pipeline's job in the demo. Atlas Search still indexes title/brand/description
    # directly, so keyword retrieval is unaffected.
    return f"{p['description']} Category: {p['category']}."
