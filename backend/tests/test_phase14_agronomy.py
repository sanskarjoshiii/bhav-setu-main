"""Phase 14 — the soil & groundwater advisory.

The test that matters most here is `test_a_dry_season_deficit_is_never_called_wet`.
The first cut of this feature used textbook medium-loam thresholds, and against
Open-Meteo's ERA5 layer that put the *dry season* above field capacity: Nashik
in March — 0.2 mm of rain against 6.2 mm/day of ET0 — came back as "the soil is
still wet, no irrigation needed". Everything else in this file is ordinary
correctness; that one guards the sentence that would actually cost a farmer a
crop.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agronomy import irrigation
from api.main import app
from core.config import settings
from core.errors import InsufficientData

client = TestClient(app)
BASE = "/api/v1"

#: A market we hold weather for, and the crop the product is built around.
MANDI = "Nashik"
CROP = "onion"

#: Peak dry season and peak monsoon in the Deccan. The advisory has to tell
#: them apart or it is not reading the data at all.
DRY_SEASON = ("2025-11-15", "2025-12-20", "2026-01-20",
              "2026-02-15", "2026-03-10", "2026-04-25")
MONSOON = ("2026-07-20", "2026-08-20")


def advise(as_of: str, crop: str = CROP, mandi: str = MANDI) -> dict:
    response = client.get(f"{BASE}/irrigation",
                          params={"crop": crop, "mandi": mandi, "as_of": as_of})
    assert response.status_code == 200, response.text
    return response.json()


# ══════════════════════════════════════════════════════════════════════════
# the one that matters
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("as_of", DRY_SEASON)
def test_a_dry_season_deficit_is_never_called_wet(as_of: str):
    """REGRESSION. No "you need not irrigate" in the middle of the dry season.

    Every date here is a week with essentially no rain and 4-7 mm/day of
    reference evapotranspiration. Whatever the soil sensor says, the advisory
    must not tell a farmer to do nothing.
    """
    body = advise(as_of)
    assert body["deficit7dMm"] > 15.0, (
        f"{as_of} was expected to be a deficit week; got {body['deficit7dMm']} mm"
    )
    assert body["action"] in ("irrigate_now", "irrigate_soon", "wait"), (
        f"{as_of}: dry-season week returned {body['action']!r} — "
        f"{body['headline']!r} (soil {body['soilMoisture']}, "
        f"{body['soilStatus']}, {body['rainForecast7dMm']} mm forecast)"
    )
    # "wait" is only defensible when real rain is actually coming.
    if body["action"] == "wait":
        assert body["rainForecast7dMm"] >= float(settings.irrigation.windows.useful_rain_mm)


@pytest.mark.parametrize("as_of", MONSOON)
def test_the_monsoon_does_not_ask_a_farmer_to_run_a_pump(as_of: str):
    body = advise(as_of)
    assert body["action"] in ("hold_off", "wait", "waterlogged"), body["headline"]


def test_the_advisory_changes_across_the_year():
    """Anti-vacuity: a constant answer would pass every single-date assertion."""
    actions = {advise(d)["action"] for d in DRY_SEASON + MONSOON}
    assert len(actions) > 1, f"advisory never changes — always {actions}"


# ══════════════════════════════════════════════════════════════════════════
# thresholds and the calibration that produced them
# ══════════════════════════════════════════════════════════════════════════

def test_thresholds_are_ordered():
    assert (irrigation.WILTING_POINT < irrigation.REFILL_POINT
            < irrigation.FIELD_CAPACITY < irrigation.SATURATION)


def test_soil_status_spans_every_band():
    assert irrigation.soil_status(irrigation.SATURATION + 0.01) == "saturated"
    assert irrigation.soil_status(irrigation.FIELD_CAPACITY + 0.01) == "wet"
    assert irrigation.soil_status(irrigation.REFILL_POINT + 0.01) == "adequate"
    assert irrigation.soil_status(irrigation.WILTING_POINT + 0.01) == "dry"
    assert irrigation.soil_status(irrigation.WILTING_POINT - 0.01) == "critical"
    assert irrigation.soil_status(None) == "unknown"


def test_thresholds_still_match_the_ingested_data():
    """The config is derived from the data; `scripts/calibrate_soil.py` re-derives it.

    If a new mandi shifts the distribution, this fails rather than letting the
    advisory quietly drift onto thresholds that no longer describe the soil.
    """
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    calibrate = pytest.importorskip("calibrate_soil", reason="scripts/ not importable")
    derived = calibrate.derive()[0]
    for key, value in derived.items():
        configured = float(getattr(settings.irrigation.soil, key))
        assert abs(configured - value) <= calibrate.TOLERANCE, (
            f"{key}: config {configured} vs data {value} — re-run scripts/calibrate_soil.py"
        )


# ══════════════════════════════════════════════════════════════════════════
# crop coefficients
# ══════════════════════════════════════════════════════════════════════════

def test_every_configured_crop_has_a_coefficient():
    for key in settings.irrigation.crop_coefficients.to_dict():
        kc, _assumed = irrigation.crop_coefficient(key)
        assert 0.2 < kc < 1.5, f"{key} has an implausible Kc of {kc}"


def test_an_unknown_crop_is_refused_rather_than_guessed():
    with pytest.raises(InsufficientData):
        irrigation.crop_coefficient("dragonfruit")


def test_an_assumed_coefficient_is_declared():
    """Pomegranate has no FAO-56 row. The UI must be able to say so."""
    kc, assumed = irrigation.crop_coefficient("pomegranate")
    assert assumed is True
    assert irrigation.crop_coefficient("onion")[1] is False


def test_an_assumed_coefficient_caps_confidence():
    body = advise("2026-03-10", crop="pomegranate", mandi="Ahmednagar")
    assert body["kcIsAssumed"] is True
    assert body["confidence"] != "high"


# ══════════════════════════════════════════════════════════════════════════
# the endpoint
# ══════════════════════════════════════════════════════════════════════════

def test_response_is_camel_case_matching_types_ts():
    body = advise("2026-03-10")
    required = {
        "action", "headline", "headlineMr", "detail", "crop", "mandi", "asOf",
        "soilMoisture", "soilStatus", "soilTempC", "et07dMm", "cropDemand7dMm",
        "rain7dMm", "deficit7dMm", "rainForecast7dMm", "kc", "kcIsAssumed",
        "confidence", "fieldCapacity", "refillPoint", "wiltingPoint", "series",
    }
    assert required <= set(body), f"missing: {required - set(body)}"


def test_crop_demand_is_kc_times_et0():
    body = advise("2026-03-10")
    assert body["cropDemand7dMm"] == pytest.approx(body["kc"] * body["et07dMm"], abs=0.15)


def test_deficit_is_demand_less_rain_and_never_negative():
    for as_of in DRY_SEASON + MONSOON:
        body = advise(as_of)
        expected = max(0.0, body["cropDemand7dMm"] - body["rain7dMm"])
        assert body["deficit7dMm"] == pytest.approx(expected, abs=0.15)
        assert body["deficit7dMm"] >= 0.0


def test_the_series_is_in_date_order_and_reaches_into_the_forecast():
    body = advise("2026-03-10")
    series = body["series"]
    assert series, "no soil series returned"
    dates = [row["date"] for row in series]
    assert dates == sorted(dates)
    assert dates[-1] >= body["asOf"]


def test_the_series_carries_real_soil_readings():
    """Guards the ingestion half: null soil columns make the advisory vacuous."""
    body = advise("2026-03-10")
    readings = [r["soilMoistureRoot"] for r in body["series"]
                if r["soilMoistureRoot"] is not None]
    assert len(readings) >= 10, "soil moisture is not being ingested — run make backfill"
    assert all(0.0 < v < 1.0 for v in readings), "volumetric water content out of range"


def test_soil_moisture_is_reported_with_its_status():
    body = advise("2026-03-10")
    assert body["soilMoisture"] is not None
    assert body["soilStatus"] == irrigation.soil_status(body["soilMoisture"])
    assert body["confidence"] in ("high", "medium", "low")


def test_a_crop_we_cannot_advise_on_is_a_readable_422():
    response = client.get(f"{BASE}/irrigation",
                          params={"crop": "dragonfruit", "mandi": MANDI})
    assert response.status_code == 422
    assert response.json()["code"] == "insufficient_data"
    assert response.json()["detail"]


def test_an_unknown_market_is_refused():
    response = client.get(f"{BASE}/irrigation",
                          params={"crop": CROP, "mandi": "Atlantis"})
    assert response.status_code == 422


def test_the_market_is_optional():
    response = client.get(f"{BASE}/irrigation", params={"crop": CROP})
    assert response.status_code == 200
    assert response.json()["mandi"]


def test_both_languages_are_filled_in():
    for as_of in DRY_SEASON + MONSOON:
        body = advise(as_of)
        assert body["headline"].strip()
        assert body["headlineMr"].strip()
        assert body["headlineMr"] != body["headline"], "Marathi headline is untranslated"
        assert any("ऀ" <= ch <= "ॿ" for ch in body["headlineMr"]), \
            f"{body['headlineMr']!r} is not Devanagari"


def test_the_advisory_is_not_wired_into_the_price_model():
    """Soil moisture must not have leaked into the forecast feature set.

    The module docstring promises this. If someone adds it to the registry the
    trained model's contract breaks, so the promise is worth a test.
    """
    from features import registry

    names = " ".join(registry.FEATURE_NAMES).lower()
    for banned in ("soil_moisture", "et0", "soil_temp"):
        assert banned not in names, f"{banned} leaked into the forecast features"
