"""Phase 6 — how much should the farmer trust this recommendation?

Three things decide it, weighted from config/decision.yaml:

  * **band tightness** (0.5) — a narrow p10-p90 means the model is sure
  * **data quality** (0.2) — how much of the recent history is real vs filled in
  * **historical hit rate** (0.3) — how often this crop's forecasts have landed

Confidence is not decoration. Below `confidence_floor` the constraints force at
least half the lot to be sold today, so this number changes the advice rather
than just colouring a dial.
"""

from __future__ import annotations

import math

from core.config import settings

_W = settings.decision.confidence_weights
W_BAND: float = float(_W.band_tightness)
W_QUALITY: float = float(_W.data_quality)
W_HIT_RATE: float = float(_W.historical_hit_rate)
BAND_SCALE: float = float(_W.band_tightness_scale)

#: Used when a crop has no recorded track record yet. Deliberately mediocre —
#: an unknown forecaster should not inherit the confidence of a good one.
DEFAULT_HIT_RATE: float = 0.60


def band_score(relative_width: float) -> float:
    """1.0 for a pinpoint forecast, decaying as the band widens.

    exp(-width / 0.30): a 30% band scores 0.37, a 10% band scores 0.72. The scale
    is in config because it is the one number here worth tuning against real
    farmer feedback rather than theory.
    """
    if not math.isfinite(relative_width) or relative_width < 0:
        return 0.0
    return math.exp(-relative_width / BAND_SCALE)


def quality_score(data_quality: float, days_since_observation: float) -> float:
    """Real-data share, penalised for staleness.

    A price from six days ago is a worse basis than one from this morning, even
    if every row behind it is genuine.
    """
    freshness = math.exp(-max(0.0, days_since_observation) / 7.0)
    return max(0.0, min(1.0, float(data_quality))) * freshness


def score(
    relative_width: float,
    data_quality: float = 1.0,
    days_since_observation: float = 0.0,
    hit_rate: float | None = None,
) -> float:
    """Weighted confidence in [0, 1]."""
    band = band_score(relative_width)
    quality = quality_score(data_quality, days_since_observation)
    hits = DEFAULT_HIT_RATE if hit_rate is None else max(0.0, min(1.0, hit_rate))
    total = W_BAND * band + W_QUALITY * quality + W_HIT_RATE * hits
    return max(0.0, min(1.0, total))


def label(confidence: float) -> str:
    """The word shown next to the meter."""
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"
