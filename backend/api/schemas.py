"""Phase 8 — Pydantic models that mirror frontend/lib/types.ts exactly.

Field names are **camelCase on the wire** because that is what the TypeScript
types already say, and `frontend/lib/api.ts` was written so that swapping mock
for fetch touches one file and no components. Python stays snake_case internally;
`alias_generator` does the translation at the boundary.

If a field name here disagrees with types.ts, the website breaks silently — a
missing field becomes `undefined` and renders as blank rather than throwing. So
`test_phase8_api.py` asserts the two agree, field by field.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


class Wire(BaseModel):
    """Base: camelCase out, either form accepted in."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


Grade = Literal["A", "B", "C"]
Storage = Literal["ambient", "shed", "cold_store"]
RiskProfile = Literal["cautious", "balanced", "aggressive"]


# ── reference data ────────────────────────────────────────────────────────

class Mandi(Wire):
    id: int
    name: str
    name_mr: str = ""
    district: str
    lat: float
    lon: float
    distance_km: float = 0.0
    today_modal: float = 0.0
    change_pct: float = 0.0
    arrival_qtl: float = 0.0
    liquidity: Literal["high", "medium", "low"] = "medium"


class Crop(Wire):
    id: int
    key: str
    name: str
    name_mr: str = ""
    group: str = ""
    perishability_class: int = 3
    shelf_life_days: int = 30
    max_hold_days: int = 7
    has_forecast: bool = True


class District(Wire):
    name: str
    mandi_count: int
    crop_count: int


# ── prices and forecasts ──────────────────────────────────────────────────

class PricePoint(Wire):
    date: str
    modal: float | None = None
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    is_forecast: bool = False


class TodayPrice(Wire):
    mandi: str
    mandi_id: int
    district: str
    crop: str
    crop_id: int
    modal: float
    min_price: float | None = None
    max_price: float | None = None
    arrival_qtl: float | None = None
    change_pct: float = 0.0
    obs_date: str
    #: Real observations in the forecast lookback window. The UI uses this to
    #: chart a market we can actually forecast rather than merely the dearest
    #: one — four of our fifty-one crop/market pairs are too thin, and landing
    #: on one of those made the product look broken when it was being honest.
    observations: int = 0
    can_forecast: bool = True


class ForecastResponse(Wire):
    crop: str
    mandi: str
    as_of: str
    provider: str
    model_version: str
    series: list[PricePoint] = Field(default_factory=list)


# ── economics ─────────────────────────────────────────────────────────────

class CostLine(Wire):
    label: str
    label_mr: str
    amount: float
    kind: Literal["gross", "deduction"]


class MandiComparison(Wire):
    mandi: str
    distance_km: float
    gross_per_qtl: float
    net_per_qtl: float
    rank_by_gross: int
    rank_by_net: int
    rank_flipped: bool
    breakdown: list[CostLine] = Field(default_factory=list)


# ── recommendation ────────────────────────────────────────────────────────

class Tranche(Wire):
    qtl: float
    when: str
    day_offset: int
    mandi: str
    net_per_qtl: float
    range_low: float
    range_high: float


class Recommendation(Wire):
    action: Literal["sell_now", "hold", "split", "sell_to_procurement"]
    headline: str
    headline_mr: str
    tranches: list[Tranche] = Field(default_factory=list)
    baseline_net: float
    strategy_net: float
    expected_gain: float
    expected_gain_pct: float
    confidence: float
    reason_text: str
    reason_text_mr: str
    constraints_applied: list[str] = Field(default_factory=list)
    alternatives_considered: int = 0


class RecommendRequest(Wire):
    crop: str
    qty_qtl: float = Field(gt=0)
    grade: Grade = "B"
    storage: Storage = "ambient"
    risk_profile: RiskProfile = "balanced"
    district: str | None = None
    mandi: str | None = None
    village_lat: float | None = None
    village_lon: float | None = None


# ── accuracy ──────────────────────────────────────────────────────────────

class HorizonMetric(Wire):
    horizon: int
    naive: float
    seasonal: float
    ma7: float
    model: float


class CropUplift(Wire):
    crop: str
    scenarios: int
    uplift_pct: float
    win_rate: float


class HorizonCoverage(Wire):
    horizon: int
    picp: float


class AccuracySummary(Wire):
    mape: list[HorizonMetric] = Field(default_factory=list)
    pinball: list[HorizonMetric] = Field(default_factory=list)
    #: Real band coverage per horizon — the honesty curve on the accuracy page.
    coverage: list[HorizonCoverage] = Field(default_factory=list)
    #: Real backtest, per crop. Empty until scripts/backtest.py --record has run.
    backtest_per_crop: list[CropUplift] = Field(default_factory=list)
    backtest_scenarios: int = 0
    picp: float
    directional_accuracy: float
    model_version: str
    trained_at: str
    train_rows: int
    uplift_pct: float
    win_rate: float


# ── transparency and history ──────────────────────────────────────────────

class SaleReport(Wire):
    id: str
    farmer: str
    village: str
    mandi: str
    date: str
    qtl: float
    quoted_per_qtl: float
    received_per_qtl: float
    gap_pct: float
    followed_advice: bool
    verification: Literal["self_reported", "slip_photo", "fpo_verified"]


class SaleReportRequest(Wire):
    farmer: str = "Anonymous"
    village: str = ""
    mandi: str
    crop: str
    qtl: float = Field(gt=0)
    quoted_per_qtl: float = Field(gt=0)
    received_per_qtl: float = Field(gt=0)
    followed_advice: bool = False
    verification: Literal["self_reported", "slip_photo", "fpo_verified"] = "self_reported"


class TransparencyScore(Wire):
    mandi: str
    reports: int
    median_gap_pct: float
    score: float
    trend: Literal["up", "down", "flat"]


class HistoryEntry(Wire):
    id: str
    crop: str
    qty_qtl: float
    mandi: str
    action: str
    created_at: str
    expected_gain: float = 0.0
    confidence: float = 0.0


# ── community pooling ─────────────────────────────────────────────────────

class PoolMember(Wire):
    id: int
    farmer: str
    village: str
    qty_qtl: float


class TransportPool(Wire):
    id: int
    mandi: str
    district: str
    travel_date: str
    capacity_qtl: float
    booked_qtl: float
    members: list[PoolMember] = Field(default_factory=list)
    distance_km: float
    total_cost: float
    cost_per_qtl_alone: float
    cost_per_qtl_pooled: float
    saving_per_qtl: float
    is_full: bool


class PoolCreateRequest(Wire):
    mandi: str
    travel_date: str
    farmer: str
    village: str = ""
    qty_qtl: float = Field(gt=0)
    capacity_qtl: float | None = None


class PoolJoinRequest(Wire):
    farmer: str
    village: str = ""
    qty_qtl: float = Field(gt=0)


# ── errors ────────────────────────────────────────────────────────────────

class ErrorResponse(Wire):
    detail: str
    code: str = "error"
    hint: str | None = None


class Health(Wire):
    status: str
    database: bool
    provider: str
    model_version: str
    crops: int
    mandis: int
    price_rows: int
