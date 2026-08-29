"""Phase 10 — registration, OTP login, and the session behind both.

    POST /auth/request-otp        {phone}
    POST /auth/verify             {phone, code, ...registration fields on first login}
    POST /auth/magic-link         {email}          — Phase 15, emailed sign-in link
    POST /auth/magic-link/verify  {token, ...}     — consume the link, get a session
    GET  /auth/me                 Authorization: Bearer <token>
    PATCH /auth/me                update village, language, risk profile

Registration and login are the **same flow**. A farmer types his number, gets a
code, and if we have never seen him he supplies his name and village at the same
moment. Asking him to pick "sign up" versus "sign in" is a question he cannot
answer about a service he has not used yet.

**Two doors, one account system.** Phase 15 adds an emailed magic link beside
the OTP. A farmer with a phone uses the code; anyone on a laptop — a judge, an
FPO officer — would rather click a link. Both end at the same `issue()` session,
and both write the sign-in to the farmer's history.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from sqlalchemy import text

from api import deps
from api.schemas import Wire
import history
from auth import email_link
from auth import otp as otp_service
from auth.session import SessionError, issue, verify
from core.db import get_conn
from core.errors import BhavSetuError, InsufficientData

router = APIRouter(prefix="/auth", tags=["auth"])


class RequestOtp(Wire):
    phone: str
    #: Optional. Given one, the code is emailed for real instead of only being
    #: shown on screen — the one delivery route that needs no paid SMS gateway.
    email: str | None = None


class VerifyOtp(Wire):
    phone: str
    code: str
    email: str | None = None
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
    email: str | None = None
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
        SELECT f.id, f.name, f.phone_e164, f.email, f.village, f.language,
               f.risk_profile, f.lat, f.lon, m.name AS home_mandi, m.district
        FROM farmers f LEFT JOIN mandis m ON m.id = f.home_mandi_id
        WHERE f.id = :id
    """), {"id": farmer_id}).mappings().first()
    if row is None:
        raise SessionError("that account no longer exists — please sign in again")
    return Farmer(
        id=int(row["id"]), name=str(row["name"] or ""), phone=str(row["phone_e164"]),
        email=str(row["email"]) if row["email"] else None,
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


def optional_farmer(authorization: str = Header(default="")) -> Farmer | None:
    """The signed-in farmer, or None.

    For endpoints that work perfectly well anonymously — the advisor, the
    irrigation page — but should write to a farmer's history when there is one
    to write to. A bad token is treated as absent rather than as an error: the
    page still works, it just is not recorded.
    """
    if not authorization.lower().startswith("bearer "):
        return None
    try:
        claims = verify(authorization.split(" ", 1)[1].strip())
        with get_conn() as conn:
            return _load(conn, claims.farmer_id)
    except (SessionError, BhavSetuError):
        return None


@router.post("/request-otp")
def request_otp(request: RequestOtp) -> dict[str, Any]:
    """Send a code. Rate-limited per number; never logs the code in production."""
    return otp_service.request_otp(request.phone, email=request.email).to_dict()


@router.post("/verify", response_model=Session)
def verify_and_sign_in(request: VerifyOtp) -> Session:
    """Check the code, create the farmer if new, and hand back a session."""
    phone = otp_service.normalise_phone(request.phone)
    otp_service.verify_otp(phone, request.code)

    lat, lon = _village_coords(request.district, request.village)
    home_mandi_id = _home_mandi_id(request.district)

    # A bad address must not block a sign-in that already passed the code check.
    email: str | None = None
    if request.email:
        try:
            email = email_link.normalise_email(request.email)
        except BhavSetuError:
            email = None

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
                INSERT INTO farmers (phone_e164, email, name, village, language,
                                     risk_profile, lat, lon, home_mandi_id, consent_at)
                VALUES (:p, :e, :n, :v, :lang, :risk, :lat, :lon, :mandi, now())
                RETURNING id
            """), {"p": phone, "e": email, "n": request.name, "v": request.village or "",
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
                    email         = coalesce(email, :e),
                    lat           = coalesce(:lat, lat),
                    lon           = coalesce(:lon, lon),
                    home_mandi_id = coalesce(:mandi, home_mandi_id)
                WHERE id = :id
            """), {"id": farmer_id, "n": request.name or "", "v": request.village or "",
                   "e": email, "lat": lat, "lon": lon, "mandi": home_mandi_id})

        farmer = _load(conn, farmer_id)

    farmer.is_new = is_new
    # History is a mirror, never a gate: `record_*` swallows a Mongo outage so a
    # farmer still gets his session if the document store is down.
    if is_new:
        history.record_signup(farmer, method="otp")
    else:
        history.record_login(farmer, method="otp")
    return Session(token=issue(farmer_id, phone), farmer=farmer)


