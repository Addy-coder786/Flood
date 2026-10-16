"""WhatsApp alerts (floodnet/alerts/whatsapp.py): never sends without configuration, always carries the TEST banner,
only to configured numbers, de-duplicated, rate-limited, and never leaks a credential. Twilio is faked: the test
suite never sends a real message."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from floodnet.alerts import whatsapp as wa
from floodnet.api.main import app

GOOD = {"TWILIO_ACCOUNT_SID": "AC" + "0" * 32, "TWILIO_API_KEY_SID": "SK" + "1" * 32, "TWILIO_API_KEY_SECRET": "s3cret-value",
        "TWILIO_AUTH_TOKEN": "", "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
        "WHATSAPP_TO": "whatsapp:+919800000001, whatsapp:+919800000002"}


@pytest.fixture
def env(monkeypatch):
    for k, v in GOOD.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


class FakeTwilio:
    def __init__(self, code=201, payload=None):
        self.calls, self.code, self.payload = [], code, payload

    def post(self, url, auth, data):
        self.calls.append((url, auth, data))
        return self.code, self.payload or {"sid": "SM" + "a" * 32, "status": "queued"}

    def get(self, url, auth):
        return 200, {"sid": "SM" + "a" * 32, "status": "failed", "error_code": 63015, "error_message": "not in sandbox"}


def make(fake, t=[0.0]):
    return wa.WhatsAppSender(post=fake.post, get=fake.get, clock=lambda: t[0])


def test_not_configured_sends_nothing(monkeypatch):
    for k in GOOD:
        monkeypatch.setenv(k, "")
    fake = FakeTwilio()
    out = make(fake).send(["hello"])
    assert out["sent"] == 0 and out["skipped"] == "not configured" and fake.calls == []
    assert any("TWILIO_ACCOUNT_SID" in m for m in out["missing"])


def test_api_key_sid_in_account_slot_is_flagged(env):
    env.setenv("TWILIO_ACCOUNT_SID", "SK" + "1" * 32)
    assert any("an SK... value is an API key" in m for m in wa.config()["missing"])


def test_banner_recipients_and_auth(env):
    fake = FakeTwilio()
    out = make(fake).send(["Hindmata 52 cm at T+45"], "forecast")
    assert out["sent"] == 2 and len(fake.calls) == 2
    url, auth, data = fake.calls[0]
    assert url.endswith(f"/Accounts/{GOOD['TWILIO_ACCOUNT_SID']}/Messages.json")
    assert auth == (GOOD["TWILIO_API_KEY_SID"], GOOD["TWILIO_API_KEY_SECRET"])
    assert data["Body"].startswith(wa.BANNER) and "Hindmata" in data["Body"]
    assert {c[2]["To"] for c in fake.calls} == {"whatsapp:+919800000001", "whatsapp:+919800000002"}
    assert all("•" in r["to"] and "9800000001" not in r["to"] for r in out["results"])     # masked


def test_auth_token_fallback(env):
    env.setenv("TWILIO_API_KEY_SID", ""); env.setenv("TWILIO_AUTH_TOKEN", "tok")
    fake = FakeTwilio()
    make(fake).send(["x"])
    assert fake.calls[0][1] == (GOOD["TWILIO_ACCOUNT_SID"], "tok")


def test_dedup_and_rate_limit(env):
    t = [0.0]
    fake = FakeTwilio()
    s = make(fake, t)
    assert s.send(["same"])["sent"] == 2
    assert "identical" in s.send(["same"])["skipped"]
    t[0] = wa.DEDUP_WINDOW_S + 1
    assert s.send(["same"])["sent"] == 2                                   # allowed again after the window
    for i in range(20):
        s.send([f"msg {i}"])
    assert "rate limit" in s.send(["one more"])["skipped"]


def test_long_body_is_trimmed(env):
    assert len(wa.compose(["x" * 2000])) == wa.MAX_BODY_CHARS


def test_twilio_error_is_explained_without_secrets(env):
    fake = FakeTwilio(code=401, payload={"code": 20003, "message": "Authenticate"})
    out = make(fake).send(["x"])
    r = out["results"][0]
    assert not r["ok"] and r["error"]["code"] == 20003 and "credentials" in r["error"]["hint"]
    assert GOOD["TWILIO_API_KEY_SECRET"] not in str(out)


def test_message_status(env):
    st = make(FakeTwilio()).message_status("SM" + "a" * 32)
    assert st["status"] == "failed" and st["error"]["code"] == 63015 and "sandbox" in st["error"]["hint"]
    with pytest.raises(ValueError):
        make(FakeTwilio()).message_status("../../Accounts")


def test_api(env, monkeypatch):
    fake = FakeTwilio()
    monkeypatch.setattr(wa, "sender", make(fake, [1e6]))
    # D-39: sending now needs the dispatcher; the test client is not a loopback client, so it presents a token
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", "test-dispatcher-token")
    c = TestClient(app, headers={"Authorization": "Bearer test-dispatcher-token"})
    st = c.get("/api/alerts/whatsapp/status").json()
    assert st["configured"] and st["auth"] == "api_key" and GOOD["TWILIO_API_KEY_SECRET"] not in str(st)
    r = c.post("/api/alerts/whatsapp/send", json={"kind": "test", "lines": ["hello from tests"]})
    assert r.status_code == 200 and r.json()["sent"] == 2
    assert c.post("/api/alerts/whatsapp/send", json={"lines": ["x" * 300]}).status_code == 422
    assert c.get("/api/alerts/whatsapp/message/not-a-sid").status_code == 422
    env.setenv("WHATSAPP_TO", "")
    r = c.post("/api/alerts/whatsapp/send", json={"lines": ["y"]})
    assert r.status_code == 503 and "WHATSAPP_TO" in str(r.json())


def test_failed_send_does_not_block_a_retry(env):
    fake = FakeTwilio(code=400, payload={"code": 21654, "message": "ContentSid Required"})
    s = make(fake, [0.0])
    out = s.send(["retry me"])
    assert out["sent"] == 0 and "template" in out["results"][0]["error"]["hint"]
    fake.code, fake.payload = 201, None
    assert s.send(["retry me"])["sent"] == 2            # not blocked by de-duplication after a failure
