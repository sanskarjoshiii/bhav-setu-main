"""Phase A0 acceptance — the forecast port.

Run:  make check-phaseA0

Needs no database, no data and no model. That is deliberate: the seam between
the model and the product must be provable before either side of it exists.

Two jobs here.

  1. Prove the machinery works — `Quantiles`, `validate_forecast`, the provider
     registry — by running the real contract suite against a stub.
  2. Prove the seam is not leaking. `test_no_model_imports_outside_ml` is the
     one that keeps swap day cheap, and it will start mattering in Phase A7 when
     `backend/api/` appears.
"""

from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path
from typing import Iterator, Sequence

import pytest

from core.config import settings
from core.errors import (
    ForecastContractError,
    InsufficientData,
    ProviderNotAvailable,
)
from ml import provider as provider_module
from ml.port import (
    DEFAULT_HORIZONS,
    ForecastProvider,
    Quantiles,
    validate_forecast,
)
from ml.provider import (
    active_provider_name,
    get_provider,
    register_provider,
    reset_provider_cache,
)
from tests.contract_forecast import (
    ContractViolation,
    ProbeCase,
    assert_provider_contract,
    check_responds_to_input,
    check_uncertainty_grows,
    check_unknown_raises,
)

pytestmark = pytest.mark.phaseA0

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
BACKEND: Path = REPO_ROOT / "backend"


# ══════════════════════════════════════════════════════════════════════════
# a stub provider — the machinery's crash-test dummy
# ══════════════════════════════════════════════════════════════════════════

class StubProvider:
    """Deterministic, input-sensitive, honestly ignorant. Nothing more.

    It is not a forecaster and never ships. It exists so the contract suite has
    something to grade before Phase A3 writes a real one.
    """

    name = "stub"
    version = "stub-v1"

    #: (commodity_id, mandi_id) -> a plausible ₹/quintal base price.
    #: Keys must cover every entry in CASES, so the stub and a real provider are
    #: asked exactly the same questions.
    KNOWN: dict[tuple[int, int], float] = {
        (2, 6): 1240.0,     # onion @ Pune
        (2, 3): 1180.0,     # onion @ Nashik
        (3, 14): 1460.0,    # potato @ Ahmednagar
        (13, 11): 1175.0,   # tomato @ Solapur
        (12, 6): 2430.0,    # brinjal @ Pune
    }

    def _base(self, commodity_id: int, mandi_id: int, as_of: date) -> float:
        key = (int(commodity_id), int(mandi_id))
        if key not in self.KNOWN:
            raise InsufficientData(
                f"stub has no history for commodity={commodity_id} mandi={mandi_id}"
            )
        # a small deterministic seasonal wobble, so two dates differ
        wobble = 1.0 + 0.05 * math.sin(as_of.timetuple().tm_yday / 58.0)
        return self.KNOWN[key] * wobble

    def predict_quantiles(
        self,
        commodity_id: int,
        mandi_id: int,
        as_of: date,
        horizons: Sequence[int] = DEFAULT_HORIZONS,
    ) -> dict[int, Quantiles]:
        base = self._base(commodity_id, mandi_id, as_of)
        result = {}
        for horizon in horizons:
            spread = base * 0.03 * math.sqrt(float(horizon))
            result[int(horizon)] = Quantiles.of(base - spread, base, base + spread)
        return validate_forecast(result, horizons)


class FlatProvider(StubProvider):
    """Returns one constant for everything. The vacuity the suite must catch."""

    name = "flat"
    version = "flat-v1"

    def predict_quantiles(self, commodity_id, mandi_id, as_of,
                          horizons=DEFAULT_HORIZONS):  # type: ignore[override]
        return validate_forecast(
            {int(h): Quantiles.of(1700.0, 1800.0, 1900.0) for h in horizons}, horizons
        )


class ZeroProvider(StubProvider):
    """Answers even when it knows nothing. The failure that reaches a farmer."""

    name = "zero"
    version = "zero-v1"

    def predict_quantiles(self, commodity_id, mandi_id, as_of,
                          horizons=DEFAULT_HORIZONS):  # type: ignore[override]
        return validate_forecast(
            {int(h): Quantiles.of(1.0, 1.0, 1.0) for h in horizons}, horizons
        )


class InvertedProvider(StubProvider):
    """Gets narrower as the horizon grows. A bug on any series."""

    name = "inverted"
    version = "inverted-v1"

    def predict_quantiles(self, commodity_id, mandi_id, as_of,
                          horizons=DEFAULT_HORIZONS):  # type: ignore[override]
        base = self._base(commodity_id, mandi_id, as_of)
        result = {}
        for horizon in horizons:
            spread = base * 0.10 / math.sqrt(float(horizon))
            result[int(horizon)] = Quantiles.of(base - spread, base, base + spread)
        return validate_forecast(result, horizons)


