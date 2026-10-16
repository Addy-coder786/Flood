"""Telegram channel and the combined phone endpoint. Telegram and Twilio are faked: no real message is sent."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from floodnet.alerts import telegram as tg
from floodnet.alerts import whatsapp as wa
from floodnet.api.main import app

TOKEN = "1234567890:" + "A" * 35


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "123456789")
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "WHATSAPP_TO"):
        monkeypatch.setenv(k, "")
    return monkeypatch


class FakeTG:
    def __init__(self, code=200, payload=None):
        self.calls, self.code, self.payload = [], code, payload

    def post(self, url, payload):
        self.calls.append((url, payload))
        return self.code, self.payload or {"ok": True, "result": {"message_id": 1}}


def test_not_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", ""); monkeypatch.setenv("TELEGRAM_CHAT_IDS", "")
    fake = FakeTG()
    assert tg.TelegramSender(post=fake.post).send(["x"])["skipped"] == "not configured" and fake.calls == []


def test_sends_banner_to_configured_chat_and_hides_token(env):
    fake = FakeTG()
    out = tg.TelegramSender(post=fake.post).send(["Hindmata 52 cm"])
    url, payload = fake.calls[0]
    assert out["sent"] == 1 and payload["chat_id"] == "123456789" and payload["text"].startswith(wa.BANNER)
    assert TOKEN not in str(out) and "•" in out["results"][0]["to"]


def test_error_hint_and_retry_after_failure(env):
    fake = FakeTG(code=400, payload={"ok": False, "description": "Bad Request: chat not found"})
    s = tg.TelegramSender(post=fake.post)
    out = s.send(["again"])
    assert out["sent"] == 0 and "press Start" in out["results"][0]["error"]["hint"]
    fake.code, fake.payload = 200, None
    assert s.send(["again"])["sent"] == 1
    assert "identical" in s.send(["again"])["skipped"]


def test_phone_endpoint_uses_configured_channels(env, monkeypatch):
    fake = FakeTG()
    monkeypatch.setattr(tg, "sender", tg.TelegramSender(post=fake.post))
    # D-39: sending now needs the dispatcher; the test client is not a loopback client, so it presents a token
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", "test-dispatcher-token")
    c = TestClient(app, headers={"Authorization": "Bearer test-dispatcher-token"})
    st = c.get("/api/alerts/phone/status").json()
    assert st["configured"] and st["telegram"]["configured"] and not st["whatsapp"]["configured"]
    j = c.post("/api/alerts/phone/send", json={"kind": "test", "lines": ["hello"]}).json()
    assert set(j["channels"]) == {"telegram"} and j["sent"] == 1
    env.setenv("TELEGRAM_CHAT_IDS", "")
    r = c.post("/api/alerts/phone/send", json={"lines": ["y"]})
    assert r.status_code == 503 and "telegram" in r.json()["detail"]["missing"]
