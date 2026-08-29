"""Phase 6 — the one-line reason a farmer reads, built from the feature row.

    explain(features)  ->  ("arrivals are 22% below normal", "आवक २२% कमी आहे")

Rule-based, driven by `explanation_templates` in config/decision.yaml. Features
are ranked by how far each sits from its normal value, and the strongest one or
two become the sentence.

**This is not a placeholder for SHAP.** SHAP output is a list of feature names
and signed contributions — it still has to be translated into farmer language,
which is exactly what these templates do. When SHAP lands in Phase B4 it re-ranks
the same templates; it does not replace them.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from core.config import settings

TEMPLATES: dict[str, str] = {
    k: str(v) for k, v in settings.decision.explanation_templates.to_dict().items()
}

#: Marathi renderings of the same templates, keyed identically.
TEMPLATES_MR: dict[str, str] = {
    "arr_vs_ma30": "आवक नेहमीपेक्षा {pct}% {direction} आहे",
    "days_to_festival": "{n} दिवसांत सणाची मागणी सुरू होईल",
    "shock_active_bearish": "{n} दिवसांपूर्वीच्या धोरण बदलामुळे भाव घसरत आहेत",
    "shock_active_bullish": "{n} दिवसांपूर्वीच्या धोरण बदलामुळे भाव वाढत आहेत",
    "rain_forecast_7d": "पुढील काही दिवसांत जोरदार पाऊस अपेक्षित आहे",
    "price_vs_nbr": "जवळचे बाजार {pct}% जास्त भाव देत आहेत",
    "roll_std_30": "या महिन्यात भाव खूप वर-खाली होत आहेत",
}

#: How far from normal a feature must be before it is worth mentioning. Below
#: this we would be narrating noise as if it were a reason.
MIN_STRENGTH: float = 0.08

_MR_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def _mr_digits(text: str) -> str:
    """Devanagari numerals — a Marathi sentence with ASCII digits reads wrong."""
    return text.translate(_MR_DIGITS)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _candidates(
    features: Mapping[str, Any],
) -> list[tuple[float, str, dict[str, Any], dict[str, Any]]]:
    """(strength, template key, English args, Marathi args) per feature worth saying.

    The two argument dicts exist because some placeholders are words, not
    numbers: `{direction}` has to be "lower"/"higher" in one sentence and
    "कमी"/"जास्त" in the other. Formatting both from one dict produced
    "आवक नेहमीपेक्षा 15% lower आहे", which is neither language.
    """
    out: list[tuple[float, str, dict[str, Any], dict[str, Any]]] = []

    arrivals = _finite(features.get("arr_vs_ma30"))
    if arrivals is not None and abs(arrivals) >= MIN_STRENGTH:
        # The feature is a log ratio, so exp() turns it back into a percentage.
        pct = abs(math.expm1(arrivals)) * 100.0
        out.append((abs(arrivals) * 1.2, "arr_vs_ma30",
                    {"pct": f"{pct:.0f}",
                     "direction": "higher" if arrivals > 0 else "lower"},
                    {"pct": _mr_digits(f"{pct:.0f}"),
                     "direction": "जास्त" if arrivals > 0 else "कमी"}))

    neighbour = _finite(features.get("price_vs_nbr"))
    if neighbour is not None and abs(neighbour) >= MIN_STRENGTH:
        pct = abs(math.expm1(neighbour)) * 100.0
        out.append((abs(neighbour), "price_vs_nbr", {"pct": f"{pct:.0f}"},
                    {"pct": _mr_digits(f"{pct:.0f}")}))

    festival_days = _finite(features.get("days_to_festival"))
    effect = _finite(features.get("festival_demand_effect")) or 0.0
    if festival_days is not None and 0 < festival_days <= 21 and effect > 0:
        out.append((0.9 - festival_days / 30.0, "days_to_festival",
                    {"festival": "Festival", "n": f"{festival_days:.0f}"},
                    {"n": _mr_digits(f"{festival_days:.0f}")}))

    for key, sign in (("shock_active_bearish", "bearish"), ("shock_active_bullish", "bullish")):
        magnitude = _finite(features.get(key))
        if magnitude and magnitude > 0.5:
            days = _finite(features.get("days_since_shock")) or 0.0
            out.append((min(1.5, magnitude / 2.0), key, {"n": f"{days:.0f}"},
                        {"n": _mr_digits(f"{days:.0f}")}))

    rain = _finite(features.get("rain_forecast_7d"))
    if rain is not None and rain > 25:
        out.append((0.5, "rain_forecast_7d", {}, {}))

    volatility = _finite(features.get("roll_std_30"))
    if volatility is not None and volatility > 0.12:
        out.append((volatility, "roll_std_30", {}, {}))

    return sorted(out, key=lambda row: -row[0])


def explain(features: Mapping[str, Any], limit: int = 2) -> tuple[str, str]:
    """The strongest one or two reasons, in English and Marathi.

    Returns empty strings when nothing is far enough from normal to be worth
    saying — the engine then falls back to a plain sentence about the decision
    itself, which is better than inventing a cause.
    """
    picked = _candidates(features)[:limit]
    if not picked:
        return "", ""

    english = [TEMPLATES[key].format(**args)
               for _, key, args, _mr in picked if key in TEMPLATES]
    marathi = [TEMPLATES_MR[key].format(**mr_args)
               for _, key, _en, mr_args in picked if key in TEMPLATES_MR]

    return (
        _join(english).capitalize() if english else "",
        _join(marathi, joiner=" आणि ") if marathi else "",
    )


def _join(parts: Sequence[str], joiner: str = " and ") -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return joiner.join(parts)


def rank_features(features: Mapping[str, Any]) -> list[str]:
    """Feature keys in the order they would be mentioned. Phase B4's SHAP hook."""
    return [key for _, key, _en, _mr in _candidates(features)]
