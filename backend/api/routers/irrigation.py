"""GET /irrigation — the soil & groundwater stream, as one decision.

The second question this product answers. Prices tell a farmer **when to sell**;
this tells him **when to irrigate**, from measured root-zone moisture, FAO-56
reference evapotranspiration, and the rain that has fallen and is forecast.

No SQL here, by the same rule as every other router: the water balance and the
trend series both live in `agronomy.irrigation`, so the page and any future
WhatsApp reply cannot drift apart on what "dry" means.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

import history
from agronomy import irrigation
from api import deps
from api.routers.auth import Farmer, optional_farmer
from api.schemas import IrrigationAdvisory, SoilPoint

router = APIRouter(tags=["agronomy"])


@router.get("/irrigation", response_model=IrrigationAdvisory)
def irrigation_advice(
    crop: str = Query(..., description="crop key, alias or display name"),
    mandi: str | None = Query(None, description="mandi or district; nearest weather station"),
    days: int = Query(30, ge=7, le=180, description="days of soil history to chart"),
    as_of: date | None = Query(None, description="defaults to today"),
    farmer: Farmer | None = Depends(optional_farmer),
) -> IrrigationAdvisory:
    """Irrigate or wait, for one crop at one place, with the numbers behind it."""
    # The crop has to be one we hold a coefficient for, and the name the farmer
    # typed has to resolve the same way it does everywhere else on the site.
    _, crop_name = deps.resolve_commodity(crop)
    crop_key = crop_name.lower().replace(" ", "_")

    if mandi:
        mandi_id, mandi_name, _district = deps.resolve_mandi(mandi)
    else:
        # No market named: use the one with the most price history, which is
        # also the one whose weather station a farmer here is nearest to.
        options = deps.list_mandis(with_data_only=True)
        if not options:
            mandi_id, mandi_name = 1, "Lasalgaon"
        else:
            best = max(options, key=lambda m: m["rows"])
            mandi_id, mandi_name = int(best["id"]), str(best["name"])

    today = as_of or date.today()
    advisory = irrigation.advise(crop_key, mandi_id, mandi_name, today)
    series = irrigation.soil_series(mandi_id, today, days=days)

    if farmer is not None:
        history.record_irrigation(
            farmer, crop=crop_name, mandi=mandi_name, action=advisory.action,
            headline=advisory.headline, soil_moisture=advisory.soil_moisture,
            soil_status=advisory.soil_status, deficit_mm=advisory.deficit_7d_mm,
            confidence=advisory.confidence)

    return IrrigationAdvisory.model_validate({
        **advisory.to_dict(),
        "fieldCapacity": irrigation.FIELD_CAPACITY,
        "refillPoint": irrigation.REFILL_POINT,
        "wiltingPoint": irrigation.WILTING_POINT,
        "series": [SoilPoint.model_validate(p) for p in series],
    })
