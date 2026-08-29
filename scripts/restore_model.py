"""Register a committed model with the database, so a clone can serve without retraining.

    python scripts/restore_model.py            # load the committed registry
    python scripts/restore_model.py --export   # save the current one to the repo

The boosters live in `data/artifacts/models/<version>/`, which git carries. What
git cannot carry is the `model_registry` table — and without a row there marked
active, `LgbmProvider` raises `ModelNotFound` and the whole API answers 503. So
the row travels as JSON alongside the boosters and this script puts it back.

Why not just retrain on a fresh clone? Because that is a forty-minute CEDA pull,
a ten-minute baseline evaluation and a ten-minute training run before anyone can
see the product — and because a teammate retraining would get a *different*
model from the one the metrics on the accuracy page describe.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401  (sys.path side effect)

from core.config import settings
from core.logging import configure_stdout_utf8
from ml import registry

configure_stdout_utf8()

EXPORT_PATH: Path = settings.path("data", "artifacts", "model_registry.json")


def export() -> int:
    rows = registry.list_versions()
    if not rows:
        print("\n⛔ model_registry is empty — nothing to export.\n")
        return 1

    payload = []
    for row in rows:
        metrics = row.get("metrics") or {}
        params = row.get("params") or {}
        payload.append({
            "version": row["version"],
            "algo": row.get("algo"),
            "is_active": bool(row.get("is_active")),
            "train_start": str(row["train_start"]) if row.get("train_start") else None,
            "train_end": str(row["train_end"]) if row.get("train_end") else None,
            "artifact_path": row.get("artifact_path"),
            "metrics": json.loads(metrics) if isinstance(metrics, str) else metrics,
            "params": json.loads(params) if isinstance(params, str) else params,
        })

    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\n✅ exported {len(payload)} version(s) → {EXPORT_PATH}")
    for row in payload:
        print(f"   {row['version']:<14}{'ACTIVE' if row['is_active'] else ''}")
    print()
    return 0


def restore() -> int:
    if not EXPORT_PATH.exists():
        print(f"\n⛔ no {EXPORT_PATH}. Train a model, or ask a teammate to run "
              f"`python scripts/restore_model.py --export`.\n")
        return 1

    payload = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    active: str | None = None
    restored = 0

    for row in payload:
        version = str(row["version"])

        # A registry row without its boosters is worse than no row: the API
        # would promise a model and then 503 on the first forecast.
        if str(row.get("algo") or "").startswith("lightgbm"):
            directory = registry.version_dir(version)
            if not (directory / registry.MANIFEST_NAME).exists():
                print(f"   ⚠️  skipping {version} — no boosters at {directory}")
                continue

        registry.record(
            version,
            algo=str(row.get("algo") or "unknown"),
            metrics=row.get("metrics") or {},
            params=row.get("params") or {},
            train_start=row.get("train_start"),
            train_end=row.get("train_end"),
            artifact_path=str(registry.version_dir(version)),
        )
        restored += 1
        if row.get("is_active"):
            active = version

    if active:
        registry.promote(active)

    print(f"\n✅ restored {restored} version(s) into model_registry")
    if active:
        print(f"   active: {active}")
        print(f"\n   Set `provider: lightgbm` in config/model.yaml, then `make api`.")
    else:
        print("   none marked active — `provider: baseline` will serve.")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Move model_registry rows between the database and the repo.")
    parser.add_argument("--export", action="store_true",
                        help="write the current registry to the repo instead of reading it")
    args = parser.parse_args(argv)
    return export() if args.export else restore()


if __name__ == "__main__":
    raise SystemExit(main())
