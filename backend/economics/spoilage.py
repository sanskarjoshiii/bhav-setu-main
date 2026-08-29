"""Phase 5 — how much of a lot is gone after holding it for N days.

    spoilage_fraction(k_c=0.006, days=7, storage="shed", tmax=34)  ->  0.0287

Exponential decay, not linear:

    fraction lost = 1 - exp(-k_c x f_storage x f_temp x days)

**Why exponential.** Spoilage compounds. Each day a fixed *proportion* of what is
still good goes bad, not a fixed quantity — so day 10 of a hold costs less
tonnage than day 1 but the curve never quite reaches zero remaining. A linear
model would predict a lot is 100% gone at some finite day, which is both wrong
and produces nonsense in the decision engine's grid search.

`k_c` per crop lives in config/crops.yaml and is derived as
`anchor / shelf_life_days` with anchor = 0.54, so "shelf life" means exactly one
thing across all fourteen crops: the day by which 42% of an ambient lot is gone.
Onion's pair (0.006 x 90) is the original measured value, so widening from one
crop to fourteen did not silently move onion.
"""

from __future__ import annotations

import math
from typing import Mapping

from core.config import crop_specs, settings

_COST = settings.cost_model
STORAGE_FACTOR: dict[str, float] = {
    k: float(v) for k, v in _COST.storage_factor.to_dict().items()
}

#: Above this, heat starts to matter. Below it, the storage type dominates.
TEMP_THRESHOLD_C: float = 30.0

#: Each degree above the threshold adds this much to the decay rate. 4% per
#: degree: a 40C day roughly doubles the loss rate versus a 30C day, which is
#: what the shed-versus-cold-store gap is actually about in a Maharashtra May.
TEMP_SENSITIVITY: float = 0.04

#: The temperature assumed when no weather is available. Matches the TypeScript
#: engine's default so the two agree to the rupee on a lot with no forecast.
DEFAULT_TMAX_C: float = 32.0


def storage_factor(storage: str) -> float:
    """How much the storage type slows decay. Cold store is 4x better than ambient."""
    try:
        return STORAGE_FACTOR[storage]
    except KeyError:
        raise ValueError(
            f"unknown storage {storage!r} — expected one of {sorted(STORAGE_FACTOR)}"
        ) from None


def temperature_factor(tmax_c: float = DEFAULT_TMAX_C) -> float:
    """Heat multiplier. 1.0 at or below 30C, rising 4% per degree above."""
    return 1.0 + TEMP_SENSITIVITY * max(0.0, float(tmax_c) - TEMP_THRESHOLD_C)


def spoilage_fraction(
    k_c: float,
    days: float,
    storage: str = "ambient",
    tmax_c: float = DEFAULT_TMAX_C,
) -> float:
    """Fraction of the lot lost after `days`. Always in [0, 1).

    A zero-day hold loses exactly zero — the decision engine relies on that, so
    that "sell today" is never penalised for spoilage it has not incurred.
    """
    if days <= 0:
        return 0.0
    rate = float(k_c) * storage_factor(storage) * temperature_factor(tmax_c)
    return 1.0 - math.exp(-rate * float(days))


def crop_k_c(crop: str) -> float:
    """`k_c` for a crop name or key, from config/crops.yaml."""
    specs = crop_specs()
    key = str(crop).strip().lower().replace(" ", "_")
    if key in specs:
        return float(specs[key]["k_c"])
    for name, spec in specs.items():                      # tolerate "Green Chilli"
        aliases = [str(a).lower() for a in spec.get("aliases", [])]
        if key == name.lower() or str(crop).lower() in aliases:
            return float(spec["k_c"])
    raise ValueError(f"no k_c configured for crop {crop!r}")


def spoilage_for_crop(
    crop: str,
    days: float,
    storage: str = "ambient",
    tmax_c: float = DEFAULT_TMAX_C,
) -> float:
    """`spoilage_fraction` with `k_c` looked up by crop name."""
    return spoilage_fraction(crop_k_c(crop), days, storage, tmax_c)


def max_hold_days(crop: str) -> int:
    """The hard ceiling from crops.yaml. The decision engine may never exceed it."""
    specs = crop_specs()
    key = str(crop).strip().lower().replace(" ", "_")
    spec: Mapping[str, object] | None = specs.get(key)
    if spec is None:
        for name, candidate in specs.items():
            aliases = [str(a).lower() for a in candidate.get("aliases", [])]
            if key == name.lower() or str(crop).lower() in aliases:
                spec = candidate
                break
    if spec is None:
        raise ValueError(f"no crop config for {crop!r}")
    return int(spec["max_hold_days"])
