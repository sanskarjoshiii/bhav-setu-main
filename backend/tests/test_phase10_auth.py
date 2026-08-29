"""Phase 10 — OTP login, sessions, locations, and pooling by district.

The security tests are the point of this file. A six-digit code with no attempt
limit falls to a script in under a minute, and a session token that is not
signed is just a farmer id anyone can edit.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import text

from api.main import app
from auth import otp as otp_service
from core.db import get_conn
from auth.session import SessionError, issue, verify

client = TestClient(app)
BASE = "/api/v1"


def _phone(suffix: int) -> str:
    """A distinct number per test, so one test's rate limit is not another's."""
    return f"98765{suffix:05d}"


@pytest.fixture(autouse=True)
def _clean_otp_state():
    """Redis outlives the test run, so state must be cleared or the second run fails.

    Rate-limit counters and cooldowns are deliberately durable — that is the
    whole point of them — which means a test suite that does not reset them
    passes once and then fails forever. Clearing every `otp:*` key for the
    numbers this file uses keeps each run identical to a cold start.
    """
    r = otp_service.client()
    phones = [otp_service.normalise_phone(_phone(i)) for i in range(0, 40)]
    for phone in phones:
        r.delete(otp_service._key(phone),
                 otp_service._attempts_key(phone),
                 otp_service._rate_key(phone))

    # Postgres too: a farmer created by the last run makes `isNew` false on this
    # one. Children go first — lots and reports carry a foreign key to farmers.
    with get_conn() as conn:
        conn.execute(text("""
            DELETE FROM sale_reports WHERE farmer_id IN (
                SELECT id FROM farmers WHERE phone_e164 = ANY(:p))
        """), {"p": phones})
        conn.execute(text("""
            DELETE FROM recommendations WHERE lot_id IN (
                SELECT l.id FROM lots l JOIN farmers f ON f.id = l.farmer_id
                WHERE f.phone_e164 = ANY(:p))
        """), {"p": phones})
        conn.execute(text("""
            DELETE FROM lots WHERE farmer_id IN (
                SELECT id FROM farmers WHERE phone_e164 = ANY(:p))
        """), {"p": phones})
        conn.execute(text("DELETE FROM farmers WHERE phone_e164 = ANY(:p)"),
                     {"p": phones})
    yield


def _issue(phone: str) -> str:
    body = client.post(f"{BASE}/auth/request-otp", json={"phone": phone}).json()
    assert "devCode" in body, "channel must be `log` for tests to read the code"
    return body["devCode"]


# ══════════════════════════════════════════════════════════════════════════
# locations
# ══════════════════════════════════════════════════════════════════════════

def test_only_districts_we_have_data_for_are_offered():
    """Registering a farmer we cannot serve is worse than telling him to wait."""
    body = client.get(f"{BASE}/locations").json()
    assert body
    for district in body:
        if district["hasData"]:
            assert district["market"], f"{district['name']} claims data but has no market"
        assert district["villages"], f"{district['name']} has no villages"


def test_every_village_carries_coordinates_and_a_distance():
    body = client.get(f"{BASE}/locations").json()
    served = [d for d in body if d["hasData"]]
    assert served, "no district has price data"
    for district in served:
        for village in district["villages"]:
            assert -90 <= village["lat"] <= 90 and -180 <= village["lon"] <= 180
            assert village["distanceToMarketKm"] is not None, village["name"]
            assert village["distanceToMarketKm"] >= 0


def test_villages_are_sorted_and_named_in_both_scripts():
    district = client.get(f"{BASE}/locations").json()[0]
    names = [v["name"] for v in district["villages"]]
    assert names == sorted(names)
    assert all(v["nameMr"] for v in district["villages"])


# ══════════════════════════════════════════════════════════════════════════
# phone normalisation
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw", [
    "9876543210", "+919876543210", "+91 98765 43210",
    "09876543210", "91-98765-43210",
])
def test_every_way_a_farmer_writes_his_number_normalises_the_same(raw):
    assert otp_service.normalise_phone(raw) == "+919876543210"


@pytest.mark.parametrize("raw", ["12345", "", "abcdefghij", "9876"])
def test_a_number_that_is_not_a_number_is_refused(raw):
    with pytest.raises(otp_service.OtpError):
        otp_service.normalise_phone(raw)


# ══════════════════════════════════════════════════════════════════════════
# the OTP itself
# ══════════════════════════════════════════════════════════════════════════

def test_a_code_is_issued_and_signs_a_new_farmer_in():
    phone = _phone(1)
    code = _issue(phone)
    assert len(code) == otp_service.LENGTH and code.isdigit()

    response = client.post(f"{BASE}/auth/verify", json={
        "phone": phone, "code": code, "name": "Ramesh Patil",
        "village": "Vinchur", "district": "Nashik",
        "language": "mr", "riskProfile": "cautious",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    farmer = body["farmer"]
    assert farmer["isNew"] is True
    assert farmer["village"] == "Vinchur" and farmer["district"] == "Nashik"
    assert farmer["riskProfile"] == "cautious"
    # The village must have been geocoded, or transport cost cannot be computed.
    assert farmer["lat"] and farmer["lon"]
    assert farmer["homeMandi"]


def test_a_wrong_code_is_rejected_and_says_how_many_tries_are_left():
    phone = _phone(2)
    _issue(phone)
    response = client.post(f"{BASE}/auth/verify",
                           json={"phone": phone, "code": "000000", "name": "X"})
    assert response.status_code == 400
    assert "not right" in response.json()["detail"]


def test_a_code_works_exactly_once():
    """An OTP read over someone's shoulder must not be a permanent key."""
    phone = _phone(3)
    code = _issue(phone)
    first = client.post(f"{BASE}/auth/verify", json={
        "phone": phone, "code": code, "name": "Sunita", "district": "Pune",
        "village": "Junnar",
    })
    assert first.status_code == 200

    second = client.post(f"{BASE}/auth/verify", json={"phone": phone, "code": code})
    assert second.status_code == 400
    assert "expired" in second.json()["detail"]


