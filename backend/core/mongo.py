"""Phase 15 — the MongoDB connection behind farmer history.

    from core.mongo import collection, is_available
    collection("events").insert_one({...})

**Why a second database.** Postgres stays the transactional store: it owns the
farmer row, the lot, the recommendation, and every foreign key between them.
What it is not good at is answering "show me everything this farmer has ever
done, in one readable object" — that needs four joins and still comes back as
rows. MongoDB holds the same history denormalised: one profile document per
farmer, one self-contained document per event. Open Compass, click a farmer,
and the whole story is on screen.

Nothing here is the source of truth. If Mongo and Postgres disagree, Postgres
wins and `scripts/backfill_history.py` rebuilds the documents.

**A history write must never take down a request.** A farmer signing in cares
that he is signed in, not that the analytics mirror was reachable. Writes are
therefore best-effort and log loudly on failure; reads say plainly that the
store is unavailable rather than pretending a farmer has no history.
"""

from __future__ import annotations

import threading
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError

from core import logging as log
from core.config import settings

#: How long to wait for Mongo before deciding it is not there. Short on purpose:
#: this sits in the request path, and a demo laptop with Docker stopped should
#: get a fast, clear failure rather than a thirty-second hang.
TIMEOUT_MS: int = 1500

_client: MongoClient | None = None

#: Reentrant on purpose. `_ensure_indexes()` holds this and then calls
#: `database()` -> `client()`, which takes it again; a plain Lock deadlocks on
#: the very first Mongo access in a process, hanging the request forever.
_lock = threading.RLock()
_indexed = False


def client() -> MongoClient:
    """The shared client. Lazy, so importing this module never opens a socket."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = MongoClient(
                    settings.env.mongodb_url,
                    serverSelectionTimeoutMS=TIMEOUT_MS,
                    connectTimeoutMS=TIMEOUT_MS,
                    tz_aware=True,
                )
    return _client


def database() -> Database:
    return client()[settings.env.mongodb_db]


def collection(name: str) -> Collection:
    _ensure_indexes()
    return database()[name]


def is_available() -> bool:
    """True if Mongo answers a ping. Used by /health and by the write path."""
    try:
        client().admin.command("ping")
        return True
    except PyMongoError:
        return False


def _ensure_indexes() -> None:
    """Create the indexes once per process. Safe to call on every access."""
    global _indexed
    if _indexed:
        return
    with _lock:
        if _indexed:
            return
        try:
            db = database()
            # One document per farmer, keyed by the Postgres id.
            db["farmers"].create_index([("phone", ASCENDING)])
            db["farmers"].create_index([("email", ASCENDING)])
            db["farmers"].create_index([("district", ASCENDING)])
            # Events: the common query is "this farmer, newest first".
            db["events"].create_index([("farmerId", ASCENDING), ("at", DESCENDING)])
            db["events"].create_index([("type", ASCENDING), ("at", DESCENDING)])
            db["events"].create_index([("at", DESCENDING)])
            _indexed = True
        except PyMongoError as exc:                            # noqa: BLE001
            # Not fatal — the collections still work unindexed, and retrying on
            # the next call costs nothing.
            log.warn("mongo_index_failed", error=str(exc))


def safe_write(operation: str, fn: Any) -> bool:
    """Run a write, swallowing connection errors but never silently.

    Returns True if it landed. The caller carries on either way — see the
    module docstring on why a history write must not fail a farmer's request.
    """
    try:
        fn()
        return True
    except PyMongoError as exc:                                # noqa: BLE001
        log.warn("mongo_write_failed", operation=operation, error=str(exc))
        return False


def reset_client() -> None:
    """Drop the cached client. Tests use this after changing the URL."""
    global _client, _indexed
    with _lock:
        if _client is not None:
            _client.close()
        _client = None
        _indexed = False
