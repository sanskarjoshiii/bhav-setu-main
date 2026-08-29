"""Lets the API reuse the seeding logic that lives in `scripts/`.

`scripts/seed_demo_data.py` is a CLI, not a package, and `scripts/` is not on
the backend's import path. Rather than duplicate the seeding logic — which would
guarantee the CLI and the reset button drift apart — this loads that file by
path and exposes it as `seed_demo`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"


def _load(name: str, filename: str) -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))   # the script imports `_bootstrap`
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename} from {_SCRIPTS}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_demo: ModuleType = _load("seed_demo_data", "seed_demo_data.py")

__all__ = ["seed_demo"]
