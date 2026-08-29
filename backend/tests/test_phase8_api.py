"""Phase 8 — every endpoint, its shape, and the rule that keeps the port sealed."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
BASE = "/api/v1"


@pytest.fixture(scope="module")
def crop() -> str:
    body = client.get(f"{BASE}/crops").json()
    assert body, "no crops with data — load prices before running the API tests"
    return body[0]["key"]


@pytest.fixture(scope="module")
def mandi(crop: str) -> str:
    body = client.get(f"{BASE}/prices/today", params={"crop": crop}).json()
    assert body, f"no prices for {crop}"
    return body[0]["mandi"]


# ══════════════════════════════════════════════════════════════════════════
# the rule that made swap day one line
# ══════════════════════════════════════════════════════════════════════════

def test_no_router_imports_the_model_directly():
    """Phase A0's guard, as a test rather than a habit.

    If a router imports LightGBM or opens a booster, promoting a new model stops
    being a config edit and becomes a refactor. The only legal path to a
    forecast is `ml.provider.get_provider()`.
    """
    api_dir = Path(__file__).resolve().parents[1] / "api"
    offenders: list[str] = []
    for path in api_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bimport\s+lightgbm\b|\blgb\.|Booster\(|lgbm_provider", text):
            offenders.append(str(path.relative_to(api_dir)))
    assert not offenders, f"routers reaching past the port: {offenders}"


def test_decision_engine_does_not_import_the_model_either():
    decision = Path(__file__).resolve().parents[1] / "decision"
    for path in decision.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "lightgbm" not in text.lower(), f"{path.name} imports the model"


# ══════════════════════════════════════════════════════════════════════════
# reference data
# ══════════════════════════════════════════════════════════════════════════

def test_health_reports_the_live_provider_and_row_count():
    body = client.get(f"{BASE}/health").json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] is True
    assert body["priceRows"] > 0
    assert body["provider"] in ("baseline", "lightgbm")
    assert body["modelVersion"]


def test_mandis_returns_camel_case_matching_types_ts():
    body = client.get(f"{BASE}/mandis").json()
    assert body
    required = {"id", "name", "nameMr", "district", "lat", "lon",
                "distanceKm", "todayModal", "changePct", "arrivalQtl", "liquidity"}
    assert required <= set(body[0]), f"missing: {required - set(body[0])}"


def test_mandis_carry_todays_price_when_a_crop_is_named(crop: str):
    body = client.get(f"{BASE}/mandis", params={"crop": crop}).json()
    assert any(m["todayModal"] > 0 for m in body)


def test_districts_are_listed_with_counts():
    body = client.get(f"{BASE}/districts").json()
    assert body
    assert {"name", "mandiCount", "cropCount"} <= set(body[0])


def test_crops_flag_whether_a_forecast_is_possible():
    """A crop with no data is honest about it rather than faking a forecast."""
    body = client.get(f"{BASE}/crops", params={"with_data": False}).json()
    assert body
    assert {"key", "name", "hasForecast", "maxHoldDays", "shelfLifeDays"} <= set(body[0])


# ══════════════════════════════════════════════════════════════════════════
# prices and forecast
# ══════════════════════════════════════════════════════════════════════════

def test_prices_today_is_sorted_and_shaped(crop: str):
    body = client.get(f"{BASE}/prices/today", params={"crop": crop}).json()
    assert body
    assert {"mandi", "district", "modal", "changePct", "obsDate"} <= set(body[0])
    assert [r["modal"] for r in body] == sorted((r["modal"] for r in body), reverse=True)


def test_price_series_returns_history_in_date_order(crop: str, mandi: str):
    body = client.get(f"{BASE}/prices/series",
                      params={"crop": crop, "mandi": mandi, "days": 120}).json()
    assert len(body) > 20
    assert [p["date"] for p in body] == sorted(p["date"] for p in body)
    assert all(p["isForecast"] is False for p in body)


def test_forecast_appends_a_band_to_the_history(crop: str, mandi: str):
    body = client.get(f"{BASE}/forecast", params={"crop": crop, "mandi": mandi}).json()
    assert body["provider"] in ("baseline", "lightgbm")
    assert body["modelVersion"]

    forecast = [p for p in body["series"] if p["isForecast"]]
    assert len(forecast) == 4, "expected one point per horizon"
    for point in forecast:
        assert point["p10"] <= point["p50"] <= point["p90"], "band came back unsorted"
        assert point["modal"] is None


def test_forecast_uncertainty_grows_with_horizon(crop: str, mandi: str):
    body = client.get(f"{BASE}/forecast", params={"crop": crop, "mandi": mandi}).json()
    widths = [p["p90"] - p["p10"] for p in body["series"] if p["isForecast"]]
    assert widths[-1] > widths[0], "a 15-day forecast must be less certain than a 1-day one"


# ══════════════════════════════════════════════════════════════════════════
# economics and advice
# ══════════════════════════════════════════════════════════════════════════

def test_compare_ranks_markets_by_net_and_marks_flips(crop: str):
    body = client.get(f"{BASE}/compare",
                      params={"crop": crop, "qty_qtl": 80}).json()
    assert body
    assert {"mandi", "grossPerQtl", "netPerQtl", "rankByGross",
            "rankByNet", "rankFlipped", "breakdown"} <= set(body[0])
    assert [r["rankByNet"] for r in body] == list(range(1, len(body) + 1))
    assert all(r["netPerQtl"] < r["grossPerQtl"] for r in body)


def test_compare_breakdown_starts_with_gross_then_deductions(crop: str):
    body = client.get(f"{BASE}/compare", params={"crop": crop}).json()
    lines = body[0]["breakdown"]
    assert lines[0]["kind"] == "gross"
    assert all(l["amount"] <= 0 for l in lines[1:])


def test_recommend_returns_an_actionable_plan(crop: str):
    body = client.post(f"{BASE}/recommend", json={
        "crop": crop, "qtyQtl": 80, "grade": "B",
        "storage": "ambient", "riskProfile": "balanced",
    }).json()
    assert body["action"] in ("sell_now", "hold", "split", "sell_to_procurement")
    assert body["tranches"]
    assert abs(sum(t["qtl"] for t in body["tranches"]) - 80) < 0.01
    assert "₹" in body["headline"]
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["alternativesConsidered"] > 1


def test_recommend_shape_matches_types_ts(crop: str):
    body = client.post(f"{BASE}/recommend",
                       json={"crop": crop, "qtyQtl": 40}).json()
    required = {"action", "headline", "headlineMr", "tranches", "baselineNet",
                "strategyNet", "expectedGain", "expectedGainPct", "confidence",
                "reasonText", "reasonTextMr", "constraintsApplied",
                "alternativesConsidered"}
    assert required <= set(body), f"missing: {required - set(body)}"


def test_perishable_crops_are_never_told_to_hold():
    for crop in ("tomato", "okra"):
        body = client.post(f"{BASE}/recommend",
                           json={"crop": crop, "qtyQtl": 50}).json()
        if "action" in body:
            assert body["action"] == "sell_now", f"{crop} was advised to wait"


# ══════════════════════════════════════════════════════════════════════════
# errors are readable, never a 500
# ══════════════════════════════════════════════════════════════════════════

def test_unknown_crop_is_a_readable_422_not_a_500():
    response = client.get(f"{BASE}/prices/today", params={"crop": "dragonfruit"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "insufficient_data"
    assert "dragonfruit" in body["detail"]
    assert body["hint"]


def test_unknown_mandi_is_a_readable_422():
    response = client.get(f"{BASE}/prices/series",
                          params={"crop": "onion", "mandi": "Atlantis"})
    assert response.status_code == 422
    assert "Atlantis" in response.json()["detail"]


def test_a_negative_quantity_is_rejected_by_validation():
    response = client.post(f"{BASE}/recommend", json={"crop": "onion", "qtyQtl": -5})
    assert response.status_code == 422


# ══════════════════════════════════════════════════════════════════════════
# accuracy, transparency, pooling
# ══════════════════════════════════════════════════════════════════════════

def test_accuracy_reports_the_active_version_and_real_metrics():
    body = client.get(f"{BASE}/accuracy").json()
    assert body["modelVersion"]
    assert 0.0 <= body["picp"] <= 1.0
    assert len(body["mape"]) == 4 and len(body["pinball"]) == 4
    assert all(m["model"] > 0 for m in body["pinball"])


def test_accuracy_lists_every_recorded_version():
    body = client.get(f"{BASE}/accuracy/versions").json()
    assert body
    assert sum(1 for v in body if v["isActive"]) == 1, "exactly one model may be active"


def test_transparency_and_sale_reports_respond():
    assert client.get(f"{BASE}/sale-reports").status_code == 200
    assert client.get(f"{BASE}/transparency").status_code == 200


def test_a_sale_report_round_trips(crop: str, mandi: str):
    created = client.post(f"{BASE}/sale-reports", json={
        "farmer": "Test Farmer", "village": "Testwadi", "mandi": mandi,
        "crop": crop, "qtl": 10, "quotedPerQtl": 2000, "receivedPerQtl": 1880,
        "followedAdvice": True,
    })
    assert created.status_code == 201
    body = created.json()
    assert body["gapPct"] < 0, "receiving less than quoted must show a negative gap"
    assert any(r["id"] == body["id"] for r in client.get(f"{BASE}/sale-reports").json())


@pytest.fixture
def _cleanup_pools():
    """Remove pools this module creates, so runs do not pile up in the demo."""
    from sqlalchemy import text

    from core.db import get_conn

    created: list[int] = []
    yield created
    if created:
        with get_conn() as conn:
            conn.execute(text("DELETE FROM pool_members WHERE pool_id = ANY(:ids)"),
                         {"ids": created})
            conn.execute(text("DELETE FROM transport_pools WHERE id = ANY(:ids)"),
                         {"ids": created})


def test_pool_create_join_and_leave(mandi: str, _cleanup_pools):
    created = client.post(f"{BASE}/pools", json={
        "mandi": mandi, "travelDate": "2025-11-20", "farmer": "Anil",
        "village": "Testwadi", "qtyQtl": 20,
    })
    assert created.status_code == 201
    pool = created.json()
    _cleanup_pools.append(pool["id"])
    assert pool["bookedQtl"] == 20
    alone = pool["costPerQtlAlone"]

    joined = client.post(f"{BASE}/pools/{pool['id']}/join",
                         json={"farmer": "Sunil", "village": "Testwadi",
                               "qtyQtl": 30}).json()
    assert joined["bookedQtl"] == 50
    assert joined["costPerQtlPooled"] < alone, "pooling must reduce the per-quintal cost"
    assert joined["savingPerQtl"] > 0

    member = joined["members"][-1]["id"]
    left = client.delete(f"{BASE}/pools/{pool['id']}/members/{member}").json()
    assert left["bookedQtl"] == 20, "leaving must free the capacity again"


def test_a_pool_cannot_be_overfilled(mandi: str, _cleanup_pools):
    pool = client.post(f"{BASE}/pools", json={
        "mandi": mandi, "travelDate": "2025-11-21", "farmer": "Anil",
        "qtyQtl": 80, "capacityQtl": 90,
    }).json()
    _cleanup_pools.append(pool["id"])
    response = client.post(f"{BASE}/pools/{pool['id']}/join",
                           json={"farmer": "Greedy", "qtyQtl": 50})
    assert response.status_code == 400
    assert "space left" in response.json()["detail"]
