"""GET /forecast — history plus the model's band, in one series for the chart.

**This router reaches the model only through `get_provider()`.** It does not
import LightGBM, does not open a booster, and does not know which forecaster is
active. That is the Phase A0 rule, and it is why swapping the model was one line
of config: `grep -rn "lightgbm" backend/api` must stay empty.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Query

from api import deps
from api.schemas import ForecastResponse, PricePoint
from ml.port import DEFAULT_HORIZONS
from ml.provider import active_provider_name, get_provider

router = APIRouter(tags=["forecast"])


@router.get("/forecast", response_model=ForecastResponse)
def get_forecast(
    crop: str = Query(...),
    mandi: str = Query(...),
    history_days: int = Query(90, ge=7, le=1825),
) -> ForecastResponse:
    commodity_id, crop_name = deps.resolve_commodity(crop)
    mandi_id, mandi_name, _ = deps.resolve_mandi(mandi)

    _, as_of = deps.latest_price_for(commodity_id, mandi_id)

    series = [
        PricePoint(date=str(r["obs_date"]), modal=float(r["modal_price"]), is_forecast=False)
        for r in deps.price_series(commodity_id, mandi_id, history_days)
    ]

    provider = get_provider()
    bands = provider.predict_quantiles(commodity_id, mandi_id, as_of, DEFAULT_HORIZONS)

    for horizon in sorted(bands):
        band = bands[horizon]
        series.append(PricePoint(
            date=str(as_of + timedelta(days=horizon)),
            modal=None,
            p10=round(band.p10, 2),
            p50=round(band.p50, 2),
            p90=round(band.p90, 2),
            is_forecast=True,
        ))

    return ForecastResponse(
        crop=crop_name,
        mandi=mandi_name,
        as_of=str(as_of),
        provider=active_provider_name(),
        model_version=getattr(provider, "version", "unknown"),
        series=series,
    )
