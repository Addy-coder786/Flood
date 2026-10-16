"""IMD token manager + rainfall-source failover + secret hygiene.

IMD's bearer token expires about hourly and can only be renewed by a human (password + CAPTCHA). These tests pin
down what FloodNet does about it: know the token's state, never leak it, accept a rotated token with no restart,
and -- whatever IMD does -- keep forecasting from the next honest source with an explicit label.
All upstream calls are mocked; no credential is needed or used.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import replace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from floodnet import config
from floodnet.api import state
from floodnet.api.main import app
from floodnet.rainfall import imd_auth
from floodnet.rainfall.imd_auth import IMDTokenManager, EXPIRED, EXPIRING_SOON, UNAVAILABLE, VALID
from floodnet.rainfall.provider import (ECMWF_ID, IMD_SRI_ID, LIVE_ID, IMDObservationProvider, ProviderUnavailable,
                                        RainfallSourceMeta, list_providers)
from floodnet.rainfall.source_manager import CACHED, DEMO, FORECAST, LIVE, RADAR, SourceManager

KEY = "KEY-" + "k" * 60
TOKEN = "TOKEN-" + "t" * 98
PRIORITY = [(LIVE_ID, LIVE), (IMD_SRI_ID, RADAR), (ECMWF_ID, FORECAST)]
REAL_PILOT_BUILT = all((config.DATA_PROCESSED / f).exists() for f in ("network.json", "terrain.npz", "scenarios.json"))


def _jwt(exp: float) -> str:
    b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{b64({'alg': 'HS256'})}.{b64({'exp': exp, 'sub': 'x'})}.c2ln"


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("IMD_API_KEY", KEY)
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN)
    monkeypatch.delenv("IMD_API_TOKEN_FILE", raising=False)
    list_providers()[LIVE_ID].clear_cache()
    yield
    list_providers()[LIVE_ID].clear_cache()


# ================================================================== token manager
def test_states_valid_expiring_expired_from_a_real_jwt_exp(monkeypatch, creds):
    now = [1_800_000_000.0]
    m = IMDTokenManager(clock=lambda: now[0])
    monkeypatch.setenv("IMD_API_TOKEN", _jwt(now[0] + 3600))
    s = m.status()
    assert (s.state, s.expiry_basis) == (VALID, "jwt_exp") and s.seconds_left == pytest.approx(3600)
    now[0] += 3600 - 300
    assert m.status().state == EXPIRING_SOON
    now[0] += 600
    assert m.status().state == EXPIRED and not m.usable()            # a real exp in the past: no request is sent


def test_opaque_token_expiry_is_estimated_and_never_blocks_a_request(monkeypatch, creds):
    now = [1_800_000_000.0]
    m = IMDTokenManager(clock=lambda: now[0])
    s = m.status()
    assert s.state == VALID and s.expiry_basis == "loaded_at+ttl"    # TTL is an assumption, reported as one
    now[0] += 61 * 60
    assert m.status().state == EXPIRED and "assumed" in m.status().detail
    assert m.usable()                                                # IMD, not our guess, is the authority


def test_unavailable_when_key_or_token_is_missing(monkeypatch, creds):
    monkeypatch.delenv("IMD_API_TOKEN")
    s = IMDTokenManager().status()
    assert s.state == UNAVAILABLE and "IMD_API_TOKEN" in s.detail and not IMDTokenManager().usable()
    monkeypatch.delenv("IMD_API_KEY")
    assert "IMD_API_KEY" in IMDTokenManager().status().detail


def test_rejected_token_stays_expired_until_the_value_changes(monkeypatch, creds):
    m = IMDTokenManager()
    m.report_auth_failure(m.token())
    assert m.status().state == EXPIRED and not m.usable()
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN + "-rotated")           # operator rotates it
    assert m.status().state == VALID and m.usable()


def test_token_file_hot_reload_without_restart(monkeypatch, creds, tmp_path):
    f = tmp_path / "imd_token"
    f.write_text("FILE-" + "a" * 60, encoding="utf-8")
    monkeypatch.setenv("IMD_API_TOKEN_FILE", str(f))
    m = IMDTokenManager()
    assert m.token() == "FILE-" + "a" * 60 and m.status().source == "token file"
    m.report_auth_failure(m.token())
    assert m.status().state == EXPIRED
    f.write_text("FILE-" + "b" * 60 + "\n", encoding="utf-8")         # rotate: overwrite the file, nothing else
    assert m.token() == "FILE-" + "b" * 60 and m.status().state == VALID


def test_dotenv_edit_is_a_hot_reload_when_the_env_value_came_from_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"IMD_API_KEY={KEY}\nIMD_API_TOKEN=first-token-{'x' * 30}\n", encoding="utf-8")
    monkeypatch.setenv("IMD_API_KEY", KEY)
    monkeypatch.setenv("IMD_API_TOKEN", f"first-token-{'x' * 30}")    # what config.py would have loaded at import
    m = IMDTokenManager(dotenv_path=env)
    assert m.token().startswith("first-token")
    env.write_text(f"IMD_API_KEY={KEY}\nIMD_API_TOKEN=second-token-{'y' * 30}\n", encoding="utf-8")
    assert m.token().startswith("second-token") and m.status().source == ".env"
    monkeypatch.setenv("IMD_API_TOKEN", "explicit-env-" + "z" * 30)   # an explicitly set variable always wins
    assert m.token().startswith("explicit-env")


def test_public_status_never_contains_a_credential_or_fingerprint(creds):
    m = IMDTokenManager()
    m.report_auth_failure(m.token())
    blob = json.dumps(m.status().to_public_dict())
    assert TOKEN not in blob and KEY not in blob and imd_auth.fingerprint(TOKEN) not in blob
    assert "credentials not configured" in json.loads(blob)["renewal_method"]   # no credentials in tests


# ================================================================== IMD failure matrix -> failover
class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code} for url https://api.imd.gov.in/x", request=None, response=None)

    def json(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


def _client(behaviour):
    class C:
        def __init__(self, **kw): self.headers = kw.get("headers", {})
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def get(self, url, params=None):
            if isinstance(behaviour, Exception):
                raise behaviour
            return behaviour
    return C


GOOD_ROW = [{"Station Id": "43003", "Station": "Mumbai-Santacruz", "Date of Observation": "2026-09-21",
             "Time": "6", "Last 24 hrs Rainfall": "12.0"}]
FAILURES = {
    "401 expired jwt": _Resp(401, {"error": "Invalid or expired JWT token"}),
    "401 auth header": _Resp(401, {"error": "Authorization header missing or invalid"}),
    "403 ip not authorized": _Resp(403, {"error": "IP address 203.0.113.9 not authorized"}),
    "403 invalid key": _Resp(403, {"error": "Invalid API key"}),
    "timeout": httpx.ReadTimeout("timed out"),
    "api unavailable": httpx.ConnectError("connection refused"),
    "http 500": _Resp(500, {"error": "server"}),
    "malformed payload": _Resp(200, {"unexpected": True}),
    "non-json body": _Resp(200, ValueError("not json")),
}


class _FakeProvider:
    def __init__(self, sid, source_type, ok=True, reason="unavailable in this test"):
        self.sid, self.source_type, self.ok, self.reason = sid, source_type, ok, reason

    def get(self, scenario_id=None, **kw):
        if not self.ok:
            raise ProviderUnavailable(self.reason)
        scen = replace(_demo_scenario(), id=self.sid, name=f"fake {self.sid}")
        return scen, RainfallSourceMeta(source_type=self.source_type, source_name=f"fake {self.sid}",
                                        timestamp="2026-09-21T06:00:00+00:00", forecast_horizon_min=180,
                                        resolution_min=5, data_mode="ESTIMATED", provenance=scen.provenance, detail={})


def _demo_scenario():
    from floodnet.data.fixtures import synthetic_pilot
    return next(s for s in synthetic_pilot()["scenarios"] if s.id == "cloudburst")


def _providers(radar_ok=True, ecmwf_ok=True):
    return {LIVE_ID: IMDObservationProvider(), IMD_SRI_ID: _FakeProvider(IMD_SRI_ID, "radar_image_derived", radar_ok),
            ECMWF_ID: _FakeProvider(ECMWF_ID, "ecmwf_forecast", ecmwf_ok)}


@pytest.mark.parametrize("name", list(FAILURES))
def test_every_imd_failure_falls_over_to_the_next_source_with_an_explicit_label(monkeypatch, creds, name):
    monkeypatch.setattr(httpx, "Client", _client(FAILURES[name]))
    scen, meta, status = SourceManager().resolve(_providers(), PRIORITY, None, 10800, _demo_scenario())
    assert status["label"] == RADAR and status["is_live"] is False and status["fell_back"] is True
    first = status["attempts"][0]
    assert first["source"] == LIVE_ID and first["ok"] is False and first["reason"]
    assert TOKEN not in json.dumps(status) and KEY not in json.dumps(status)     # no secret in any reason
    assert scen.id == "auto" and meta.detail["source_status"]["label"] == RADAR
    assert np.isfinite(scen.intensity_mm_h).all()                                 # a usable driver came back


def test_valid_token_gives_live(monkeypatch, creds):
    monkeypatch.setattr(httpx, "Client", _client(_Resp(200, GOOD_ROW)))
    _, meta, status = SourceManager().resolve(_providers(), PRIORITY, None, 10800, _demo_scenario())
    assert status["label"] == LIVE and status["is_live"] and not status["fell_back"]
    assert meta.detail["observed_rainfall_mm"] == 12.0


def test_expired_jwt_and_missing_token_fail_over_without_calling_imd(monkeypatch, creds):
    calls = []

    class Spy(_client(_Resp(200, GOOD_ROW))):
        def get(self, url, params=None):
            calls.append(url)
            return super().get(url, params)
    monkeypatch.setattr(httpx, "Client", Spy)
    monkeypatch.setenv("IMD_API_TOKEN", _jwt(time.time() - 60))
    _, _, status = SourceManager().resolve(_providers(), PRIORITY, None, 10800, _demo_scenario())
    assert status["label"] == RADAR and "token lifetime elapsed" in status["attempts"][0]["reason"] and calls == []
    monkeypatch.delenv("IMD_API_TOKEN")
    _, _, status = SourceManager().resolve(_providers(), PRIORITY, None, 10800, _demo_scenario())
    assert status["label"] == RADAR and "not available on the server" in status["attempts"][0]["reason"] and calls == []


def test_token_expiring_mid_session_keeps_the_dashboard_running_then_recovers_on_rotation(monkeypatch, creds):
    sm, providers = SourceManager(), _providers()
    monkeypatch.setattr(httpx, "Client", _client(_Resp(200, GOOD_ROW)))
    assert sm.resolve(providers, PRIORITY, None, 10800, _demo_scenario())[2]["label"] == LIVE
    providers[LIVE_ID].clear_cache()
    monkeypatch.setattr(httpx, "Client", _client(FAILURES["401 expired jwt"]))    # the hour is up
    assert sm.resolve(providers, PRIORITY, None, 10800, _demo_scenario())[2]["label"] == RADAR
    assert imd_auth.manager.status().state == EXPIRED
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN + "-new")                           # operator rotates the token
    monkeypatch.setattr(httpx, "Client", _client(_Resp(200, GOOD_ROW)))
    assert sm.resolve(providers, PRIORITY, None, 10800, _demo_scenario())[2]["label"] == LIVE


def test_priority_order_then_cache_then_demo_and_cache_is_never_relabelled_live(monkeypatch, creds):
    now = [1_800_000_000.0]
    sm = SourceManager(clock=lambda: now[0])
    monkeypatch.setattr(httpx, "Client", _client(FAILURES["timeout"]))
    assert sm.resolve(_providers(radar_ok=False), PRIORITY, None, 10800, _demo_scenario())[2]["label"] == FORECAST
    now[0] += 45 * 60
    scen, meta, st = sm.resolve(_providers(radar_ok=False, ecmwf_ok=False), PRIORITY, None, 10800, _demo_scenario())
    assert st["label"] == CACHED and st["is_live"] is False
    assert st["cached"] == {"original_label": FORECAST, "cached_at": st["cached"]["cached_at"], "age_min": 45.0}
    assert [a["ok"] for a in st["attempts"]] == [False, False, False, True]
    now[0] += 6 * 3600                                                            # too old to show at all
    scen, meta, st = sm.resolve(_providers(radar_ok=False, ecmwf_ok=False), PRIORITY, None, 10800, _demo_scenario())
    assert st["label"] == DEMO and meta.data_mode == "SYNTHETIC" and "DEMO fallback" in scen.provenance.note
    assert "older than 6 h" in st["attempts"][3]["reason"]
    h = sm.health()
    assert h["current"]["label"] == DEMO and h["sources"][LIVE_ID]["ok"] is False and h["cache"]["available"] is False


def test_last_good_field_survives_a_restart(monkeypatch, creds, tmp_path):
    monkeypatch.setattr(httpx, "Client", _client(_Resp(200, GOOD_ROW)))
    SourceManager(cache_dir=tmp_path).resolve(_providers(), PRIORITY, None, 10800, _demo_scenario())
    fresh = SourceManager(cache_dir=tmp_path)                                     # "restart"
    monkeypatch.setattr(httpx, "Client", _client(FAILURES["api unavailable"]))
    providers = _providers(radar_ok=False, ecmwf_ok=False)
    _, _, st = fresh.resolve(providers, PRIORITY, None, 10800, _demo_scenario())
    assert st["label"] == CACHED and st["cached"]["original_label"] == LIVE


def test_a_crashing_source_cannot_take_the_forecast_down(creds):
    class Boom:
        def get(self, *a, **k): raise RuntimeError("bug in a provider " + TOKEN)
    _, _, st = SourceManager().resolve({LIVE_ID: Boom(), IMD_SRI_ID: Boom(), ECMWF_ID: Boom()}, PRIORITY, None, 10800,
                                       _demo_scenario())
    assert st["label"] == DEMO and TOKEN not in json.dumps(st)


# ================================================================== through the API
@pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
def test_auto_run_completes_on_demo_when_every_live_source_is_down_and_says_so(monkeypatch, creds):
    monkeypatch.setattr(httpx, "Client", _client(FAILURES["api unavailable"]))
    providers = list_providers()
    for sid in (IMD_SRI_ID, ECMWF_ID):
        monkeypatch.setattr(providers[sid], "get", _FakeProvider(sid, "x", ok=False, reason="down").get)
    c = TestClient(app)
    r = c.post("/api/simulate", json={"scenario_id": "auto", "horizon_min": 15})
    assert r.status_code == 200, r.text
    b = r.json()
    st = b["provenance"]["rainfall_source"]["detail"]["source_status"]
    assert b["scenario_id"] == "auto" and st["label"] == DEMO and st["fell_back"] is True
    assert b["provenance"]["rainfall"]["tag"] == "SYNTHETIC" and b["n_frames"] == 4
    assert TOKEN not in r.text and KEY not in r.text
    d = c.get("/api/data-status").json()
    assert d["backend"]["ok"] and d["engine"]["ok"] and d["current_rainfall_source"]["label"] == DEMO
    assert d["imd_live"]["ok"] is False and d["priority"] == [LIVE, RADAR, FORECAST, CACHED, DEMO]
    assert TOKEN not in json.dumps(d) and KEY not in json.dumps(d)
    assert c.get("/health").json() == {"ok": True}


@pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
def test_golden_demo_scenario_is_deterministic():
    c = TestClient(app)
    a = c.post("/api/simulate", json={"scenario_id": "demo", "horizon_min": 30}).json()
    b = c.post("/api/simulate", json={"scenario_id": "demo", "horizon_min": 30}).json()
    assert a["summary"] == b["summary"] and a["mass_balance"]["rain_in_m3"] == b["mass_balance"]["rain_in_m3"]
    assert a["provenance"]["rainfall_source"]["detail"]["source_status"]["label"] == DEMO
    assert a["summary"]["peak_rain_mm_h"] > 0


# ================================================================== secrets: logs, errors, admin
def test_no_credential_reaches_logs_or_exception_text(monkeypatch, creds, caplog):
    caplog.set_level(logging.DEBUG)
    for name, behaviour in FAILURES.items():
        monkeypatch.setattr(httpx, "Client", _client(behaviour))
        list_providers()[LIVE_ID].clear_cache()
        imd_auth.manager.reset()
        with pytest.raises(ProviderUnavailable) as exc:
            IMDObservationProvider().get()
        assert TOKEN not in str(exc.value) and KEY not in str(exc.value), name
        assert "203.0.113.9" not in str(exc.value)                                # the caller's IP is not echoed
    assert TOKEN not in caplog.text and KEY not in caplog.text


def test_token_rotation_endpoint_is_off_by_default_protected_and_silent(monkeypatch, creds):
    c = TestClient(app)
    new = "ROTATED-" + "r" * 90
    monkeypatch.delenv("FLOODNET_ADMIN_TOKEN", raising=False)
    assert c.post("/api/admin/imd-token", json={"token": new}).status_code == 404          # feature off
    monkeypatch.setenv("FLOODNET_ADMIN_TOKEN", "admin-secret-value")
    assert c.post("/api/admin/imd-token", json={"token": new}).status_code == 403          # no header
    assert c.post("/api/admin/imd-token", json={"token": new}, headers={"X-Admin-Token": "wrong"}).status_code == 403
    ok = {"X-Admin-Token": "admin-secret-value"}
    assert c.post("/api/admin/imd-token", json={"token": "short"}, headers=ok).status_code == 422
    assert c.post("/api/admin/imd-token", content=b"not json", headers=ok).status_code == 422
    imd_auth.manager.report_auth_failure(imd_auth.manager.token())
    r = c.post("/api/admin/imd-token", json={"token": new}, headers=ok)
    assert r.status_code == 200 and r.json()["imd_auth"]["state"] == VALID
    assert new not in r.text and "admin-secret-value" not in r.text                        # never echoed
    assert imd_auth.manager.token() == new                                                 # live immediately
    assert "/api/admin/imd-token" not in json.dumps(c.get("/openapi.json").json())         # not advertised
