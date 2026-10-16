"""MSG91 delivery reports and adapter details from docs/RESEARCH_ALERTING_AND_INTERNATIONAL.md (2026-10-11).
Written before the changes.

Documented behaviour only (msg91.com/help/webhook-new, docs.msg91.com/sms/send-sms, as summarised in the research):
  * a webhook that answers 4xx/5xx is auto-paused; no answer within 8 s is retried (duplicates possible)
  * Webhook (New) posts JSON, one event per recipient, with requestId, telNum (the recipient's NUMBER), CRQID, status
  * the older report form nests the status: data=[{requestId, report:[{status}]}]
  * CRQID sent with a request is returned in reports and webhooks
Fixtures below are HAND-WRITTEN from that documented shape, not captured from MSG91. Numbers are DUMMIES, secrets FAKE;
the MSG91 adapter talks to a FAKE HTTP function: nothing leaves the process."""
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from floodnet.messaging import devtest as dt
from floodnet.messaging import providers as pv
from floodnet.ops.store import get_store, reset_store_for_tests

TOKEN = "fake-dispatcher-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SECRET = {"X-FloodNet-Webhook-Secret": "fake-shared-secret"}
TEXT_SHA = hashlib.sha256(dt.TEST_TEXT.encode("utf-8")).hexdigest()
DUMMY_NATIONAL = "6000000001"


class FakeHTTP:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, url, **kw):
        self.calls.append({"url": url, **kw})
        return self.response