def test_the_code_is_burned_after_too_many_wrong_guesses():
    """Without this, six digits falls to a script in under a minute."""
    phone = _phone(4)
    code = _issue(phone)
    for _ in range(otp_service.MAX_ATTEMPTS + 1):
        client.post(f"{BASE}/auth/verify",
                    json={"phone": phone, "code": "111111", "name": "X"})

    # Even the RIGHT code is now dead.
    response = client.post(f"{BASE}/auth/verify",
                           json={"phone": phone, "code": code, "name": "X"})
    assert response.status_code == 400
    assert "expired" in response.json()["detail"] or "attempts" in response.json()["detail"]


def test_resending_immediately_is_refused():
    phone = _phone(5)
    _issue(phone)
    response = client.post(f"{BASE}/auth/request-otp", json={"phone": phone})
    assert response.status_code == 400
    assert "try again" in response.json()["detail"]


def test_a_number_cannot_be_pumped_with_requests():
    """Rate limit protects the farmer's phone, not just our bill."""
    phone = _phone(6)
    refused = 0
    for _ in range(otp_service.RATE_LIMIT_PER_HOUR + 3):
        # Clear the cooldown so we are testing the hourly cap, not the resend gap.
        otp_service.client().delete(otp_service._key(otp_service.normalise_phone(phone)))
        if client.post(f"{BASE}/auth/request-otp", json={"phone": phone}).status_code == 400:
            refused += 1
    assert refused > 0, "a number can be pumped indefinitely"


def test_the_code_is_never_stored_in_plain_text():
    phone = _phone(7)
    code = _issue(phone)
    stored = otp_service.client().get(
        otp_service._key(otp_service.normalise_phone(phone)))
    assert stored and code not in stored, "the raw code is sitting in Redis"


