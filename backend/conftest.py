"""Shared pytest options.

`--provider` lets the forecast contract suite be pointed at a real provider:

    pytest tests/test_phaseA0_port.py --provider baseline
    pytest tests/test_phaseA0_port.py --provider lightgbm

Swap day runs that second line against this same, unmodified file.
"""

from __future__ import annotations

import os

# Point the history store at a throwaway database BEFORE anything imports
# `core.config`, which reads the environment once at import time.
#
# Without this the auth tests write farmer documents into the same MongoDB the
# demo reads from, and every test run leaves orphaned farmers behind for a judge
# to scroll past. pytest loads conftest before the test modules, so this is early
# enough.
os.environ.setdefault("MONGODB_DB", "bhav_history_test")

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--provider",
        action="store",
        default=None,
        help="also run the forecast contract suite against this configured provider",
    )


@pytest.fixture(scope="session")
def provider_name(request: pytest.FixtureRequest) -> str | None:
    """The provider named with --provider, or None."""
    return request.config.getoption("--provider")
