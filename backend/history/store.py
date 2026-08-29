"""Phase 15 — every farmer's history, written as documents.

Two collections, and the split matters:

  **farmers** — one document per farmer, `_id` = the Postgres `farmers.id`.
  The current profile (name, phone, email, village, district, coordinates,
  language, risk profile, home market) plus running counters. This is the
  "who" — open it and you know the person.

  **events** — one document per thing that happened, newest-first by farmer.
  Each event carries a *copy* of the farmer block at the time it happened.
  That denormalisation is deliberate: an event has to be readable on its own,
  and a farmer who moves village should not silently rewrite the location on
  advice he was given last month.

Every write goes through `core.mongo.safe_write`, so a history failure is
logged but never breaks the request that triggered it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from pymongo import DESCENDING, ReturnDocument

from core import logging as log
from core.mongo import collection, is_available, safe_write

#: The events we record. Kept as a tuple so a typo becomes a test failure
#: rather than a document nobody ever queries.
EVENT_TYPES: tuple[str, ...] = (
    "signup",
    "login",
    "profile_update",
    "recommendation",
    "sale_report",
    "pool_created",
    "pool_joined",
    "irrigation_advice",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _farmer_block(farmer: Any) -> dict[str, Any]:
    """The snapshot of a farmer embedded in the profile and in every event.

    Accepts anything with the attributes (the auth router's `Farmer` model) or
    a plain dict, so callers do not have to convert.
    """
    get = (lambda k, d=None: farmer.get(k, d)) if isinstance(farmer, dict) \
        else (lambda k, d=None: getattr(farmer, k, d))

    return {
        "farmerId": int(get("id") or 0),
        "name": str(get("name") or ""),
        "phone": str(get("phone") or ""),
        "email": str(get("email") or "") or None,
        "village": str(get("village") or ""),
        "district": str(get("district") or ""),
        "lat": get("lat"),
        "lon": get("lon"),
        "language": str(get("language") or "mr"),
        "riskProfile": str(get("risk_profile") or get("riskProfile") or "balanced"),
        "homeMandi": get("home_mandi") or get("homeMandi"),
    }


def snapshot_from_db(farmer_id: int) -> dict[str, Any] | None:
    """Build a farmer block from Postgres.

    Several endpoints (sale reports, pooling) identify a farmer by name and
    village rather than by session, so they hold an id and nothing else. This
    turns that id into the same block a session-authenticated caller passes.
    """
    from sqlalchemy import text

    from core.db import get_conn

    with get_conn() as conn:
        row = conn.execute(text("""
            SELECT f.id, f.name, f.phone_e164, f.email, f.village, f.language,
                   f.risk_profile, f.lat, f.lon,
                   m.name AS home_mandi, m.district
            FROM farmers f LEFT JOIN mandis m ON m.id = f.home_mandi_id
            WHERE f.id = :id
        """), {"id": int(farmer_id)}).mappings().first()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "name": row["name"] or "",
        "phone": row["phone_e164"] or "",
        "email": row["email"],
        "village": row["village"] or "",
        "district": row["district"] or "",
        "lat": float(row["lat"]) if row["lat"] is not None else None,
        "lon": float(row["lon"]) if row["lon"] is not None else None,
        "language": row["language"] or "mr",
        "risk_profile": row["risk_profile"] or "balanced",
        "home_mandi": row["home_mandi"],
    }


# ══════════════════════════════════════════════════════════════════════════
# writing
# ══════════════════════════════════════════════════════════════════════════

def upsert_farmer(farmer: Any) -> bool:
    """Create or refresh the farmer's profile document.

    Counters are left alone — `$set` would reset them on every login.
    """
    block = _farmer_block(farmer)
    farmer_id = block["farmerId"]
    if not farmer_id:
        return False

    def write() -> None:
        collection("farmers").update_one(
            {"_id": farmer_id},
            {
                "$set": {**block, "updatedAt": _now()},
                "$setOnInsert": {"createdAt": _now(), "eventCounts": {}, "events": 0},
            },
            upsert=True,
        )

    return safe_write("upsert_farmer", write)


def record_event(farmer: Any, event_type: str, payload: dict[str, Any] | None = None,
                 summary: str = "") -> bool:
    """Append one event, and bump the farmer's counters.

    `summary` is a human sentence — it is what makes the collection readable to
    someone scrolling it rather than querying it.
    """
    if event_type not in EVENT_TYPES:
        log.warn("history_unknown_event", type=event_type)

    block = _farmer_block(farmer)
    farmer_id = block["farmerId"]
    if not farmer_id:
        return False

    document = {
        "farmerId": farmer_id,
        "type": event_type,
        "at": _now(),
        "summary": summary,
        "farmer": block,
        "data": payload or {},
    }

    def write() -> None:
        collection("events").insert_one(document)
        collection("farmers").update_one(
            {"_id": farmer_id},
            {
                "$set": {**block, "updatedAt": _now(), "lastEventAt": _now(),
                         "lastEvent": event_type},
                "$inc": {"events": 1, f"eventCounts.{event_type}": 1},
                "$setOnInsert": {"createdAt": _now()},
            },
            upsert=True,
        )

    return safe_write(f"record_{event_type}", write)


# ── the specific events, so callers never type a string ───────────────────

def record_signup(farmer: Any, method: str = "otp") -> bool:
    upsert_farmer(farmer)
    return record_event(
        farmer, "signup", {"method": method},
        summary=f"Registered from {getattr(farmer, 'village', '') or 'an unknown village'} "
                f"via {method}",
    )


def record_login(farmer: Any, method: str = "otp") -> bool:
    upsert_farmer(farmer)
    return record_event(farmer, "login", {"method": method},
                        summary=f"Signed in via {method}")


def record_recommendation(farmer: Any, *, crop: str, qty_qtl: float, mandi: str,
                          action: str, expected_gain: float = 0.0,
                          confidence: float = 0.0,
                          payload: dict[str, Any] | None = None) -> bool:
    return record_event(
        farmer, "recommendation",
        {"crop": crop, "qtyQtl": qty_qtl, "mandi": mandi, "action": action,
         "expectedGain": expected_gain, "confidence": confidence,
         **(payload or {})},
        summary=f"Advised to {action.replace('_', ' ')} {qty_qtl:g} qtl of {crop} at {mandi}",
    )


def record_sale(farmer: Any, *, crop: str, mandi: str, qtl: float,
                quoted_per_qtl: float, received_per_qtl: float,
                followed_advice: bool = False) -> bool:
    gap = received_per_qtl - quoted_per_qtl
    return record_event(
        farmer, "sale_report",
        {"crop": crop, "mandi": mandi, "qtl": qtl,
         "quotedPerQtl": quoted_per_qtl, "receivedPerQtl": received_per_qtl,
         "gapPerQtl": gap, "followedAdvice": followed_advice},
        summary=f"Sold {qtl:g} qtl of {crop} at {mandi} for ₹{received_per_qtl:,.0f}/qtl",
    )


def record_pool(farmer: Any, *, joined: bool, mandi: str, travel_date: str,
                qty_qtl: float, saving_per_qtl: float = 0.0,
                pool_id: int | None = None) -> bool:
    verb = "Joined" if joined else "Created"
    return record_event(
        farmer, "pool_joined" if joined else "pool_created",
        {"poolId": pool_id, "mandi": mandi, "travelDate": travel_date,
         "qtyQtl": qty_qtl, "savingPerQtl": saving_per_qtl},
        summary=f"{verb} a truck to {mandi} on {travel_date} with {qty_qtl:g} qtl",
    )


def record_irrigation(farmer: Any, *, crop: str, mandi: str, action: str,
                      headline: str, soil_moisture: float | None,
                      soil_status: str, deficit_mm: float,
                      confidence: str = "") -> bool:
    return record_event(
        farmer, "irrigation_advice",
        {"crop": crop, "mandi": mandi, "action": action, "headline": headline,
         "soilMoisture": soil_moisture, "soilStatus": soil_status,
         "deficitMm": deficit_mm, "confidence": confidence},
        summary=f"Irrigation advice for {crop} at {mandi}: {headline}",
    )


# ══════════════════════════════════════════════════════════════════════════
# reading — what the judges' page and the API serve
# ══════════════════════════════════════════════════════════════════════════

class HistoryUnavailable(RuntimeError):
    """Mongo is not reachable. Said plainly rather than returning an empty list."""


def _require() -> None:
    if not is_available():
        raise HistoryUnavailable(
            "the history store is not reachable — start it with `docker compose up -d mongo`"
        )


def list_farmers(limit: int = 100) -> list[dict[str, Any]]:
    """Every farmer we hold history for, busiest first."""
    _require()
    rows = collection("farmers").find({}, {"events": 1, "eventCounts": 1, "name": 1,
                                           "phone": 1, "email": 1, "village": 1,
                                           "district": 1, "language": 1,
                                           "riskProfile": 1, "homeMandi": 1,
                                           "lat": 1, "lon": 1, "createdAt": 1,
                                           "lastEventAt": 1, "lastEvent": 1})
    ordered = rows.sort([("events", DESCENDING), ("createdAt", DESCENDING)]).limit(limit)
    return [_clean(doc) for doc in ordered]


def farmer_document(farmer_id: int, event_limit: int = 200) -> dict[str, Any]:
    """One farmer, their profile, and their whole timeline."""
    _require()
    profile = collection("farmers").find_one({"_id": int(farmer_id)})
    if profile is None:
        raise HistoryUnavailable(f"no history on record for farmer {farmer_id}")
    events = (collection("events")
              .find({"farmerId": int(farmer_id)})
              .sort([("at", DESCENDING)])
              .limit(event_limit))
    document = _clean(profile)
    document["timeline"] = [_clean(e) for e in events]
    return document


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    """Mongo's `_id` and ObjectIds are not JSON. Make the document serialisable."""
    out = dict(doc)
    raw_id = out.pop("_id", None)
    if isinstance(raw_id, int):
        out.setdefault("farmerId", raw_id)
    elif raw_id is not None:
        out["eventId"] = str(raw_id)
    for key, value in list(out.items()):
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


def counts() -> dict[str, int]:
    """Totals for the health endpoint and the top of the judges' page."""
    _require()
    return {
        "farmers": collection("farmers").count_documents({}),
        "events": collection("events").count_documents({}),
    }
