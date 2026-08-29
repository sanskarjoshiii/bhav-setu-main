"""Phase 12 — transport pooling, backed by the database.

Transport is the one cost a small farmer can actually control, and it is charged
per truck rather than per quintal. A 62 km trip costs ₹2,604 whether the truck
carries ten quintals or ninety — ₹260/qtl on a small lot, which is why the
nearest mandi so often wins the comparison. Four farmers travelling the same
morning split that four ways.

The saving is computed with the same `transport_per_km` and `truck_capacity_qtl`
the economics engine uses, so the number on the community page and the number in
the cost waterfall cannot drift apart.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text

from core.db import get_conn
from core.errors import BhavSetuError, InsufficientData
from economics.net_realisation import TRANSPORT_PER_KM, TRUCK_CAPACITY_QTL


class PoolFull(BhavSetuError):
    """Raised when a join would exceed the truck's capacity."""


@dataclass
class PoolMember:
    id: int
    farmer: str
    village: str
    qty_qtl: float

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "farmer": self.farmer,
                "village": self.village, "qtyQtl": round(self.qty_qtl, 2)}


@dataclass
class Pool:
    id: int
    mandi: str
    district: str
    travel_date: date
    capacity_qtl: float
    distance_km: float
    members: list[PoolMember] = field(default_factory=list)

    @property
    def booked_qtl(self) -> float:
        return sum(m.qty_qtl for m in self.members)

    @property
    def remaining_qtl(self) -> float:
        return max(0.0, self.capacity_qtl - self.booked_qtl)

    @property
    def is_full(self) -> bool:
        return self.remaining_qtl <= 1e-9

    @property
    def total_cost(self) -> float:
        """One truck for the round trip. Empty seats cost the same as full ones."""
        trucks = max(1, math.ceil(self.booked_qtl / TRUCK_CAPACITY_QTL))
        return trucks * self.distance_km * TRANSPORT_PER_KM

    def cost_per_qtl_alone(self, qty_qtl: float | None = None) -> float:
        """What one farmer pays travelling by himself — a whole truck for his lot."""
        qty = qty_qtl if qty_qtl and qty_qtl > 0 else (
            self.members[0].qty_qtl if self.members else 1.0)
        return (self.distance_km * TRANSPORT_PER_KM) / max(qty, 1e-9)

    def cost_per_qtl_pooled(self) -> float:
        """Split by share of the load, which is how farmers actually settle it."""
        if self.booked_qtl <= 0:
            return 0.0
        return self.total_cost / self.booked_qtl

    def saving_per_qtl(self, qty_qtl: float | None = None) -> float:
        return max(0.0, self.cost_per_qtl_alone(qty_qtl) - self.cost_per_qtl_pooled())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mandi": self.mandi,
            "district": self.district,
            "travelDate": str(self.travel_date),
            "capacityQtl": round(self.capacity_qtl, 2),
            "bookedQtl": round(self.booked_qtl, 2),
            "members": [m.to_dict() for m in self.members],
            "distanceKm": round(self.distance_km, 1),
            "totalCost": round(self.total_cost, 2),
            "costPerQtlAlone": round(self.cost_per_qtl_alone(), 2),
            "costPerQtlPooled": round(self.cost_per_qtl_pooled(), 2),
            "savingPerQtl": round(self.saving_per_qtl(), 2),
            "isFull": self.is_full,
        }


_SELECT = """
    SELECT p.id, p.travel_date, p.capacity_qtl, p.distance_km, p.status,
           m.name AS mandi, m.district
    FROM transport_pools p JOIN mandis m ON m.id = p.mandi_id
"""


def _load(conn, rows) -> list[Pool]:
    pools: dict[int, Pool] = {}
    for r in rows:
        pools[int(r["id"])] = Pool(
            id=int(r["id"]), mandi=str(r["mandi"]), district=str(r["district"]),
            travel_date=r["travel_date"], capacity_qtl=float(r["capacity_qtl"]),
            distance_km=float(r["distance_km"]),
        )
    if not pools:
        return []
    members = conn.execute(text("""
        SELECT id, pool_id, farmer_name, village, qty_qtl FROM pool_members
        WHERE pool_id = ANY(:ids) ORDER BY joined_at
    """), {"ids": list(pools)}).mappings().all()
    for m in members:
        pools[int(m["pool_id"])].members.append(PoolMember(
            id=int(m["id"]), farmer=str(m["farmer_name"]),
            village=str(m["village"] or ""), qty_qtl=float(m["qty_qtl"]),
        ))
    return list(pools.values())


