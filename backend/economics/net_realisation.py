"""Phase 5 — what actually reaches the farmer's hand. The heart of the product.

    net_in_hand(NetInput(price_per_qtl=2010, qty_qtl=80, days_held=0,
                         distance_km=62, grade="B", storage="ambient",
                         crop="onion"))
    -> NetResult(net_per_qtl=1842.31, ...)

Every other app shows a farmer ₹2,010. That number is a lie for him. Out of it
comes 3% commission, 1.05% market cess, 0.3% other fees, ₹23/qtl of hamali and
weighing and packing, the diesel to move 80 quintals 62 km, and — if he waits —
storage, interest, and the share of the lot that rots. What reaches his hand is
₹1,842.

**Zero model dependency.** This is arithmetic. It is finished and correct whether
or not a forecast exists, which is why it can be trusted more than anything else
in the system.

**The one subtle rule, carried from the TypeScript:** `net_per_qtl` divides by
the ORIGINAL quantity, not the surviving quantity. Spoilage therefore shows up
as a lower rate per quintal rather than hiding inside a smaller total. A farmer
comparing "₹1,842/qtl if I sell today" against "₹1,790/qtl if I wait a week" is
seeing the cost of waiting in the number he actually thinks in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from core.config import settings
from economics.spoilage import DEFAULT_TMAX_C, crop_k_c, spoilage_fraction

_COST = settings.cost_model
_DEFAULTS = _COST.defaults

GRADE_FACTOR: dict[str, float] = {k: float(v) for k, v in _COST.grade_factor.to_dict().items()}
STORAGE_COST_PER_QTL_DAY: dict[str, float] = {
    k: float(v) for k, v in _DEFAULTS.storage_cost_per_qtl_per_day.to_dict().items()
}
HAMALI_PER_QTL: float = float(_DEFAULTS.hamali_per_qtl)
WEIGHING_PER_QTL: float = float(_DEFAULTS.weighing_per_qtl)
PACKING_PER_QTL: float = float(_DEFAULTS.packing_per_qtl)
TRUCK_CAPACITY_QTL: float = float(_DEFAULTS.truck_capacity_qtl)
TRANSPORT_PER_KM: float = float(_DEFAULTS.transport_per_km)
INTEREST_RATE_ANNUAL: float = float(_DEFAULTS.interest_rate_annual)

Grade = Literal["A", "B", "C"]
Storage = Literal["ambient", "shed", "cold_store"]


def state_fees(state: str = "Maharashtra") -> tuple[float, float, float]:
    """(commission %, APMC cess %, other fees %) for a state, with a fallback."""
    states = _COST.states.to_dict()
    block = states.get(state) or states.get("_default")
    return (
        float(block["commission_pct"]),
        float(block["apmc_cess_pct"]),
        float(block["other_fees_pct"]),
    )


@dataclass(frozen=True)
class CostLine:
    """One row of the waterfall the farmer sees. Mirrors CostLine in types.ts."""

    label: str
    label_mr: str
    amount: float                      # negative for deductions
    kind: Literal["gross", "deduction"]

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "labelMr": self.label_mr,
                "amount": round(self.amount, 2), "kind": self.kind}


@dataclass(frozen=True)
class NetInput:
    price_per_qtl: float
    qty_qtl: float
    days_held: float = 0.0
    distance_km: float = 0.0
    grade: Grade = "B"
    storage: Storage = "ambient"
    crop: str = "onion"
    state: str = "Maharashtra"
    tmax_c: float = DEFAULT_TMAX_C


@dataclass(frozen=True)
class NetResult:
    gross: float
    deductions: float
    transport: float
    holding: float
    spoilage_qtl: float
    net_total: float
    net_per_qtl: float
    gross_per_qtl: float
    lines: list[CostLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gross": round(self.gross, 2),
            "deductions": round(self.deductions, 2),
            "transport": round(self.transport, 2),
            "holding": round(self.holding, 2),
            "spoilageQtl": round(self.spoilage_qtl, 3),
            "netTotal": round(self.net_total, 2),
            "netPerQtl": round(self.net_per_qtl, 2),
            "grossPerQtl": round(self.gross_per_qtl, 2),
            "lines": [line.to_dict() for line in self.lines],
        }


def _mr_number(value: float) -> str:
    """Devanagari digits, because the Marathi label reads wrong with ASCII ones."""
    table = str.maketrans("0123456789", "०१२३४५६७८९")
    return f"{value:g}".translate(table)


def net_in_hand(spec: NetInput) -> NetResult:
    """Gross → fees → handling → transport → holding → what he actually gets.

    Order matters: percentage fees are charged on the graded gross, handling is
    per surviving quintal, transport is per truckload, and interest accrues on
    the gross value of the stock he is choosing not to sell.
    """
    if spec.qty_qtl <= 0:
        raise ValueError("qty_qtl must be positive")

    commission_pct, cess_pct, other_pct = state_fees(spec.state)

    spoil = spoilage_fraction(
        crop_k_c(spec.crop), spec.days_held, spec.storage, spec.tmax_c
    )
    qty_effective = spec.qty_qtl * (1.0 - spoil)

    grade_factor = GRADE_FACTOR[spec.grade]
    gross = spec.price_per_qtl * qty_effective * grade_factor

    commission = gross * commission_pct / 100.0
    cess = gross * cess_pct / 100.0
    other = gross * other_pct / 100.0
    handling = qty_effective * (HAMALI_PER_QTL + WEIGHING_PER_QTL + PACKING_PER_QTL)
    deductions = commission + cess + other + handling

    # Transport is per TRUCK, not per quintal — which is exactly why a nearer
    # mandi so often wins. A 10-quintal lot pays for a whole truck.
    trucks = math.ceil(qty_effective / TRUCK_CAPACITY_QTL) if qty_effective > 0 else 0
    transport = trucks * spec.distance_km * TRANSPORT_PER_KM

    storage_cost = STORAGE_COST_PER_QTL_DAY[spec.storage] * spec.qty_qtl * spec.days_held
    interest = gross * (INTEREST_RATE_ANNUAL / 365.0) * spec.days_held
    holding = storage_cost + interest

    net_total = gross - deductions - transport - holding

    lines = [
        CostLine("Gross at mandi", "बाजारातील एकूण", gross, "gross"),
        CostLine(f"APMC commission ({commission_pct}%)",
                 f"आडत ({_mr_number(commission_pct)}%)", -commission, "deduction"),
        CostLine(f"Market cess ({cess_pct}%)",
                 f"बाजार उपकर ({_mr_number(cess_pct)}%)", -cess, "deduction"),
        CostLine(f"Other fees ({other_pct}%)",
                 f"इतर शुल्क ({_mr_number(other_pct)}%)", -other, "deduction"),
        CostLine("Hamali, weighing, packing", "हमाली, वजन, पॅकिंग", -handling, "deduction"),
        CostLine(
            f"Transport ({trucks} truck{'s' if trucks != 1 else ''} × {spec.distance_km:g} km)",
            f"वाहतूक ({_mr_number(spec.distance_km)} कि.मी.)",
            -transport, "deduction",
        ),
    ]
    if holding > 0:
        lines.append(CostLine(
            f"Holding {spec.days_held:g} days (storage + interest)",
            f"{_mr_number(spec.days_held)} दिवस साठवण + व्याज",
            -holding, "deduction",
        ))

    return NetResult(
        gross=gross,
        deductions=deductions,
        transport=transport,
        holding=holding,
        spoilage_qtl=spec.qty_qtl - qty_effective,
        net_total=net_total,
        # The ORIGINAL quantity, deliberately — see the module docstring.
        net_per_qtl=net_total / spec.qty_qtl,
        gross_per_qtl=spec.price_per_qtl * grade_factor,
        lines=lines,
    )


def net_per_qtl(price_per_qtl: float, qty_qtl: float, **kwargs: Any) -> float:
    """Convenience wrapper for the decision engine's inner loop."""
    return net_in_hand(NetInput(price_per_qtl=price_per_qtl, qty_qtl=qty_qtl, **kwargs)).net_per_qtl