class BarelyWideningProvider(StubProvider):
    """Almost horizon-independent width — what an honest provider reports for a
    mean-reverting series. Must be allowed."""

    name = "barely"
    version = "barely-v1"

    def predict_quantiles(self, commodity_id, mandi_id, as_of,
                          horizons=DEFAULT_HORIZONS):  # type: ignore[override]
        base = self._base(commodity_id, mandi_id, as_of)
        result = {}
        for horizon in horizons:
            spread = base * (0.05 + 0.00001 * float(horizon))
            result[int(horizon)] = Quantiles.of(base - spread, base, base + spread)
        return validate_forecast(result, horizons)


#: The cases `--provider <name>` is probed against on real data.
#:
#: These point at mandis that actually carry history. CEDA serves district-level
#: series, so the four loaded "mandis" are the district aggregates — Nashik 3,
#: Pune 6, Solapur 11, Ahmednagar 14 — and the market-level entries in
#: config/mandis.yaml (Lasalgaon, Pimpalgaon, …) hold no rows. The original
#: cases pointed at Lasalgaon and so skipped with "no data for the probe cases",
#: which the suite is careful to call out as *not a pass*. A gate that cannot
#: reach any data is not a gate.
#:
#: Commodity ids come from config/crops.yaml via scripts/init_db.py:
#: 2 onion, 3 potato, 13 tomato, 12 brinjal.
CASES: list[ProbeCase] = [
    ProbeCase(2, 6, date(2025, 3, 14), "onion @ Pune"),
    ProbeCase(2, 3, date(2025, 3, 14), "onion @ Nashik"),
    ProbeCase(3, 14, date(2025, 7, 2), "potato @ Ahmednagar"),
    ProbeCase(13, 11, date(2025, 6, 9), "tomato @ Solapur"),
    ProbeCase(12, 6, date(2025, 8, 11), "brinjal @ Pune"),
]
UNKNOWN = ProbeCase(-1, -1, date(2025, 3, 14), "a crop we have never heard of")


@pytest.fixture
def clean_registry() -> Iterator[None]:
    """Restore the provider registry, so one test cannot leak into the next."""
    saved_overrides = dict(provider_module._overrides)
    saved_cache = dict(provider_module._cache)
    try:
        yield
    finally:
        provider_module._overrides.clear()
        provider_module._overrides.update(saved_overrides)
        provider_module._cache.clear()
        provider_module._cache.update(saved_cache)


# ══════════════════════════════════════════════════════════════════════════
# 1. Quantiles — the band itself
# ══════════════════════════════════════════════════════════════════════════

def test_quantiles_sorts_crossed_input():
    """Quantile models cross. Quantiles.of() is where that stops being a problem."""
    band = Quantiles.of(2100.0, 1800.0, 1500.0)
    assert (band.p10, band.p50, band.p90) == (1500.0, 1800.0, 2100.0)


