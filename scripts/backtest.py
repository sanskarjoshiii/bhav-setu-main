"""Phase 7 — the one number that matters, computed and written up.

    python scripts/backtest.py                        # the active provider
    python scripts/backtest.py --provider baseline    # the opponent
    python scripts/backtest.py --holdout-days 120

Samples lots across a held-out tail, runs each through the **live** decision
engine, settles them on prices that actually happened, and writes
`data/artifacts/backtest.md`.

The held-out window ends `max(horizons)` days before the last observation we
have, because a 15-day plan made on the final day has nothing to settle against.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from typing import Any

import _bootstrap  # noqa: F401  (sys.path side effect)

from sqlalchemy import text

from api import deps
from backtest.runner import BacktestReport, Scenario, run
from core.config import settings
from core.db import get_conn
from core.logging import configure_stdout_utf8
from economics.compare import MandiOption
from ml import registry
from ml.port import DEFAULT_HORIZONS
from ml.provider import active_provider_name, get_provider

configure_stdout_utf8()

REPORT_PATH = settings.path("data", "artifacts", "backtest.md")

#: Lot sizes to sample, in quintals. Deliberately spread: a 10-quintal lot pays
#: for a whole truck and a 180-quintal one needs two, and those are different
#: economics rather than the same answer scaled.
LOT_SIZES: tuple[float, ...] = (10.0, 25.0, 60.0, 90.0, 180.0)
RISK_PROFILES: tuple[str, ...] = ("cautious", "balanced", "aggressive")


def load_actuals(commodity_id: int) -> dict[str, dict[date, float]]:
    """Every real modal price for this crop, by market and date."""
    with get_conn() as conn:
        rows = conn.execute(text("""
            SELECT m.name AS mandi, p.obs_date, p.modal_price
            FROM price_observations p JOIN mandis m ON m.id = p.mandi_id
            WHERE p.commodity_id = :c AND p.modal_price IS NOT NULL
              AND NOT p.is_imputed
        """), {"c": commodity_id}).mappings().all()
    out: dict[str, dict[date, float]] = {}
    for row in rows:
        out.setdefault(str(row["mandi"]), {})[row["obs_date"]] = float(row["modal_price"])
    return out


def build_scenarios(crops: list[str], holdout_days: int, per_crop: int,
                    seed: int) -> tuple[list[Scenario], dict, dict]:
    rng = random.Random(seed)
    scenarios: list[Scenario] = []
    options_by_crop: dict[str, list[MandiOption]] = {}
    actuals_by_crop: dict[str, dict[str, dict[date, float]]] = {}

    horizon = max(DEFAULT_HORIZONS)

    for crop in crops:
        try:
            commodity_id, crop_name = deps.resolve_commodity(crop)
        except Exception:                                  # noqa: BLE001
            continue

        actuals = load_actuals(commodity_id)
        if not actuals:
            continue

        options = deps.mandi_options(commodity_id)
        if not options:
            continue

        last = deps.latest_observation_date(commodity_id)
        # Leave room for the longest plan to settle.
        window_end = last - timedelta(days=horizon + 2)
        window_start = window_end - timedelta(days=holdout_days)

        candidates = sorted({
            day
            for series in actuals.values()
            for day in series
            if window_start <= day <= window_end
        })
        if len(candidates) < 10:
            continue

        key = crop_name.lower().replace(" ", "_")
        options_by_crop[key] = options
        actuals_by_crop[key] = actuals

        for _ in range(per_crop):
            anchor = rng.choice(options)
            scenarios.append(Scenario(
                as_of=rng.choice(candidates),
                commodity_id=commodity_id,
                crop=key,
                mandi_id=anchor.mandi_id or 0,
                mandi=anchor.mandi,
                qty_qtl=rng.choice(LOT_SIZES),
                grade=rng.choice(("A", "B", "B", "C")),
                storage=rng.choice(("ambient", "ambient", "shed", "cold_store")),
                risk_profile=rng.choice(RISK_PROFILES),
            ))

    return scenarios, options_by_crop, actuals_by_crop


def write_report(report: BacktestReport) -> None:
    per_crop = report.per_crop()
    lines: list[str] = [
        "# Backtest — following our advice versus selling immediately",
        "",
        f"> Provider **{report.provider}**, version **{report.version}**.",
        f"> Held-out period {report.start} → {report.end}, "
        f"{report.scored:,} scenarios settled on prices that actually happened "
        f"({report.skipped:,} skipped for want of a settleable price).",
        "",
        "Every plan was built by the **live** `optimise()` — the same function the "
        "API calls — with the forecast made as of the scenario's own date, then "
        "valued at the real modal price on the day each tranche came due.",
        "",
        "## Headline",
        "",
        f"| Metric | Value |",
        f"|---|---:|",
        f"| **Uplift** | **{report.uplift_pct:+.2f}%** |",
        f"| Win rate | {report.win_rate:.1%} |",
        f"| Scenarios | {report.scored:,} |",
        "",
        "Uplift is total strategy rupees over total baseline rupees across the "
        "whole book — not a mean of percentages, which would let one lucky small "
        "lot outweigh a large one.",
        "",
        "## What it recommended",
        "",
        "| Action | Scenarios |",
        "|---|---:|",
    ]
    for action, count in sorted(report.action_mix.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {action} | {count:,} |")

    lines += ["", "## Per crop", "",
              "| Crop | Scenarios | Uplift | Win rate | ₹ |",
              "|---|---:|---:|---:|---:|"]
    for crop, stats in sorted(per_crop.items(), key=lambda kv: -kv[1]["uplift_pct"]):
        lines.append(
            f"| {crop} | {stats['n']:.0f} | {stats['uplift_pct']:+.2f}% | "
            f"{stats['win_rate']:.0%} | {stats['rupees']:+,.0f} |"
        )

    lines += [
        "",
        "## How to read this honestly",
        "",
        "- A win rate near 100% is a **bug**, not a triumph. Ties — scenarios where "
        "the engine said sell-now and therefore matched the baseline exactly — are "
        "excluded from the rate rather than counted as wins.",
        "- Check the per-crop table: uplift concentrated in one crop is luck, "
        "uplift spread across many is a signal.",
        "- The held-out window ends "
        f"{max(DEFAULT_HORIZONS)} days before the last observation, so every plan "
        "has a real price to settle against.",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the decision engine.")
    parser.add_argument("--provider", default=None,
                        help="baseline | lightgbm (default: whatever is active)")
    parser.add_argument("--crops", nargs="+", default=None)
    parser.add_argument("--holdout-days", type=int, default=180)
    parser.add_argument("--per-crop", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--record", action="store_true",
                        help="write the uplift into the model's model_registry row")
    args = parser.parse_args(argv)

    provider_name = args.provider or active_provider_name()
    provider = get_provider(args.provider)

    crops = args.crops or [c["name"] for c in deps.list_commodities(with_data_only=True)]
    print(f"\nBacktest — provider {provider_name}, {len(crops)} crops, "
          f"{args.holdout_days}-day held-out window\n")

    scenarios, options, actuals = build_scenarios(
        crops, args.holdout_days, args.per_crop, args.seed)
    if not scenarios:
        print("⛔ no scenarios could be built — is there enough price history?")
        return 1
    print(f"  {len(scenarios):,} scenarios built. Running them through the live engine…\n")

    report = run(scenarios, provider, options, actuals, provider_name)
    if not report.outcomes:
        print("⛔ nothing settled. Every scenario lacked a real price to value against.")
        return 1

    write_report(report)

    print(f"  {'uplift':<14}{report.uplift_pct:+.2f}%")
    print(f"  {'win rate':<14}{report.win_rate:.1%}")
    print(f"  {'scenarios':<14}{report.scored:,} scored, {report.skipped:,} skipped")
    print(f"  {'actions':<14}{report.action_mix}")
    print("\n  per crop")
    print("  " + "─" * 52)
    for crop, stats in sorted(report.per_crop().items(),
                              key=lambda kv: -kv[1]["uplift_pct"]):
        print(f"  {crop:<16}{stats['n']:>5.0f}  {stats['uplift_pct']:>+7.2f}%  "
              f"win {stats['win_rate']:>5.0%}")

    print(f"\n  report → {REPORT_PATH}")

    if args.record:
        active = registry.active()
        if active and active["version"] == getattr(provider, "version", None):
            metrics = active.get("metrics") or {}
            if isinstance(metrics, str):
                metrics = json.loads(metrics)
            metrics["backtest"] = report.to_metrics()
            registry.record(
                str(active["version"]), algo=str(active.get("algo") or "unknown"),
                metrics=metrics, artifact_path=active.get("artifact_path"),
            )
            print(f"  recorded uplift into model_registry.{active['version']}")
        else:
            print("  ⚠️  not recorded: this provider is not the active version")

    if report.uplift_pct <= 0:
        print("\n  ⚠️  Uplift is not positive. That is a finding, not a failure —")
        print("      it means the decision engine is not beating 'sell today' on")
        print("      this data. Read the per-crop table before changing anything.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
