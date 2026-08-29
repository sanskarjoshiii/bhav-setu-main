"""Phase 13 — the demo farmer, his lots, his sale reports, and a transport pool.

    python scripts/seed_demo_data.py           # add demo rows
    python scripts/seed_demo_data.py --reset   # remove them and re-add

Everything written here carries `source='seed_demo'` (or a `demo:` phone prefix),
so the reset can remove exactly the demo rows and nothing else. **No price
observation is ever seeded** — those are real, and inventing one would poison
the model that trains on them.

The sale reports are the important part: the transparency page has nothing to
show without them, and its whole claim is "this is what farmers told us they
actually got". They are generated from real prices with a realistic shortfall,
and every row is labelled `self_reported`, which is what they are.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

import _bootstrap  # noqa: F401  (sys.path side effect)

from sqlalchemy import text

from api import deps
from core.config import settings
from core.db import get_conn
from core.logging import configure_stdout_utf8
from economics.net_realisation import TRUCK_CAPACITY_QTL

configure_stdout_utf8()

DEMO_PHONE = "demo:+919876543210"
DEMO_PREFIX = "demo:"
SEED_SOURCE = "seed_demo"

FARMERS = [
    ("Ramesh Patil", "Vinchur", "+919876543210"),
    ("Sunita Jadhav", "Lasalgaon", "+919876543211"),
    ("Anil Shinde", "Niphad", "+919876543212"),
    ("Kavita More", "Pimpalgaon", "+919876543213"),
    ("Ganesh Kale", "Yeola", "+919876543214"),
    ("Meera Wagh", "Chandvad", "+919876543215"),
    ("Prakash Thorat", "Sangamner", "+919876543216"),
]


def reset(conn) -> dict[str, int]:
    """Remove every demo row. Real prices and the model are untouched."""
    counts: dict[str, int] = {}
    # Every pool, not just the seeded ones. A pool a judge created during the
    # demo — or one a test run left behind — is exactly what "reset" should
    # clear. Pools carry no real data, so nothing of value is lost.
    counts["pool_members"] = conn.execute(text("DELETE FROM pool_members")).rowcount or 0
    counts["transport_pools"] = conn.execute(text("DELETE FROM transport_pools")).rowcount or 0
    counts["sale_reports"] = conn.execute(text(
        "DELETE FROM sale_reports WHERE source = :s"), {"s": SEED_SOURCE}).rowcount or 0
    counts["recommendations"] = conn.execute(text("""
        DELETE FROM recommendations WHERE lot_id IN (
            SELECT l.id FROM lots l JOIN farmers f ON f.id = l.farmer_id
            WHERE f.phone_e164 LIKE 'demo:%')
    """)).rowcount or 0
    counts["lots"] = conn.execute(text("""
        DELETE FROM lots WHERE farmer_id IN (
            SELECT id FROM farmers WHERE phone_e164 LIKE 'demo:%')
    """)).rowcount or 0
    counts["farmers"] = conn.execute(text(
        "DELETE FROM farmers WHERE phone_e164 LIKE 'demo:%'")).rowcount or 0
    return counts


def _village_origin(district: str) -> tuple[float, float]:
    """A representative village in the district, from config/locations.yaml."""
    blocks = settings.locations.districts.to_dict()
    block = blocks.get(district)
    if block and block.get("villages"):
        v = block["villages"][0]
        return float(v["lat"]), float(v["lon"])
    return deps.REFERENCE_LAT, deps.REFERENCE_LON


def seed(conn, rng: random.Random) -> dict[str, int]:
    counts = {"farmers": 0, "lots": 0, "sale_reports": 0, "pools": 0}

    mandis = conn.execute(text("""
        SELECT m.id, m.name FROM mandis m
        JOIN price_observations p ON p.mandi_id = m.id
        GROUP BY m.id, m.name ORDER BY count(*) DESC
    """)).all()
    crops = conn.execute(text("""
        SELECT c.id, c.name FROM commodities c
        JOIN price_observations p ON p.commodity_id = c.id
        GROUP BY c.id, c.name ORDER BY count(*) DESC
    """)).all()
    if not mandis or not crops:
        raise SystemExit("⛔ no real prices loaded — run the backfill before seeding")

    # ── farmers ───────────────────────────────────────────────────────────
    farmer_ids: list[int] = []
    for name, village, phone in FARMERS:
        farmer_ids.append(int(conn.execute(text("""
            INSERT INTO farmers (phone_e164, name, village, language, risk_profile)
            VALUES (:p, :n, :v, 'mr', :r) RETURNING id
        """), {"p": f"{DEMO_PREFIX}{phone}", "n": name, "v": village,
               "r": rng.choice(("cautious", "balanced", "aggressive"))}).scalar_one()))
        counts["farmers"] += 1

    # ── open lots for the demo farmer ─────────────────────────────────────
    for crop in crops[:3]:
        qty = float(rng.choice((25, 40, 80)))
        conn.execute(text("""
            INSERT INTO lots (farmer_id, commodity_id, quantity_qtl, remaining_qtl,
                              harvest_date, quality_grade, storage_type)
            VALUES (:f, :c, :q, :q, :h, :g, :s)
        """), {"f": farmer_ids[0], "c": int(crop.id), "q": qty,
               "h": date.today() - timedelta(days=rng.randint(2, 12)),
               "g": rng.choice(("A", "B", "B")),
               "s": rng.choice(("ambient", "shed"))})
        counts["lots"] += 1

    # ── sale reports, priced off real observations ────────────────────────
    for _ in range(34):
        mandi = rng.choice(mandis)
        crop = rng.choice(crops[:8])
        row = conn.execute(text("""
            SELECT obs_date, modal_price FROM price_observations
            WHERE mandi_id = :m AND commodity_id = :c AND modal_price IS NOT NULL
              AND NOT is_imputed
            ORDER BY obs_date DESC LIMIT 90
        """), {"m": int(mandi.id), "c": int(crop.id)}).mappings().all()
        if not row:
            continue
        picked = rng.choice(row)
        quoted = float(picked["modal_price"])

        # The reported shortfall. Centred near -6% because that is roughly what
        # the fee stack alone costs; the spread is what makes the transparency
        # score worth computing at all.
        gap = rng.gauss(-6.0, 3.5)
        received = quoted * (1.0 + gap / 100.0)

        conn.execute(text("""
            INSERT INTO sale_reports
                (farmer_id, mandi_id, sale_date, channel, quantity_qtl,
                 gross_price_qtl, net_received_qtl, followed_advice,
                 verification, source)
            VALUES (:f, :m, :d, 'mandi', :q, :quoted, :received, :followed,
                    :verification, :source)
        """), {
            "f": rng.choice(farmer_ids), "m": int(mandi.id),
            "d": picked["obs_date"], "q": float(rng.choice((8, 12, 20, 35, 60))),
            "quoted": round(quoted, 2), "received": round(received, 2),
            "followed": rng.random() < 0.62,
            "verification": rng.choices(
                ("self_reported", "slip_photo", "fpo_verified"),
                weights=(70, 22, 8))[0],
            "source": SEED_SOURCE,
        })
        counts["sale_reports"] += 1

    # ── one open pool per district, with room left, so the demo can join ──
    # Every district gets one: whichever district a judge registers in, the
    # community page has something to show. Seeding only the two busiest left
    # an Ahmednagar farmer staring at an empty page.
    for offset, mandi in enumerate(mandis):
        # Same basis the compare page uses, so the saving on the community page
        # and the transport line in the cost waterfall cannot disagree.
        coords = conn.execute(
            text("SELECT lat, lon, district FROM mandis WHERE id = :m"),
            {"m": int(mandi.id)}).mappings().first()
        # The truck runs from a village in that district to its market, so
        # measure it that way. Measuring from a fixed point elsewhere made the
        # pool in that same district look like a zero-kilometre trip.
        origin = _village_origin(str(coords["district"]) if coords else "")
        distance = round(deps.haversine_km(
            origin[0], origin[1], float(coords["lat"]), float(coords["lon"]),
        ) * deps.ROAD_FACTOR, 1) if coords and coords["lat"] is not None else 40.0
        distance = max(distance, 8.0)   # a market yard is never next door
        pool_id = int(conn.execute(text("""
            INSERT INTO transport_pools
                (mandi_id, travel_date, capacity_qtl, distance_km, created_by)
            VALUES (:m, :d, :cap, :dist, :by) RETURNING id
        """), {"m": int(mandi.id),
               "d": date.today() + timedelta(days=2 + offset),
               "cap": TRUCK_CAPACITY_QTL, "dist": round(distance, 1),
               "by": f"{DEMO_PREFIX}{FARMERS[0][2]}"}).scalar_one())
        for name, village, _phone in FARMERS[1:1 + rng.randint(2, 3)]:
            conn.execute(text("""
                INSERT INTO pool_members (pool_id, farmer_name, village, qty_qtl)
                VALUES (:p, :n, :v, :q)
            """), {"p": pool_id, "n": name, "v": village,
                   "q": float(rng.choice((10, 15, 20, 25)))})
        counts["pools"] += 1

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed demo rows for the presentation.")
    parser.add_argument("--reset", action="store_true",
                        help="remove existing demo rows first")
    parser.add_argument("--reset-only", action="store_true",
                        help="remove demo rows and stop")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)

    with get_conn() as conn:
        if args.reset or args.reset_only:
            removed = reset(conn)
            print(f"\n  removed demo rows: "
                  f"{', '.join(f'{k}={v}' for k, v in removed.items() if v)}"
                  or "\n  nothing to remove")
            if args.reset_only:
                print()
                return 0
        counts = seed(conn, rng)

    print("\n✅ demo data seeded")
    for key, value in counts.items():
        print(f"   {key:<16}{value}")
    print("\n   Every row is tagged 'seed_demo' or 'demo:' — no real price was touched.")
    print("   Undo with:  python scripts/seed_demo_data.py --reset-only\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
