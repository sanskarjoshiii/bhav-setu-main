"""GET/POST /sale-reports, GET /transparency — what farmers actually got paid.

The gap between the price a mandi quotes and the price a farmer receives is the
thing nobody publishes. Every report here is farmer-submitted, so the honest
framing is "this is what people told us", not "this is audited truth" — the
`verification` field carries that distinction to the UI.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from api import deps
from api.schemas import SaleReport, SaleReportRequest, TransparencyScore
from core.db import get_conn

router = APIRouter(tags=["transparency"])

#: Below this many reports a median gap is one person's bad day, not a signal.
MIN_REPORTS_FOR_SCORE: int = 3


@router.get("/sale-reports", response_model=list[SaleReport])
def list_sale_reports(limit: int = Query(60, ge=1, le=500)) -> list[SaleReport]:
    with get_conn() as conn:
        rows = conn.execute(text("""
            SELECT s.id, m.name AS mandi, s.sale_date, s.quantity_qtl,
                   s.gross_price_qtl, s.net_received_qtl, s.followed_advice,
                   s.verification,
                   coalesce(f.name, 'Anonymous') AS farmer,
                   coalesce(f.village, '')       AS village
            FROM sale_reports s
            JOIN mandis m ON m.id = s.mandi_id
            LEFT JOIN farmers f ON f.id = s.farmer_id
            WHERE s.gross_price_qtl > 0
            ORDER BY s.sale_date DESC, s.id DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()

    out: list[SaleReport] = []
    for r in rows:
        quoted = float(r["gross_price_qtl"] or 0.0)
        received = float(r["net_received_qtl"] or 0.0)
        gap = ((received - quoted) / quoted * 100.0) if quoted else 0.0
        out.append(SaleReport(
            id=str(r["id"]),
            farmer=str(r["farmer"]),
            village=str(r["village"] or ""),
            mandi=str(r["mandi"]),
            date=str(r["sale_date"]),
            qtl=float(r["quantity_qtl"] or 0.0),
            quoted_per_qtl=round(quoted, 2),
            received_per_qtl=round(received, 2),
            gap_pct=round(gap, 2),
            followed_advice=bool(r["followed_advice"]),
            verification=str(r["verification"] or "self_reported"),
        ))
    return out


@router.post("/sale-reports", response_model=SaleReport, status_code=201)
def create_sale_report(request: SaleReportRequest) -> SaleReport:
    mandi_id, mandi_name, _ = deps.resolve_mandi(request.mandi)
    deps.resolve_commodity(request.crop)   # validate the crop exists

    with get_conn() as conn:
        new_id = conn.execute(text("""
            INSERT INTO sale_reports
                (farmer_id, mandi_id, sale_date, quantity_qtl, channel,
                 gross_price_qtl, net_received_qtl, followed_advice,
                 verification, source)
            VALUES (:farmer_id, :mandi_id, :sale_date, :qty, 'mandi',
                    :quoted, :received, :followed, :verification, 'web')
            RETURNING id
        """), {
            "farmer_id": _farmer_id(conn, request.farmer, request.village),
            "mandi_id": mandi_id,
            "sale_date": date.today(), "qty": request.qtl,
            "quoted": request.quoted_per_qtl, "received": request.received_per_qtl,
            "followed": request.followed_advice, "verification": request.verification,
        }).scalar_one()

    gap = (request.received_per_qtl - request.quoted_per_qtl) / request.quoted_per_qtl * 100.0
    return SaleReport(
        id=str(new_id), farmer=request.farmer, village=request.village,
        mandi=mandi_name, date=str(date.today()), qtl=request.qtl,
        quoted_per_qtl=request.quoted_per_qtl, received_per_qtl=request.received_per_qtl,
        gap_pct=round(gap, 2), followed_advice=request.followed_advice,
        verification=request.verification,
    )


@router.get("/transparency", response_model=list[TransparencyScore])
def transparency() -> list[TransparencyScore]:
    """Per-market score from the median quoted-versus-received gap."""
    with get_conn() as conn:
        rows = conn.execute(text("""
            SELECT m.name AS mandi, s.gross_price_qtl, s.net_received_qtl, s.sale_date
            FROM sale_reports s JOIN mandis m ON m.id = s.mandi_id
            WHERE s.gross_price_qtl > 0 AND s.net_received_qtl IS NOT NULL
        """)).mappings().all()

    by_mandi: dict[str, list[tuple[date, float]]] = {}
    for r in rows:
        gap = (float(r["net_received_qtl"]) - float(r["gross_price_qtl"])) / float(r["gross_price_qtl"]) * 100.0
        by_mandi.setdefault(str(r["mandi"]), []).append((r["sale_date"], gap))

    out: list[TransparencyScore] = []
    for mandi, entries in by_mandi.items():
        if len(entries) < MIN_REPORTS_FOR_SCORE:
            continue
        gaps = [g for _, g in entries]
        median = statistics.median(gaps)
        # 0% gap scores 100; every 1% shortfall costs 5 points, floored at 0.
        score = max(0.0, min(100.0, 100.0 + median * 5.0))

        entries.sort(key=lambda e: e[0])
        half = len(entries) // 2
        if half:
            recent = statistics.median([g for _, g in entries[half:]])
            older = statistics.median([g for _, g in entries[:half]])
            trend = "up" if recent > older + 0.5 else "down" if recent < older - 0.5 else "flat"
        else:
            trend = "flat"

        out.append(TransparencyScore(
            mandi=mandi, reports=len(entries), median_gap_pct=round(median, 2),
            score=round(score, 1), trend=trend,
        ))
    return sorted(out, key=lambda s: -s.score)


def _farmer_id(conn, name: str, village: str) -> int | None:
    """Find or create a farmer row for a web-submitted report.

    Web reports have no phone number, so we key on name+village and synthesise a
    placeholder `phone_e164` (the column is UNIQUE NOT NULL). Real identities
    arrive with Phase 10's OTP login; until then this keeps the reports
    attributable without pretending they are verified.
    """
    if not name or name.lower() == "anonymous":
        return None
    handle = f"web:{name.strip().lower()}|{village.strip().lower()}"
    existing = conn.execute(
        text("SELECT id FROM farmers WHERE phone_e164 = :h"), {"h": handle}
    ).scalar()
    if existing:
        return int(existing)
    return int(conn.execute(text("""
        INSERT INTO farmers (phone_e164, name, village) VALUES (:h, :n, :v)
        RETURNING id
    """), {"h": handle, "n": name, "v": village}).scalar_one())
