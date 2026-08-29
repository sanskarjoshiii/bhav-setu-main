"""Phase 14 — the soil water stream, turned into one irrigation sentence.

A note on the name: this is soil moisture, not **groundwater**. Nothing here
knows the depth of a water table or what a borewell will yield — Open-Meteo
serves the root zone, and CGWB's well readings are a separate source nobody
has wired in. The advisory says when the crop needs water, never whether
there is water down there to give it.

    advise(crop="onion", mandi_id=3, mandi_name="Nashik", as_of=date(2026, 3, 10))
    -> Advisory(action="irrigate_soon",
                headline="Plan to irrigate — the week ran a 52 mm shortfall", ...)

Three inputs, all measured rather than assumed:

  * **root-zone soil moisture** — how much water is actually there now
  * **ET0** — how fast the atmosphere is pulling water out, FAO-56 reference
  * **rainfall, past and forecast** — what nature has supplied and will supply

The arithmetic is FAO-56's simplest form: crop demand is `Kc x ET0`, and what
irrigation must supply is that minus rain. Growth stages are deliberately not
modelled, because a sowing date is something we do not know and would have to
invent. Mid-season Kc over-states demand for a young crop, and the advisory says
so rather than pretending to a precision it does not have.

**This does not feed the price model.** Soil moisture in Nashik does not move
the onion price in any way we could defend, and adding it to the feature set
would invalidate the trained model for no gain. It is a separate advisory built
from the same weather pull — a second question answered from data we already
fetch, not a fudge to the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import text

from core.config import settings
from core.db import get_conn
from core.errors import InsufficientData

_CFG = settings.irrigation
KC: dict[str, float] = {k: float(v) for k, v in _CFG.crop_coefficients.to_dict().items()}
SATURATION: float = float(_CFG.soil.saturation)
FIELD_CAPACITY: float = float(_CFG.soil.field_capacity)
REFILL_POINT: float = float(_CFG.soil.refill_point)
WILTING_POINT: float = float(_CFG.soil.wilting_point)
BALANCE_DAYS: int = int(_CFG.windows.balance_days)
FORECAST_DAYS: int = int(_CFG.windows.forecast_days)
USEFUL_RAIN_MM: float = float(_CFG.windows.useful_rain_mm)

#: Crops whose Kc is an interpolation rather than a FAO-56 table row. Surfaced
#: to the caller so the UI can mark the number as an estimate.
ASSUMED_KC: frozenset[str] = frozenset({"pomegranate"})

Action = Literal["irrigate_now", "irrigate_soon", "wait", "hold_off", "waterlogged"]


@dataclass
class Advisory:
    """One irrigation decision, with the numbers it was made from."""

    action: Action
    headline: str
    headline_mr: str
    detail: str
    crop: str
    mandi: str
    as_of: date

    soil_moisture: float | None          # root zone, m3/m3
    soil_status: str                     # saturated | wet | adequate | dry | critical
    soil_temp_c: float | None

    et0_7d_mm: float                     # reference demand, last 7 days
    crop_demand_7d_mm: float             # Kc x ET0
    rain_7d_mm: float                    # what actually fell
    deficit_7d_mm: float                 # demand - rain, floored at 0
    rain_forecast_7d_mm: float           # what is coming

    kc: float
    kc_is_assumed: bool
    confidence: str                      # high | medium | low

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "headline": self.headline,
            "headlineMr": self.headline_mr,
            "detail": self.detail,
            "crop": self.crop,
            "mandi": self.mandi,
            "asOf": str(self.as_of),
            "soilMoisture": round(self.soil_moisture, 3) if self.soil_moisture is not None else None,
            "soilStatus": self.soil_status,
            "soilTempC": round(self.soil_temp_c, 1) if self.soil_temp_c is not None else None,
            "et07dMm": round(self.et0_7d_mm, 1),
            "cropDemand7dMm": round(self.crop_demand_7d_mm, 1),
            "rain7dMm": round(self.rain_7d_mm, 1),
            "deficit7dMm": round(self.deficit_7d_mm, 1),
            "rainForecast7dMm": round(self.rain_forecast_7d_mm, 1),
            "kc": self.kc,
            "kcIsAssumed": self.kc_is_assumed,
            "confidence": self.confidence,
        }


def crop_coefficient(crop: str) -> tuple[float, bool]:
    """(Kc, is_an_assumption) for a crop key."""
    key = str(crop).strip().lower().replace(" ", "_")
    if key not in KC:
        raise InsufficientData(
            f"no crop coefficient for {crop!r} — add it to config/irrigation.yaml "
            f"from FAO-56 Table 12"
        )
    return KC[key], key in ASSUMED_KC


def soil_status(moisture: float | None) -> str:
    if moisture is None:
        return "unknown"
    if moisture >= SATURATION:
        return "saturated"
    if moisture >= FIELD_CAPACITY:
        return "wet"
    if moisture >= REFILL_POINT:
        return "adequate"
    if moisture >= WILTING_POINT:
        return "dry"
    return "critical"


_WEATHER_SQL = text(
    """
    SELECT obs_date, rainfall_mm, tmax_c, soil_moisture_root, soil_temp_c,
           et0_mm, is_forecast
    FROM weather_daily
    WHERE mandi_id = :mandi_id
      AND obs_date BETWEEN :start AND :end
    ORDER BY obs_date
    """
)


def _load(mandi_id: int, as_of: date) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(_WEATHER_SQL, {
            "mandi_id": mandi_id,
            "start": as_of - timedelta(days=BALANCE_DAYS),
            "end": as_of + timedelta(days=FORECAST_DAYS),
        }).mappings().all()
    return [dict(r) for r in rows]


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_SERIES_SQL = text(
    """
    SELECT obs_date, soil_moisture_root, soil_moisture_surface,
           et0_mm, rainfall_mm, is_forecast
    FROM weather_daily
    WHERE mandi_id = :mandi_id
      AND obs_date BETWEEN :start AND :end
    ORDER BY obs_date
    """
)


def soil_series(mandi_id: int, as_of: date, days: int = 30) -> list[dict[str, Any]]:
    """The soil water record around `as_of` — history behind, forecast ahead.

    Returned for the chart, so a farmer can see the line falling towards the
    refill point rather than being told a bare verdict.
    """
    with get_conn() as conn:
        rows = conn.execute(_SERIES_SQL, {
            "mandi_id": mandi_id,
            "start": as_of - timedelta(days=days),
            "end": as_of + timedelta(days=FORECAST_DAYS),
        }).mappings().all()
    return [
        {
            "date": str(r["obs_date"]),
            "soilMoistureRoot": _number(r["soil_moisture_root"]),
            "soilMoistureSurface": _number(r["soil_moisture_surface"]),
            "et0Mm": _number(r["et0_mm"]),
            "rainfallMm": _number(r["rainfall_mm"]),
            "isForecast": bool(r["is_forecast"]),
        }
        for r in rows
    ]


def advise(crop: str, mandi_id: int, mandi_name: str, as_of: date) -> Advisory:
    """The irrigation decision for one crop at one place on one day."""
    kc, kc_assumed = crop_coefficient(crop)
    rows = _load(mandi_id, as_of)
    if not rows:
        raise InsufficientData(
            f"no weather on record for {mandi_name} around {as_of}"
        )

    past = [r for r in rows if r["obs_date"] <= as_of]
    ahead = [r for r in rows if r["obs_date"] > as_of]

    et0_7d = sum(_number(r["et0_mm"]) or 0.0 for r in past)
    rain_7d = sum(_number(r["rainfall_mm"]) or 0.0 for r in past)
    rain_ahead = sum(_number(r["rainfall_mm"]) or 0.0 for r in ahead)
    demand_7d = kc * et0_7d
    deficit = max(0.0, demand_7d - rain_7d)

    # Most recent real soil reading. The archive lags a few days, so the newest
    # row is often a forecast — which carries no soil moisture.
    moisture = next(
        (_number(r["soil_moisture_root"]) for r in reversed(past)
         if _number(r["soil_moisture_root"]) is not None), None)
    temp = next(
        (_number(r["soil_temp_c"]) for r in reversed(past)
         if _number(r["soil_temp_c"]) is not None), None)

    status = soil_status(moisture)
    action, headline, headline_mr, detail = _decide(
        status, moisture, deficit, rain_ahead, crop)

    # Confidence is about the INPUTS, not the arithmetic. No soil reading means
    # the advisory rests on the water balance alone, which is weaker.
    if moisture is None:
        confidence = "low"
    elif et0_7d <= 0:
        confidence = "low"
    elif kc_assumed:
        confidence = "medium"
    else:
        confidence = "high"

    return Advisory(
        action=action, headline=headline, headline_mr=headline_mr, detail=detail,
        crop=crop, mandi=mandi_name, as_of=as_of,
        soil_moisture=moisture, soil_status=status, soil_temp_c=temp,
        et0_7d_mm=et0_7d, crop_demand_7d_mm=demand_7d, rain_7d_mm=rain_7d,
        deficit_7d_mm=deficit, rain_forecast_7d_mm=rain_ahead,
        kc=kc, kc_is_assumed=kc_assumed, confidence=confidence,
    )




def _decide(status: str, moisture: float | None, deficit: float,
            rain_ahead: float, crop: str) -> tuple[Action, str, str, str]:
    """Soil state first, then the water balance, then what the sky is doing."""
    name = crop.replace("_", " ")

    # Too much water is a problem in its own right, and irrigating into it is
    # actively harmful — roots need air.
    if status == "saturated":
        return ("waterlogged",
                "Soil is water-logged — do not irrigate",
                "जमिनीत पाणी साचले आहे — पाणी देऊ नका",
                f"Root-zone moisture is above field capacity. {name.capitalize()} roots "
                f"need air as well as water; drain standing water if you can.")

    if status == "critical":
        return ("irrigate_now",
                "Irrigate today — the soil is at wilting point",
                "आजच पाणी द्या — जमीन कोरडी पडली आहे",
                f"Root-zone moisture is below the point where {name} can pull water "
                f"out of the soil. Waiting costs yield.")

    # Rain worth waiting for beats running a pump.
    if rain_ahead >= USEFUL_RAIN_MM and status in ("adequate", "wet"):
        return ("wait",
                f"Hold off — about {rain_ahead:.0f} mm of rain is expected",
                f"थांबा — पुढील दिवसांत सुमारे {rain_ahead:.0f} मि.मी. पाऊस अपेक्षित आहे",
                f"There is enough moisture for now and rain is forecast within a week. "
                f"Irrigating before it arrives wastes water and diesel.")

    if status == "dry":
        if rain_ahead >= USEFUL_RAIN_MM:
            return ("irrigate_soon",
                    f"Irrigate within a day or two unless the {rain_ahead:.0f} mm arrives",
                    f"एक-दोन दिवसांत पाणी द्या, पाऊस आला नाही तर",
                    f"The soil is dry and the last week ran a {deficit:.0f} mm deficit, "
                    f"but rain is forecast. Watch the sky for a day before starting the pump.")
        return ("irrigate_now",
                "Irrigate now — the soil is dry and no rain is coming",
                "आताच पाणी द्या — जमीन कोरडी आहे आणि पाऊस नाही",
                f"Root-zone moisture has fallen below the refill point and the last "
                f"week left a {deficit:.0f} mm shortfall.")

    if status == "wet":
        return ("hold_off",
                "No irrigation needed — the soil is still wet",
                "पाणी देण्याची गरज नाही — जमीन अजून ओली आहे",
                "Root-zone moisture is at or above field capacity.")

    # adequate, or no reading at all — fall back to the water balance
    if deficit > 25.0:
        return ("irrigate_soon",
                f"Plan to irrigate — the week ran a {deficit:.0f} mm shortfall",
                f"पाणी देण्याची तयारी करा — या आठवड्यात {deficit:.0f} मि.मी. कमी पडले",
                f"{name.capitalize()} used about {deficit:.0f} mm more than the rain "
                f"supplied over the last week.")

    return ("hold_off",
            "No irrigation needed this week",
            "या आठवड्यात पाणी देण्याची गरज नाही",
            f"Rain has broadly kept up with what {name} is using.")
