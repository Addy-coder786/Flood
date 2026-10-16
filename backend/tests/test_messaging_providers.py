"""Messaging providers (DECISIONS D-44, docs/LIVE_OPERATIONS_RESEARCH.md §6). Written before
floodnet/messaging/providers.py existed.

No test here makes a network call: Twilio and MSG91 adapters get an injected fake HTTP function; every credential and
number below is FAKE (numbers in the +9100000 range are not valid Indian mobile numbers)."""
import base64
import hashlib
import hmac
import json

import pytest

from floodnet.messaging import providers as pv

FAKE = "+910000000007"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())
    for n in ("FLOODNET_LIVE_MESSAGING", "FLOODNET_MESSAGING_ALLOWLIST", "FLOODNET_PUBLIC_BASE_URL", "TWILIO_ACCOUNT_SID",
              "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "TWILIO_SMS_FROM",
              "TWILIO_WHATSAPP_FROM", "MSG91_AUTHKEY", "MSG91_TEMPLATE_ID", "FLOODNET_SMS_PROVIDER",
              "MSG91_WEBHOOK_SECRET"):
        monkeypatch.delenv(n, raising=False)


# ----------------------------------------------------------------------------- status mapping
def test_twilio_status_mapping():
    m = {"queued": "ACCEPTED", "accepted": "ACCEPTED", "sending": "ACCEPTED", "sent": "SENT", "delivered": "DELIVERED",
         "undelivered": "FAILED", "failed": "FAILED", "read": "READ"}
    for raw, want in m.items():
        assert pv.map_twilio_status(raw) == want
    assert pv.map_twilio_status("something-new") is None


def test_msg91_dlr_mapping():
    assert pv.map_msg91_status(0)[0] == "SENT"
    assert pv.map_msg91_status("1")[0] == "DELIVERED"
    assert pv.map_msg91_status(2)[0] == "FAILED"
    for code in (9, 16, 17, 20, 25):
        assert pv.map_msg91_status(code)[0] == "REJECTED"
    assert "NDNC" in pv.map_msg91_status(9)[1]
    assert pv.map_msg91_status(99)[0] is None


def test_whatsapp_cloud_mapping():
    assert [pv.map_whatsapp_status(s) for s in ("sent", "delivered", "read", "failed", "x")] == \
        ["SENT", "DELIVERED", "READ", "FAILED", None]


def test_forward_only_transitions():
    assert pv.can_advance("QUEUED", "SUBMITTED") and pv.can_advance("SUBMITTED", "ACCEPTED")
    assert pv.can_advance("UNKNOWN", "DELIVERED")                 # a late callback resolves a timeout
    assert pv.can_advance("SENT", "FAILED")
    assert not pv.can_advance("DELIVERED", "SENT")                # out-of-order callback: no regression
    assert not pv.can_advance("DELIVERED", "FAILED")
    assert not pv.can_advance("FAILED", "DELIVERED")
    assert not pv.can_advance("SUPPRESSED", "SUBMITTED")
    assert not pv.can_advance("READ", "DELIVERED")
    assert not pv.can_advance("SENT", "SENT")


# ----------------------------------------------------------------------------- signatures
def _twilio_sig(token, url, params):
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def test_twilio_signature():
    url, token = "https://example.test/api/ops/providers/twilio/status", "fake-auth-token"
    params = {"MessageSid": "SM" + "0" * 32, "MessageStatus": "delivered", "AccountSid": "AC" + "1" * 32}
    good = _twilio_sig(token, url, params)
    assert pv.twilio_signature(url, params, token) == good
    assert pv.verify_twilio(url, params, good, token)
    assert not pv.verify_twilio(url, {**params, "MessageStatus": "failed"}, good, token)
    assert not pv.verify_twilio(url, params, good, "other-token")
    assert not pv.verify_twilio(url, params, "", token)
    assert not pv.verify_twilio(url, params, good, "")             # no configured token: never accepted


