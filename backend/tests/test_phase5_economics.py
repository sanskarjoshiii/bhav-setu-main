"""Phase 5 — the economics engine, including the rank-flip the demo depends on."""

from __future__ import annotations

import math

import pytest

from economics.compare import MandiOption, compare_mandis, has_rank_flip
from economics.net_realisation import (
    GRADE_FACTOR,
    NetInput,
    net_in_hand,
    state_fees,
)
from economics.spoilage import (
    crop_k_c,
    max_hold_days,
    spoilage_fraction,
    spoilage_for_crop,
    storage_factor,
    temperature_factor,
)

BASE = dict(price_per_qtl=2010.0, qty_qtl=80.0, distance_km=62.0,
            grade="B", storage="ambient", crop="onion")


# ── spoilage ──────────────────────────────────────────────────────────────

def test_zero_day_hold_has_exactly_zero_spoilage():
    """The engine relies on this: selling today is never charged for rot."""
    for crop in ("onion", "tomato", "okra", "garlic"):
        assert spoilage_for_crop(crop, 0) == 0.0


def test_spoilage_grows_with_days_and_never_reaches_one():
    previous = 0.0
    for days in (1, 3, 7, 15, 30, 90):
        value = spoilage_for_crop("onion", days)
        assert value > previous, "spoilage must increase with time held"
        assert 0.0 <= value < 1.0
        previous = value


def test_cold_store_beats_shed_beats_ambient():
    ambient = spoilage_for_crop("onion", 10, "ambient")
    shed = spoilage_for_crop("onion", 10, "shed")
    cold = spoilage_for_crop("onion", 10, "cold_store")
    assert cold < shed < ambient


def test_heat_accelerates_spoilage_only_above_the_threshold():
    assert temperature_factor(28) == 1.0
    assert temperature_factor(30) == 1.0
    assert temperature_factor(40) > temperature_factor(35) > 1.0


def test_perishable_crops_rot_faster_than_storable_ones():
    """Tomato and potato are different businesses — the k_c must say so."""
    assert spoilage_for_crop("tomato", 7) > spoilage_for_crop("potato", 7)
    assert spoilage_for_crop("okra", 7) > spoilage_for_crop("onion", 7)
    assert spoilage_for_crop("garlic", 7) < spoilage_for_crop("onion", 7)


def test_k_c_is_derived_consistently_from_shelf_life():
    """anchor / shelf_life, so 'shelf life' means one thing for all crops."""
    from core.config import crop_specs

    for name, spec in crop_specs().items():
        expected = 0.54 / float(spec["shelf_life_days"])
        assert math.isclose(float(spec["k_c"]), expected, rel_tol=0.02), name


def test_no_crop_may_be_held_past_its_shelf_life():
    from core.config import crop_specs

    for name, spec in crop_specs().items():
        assert int(spec["max_hold_days"]) <= int(spec["shelf_life_days"]), name


def test_unknown_storage_is_rejected_loudly():
    with pytest.raises(ValueError):
        storage_factor("cellar")


# ── net in hand ───────────────────────────────────────────────────────────

def test_net_is_always_below_gross():
    result = net_in_hand(NetInput(**BASE))
    assert result.net_per_qtl < result.gross_per_qtl
    assert result.net_total < result.gross


def test_net_falls_as_distance_grows():
    near = net_in_hand(NetInput(**{**BASE, "distance_km": 10})).net_per_qtl
    far = net_in_hand(NetInput(**{**BASE, "distance_km": 140})).net_per_qtl
    assert far < near


def test_net_falls_as_days_held_grows():
    same_day = net_in_hand(NetInput(**{**BASE, "days_held": 0})).net_per_qtl
    week = net_in_hand(NetInput(**{**BASE, "days_held": 7})).net_per_qtl
    assert week < same_day


def test_net_per_qtl_divides_by_the_original_quantity():
    """Spoilage must show as a lower RATE, not hide inside a smaller total."""
    held = net_in_hand(NetInput(**{**BASE, "days_held": 15}))
    assert held.spoilage_qtl > 0
    assert math.isclose(held.net_per_qtl, held.net_total / BASE["qty_qtl"], rel_tol=1e-9)


def test_grade_a_beats_b_beats_c():
    grades = [net_in_hand(NetInput(**{**BASE, "grade": g})).net_per_qtl
              for g in ("A", "B", "C")]
    assert grades[0] > grades[1] > grades[2]
    assert GRADE_FACTOR["A"] > GRADE_FACTOR["B"] > GRADE_FACTOR["C"]


