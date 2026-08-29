"""Phase 6 — the decision engine, including the two tests that protect it.

The anti-vacuity test (a genuine split must be reachable) and the provider-swap
test (the recommendation must move when the forecast moves) are the reason this
file exists. Everything else here is ordinary correctness.
"""

from __future__ import annotations

import math

import pytest

from decision import confidence as conf
from decision import constraints as rules
from decision.engine import Lot, optimise
from decision.explain import explain, rank_features
from economics.compare import MandiOption
from ml.port import Quantiles

MANDIS = [MandiOption("Pune", 2010.0, 62.0), MandiOption("Nashik", 1950.0, 18.0)]
PRICES = {"Pune": 2010.0, "Nashik": 1950.0}


def band(rise: float, spread: float = 0.10, base: float = 2010.0):
    """A forecast rising `rise` with a p10-p90 half-width of `spread`."""
    p = base * (1.0 + rise)
    return {
        1: Quantiles.of(p * (1 - spread / 4), p, p * (1 + spread / 4)),
        3: Quantiles.of(p * (1 - spread / 2), p, p * (1 + spread / 2)),
        7: Quantiles.of(p * (1 - spread), p, p * (1 + spread)),
        15: Quantiles.of(p * (1 - spread * 1.4), p, p * (1 + spread * 1.4)),
    }


def plan(crop="garlic", qty=180.0, rise=0.03, spread=0.10, profile="balanced", **kw):
    return optimise(Lot(crop=crop, qty_qtl=qty, risk_profile=profile),
                    PRICES, band(rise, spread), MANDIS, **kw)


# ══════════════════════════════════════════════════════════════════════════
# the two that matter most
# ══════════════════════════════════════════════════════════════════════════

def test_a_genuine_split_is_reachable():
    """ANTI-VACUITY. The whole product is built on split recommendations.

    Round 1 shipped a linear objective, so the optimum always sat at a corner —
    sell everything or hold everything — and a split was mathematically
    impossible. It passed 47 tests, because every test asked "is the answer
    sensible?" and "sell everything" always is. Nothing asked "can this function
    ever return a split?". This does.
    """
    splits = []
    for qty in (80.0, 180.0, 270.0):
        for rise in (0.01, 0.02, 0.03, 0.04, 0.06):
            for spread in (0.10, 0.20, 0.30):
                for profile in ("cautious", "balanced"):
                    result = plan(qty=qty, rise=rise, spread=spread, profile=profile)
                    if result.action == "split":
                        splits.append(result)

    assert splits, "no split is reachable — the objective has gone linear again"
    for result in splits:
        assert len(result.tranches) == 2
        assert all(t.qtl > 0 for t in result.tranches), "a tranche of zero is not a split"


def test_the_recommendation_moves_when_the_forecast_moves():
    """PROVIDER SWAP. If this fails, the engine is ignoring the forecast.

    That failure would pass every other test in this file and make the model
    pointless — swapping baseline for LightGBM would change nothing on screen.
    """
    falling = optimise(Lot(crop="garlic", qty_qtl=180.0), PRICES, band(-0.10), MANDIS)
    rising = optimise(Lot(crop="garlic", qty_qtl=180.0), PRICES, band(+0.15), MANDIS)

    assert falling.action != rising.action or \
        abs(falling.strategy_net - rising.strategy_net) > 1.0, \
        "the recommendation did not move when the forecast did"
    assert falling.action == "sell_now", "a falling forecast must not advise holding"


# ══════════════════════════════════════════════════════════════════════════
# ordinary correctness
# ══════════════════════════════════════════════════════════════════════════

def test_tranche_quantities_sum_to_the_lot():
    for qty in (10.0, 80.0, 180.0):
        for rise in (-0.05, 0.02, 0.10):
            result = plan(qty=qty, rise=rise)
            assert math.isclose(sum(t.qtl for t in result.tranches), qty, rel_tol=1e-6)


def test_perishable_crops_are_told_to_sell_now():
    """Tomato, okra and banana rot too fast for any forecast to justify a hold."""
    for crop in ("tomato", "okra", "banana", "cauliflower"):
        result = plan(crop=crop, qty=60.0, rise=0.20)
        assert result.action == "sell_now", f"{crop} was advised to wait"


def test_storable_crops_may_hold_when_the_forecast_justifies_it():
    held = [plan(crop=c, qty=180.0, rise=0.12).action
            for c in ("garlic", "onion", "potato")]
    assert any(a in ("hold", "split") for a in held), \
        "no storable crop ever holds — the engine is ignoring upside"


def test_cautious_never_holds_longer_than_aggressive():
    for rise in (0.01, 0.03, 0.06, 0.12):
        cautious = plan(rise=rise, profile="cautious")
        aggressive = plan(rise=rise, profile="aggressive")
        cautious_days = max((t.day_offset for t in cautious.tranches), default=0)
        aggressive_days = max((t.day_offset for t in aggressive.tranches), default=0)
        assert cautious_days <= aggressive_days, f"cautious held longer at rise={rise}"


def test_expected_gain_is_non_negative_for_the_chosen_plan():
    """The optimiser may never recommend something worse than selling today."""
    losses = 0
    for qty in (20.0, 80.0, 180.0):
        for rise in (-0.15, -0.05, 0.0, 0.05, 0.15):
            for spread in (0.08, 0.25):
                for profile in ("cautious", "balanced", "aggressive"):
                    if plan(qty=qty, rise=rise, spread=spread, profile=profile).expected_gain < -1.0:
                        losses += 1
    assert losses == 0, f"{losses} plans scored worse than selling immediately"


