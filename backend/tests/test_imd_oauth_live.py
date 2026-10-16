"""Opt-in LIVE check of automatic IMD authentication -- NOT run by a normal `pytest` invocation.

Runs through the real FastAPI app with NO manually supplied JWT: IMD_API_TOKEN is removed, so the backend itself
must call IMD's token endpoint (POST /api/oauth/token.php) with IMD_EMAIL / IMD_PASSWORD. Prints only the HTTP
status, token_type, expires_in and whether the authenticated IMD request succeeded -- never the token.

Run explicitly (from backend/):
    RUN_LIVE_IMD=1 .venv/Scripts/python.exe -m pytest tests/test_imd_oauth_live.py -v -s -m live
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_IMD") != "1",
                       reason="live IMD tests are opt-in; set RUN_LIVE_IMD=1 to run them"),
]


@pytest.fixture
def backend(monkeypatch):
    """The real app, a fresh token manager, and NO JWT anywhere (env, .env, file): only the account credentials."""
    import floodnet.config  # noqa: F401 -- loads the root .env
    missing = [n for n in ("IMD_API_KEY", "IMD_EMAIL", "IMD_PASSWORD") if not os.environ.get(n)]
    if missing:
        pytest.fail(f"RUN_LIVE_IMD=1 but {', '.join(missing)} not configured on the server")
    from fastapi.testclient import TestClient
    from floodnet.api.main import app
    from floodnet.rainfall import imd_auth
    from floodnet.rainfall.imd_token_issuer import IMDTokenIssuer
    from floodnet.rainfall.provider import LIVE_ID, list_providers
    for name in ("IMD_API_TOKEN", "IMD_API_TOKEN_FILE", "IMD_TOKEN_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLOODNET_IMD_AUTO_RENEW", "0")     # count token requests exactly (no background keeper)
    issuer = IMDTokenIssuer()
    mgr = imd_auth.IMDTokenManager(dotenv_path=imd_auth.config.REPO_DIR / "__no_dotenv__", issuer=issuer)
    monkeypatch.setattr(imd_auth, "manager", mgr)
    live = list_providers()[LIVE_ID]
    live.clear_cache()
    with TestClient(app) as client:
        yield client, mgr, issuer, live
    live.clear_cache()


def _report(step, issuer, r):
    print(f"\n[{step}] token endpoint HTTP {issuer.last_http_status}, token_type={issuer.last_token_type}, "
          f"expires_in={issuer.last_expires_in}; token requests so far={issuer.requests}; "
          f"IMD-backed request HTTP {r.status_code} -> {'SUCCESS' if r.status_code == 200 else 'FAILED'}")


def test_backend_obtains_a_token_by_itself_and_renews_it_after_replacement(backend):
    client, mgr, issuer, live = backend
    from floodnet.rainfall import imd_auth
    from floodnet.rainfall.imd_token_sources import TokenReading

    # 1. no JWT supplied: the status says one will be generated automatically
    ds = client.get("/api/data-status").json()
    assert mgr.token() is None and ds["imd_auto_renewal"] is True and ds["imd_auth_status"] == "EXPIRED"

    # 2-5. the first IMD-backed request makes the backend generate a token and use it
    r = client.post("/api/simulate", json={"scenario_id": "live"})
    _report("first request", issuer, r)
    assert issuer.requests == 1 and issuer.last_http_status == 200, "token generation did not succeed"
    assert mgr.status().source == imd_auth.ISSUED_SOURCE
    if r.status_code != 200:
        pytest.fail(f"token generated OK, but the IMD data request failed: {r.json().get('detail')}")
    ds = client.get("/api/data-status").json()
    assert ds["imd_auth_status"] in ("VALID", "EXPIRING_SOON") and ds["imd_live"]["ok"] is True

    # 6-10. replace the token with a well-formed one IMD will reject -> 401 -> one new token -> one retry
    bogus = "eyJ1aWQiOjAsImV4cCI6NDEwMjQ0NDgwMH0." + "x" * 64
    mgr._issued = TokenReading(bogus, imd_auth.ISSUED_SOURCE, time.time(), None)
    live.clear_cache()
    r2 = client.post("/api/simulate", json={"scenario_id": "live"})
    _report("after token replaced", issuer, r2)
    assert issuer.requests == 2, "the rejected token did not trigger exactly one new token generation"
    assert r2.status_code == 200 and mgr.token() != bogus
    ds = client.get("/api/data-status").json()
    assert ds["imd_auth_status"] in ("VALID", "EXPIRING_SOON") and ds["imd_live"]["ok"] is True
    for field in ("IMD_EMAIL", "IMD_PASSWORD", "IMD_API_KEY"):
        assert os.environ[field] not in str(ds)
    assert mgr.token() not in str(ds)
