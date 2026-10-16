"""IMD token resilience: pluggable token sources (env / file / external secret store), 403 vs 401, malformed tokens,
upper-case states, the safe active-source metadata and the data-status fields. Complements
test_imd_token_and_failover.py (failure matrix + failover). No credential is needed; all upstream calls are mocked.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from floodnet import config
from floodnet.api.main import app
from floodnet.rainfall import imd_auth
from floodnet.rainfall.imd_auth import EXPIRED, EXPIRING_SOON, UNAVAILABLE, VALID, IMDTokenManager
from floodnet.rainfall.imd_token_sources import (CachedExternalSource, ChainSource, EnvTokenSource, FileTokenSource,
                                                 build_source)
from floodnet.rainfall.provider import LIVE_ID, IMDObservationProvider, ProviderUnavailable, list_providers
from floodnet.rainfall.source_manager import active_source_metadata

KEY = "KEY-" + "k" * 60
TOKEN = "TOKEN-" + "t" * 98
REAL_PILOT_BUILT = all((config.DATA_PROCESSED / f).exists() for f in ("network.json", "terrain.npz", "scenarios.json"))


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("IMD_API_KEY", KEY)
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN)
    for n in ("IMD_API_TOKEN_FILE", "IMD_TOKEN_PROVIDER"):
        monkeypatch.delenv(n, raising=False)
    list_providers()[LIVE_ID].clear_cache()
    yield
    list_providers()[LIVE_ID].clear_cache()


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)

    def json(self):
        return self._p


def _client(resp):
    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None): return resp
    return C


def test_states_are_the_four_upper_case_values():
    assert (VALID, EXPIRING_SOON, EXPIRED, UNAVAILABLE) == ("VALID", "EXPIRING_SOON", "EXPIRED", "UNAVAILABLE")


# ------------------------------------------------------------------ token sources
def test_file_source_is_hot_reloaded_and_env_is_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("IMD_API_TOKEN", "ENV-" + "e" * 40)
    f = tmp_path / "tok"
    chain = ChainSource([FileTokenSource(str(f)), EnvTokenSource(None)])
    assert chain.read().source == "environment"                       # no file yet
    f.write_text("FILE-" + "1" * 40)
    assert chain.read().value == "FILE-" + "1" * 40 and chain.read().source == "token file"
    f.write_text("FILE-" + "2" * 40 + "\n")                           # rotate while running
    assert chain.read().value == "FILE-" + "2" * 40


def test_external_secret_source_is_cached_and_survives_store_outages():
    now = [1000.0]
    values = iter(["SECRET-" + "a" * 40, RuntimeError("secrets manager down"), "SECRET-" + "b" * 40])
    calls = []

    def fetch():
        calls.append(1)
        v = next(values)
        if isinstance(v, Exception):
            raise v
        return v
    src = CachedExternalSource("aws-ssm", fetch, ttl_s=60, clock=lambda: now[0])
    assert src.read().value.endswith("a" * 40) and src.read().source == "aws-ssm"
    assert len(calls) == 1                                            # cached inside the TTL
    now[0] += 61
    assert src.read() is None                                         # store down -> no token, no exception
    now[0] += 61
    assert src.read().value.endswith("b" * 40)                        # rotation picked up after the TTL


def test_provider_selection_is_configuration_not_code(monkeypatch):
    monkeypatch.setenv("IMD_TOKEN_PROVIDER", "aws-secretsmanager")
    assert isinstance(build_source(None), CachedExternalSource) and build_source(None).name == "aws-secretsmanager"
    monkeypatch.setenv("IMD_TOKEN_PROVIDER", "aws-ssm")
    assert build_source(None).name == "aws-ssm"
    monkeypatch.setenv("IMD_TOKEN_PROVIDER", "file")
    assert isinstance(build_source(None), FileTokenSource)
    monkeypatch.setenv("IMD_TOKEN_PROVIDER", "env")
    assert isinstance(build_source(None), ChainSource)


def test_manager_uses_an_injected_external_source_and_hot_reloads_it(monkeypatch):
    monkeypatch.setenv("IMD_API_KEY", KEY)
    holder = {"v": "EXT-" + "1" * 40}
    now = [1000.0]
    src = CachedExternalSource("aws-secretsmanager", lambda: holder["v"], ttl_s=0, clock=lambda: now[0])
    m = IMDTokenManager(source=src, clock=lambda: now[0])
    assert m.token() == holder["v"] and m.status().source == "aws-secretsmanager" and m.status().state == VALID
    m.report_auth_failure(m.token(), 401)
    assert m.status().state == EXPIRED
    holder["v"] = "EXT-" + "2" * 40                                   # rotated in the secret store
    now[0] += 1
    assert m.token() == holder["v"] and m.status().state == VALID and m.usable()


def test_missing_secret_in_the_store_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setenv("IMD_API_KEY", KEY)
    m = IMDTokenManager(source=CachedExternalSource("aws-ssm", lambda: None))
    assert m.status().state == UNAVAILABLE and not m.usable() and m.token() is None


# ------------------------------------------------------------------ malformed tokens, 401 vs 403
@pytest.mark.parametrize("bad", ["short", "has space in the middle " + "x" * 30, "ctrl\x07" + "x" * 30, "x" * 9000])
def test_malformed_token_is_unavailable_and_never_sent(monkeypatch, creds, bad):
    monkeypatch.setenv("IMD_API_TOKEN", bad)
    calls = []

    class Spy(_client(_Resp(200, []))):
        def get(self, url, params=None):
            calls.append(url)
            return super().get(url, params)
    monkeypatch.setattr(httpx, "Client", Spy)
    s = imd_auth.manager.status()
    assert s.state == UNAVAILABLE and "not sent" in s.detail
    with pytest.raises(ProviderUnavailable):
        IMDObservationProvider().get()
    assert calls == []
    assert bad not in json.dumps(s.to_public_dict())


def test_403_blames_the_key_or_ip_and_a_new_token_does_not_clear_it(monkeypatch, creds):
    monkeypatch.setattr(httpx, "Client", _client(_Resp(403, {"error": "IP address 203.0.113.9 not authorized"})))
    with pytest.raises(ProviderUnavailable):
        IMDObservationProvider().get()
    s = imd_auth.manager.status()
    assert s.state == UNAVAILABLE and s.rejected_by == "key_or_ip" and "403" in s.detail
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN + "-new")                 # rotating the token is not the fix ...
    assert imd_auth.manager.status().state == UNAVAILABLE
    monkeypatch.setenv("IMD_API_KEY", KEY + "-reissued")                # ... a key for the right IP is
    assert imd_auth.manager.status().state == VALID


def test_401_blames_the_token_and_rotation_clears_it(monkeypatch, creds):
    monkeypatch.setattr(httpx, "Client", _client(_Resp(401, {"error": "Invalid or expired JWT token"})))
    with pytest.raises(ProviderUnavailable):
        IMDObservationProvider().get()
    s = imd_auth.manager.status()
    assert s.state == EXPIRED and s.rejected_by == "token"
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN + "-rotated")
    assert imd_auth.manager.status().state == VALID


# ------------------------------------------------------------------ safe metadata
def test_active_source_metadata_has_exactly_the_safe_fields():
    live = {"source_type": "live_observation", "source_name": "IMD Mumbai-Santacruz",
            "timestamp": "2026-09-21T06:00:00+00:00",
            "detail": {"source_status": {"label": "LIVE", "fell_back": False}, "station": "x"}}
    m = active_source_metadata(live, now=1_790_000_000.0)
    assert set(m) == {"source", "source_label", "timestamp", "age_seconds", "status", "fallback_active"}
    assert m["source_label"] == "IMD LIVE" and m["status"] == "ok" and m["fallback_active"] is False
    fell = {**live, "source_type": "ecmwf_forecast", "detail": {"source_status": {"label": "FORECAST", "fell_back": True}}}
    assert active_source_metadata(fell)["source_label"] == "ECMWF NWP"
    assert active_source_metadata(fell)["status"] == "fallback"
    cached = {**live, "detail": {"source_status": {"label": "CACHED", "fell_back": True,
                                                   "cached": {"cached_at": "2026-09-21T05:00:00+00:00"}}}}
    c = active_source_metadata(cached, now=1_790_000_000.0)
    assert c["source_label"] == "CACHED" and c["status"] == "cached" and c["timestamp"].startswith("2026-09-21T05")
    radar = {"source_type": "radar_image_derived", "source_name": "IMD Mumbai-Veravali DWR", "timestamp": "bad"}
    r = active_source_metadata(radar)
    assert r["source_label"] == "IMD DWR RADAR-DERIVED" and r["timestamp"] is None and r["age_seconds"] is None
    assert active_source_metadata(None) is None


@pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
def test_data_status_reports_auth_expiry_and_the_active_fallback_without_credentials(monkeypatch, creds):
    monkeypatch.setattr(httpx, "Client", _client(_Resp(401, {"error": "Invalid or expired JWT token"})))
    providers = list_providers()
    for sid in ("imd_sri", "ecmwf"):
        def down(*a, **k):
            raise ProviderUnavailable("down in this test")
        monkeypatch.setattr(providers[sid], "get", down)
    c = TestClient(app)
    run = c.post("/api/simulate", json={"scenario_id": "auto", "horizon_min": 15})
    assert run.status_code == 200                                     # the expired JWT did not crash the forecast
    assert run.json()["active_source"]["source_label"] == "DEMO" and run.json()["active_source"]["status"] == "demo"
    d = c.get("/api/data-status").json()
    assert d["imd_auth_status"] == EXPIRED
    assert d["active_rainfall_source"] == "DEMO" and d["fallback_active"] is True
    assert "imd_token_expires_at" in d and "active_source_timestamp" in d
    blob = json.dumps(d) + run.text
    for secret in (TOKEN, KEY, imd_auth.fingerprint(TOKEN), imd_auth.fingerprint(KEY)):
        assert secret not in blob
    assert "Bearer" not in blob


@pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
def test_status_endpoint_does_not_offer_imd_live_when_the_token_cannot_answer(monkeypatch, creds):
    imd_auth.manager.report_auth_failure(imd_auth.manager.token(), 401)
    row = next(p for p in TestClient(app).get("/api/status").json()["rainfall_providers"] if p["id"] == LIVE_ID)
    assert row["available"] is False and row["auth_state"] == EXPIRED and TOKEN not in json.dumps(row)
