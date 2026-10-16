"""Messaging and public-alert HTTP API (DECISIONS D-44, D-45, D-46; acceptance criteria 9-12).
Written before floodnet/api/messaging_api.py existed.

The app here is a bare FastAPI() with only the messaging router. All credentials, secrets and numbers are FAKE;
providers are the SimulatedProvider or a Twilio adapter with an injected fake HTTP function: no network call is made.
Depths are SYNTHETIC TEST FIXTURES on REAL pilot road ids."""
import base64
import hashlib
import hmac
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from floodnet.messaging import providers as pv
from floodnet.ops import incident as inc
from floodnet.ops.store import get_store, reset_store_for_tests
from tests.test_messaging_alerts import ADVISORY, KHODADAD_A, T, WATCH

TOKEN = "fake-dispatcher-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NUMBER_RE = re.compile(r"\+?\d{8,}")
DIRECT = {"number": "+910000000021", "area": {"lonlat": [72.848752, 19.017141], "radius_m": 300},
          "channels": {"sms": {"method": "test"}}, "verified": True, "simulated": True}
NEARBY = {"number": "+910000000022", "area": {"lonlat": [72.8509328, 19.0177508], "radius_m": 100},
          "channels": {"sms": {"method": "test"}, "whatsapp": {"method": "test"}}, "verified": True, "simulated": True}
FAR = {"number": "+910000000023", "area": {"lonlat": [72.841445, 19.031244], "radius_m": 300},
       "channels": {"sms": {"method": "test"}}, "verified": True, "simulated": True}


def has_number(text: str) -> bool:
    """Any of this file's FAKE numbers (full or national part) or a +91 prefix in the text."""
    return "+91" in text or any(b["number"][3:] in text for b in (DIRECT, NEARBY, FAR))


