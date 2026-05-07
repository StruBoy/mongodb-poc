import os

import voyageai
from dotenv import load_dotenv

load_dotenv()

vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    result = vo.embed(texts, model="voyage-3", input_type=input_type)
    return result.embeddings


def doc_to_text(doc: dict) -> str:
    return f"{doc['title']}\n\n{doc['body']}"