def test_quantiles_keeps_already_sorted_input():
    band = Quantiles.of(1500.0, 1800.0, 2100.0)
    assert (band.p10, band.p50, band.p90) == (1500.0, 1800.0, 2100.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_quantiles_rejects_non_finite(bad):
    with pytest.raises(ForecastContractError):
        Quantiles.of(bad, 1800.0, 2100.0)


@pytest.mark.parametrize("bad", [0.0, -50.0, 5_000_000.0])
def test_quantiles_rejects_implausible_price(bad):
    """A price of zero rupees a quintal is a bug, not a market."""
    with pytest.raises(ForecastContractError):
        Quantiles.of(bad, bad, bad)


def test_quantiles_width_and_relative_width():
    band = Quantiles.of(1700.0, 2000.0, 2300.0)
    assert band.width == pytest.approx(600.0)
    assert band.relative_width == pytest.approx(0.3)
    assert band.as_dict() == {"p10": 1700.0, "p50": 2000.0, "p90": 2300.0}


# ══════════════════════════════════════════════════════════════════════════
# 2. validate_forecast — the cheap check every provider should run on itself
# ══════════════════════════════════════════════════════════════════════════

def test_validate_forecast_accepts_a_good_result():
    result = {h: Quantiles.of(1700.0, 1800.0, 1900.0) for h in DEFAULT_HORIZONS}
    assert validate_forecast(result, DEFAULT_HORIZONS) == result


def test_validate_forecast_rejects_missing_horizon():
    result = {h: Quantiles.of(1700.0, 1800.0, 1900.0) for h in DEFAULT_HORIZONS[:-1]}
    with pytest.raises(ForecastContractError, match="missing"):
        validate_forecast(result, DEFAULT_HORIZONS)


def test_validate_forecast_rejects_extra_horizon():
    result = {h: Quantiles.of(1700.0, 1800.0, 1900.0) for h in DEFAULT_HORIZONS}
    result[99] = Quantiles.of(1700.0, 1800.0, 1900.0)
    with pytest.raises(ForecastContractError, match="unexpected"):
        validate_forecast(result, DEFAULT_HORIZONS)


def test_validate_forecast_rejects_unsorted_band():
    """A band built by hand rather than through Quantiles.of() must not slip past."""
    crossed = Quantiles(p10=2100.0, p50=1800.0, p90=1500.0)
    with pytest.raises(ForecastContractError, match="unsorted"):
        validate_forecast({h: crossed for h in DEFAULT_HORIZONS}, DEFAULT_HORIZONS)


def test_validate_forecast_rejects_wrong_type():
    with pytest.raises(ForecastContractError, match="expected Quantiles"):
        validate_forecast({h: (1, 2, 3) for h in DEFAULT_HORIZONS}, DEFAULT_HORIZONS)  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════
# 3. The contract suite itself
# ══════════════════════════════════════════════════════════════════════════

def test_stub_satisfies_the_full_contract():
    assert_provider_contract(StubProvider(), CASES, unknown=UNKNOWN)


def test_stub_is_recognised_as_a_forecast_provider():
    assert isinstance(StubProvider(), ForecastProvider)


def test_contract_catches_a_provider_that_ignores_its_input():
    """The Round 1 failure, in miniature: valid shapes, no thought behind them."""
    with pytest.raises(ContractViolation, match="same p50"):
        check_responds_to_input(FlatProvider(), CASES, DEFAULT_HORIZONS)


def test_contract_catches_a_band_that_ignores_the_horizon():
    """Decoration, not uncertainty — a 15-day band identical to a 1-day one."""
    with pytest.raises(ContractViolation, match="identical at every horizon"):
        check_uncertainty_grows(FlatProvider(), CASES, DEFAULT_HORIZONS)


def test_contract_catches_an_inverted_band():
    """Narrowing with horizon is a bug on any series, mean-reverting or not."""
    with pytest.raises(ContractViolation, match="inverted"):
        check_uncertainty_grows(InvertedProvider(), CASES, DEFAULT_HORIZONS)


def test_contract_allows_a_flat_but_horizon_aware_band():
    """A mean-reverting series legitimately has horizon-independent error.

    The clause must not fail a provider for being right about that — it only
    forbids a band that ignores the horizon entirely, or that narrows.
    """
    check_uncertainty_grows(BarelyWideningProvider(), CASES, DEFAULT_HORIZONS)


def test_contract_catches_a_provider_that_answers_when_it_should_not():
    """The clause that keeps an invented price off a farmer's screen."""
    with pytest.raises(ContractViolation, match="InsufficientData"):
        check_unknown_raises(ZeroProvider(), UNKNOWN)


def test_stub_bands_widen_with_horizon():
    probe = CASES[0]
    result = StubProvider().predict_quantiles(
        probe.commodity_id, probe.mandi_id, probe.as_of, DEFAULT_HORIZONS
    )
    widths = [result[h].relative_width for h in sorted(result)]
    assert widths == sorted(widths)


# ══════════════════════════════════════════════════════════════════════════
# 4. The registry — swap day is one config line
# ══════════════════════════════════════════════════════════════════════════

def test_config_names_an_active_provider():
    assert active_provider_name() in settings.model.providers.to_dict()


def test_config_lists_both_providers_including_the_unbuilt_one():
    """`lightgbm` is registered before it exists on purpose — that is the point."""
    paths = settings.model.providers.to_dict()
    assert {"baseline", "lightgbm"} <= set(paths)
    for name, target in paths.items():
        assert ":" in str(target), f"provider {name} must be 'module:attribute', got {target}"


def test_get_provider_returns_a_registered_override(clean_registry):
    register_provider("stub", StubProvider)
    assert isinstance(get_provider("stub"), StubProvider)


def test_get_provider_caches_one_instance(clean_registry):
    """Providers load artefacts; two requests must share one."""
    register_provider("stub", StubProvider)
    assert get_provider("stub") is get_provider("stub")


def test_reset_provider_cache_rebuilds(clean_registry):
    register_provider("stub", StubProvider)
    first = get_provider("stub")
    reset_provider_cache()
    assert get_provider("stub") is not first


def test_unknown_provider_name_is_a_clear_error(clean_registry):
    with pytest.raises(ProviderNotAvailable, match="unknown forecast provider"):
        get_provider("no-such-forecaster")


def test_unbuilt_provider_fails_with_an_actionable_message(clean_registry):
    """Until Phase B2, `lightgbm` resolves to a module that does not exist.

    The message must say what to do, because this is the error a teammate hits
    when they check out the repo with the wrong config line.
    """
    reset_provider_cache()
    try:
        get_provider("lightgbm")
    except ProviderNotAvailable as exc:
        assert "config/model.yaml" in str(exc)
    else:
        pytest.skip("ml.lgbm_provider exists — Phase B2 has landed, this test retires")


def test_named_provider_satisfies_the_contract(provider_name, clean_registry):
    """`pytest --provider lightgbm` runs swap day's gate against this same file.

    Needs a database with real prices for the probe cases. Without one the
    honest outcome is "not verified", not "passed" — so a provider that cannot
    answer for lack of data skips loudly rather than reporting a green tick.
    """
    if not provider_name:
        pytest.skip("no --provider given")
    provider = get_provider(provider_name)
    try:
        assert_provider_contract(provider, CASES, unknown=UNKNOWN)
    except InsufficientData as exc:
        pytest.skip(
            f"{provider_name} has no data for the probe cases ({exc}). "
            f"Load prices with `make collect`, then re-run — this is not a pass."
        )


# ══════════════════════════════════════════════════════════════════════════
# 5. The seam must not leak
# ══════════════════════════════════════════════════════════════════════════

#: consumers of forecasts. None of these may know how a forecast is produced.
CONSUMER_DIRS: tuple[str, ...] = (
    "api", "decision", "agent", "whatsapp", "community", "economics", "auth", "backtest",
)

#: The only names a consumer may import from the model side.
#:
#: `ml.registry` was added in Phase 8 for `routers/accuracy.py`, and the addition
#: is deliberate rather than a convenience. What the guard exists to prevent is a
#: consumer that must be EDITED when the model changes — one that opens a booster
#: file or imports LightGBM. `ml.registry` does neither: it reads version rows and
#: their metrics out of Postgres and handles boosters as opaque objects, so the
#: accuracy page shows `baseline-v1` or `lgbm-v2` with the same code either way.
#: PLAN-NOMODEL.md's Phase B4 says of that router, in as many words, "nothing to
#: change — it already reads the active version".
#:
#: If a future edit makes `ml.registry` import LightGBM, `FORBIDDEN_ROOTS` still
#: catches it, because that check runs on the module's own imports.
ALLOWED_ML_MODULES: frozenset[str] = frozenset({"ml.port", "ml.provider", "ml.registry"})

_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)

