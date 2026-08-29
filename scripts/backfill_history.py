"""Phase 15 — rebuild the MongoDB history from Postgres.

    python scripts/backfill_history.py            # add what is missing
    python scripts/backfill_history.py --reset    # wipe the documents first

Postgres is the source of truth; the documents are a mirror. So this script
exists for three situations, and it is safe to run in all of them:

  * **First run.** Farmers registered before Phase 15 have no documents. This
    gives every one of them a profile and a timeline built from the rows they
    already generated — so a demo does not start with an empty collection.
  * **Mongo was down.** Writes are best-effort by design, so an outage leaves
    gaps. Re-running fills them.
  * **The document shape changed.** `--reset` rebuilds from scratch.

It reads farmers, their recommendations (via lots) and their sale reports. Pool
membership is not reconstructed: `pool_members` records a name and a village,
not a farmer id, so attributing a historical pool to an account would be a
guess. New pool actions are recorded live.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

import _bootstrap  # noqa: F401  (sys.path side effect)

from sqlalchemy import text

from core import logging as log
from core.db import get_conn
from core.mongo import collection, is_available
from history import store


def _utc(value: Any) -> datetime:
    """Postgres timestamps may be naive; Mongo documents are always aware."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _farmers(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text("""
        SELECT f.id, f.name, f.phone_e164, f.email, f.village, f.language,
               f.risk_profile, f.lat, f.lon, f.created_at,
               m.name AS home_mandi, m.district
        FROM farmers f LEFT JOIN mandis m ON m.id = f.home_mandi_id
        ORDER BY f.id
    """)).mappings()]


def _block(row: dict[str, Any]) -> dict[str, Any]:
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


def _recommendations(conn, farmer_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text("""
        SELECT r.id, r.issued_at, r.action, r.expected_gain, r.confidence,
               c.name AS crop, l.quantity_qtl, m.name AS mandi
        FROM recommendations r
        JOIN lots l        ON l.id = r.lot_id
        JOIN commodities c ON c.id = l.commodity_id
        LEFT JOIN mandis m ON m.id = r.target_mandi_id
        WHERE l.farmer_id = :id
        ORDER BY r.issued_at
    """), {"id": farmer_id}).mappings()]


def _sales(conn, farmer_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text("""
        SELECT s.id, s.sale_date, s.quantity_qtl, s.gross_price_qtl,
               s.net_received_qtl, s.followed_advice, m.name AS mandi
        FROM sale_reports s
        LEFT JOIN mandis m ON m.id = s.mandi_id
        WHERE s.farmer_id = :id
        ORDER BY s.sale_date
    """), {"id": farmer_id}).mappings()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild farmer history documents.")
    parser.add_argument("--reset", action="store_true",
                        help="delete every document before rebuilding")
    parser.add_argument("--prune", action="store_true",
                        help="also delete documents whose Postgres farmer is gone")
    args = parser.parse_args(argv)

    if not is_available():
        print("\n⛔ MongoDB is not reachable.\n"
              "   Start it with:  docker compose up -d mongo\n")
        return 2

    if args.reset:
        collection("farmers").delete_many({})
        collection("events").delete_many({})
        print("  cleared existing documents")

    farmers = events = 0
    with get_conn() as conn:
        rows = _farmers(conn)
        for row in rows:
            block = _block(row)
            store.upsert_farmer(block)
            farmers += 1

            # The account itself is the first thing that ever happened.
            registered = _utc(row["created_at"])
            if collection("events").count_documents(
                    {"farmerId": block["id"], "type": "signup"}) == 0:
                collection("events").insert_one({
                    "farmerId": block["id"], "type": "signup", "at": registered,
                    "summary": f"Registered from {block['village'] or 'an unknown village'}",
                    "farmer": store._farmer_block(block),
                    "data": {"method": "backfill"},
                })
                events += 1

            for rec in _recommendations(conn, block["id"]):
                key = {"farmerId": block["id"], "type": "recommendation",
                       "data.sourceId": int(rec["id"])}
                if collection("events").count_documents(key):
                    continue
                collection("events").insert_one({
                    "farmerId": block["id"], "type": "recommendation",
                    "at": _utc(rec["issued_at"]),
                    "summary": (f"Advised to {str(rec['action'] or '').replace('_', ' ')} "
                                f"{float(rec['quantity_qtl'] or 0):g} qtl of "
                                f"{rec['crop']} at {rec['mandi'] or 'the best market'}"),
                    "farmer": store._farmer_block(block),
                    "data": {
                        "sourceId": int(rec["id"]),
                        "crop": rec["crop"],
                        "qtyQtl": float(rec["quantity_qtl"] or 0),
                        "mandi": rec["mandi"],
                        "action": rec["action"],
                        "expectedGain": float(rec["expected_gain"] or 0),
                        "confidence": float(rec["confidence"] or 0),
                    },
                })
                events += 1

            for sale in _sales(conn, block["id"]):
                key = {"farmerId": block["id"], "type": "sale_report",
                       "data.sourceId": int(sale["id"])}
                if collection("events").count_documents(key):
                    continue
                received = float(sale["net_received_qtl"] or 0)
                quoted = float(sale["gross_price_qtl"] or 0)
                collection("events").insert_one({
                    "farmerId": block["id"], "type": "sale_report",
                    "at": _utc(sale["sale_date"]),
                    "summary": (f"Sold {float(sale['quantity_qtl'] or 0):g} qtl at "
                                f"{sale['mandi'] or 'a market'} for ₹{received:,.0f}/qtl"),
                    "farmer": store._farmer_block(block),
                    "data": {
                        "sourceId": int(sale["id"]),
                        "mandi": sale["mandi"],
                        "qtl": float(sale["quantity_qtl"] or 0),
                        "quotedPerQtl": quoted,
                        "receivedPerQtl": received,
                        "gapPerQtl": received - quoted,
                        "followedAdvice": bool(sale["followed_advice"]),
                    },
                })
                events += 1

    if args.prune:
        with get_conn() as conn:
            live = {int(r) for r in conn.execute(text("SELECT id FROM farmers")).scalars()}
        stale = [int(d["_id"]) for d in collection("farmers").find({}, {"_id": 1})
                 if int(d["_id"]) not in live]
        if stale:
            collection("farmers").delete_many({"_id": {"$in": stale}})
            collection("events").delete_many({"farmerId": {"$in": stale}})
            print(f"  pruned {len(stale)} document(s) with no Postgres row")

    # Recount the per-farmer totals from the events actually present, so a
    # re-run cannot inflate them.
    for doc in collection("farmers").find({}, {"_id": 1}):
        farmer_id = doc["_id"]
        by_type: dict[str, int] = {}
        for event in collection("events").find({"farmerId": farmer_id}, {"type": 1}):
            by_type[event["type"]] = by_type.get(event["type"], 0) + 1
        collection("farmers").update_one(
            {"_id": farmer_id},
            {"$set": {"events": sum(by_type.values()), "eventCounts": by_type}},
        )

    log.info("history_backfill_done", farmers=farmers, events=events)
    print(f"\n  {farmers} farmer document(s), {events} new event(s)")
    print(f"  totals now: {store.counts()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
