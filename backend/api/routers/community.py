"""GET/POST /pools — transport pooling backed by real rows."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Query

from api import deps
from api.schemas import PoolCreateRequest, PoolJoinRequest, TransportPool
from community import pools as community
from core.errors import InsufficientData

router = APIRouter(prefix="/pools", tags=["community"])


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise InsufficientData(f"travel_date must be YYYY-MM-DD, got {value!r}") from None


def _wire(pool: community.Pool) -> TransportPool:
    return TransportPool.model_validate(pool.to_dict())


@router.get("", response_model=list[TransportPool])
def list_pools(
    mandi: str | None = Query(None),
    district: str | None = Query(None, description="narrow to one district"),
    travel_date: str | None = Query(None),
    open_only: bool = Query(True),
) -> list[TransportPool]:
    mandi_id = deps.resolve_mandi(mandi)[0] if mandi else None
    when = _parse_date(travel_date) if travel_date else None
    return [_wire(p) for p in community.list_pools(mandi_id, when, open_only, district)]


@router.post("", response_model=TransportPool, status_code=201)
def create_pool(request: PoolCreateRequest) -> TransportPool:
    mandi_id, mandi_name, _ = deps.resolve_mandi(request.mandi)
    distance = _distance_to(mandi_id)
    return _wire(community.create_pool(
        mandi_id=mandi_id,
        travel_date=_parse_date(request.travel_date),
        farmer=request.farmer,
        village=request.village,
        qty_qtl=request.qty_qtl,
        distance_km=distance,
        capacity_qtl=request.capacity_qtl,
    ))


@router.post("/{pool_id}/join", response_model=TransportPool)
def join_pool(pool_id: int, request: PoolJoinRequest) -> TransportPool:
    return _wire(community.join_pool(
        pool_id, request.farmer, request.village, request.qty_qtl))


@router.delete("/{pool_id}/members/{member_id}", response_model=TransportPool)
def leave_pool(pool_id: int, member_id: int) -> TransportPool:
    return _wire(community.leave_pool(pool_id, member_id))


def _distance_to(mandi_id: int) -> float:
    """Road distance from the reference village, same basis as the compare page."""
    for row in deps.list_mandis(with_data_only=False):
        if int(row["id"]) == mandi_id:
            return round(deps.haversine_km(
                deps.REFERENCE_LAT, deps.REFERENCE_LON,
                float(row["lat"] or deps.REFERENCE_LAT),
                float(row["lon"] or deps.REFERENCE_LON),
            ) * deps.ROAD_FACTOR, 1)
    return 0.0
