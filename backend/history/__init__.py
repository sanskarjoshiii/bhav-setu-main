"""Phase 15 — farmer history, as documents.

`store.py` is the only module that writes to MongoDB. Everything else calls
`record_*` and does not know where history lives.
"""

from history.store import (  # noqa: F401
    farmer_document,
    list_farmers,
    record_event,
    record_irrigation,
    record_login,
    record_pool,
    record_recommendation,
    record_sale,
    record_signup,
    snapshot_from_db,
    upsert_farmer,
)
