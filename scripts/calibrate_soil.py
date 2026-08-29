"""Phase 14 — re-derive the soil thresholds in config/irrigation.yaml from data.

    python scripts/calibrate_soil.py            # print the derivation
    python scripts/calibrate_soil.py --check    # exit 1 if config has drifted

Why this exists. The first version of `config/irrigation.yaml` used textbook
medium-loam thresholds (field capacity 0.30, wilting point 0.12). Those are
correct soil physics and wrong for this data source: Open-Meteo's ERA5 7-28 cm
layer over the Deccan sits on a higher scale, so the *dry season* read as "above
field capacity" and the advisory told a farmer in Nashik in March — 0.2 mm of
rain against 6.2 mm/day of ET0 — that his soil was still wet.

So the thresholds are percentiles of the series we actually ingest, and this
script is how they were obtained. Re-run it whenever the mandi list changes.

The distribution is bimodal — a monsoon plateau and a dry-season plateau — and
that structure is what the thresholds hang off:

    wilting_point  = p05                      the driest days on record
    refill_point   = p25                      ~ the dry-season plateau
    field_capacity = midway between plateaus  the line "wet" has to sit above
    saturation     = p85                      the top of the monsoon plateau
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401  (sys.path side effect)

from sqlalchemy import text

from core.config import settings
from core.db import get_conn

#: Months that define each plateau in a Deccan monsoon climate.
MONSOON_MONTHS = ("07", "08", "09")
DRY_MONTHS = ("12", "01", "02", "03", "04", "05")

#: How far a config value may sit from the derived one before --check complains.
TOLERANCE = 0.02


def _percentiles(conn) -> dict[str, float]:
    wanted = (0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.85, 0.90, 0.95)
    select = ", ".join(
        f"percentile_cont({p}) WITHIN GROUP (ORDER BY soil_moisture_root) AS p{int(p * 100)}"
        for p in wanted
    )
    row = conn.execute(text(
        f"SELECT {select}, count(*) AS n FROM weather_daily "
        f"WHERE soil_moisture_root IS NOT NULL"
    )).mappings().first()
    return {k: float(v) for k, v in row.items() if v is not None}


def _plateaus(conn) -> tuple[float, float]:
    monsoon = ",".join(f"'{m}'" for m in MONSOON_MONTHS)
    dry = ",".join(f"'{m}'" for m in DRY_MONTHS)
    row = conn.execute(text(f"""
        SELECT avg(soil_moisture_root) FILTER (
                   WHERE to_char(obs_date, 'MM') IN ({monsoon})) AS monsoon,
               avg(soil_moisture_root) FILTER (
                   WHERE to_char(obs_date, 'MM') IN ({dry})) AS dry
        FROM weather_daily WHERE soil_moisture_root IS NOT NULL
    """)).mappings().first()
    return float(row["monsoon"]), float(row["dry"])


def derive() -> tuple[dict[str, float], dict[str, float], float, float, int]:
    with get_conn() as conn:
        pct = _percentiles(conn)
        monsoon, dry = _plateaus(conn)

    n = int(pct.pop("n"))
    if n < 500:
        print(f"⛔ only {n} soil observations — run `make backfill` first.", file=sys.stderr)
        raise SystemExit(2)

    derived = {
        "wilting_point": round(pct["p5"], 2),
        "refill_point": round(pct["p25"], 2),
        "field_capacity": round((monsoon + dry) / 2, 2),
        "saturation": round(pct["p85"], 2),
    }
    return derived, pct, monsoon, dry, n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-derive soil thresholds from ingested data.")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if config/irrigation.yaml has drifted from the data")
    args = parser.parse_args(argv)

    derived, pct, monsoon, dry, n = derive()

    print(f"\n  {n:,} observed days of root-zone soil moisture\n")
    print("  percentiles")
    for key in sorted(pct, key=lambda k: int(k[1:])):
        print(f"    {key:>4} = {pct[key]:.3f}")
    print(f"\n  monsoon plateau    = {monsoon:.3f}   ({'/'.join(MONSOON_MONTHS)})")
    print(f"  dry-season plateau = {dry:.3f}   ({'/'.join(DRY_MONTHS)})\n")

    configured = {
        "wilting_point": float(settings.irrigation.soil.wilting_point),
        "refill_point": float(settings.irrigation.soil.refill_point),
        "field_capacity": float(settings.irrigation.soil.field_capacity),
        "saturation": float(settings.irrigation.soil.saturation),
    }

    print(f"  {'threshold':<16} {'config':>8} {'derived':>8}   drift")
    drifted: list[str] = []
    for key in ("wilting_point", "refill_point", "field_capacity", "saturation"):
        gap = abs(configured[key] - derived[key])
        flag = "ok" if gap <= TOLERANCE else "DRIFTED"
        if gap > TOLERANCE:
            drifted.append(key)
        print(f"  {key:<16} {configured[key]:>8.2f} {derived[key]:>8.2f}   {gap:.3f} {flag}")

    if args.check and drifted:
        print(f"\n⛔ {', '.join(drifted)} differ from the data by more than {TOLERANCE}.",
              file=sys.stderr)
        print("   Update config/irrigation.yaml, or re-run without --check.\n", file=sys.stderr)
        return 1

    print("\n  Thresholds agree with the ingested data.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