@pytest.fixture
def api(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from floodnet.api import messaging_api
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FLOODNET_DEV_TEST_RECIPIENTS", "+91" + DUMMY_NATIONAL)
    monkeypatch.setenv("MSG91_WEBHOOK_SECRET", "fake-shared-secret")
    for n in ("FLOODNET_LIVE_MESSAGING", "VERCEL", "FLOODNET_ENV", "FLOODNET_OPS_ALLOW_EPHEMERAL"):
        monkeypatch.delenv(n, raising=False)
    http = FakeHTTP((200, {"type": "success", "message": "req-0001"}))
    msg91 = pv.Msg91Provider(authkey="fake-authkey", template_id="fake-template", http_post=http)
    monkeypatch.setattr(messaging_api, "dev_test_provider_factory",
                        lambda store: (lambda ch, to, simulated: (msg91, "LIVE", "test: fake MSG91 adapter")))
    reset_store_for_tests()
    app = FastAPI()
    app.include_router(messaging_api.router)
    client = TestClient(app)
    client.http = http
    yield client
    reset_store_for_tests()


def do_send(api, key="ui-click-msg91-01"):
    seeded = api.post("/api/ops/subscribers/seed-dev-test", json={"locality_id": "dadar-west"}, headers=AUTH).json()
    sid = seeded["seeded"][0]["id"]
    # as the UI does: preview first, then send in the mode the preview showed (QA 2026-10-11 M8)
    mode = api.post("/api/ops/test-sends/preview", json={"subscriber_id": sid, "channel": "sms"}, headers=AUTH).json()["mode"]
    r = api.post("/api/ops/test-sends", json={"subscriber_id": sid, "channel": "sms", "idempotency_key": key,
                                             "text_sha256": TEXT_SHA, "expected_mode": mode}, headers=AUTH)
    assert r.status_code == 201, r.text
    return r.json()


def all_text() -> str:
    db = get_store()._db
    return json.dumps([r[0] for r in db.execute("SELECT body FROM docs").fetchall()]
                      + [r[0] for r in db.execute("SELECT body FROM audit").fetchall()])


def status_of(api, delivery_id):
    return [s for s in api.get("/api/ops/test-sends", headers=AUTH).json()["sends"]
            if s["delivery_id"] == delivery_id][0]["status"]


# ----------------------------------------------------------------------------- adapter
def test_msg91_request_carries_crqid_equal_to_our_delivery_id(api):
    sent = do_send(api)
    payload = api.http.calls[0]["json"]
    assert payload["CRQID"] == sent["delivery_id"]


def test_msg91_crqid_in_the_adapter_directly():
    http = FakeHTTP((200, {"type": "success", "message": "req-x"}))
    pv.Msg91Provider(authkey="a", template_id="t", http_post=http).send("sms", "+910000000007", "b", "AL1:v1:SUB1:sms")
    assert http.calls[0]["json"]["CRQID"] == "AL1:v1:SUB1:sms"


# ----------------------------------------------------------------------------- report shapes
def test_flat_webhook_new_report_applies_and_never_stores_the_number(api):
    sent = do_send(api)
    rep = {"requestId": "req-0001", "telNum": "91" + DUMMY_NATIONAL, "CRQID": sent["delivery_id"], "status": "1",
           "failureReason": "", "credit": "0.25"}
    r = api.post("/api/ops/providers/msg91/dlr", json=rep, headers=SECRET)
    assert r.status_code == 200 and r.json()["results"][0]["applied"] is True
    assert DUMMY_NATIONAL not in r.text
    assert status_of(api, sent["delivery_id"]) == "DELIVERED"
    assert DUMMY_NATIONAL not in all_text()


def test_legacy_report_with_nested_status_is_read(api):
    sent = do_send(api)
    body = {"data": [{"requestId": "req-0001", "report": [{"status": "1"}]}]}
    r = api.post("/api/ops/providers/msg91/dlr", json=body, headers=SECRET)
    assert r.status_code == 200 and r.json()["results"][0]["applied"] is True
    assert status_of(api, sent["delivery_id"]) == "DELIVERED"


def test_report_matched_by_crqid_when_request_id_is_unknown(api):
    sent = do_send(api)
    rep = {"requestId": "req-unrelated", "CRQID": sent["delivery_id"], "status": "2"}
    r = api.post("/api/ops/providers/msg91/dlr", json=rep, headers=SECRET)
    assert r.status_code == 200 and r.json()["results"][0]["matched"] is True
    assert status_of(api, sent["delivery_id"]) == "FAILED"


def test_crqid_never_matches_another_providers_delivery(api, monkeypatch):
    from floodnet.api import messaging_api
    sim = pv.SimulatedProvider()
    monkeypatch.setattr(messaging_api, "dev_test_provider_factory",
                        lambda store: (lambda ch, to, simulated: (sim, "SIMULATED", "test")))
    sent = do_send(api, key="ui-click-sim-0001")
    r = api.post("/api/ops/providers/msg91/dlr", json={"CRQID": sent["delivery_id"], "status": "2"}, headers=SECRET)
    assert r.status_code == 200 and r.json()["results"][0]["matched"] is False
    assert status_of(api, sent["delivery_id"]) == "ACCEPTED"


# ----------------------------------------------------------------------------- answers that never pause the webhook
@pytest.mark.parametrize("raw", [b"not json at all " + DUMMY_NATIONAL.encode(), b"[1, 2", b"\xff\xfe", b"",
                                 b'"just a string"', b'{"data": "no list"}'])
def test_authenticated_unparseable_body_is_200_and_recorded_without_the_number(api, raw):
    r = api.post("/api/ops/providers/msg91/dlr", content=raw, headers={**SECRET, "Content-Type": "application/json"})
    assert r.status_code == 200 and DUMMY_NATIONAL not in r.text
    events = get_store().list("provider_webhook_events")
    assert events and events[-1]["provider"] == "msg91" and events[-1]["outcome"] in ("unparseable", "no_reports")
    assert DUMMY_NATIONAL not in all_text()


def test_unknown_message_and_store_outage_still_answer_200(api, monkeypatch):
    r = api.post("/api/ops/providers/msg91/dlr", json={"requestId": "req-nobody", "status": "1"}, headers=SECRET)
    assert r.status_code == 200 and r.json()["results"][0]["matched"] is False
    monkeypatch.setenv("VERCEL", "1")                       # the store refuses to open (D-38)
    reset_store_for_tests()
    r = api.post("/api/ops/providers/msg91/dlr", json={"requestId": "req-nobody", "status": "1"}, headers=SECRET)
    assert r.status_code == 200 and r.json()["recorded"] is False


def test_wrong_or_missing_secret_is_still_refused(api):
    assert api.post("/api/ops/providers/msg91/dlr", content=b"x").status_code == 401
    assert api.post("/api/ops/providers/msg91/dlr", content=b"x",
                    headers={"X-FloodNet-Webhook-Secret": "wrong"}).status_code == 403


# ----------------------------------------------------------------------------- honest status texts
def test_status_text_says_what_blocks_real_sms_and_whatsapp(api):
    st = api.get("/api/ops/messaging/status", headers=AUTH).json()
    assert "TWILIO_SMS_FROM" in st["sms_note"] and "DLT" in st["sms_note"] and "upgraded" in st["sms_note"]
    assert "24 h" in st["whatsapp_note"] and "template" in st["whatsapp_note"] and "join" in st["whatsapp_note"]
    assert "impossible" not in json.dumps(st).lower()
