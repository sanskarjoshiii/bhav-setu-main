"""GET /compare — net in hand at every market, ranked, with the rank flip marked.

The demo moment: rank by the price on the board, rank by what actually reaches
the farmer's hand, and show where the two disagree.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from api import deps
from api.schemas import Grade, MandiComparison, Storage
from core.errors import InsufficientData
from economics.compare import compare_mandis

router = APIRouter(tags=["economics"])


@router.get("/compare", response_model=list[MandiComparison])
def compare(
    crop: str = Query(...),
    qty_qtl: float = Query(80.0, gt=0),
    days_held: int = Query(0, ge=0, le=60),
    grade: Grade = Query("B"),
    storage: Storage = Query("ambient"),
    village_lat: float | None = Query(None),
    village_lon: float | None = Query(None),
) -> list[MandiComparison]:
    commodity_id, crop_name = deps.resolve_commodity(crop)

    origin = None
    if village_lat is not None and village_lon is not None:
        origin = (village_lat, village_lon)

    options = deps.mandi_options(commodity_id, origin)
    if not options:
        raise InsufficientData(f"no market currently carries {crop_name}")

    rows = compare_mandis(
        options,
        qty_qtl=qty_qtl,
        days_held=days_held,
        grade=grade,
        storage=storage,
        crop=crop_name.lower().replace(" ", "_"),
    )
    return [MandiComparison.model_validate(row.to_dict()) for row in rows]
