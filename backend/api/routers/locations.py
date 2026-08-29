"""GET /locations — the districts and villages a farmer may register from.

Only the four districts we hold price data for. Offering a village in Kolhapur
would take a farmer's registration and then have nothing to tell him, which is
worse than saying "not yet". When more districts are pulled, this list widens on
its own from `config/locations.yaml`.
"""

from __future__ import annotations

from fastapi import APIRouter

from api import deps
from api.schemas import Wire
from core.config import settings

router = APIRouter(tags=["reference"])


class Village(Wire):
    name: str
    name_mr: str
    lat: float
    lon: float
    #: Road distance to the market we carry for this district. What the transport
    #: line in the cost waterfall will use once he registers.
    distance_to_market_km: float | None = None


class DistrictLocation(Wire):
    name: str
    name_mr: str
    lat: float
    lon: float
    villages: list[Village]
    #: The market yard we actually have prices for in this district.
    market: str | None = None
    has_data: bool = False


@router.get("/locations", response_model=list[DistrictLocation])
def locations() -> list[DistrictLocation]:
    blocks = settings.locations.districts.to_dict()

    with_data = {m["district"] for m in deps.list_mandis(with_data_only=True)}
    markets = {m["district"]: m for m in deps.list_mandis(with_data_only=True)}

    out: list[DistrictLocation] = []
    for name, block in blocks.items():
        market = markets.get(name)
        villages = []
        for v in block.get("villages", []):
            distance = None
            if market and market.get("lat") is not None:
                distance = round(deps.haversine_km(
                    float(v["lat"]), float(v["lon"]),
                    float(market["lat"]), float(market["lon"]),
                ) * deps.ROAD_FACTOR, 1)
            villages.append(Village(
                name=str(v["name"]), name_mr=str(v.get("name_mr", v["name"])),
                lat=float(v["lat"]), lon=float(v["lon"]),
                distance_to_market_km=distance,
            ))
        out.append(DistrictLocation(
            name=name, name_mr=str(block.get("name_mr", name)),
            lat=float(block["lat"]), lon=float(block["lon"]),
            villages=sorted(villages, key=lambda v: v.name),
            market=str(market["name"]) if market else None,
            has_data=name in with_data,
        ))
    return sorted(out, key=lambda d: d.name)