@pytest.fixture
def api(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from floodnet.api import messaging_api, state
    if KHODADAD_A not in {s.seg_id for s in state.get_pilot()["roads"].segments}:
        pytest.skip("REAL pilot road network not loaded")
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())
    for n in ("FLOODNET_LIVE_MESSAGING", "FLOODNET_PUBLIC_BASE_URL", "TWILIO_AUTH_TOKEN", "MSG91_WEBHOOK_SECRET",
              "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "SACHET_RSS_URL", "VERCEL", "FLOODNET_MESSAGING_ALLOWLIST"):
        monkeypatch.delenv(n, raising=False)
    reset_store_for_tests()
    app = FastAPI()
    app.include_router(messaging_api.router)
    yield TestClient(app)
    reset_store_for_tests()


def publish(streets):
    inc.publish_forecast(get_store(), run_id="test", data_mode="SYNTHETIC TEST FIXTURE", series_t_min=T,
                         streets_cm=streets, label="SYNTHETIC TEST FIXTURE")


def add(api, body):
    r = api.post("/api/ops/subscribers", json=body, headers=AUTH)
    assert r.status_code in (200, 201), r.text
    return r.json()


def sent_alert(api, streets=WATCH):
    ids = [add(api, b)["id"] for b in (DIRECT, NEARBY, FAR)]
    publish(streets)
    a = api.post("/api/ops/alerts/draft", json={"window_min": 60}, headers=AUTH).json()
    assert api.post(f"/api/ops/alerts/{a['alert_id']}/submit-review", headers=AUTH).status_code == 200
    # QA M8 (2026-10-11): the API approves only the version the reviewer saw, so it is sent explicitly
    assert api.post(f"/api/ops/alerts/{a['alert_id']}/approve", json={"approver_name": "Test", "version": a["current_version"]},
                    headers=AUTH).status_code == 200
    r = api.post(f"/api/ops/alerts/{a['alert_id']}/send", headers=AUTH)
    assert r.status_code == 200, r.text
    return a, ids


# ----------------------------------------------------------------------------- access control
def test_dispatcher_endpoints_need_the_token(api):
    for method, path in (("get", "/api/ops/subscribers"), ("post", "/api/ops/subscribers"),
                         ("post", "/api/ops/alerts/draft"), ("get", "/api/ops/alerts"),
                         ("post", "/api/ops/alerts/AL1/send"), ("delete", "/api/ops/subscribers/SUB1")):
        assert getattr(api, method)(path).status_code == 401, path


def test_ops_unavailable_is_503(api, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("FLOODNET_OPS_ALLOW_EPHEMERAL", raising=False)
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    reset_store_for_tests()
    assert api.get("/api/ops/subscribers", headers=AUTH).status_code == 503
    assert api.get("/api/public/alerts/whatever").status_code in (404, 503)


# ----------------------------------------------------------------------------- subscribers
def test_subscriber_crud_never_returns_numbers(api, monkeypatch):
    r = api.post("/api/ops/subscribers", json=DIRECT, headers=AUTH)
    assert r.status_code == 201 and r.json()["created"] is True
    sid = r.json()["id"]
    dup = api.post("/api/ops/subscribers", json=DIRECT, headers=AUTH)
    assert dup.status_code == 200 and dup.json()["created"] is False and dup.json()["id"] == sid
    assert api.post("/api/ops/subscribers", json={**DIRECT, "number": "12"}, headers=AUTH).status_code == 422
    listing = api.get("/api/ops/subscribers", headers=AUTH)
    assert listing.status_code == 200 and not has_number(listing.text)
    assert api.post(f"/api/ops/subscribers/{sid}/opt-out", headers=AUTH).json()["status"] == "OPTED_OUT"
    gone = api.delete(f"/api/ops/subscribers/{sid}", headers=AUTH)
    assert gone.status_code == 200 and gone.json()["status"] == "ERASED"
    assert api.delete("/api/ops/subscribers/SUB999", headers=AUTH).status_code == 404
    monkeypatch.delenv("FLOODNET_PII_KEY")
    r = api.post("/api/ops/subscribers", json=FAR, headers=AUTH)
    assert r.status_code == 503 and "FLOODNET_PII_KEY" in r.text


# ----------------------------------------------------------------------------- alerts
def test_draft_requires_published_forecast(api):
    assert api.post("/api/ops/alerts/draft", json={"window_min": 60}, headers=AUTH).status_code == 422


def test_draft_link_uses_configured_or_request_base(api, monkeypatch):
    publish(ADVISORY)
    a = api.post("/api/ops/alerts/draft", json={"window_min": 60}, headers=AUTH).json()
    assert a["link"] == f"http://testserver/alerts/{a['public_id']}"
    b = api.post("/api/ops/alerts/draft", json={"window_min": 60}, headers={**AUTH, "Origin": "http://localhost:5173"}).json()
    assert b["link"] == f"http://localhost:5173/alerts/{b['public_id']}"
    monkeypatch.setenv("FLOODNET_PUBLIC_BASE_URL", "https://configured.example.test/")
    c = api.post("/api/ops/alerts/draft", json={"window_min": 60}, headers=AUTH).json()
    assert c["link"] == f"https://configured.example.test/alerts/{c['public_id']}"
    for ch in ("sms", "whatsapp"):
        text = c["versions"][-1]["texts"][ch]
        assert c["link"] in text and "Reply STOP to unsubscribe" in text
        assert not NUMBER_RE.search(text.replace(c["link"], ""))


def test_full_flow_targeting_double_send_and_callbacks(api):
    a, (direct, nearby, far) = sent_alert(api)
    detail = api.get(f"/api/ops/alerts/{a['alert_id']}", headers=AUTH).json()
    assert detail["state"] == "SENT"
    got = {(d["subscriber_id"], d["channel"]) for d in detail["deliveries"]}
    assert got == {(direct, "sms"), (nearby, "sms"), (nearby, "whatsapp")}          # far: nothing
    assert all(d["mode"] == "SIMULATED" for d in detail["deliveries"])
    assert not has_number(json.dumps(detail))
    again = api.post(f"/api/ops/alerts/{a['alert_id']}/send", headers=AUTH).json()
    assert again["already_sent"] is True
    assert len(api.get(f"/api/ops/alerts/{a['alert_id']}", headers=AUTH).json()["deliveries"]) == 3
    cb = api.post(f"/api/ops/alerts/{a['alert_id']}/simulate-callbacks", headers=AUTH)
    assert cb.status_code == 200 and cb.json()["applied"] == 6
    statuses = {d["status"] for d in api.get(f"/api/ops/alerts/{a['alert_id']}", headers=AUTH).json()["deliveries"]}
    assert statuses == {"DELIVERED"}
    assert api.get("/api/ops/alerts", headers=AUTH).status_code == 200


def test_state_errors_are_409(api):
    publish(WATCH)
    a = api.post("/api/ops/alerts/draft", json={"window_min": 60}, headers=AUTH).json()
    assert api.post(f"/api/ops/alerts/{a['alert_id']}/approve", headers=AUTH).status_code == 409
    assert api.post(f"/api/ops/alerts/{a['alert_id']}/send", headers=AUTH).status_code == 409
    assert api.get("/api/ops/alerts/AL999", headers=AUTH).status_code == 404


# ----------------------------------------------------------------------------- public endpoints
def test_public_alert_headers_versions_and_404s(api):
    publish(WATCH)
    draft = api.post("/api/ops/alerts/draft", json={"window_min": 60}, headers=AUTH).json()
    a, _ = sent_alert(api)
    r = api.get(f"/api/public/alerts/{a['public_id']}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=30"
    assert r.headers["x-robots-tag"] == "noindex" and r.headers["referrer-policy"] == "no-referrer"
    body = r.json()
    assert body["version"] == 1 and body["status_text"] == "Flooding possible – plan ahead"
    text = r.text.replace(a["public_id"], "<id>")
    for forbidden in ("SUB", "subscriber", "deliver", "_actor", "trip", "vehicle", "approved_by", "dispatcher"):
        assert forbidden not in text, forbidden
    assert not NUMBER_RE.search(text)
    assert api.get(f"/api/public/alerts/{a['public_id']}?v=1").json()["version"] == 1
    unknown = api.get("/api/public/alerts/unknown-id-xyz")
    unpublished = api.get(f"/api/public/alerts/{draft['public_id']}")
    api.post(f"/api/ops/alerts/{draft['alert_id']}/cancel", headers=AUTH)
    cancelled = api.get(f"/api/public/alerts/{draft['public_id']}")
    wrong_version = api.get(f"/api/public/alerts/{a['public_id']}?v=7")
    assert {x.status_code for x in (unknown, unpublished, cancelled, wrong_version)} == {404}
    assert unknown.content == unpublished.content == cancelled.content == wrong_version.content


def test_resolve_via_api(api):
    a, _ = sent_alert(api)
    r = api.post(f"/api/ops/alerts/{a['alert_id']}/resolve", headers=AUTH)
    assert r.status_code == 200 and r.json()["state"] == "RESOLVED"
    pub = api.get(f"/api/public/alerts/{a['public_id']}").json()
    assert pub["state"] == "resolved" and pub["status_text"] == "No longer active"


def test_official_alerts_public_cached_only(api):
    r = api.get("/api/public/official-alerts")
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["label"] == "OFFICIAL (SACHET/NDMA)"
    assert api.post("/api/ops/official-alerts/refresh", headers=AUTH).status_code == 409    # no feed configured


# ----------------------------------------------------------------------------- provider webhooks
def _twilio_sig(token, url, params):
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


STATUS_URL = "http://testserver/api/ops/providers/twilio/status"
INBOUND_URL = "http://testserver/api/ops/providers/twilio/inbound"


def test_twilio_status_requires_valid_signature_and_never_regresses(api, monkeypatch):
    from floodnet.api import messaging_api
    sids = iter(f"SM{i:032x}" for i in range(1, 100))

    def fake_http(url, **kw):
        return 201, {"sid": next(sids), "status": "queued"}
    twilio = pv.TwilioProvider(account_sid="AC" + "f" * 32, auth=("AC" + "f" * 32, "fake"), sms_from="+15550000000",
                               whatsapp_from="whatsapp:+15550000001", http_post=fake_http)
    monkeypatch.setattr(messaging_api, "provider_factory", lambda store: (lambda ch, to, sim: (twilio, "LIVE", "test")))
    a, (direct, _, _) = sent_alert(api)
    d = [x for x in api.get(f"/api/ops/alerts/{a['alert_id']}", headers=AUTH).json()["deliveries"]
         if x["subscriber_id"] == direct][0]
    store_doc = get_store().get("deliveries", d["delivery_id"])
    mid = store_doc["provider_message_id"]
    params = {"MessageSid": mid, "MessageStatus": "delivered", "AccountSid": "AC" + "f" * 32}
    form = {"content-type": "application/x-www-form-urlencoded"}

    assert api.post("/api/ops/providers/twilio/status", data=params).status_code == 403      # no token configured
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-auth-token")
    assert api.post("/api/ops/providers/twilio/status", data=params).status_code == 401      # unsigned
    bad = api.post("/api/ops/providers/twilio/status", data=params, headers={"X-Twilio-Signature": "AAAA"})
    assert bad.status_code == 403
    sig = _twilio_sig("fake-auth-token", STATUS_URL, params)
    ok = api.post("/api/ops/providers/twilio/status", data=params, headers={"X-Twilio-Signature": sig, **form})
    assert ok.status_code == 200 and ok.json()["applied"] is True
    late = {**params, "MessageStatus": "sent"}
    r = api.post("/api/ops/providers/twilio/status", data=late,
                 headers={"X-Twilio-Signature": _twilio_sig("fake-auth-token", STATUS_URL, late)})
    assert r.status_code == 200 and r.json()["applied"] is False
    assert get_store().get("deliveries", d["delivery_id"])["status"] == "DELIVERED"


def test_twilio_inbound_stop(api, monkeypatch):
    sid = add(api, DIRECT)["id"]
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-auth-token")
    params = {"From": DIRECT["number"], "Body": "STOP", "MessageSid": "SM" + "e" * 32}
    assert api.post("/api/ops/providers/twilio/inbound", data=params).status_code == 401
    r = api.post("/api/ops/providers/twilio/inbound", data=params,
                 headers={"X-Twilio-Signature": _twilio_sig("fake-auth-token", INBOUND_URL, params)})
    assert r.status_code == 200 and "<Response" in r.text and DIRECT["number"] not in r.text
    assert [s for s in api.get("/api/ops/subscribers", headers=AUTH).json()["subscribers"] if s["id"] == sid][0]["status"] == "OPTED_OUT"


def test_msg91_dlr_shared_secret(api, monkeypatch):
    body = {"data": [{"requestId": "req-unknown", "status": 1}]}
    assert api.post("/api/ops/providers/msg91/dlr", json=body).status_code == 403              # not configured
    monkeypatch.setenv("MSG91_WEBHOOK_SECRET", "fake-shared-secret")
    assert api.post("/api/ops/providers/msg91/dlr", json=body).status_code == 401
    assert api.post("/api/ops/providers/msg91/dlr", json=body,
                    headers={"X-FloodNet-Webhook-Secret": "wrong"}).status_code == 403
    r = api.post("/api/ops/providers/msg91/dlr", json=body, headers={"X-FloodNet-Webhook-Secret": "fake-shared-secret"})
    assert r.status_code == 200 and r.json()["results"][0]["matched"] is False


def test_whatsapp_webhook_handshake_and_signature(api, monkeypatch):
    sid = add(api, NEARBY)["id"]
    q = {"hub.mode": "subscribe", "hub.verify_token": "fake-verify", "hub.challenge": "12345"}
    assert api.get("/api/ops/providers/whatsapp/webhook", params=q).status_code == 403       # not configured
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "fake-verify")
    r = api.get("/api/ops/providers/whatsapp/webhook", params=q)
    assert r.status_code == 200 and r.text == "12345"
    assert api.get("/api/ops/providers/whatsapp/webhook", params={**q, "hub.verify_token": "x"}).status_code == 403
    payload = {"entry": [{"changes": [{"value": {
        "statuses": [{"id": "wamid.FAKE", "status": "delivered", "timestamp": "1760000000"}],
        "messages": [{"from": NEARBY["number"][1:], "type": "text", "text": {"body": "STOP"}}]}}]}]}
    raw = json.dumps(payload).encode()
    hdr = {"content-type": "application/json"}
    assert api.post("/api/ops/providers/whatsapp/webhook", content=raw, headers=hdr).status_code == 403  # no secret
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "fake-app-secret")
    assert api.post("/api/ops/providers/whatsapp/webhook", content=raw, headers=hdr).status_code == 401
    assert api.post("/api/ops/providers/whatsapp/webhook", content=raw,
                    headers={**hdr, "X-Hub-Signature-256": "sha256=00"}).status_code == 403
    sig = "sha256=" + hmac.new(b"fake-app-secret", raw, hashlib.sha256).hexdigest()
    ok = api.post("/api/ops/providers/whatsapp/webhook", content=raw, headers={**hdr, "X-Hub-Signature-256": sig})
    assert ok.status_code == 200 and ok.json()["statuses"] == 1 and ok.json()["stops"] == 1
    assert [s for s in api.get("/api/ops/subscribers", headers=AUTH).json()["subscribers"] if s["id"] == sid][0]["status"] == "OPTED_OUT"


def test_dashboard_base_path_is_used_only_on_its_own_origin(api):
    """Lead addition: links follow the dashboard's base path (/static locally) but never another site."""
    publish(WATCH)
    ok = api.post("/api/ops/alerts/draft", headers={**AUTH, "Origin": "http://localhost:5173"},
                     json={"window_min": 60, "public_base": "http://localhost:5173/static"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["link"].startswith("http://localhost:5173/static/alerts/")
    bad = api.post("/api/ops/alerts/draft", headers={**AUTH, "Origin": "http://localhost:5173"},
                      json={"window_min": 60, "public_base": "https://evil.example/static"})
    assert bad.status_code == 422
