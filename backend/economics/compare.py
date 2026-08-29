"""Phase 5 — rank markets by what the farmer actually keeps, not by the board price.

    compare_mandis(crop="onion", qty_qtl=80, days_held=0, grade="B",
                   storage="ambient", origin=(19.09, 74.74))

**The demo moment lives here.** Rank the same markets twice — once by the price
on the board, once by net in hand — and the two orders disagree. A mandi paying
₹60 more per quintal but sitting 40 km further away loses ₹2,520 of diesel on a
single truck, which is ₹31/qtl on an 80-quintal lot. The board price says go; the
arithmetic says stay.

`rank_flipped` marks every market where the two disagree, and
`has_rank_flip()` tells you whether the comparison contains the moment at all.
Phase 5's test suite asserts it does, so the demo cannot quietly lose it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from economics.net_realisation import (
    CostLine,
    Grade,
    NetInput,
    Storage,
    net_in_hand,
)


@dataclass
class MandiOption:
    """One market to consider: its price today and how far the lot must travel."""

    mandi: str
    price_per_qtl: float
    distance_km: float
    mandi_id: int | None = None
    district: str | None = None


@dataclass
class MandiComparison:
    """One row of the comparison table. Mirrors MandiComparison in types.ts."""

    mandi: str
    distance_km: float
    gross_per_qtl: float
    net_per_qtl: float
    rank_by_gross: int = 0
    rank_by_net: int = 0
    rank_flipped: bool = False
    breakdown: list[CostLine] = field(default_factory=list)
    mandi_id: int | None = None
    district: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mandi": self.mandi,
            "distanceKm": round(self.distance_km, 1),
            "grossPerQtl": round(self.gross_per_qtl, 2),
            "netPerQtl": round(self.net_per_qtl, 2),
            "rankByGross": self.rank_by_gross,
            "rankByNet": self.rank_by_net,
            "rankFlipped": self.rank_flipped,
            "breakdown": [line.to_dict() for line in self.breakdown],
        }


def compare_mandis(
    options: Sequence[MandiOption],
    qty_qtl: float,
    *,
    days_held: float = 0.0,
    grade: Grade = "B",
    storage: Storage = "ambient",
    crop: str = "onion",
    state: str = "Maharashtra",
    tmax_c: float | None = None,
) -> list[MandiComparison]:
    """Score every market, rank it twice, and return sorted by net (best first)."""
    rows: list[MandiComparison] = []
    for option in options:
        spec = NetInput(
            price_per_qtl=option.price_per_qtl,
            qty_qtl=qty_qtl,
            days_held=days_held,
            distance_km=option.distance_km,
            grade=grade,
            storage=storage,
            crop=crop,
            state=state,
            **({"tmax_c": tmax_c} if tmax_c is not None else {}),
        )
        result = net_in_hand(spec)
        rows.append(MandiComparison(
            mandi=option.mandi,
            distance_km=option.distance_km,
            gross_per_qtl=result.gross_per_qtl,
            net_per_qtl=result.net_per_qtl,
            breakdown=result.lines,
            mandi_id=option.mandi_id,
            district=option.district,
        ))

    for rank, row in enumerate(sorted(rows, key=lambda r: -r.gross_per_qtl), start=1):
        row.rank_by_gross = rank
    for rank, row in enumerate(sorted(rows, key=lambda r: -r.net_per_qtl), start=1):
        row.rank_by_net = rank
    for row in rows:
        row.rank_flipped = row.rank_by_gross != row.rank_by_net

    return sorted(rows, key=lambda r: r.rank_by_net)


def has_rank_flip(rows: Iterable[MandiComparison]) -> bool:
    """True when the highest-price market is not the best market."""
    return any(row.rank_flipped for row in rows)


def best_by_net(rows: Sequence[MandiComparison]) -> MandiComparison | None:
    return rows[0] if rows else None
