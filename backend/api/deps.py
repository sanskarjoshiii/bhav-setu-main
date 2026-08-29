"""Phase 8 — the queries and lookups every router shares.

Kept in one file so a router is only ever a thin translation between HTTP and a
function call. Routers must not write SQL: the moment two of them query
`price_observations` differently, the compare page and the advisor page start
disagreeing about today's price, and that bug is invisible until a judge notices.

**Nothing here imports LightGBM.** Forecasts are reached only through
`ml.provider.get_provider()`, which is the Phase A0 rule that makes swapping the
model a one-line config change.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Iterable, Sequence

from sqlalchemy import text

from core.config import crop_specs, settings
from core.db import get_conn
from core.errors import InsufficientData
from economics.compare import MandiOption

#: Where distance is measured from when the caller is not signed in.
#:
#: Vinchur, in the Nashik onion belt — a real village, deliberately NOT a market
#: yard. It previously sat on Ahmednagar's exact coordinates, which made every
#: distance to Ahmednagar zero and every transport cost there free: the compare
#: page flattered one market and the community page showed a pool saving ₹0.
#: A signed-in farmer's own village overrides this.
REFERENCE_LAT: float = 20.1058
REFERENCE_LON: float = 74.3406

EARTH_RADIUS_KM: float = 6371.0

#: Straight-line distance under-states a road trip. `sources.yaml → routing`
#: carries the same factor for the ingestion side.
ROAD_FACTOR: float = float(settings.sources.routing.haversine_fallback_factor)

#: The same two thresholds `features.builder.build_features` enforces, read from
#: the same config, so the UI's idea of "forecastable" cannot drift from the
#: builder's.
LOOKBACK_DAYS: int = int(settings.app.history_lookback_days)
MIN_OBSERVATIONS: int = int(settings.app.features.min_observations)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ══════════════════════════════════════════════════════════════════════════
# reference data
# ══════════════════════════════════════════════════════════════════════════

def list_mandis(with_data_only: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT m.id, m.name, m.district, m.lat, m.lon,
               count(p.id) AS rows
        FROM mandis m
        LEFT JOIN price_observations p ON p.mandi_id = m.id
        GROUP BY m.id, m.name, m.district, m.lat, m.lon
        ORDER BY m.name
    """
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(text(sql)).mappings()]
    if with_data_only:
        rows = [r for r in rows if r["rows"] > 0]
    return rows


