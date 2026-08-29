"""Phase 15 — sign-in by emailed magic link.

    request_link("farmer@example.com")   -> Challenge(link shown only in dev)
    consume(token)                       -> the verified email address

A second door into the same account system. OTP over a phone is the right
default for a farmer; a judge, an FPO officer or anyone demoing on a laptop
would rather click a link than wait for a code, and email costs nothing to
send.

**The link is a signed token, not a database row.** Same reasoning as
`session.py`: the API stays stateless, a restart does not invalidate a link
that is already in someone's inbox, and there is no table to clean up. The
token carries the email, an expiry, and a single-use nonce that is burned in
Redis when it is consumed.

Three properties worth stating, because the naive version has none of them:

  * **It expires.** Fifteen minutes. A link forwarded or left in an inbox for a
    week is not a permanent key to the account.
  * **It works once.** The nonce is deleted on use, so a link in a mail archive
    or a proxy log cannot be replayed.
  * **Requests are rate-limited per address**, so nobody can use us to send mail
    to a stranger repeatedly.

**With SMTP unconfigured the link is printed to the console and returned in the
response**, exactly like `otp.channel: log`. That is what makes a demo work with
no mail account at all, and it is why the response field is named `devLink` —
it must be obvious that it would not be there in production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

import redis

from core import logging as log
from core.config import settings
from core.errors import BhavSetuError

#: Fifteen minutes. Long enough to switch to a mail app, short enough that a
#: forwarded link is not a standing invitation.
TTL_SECONDS: int = 15 * 60

#: Requests per address per hour.
RATE_LIMIT_PER_HOUR: int = 8

#: Seconds before the same address may request another link.
RESEND_COOLDOWN: int = 45

#: Deliberately permissive. Rejecting unusual-but-valid addresses is a worse
#: failure than letting a typo through — the mail simply will not arrive.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class EmailLinkError(BhavSetuError):
    """Anything the caller should be told plainly: bad address, expired, throttled."""


class EmailRateLimited(EmailLinkError):
    pass


_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.env.redis_url, decode_responses=True)
    return _client


def normalise_email(raw: str) -> str:
    """Lowercased and trimmed. Rejected here rather than at the SMTP server."""
    email = str(raw or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise EmailLinkError(f"{raw!r} does not look like an email address")
    return email


def smtp_configured() -> bool:
    """True when there is enough in .env to actually send mail."""
    env = settings.env
    return bool(env.smtp_host and env.smtp_port and env.smtp_user and env.smtp_password)


# ══════════════════════════════════════════════════════════════════════════
# the token
# ══════════════════════════════════════════════════════════════════════════

def _secret() -> bytes:
    """Signing key, derived like `session.py` so no new env var can be blank."""
    return hashlib.sha256(
        ("bhav-setu-magic-link:" + settings.env.database_url).encode()
    ).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _nonce_key(nonce: str) -> str:
    return f"magic:nonce:{nonce}"


def _sign(email: str, nonce: str, expires_at: int) -> str:
    payload = {"email": email, "n": nonce, "exp": int(expires_at)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


@dataclass
class Challenge:
    """What `request_link` gives back. `dev_link` is None once SMTP is set up."""

    email: str
    sent: bool
    expires_in: int
    dev_link: str | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "email": self.email,
            "sent": self.sent,
            "expiresIn": self.expires_in,
        }
        if self.dev_link:
            body["devLink"] = self.dev_link
            body["note"] = (
                "SMTP is not configured, so the link is returned here instead of "
                "being emailed. Fill SMTP_* in .env to send it for real."
            )
        return body


def request_link(raw_email: str) -> Challenge:
    """Mint a single-use link and email it, or return it when SMTP is blank."""
    email = normalise_email(raw_email)
    redis_client = client()

    cooldown_key = f"magic:cooldown:{email}"
    if redis_client.get(cooldown_key):
        raise EmailRateLimited(
            "a link was just sent to that address — check your inbox, or try again "
            "in a minute"
        )

    hourly_key = f"magic:rate:{email}:{int(time.time() // 3600)}"
    used = int(redis_client.get(hourly_key) or 0)
    if used >= RATE_LIMIT_PER_HOUR:
        raise EmailRateLimited(
            "too many sign-in links requested for that address in the last hour"
        )

    nonce = _b64(hashlib.sha256(f"{email}{time.time()}".encode()).digest())[:22]
    expires_at = int(time.time()) + TTL_SECONDS
    token = _sign(email, nonce, expires_at)

    # The nonce is what makes the link single-use; the signature alone would be
    # replayable until it expired.
    redis_client.setex(_nonce_key(nonce), TTL_SECONDS, email)
    redis_client.setex(cooldown_key, RESEND_COOLDOWN, "1")
    redis_client.incr(hourly_key)
    redis_client.expire(hourly_key, 3600)

    link = f"{settings.env.app_base_url.rstrip('/')}/login/verify?token={quote(token)}"

    if smtp_configured():
        _send(email, link)
        log.info("magic_link_sent", email=email)
        return Challenge(email=email, sent=True, expires_in=TTL_SECONDS)

    # No SMTP: print it and hand it back, so a demo needs no mail account.
    #
    # ASCII only, and wrapped, deliberately. Uvicorn on Windows writes stdout
    # through cp1252, so one non-ASCII character here raises UnicodeEncodeError
    # *inside the request* and turns a working sign-in into a 500. Printing the
    # link is a convenience; it must never be what breaks the endpoint.
    log.warn("magic_link_not_emailed", email=email, reason="smtp_not_configured")
    try:
        print(f"\n  [magic link] {email} (SMTP not configured):\n     {link}\n")
    except Exception:                                      # noqa: BLE001
        pass
    return Challenge(email=email, sent=False, expires_in=TTL_SECONDS, dev_link=link)


def consume(token: str) -> str:
    """Validate a link and burn it. Returns the verified email address."""
    try:
        body, signature = str(token).split(".", 1)
    except ValueError:
        raise EmailLinkError("that sign-in link is malformed") from None

    expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise EmailLinkError("that sign-in link has been tampered with")

    try:
        payload = json.loads(_unb64(body))
        email = str(payload["email"])
        nonce = str(payload["n"])
        expires_at = int(payload["exp"])
    except (ValueError, KeyError, TypeError):
        raise EmailLinkError("that sign-in link is malformed") from None

    if time.time() > expires_at:
        raise EmailLinkError("that sign-in link has expired — request a new one")

    # Single use: deleting returns 1 only for the first caller.
    if client().delete(_nonce_key(nonce)) != 1:
        raise EmailLinkError(
            "that sign-in link has already been used — request a new one"
        )
    return email


# ══════════════════════════════════════════════════════════════════════════
# delivery
# ══════════════════════════════════════════════════════════════════════════

SUBJECT = "Your Bhav Setu sign-in link"


def _body(link: str) -> tuple[str, str]:
    plain = (
        "Namaskar,\n\n"
        "Here is your sign-in link for Bhav Setu. It works once and expires in "
        "15 minutes.\n\n"
        f"{link}\n\n"
        "If you did not ask for this, you can ignore this email — nobody can "
        "sign in without opening the link.\n\n"
        "— Bhav Setu\n"
    )
    html = f"""\