def test_whatsapp_signature():
    raw, secret = b'{"entry": []}', "fake-app-secret"
    good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert pv.verify_whatsapp(raw, good, secret)
    assert not pv.verify_whatsapp(raw + b" ", good, secret)
    assert not pv.verify_whatsapp(raw, good.replace("sha256=", ""), secret)
    assert not pv.verify_whatsapp(raw, good, "")


def test_msg91_shared_secret():
    assert not pv.verify_shared_secret("x", "")
    assert not pv.verify_shared_secret("", "s3")
    assert not pv.verify_shared_secret("s4", "s3")
    assert pv.verify_shared_secret("s3", "s3")


# ----------------------------------------------------------------------------- simulated provider
def test_simulated_provider_is_deterministic_idempotent_and_keeps_no_number():
    p = pv.SimulatedProvider()
    a = p.send("sms", FAKE, "hello", "AL1:v1:SUB1:sms")
    b = p.send("sms", FAKE, "hello", "AL1:v1:SUB1:sms")
    assert a["status"] == "ACCEPTED" and a["provider_message_id"] == b["provider_message_id"]
    assert a["provider_message_id"].startswith("SIM-")
    assert len(p.outbox) == 1 and p.outbox[0]["to"] == "•••• 0007" and p.outbox[0]["label"] == "SIMULATED"
    assert FAKE not in json.dumps(p.outbox) and "0000000007" not in json.dumps(p.outbox)


def test_simulated_provider_injected_failures_and_callbacks():
    p = pv.SimulatedProvider(failures={"k-rej": "REJECTED", "k-fail": "FAILED", "k-time": "TIMEOUT",
                                       "k-late": "FAIL_AFTER_ACCEPT"})
    assert p.send("sms", FAKE, "x", "k-rej")["status"] == "REJECTED"
    assert p.send("sms", FAKE, "x", "k-fail")["status"] == "FAILED"
    assert p.send("sms", FAKE, "x", "k-time")["status"] == "UNKNOWN"
    late = p.send("sms", FAKE, "x", "k-late")
    ok = p.send("sms", FAKE, "x", "k-ok")
    assert late["status"] == "ACCEPTED"
    assert [c["status"] for c in p.callbacks(late["provider_message_id"])] == ["SENT", "FAILED"]
    assert [c["status"] for c in p.callbacks(ok["provider_message_id"])] == ["SENT", "DELIVERED"]
    assert all(c["provider"] == "simulated" for c in p.callbacks(ok["provider_message_id"]))


# ----------------------------------------------------------------------------- Twilio adapter (fake HTTP)
class FakeHTTP:
    def __init__(self, result=None, exc=None):
        self.calls, self.result, self.exc = [], result, exc

    def __call__(self, url, **kw):
        self.calls.append({"url": url, **kw})
        if self.exc:
            raise self.exc
        return self.result


def _twilio(http):
    return pv.TwilioProvider(account_sid="AC" + "f" * 32, auth=("AC" + "f" * 32, "fake-token"),
                             sms_from="+15550000000", whatsapp_from="whatsapp:+15550000001", http_post=http)


def test_twilio_adapter_shapes_and_results():
    http = FakeHTTP((201, {"sid": "SM" + "a" * 32, "status": "queued"}))
    r = _twilio(http).send("whatsapp", FAKE, "body text", "AL1:v1:SUB1:whatsapp")
    assert r == {"status": "ACCEPTED", "provider_message_id": "SM" + "a" * 32, "error": None, "raw_status": "queued"}
    call = http.calls[0]
    assert call["url"].endswith("/Accounts/AC" + "f" * 32 + "/Messages.json")
    assert call["data"]["To"] == "whatsapp:" + FAKE and call["data"]["From"] == "whatsapp:+15550000001"
    assert call["data"]["Body"] == "body text"
    sms = FakeHTTP((201, {"sid": "SM" + "b" * 32, "status": "queued"}))
    _twilio(sms).send("sms", FAKE, "b", "k")
    assert sms.calls[0]["data"]["To"] == FAKE and sms.calls[0]["data"]["From"] == "+15550000000"


