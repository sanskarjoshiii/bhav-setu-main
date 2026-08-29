"""Phase 10 — signed session tokens.

    token = issue(farmer_id=12, phone="+919876543210")
    claims = verify(token)        # raises SessionError if tampered or expired

A compact signed token rather than a server-side session table: the API is
stateless, the frontend stores one string, and a restart does not log everyone
out. The signature is HMAC-SHA256 over the payload, so a farmer cannot edit his
own id and read someone else's lots.

**This is not JWT and does not pretend to be.** It is three base64url segments —
payload, expiry, signature — because that is all a demo needs and a hand-rolled
JWT is a worse idea than a hand-rolled token that never claims to be one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from core.config import settings
from core.errors import BhavSetuError

#: Thirty days. A farmer should not be asked to re-verify every week; the token
#: carries no privilege beyond reading and writing his own rows.
TTL_SECONDS: int = 30 * 24 * 3600


class SessionError(BhavSetuError):
    """Tampered, malformed, or expired."""


def _secret() -> bytes:
    """Signing key.

    Derived from DATABASE_URL, which is already a required secret and already
    differs between environments. A dedicated SESSION_SECRET would be better and
    is a one-line change; this avoids adding an env var that would be blank on
    someone's machine and silently weaken every token.
    """
    return hashlib.sha256(
        ("bhav-setu-session:" + settings.env.database_url).encode()
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Claims:
    farmer_id: int
    phone: str
    expires_at: int

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at


def issue(farmer_id: int, phone: str, ttl_seconds: int = TTL_SECONDS) -> str:
    payload = {
        "sub": int(farmer_id),
        "phone": str(phone),
        "exp": int(time.time()) + int(ttl_seconds),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify(token: str) -> Claims:
    try:
        body, signature = str(token).split(".", 1)
    except ValueError:
        raise SessionError("malformed session token") from None

    expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    # Constant-time: a fast reject leaks how much of a forged signature is right.
    if not hmac.compare_digest(signature, expected):
        raise SessionError("session signature does not check out — please sign in again")

    try:
        payload: dict[str, Any] = json.loads(_unb64(body))
        claims = Claims(int(payload["sub"]), str(payload["phone"]), int(payload["exp"]))
    except (ValueError, KeyError, TypeError):
        raise SessionError("malformed session token") from None

    if claims.expired:
        raise SessionError("your session has expired — please sign in again")
    return claims