# ══════════════════════════════════════════════════════════════════════════
# Phase 15 — sign in by emailed link
# ══════════════════════════════════════════════════════════════════════════

class MagicLinkRequest(Wire):
    email: str


class MagicLinkVerify(Wire):
    token: str
    # Same idea as the OTP flow: supplied only when we have not seen the
    # address before, ignored for a returning farmer.
    name: str | None = None
    village: str | None = None
    district: str | None = None
    language: Literal["mr", "hi", "en"] = "mr"
    risk_profile: Literal["cautious", "balanced", "aggressive"] = "balanced"


@router.post("/magic-link")
def request_magic_link(request: MagicLinkRequest) -> dict[str, Any]:
    """Email a single-use sign-in link.

    With SMTP unconfigured the link comes back in the response instead, so a
    demo works with no mail account. The response says so explicitly.
    """
    return email_link.request_link(request.email).to_dict()


@router.post("/magic-link/verify", response_model=Session)
def verify_magic_link(request: MagicLinkVerify) -> Session:
    """Burn the link and hand back a session, creating the farmer if new."""
    email = email_link.consume(request.token)

    lat, lon = _village_coords(request.district, request.village)
    home_mandi_id = _home_mandi_id(request.district)

    with get_conn() as conn:
        existing = conn.execute(
            text("SELECT id FROM farmers WHERE lower(email) = :e"), {"e": email}
        ).scalar()

        if existing is None:
            # An email-only account has no phone yet, but `phone_e164` is the
            # NOT NULL identity column. `email:<address>` is the same handle
            # convention the history router already uses for web-only farmers,
            # and it is replaced the first time this farmer verifies a number.
            handle = f"email:{email}"
            farmer_id = int(conn.execute(text("""
                INSERT INTO farmers (phone_e164, email, email_verified_at, name,
                                     village, language, risk_profile,
                                     lat, lon, home_mandi_id, consent_at)
                VALUES (:h, :e, now(), :n, :v, :lang, :risk, :lat, :lon, :mandi, now())
                RETURNING id
            """), {"h": handle, "e": email, "n": request.name or email.split("@")[0],
                   "v": request.village or "", "lang": request.language,
                   "risk": request.risk_profile, "lat": lat, "lon": lon,
                   "mandi": home_mandi_id}).scalar_one())
            is_new = True
        else:
            farmer_id, is_new = int(existing), False
            conn.execute(text("""
                UPDATE farmers SET
                    email_verified_at = now(),
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
    if is_new:
        history.record_signup(farmer, method="email_link")
    else:
        history.record_login(farmer, method="email_link")
    return Session(token=issue(farmer_id, farmer.phone), farmer=farmer)


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
        updated = _load(conn, farmer.id)

    history.record_event(updated, "profile_update",
                         {"village": updated.village, "district": updated.district,
                          "language": updated.language,
                          "riskProfile": updated.risk_profile},
                         summary=f"Updated profile — {updated.village}, {updated.district}")
    return updated