<html><body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
     background:#F3F3EA;padding:32px;color:#16160F">
  <div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #E2E2D6;
       border-radius:18px;padding:32px">
    <p style="font-size:12px;letter-spacing:.16em;text-transform:uppercase;
       color:#6F6F63;margin:0 0 12px">Bhav Setu</p>
    <h1 style="font-size:22px;margin:0 0 16px">Your sign-in link</h1>
    <p style="line-height:1.6;margin:0 0 24px">
      Click the button to sign in. The link works once and expires in 15 minutes.
    </p>
    <p style="margin:0 0 24px">
      <a href="{link}" style="display:inline-block;background:#16160F;color:#F3F3EA;
         text-decoration:none;padding:12px 24px;border-radius:999px;font-weight:600">
        Sign in to Bhav Setu
      </a>
    </p>
    <p style="line-height:1.6;font-size:13px;color:#6F6F63;margin:0">
      If you did not ask for this, ignore this email — nobody can sign in
      without opening the link.
    </p>
  </div>
</body></html>"""
    return plain, html


def _send(to_email: str, link: str) -> None:
    """Email a magic link."""
    plain, html = _body(link)
    deliver(to_email, SUBJECT, plain, html)


def deliver(to_email: str, subject: str, plain: str, html: str) -> None:
    """Send one message over SMTP. Raises `EmailLinkError` with the reason.

    Shared by the magic link and the email OTP so there is exactly one place
    that knows how to talk to a mail server.
    """
    env = settings.env
    sender = env.smtp_from or env.smtp_user

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(plain)
    message.add_alternative(html, subtype="html")

    port = int(env.smtp_port or 587)
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(env.smtp_host, port, context=context, timeout=20) as server:
                server.login(env.smtp_user, env.smtp_password)
                server.send_message(message)
        else:
            # 587 and friends: plain connection upgraded with STARTTLS.
            with smtplib.SMTP(env.smtp_host, port, timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(env.smtp_user, env.smtp_password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailLinkError(
            "the mail server rejected our credentials. For Gmail, SMTP_PASSWORD "
            "must be a 16-character App Password, not the account password."
        ) from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise EmailLinkError(f"could not send the sign-in email: {exc}") from exc


# ══════════════════════════════════════════════════════════════════════════
# email OTP — the code, not a link
# ══════════════════════════════════════════════════════════════════════════

OTP_SUBJECT = "Your Bhav Setu code"


def send_otp_email(to_email: str, code: str, ttl_seconds: int) -> None:
    """Email a six-digit sign-in code.

    A code rather than a link, deliberately. The magic link points at
    `APP_BASE_URL`, so until this is deployed it lands on someone else's
    localhost and is useless. A code is typed in, so it works from any inbox on
    any device against a server running only on this laptop.
    """
    minutes = max(1, ttl_seconds // 60)
    plain = (
        "Namaskar,\n\n"
        f"Your Bhav Setu sign-in code is {code}\n\n"
        f"It expires in {minutes} minutes and works once.\n\n"
        "If you did not ask for this, ignore this email.\n\n"
        "- Bhav Setu\n"
    )
    html = f"""<html><body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;
     background:#F3F3EA;padding:32px;color:#16160F">
  <div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #E2E2D6;
       border-radius:18px;padding:32px;text-align:center">
    <p style="font-size:12px;letter-spacing:.16em;text-transform:uppercase;
       color:#6F6F63;margin:0 0 12px">Bhav Setu</p>
    <h1 style="font-size:20px;margin:0 0 20px">Your sign-in code</h1>
    <p style="font-size:38px;font-weight:700;letter-spacing:.18em;
       margin:0 0 20px;font-family:ui-monospace,Menlo,Consolas,monospace">{code}</p>
    <p style="line-height:1.6;margin:0 0 8px">
      Type this into the app. It expires in {minutes} minutes and works once.
    </p>
    <p style="line-height:1.6;font-size:13px;color:#6F6F63;margin:16px 0 0">
      If you did not ask for this, ignore this email.
    </p>
  </div>
</body></html>"""
    deliver(to_email, OTP_SUBJECT, plain, html)