def list_pools(mandi_id: int | None = None, travel_date: date | None = None,
               open_only: bool = True, district: str | None = None) -> list[Pool]:
    """Pools, optionally narrowed to one market or one district.

    District is the useful filter for a signed-in farmer: he wants the trucks
    leaving from where he actually lives, not every pool in the state.
    """
    clauses, params = [], {}
    if mandi_id is not None:
        clauses.append("p.mandi_id = :mandi_id")
        params["mandi_id"] = mandi_id
    if district:
        clauses.append("lower(m.district) = lower(:district)")
        params["district"] = district
    if travel_date is not None:
        clauses.append("p.travel_date = :travel_date")
        params["travel_date"] = travel_date
    if open_only:
        clauses.append("p.status = 'open'")
    sql = _SELECT + (" WHERE " + " AND ".join(clauses) if clauses else "") + \
        " ORDER BY p.travel_date, p.id"
    with get_conn() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        return _load(conn, rows)


def get_pool(pool_id: int) -> Pool:
    with get_conn() as conn:
        rows = conn.execute(text(_SELECT + " WHERE p.id = :id"),
                            {"id": pool_id}).mappings().all()
        pools = _load(conn, rows)
    if not pools:
        raise InsufficientData(f"no transport pool with id {pool_id}")
    return pools[0]


def create_pool(mandi_id: int, travel_date: date, farmer: str, village: str,
                qty_qtl: float, distance_km: float,
                capacity_qtl: float | None = None) -> Pool:
    capacity = capacity_qtl or TRUCK_CAPACITY_QTL
    if qty_qtl > capacity:
        raise PoolFull(f"{qty_qtl} qtl exceeds the truck capacity of {capacity} qtl")
    with get_conn() as conn:
        pool_id = int(conn.execute(text("""
            INSERT INTO transport_pools
                (mandi_id, travel_date, capacity_qtl, distance_km, created_by)
            VALUES (:mandi_id, :travel_date, :capacity, :distance, :farmer)
            RETURNING id
        """), {"mandi_id": mandi_id, "travel_date": travel_date,
               "capacity": capacity, "distance": distance_km,
               "farmer": farmer}).scalar_one())
        conn.execute(text("""
            INSERT INTO pool_members (pool_id, farmer_name, village, qty_qtl)
            VALUES (:pool_id, :farmer, :village, :qty)
        """), {"pool_id": pool_id, "farmer": farmer,
               "village": village, "qty": qty_qtl})
    return get_pool(pool_id)


def join_pool(pool_id: int, farmer: str, village: str, qty_qtl: float) -> Pool:
    """Add a farmer, refusing to overfill the truck.

    Capacity is checked against what is already booked, so two people joining a
    nearly-full pool cannot both succeed.
    """
    pool = get_pool(pool_id)
    if qty_qtl > pool.remaining_qtl + 1e-9:
        raise PoolFull(
            f"only {pool.remaining_qtl:.1f} qtl of space left on this truck, "
            f"{qty_qtl:.1f} qtl requested"
        )
    with get_conn() as conn:
        conn.execute(text("""
            INSERT INTO pool_members (pool_id, farmer_name, village, qty_qtl)
            VALUES (:pool_id, :farmer, :village, :qty)
        """), {"pool_id": pool_id, "farmer": farmer,
               "village": village, "qty": qty_qtl})
        updated = get_pool(pool_id)
        if updated.is_full:
            conn.execute(text("UPDATE transport_pools SET status='full' WHERE id=:id"),
                         {"id": pool_id})
    return get_pool(pool_id)


def leave_pool(pool_id: int, member_id: int) -> Pool:
    """Remove a member. Everyone remaining pays more — that is the whole point."""
    with get_conn() as conn:
        deleted = conn.execute(text("""
            DELETE FROM pool_members WHERE id = :mid AND pool_id = :pid RETURNING id
        """), {"mid": member_id, "pid": pool_id}).scalar()
        if deleted is None:
            raise InsufficientData(f"member {member_id} is not in pool {pool_id}")
        conn.execute(text("UPDATE transport_pools SET status='open' WHERE id=:id"),
                     {"id": pool_id})
    return get_pool(pool_id)