def test_twilio_adapter_rejection_timeout_and_failure_never_echo_number():
    rej = _twilio(FakeHTTP((400, {"code": 21211, "message": f"The 'To' number {FAKE} is not valid"}))).send("sms", FAKE, "b", "k")
    assert rej["status"] == "REJECTED" and rej["error"]["code"] == 21211
    assert FAKE not in json.dumps(rej) and "0000000007" not in json.dumps(rej)
    assert _twilio(FakeHTTP(exc=TimeoutError("read timed out"))).send("sms", FAKE, "b", "k")["status"] == "UNKNOWN"
    assert _twilio(FakeHTTP((503, {}))).send("sms", FAKE, "b", "k")["status"] == "UNKNOWN"
    assert _twilio(FakeHTTP(exc=ConnectionRefusedError("refused"))).send("sms", FAKE, "b", "k")["status"] == "FAILED"


# ----------------------------------------------------------------------------- MSG91 adapter (fake HTTP)
def test_msg91_adapter_shape():
    http = FakeHTTP((200, {"type": "success", "message": "req-123"}))
    p = pv.Msg91Provider(authkey="fake-authkey", template_id="fake-template", http_post=http)
    r = p.send("sms", FAKE, "body", "k", variables={"link": "http://x/alerts/abc"})
    assert r["status"] == "ACCEPTED" and r["provider_message_id"] == "req-123"
    c = http.calls[0]
    assert c["url"] == "https://control.msg91.com/api/v5/flow/"
    assert c["headers"]["authkey"] == "fake-authkey"
    assert c["json"]["template_id"] == "fake-template" and c["json"]["recipients"][0]["mobiles"] == FAKE[1:]
    assert c["json"]["recipients"][0]["link"] == "http://x/alerts/abc"
    err = pv.Msg91Provider(authkey="a", template_id="t", http_post=FakeHTTP((200, {"type": "error", "message": "bad"})))
    assert err.send("sms", FAKE, "b", "k")["status"] == "REJECTED"
    assert pv.Msg91Provider(authkey="a", template_id="t", http_post=FakeHTTP()).send("whatsapp", FAKE, "b", "k")["status"] == "REJECTED"


# ----------------------------------------------------------------------------- the live gate
def _live_env(monkeypatch, allow):
    monkeypatch.setenv("FLOODNET_LIVE_MESSAGING", "1")
    monkeypatch.setenv("FLOODNET_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "f" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_SMS_FROM", "+15550000000")
    monkeypatch.setenv("FLOODNET_MESSAGING_ALLOWLIST", allow)


def test_live_gate_defaults_to_simulated(monkeypatch):
    ok, why = pv.live_sending_allowed("sms", "+919800000000")
    assert not ok and "FLOODNET_LIVE_MESSAGING" in why
    prov, mode, _ = pv.choose_provider("sms", "+919800000000", simulated=False)
    assert mode == "SIMULATED" and isinstance(prov, pv.SimulatedProvider)


def test_live_gate_needs_credentials_and_allow_list(monkeypatch):
    monkeypatch.setenv("FLOODNET_LIVE_MESSAGING", "1")
    ok, why = pv.live_sending_allowed("sms", "+919800000000")
    assert not ok and "credentials" in why
    _live_env(monkeypatch, "+919800000001")
    ok, why = pv.live_sending_allowed("sms", "+919800000000")
    assert not ok and "allow-list" in why
    _live_env(monkeypatch, "+91 98000 00000, +919800000001")
    ok, why = pv.live_sending_allowed("sms", "+919800000000")
    assert ok, why
    assert "+919800000000" not in why
    http = FakeHTTP((201, {"sid": "SM" + "c" * 32, "status": "queued"}))
    prov, mode, _ = pv.choose_provider("sms", "+919800000000", simulated=False, http_post=http)
    assert mode == "LIVE" and isinstance(prov, pv.TwilioProvider)
    prov2, mode2, _ = pv.choose_provider("sms", "+919800000000", simulated=True, http_post=http)
    assert mode2 == "SIMULATED"                                         # simulated subscribers never go live
    assert http.calls == []                                             # choosing never sends
