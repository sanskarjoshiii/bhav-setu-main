"""POST /recommend — the sentence a farmer acts on.

Wires the forecast (through the port), the economics engine and the decision
engine into one answer. This is the endpoint the whole product exists to serve.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

import history
from api import deps
from api.routers.auth import Farmer, optional_farmer
from api.schemas import Recommendation, RecommendRequest
from core.errors import InsufficientData
from decision.engine import Lot, optimise
from decision.explain import explain
from ml.port import DEFAULT_HORIZONS
from ml.provider import get_provider

router = APIRouter(tags=["advice"])


@router.post("/recommend", response_model=Recommendation)
def recommend(request: RecommendRequest,
              farmer: Farmer | None = Depends(optional_farmer)) -> Recommendation:
    commodity_id, crop_name = deps.resolve_commodity(request.crop)

    origin = None
    if request.village_lat is not None and request.village_lon is not None:
        origin = (request.village_lat, request.village_lon)

    options = deps.mandi_options(commodity_id, origin)
    if request.district:
        narrowed = [o for o in options
                    if (o.district or "").lower() == request.district.lower()]
        options = narrowed or options
    if not options:
        raise InsufficientData(
            f"no market currently carries {crop_name}. Try another crop or district."
        )

    anchor = next((o for o in options if o.mandi == request.mandi), None) or \
        max(options, key=lambda o: o.price_per_qtl)
    mandi_id = anchor.mandi_id or deps.resolve_mandi(anchor.mandi)[0]
    _, as_of = deps.latest_price_for(commodity_id, mandi_id)

    provider = get_provider()
    forecast = provider.predict_quantiles(commodity_id, mandi_id, as_of, DEFAULT_HORIZONS)

    # A feature row is what the explanation is written from. It is optional: if
    # it cannot be built we still give advice, just with a plainer reason —
    # better than refusing to answer because the prose is unavailable.
    reason = reason_mr = ""
    try:
        from core.db import get_conn
        from ml.port import build_serving_row

        with get_conn() as conn:
            row = build_serving_row(as_of, mandi_id, commodity_id, conn)
        reason, reason_mr = explain(row.values)
    except Exception:                                      # noqa: BLE001
        pass

    plan = optimise(
        Lot(crop=crop_name.lower().replace(" ", "_"),
            qty_qtl=request.qty_qtl,
            grade=request.grade,
            storage=request.storage,
            risk_profile=request.risk_profile),
        today_prices={o.mandi: o.price_per_qtl for o in options},
        forecast=forecast,
        mandis=options,
        as_of=as_of,
        reason=reason,
        reason_mr=reason_mr,
    )
    wire = Recommendation.model_validate(plan.to_dict())

    # Anonymous use is fine and stays anonymous; a signed-in farmer gets the
    # advice written to his history so he can see what he was told, and when.
    if farmer is not None:
        target = wire.tranches[0].mandi if wire.tranches else anchor.mandi
        history.record_recommendation(
            farmer, crop=crop_name, qty_qtl=request.qty_qtl, mandi=target,
            action=wire.action, expected_gain=wire.expected_gain,
            confidence=wire.confidence,
            payload={"headline": wire.headline, "grade": request.grade,
                     "storage": request.storage, "riskProfile": request.risk_profile})
    return wire