def list_commodities(with_data_only: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT c.id, c.name, count(p.id) AS rows,
               count(DISTINCT p.mandi_id) AS mandis
        FROM commodities c
        LEFT JOIN price_observations p ON p.commodity_id = c.id
        GROUP BY c.id, c.name
        ORDER BY c.name
    """
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(text(sql)).mappings()]
    if with_data_only:
        rows = [r for r in rows if r["rows"] > 0]
    return rows


@lru_cache(maxsize=1)
def _crop_index() -> dict[str, dict[str, Any]]:
    """Every crop key and alias, lowercased, pointing at its config block."""
    index: dict[str, dict[str, Any]] = {}
    for key, spec in crop_specs().items():
        block = {"key": key, **spec}
        index[key.lower()] = block
        index[key.replace("_", " ").lower()] = block
        for alias in spec.get("aliases", []):
            index[str(alias).lower()] = block
    return index


def crop_config(crop: str) -> dict[str, Any]:
    block = _crop_index().get(str(crop).strip().lower())
    if block is None:
        raise InsufficientData(
            f"unknown crop {crop!r}. Known crops: "
            f"{', '.join(sorted(k for k in crop_specs()))}"
        )
    return block


def resolve_commodity(crop: str) -> tuple[int, str]:
    """(commodity_id, canonical name) for a crop key, alias or display name."""
    block = crop_config(crop)
    wanted = {block["key"].lower(), block["key"].replace("_", " ").lower()}
    wanted |= {str(a).lower() for a in block.get("aliases", [])}
    with get_conn() as conn:
        rows = conn.execute(text("SELECT id, name FROM commodities")).all()
    for row in rows:
        if str(row.name).lower() in wanted:
            return int(row.id), str(row.name)
    raise InsufficientData(f"crop {crop!r} is configured but not seeded in the database")


def resolve_mandi(name: str) -> tuple[int, str, str]:
    """(mandi_id, name, district). Accepts a mandi name or a district name."""
    with get_conn() as conn:
        row = conn.execute(
            text("SELECT id, name, district FROM mandis WHERE lower(name) = lower(:n)"),
            {"n": name},
        ).mappings().first()
        if row is None:
            row = conn.execute(
                text("""SELECT m.id, m.name, m.district FROM mandis m
                        JOIN price_observations p ON p.mandi_id = m.id
                        WHERE lower(m.district) = lower(:n)
                        GROUP BY m.id, m.name, m.district
                        ORDER BY count(*) DESC LIMIT 1"""),
                {"n": name},
            ).mappings().first()
    if row is None:
        raise InsufficientData(f"unknown mandi or district {name!r}")
    return int(row["id"]), str(row["name"]), str(row["district"])


# ══════════════════════════════════════════════════════════════════════════
# prices
# ══════════════════════════════════════════════════════════════════════════

_LATEST_SQL = text(
    """
    SELECT DISTINCT ON (p.mandi_id)
           p.mandi_id, m.name AS mandi, m.district, m.lat, m.lon, p.obs_date,
           p.modal_price, p.min_price, p.max_price, p.arrival_qtl
    FROM price_observations p
    JOIN mandis m ON m.id = p.mandi_id
    WHERE p.commodity_id = :commodity_id AND p.modal_price IS NOT NULL
    ORDER BY p.mandi_id, p.obs_date DESC
    """
)


def latest_prices(commodity_id: int) -> list[dict[str, Any]]:
    """The most recent price for this crop at every market that carries it."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            _LATEST_SQL, {"commodity_id": commodity_id}).mappings()]

        # How much real history sits behind each market, using the same window
        # and the same is_imputed rule build_features() applies — so a market
        # marked forecastable here is one the forecast endpoint will accept.
        counts = {
            int(r["mandi_id"]): int(r["n"])
            for r in conn.execute(text("""
                SELECT mandi_id, count(*) AS n FROM price_observations
                WHERE commodity_id = :c AND NOT is_imputed AND modal_price IS NOT NULL
                  AND obs_date > (SELECT max(obs_date) FROM price_observations
                                  WHERE commodity_id = :c) - make_interval(days => :lb)
                GROUP BY mandi_id
            """), {"c": commodity_id, "lb": LOOKBACK_DAYS}).mappings()
        }
        for row in rows:
            row["observations"] = counts.get(int(row["mandi_id"]), 0)
            row["can_forecast"] = row["observations"] >= MIN_OBSERVATIONS
            previous = conn.execute(
                text("""SELECT modal_price FROM price_observations
                        WHERE commodity_id = :c AND mandi_id = :m
                          AND obs_date < :d AND modal_price IS NOT NULL
                        ORDER BY obs_date DESC LIMIT 1"""),
                {"c": commodity_id, "m": row["mandi_id"], "d": row["obs_date"]},
            ).scalar()
            row["change_pct"] = (
                (float(row["modal_price"]) - float(previous)) / float(previous) * 100.0
                if previous else 0.0
            )
    return rows


def price_series(commodity_id: int, mandi_id: int, days: int = 90) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            text("""SELECT obs_date, modal_price FROM price_observations
                    WHERE commodity_id = :c AND mandi_id = :m
                      AND modal_price IS NOT NULL
                      AND obs_date >= (SELECT max(obs_date) FROM price_observations
                                       WHERE commodity_id = :c AND mandi_id = :m)
                                      - make_interval(days => :d)
                    ORDER BY obs_date"""),
            {"c": commodity_id, "m": mandi_id, "d": int(days)},
        ).mappings().all()
    return [dict(r) for r in rows]


def latest_price_for(commodity_id: int, mandi_id: int) -> tuple[float, date]:
    with get_conn() as conn:
        row = conn.execute(
            text("""SELECT modal_price, obs_date FROM price_observations
                    WHERE commodity_id = :c AND mandi_id = :m AND modal_price IS NOT NULL
                    ORDER BY obs_date DESC LIMIT 1"""),
            {"c": commodity_id, "m": mandi_id},
        ).mappings().first()
    if row is None:
        raise InsufficientData(
            f"no price on record for commodity {commodity_id} at mandi {mandi_id}"
        )
    return float(row["modal_price"]), row["obs_date"]


def latest_observation_date(commodity_id: int | None = None) -> date:
    """The newest date anywhere. The dataset ends in the past, so 'today' for a
    demo means the last day we actually have — not the wall clock."""
    sql = "SELECT max(obs_date) FROM price_observations"
    params: dict[str, Any] = {}
    if commodity_id is not None:
        sql += " WHERE commodity_id = :c"
        params["c"] = commodity_id
    with get_conn() as conn:
        value = conn.execute(text(sql), params).scalar()
    return value or date.today()


# ══════════════════════════════════════════════════════════════════════════
# markets to compare
# ══════════════════════════════════════════════════════════════════════════

def mandi_options(
    commodity_id: int,
    origin: tuple[float, float] | None = None,
    limit: int | None = None,
) -> list[MandiOption]:
    """Every market carrying this crop, with today's price and road distance."""
    lat, lon = origin or (REFERENCE_LAT, REFERENCE_LON)
    options: list[MandiOption] = []
    for row in latest_prices(commodity_id):
        distance = haversine_km(lat, lon, float(row["lat"]), float(row["lon"])) \
            if row.get("lat") is not None else 0.0
        options.append(MandiOption(
            mandi=str(row["mandi"]),
            price_per_qtl=float(row["modal_price"]),
            distance_km=round(distance * ROAD_FACTOR, 1),
            mandi_id=int(row["mandi_id"]),
            district=str(row["district"]),
        ))
    return options
