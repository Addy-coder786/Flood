"""Role-based access for live operations (DECISIONS D-39). Written before floodnet/ops/auth.py existed.

Rules: tokens are random and stored only as hashes; a configured dispatcher token is compared in constant time;
with no dispatcher token configured only loopback clients are dispatchers, and never on Vercel; a driver token opens
exactly one trip; the public role needs nothing and gets nothing private."""
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from floodnet.ops import auth


def _app():
    app = FastAPI()

    @app.get("/d")
    def d(who=Depends(auth.require_dispatcher)):
        return {"who": who}
    return app


def test_tokens_are_random_and_only_hashes_compare():
    a, b = auth.new_token(), auth.new_token()
    assert a != b and len(a) >= 22
    h = auth.hash_token(a)
    assert h != a and auth.token_matches(a, h) and not auth.token_matches(b, h)


def test_configured_dispatcher_token_is_required(monkeypatch):
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", "s3cret-token-value")
    c = TestClient(_app())
    assert c.get("/d").status_code == 401
    assert c.get("/d", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/d", headers={"Authorization": "Bearer s3cret-token-value"})
    assert r.status_code == 200 and r.json()["who"] == "dispatcher"
    assert c.get("/d", headers={"X-FloodNet-Token": "s3cret-token-value"}).status_code == 200


def test_no_token_configured_allows_loopback_only(monkeypatch):
    monkeypatch.delenv("FLOODNET_DISPATCHER_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    assert auth.dispatcher_allowed(None, "127.0.0.1") is True
    assert auth.dispatcher_allowed(None, "::1") is True
    assert auth.dispatcher_allowed(None, "203.0.113.9") is False
    assert auth.dispatcher_allowed(None, "testclient") is False      # starlette's TestClient is not loopback
    c = TestClient(_app())
    assert c.get("/d").status_code == 401


def test_no_token_on_vercel_is_never_open(monkeypatch):
    monkeypatch.delenv("FLOODNET_DISPATCHER_TOKEN", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert auth.dispatcher_allowed(None, "127.0.0.1") is False


def test_phone_send_now_requires_the_dispatcher(monkeypatch):
    """D-39: the existing send endpoint had no access control and was called during browser testing without any
    user action. It must now refuse an unauthenticated non-local caller."""
    from floodnet.api.main import app
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", "s3cret-token-value")
    c = TestClient(app)
    for path in ("/api/alerts/phone/send", "/api/alerts/whatsapp/send"):
        assert c.post(path, json={"kind": "test", "lines": ["x"]}).status_code == 401
