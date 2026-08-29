"""Phase 2.5 — Open-Meteo daily weather per mandi. No API key needed.

Two endpoints, one table:
  archive  -> real observations, is_forecast = false
  forecast -> next 16 days,      is_forecast = true

A forecast row may never overwrite a historical one. Using a forecast as a
feature is legitimate (it *was* available on the day); overwriting the truth with
it later is leakage in reverse and would quietly poison training.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection

from core import logging as log
from core.config import settings
from core.db import get_conn
from core.errors import IngestionError
from ingestion import RunCounters

_CFG = settings.sources.weather
ARCHIVE_URL: str = str(_CFG.archive_url)
FORECAST_URL: str = str(_CFG.forecast_url)
DAILY_VARS: list[str] = [str(v) for v in _CFG.daily_vars]
FORECAST_DAYS: int = int(_CFG.forecast_days)
ARCHIVE_LAG_DAYS: int = int(_CFG.archive_lag_days)
TIMEOUT: float = float(settings.sources.ingestion.http_timeout_seconds)
ATTEMPTS: int = int(settings.sources.agmarknet.retry.attempts)
BACKOFF: list[float] = [float(s) for s in settings.sources.agmarknet.retry.backoff_seconds]

#: Seconds to wait out an Open-Meteo 429. Their limit resets on the minute, so
#: anything under 60 just burns an attempt inside the same window.
RATE_LIMIT_COOLDOWN: float = 65.0

#: Breathing room between mandis. Cheap insurance: seventeen mandis at two calls
#: each is well inside the daily budget, and it is only the per-minute one we
#: keep tripping.
PAUSE_BETWEEN_MANDIS: float = 2.0


@dataclass
class WeatherResult:
    mandis: int = 0
    historical_rows: int = 0
    forecast_rows: int = 0
    stale_forecasts_removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "open_meteo",
            "mandis": self.mandis,
            "historical_rows": self.historical_rows,
            "forecast_rows": self.forecast_rows,
            "stale_forecasts_removed": self.stale_forecasts_removed,
        }


# Real observations win: update only when the stored row is itself a forecast,
# or when we are writing a historical row over anything.
_UPSERT_SQL = text(
    """
    INSERT INTO weather_daily
        (obs_date, mandi_id, rainfall_mm, tmax_c, tmin_c,
         soil_moisture_surface, soil_moisture_root, soil_temp_c, et0_mm,
         is_forecast)
    VALUES (:obs_date, :mandi_id, :rainfall_mm, :tmax_c, :tmin_c,
            :soil_moisture_surface, :soil_moisture_root, :soil_temp_c, :et0_mm,
            :is_forecast)
    ON CONFLICT (obs_date, mandi_id) DO UPDATE SET
        rainfall_mm = EXCLUDED.rainfall_mm,
        tmax_c      = EXCLUDED.tmax_c,
        tmin_c      = EXCLUDED.tmin_c,
        soil_moisture_surface = EXCLUDED.soil_moisture_surface,
        soil_moisture_root    = EXCLUDED.soil_moisture_root,
        soil_temp_c           = EXCLUDED.soil_temp_c,
        et0_mm                = EXCLUDED.et0_mm,
        is_forecast = EXCLUDED.is_forecast
    WHERE weather_daily.is_forecast OR NOT EXCLUDED.is_forecast
    """
)


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None
    with httpx.Client(follow_redirects=True) as client:
        for attempt in range(ATTEMPTS):
            try:
                response = client.get(url, params=params, timeout=TIMEOUT)
                payload = response.json() if response.status_code == 200 else {}
                rows = len(payload.get("daily", {}).get("time", [])) if payload else 0
                log.external_call(url, response.status_code, rows=rows,
                                  lat=params.get("latitude"), attempt=attempt + 1)
                if response.status_code == 200:
                    return payload
                last_error = IngestionError(f"HTTP {response.status_code}: {response.text[:200]}")
                # 429 here is Open-Meteo's *minutely* budget, not a ban. Their
                # archive endpoint prices a request by days x variables, and a
                # five-year window for seventeen mandis spends it in about
                # thirteen calls. The standard [1,2,4,8] backoff is far too
                # short — every retry lands inside the same minute and fails
                # again, which is how a transient limit turned into a failed
                # step. Wait out the window instead.
                if response.status_code == 429:
                    time.sleep(RATE_LIMIT_COOLDOWN)
                    continue
            except httpx.HTTPError as exc:
                log.external_call(url, "network_error", rows=None, attempt=attempt + 1, error=str(exc))
                last_error = exc
            if attempt < ATTEMPTS - 1:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    raise IngestionError(f"open-meteo failed after {ATTEMPTS} attempts: {last_error}")


def _rows_from_payload(payload: dict[str, Any], mandi_id: int,
                       is_forecast: bool) -> list[dict[str, Any]]:
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        raise IngestionError(f"open-meteo returned no daily block for mandi {mandi_id}")
    blank = [None] * len(dates)

    def column(name: str) -> list[Any]:
        # A variable the endpoint does not serve comes back absent rather than
        # as an error, so every column falls back to nulls. The forecast
        # endpoint carries soil moisture; some archive windows do not.
        return daily.get(name) or blank

    rain = column("precipitation_sum")
    tmax = column("temperature_2m_max")
    tmin = column("temperature_2m_min")
    soil_surface = column("soil_moisture_0_to_7cm_mean")
    soil_root = column("soil_moisture_7_to_28cm_mean")
    soil_temp = column("soil_temperature_0_to_7cm_mean")
    et0 = column("et0_fao_evapotranspiration")

    return [
        {
            "obs_date": date.fromisoformat(d),
            "mandi_id": mandi_id,
            "rainfall_mm": rain[i],
            "tmax_c": tmax[i],
            "tmin_c": tmin[i],
            "soil_moisture_surface": soil_surface[i],
            "soil_moisture_root": soil_root[i],
            "soil_temp_c": soil_temp[i],
            "et0_mm": et0[i],
            "is_forecast": is_forecast,
        }
        for i, d in enumerate(dates)
    ]


def _mandis(conn: Connection) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            text("SELECT id, name, lat, lon FROM mandis WHERE active ORDER BY id")
        ).mappings()
    ]
    if not rows:
        raise IngestionError("no mandis in the database — run scripts/init_db.py first")
    return rows


def _price_history_start(conn: Connection) -> date | None:
    return conn.execute(text("SELECT min(obs_date) FROM price_observations")).scalar()


def run(start: date | None = None, end: date | None = None,
        counters: RunCounters | None = None) -> WeatherResult:
    """Backfill weather for every mandi over the price history window, plus 16 days ahead.

    Defaults: start = first price observation (or today - app.history_lookback_days).
    """
    result = WeatherResult()
    today = date.today()
    with get_conn() as conn:
        mandis = _mandis(conn)
        if start is None:
            start = _price_history_start(conn) or (
                today - timedelta(days=int(settings.app.history_lookback_days))
            )
        archive_end = min(end or today, today - timedelta(days=ARCHIVE_LAG_DAYS))
        if archive_end < start:
            raise IngestionError(
                f"weather window is empty: start={start} archive_end={archive_end}"
            )

        for mandi in mandis:
            historical = _get_json(
                ARCHIVE_URL,
                {
                    "latitude": mandi["lat"],
                    "longitude": mandi["lon"],
                    "start_date": start.isoformat(),
                    "end_date": archive_end.isoformat(),
                    "daily": ",".join(DAILY_VARS),
                    "timezone": "Asia/Kolkata",
                },
            )
            rows = _rows_from_payload(historical, int(mandi["id"]), is_forecast=False)
            conn.execute(_UPSERT_SQL, rows)
            result.historical_rows += len(rows)

            forecast = _get_json(
                FORECAST_URL,
                {
                    "latitude": mandi["lat"],
                    "longitude": mandi["lon"],
                    "daily": ",".join(DAILY_VARS),
                    "forecast_days": FORECAST_DAYS,
                    "timezone": "Asia/Kolkata",
                },
            )
            frows = _rows_from_payload(forecast, int(mandi["id"]), is_forecast=True)
            conn.execute(_UPSERT_SQL, frows)
            result.forecast_rows += len(frows)
            result.mandis += 1
            log.info("weather_mandi_done", mandi=mandi["name"],
                     historical=len(rows), forecast=len(frows))
            time.sleep(PAUSE_BETWEEN_MANDIS)

        # A forecast for a day that has already passed is worthless, and worse,
        # it sits in the table looking like an observation. The archive endpoint
        # lags real time by ARCHIVE_LAG_DAYS, so those rows cannot simply be
        # overwritten by a re-fetch — they have to be dropped.
        result.stale_forecasts_removed = int(conn.execute(text(
            "DELETE FROM weather_daily WHERE is_forecast AND obs_date < CURRENT_DATE"
        )).rowcount or 0)
        if result.stale_forecasts_removed:
            log.info("weather_stale_forecasts_removed",
                     rows=result.stale_forecasts_removed)

    if counters is not None:
        counters.rows_in = result.historical_rows + result.forecast_rows
        counters.rows_kept = counters.rows_in

    log.info("weather_done", mandis=result.mandis,
             historical=result.historical_rows, forecast=result.forecast_rows)
    return result
