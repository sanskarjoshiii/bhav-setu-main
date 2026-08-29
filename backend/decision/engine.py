"""Phase 6 — forecasts + economics → one imperative sentence with a rupee figure.

    optimise(Lot(crop="onion", qty_qtl=80, ...), provider, mandis)
    -> Plan(action="split", headline="Sell 40 qtl today at Pune, hold 40 for 7 days",
            expected_gain=18_420, confidence=0.71, ...)

It tries every combination of **sell fraction × hold days × market**, prices each
one through the real economics engine, and picks the best score. Then the six
hard rules in `constraints.py` override it where the maths would be dangerous.

**Model-independent by construction.** It consumes `dict[int, Quantiles]` and
nothing else, so it never learns whether a baseline or a booster fed it. Swapping
the model changes the numbers, not this file.

---

## The convexity fix — read this before changing the scoring

Scoring must be **convex** in the sell fraction:

    exposure = later_qty / lot.qty_qtl
    score    = e_net - risk_lambda * downside * exposure

A flat `e_net - λ × downside` is **linear** in the fraction, so the maximum
always sits at a corner — sell everything or hold everything — and a split
becomes mathematically unreachable. The whole product is built on splits.

We shipped that bug in Round 1. It passed 47 tests, because every test asked
"is the answer sensible?" and "sell everything" always is. Nothing asked "can
this function ever return a split?". `test_phase6_decision.py` now does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from core.config import settings
from core.errors import InsufficientData
from decision import confidence as conf
from decision import constraints as rules
from economics.compare import MandiOption
from economics.net_realisation import (
    TRUCK_CAPACITY_QTL,
    Grade,
    NetInput,
    Storage,
    net_in_hand,
)
from ml.port import Quantiles

_D = settings.decision
SELL_FRACTIONS: list[float] = [float(f) for f in _D.sell_fractions]
HOLD_HORIZONS: list[int] = [int(h) for h in _D.hold_horizons]
RISK_LAMBDA: dict[str, float] = {k: float(v) for k, v in _D.risk_lambda.to_dict().items()}
DEFAULT_RISK: str = str(_D.default_risk_profile)


@dataclass(frozen=True)
class Lot:
    """What the farmer has, and how he feels about risk."""

    crop: str
    qty_qtl: float
    grade: Grade = "B"
    storage: Storage = "ambient"
    risk_profile: str = DEFAULT_RISK
    state: str = "Maharashtra"


@dataclass
class Tranche:
    """One instruction: sell this much, on this day, at this market."""

    qtl: float
    when: str
    day_offset: int
    mandi: str
    net_per_qtl: float
    range_low: float
    range_high: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "qtl": round(self.qtl, 2),
            "when": self.when,
            "dayOffset": self.day_offset,
            "mandi": self.mandi,
            "netPerQtl": round(self.net_per_qtl, 2),
            "rangeLow": round(self.range_low, 2),
            "rangeHigh": round(self.range_high, 2),
        }


@dataclass
class Plan:
    """The recommendation. Mirrors Recommendation in frontend/lib/types.ts."""

    action: str                       # sell_now | hold | split
    headline: str
    headline_mr: str
    tranches: list[Tranche]
    baseline_net: float
    strategy_net: float
    expected_gain: float
    expected_gain_pct: float
    confidence: float
    reason_text: str
    reason_text_mr: str
    constraints_applied: list[str] = field(default_factory=list)
    alternatives_considered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "headline": self.headline,
            "headlineMr": self.headline_mr,
            "tranches": [t.to_dict() for t in self.tranches],
            "baselineNet": round(self.baseline_net, 2),
            "strategyNet": round(self.strategy_net, 2),
            "expectedGain": round(self.expected_gain, 2),
            "expectedGainPct": round(self.expected_gain_pct, 2),
            "confidence": round(self.confidence, 3),
            "reasonText": self.reason_text,
            "reasonTextMr": self.reason_text_mr,
            "constraintsApplied": self.constraints_applied,
            "alternativesConsidered": self.alternatives_considered,
        }


@dataclass
class _Candidate:
    sell_fraction: float
    hold_days: int
    now_mandi: MandiOption
    later_mandi: MandiOption
    score: float
    expected_net: float
    now_net_per_qtl: float
    later_net_per_qtl: float
    later_low_per_qtl: float
    later_high_per_qtl: float


def _net(price: float, qty: float, lot: Lot, distance_km: float,
         days: float, tmax_c: float | None) -> float:
    if qty <= 0:
        return 0.0
    return net_in_hand(NetInput(
        price_per_qtl=price, qty_qtl=qty, days_held=days, distance_km=distance_km,
        grade=lot.grade, storage=lot.storage, crop=lot.crop, state=lot.state,
        **({"tmax_c": tmax_c} if tmax_c is not None else {}),
    )).net_total


def optimise(
    lot: Lot,
    today_prices: Mapping[str, float],
    forecast: Mapping[int, Quantiles],
    mandis: Sequence[MandiOption],
    *,
    as_of: date | None = None,
    relative_width: float | None = None,
    data_quality: float = 1.0,
    days_since_observation: float = 0.0,
    hit_rate: float | None = None,
    shock_magnitude: float = 0.0,
    shock_days_since: float = 999.0,
    shock_bearish: bool = False,
    tmax_c: float | None = None,
    reason: str = "",
    reason_mr: str = "",
) -> Plan:
    """Grid-search sell fraction × hold days × market, then apply the hard rules."""
    if not mandis:
        raise InsufficientData("no markets to compare — cannot build a recommendation")
    if lot.qty_qtl <= 0:
        raise ValueError("qty_qtl must be positive")

    as_of = as_of or date.today()
    horizons = sorted(h for h in forecast if h > 0)
    if not horizons:
        raise InsufficientData("forecast carries no horizons")

    # ── confidence, from the band we were handed ──────────────────────────
    reference = forecast[min(horizons, key=lambda h: abs(h - 7))]
    width = relative_width if relative_width is not None else reference.relative_width
    confidence = conf.score(width, data_quality, days_since_observation, hit_rate)

    # ── the six hard rules ────────────────────────────────────────────────
    usable_horizons = [h for h in HOLD_HORIZONS if h in forecast]
    fired = [
        rules.perishability_cap(lot.crop),
        rules.spoilage_cap(lot.crop, lot.storage, usable_horizons or horizons, tmax_c),
        rules.wide_band_cap(width),
        rules.low_confidence_cap(confidence),
        rules.shock_override(shock_magnitude, shock_days_since, shock_bearish),
    ]
    nearest = min(mandis, key=lambda m: m.distance_km)
    fired.append(rules.transport_viability(lot.qty_qtl, nearest.distance_km, TRUCK_CAPACITY_QTL))
    hold_cap, sell_floor = rules.apply(fired)
    applied = [c.name for c in fired if c is not None]
    rule_reasons = [c for c in fired if c is not None and (
        c.max_hold_days is not None or c.min_sell_fraction is not None)]

    allowed_holds = [h for h in usable_horizons
                     if hold_cap is None or h <= hold_cap]
    allowed_fractions = [f for f in SELL_FRACTIONS if f >= sell_floor] or [1.0]

    risk_lambda = RISK_LAMBDA.get(lot.risk_profile, RISK_LAMBDA[DEFAULT_RISK])

    # ── the grid ──────────────────────────────────────────────────────────
    candidates: list[_Candidate] = []
    considered = 0

    for now_mandi in mandis:
        now_price = today_prices.get(now_mandi.mandi, now_mandi.price_per_qtl)
        for sell_fraction in allowed_fractions:
            now_qty = lot.qty_qtl * sell_fraction
            later_qty = lot.qty_qtl - now_qty

            if later_qty <= 1e-9:                       # sell everything today
                considered += 1
                now_net = _net(now_price, now_qty, lot, now_mandi.distance_km, 0, tmax_c)
                candidates.append(_Candidate(
                    sell_fraction=1.0, hold_days=0,
                    now_mandi=now_mandi, later_mandi=now_mandi,
                    score=now_net, expected_net=now_net,
                    now_net_per_qtl=now_net / max(now_qty, 1e-9),
                    later_net_per_qtl=0.0, later_low_per_qtl=0.0, later_high_per_qtl=0.0,
                ))
                continue

            for hold_days in allowed_holds:
                band = forecast[hold_days]
                for later_mandi in mandis:
                    considered += 1
                    now_net = _net(now_price, now_qty, lot, now_mandi.distance_km, 0, tmax_c)
                    mid = _net(band.p50, later_qty, lot, later_mandi.distance_km,
                               hold_days, tmax_c)
                    low = _net(band.p10, later_qty, lot, later_mandi.distance_km,
                               hold_days, tmax_c)
                    high = _net(band.p90, later_qty, lot, later_mandi.distance_km,
                                hold_days, tmax_c)

                    expected_net = now_net + mid
                    downside = max(0.0, mid - low)

                    # ── THE CONVEXITY FIX ──────────────────────────────
                    # Scaling the risk penalty by how much of the lot is still
                    # exposed makes the objective curve in the sell fraction, so
                    # an interior optimum — a split — can actually win. Without
                    # `exposure` this is linear and only corners can win.
                    exposure = later_qty / lot.qty_qtl
                    score = expected_net - risk_lambda * downside * exposure

                    candidates.append(_Candidate(
                        sell_fraction=sell_fraction, hold_days=hold_days,
                        now_mandi=now_mandi, later_mandi=later_mandi,
                        score=score, expected_net=expected_net,
                        now_net_per_qtl=now_net / max(now_qty, 1e-9),
                        later_net_per_qtl=mid / later_qty,
                        later_low_per_qtl=low / later_qty,
                        later_high_per_qtl=high / later_qty,
                    ))

    best = max(candidates, key=lambda c: c.score)

    # ── the baseline we are beating: sell everything today, best market ───
    baseline_net = max(
        _net(today_prices.get(m.mandi, m.price_per_qtl), lot.qty_qtl, lot,
             m.distance_km, 0, tmax_c)
        for m in mandis
    )

    # ── turn the winner into instructions ─────────────────────────────────
    tranches: list[Tranche] = []
    now_qty = lot.qty_qtl * best.sell_fraction
    later_qty = lot.qty_qtl - now_qty

    if now_qty > 1e-9:
        tranches.append(Tranche(
            qtl=now_qty, when="today", day_offset=0, mandi=best.now_mandi.mandi,
            net_per_qtl=best.now_net_per_qtl,
            range_low=best.now_net_per_qtl, range_high=best.now_net_per_qtl,
        ))
    if later_qty > 1e-9:
        target = as_of + timedelta(days=best.hold_days)
        tranches.append(Tranche(
            qtl=later_qty, when=f"in {best.hold_days} days ({target:%d %b})",
            day_offset=best.hold_days, mandi=best.later_mandi.mandi,
            net_per_qtl=best.later_net_per_qtl,
            range_low=best.later_low_per_qtl, range_high=best.later_high_per_qtl,
        ))

    if not tranches:                                     # cannot happen; be safe
        raise InsufficientData("optimiser produced no tranches")

    action = ("sell_now" if later_qty <= 1e-9
              else "hold" if now_qty <= 1e-9
              else "split")

    expected_gain = best.expected_net - baseline_net
    expected_gain_pct = (expected_gain / baseline_net * 100.0) if baseline_net else 0.0

    headline, headline_mr = _headline(action, tranches, lot, best)
    if not reason:
        reason, reason_mr = _fallback_reason(action, rule_reasons, best, width)

    return Plan(
        action=action,
        headline=headline,
        headline_mr=headline_mr,
        tranches=tranches,
        baseline_net=baseline_net,
        strategy_net=best.expected_net,
        expected_gain=expected_gain,
        expected_gain_pct=expected_gain_pct,
        confidence=confidence,
        reason_text=reason,
        reason_text_mr=reason_mr,
        constraints_applied=applied,
        alternatives_considered=considered,
    )


#: Crop names in Devanagari. A Marathi sentence with "onion" in the middle of it
#: is not a Marathi sentence, and this is the headline a farmer actually reads.
CROP_MR: dict[str, str] = {
    "onion": "कांदा", "potato": "बटाटा", "tomato": "टोमॅटो", "garlic": "लसूण",
    "brinjal": "वांगी", "cauliflower": "फुलकोबी", "green_chilli": "हिरवी मिरची",
    "okra": "भेंडी", "banana": "केळी", "mango": "आंबा", "grapes": "द्राक्षे",
    "orange": "संत्रा", "pomegranate": "डाळिंब", "cabbage": "कोबी",
}


def _headline(action: str, tranches: Sequence[Tranche], lot: Lot,
              best: _Candidate) -> tuple[str, str]:
    crop = lot.crop.replace("_", " ")
    crop_mr = CROP_MR.get(lot.crop.lower(), crop)
    if action == "sell_now":
        t = tranches[0]
        return (
            f"Sell all {t.qtl:.0f} qtl of {crop} today at {t.mandi} — "
            f"about ₹{t.net_per_qtl:,.0f}/qtl in hand",
            f"सर्व {t.qtl:.0f} क्विंटल {crop_mr} आजच {t.mandi} येथे विका — "
            f"हातात सुमारे ₹{t.net_per_qtl:,.0f}/क्विंटल",
        )
    if action == "hold":
        t = tranches[0]
        return (
            f"Hold all {t.qtl:.0f} qtl of {crop} for {t.day_offset} days, then sell "
            f"at {t.mandi} — about ₹{t.net_per_qtl:,.0f}/qtl in hand",
            f"सर्व {t.qtl:.0f} क्विंटल {crop_mr} {t.day_offset} दिवस थांबवा, नंतर "
            f"{t.mandi} येथे विका — सुमारे ₹{t.net_per_qtl:,.0f}/क्विंटल",
        )
    now, later = tranches[0], tranches[1]
    return (
        f"Sell {now.qtl:.0f} qtl today at {now.mandi}, hold {later.qtl:.0f} qtl "
        f"for {later.day_offset} days — about ₹{later.net_per_qtl:,.0f}/qtl on the rest",
        f"{now.qtl:.0f} क्विंटल आज {now.mandi} येथे विका, {later.qtl:.0f} क्विंटल "
        f"{later.day_offset} दिवस थांबवा — उरलेल्यावर सुमारे ₹{later.net_per_qtl:,.0f}/क्विंटल",
    )


def _fallback_reason(action: str, fired: Sequence[rules.Constraint],
                     best: _Candidate, width: float) -> tuple[str, str]:
    """A plain sentence when no feature-driven explanation was supplied."""
    if fired:
        return fired[0].reason, fired[0].reason_mr
    if action == "sell_now":
        return ("today's price already gives the best net return after costs",
                "खर्च वजा जाता आजचा भाव सर्वोत्तम आहे")
    if action == "hold":
        return (f"the forecast expects a better price in {best.hold_days} days, "
                f"and the crop can safely wait that long",
                f"{best.hold_days} दिवसांत भाव वाढण्याची शक्यता आहे")
    return (f"splitting protects against a bad week while keeping some upside "
            f"({width:.0%} forecast range)",
            "अर्धा माल आज विकल्याने धोका कमी होतो, उरलेल्यावर फायदा मिळू शकतो")
