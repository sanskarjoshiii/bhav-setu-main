"""Phase 10 — registration, OTP login, and the session behind both.

    POST /auth/request-otp   {phone}
    POST /auth/verify        {phone, code, ...registration fields on first login}
    GET  /auth/me            Authorization: Bearer <token>
    PATCH /auth/me           update village, language, risk profile

Registration and login are the **same flow**. A farmer types his number, gets a
code, and if we have never seen him he supplies his name and village at the same
moment. Asking him to pick "sign up" versus "sign in" is a question he cannot
answer about a service he has not used yet.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from sqlalchemy import text

from api import deps
from api.schemas import Wire
from auth import otp as otp_service
from auth.session import SessionError, issue, verify
from core.db import get_conn
from core.errors import BhavSetuError, InsufficientData

router = APIRouter(prefix="/auth", tags=["auth"])


class RequestOtp(Wire):
    phone: str


class VerifyOtp(Wire):
    phone: str
    code: str
    # Supplied the first time we see this number. Ignored afterwards, so a
    # returning farmer's saved profile is never overwritten by a blank form.
    name: str | None = None
    village: str | None = None
    district: str | None = None
    language: Literal["mr", "hi", "en"] = "mr"
    risk_profile: Literal["cautious", "balanced", "aggressive"] = "balanced"


class ProfileUpdate(Wire):
    name: str | None = None
    village: str | None = None
    district: str | None = None
    language: Literal["mr", "hi", "en"] | None = None
    risk_profile: Literal["cautious", "balanced", "aggressive"] | None = None


class Farmer(Wire):
    id: int
    name: str
    phone: str
    village: str
    district: str
    language: str
    risk_profile: str
    lat: float | None = None
    lon: float | None = None
    home_mandi: str | None = None
    is_new: bool = False


class Session(Wire):
    token: str
    farmer: Farmer


def _village_coords(district: str | None, village: str | None) -> tuple[float | None, float | None]:
    """Look the village up in config/locations.yaml. Falls back to the district."""
    from core.config import settings

    districts = settings.locations.districts.to_dict()
    block = districts.get(str(district)) if district else None
    if block is None:
        return None, None
    for entry in block.get("villages", []):
        if str(entry["name"]).lower() == str(village or "").lower():
            return float(entry["lat"]), float(entry["lon"])
    return float(block["lat"]), float(block["lon"])


def _home_mandi_id(district: str | None) -> int | None:
    """The market we carry data for in his district — his default comparison."""
    if not district:
        return None
    try:
        return deps.resolve_mandi(district)[0]
    except InsufficientData:
        return None


def _load(conn, farmer_id: int) -> Farmer:
    row = conn.execute(text("""
        SELECT f.id, f.name, f.phone_e164, f.village, f.language, f.risk_profile,
               f.lat, f.lon, m.name AS home_mandi, m.district
        FROM farmers f LEFT JOIN mandis m ON m.id = f.home_mandi_id
        WHERE f.id = :id
    """), {"id": farmer_id}).mappings().first()
    if row is None:
        raise SessionError("that account no longer exists — please sign in again")
    return Farmer(
        id=int(row["id"]), name=str(row["name"] or ""), phone=str(row["phone_e164"]),
        village=str(row["village"] or ""), district=str(row["district"] or ""),
        language=str(row["language"] or "mr"),
        risk_profile=str(row["risk_profile"] or "balanced"),
        lat=float(row["lat"]) if row["lat"] is not None else None,
        lon=float(row["lon"]) if row["lon"] is not None else None,
        home_mandi=str(row["home_mandi"]) if row["home_mandi"] else None,
    )


def current_farmer(authorization: str = Header(default="")) -> Farmer:
    """FastAPI dependency: turns a bearer token into the farmer it belongs to."""
    if not authorization.lower().startswith("bearer "):
        raise SessionError("sign in to see this")
    claims = verify(authorization.split(" ", 1)[1].strip())
    with get_conn() as conn:
        return _load(conn, claims.farmer_id)


@router.post("/request-otp")
def request_otp(request: RequestOtp) -> dict[str, Any]:
    """Send a code. Rate-limited per number; never logs the code in production."""
    return otp_service.request_otp(request.phone).to_dict()


@router.post("/verify", response_model=Session)
def verify_and_sign_in(request: VerifyOtp) -> Session:
    """Check the code, create the farmer if new, and hand back a session."""
    phone = otp_service.normalise_phone(request.phone)
    otp_service.verify_otp(phone, request.code)

    lat, lon = _village_coords(request.district, request.village)
    home_mandi_id = _home_mandi_id(request.district)

    with get_conn() as conn:
        existing = conn.execute(
            text("SELECT id FROM farmers WHERE phone_e164 = :p"), {"p": phone}
        ).scalar()

        if existing is None:
            if not request.name:
                raise BhavSetuError(
                    "we have not seen this number before — tell us your name and village"
                )
            farmer_id = int(conn.execute(text("""
                INSERT INTO farmers (phone_e164, name, village, language, risk_profile,
                                     lat, lon, home_mandi_id, consent_at)
                VALUES (:p, :n, :v, :lang, :risk, :lat, :lon, :mandi, now())
                RETURNING id
            """), {"p": phone, "n": request.name, "v": request.village or "",
                   "lang": request.language, "risk": request.risk_profile,
                   "lat": lat, "lon": lon, "mandi": home_mandi_id}).scalar_one())
            is_new = True
        else:
            farmer_id, is_new = int(existing), False
            # Only fill blanks — never clobber a saved profile with an empty form.
            conn.execute(text("""
                UPDATE farmers SET
                    name          = coalesce(nullif(:n, ''), name),
                    village       = coalesce(nullif(:v, ''), village),
                    lat           = coalesce(:lat, lat),
                    lon           = coalesce(:lon, lon),
                    home_mandi_id = coalesce(:mandi, home_mandi_id)
                WHERE id = :id
            """), {"id": farmer_id, "n": request.name or "", "v": request.village or "",
                   "lat": lat, "lon": lon, "mandi": home_mandi_id})

        farmer = _load(conn, farmer_id)

    farmer.is_new = is_new
    return Session(token=issue(farmer_id, phone), farmer=farmer)


@router.get("/me", response_model=Farmer)
def me(farmer: Farmer = Depends(current_farmer)) -> Farmer:
    return farmer


@router.patch("/me", response_model=Farmer)
def update_me(update: ProfileUpdate,
              farmer: Farmer = Depends(current_farmer)) -> Farmer:
    lat, lon = _village_coords(update.district or farmer.district,
                               update.village or farmer.village)
    home_mandi_id = _home_mandi_id(update.district or farmer.district)
    with get_conn() as conn:
        conn.execute(text("""
            UPDATE farmers SET
                name          = coalesce(nullif(:n, ''), name),
                village       = coalesce(nullif(:v, ''), village),
                language      = coalesce(nullif(:lang, ''), language),
                risk_profile  = coalesce(nullif(:risk, ''), risk_profile),
                lat           = coalesce(:lat, lat),
                lon           = coalesce(:lon, lon),
                home_mandi_id = coalesce(:mandi, home_mandi_id)
            WHERE id = :id
        """), {"id": farmer.id, "n": update.name or "", "v": update.village or "",
               "lang": update.language or "", "risk": update.risk_profile or "",
               "lat": lat, "lon": lon, "mandi": home_mandi_id})
        return _load(conn, farmer.id)
