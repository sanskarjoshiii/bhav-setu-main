"""GET/POST /history — the farmer's own lots and the advice he was given.

Stored server-side so his history follows him between the website and WhatsApp
rather than living in one browser's localStorage.

Phase 15 adds the document view on top:

    GET /history/farmers        every farmer we hold history for
    GET /history/farmers/{id}   one farmer, profile plus full timeline

Those two read MongoDB, where the same history is mirrored denormalised — one
readable object per farmer instead of a four-table join. See
`backend/history/store.py` for why both stores exist.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

import history as history_store
from api import deps
from api.schemas import HistoryEntry, Wire
from core.db import get_conn
from core.errors import InsufficientData
from core.mongo import is_available
from history.store import HistoryUnavailable, counts

router = APIRouter(prefix="/history", tags=["history"])


class HistoryCreate(Wire):
    farmer: str
    village: str = ""
    crop: str
    qty_qtl: float
    mandi: str
    action: str
    expected_gain: float = 0.0
    confidence: float = 0.0


def _farmer_id(conn, name: str, village: str) -> int:
    handle = f"web:{name.strip().lower()}|{village.strip().lower()}"
    existing = conn.execute(
        text("SELECT id FROM farmers WHERE phone_e164 = :h"), {"h": handle}).scalar()
    if existing:
        return int(existing)
    return int(conn.execute(text(
        "INSERT INTO farmers (phone_e164, name, village) VALUES (:h,:n,:v) RETURNING id"
    ), {"h": handle, "n": name, "v": village}).scalar_one())


@router.get("", response_model=list[HistoryEntry])
def get_history(
    farmer: str = Query(...),
    village: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
) -> list[HistoryEntry]:
    handle = f"web:{farmer.strip().lower()}|{village.strip().lower()}"
    with get_conn() as conn:
        rows = conn.execute(text("""
            SELECT r.id, c.name AS crop, l.quantity_qtl, m.name AS mandi,
                   r.action, r.issued_at, r.expected_gain, r.confidence
            FROM recommendations r
            JOIN lots l        ON l.id = r.lot_id
            JOIN commodities c ON c.id = l.commodity_id
            JOIN farmers f     ON f.id = l.farmer_id
            LEFT JOIN mandis m ON m.id = r.target_mandi_id
            WHERE f.phone_e164 = :handle
            ORDER BY r.issued_at DESC
            LIMIT :limit
        """), {"handle": handle, "limit": limit}).mappings().all()

    return [
        HistoryEntry(
            id=str(r["id"]), crop=str(r["crop"]), qty_qtl=float(r["quantity_qtl"] or 0),
            mandi=str(r["mandi"] or ""), action=str(r["action"] or ""),
            created_at=str(r["issued_at"]),
            expected_gain=float(r["expected_gain"] or 0.0),
            confidence=float(r["confidence"] or 0.0),
        )
        for r in rows
    ]


@router.post("", response_model=HistoryEntry, status_code=201)
def add_history(request: HistoryCreate) -> HistoryEntry:
    commodity_id, crop_name = deps.resolve_commodity(request.crop)
    mandi_id, mandi_name, _ = deps.resolve_mandi(request.mandi)

    with get_conn() as conn:
        farmer_id = _farmer_id(conn, request.farmer, request.village)
        lot_id = int(conn.execute(text("""
            INSERT INTO lots (farmer_id, commodity_id, quantity_qtl, remaining_qtl)
            VALUES (:f, :c, :q, :q) RETURNING id
        """), {"f": farmer_id, "c": commodity_id, "q": request.qty_qtl}).scalar_one())
        rec_id = int(conn.execute(text("""
            INSERT INTO recommendations
                (lot_id, target_mandi_id, action, expected_gain, confidence)
            VALUES (:l, :m, :a, :g, :conf) RETURNING id
        """), {"l": lot_id, "m": mandi_id, "a": request.action,
               "g": request.expected_gain, "conf": request.confidence}).scalar_one())

    return HistoryEntry(
        id=str(rec_id), crop=crop_name, qty_qtl=request.qty_qtl, mandi=mandi_name,
        action=request.action, created_at=str(date.today()),
        expected_gain=request.expected_gain, confidence=request.confidence,
    )


# ══════════════════════════════════════════════════════════════════════════
# Phase 15 — the document view, for a judge who wants to see it all
# ══════════════════════════════════════════════════════════════════════════

@router.get("/farmers")
def all_farmer_history(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Every farmer we hold history for, busiest first, with their counters."""
    try:
        return {
            "available": True,
            "totals": counts(),
            "farmers": history_store.list_farmers(limit=limit),
        }
    except HistoryUnavailable as exc:
        # A readable sentence beats an empty list that looks like "no farmers".
        raise InsufficientData(str(exc)) from exc


@router.get("/farmers/{farmer_id}")
def one_farmer_history(farmer_id: int,
                       events: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    """One farmer: who they are, and everything they have ever done here."""
    try:
        return history_store.farmer_document(farmer_id, event_limit=events)
    except HistoryUnavailable as exc:
        raise InsufficientData(str(exc)) from exc


@router.get("/store-status")
def store_status() -> dict[str, Any]:
    """Is the document store reachable, and what is in it?"""
    if not is_available():
        return {"available": False,
                "hint": "start it with `docker compose up -d mongo`"}
    return {"available": True, **counts()}
