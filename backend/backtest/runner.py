"""Phase 7 — "following our advice beats selling immediately by X%."

    python scripts/backtest.py --provider lightgbm

Rewind to a held-out period the model never trained on, sample lots, ask the
**live** decision engine what to do, then fast-forward and settle each plan
against what the price actually did.

**The runner calls the real `optimise()`.** Not a special backtest path, not a
simplified copy. If backtest and production run different code, the backtest is
fiction and the number in the deck is a lie you have not caught yet.

Two rules that keep it honest:

  * **Point-in-time forecasts.** Every plan is built from a forecast made with
    `as_of` set to the scenario's own date, so the engine never sees a price
    from after the decision it is making.
  * **Settled on real prices.** A tranche due in 7 days is valued at the actual
    modal price on that date, through the same economics engine — not at the
    forecast. Scoring a forecast against itself measures nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from core import logging as log
from core.errors import InsufficientData
from decision.engine import Lot, Plan, optimise
from economics.compare import MandiOption
from economics.net_realisation import NetInput, net_in_hand
from ml.port import DEFAULT_HORIZONS


@dataclass
class Scenario:
    """One imaginary lot, on one day, in one market."""

    as_of: date
    commodity_id: int
    crop: str
    mandi_id: int
    mandi: str
    qty_qtl: float
    grade: str = "B"
    storage: str = "ambient"
    risk_profile: str = "balanced"


@dataclass
class Outcome:
    """What the plan was worth once the real prices arrived."""

    scenario: Scenario
    action: str
    strategy_net: float
    baseline_net: float
    confidence: float
    settled: bool = True

    @property
    def uplift(self) -> float:
        return self.strategy_net - self.baseline_net

    @property
    def uplift_pct(self) -> float:
        return (self.uplift / self.baseline_net * 100.0) if self.baseline_net else 0.0

    @property
    def won(self) -> bool:
        return self.uplift > 0


@dataclass
class BacktestReport:
    provider: str
    version: str
    start: date | None = None
    end: date | None = None
    outcomes: list[Outcome] = field(default_factory=list)
    skipped: int = 0

    @property
    def scored(self) -> int:
        return len(self.outcomes)

    @property
    def uplift_pct(self) -> float:
        """Total strategy rupees versus total baseline rupees.

        Aggregated over the whole book, not averaged per scenario — a mean of
        percentages lets a tiny lot with a lucky 40% swing outweigh a large one,
        which is how a backtest flatters itself without technically lying.
        """
        baseline = sum(o.baseline_net for o in self.outcomes)
        strategy = sum(o.strategy_net for o in self.outcomes)
        return ((strategy - baseline) / baseline * 100.0) if baseline else 0.0

    @property
    def win_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        # Ties are scenarios where the engine said "sell now" and so matched the
        # baseline exactly. Counting them as wins would inflate the rate toward
        # 100% while the model contributed nothing.
        decided = [o for o in self.outcomes if abs(o.uplift) > 1e-6]
        if not decided:
            return 0.0
        return sum(1 for o in decided if o.won) / len(decided)

    @property
    def action_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for outcome in self.outcomes:
            mix[outcome.action] = mix.get(outcome.action, 0) + 1
        return mix

    def per_crop(self) -> dict[str, dict[str, float]]:
        """Uplift broken out by crop — is it broad, or one lucky crop?"""
        grouped: dict[str, list[Outcome]] = {}
        for outcome in self.outcomes:
            grouped.setdefault(outcome.scenario.crop, []).append(outcome)

        out: dict[str, dict[str, float]] = {}
        for crop, items in sorted(grouped.items()):
            baseline = sum(o.baseline_net for o in items)
            strategy = sum(o.strategy_net for o in items)
            decided = [o for o in items if abs(o.uplift) > 1e-6]
            out[crop] = {
                "n": float(len(items)),
                "uplift_pct": ((strategy - baseline) / baseline * 100.0) if baseline else 0.0,
                "win_rate": (sum(1 for o in decided if o.won) / len(decided)) if decided else 0.0,
                "rupees": strategy - baseline,
            }
        return out

    def to_metrics(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "version": self.version,
            "scenarios": self.scored,
            "uplift_pct": round(self.uplift_pct, 3),
            "win_rate": round(self.win_rate, 4),
            "action_mix": self.action_mix,
            "per_crop": {k: {m: round(v, 3) for m, v in stats.items()}
                         for k, stats in self.per_crop().items()},
            "start": str(self.start) if self.start else None,
            "end": str(self.end) if self.end else None,
        }


def _settle_price(series: Mapping[date, float], target: date,
                  tolerance_days: int = 3) -> float | None:
    """The real modal price on `target`, or the closest one just before it."""
    for offset in range(tolerance_days + 1):
        value = series.get(target - timedelta(days=offset))
        if value is not None:
            return value
    return None


def run_scenario(
    scenario: Scenario,
    provider: Any,
    options: Sequence[MandiOption],
    actuals: Mapping[str, Mapping[date, float]],
) -> Outcome | None:
    """Build a plan as of the scenario date, then settle it on what really happened."""
    try:
        forecast = provider.predict_quantiles(
            scenario.commodity_id, scenario.mandi_id, scenario.as_of, DEFAULT_HORIZONS
        )
    except InsufficientData:
        return None

    today_prices: dict[str, float] = {}
    for option in options:
        price = _settle_price(actuals.get(option.mandi, {}), scenario.as_of)
        if price is not None:
            today_prices[option.mandi] = price
    if not today_prices:
        return None

    priced = [o for o in options if o.mandi in today_prices]
    for option in priced:
        option.price_per_qtl = today_prices[option.mandi]

    lot = Lot(crop=scenario.crop, qty_qtl=scenario.qty_qtl, grade=scenario.grade,
              storage=scenario.storage, risk_profile=scenario.risk_profile)

    try:
        plan: Plan = optimise(lot, today_prices, forecast, priced, as_of=scenario.as_of)
    except (InsufficientData, ValueError):
        return None

    # ── settle every tranche on the price that actually happened ──────────
    strategy_net = 0.0
    for tranche in plan.tranches:
        target = scenario.as_of + timedelta(days=tranche.day_offset)
        realised = _settle_price(actuals.get(tranche.mandi, {}), target)
        if realised is None:
            return None                       # cannot settle honestly, so do not
        distance = next((o.distance_km for o in priced if o.mandi == tranche.mandi), 0.0)
        strategy_net += net_in_hand(NetInput(
            price_per_qtl=realised, qty_qtl=tranche.qtl,
            days_held=tranche.day_offset, distance_km=distance,
            grade=scenario.grade, storage=scenario.storage, crop=scenario.crop,
        )).net_total

    # ── the baseline: sell the whole lot today, at the best market ────────
    baseline_net = max(
        net_in_hand(NetInput(
            price_per_qtl=today_prices[o.mandi], qty_qtl=scenario.qty_qtl,
            days_held=0, distance_km=o.distance_km, grade=scenario.grade,
            storage=scenario.storage, crop=scenario.crop,
        )).net_total
        for o in priced
    )

    return Outcome(
        scenario=scenario, action=plan.action, strategy_net=strategy_net,
        baseline_net=baseline_net, confidence=plan.confidence,
    )


def run(
    scenarios: Sequence[Scenario],
    provider: Any,
    options_by_crop: Mapping[str, Sequence[MandiOption]],
    actuals_by_crop: Mapping[str, Mapping[str, Mapping[date, float]]],
    provider_name: str = "unknown",
) -> BacktestReport:
    report = BacktestReport(
        provider=provider_name,
        version=getattr(provider, "version", "unknown"),
    )
    for scenario in scenarios:
        outcome = run_scenario(
            scenario, provider,
            list(options_by_crop.get(scenario.crop, [])),
            actuals_by_crop.get(scenario.crop, {}),
        )
        if outcome is None:
            report.skipped += 1
            continue
        report.outcomes.append(outcome)

    if report.outcomes:
        dates = [o.scenario.as_of for o in report.outcomes]
        report.start, report.end = min(dates), max(dates)

    log.info("backtest_complete", provider=provider_name,
             scored=report.scored, skipped=report.skipped,
             uplift_pct=round(report.uplift_pct, 3),
             win_rate=round(report.win_rate, 4))
    return report
