"""Phase 8 — the FastAPI app.

    make api          →  http://localhost:8000/docs

Every page's data now comes from Postgres. The one design rule that matters:
**no router imports anything from `backend/ml` except `get_provider`.** That is
what kept swap day to a single config line, and `test_phase8_api.py` greps for
violations so it cannot quietly regress.

Error handling is deliberate. `InsufficientData` becomes a **422 with a readable
sentence**, never a 500 — "we don't have enough history for mango at Solapur" is
information a farmer can act on, while a stack trace is not.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import (
    accuracy,
    admin,
    auth,
    community,
    compare,
    forecast,
    history,
    irrigation,
    locations,
    mandis,
    prices,
    recommend,
    transparency,
)
from api.schemas import Health
from core import logging as log
from auth.session import SessionError
from core.errors import (
    BhavSetuError,
    ForecastContractError,
    InsufficientData,
    ModelNotFound,
)

app = FastAPI(
    title="Bhav Setu API",
    version="1.0.0",
    description=(
        "Real mandi prices, a trained quantile forecast, and the Net In-Hand "
        "economics that turn a board price into what a farmer actually keeps."
    ),
)

# The Next.js dev server and any deployed frontend. Credentials are not used —
# auth rides on a bearer token, not a cookie — so a permissive origin list here
# does not expose anything a reader could not fetch anyway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=r"https?://.*\.(vercel\.app|onrender\.com|railway\.app)",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(InsufficientData)
async def _insufficient(request: Request, exc: InsufficientData) -> JSONResponse:
    """422, with the reason. The UI shows this sentence to the farmer verbatim."""
    log.warn("insufficient_data", path=str(request.url.path), detail=str(exc))
    return JSONResponse(status_code=422, content={
        "detail": str(exc),
        "code": "insufficient_data",
        "hint": "Try another crop, market or district — this one has too little history.",
    })


@app.exception_handler(ModelNotFound)
async def _no_model(request: Request, exc: ModelNotFound) -> JSONResponse:
    return JSONResponse(status_code=503, content={
        "detail": str(exc), "code": "model_unavailable",
        "hint": "No forecaster is promoted. Set provider: baseline in config/model.yaml.",
    })


@app.exception_handler(ForecastContractError)
async def _contract(request: Request, exc: ForecastContractError) -> JSONResponse:
    log.error("forecast_contract_violated", path=str(request.url.path), detail=str(exc))
    return JSONResponse(status_code=503, content={
        "detail": str(exc), "code": "forecast_contract",
        "hint": "The active model disagrees with features/registry.py. Retrain it.",
    })


@app.exception_handler(SessionError)
async def _session(request: Request, exc: SessionError) -> JSONResponse:
    """401, so the frontend knows to send the farmer back to sign-in."""
    return JSONResponse(status_code=401, content={
        "detail": str(exc), "code": "unauthenticated",
    })


@app.exception_handler(BhavSetuError)
async def _known(request: Request, exc: BhavSetuError) -> JSONResponse:
    log.warn("api_error", path=str(request.url.path), detail=str(exc))
    return JSONResponse(status_code=400, content={
        "detail": str(exc), "code": type(exc).__name__,
    })


for module in (mandis, prices, forecast, recommend, compare, accuracy,
               transparency, history, community, admin, auth, locations,
               irrigation):
    app.include_router(module.router, prefix="/api/v1")


@app.get("/api/v1/health", response_model=Health, tags=["meta"])
def health() -> Health:
    """Is everything wired up? The first thing to check when a demo misbehaves."""
    from sqlalchemy import text

    from core.db import get_conn
    from ml.provider import active_provider_name, get_provider

    crops = mandis_count = rows = 0
    database = True
    try:
        with get_conn() as conn:
            crops = conn.execute(text(
                "SELECT count(DISTINCT commodity_id) FROM price_observations")).scalar() or 0
            mandis_count = conn.execute(text(
                "SELECT count(DISTINCT mandi_id) FROM price_observations")).scalar() or 0
            rows = conn.execute(text("SELECT count(*) FROM price_observations")).scalar() or 0
    except Exception:                                      # noqa: BLE001
        database = False

    version = "unavailable"
    try:
        version = getattr(get_provider(), "version", "unknown")
    except Exception:                                      # noqa: BLE001
        pass

    return Health(
        status="ok" if database and rows > 0 else "degraded",
        database=database,
        provider=active_provider_name(),
        model_version=version,
        crops=int(crops),
        mandis=int(mandis_count),
        price_rows=int(rows),
    )


@app.get("/", include_in_schema=False)
def root() -> dict[str, Any]:
    return {"service": "bhav-setu", "docs": "/docs", "health": "/api/v1/health"}