def test_the_waterfall_adds_up_to_the_net():
    result = net_in_hand(NetInput(**BASE))
    assert math.isclose(sum(line.amount for line in result.lines),
                        result.net_total, rel_tol=1e-6)


def test_transport_is_charged_per_truck_not_per_quintal():
    """One quintal and ninety quintals pay the same diesel. This is why a
    nearer mandi so often wins, and the compare page exists because of it."""
    small = net_in_hand(NetInput(**{**BASE, "qty_qtl": 5.0}))
    assert math.isclose(small.transport, net_in_hand(NetInput(**BASE)).transport)


def test_a_second_truck_is_charged_above_capacity():
    one = net_in_hand(NetInput(**{**BASE, "qty_qtl": 80.0})).transport
    two = net_in_hand(NetInput(**{**BASE, "qty_qtl": 120.0})).transport
    assert math.isclose(two, one * 2)


def test_state_fees_fall_back_for_an_unknown_state():
    assert state_fees("Maharashtra") == (3.0, 1.05, 0.3)
    assert state_fees("Atlantis") == (2.5, 1.5, 0.5)


def test_zero_quantity_is_rejected():
    with pytest.raises(ValueError):
        net_in_hand(NetInput(**{**BASE, "qty_qtl": 0}))


# ── the demo moment ───────────────────────────────────────────────────────

def test_the_highest_price_mandi_is_not_always_the_best_mandi():
    """THE anti-vacuity test for Phase 5.

    If ranking by gross and ranking by net can never disagree, the compare page
    is decoration and the product's central claim is false. A 10-quintal lot
    pays for a whole truck, so ₹42/km of diesel swamps a ₹95/qtl price edge.
    """
    options = [
        MandiOption("Far but rich", 2200.0, 150.0),
        MandiOption("Near and fair", 2105.0, 12.0),
    ]
    rows = compare_mandis(options, qty_qtl=10.0, crop="onion")

    assert has_rank_flip(rows), "no rank flip — the demo moment has been lost"
    best = rows[0]
    assert best.mandi == "Near and fair"
    assert best.rank_by_net == 1 and best.rank_by_gross == 2


def test_comparison_is_sorted_by_net_best_first():
    options = [MandiOption("A", 2000.0, 20.0), MandiOption("B", 2100.0, 30.0),
               MandiOption("C", 1900.0, 5.0)]
    rows = compare_mandis(options, qty_qtl=80.0, crop="onion")
    nets = [r.net_per_qtl for r in rows]
    assert nets == sorted(nets, reverse=True)
    assert [r.rank_by_net for r in rows] == [1, 2, 3]


def test_every_comparison_row_carries_its_full_breakdown():
    rows = compare_mandis([MandiOption("A", 2000.0, 20.0)], qty_qtl=80.0, crop="onion")
    assert rows[0].breakdown, "the farmer must be able to see where the money went"
    assert rows[0].breakdown[0].kind == "gross"
    assert all(line.amount <= 0 for line in rows[0].breakdown[1:])


# ── agreement with the TypeScript engine ──────────────────────────────────

def test_matches_the_typescript_engine_on_a_reference_lot():
    """frontend/lib/mock/economics.ts, same inputs, onion k_c 0.006.

    Hand-computed from that file: gross 160,800; fees 4.35% = 6,994.80;
    handling 80 x 23 = 1,840; transport 1 x 62 x 42 = 2,604; holding 0.
    Net 149,361.20 over 80 qtl = 1,867.02/qtl.
    """
    result = net_in_hand(NetInput(**BASE))
    assert math.isclose(result.gross, 160_800.0, rel_tol=1e-9)
    assert math.isclose(result.transport, 2_604.0, rel_tol=1e-9)
    assert math.isclose(result.net_per_qtl, 1_867.015, rel_tol=1e-4)


@pytest.mark.parametrize("qty,days,distance,grade,storage", [
    (10, 0, 12, "A", "ambient"), (80, 0, 62, "B", "ambient"),
    (45, 3, 30, "C", "shed"), (120, 7, 90, "B", "cold_store"),
    (200, 15, 140, "A", "cold_store"), (5, 1, 8, "C", "ambient"),
])
def test_net_stays_finite_and_ordered_across_the_input_space(
    qty, days, distance, grade, storage
):
    result = net_in_hand(NetInput(
        price_per_qtl=2010.0, qty_qtl=qty, days_held=days, distance_km=distance,
        grade=grade, storage=storage, crop="onion",
    ))
    assert math.isfinite(result.net_per_qtl)
    assert result.net_per_qtl < result.gross_per_qtl
    assert 0 <= result.spoilage_qtl <= qty
