"""Phase 15 — farmer history in MongoDB, and sign-in by emailed link.

`conftest.py` points MONGODB_DB at a throwaway database before anything imports
config, so these tests never write into the history a demo reads from.

The test that matters most is `test_the_first_mongo_access_does_not_deadlock`.
`core/mongo.py` originally used a plain `threading.Lock`, and `_ensure_indexes`
holds it while calling `client()`, which takes it again — so the very first
history write in a process hung forever. It looked like a slow database; it was
a non-reentrant lock, and it hung sign-in.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api.main import app
from auth import email_link
from core.mongo import collection, is_available
from history import store

client = TestClient(app)
BASE = "/api/v1"

pytestmark = pytest.mark.skipif(
    not is_available(),
    reason="MongoDB is not running — start it with `docker compose up -d mongo`",
)


def _register(phone: str, name: str = "Test Farmer",
              village: str = "Vinchur", district: str = "Nashik"):
    """Sign a farmer up over OTP and return (farmer, auth header)."""
    issued = client.post(f"{BASE}/auth/request-otp", json={"phone": phone}).json()
    response = client.post(f"{BASE}/auth/verify", json={
        "phone": phone, "code": issued["devCode"], "name": name,
        "village": village, "district": district, "language": "mr"})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["farmer"], {"Authorization": f"Bearer {body['token']}"}


def _unique_phone() -> str:
    return f"+9199{int(time.time() * 1000) % 100_000_000:08d}"


# ══════════════════════════════════════════════════════════════════════════
# the one that matters
# ══════════════════════════════════════════════════════════════════════════

def test_the_first_mongo_access_does_not_deadlock():
    """REGRESSION. A non-reentrant lock made the first history write hang.

    Signing up is the first thing that touches Mongo in a fresh process. If the
    index-creation lock is not reentrant this call never returns, so the assert
    is really "this finished at all".
    """
    farmer, _ = _register(_unique_phone(), name="Deadlock Canary")
    assert farmer["id"] > 0


# ══════════════════════════════════════════════════════════════════════════
# history is written
# ══════════════════════════════════════════════════════════════════════════

def test_signing_up_writes_a_farmer_document_with_their_details():
    farmer, _ = _register(_unique_phone(), name="Ramesh Pawar", village="Vinchur")
    document = client.get(f"{BASE}/history/farmers/{farmer['id']}").json()

    assert document["farmerId"] == farmer["id"]
    assert document["name"] == "Ramesh Pawar"
    assert document["phone"] == farmer["phone"]
    assert document["village"] == "Vinchur"
    assert document["district"] == "Nashik"
    # Location is what makes the record useful — a farmer with no coordinates
    # cannot be compared to a market.
    assert document["lat"] is not None and document["lon"] is not None


def test_the_signup_appears_on_the_timeline():
    farmer, _ = _register(_unique_phone())
    document = client.get(f"{BASE}/history/farmers/{farmer['id']}").json()
    kinds = [event["type"] for event in document["timeline"]]
    assert "signup" in kinds
    assert all(event["summary"] for event in document["timeline"]), \
        "every event needs a readable sentence, not just a payload"


def test_every_event_carries_the_farmer_it_belongs_to():
    """Denormalised on purpose: an event has to be readable on its own."""
    farmer, _ = _register(_unique_phone(), name="Sunita Jadhav")
    document = client.get(f"{BASE}/history/farmers/{farmer['id']}").json()
    for event in document["timeline"]:
        assert event["farmerId"] == farmer["id"]
        assert event["farmer"]["name"] == "Sunita Jadhav"
        assert event["farmer"]["phone"] == farmer["phone"]


def test_signing_in_again_is_recorded_without_duplicating_the_farmer():
    phone = _unique_phone()
    farmer, _ = _register(phone)
    issued = client.post(f"{BASE}/auth/request-otp", json={"phone": phone}).json()
    client.post(f"{BASE}/auth/verify", json={"phone": phone, "code": issued["devCode"]})

    document = client.get(f"{BASE}/history/farmers/{farmer['id']}").json()
    assert document["eventCounts"].get("login", 0) >= 1
    assert collection("farmers").count_documents({"_id": farmer["id"]}) == 1


def test_advice_is_recorded_against_the_signed_in_farmer():
    farmer, auth = _register(_unique_phone())
    response = client.post(f"{BASE}/recommend", headers=auth, json={
        "crop": "onion", "qtyQtl": 80, "grade": "B", "storage": "ambient",
        "riskProfile": "balanced", "mandi": "Nashik"})
    assert response.status_code == 200, response.text

    document = client.get(f"{BASE}/history/farmers/{farmer['id']}").json()
    assert document["eventCounts"].get("recommendation", 0) >= 1


def test_a_sale_report_lands_on_the_signed_in_farmer():
    """It used to attribute to a name+village handle, creating a second farmer."""
    farmer, auth = _register(_unique_phone(), name="Balasaheb More", village="Saykheda")
    response = client.post(f"{BASE}/sale-reports", headers=auth, json={
        "farmer": "Balasaheb More", "village": "Saykheda", "mandi": "Nashik",
        "crop": "onion", "qtl": 12, "quotedPerQtl": 1450, "receivedPerQtl": 1390})
    assert response.status_code == 201, response.text

    document = client.get(f"{BASE}/history/farmers/{farmer['id']}").json()
    assert document["eventCounts"].get("sale_report", 0) >= 1


def test_anonymous_use_records_nothing():
    """No session, no history. The page still has to work."""
    before = collection("events").count_documents({})
    response = client.get(f"{BASE}/irrigation", params={"crop": "onion", "mandi": "Nashik"})
    assert response.status_code == 200
    assert collection("events").count_documents({}) == before


# ══════════════════════════════════════════════════════════════════════════
# reading it back
# ══════════════════════════════════════════════════════════════════════════

def test_the_farmer_list_carries_counts_and_contact_details():
    _register(_unique_phone())
    body = client.get(f"{BASE}/history/farmers").json()
    assert body["available"] is True
    assert body["totals"]["farmers"] >= 1
    row = body["farmers"][0]
    assert {"farmerId", "name", "phone", "village", "events"} <= set(row)


def test_an_unknown_farmer_is_a_readable_422():
    response = client.get(f"{BASE}/history/farmers/99999999")
    assert response.status_code == 422
    assert response.json()["detail"]


def test_store_status_reports_the_totals():
    body = client.get(f"{BASE}/history/store-status").json()
    assert body["available"] is True
    assert body["farmers"] >= 0 and body["events"] >= 0


# ══════════════════════════════════════════════════════════════════════════
# the magic link
# ══════════════════════════════════════════════════════════════════════════

def _mint(email: str) -> str:
    """A valid link without going near SMTP."""
    nonce = f"test{int(time.time() * 1000)}"
    token = email_link._sign(email, nonce, int(time.time()) + 900)
    email_link.client().setex(email_link._nonce_key(nonce), 900, email)
    return token


def test_a_magic_link_signs_a_new_farmer_in_and_records_it():
    email = f"judge{int(time.time() * 1000)}@example.com"
    response = client.post(f"{BASE}/auth/magic-link/verify", json={
        "token": _mint(email), "name": "Judge Patil",
        "village": "Vinchur", "district": "Nashik"})
    assert response.status_code == 200, response.text
    farmer = response.json()["farmer"]
    assert farmer["email"] == email
    assert farmer["isNew"] is True

    document = client.get(f"{BASE}/history/farmers/{farmer['id']}").json()
    assert document["email"] == email
    assert document["eventCounts"].get("signup", 0) >= 1


def test_a_magic_link_works_only_once():
    email = f"replay{int(time.time() * 1000)}@example.com"
    token = _mint(email)
    assert client.post(f"{BASE}/auth/magic-link/verify",
                       json={"token": token, "name": "One Use"}).status_code == 200
    again = client.post(f"{BASE}/auth/magic-link/verify", json={"token": token})
    assert again.status_code == 400
    assert "already been used" in again.json()["detail"]


def test_a_tampered_link_is_refused():
    token = _mint("tamper@example.com")
    body, _, _signature = token.partition(".")
    response = client.post(f"{BASE}/auth/magic-link/verify",
                           json={"token": f"{body}.deadbeef"})
    assert response.status_code == 400
    assert "tampered" in response.json()["detail"]


def test_an_expired_link_is_refused():
    nonce = f"expired{int(time.time() * 1000)}"
    token = email_link._sign("old@example.com", nonce, int(time.time()) - 5)
    email_link.client().setex(email_link._nonce_key(nonce), 900, "old@example.com")
    response = client.post(f"{BASE}/auth/magic-link/verify", json={"token": token})
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


@pytest.mark.parametrize("bad", ["", "not-an-email", "no@domain", "a b@c.com"])
def test_a_bad_address_is_refused_before_any_mail_is_attempted(bad: str):
    with pytest.raises(email_link.EmailLinkError):
        email_link.normalise_email(bad)


def test_the_link_is_not_emailed_when_smtp_is_blank(monkeypatch):
    """The console fallback is what lets a demo run with no mail account."""
    monkeypatch.setattr(email_link, "smtp_configured", lambda: False)
    email = f"nosmtp{int(time.time() * 1000)}@example.com"
    challenge = email_link.request_link(email)
    assert challenge.sent is False
    assert challenge.dev_link and "token=" in challenge.dev_link


# ══════════════════════════════════════════════════════════════════════════
# the store itself
# ══════════════════════════════════════════════════════════════════════════

def test_a_mongo_outage_is_swallowed_but_a_real_bug_is_not():
    """Writes are best-effort — but only for *connection* failures.

    `safe_write` exists so a farmer still gets his session when the history
    mirror is unreachable. It must not become a blanket try/except that hides
    our own mistakes, so anything that is not a PyMongoError still surfaces.
    """
    from pymongo.errors import PyMongoError

    from core.mongo import safe_write

    def unreachable() -> None:
        raise PyMongoError("connection refused")

    def our_own_bug() -> None:
        raise TypeError("this is a coding error, not an outage")

    assert safe_write("outage", unreachable) is False

    with pytest.raises(TypeError):
        safe_write("bug", our_own_bug)


def test_event_types_are_a_closed_set():
    assert "signup" in store.EVENT_TYPES
    assert "sale_report" in store.EVENT_TYPES
    assert len(set(store.EVENT_TYPES)) == len(store.EVENT_TYPES)
