"""HTTP API for development test recipients, the "Send test SMS" workflow and subscriber editing (2026-10-11).
Written before the endpoints existed.

A bare FastAPI() with only the messaging router. Numbers are DUMMIES ("+9160000000NN"), credentials FAKE; the live
flag is never set, so every send goes to the SimulatedProvider, and the real HTTP function is replaced by one that
fails the test if called."""
import hashlib
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from floodnet.messaging import devtest as dt
from floodnet.messaging import providers as pv
from floodnet.ops.store import reset_store_for_tests

TOKEN = "fake-dispatcher-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
def leaks(text) -> bool:
    """A dummy number (full or national part) or a +91 prefix in the text. A generic '8+ digits' check would
    flag hex hashes and provider message ids by chance."""
    return "+91" in str(text) or "600000000" in str(text)
TEXT_SHA = hashlib.sha256(dt.TEST_TEXT.encode("utf-8")).hexdigest()


@pytest.fixture
def api(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from floodnet.api import messaging_api
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FLOODNET_DEV_TEST_RECIPIENTS", "+916000000001:Owner,+916000000002")
    for n in ("FLOODNET_LIVE_MESSAGING", "FLOODNET_PUBLIC_BASE_URL", "VERCEL", "FLOODNET_ENV",
              "FLOODNET_MESSAGING_ALLOWLIST", "FLOODNET_DEV_TEST_CHANNELS"):
        monkeypatch.delenv(n, raising=False)

    def no_network(*a, **k):
        raise AssertionError("a test tried to reach a real provider")
    monkeypatch.setattr(pv, "_httpx_post", no_network)
    reset_store_for_tests()
    app = FastAPI()
    app.include_router(messaging_api.router)
    yield TestClient(app)
    reset_store_for_tests()


def seed(api):
    r = api.post("/api/ops/subscribers/seed-dev-test", json={"locality_id": "dadar-west"}, headers=AUTH)
    assert r.status_code == 201, r.text
    return {v["label"]: v for v in r.json()["seeded"] + r.json()["existing"]}


def test_endpoints_need_the_dispatcher_token(api):
    for method, path in (("post", "/api/ops/subscribers/seed-dev-test"), ("patch", "/api/ops/subscribers/SUB1"),
                         ("post", "/api/ops/test-sends/preview"), ("post", "/api/ops/test-sends"),
                         ("get", "/api/ops/test-sends"), ("post", "/api/ops/test-sends/TEST%3Ax/retry"),
                         ("post", "/api/ops/test-sends/TEST%3Ax/simulate-callbacks")):
        assert getattr(api, method)(path).status_code == 401, path


def test_status_says_whether_dev_recipients_are_configured_without_numbers(api, monkeypatch):
    st = api.get("/api/ops/messaging/status", headers=AUTH).json()
    assert st["dev_test"]["configured"] is True and st["dev_test"]["valid"] == 2
    assert not leaks(str(st))
    monkeypatch.delenv("FLOODNET_DEV_TEST_RECIPIENTS")
    assert api.get("/api/ops/messaging/status", headers=AUTH).json()["dev_test"]["configured"] is False


@pytest.mark.parametrize("var,value", [("VERCEL", "1"), ("FLOODNET_ENV", "production")])
def test_seeding_is_refused_in_production(api, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    monkeypatch.setenv("FLOODNET_OPS_ALLOW_EPHEMERAL", "1")         # the store opens, the seeding must still refuse
    reset_store_for_tests()
    r = api.post("/api/ops/subscribers/seed-dev-test", json={"locality_id": "dadar-west"}, headers=AUTH)
    assert r.status_code == 403 and "production" in r.text
    assert api.get("/api/ops/subscribers", headers=AUTH).json()["subscribers"] == []


def test_seed_without_configuration_is_a_409(api, monkeypatch):
    monkeypatch.delenv("FLOODNET_DEV_TEST_RECIPIENTS")
    r = api.post("/api/ops/subscribers/seed-dev-test", json={"locality_id": "dadar-west"}, headers=AUTH)
    assert r.status_code == 409 and "FLOODNET_DEV_TEST_RECIPIENTS" in r.text


def test_preview_confirm_send_double_click_and_list(api):
    devs = seed(api)
    owner = devs["Owner"]
    assert owner["number_masked"] == "•••• 0001" and owner["dev_test"] is True
    p = api.post("/api/ops/test-sends/preview", json={"subscriber_id": owner["id"], "channel": "sms"}, headers=AUTH)
    assert p.status_code == 200 and p.json()["text"] == dt.TEST_TEXT and p.json()["mode"] == "SIMULATED"
    body = {"subscriber_id": owner["id"], "channel": "sms", "idempotency_key": "ui-click-000001",
            "text_sha256": p.json()["text_sha256"]}
    first = api.post("/api/ops/test-sends", json=body, headers=AUTH)
    second = api.post("/api/ops/test-sends", json=body, headers=AUTH)
    assert first.status_code == 201 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert first.json()["label"] == "SIMULATED" and first.json()["status"] == "ACCEPTED"
    listing = api.get("/api/ops/test-sends", headers=AUTH)
    assert listing.status_code == 200 and len(listing.json()["sends"]) == 1
    for r in (p, first, second, listing, api.get("/api/ops/subscribers", headers=AUTH)):
        assert not leaks(r.text), r.text
    did = quote(first.json()["delivery_id"], safe="")
    cb = api.post(f"/api/ops/test-sends/{did}/simulate-callbacks", headers=AUTH)
    assert cb.status_code == 200 and cb.json()["label"] == "SIMULATED"
    assert api.get("/api/ops/test-sends", headers=AUTH).json()["sends"][0]["status"] == "DELIVERED"
    assert api.post(f"/api/ops/test-sends/{did}/retry", headers=AUTH).status_code == 409


def test_errors_map_to_http_codes(api):
    devs = seed(api)
    owner = devs["Owner"]
    ok = {"subscriber_id": owner["id"], "channel": "sms", "idempotency_key": "ui-click-000002", "text_sha256": TEXT_SHA}
    assert api.post("/api/ops/test-sends", json={**ok, "text_sha256": "0" * 64}, headers=AUTH).status_code == 409
    assert api.post("/api/ops/test-sends", json={**ok, "idempotency_key": "x"}, headers=AUTH).status_code == 422
    assert api.post("/api/ops/test-sends", json={**ok, "subscriber_id": "SUB999"}, headers=AUTH).status_code == 404
    assert api.post("/api/ops/test-sends", json={**ok, "channel": "whatsapp"}, headers=AUTH).status_code == 422
    plain = api.post("/api/ops/subscribers", json={"number": "6000000005", "area": {"locality_id": "naigaon"},
                                                  "channels": {"sms": {"method": "test"}}, "verified": True},
                     headers=AUTH).json()
    r = api.post("/api/ops/test-sends", json={**ok, "subscriber_id": plain["id"]}, headers=AUTH)
    assert r.status_code == 403 and "development test recipient" in r.text
    assert api.post("/api/ops/test-sends/TEST%3Anope-nope-nope/retry", headers=AUTH).status_code == 404


def test_subscriber_edit_endpoint(api):
    created = api.post("/api/ops/subscribers", json={"number": "+91 60000 00007", "area": {"locality_id": "naigaon"},
                                                    "channels": {"sms": {"method": "web_form"}}}, headers=AUTH)
    assert created.status_code == 201
    sid = created.json()["id"]
    r = api.patch(f"/api/ops/subscribers/{sid}", json={"language": "hi", "locality_id": "dadar-west",
                                                       "channels": {"sms": {}, "whatsapp": {"method": "phone_call"}}},
                  headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["language"] == "hi" and set(r.json()["channels"]) == {"sms", "whatsapp"}
    assert not leaks(r.text)
    assert api.patch(f"/api/ops/subscribers/{sid}", json={"number": "6000000008"}, headers=AUTH).status_code == 422
    assert api.patch("/api/ops/subscribers/SUB999", json={"language": "en"}, headers=AUTH).status_code == 404
    assert api.post(f"/api/ops/subscribers/{sid}/opt-out", headers=AUTH).status_code == 200
    assert api.patch(f"/api/ops/subscribers/{sid}", json={"language": "en"}, headers=AUTH).status_code == 409
