"""Phase 10 — one-time codes, stored in Redis with a TTL.

    request_otp("+919876543210")  -> Challenge(code shown only in dev)
    verify_otp("+919876543210", "418302")  -> True

Four rules, and every one of them exists because the naive version is unsafe:

  * **The code expires.** Ten minutes, enforced by Redis TTL rather than a
    timestamp we have to remember to check.
  * **Wrong guesses are counted.** Five, then the code is burned. Without this a
    six-digit code falls to a script in under a minute.
  * **A code works once.** Verified codes are deleted immediately, so an SMS
    read over someone's shoulder is not a permanent key.
  * **Requests are rate-limited per number**, so nobody can pump a stranger's
    phone or walk the code space by re-requesting.

**The code is never logged in production.** With `channel: log` it is printed
and returned so a demo works without WhatsApp; with `channel: whatsapp` the
response carries no code at all. That switch is one line in
`config/locations.yaml`, and `verify` behaves identically either way.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any

import redis

from core import logging as log
from core.config import settings
from core.errors import BhavSetuError

_CFG = settings.locations.otp
LENGTH: int = int(_CFG.length)
TTL_SECONDS: int = int(_CFG.ttl_seconds)
MAX_ATTEMPTS: int = int(_CFG.max_attempts)
RESEND_COOLDOWN: int = int(_CFG.resend_cooldown_seconds)
RATE_LIMIT_PER_HOUR: int = int(_CFG.rate_limit_per_hour)
CHANNEL: str = str(_CFG.channel)

#: Dev delivery. The code comes back in the response so a demo needs no phone.
DEV_CHANNEL: str = "log"


class OtpError(BhavSetuError):
    """Anything a caller should be told about plainly: expired, wrong, throttled."""


class RateLimited(OtpError):
    pass


_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.env.redis_url, decode_responses=True)
    return _client


def _key(phone: str) -> str:
    return f"otp:{phone}"


def _attempts_key(phone: str) -> str:
    return f"otp:attempts:{phone}"


def _rate_key(phone: str) -> str:
    return f"otp:rate:{phone}"


def _hash(phone: str, code: str) -> str:
    """Store a keyed hash, never the code.

    Redis is not the threat model most of the time, but a code sitting in plain
    text in a datastore is the kind of thing that turns one breach into account
    takeover. `compare_digest` on verify keeps the check constant-time.
    """
    secret = settings.env.database_url.encode()      # process-local, stable secret
    return hmac.new(secret, f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


@dataclass
class Challenge:
    phone: str
    expires_in: int
    channel: str
    #: Populated only when `channel == "log"`. Never set for real delivery.
    code: str | None = None
    #: Where it was actually sent, when the channel is email. Masked for display
    #: so a shoulder-surfer at a demo does not read the farmer's full address.
    email: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "phone": self.phone,
            "expiresIn": self.expires_in,
            "channel": self.channel,
        }
        if self.code is not None:
            out["devCode"] = self.code
        if self.email:
            out["sentTo"] = _mask_email(self.email)
        return out


def _mask_email(email: str) -> str:
    """r****h@gmail.com — enough to recognise your own address, not to read it."""
    name, _, domain = email.partition("@")
    if not domain:
        return email
    if len(name) <= 2:
        return f"{name[:1]}***@{domain}"
    return f"{name[0]}{'*' * (len(name) - 2)}{name[-1]}@{domain}"


def normalise_phone(raw: str) -> str:
    """+91XXXXXXXXXX. Farmers type it every possible way; storage must not care."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]
    raise OtpError(
        f"{raw!r} is not a valid Indian mobile number — 10 digits, or +91 and 10 digits"
    )


def request_otp(phone: str, email: str | None = None) -> Challenge:
    """Issue a code, or refuse if this number is asking too often."""
    phone = normalise_phone(phone)
    r = client()

    # Cooldown first: a user hammering "resend" should be told to wait, not
    # silently consume their hourly budget.
    ttl = r.ttl(_key(phone))
    if ttl and ttl > TTL_SECONDS - RESEND_COOLDOWN:
        wait = ttl - (TTL_SECONDS - RESEND_COOLDOWN)
        raise RateLimited(f"a code was just sent — try again in {wait} seconds")

    used = r.incr(_rate_key(phone))
    if used == 1:
        r.expire(_rate_key(phone), 3600)
    if used > RATE_LIMIT_PER_HOUR:
        raise RateLimited(
            f"too many codes requested for this number — try again in an hour"
        )

    code = "".join(secrets.choice("0123456789") for _ in range(LENGTH))
    r.set(_key(phone), _hash(phone, code), ex=TTL_SECONDS)
    r.delete(_attempts_key(phone))

    # Deliberately does NOT log the code when a real channel is configured.
    log.info("otp_issued", phone=phone, channel=CHANNEL, ttl=TTL_SECONDS)

    # Email delivery, when the farmer gave us an address and SMTP is set up.
    #
    # This is a real channel that works without deploying anything: the farmer
    # types the code, so unlike a magic link there is no URL that has to be
    # reachable from his device. SMS is the one delivery route we do NOT have —
    # it needs a paid gateway and, in India, DLT sender-ID registration.
    if email:
        from auth import email_link             # noqa: PLC0415 — avoids a cycle

        if email_link.smtp_configured():
            address = email_link.normalise_email(email)
            email_link.send_otp_email(address, code, TTL_SECONDS)
            log.info("otp_emailed", phone=phone, email=address)
            return Challenge(phone, TTL_SECONDS, "email", email=address)
        log.warn("otp_email_skipped", reason="smtp_not_configured")

    if CHANNEL == DEV_CHANNEL:
        log.warn("otp_dev_code", phone=phone, code=code,
                 note="channel=log — never use this in production")
        return Challenge(phone, TTL_SECONDS, CHANNEL, code=code)

    from whatsapp.client import send_otp      # noqa: PLC0415 — optional dependency

    send_otp(phone, code)
    return Challenge(phone, TTL_SECONDS, CHANNEL)


def verify_otp(phone: str, code: str) -> bool:
    """True once. Raises with a readable reason on expiry, exhaustion or mismatch."""
    phone = normalise_phone(phone)
    r = client()

    stored = r.get(_key(phone))
    if stored is None:
        raise OtpError("that code has expired — request a new one")

    attempts = r.incr(_attempts_key(phone))
    if attempts == 1:
        r.expire(_attempts_key(phone), TTL_SECONDS)
    if attempts > MAX_ATTEMPTS:
        r.delete(_key(phone))
        raise OtpError("too many wrong attempts — request a new code")

    if not hmac.compare_digest(stored, _hash(phone, str(code).strip())):
        remaining = MAX_ATTEMPTS - attempts
        raise OtpError(
            f"that code is not right — {remaining} attempt{'s' if remaining != 1 else ''} left"
            if remaining > 0 else "that code is not right"
        )

    # Single use: burn it the moment it works.
    r.delete(_key(phone))
    r.delete(_attempts_key(phone))
    log.info("otp_verified", phone=phone)
    return True
