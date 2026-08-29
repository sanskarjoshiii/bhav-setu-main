"""POST /admin/reset-demo — put the demo back to its starting state.

The fastest way to recover from a broken demo is to start it again cleanly. This
removes only rows tagged `seed_demo` / `demo:` and re-creates them; real prices,
the trained model and `model_registry` are never touched.
"""

from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter

from core.db import get_conn

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset-demo")
def reset_demo(seed: int = 7) -> dict[str, Any]:
    from scripts_bridge import seed_demo   # local import: keeps scripts/ off the API path

    rng = random.Random(seed)
    with get_conn() as conn:
        removed = seed_demo.reset(conn)
        added = seed_demo.seed(conn, rng)
    return {"ok": True, "removed": removed, "added": added,
            "note": "Real prices and the trained model were not touched."}
