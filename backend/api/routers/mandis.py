"""GET /mandis, GET /districts, GET /crops — the reference data every page needs."""

from __future__ import annotations

from fastapi import APIRouter, Query

from api import deps
from api.schemas import Crop, District, Mandi

router = APIRouter(tags=["reference"])

#: Marathi names for the four districts we carry. The website shows these next
#: to the English name; a missing one falls back to English rather than blank.
NAME_MR: dict[str, str] = {
    "Pune": "पुणे", "Nashik": "नाशिक", "Nasik": "नाशिक",
    "Ahmednagar": "अहमदनगर", "Solapur": "सोलापूर",
    "Lasalgaon": "लासलगाव", "Pimpalgaon Baswant": "पिंपळगाव बसवंत",
    "Yeola": "येवला", "Chandvad": "चांदवड", "Junnar": "जुन्नर",
    "Manchar": "मंचर", "Khed": "खेड", "Baramati": "बारामती",
    "Pandharpur": "पंढरपूर", "Barshi": "बार्शी", "Rahuri": "राहुरी",
    "Sangamner": "संगमनेर", "Shrirampur": "श्रीरामपूर",
}

CROP_NAME_MR: dict[str, str] = {
    "onion": "कांदा", "potato": "बटाटा", "tomato": "टोमॅटो", "garlic": "लसूण",
    "brinjal": "वांगी", "cauliflower": "फुलकोबी", "green_chilli": "हिरवी मिरची",
    "okra": "भेंडी", "banana": "केळी", "mango": "आंबा", "grapes": "द्राक्षे",
    "orange": "संत्रा", "pomegranate": "डाळिंब", "cabbage": "कोबी",
}


def _liquidity(arrival: float | None) -> str:
    if arrival is None:
        return "low"
    if arrival >= 1000:
        return "high"
    return "medium" if arrival >= 200 else "low"


@router.get("/mandis", response_model=list[Mandi])
def get_mandis(
    crop: str | None = Query(None, description="show today's price for this crop"),
    with_data: bool = Query(True, description="only markets that carry price data"),
) -> list[Mandi]:
    rows = deps.list_mandis(with_data_only=with_data)

    prices: dict[int, dict] = {}
    if crop:
        commodity_id, _ = deps.resolve_commodity(crop)
        prices = {int(r["mandi_id"]): r for r in deps.latest_prices(commodity_id)}

    out: list[Mandi] = []
    for row in rows:
        price = prices.get(int(row["id"]), {})
        out.append(Mandi(
            id=int(row["id"]),
            name=str(row["name"]),
            name_mr=NAME_MR.get(str(row["name"]), str(row["name"])),
            district=str(row["district"]),
            lat=float(row["lat"] or 0.0),
            lon=float(row["lon"] or 0.0),
            distance_km=round(deps.haversine_km(
                deps.REFERENCE_LAT, deps.REFERENCE_LON,
                float(row["lat"] or deps.REFERENCE_LAT),
                float(row["lon"] or deps.REFERENCE_LON),
            ) * deps.ROAD_FACTOR, 1),
            today_modal=float(price.get("modal_price") or 0.0),
            change_pct=round(float(price.get("change_pct") or 0.0), 2),
            arrival_qtl=float(price.get("arrival_qtl") or 0.0),
            liquidity=_liquidity(price.get("arrival_qtl")),
        ))
    return out


@router.get("/districts", response_model=list[District])
def get_districts() -> list[District]:
    seen: dict[str, dict[str, int]] = {}
    for row in deps.list_mandis(with_data_only=True):
        entry = seen.setdefault(str(row["district"]), {"mandis": 0})
        entry["mandis"] += 1
    crops = len(deps.list_commodities(with_data_only=True))
    return [District(name=name, mandi_count=v["mandis"], crop_count=crops)
            for name, v in sorted(seen.items())]


@router.get("/crops", response_model=list[Crop])
def get_crops(with_data: bool = Query(True)) -> list[Crop]:
    """Every crop, flagged by whether we can actually forecast it.

    `has_forecast` is the honest column: a crop with no rows is still listed so
    the picker looks complete, but it is marked so the UI can say "price only,
    no forecast" instead of faking one.
    """
    rows = deps.list_commodities(with_data_only=False)
    out: list[Crop] = []
    for row in rows:
        try:
            spec = deps.crop_config(str(row["name"]))
        except Exception:                                  # noqa: BLE001
            continue
        has_data = int(row["rows"]) > 0
        if with_data and not has_data:
            continue
        out.append(Crop(
            id=int(row["id"]),
            key=str(spec["key"]),
            name=str(row["name"]),
            name_mr=CROP_NAME_MR.get(str(spec["key"]), str(row["name"])),
            group=str(spec.get("crop_group", "")),
            perishability_class=int(spec.get("perishability_class", 3)),
            shelf_life_days=int(spec.get("shelf_life_days", 30)),
            max_hold_days=int(spec.get("max_hold_days", 7)),
            has_forecast=has_data,
        ))
    return out