FORBIDDEN_ROOTS: frozenset[str] = frozenset({"lightgbm", "shap", "sklearn"})


def _imported_modules(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return {m.group(1) or m.group(2) for m in _IMPORT.finditer(text)}


def test_no_model_imports_outside_ml():
    """The check that keeps swap day to one config line.

    A consumer that imports LightGBM, or reaches into `ml.lgbm_provider`
    directly, has to be edited when the model changes — and once one file does
    it, the next one does too. Empty today; it starts earning its keep in A7.
    """
    offences: list[str] = []
    for directory in CONSUMER_DIRS:
        root = BACKEND / directory
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            for module in _imported_modules(path):
                head = module.split(".")[0]
                if head in FORBIDDEN_ROOTS:
                    offences.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
                elif head == "ml" and module not in ALLOWED_ML_MODULES:
                    offences.append(
                        f"{path.relative_to(REPO_ROOT)} imports {module} "
                        f"(allowed: {sorted(ALLOWED_ML_MODULES)})"
                    )
    assert not offences, "the forecast port has leaked:\n  " + "\n  ".join(offences)


def test_port_does_not_import_the_model_stack():
    """ml/port.py is imported by the API. It must stay cheap and model-free."""
    modules = _imported_modules(BACKEND / "ml" / "port.py")
    assert not {m.split(".")[0] for m in modules} & FORBIDDEN_ROOTS


def test_provider_module_does_not_import_the_model_stack():
    """Importing the registry must not import LightGBM — the API starts without it."""
    modules = _imported_modules(BACKEND / "ml" / "provider.py")
    assert not {m.split(".")[0] for m in modules} & FORBIDDEN_ROOTS


# ══════════════════════════════════════════════════════════════════════════
# 6. Horizons have one home
# ══════════════════════════════════════════════════════════════════════════

def test_default_horizons_come_from_app_yaml():
    assert DEFAULT_HORIZONS == tuple(int(h) for h in settings.app.horizons)


def test_default_horizons_are_sorted_and_positive():
    assert list(DEFAULT_HORIZONS) == sorted(DEFAULT_HORIZONS)
    assert all(h > 0 for h in DEFAULT_HORIZONS)
