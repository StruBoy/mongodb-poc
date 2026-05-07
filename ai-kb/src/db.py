import os

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

_client = None


def get_client():
    global _client
    if _client is None:
        _client = MongoClient(os.environ["MONGODB_URI"], tlsCAFile=certifi.where())
    return _client


def get_db():
    return get_client()["kb_demo"]
