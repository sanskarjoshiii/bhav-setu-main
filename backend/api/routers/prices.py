"""GET /prices/today, GET /prices/series — real mandi prices from Postgres."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api import deps
from api.schemas import PricePoint, TodayPrice

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/today", response_model=list[TodayPrice])
def prices_today(
    crop: str = Query(..., description="crop key, alias or display name"),
    district: str | None = Query(None),
) -> list[TodayPrice]:
    """The most recent price for a crop at every market that carries it."""
    commodity_id, crop_name = deps.resolve_commodity(crop)
    rows = deps.latest_prices(commodity_id)
    if district:
        rows = [r for r in rows if str(r["district"]).lower() == district.lower()]

    return [
        TodayPrice(
            mandi=str(r["mandi"]),
            mandi_id=int(r["mandi_id"]),
            district=str(r["district"]),
            crop=crop_name,
            crop_id=commodity_id,
            modal=float(r["modal_price"]),
            min_price=float(r["min_price"]) if r.get("min_price") is not None else None,
            max_price=float(r["max_price"]) if r.get("max_price") is not None else None,
            arrival_qtl=float(r["arrival_qtl"]) if r.get("arrival_qtl") is not None else None,
            change_pct=round(float(r.get("change_pct") or 0.0), 2),
            obs_date=str(r["obs_date"]),
            observations=int(r.get("observations") or 0),
            can_forecast=bool(r.get("can_forecast", True)),
        )
        for r in sorted(rows, key=lambda r: -float(r["modal_price"]))
    ]


@router.get("/series", response_model=list[PricePoint])
def price_series(
    crop: str = Query(...),
    mandi: str = Query(...),
    days: int = Query(90, ge=7, le=1825),
) -> list[PricePoint]:
    """The history chart. Actual prices only — the forecast lives on /forecast."""
    commodity_id, _ = deps.resolve_commodity(crop)
    mandi_id, _, _ = deps.resolve_mandi(mandi)
    rows = deps.price_series(commodity_id, mandi_id, days)
    return [
        PricePoint(date=str(r["obs_date"]), modal=float(r["modal_price"]), is_forecast=False)
        for r in rows
    ]