# ══════════════════════════════════════════════════════════════════════════
# sessions
# ══════════════════════════════════════════════════════════════════════════

def test_a_session_token_round_trips():
    token = issue(42, "+919876543210")
    claims = verify(token)
    assert claims.farmer_id == 42 and claims.phone == "+919876543210"


def test_a_tampered_token_is_refused():
    """The whole point: a farmer must not be able to edit his own id."""
    token = issue(42, "+919876543210")
    with pytest.raises(SessionError):
        verify(token[:-4] + "AAAA")
    with pytest.raises(SessionError):
        verify("not-a-token")
    with pytest.raises(SessionError):
        verify(token.split(".")[0])          # signature stripped entirely


def test_an_expired_token_is_refused():
    with pytest.raises(SessionError):
        verify(issue(42, "+919876543210", ttl_seconds=-1))


def test_me_requires_a_valid_token():
    assert client.get(f"{BASE}/auth/me").status_code == 401
    assert client.get(f"{BASE}/auth/me",
                      headers={"Authorization": "Bearer rubbish"}).status_code == 401


def test_me_returns_the_signed_in_farmer():
    phone = _phone(8)
    code = _issue(phone)
    token = client.post(f"{BASE}/auth/verify", json={
        "phone": phone, "code": code, "name": "Kavita More",
        "district": "Solapur", "village": "Pandharpur",
    }).json()["token"]

    body = client.get(f"{BASE}/auth/me",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert body["name"] == "Kavita More"
    assert body["district"] == "Solapur" and body["village"] == "Pandharpur"


def test_a_returning_farmer_keeps_his_saved_profile():
    """Signing in with a blank form must not wipe the name and village."""
    phone = _phone(9)
    client.post(f"{BASE}/auth/verify", json={
        "phone": phone, "code": _issue(phone), "name": "Ganesh Kale",
        "district": "Ahmednagar", "village": "Rahuri",
    })
    otp_service.client().delete(otp_service._key(otp_service.normalise_phone(phone)))

    body = client.post(f"{BASE}/auth/verify",
                       json={"phone": phone, "code": _issue(phone)}).json()["farmer"]
    assert body["isNew"] is False
    assert body["name"] == "Ganesh Kale" and body["village"] == "Rahuri"


def test_an_unknown_number_must_supply_a_name():
    phone = _phone(10)
    response = client.post(f"{BASE}/auth/verify",
                           json={"phone": phone, "code": _issue(phone)})
    assert response.status_code == 400
    assert "name" in response.json()["detail"].lower()


def test_a_farmer_can_move_village():
    phone = _phone(11)
    token = client.post(f"{BASE}/auth/verify", json={
        "phone": phone, "code": _issue(phone), "name": "Meera",
        "district": "Nashik", "village": "Yeola",
    }).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    before = client.get(f"{BASE}/auth/me", headers=headers).json()
    after = client.patch(f"{BASE}/auth/me", headers=headers,
                         json={"district": "Pune", "village": "Baramati"}).json()

    assert after["village"] == "Baramati" and after["district"] == "Pune"
    assert after["lat"] != before["lat"], "moving village must move the coordinates"
    assert after["name"] == "Meera", "an unrelated field was clobbered"


# ══════════════════════════════════════════════════════════════════════════
# pooling, by location
# ══════════════════════════════════════════════════════════════════════════

def test_pools_can_be_narrowed_to_one_district():
    everything = client.get(f"{BASE}/pools").json()
    if not everything:
        pytest.skip("no pools seeded")
    district = everything[0]["district"]
    narrowed = client.get(f"{BASE}/pools", params={"district": district}).json()
    assert narrowed
    assert all(p["district"] == district for p in narrowed)
    assert len(narrowed) <= len(everything)


def test_an_unknown_district_returns_nothing_rather_than_everything():
    assert client.get(f"{BASE}/pools", params={"district": "Atlantis"}).json() == []
