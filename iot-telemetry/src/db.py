"""MongoDB connection helpers for the iot-telemetry PoC.

Sync client is used by the dashboard, scripts, and the analytics module.
Async (motor) client is used by data/stream_telemetry.py for batched writes
that hit ~1000 events/sec without saturating a single connection.

Both clients use certifi for TLS — required on macOS Python builds that don't
ship with system root CAs.
"""
import os

import certifi
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

load_dotenv()

DB_NAME = "telco_demo"

_client = None
_async_client = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(os.environ["MONGODB_URI"], tlsCAFile=certifi.where())
    return _client


def get_db():
    return get_client()[DB_NAME]


def get_async_client() -> AsyncIOMotorClient:
    global _async_client
    if _async_client is None:
        _async_client = AsyncIOMotorClient(
            os.environ["MONGODB_URI"],
            tlsCAFile=certifi.where(),
            maxPoolSize=20,
        )
    return _async_client


def get_async_db():
    return get_async_client()[DB_NAME]