def test_a_falling_forecast_never_advises_holding():
    for crop in ("garlic", "onion", "potato"):
        assert plan(crop=crop, rise=-0.20).action == "sell_now"


def test_alternatives_considered_is_reported_and_plural():
    result = plan()
    assert result.alternatives_considered > 1


def test_headline_carries_a_rupee_figure_in_both_languages():
    result = plan()
    assert "₹" in result.headline and "₹" in result.headline_mr
    assert result.reason_text and result.reason_text_mr


def test_zero_quantity_is_rejected():
    with pytest.raises(ValueError):
        optimise(Lot(crop="onion", qty_qtl=0.0), PRICES, band(0.05), MANDIS)


def test_no_markets_is_rejected():
    from core.errors import InsufficientData

    with pytest.raises(InsufficientData):
        optimise(Lot(crop="onion", qty_qtl=80.0), PRICES, band(0.05), [])


# ══════════════════════════════════════════════════════════════════════════
# every constraint must be provably able to fire
# ══════════════════════════════════════════════════════════════════════════

def test_perishability_cap_fires_and_caps_at_the_crop_ceiling():
    assert rules.perishability_cap("okra").max_hold_days == 2
    assert rules.perishability_cap("garlic").max_hold_days == 30


def test_spoilage_cap_fires_for_a_perishable_crop_in_ambient_storage():
    fired = rules.spoilage_cap("tomato", "ambient", [3, 7, 15])
    assert fired is not None and fired.max_hold_days < 15


def test_spoilage_cap_is_relaxed_by_cold_storage():
    ambient = rules.spoilage_cap("onion", "ambient", [3, 7, 15])
    cold = rules.spoilage_cap("onion", "cold_store", [3, 7, 15])
    ambient_cap = ambient.max_hold_days if ambient else 15
    cold_cap = cold.max_hold_days if cold else 15
    assert cold_cap >= ambient_cap


def test_wide_band_cap_fires_and_forces_a_sell_floor():
    assert rules.wide_band_cap(0.10) is None
    fired = rules.wide_band_cap(0.60)
    assert fired is not None and fired.min_sell_fraction == 0.5


def test_low_confidence_cap_fires():
    assert rules.low_confidence_cap(0.9) is None
    assert rules.low_confidence_cap(0.2).min_sell_fraction == 0.5


def test_transport_viability_blocks_a_tiny_lot_going_far():
    assert rules.transport_viability(5.0, 120.0, 90.0) is not None
    assert rules.transport_viability(5.0, 10.0, 90.0) is None, "near markets are fine"
    assert rules.transport_viability(80.0, 120.0, 90.0) is None, "a full truck is fine"


def test_shock_override_forces_sell_now():
    fired = rules.shock_override(magnitude=4.0, days_since=1, bearish=True)
    assert fired is not None
    assert fired.max_hold_days == 0 and fired.min_sell_fraction == 1.0
    assert rules.shock_override(4.0, 1, bearish=False) is None
    assert rules.shock_override(4.0, 30, bearish=True) is None, "stale shocks expire"


def test_a_bearish_shock_overrides_a_bullish_forecast():
    result = plan(rise=0.25, shock_magnitude=5.0, shock_days_since=1.0,
                  shock_bearish=True)
    assert result.action == "sell_now"
    assert "shock_override" in result.constraints_applied


def test_constraints_applied_is_reported_to_the_farmer():
    assert plan(crop="tomato", qty=60.0).constraints_applied


# ══════════════════════════════════════════════════════════════════════════
# confidence and explanations
# ══════════════════════════════════════════════════════════════════════════

def test_confidence_falls_as_the_band_widens():
    tight = conf.score(0.05)
    wide = conf.score(0.60)
    assert 0.0 <= wide < tight <= 1.0


def test_confidence_falls_with_stale_or_imputed_data():
    assert conf.score(0.15, data_quality=0.4) < conf.score(0.15, data_quality=1.0)
    assert conf.score(0.15, days_since_observation=14) < conf.score(0.15, days_since_observation=0)


def test_confidence_is_always_a_probability():
    for width in (0.0, 0.1, 0.5, 2.0, float("inf")):
        assert 0.0 <= conf.score(width) <= 1.0


def test_confidence_labels_are_ordered():
    assert conf.label(0.9) == "high"
    assert conf.label(0.6) == "medium"
    assert conf.label(0.2) == "low"


def test_explanations_are_built_from_the_feature_row():
    text, text_mr = explain({"arr_vs_ma30": -0.25, "price_vs_nbr": 0.04})
    assert "arrivals" in text.lower()
    assert "%" in text and text_mr


def test_explanations_stay_silent_when_nothing_is_unusual():
    text, text_mr = explain({"arr_vs_ma30": 0.001, "price_vs_nbr": 0.002})
    assert text == "" and text_mr == "", "narrating noise as a reason is worse than silence"


def test_explanation_ranking_puts_the_strongest_signal_first():
    ranked = rank_features({"arr_vs_ma30": -0.8, "roll_std_30": 0.13})
    assert ranked and ranked[0] == "arr_vs_ma30"
