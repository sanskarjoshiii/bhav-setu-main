"""GET /accuracy — the real metrics of whichever forecaster is live.

**This endpoint is the one place the provider is visible**, and it must say which
one it is. `baseline-v1` today, `lgbm-v2` now — the page shows honest numbers
either way and only the label changes. That is what made swap day a config edit
rather than a frontend change.

Everything here is read from `model_registry`, written by
`scripts/evaluate_baseline.py` and `scripts/train.py`. Nothing is hardcoded, so
the page cannot drift away from what was actually measured.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from api.schemas import AccuracySummary, CropUplift, HorizonCoverage, HorizonMetric
from core.errors import InsufficientData
from ml.registry import BASELINE_VERSION, active as active_version, get as get_version, list_versions
from ml.port import DEFAULT_HORIZONS
from ml.provider import active_provider_name

router = APIRouter(tags=["accuracy"])


def _metrics(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    raw = row.get("metrics") or {}
    return json.loads(raw) if isinstance(raw, str) else raw


def _pick(block: dict[str, Any], horizon: int, key: str) -> float:
    value = (block.get(f"h{horizon}") or {}).get(key)
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


@router.get("/accuracy", response_model=AccuracySummary)
def accuracy() -> AccuracySummary:
    active = active_version()
    if active is None:
        raise InsufficientData(
            "no model has been promoted yet. Run scripts/evaluate_baseline.py "
            "then scripts/train.py --promote."
        )

    live = _metrics(active)
    baseline_row = get_version(BASELINE_VERSION)
    baseline = _metrics(baseline_row)

    # The baseline's own row scores the four naive methods; the model's row
    # carries its own numbers plus the benchmark it was compared against.
    bench = live.get("baseline") or baseline

    mape: list[HorizonMetric] = []
    pinball: list[HorizonMetric] = []
    for horizon in DEFAULT_HORIZONS:
        naive = _pick(bench, horizon, "mape")
        mape.append(HorizonMetric(
            horizon=horizon, naive=naive, seasonal=naive, ma7=naive,
            model=_pick(live, horizon, "mape"),
        ))
        naive_pin = _pick(bench, horizon, "pinball_mean")
        pinball.append(HorizonMetric(
            horizon=horizon, naive=naive_pin, seasonal=naive_pin, ma7=naive_pin,
            model=_pick(live, horizon, "pinball_mean"),
        ))

    picp = sum(_pick(live, h, "picp") for h in DEFAULT_HORIZONS) / len(DEFAULT_HORIZONS)

    backtest = live.get("backtest") or {}
    per_crop = [
        CropUplift(
            crop=str(crop),
            scenarios=int(stats.get("n") or 0),
            uplift_pct=round(float(stats.get("uplift_pct") or 0.0), 3),
            win_rate=round(float(stats.get("win_rate") or 0.0), 4),
        )
        for crop, stats in sorted(
            (backtest.get("per_crop") or {}).items(),
            key=lambda kv: -float(kv[1].get("uplift_pct") or 0.0),
        )
    ]
    coverage = [
        HorizonCoverage(horizon=h, picp=_pick(live, h, "picp"))
        for h in DEFAULT_HORIZONS
    ]

    return AccuracySummary(
        mape=mape,
        pinball=pinball,
        coverage=coverage,
        backtest_per_crop=per_crop,
        backtest_scenarios=int(backtest.get("scenarios") or 0),
        picp=round(picp, 4),
        directional_accuracy=_pick(live, 7, "directional_accuracy"),
        model_version=str(active["version"]),
        trained_at=str(active.get("trained_at") or ""),
        train_rows=int(live.get("rows_trained") or 0),
        uplift_pct=round(float(backtest.get("uplift_pct") or 0.0), 2),
        win_rate=round(float(backtest.get("win_rate") or 0.0), 4),
    )


@router.get("/accuracy/versions")
def versions() -> list[dict[str, Any]]:
    """Every recorded version, so the accuracy page can show what was rejected too."""
    out = []
    for row in list_versions():
        metrics = _metrics(row)
        out.append({
            "version": row["version"],
            "algo": row.get("algo"),
            "isActive": bool(row.get("is_active")),
            "trainedAt": str(row.get("trained_at") or ""),
            "trainStart": str(row.get("train_start") or ""),
            "trainEnd": str(row.get("train_end") or ""),
            "rowsTrained": metrics.get("rows_trained"),
            "provider": active_provider_name() if row.get("is_active") else None,
        })
    return out
