"""IMD token RENEWAL lifecycle: VALID -> EXPIRING_SOON -> REFRESH -> new token -> the IMD request continues.

What "renewal" means here, precisely: the token manager re-reads the configured server-side secret source
(file / .env / environment / AWS Secrets Manager / SSM), bypassing caches, and uses a newer token it finds there.
It never logs in to IMD: the portal issues tokens only after an email + password + CAPTCHA form login and
documents no refresh endpoint (docs/IMD_TOKEN_RENEWAL.md). These tests use a fake secret store whose value can be
rotated, and a fake IMD that accepts only the current token -- exactly the production shape.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from floodnet.rainfall import imd_auth
from floodnet.rainfall.imd_auth import EXPIRED, EXPIRING_SOON, UNAVAILABLE, VALID, IMDTokenManager
from floodnet.rainfall.imd_token_sources import CachedExternalSource
from floodnet.rainfall.provider import (ECMWF_ID, IMD_SRI_ID, LIVE_ID, IMDObservationProvider, ProviderUnavailable,
                                        RainfallSourceMeta)
from floodnet.rainfall.source_manager import FORECAST, LIVE, RADAR, SourceManager

KEY = "KEY-" + "k" * 60
ROW = [{"Station Id": "43003", "Station": "Mumbai-Santacruz", "Date of Observation": "2026-09-21",
        "Time": "6", "Last 24 hrs Rainfall": "7.2"}]


def imd_token(uid: int, exp: float, sig: str = "s") -> str:
    """Same format as the IMD portal's token: base64url(JSON {uid, exp}) + '.' + signature."""
    return base64.urlsafe_b64encode(json.dumps({"uid": uid, "exp": int(exp)}).encode()).decode().rstrip("=") + "." + sig * 64


class SecretStore:
    """Stand-in for AWS SSM / Secrets Manager / a mounted file: holds the current token; counts reads."""

    def __init__(self, value):
        self.value, self.reads, self._lock = value, 0, threading.Lock()

    def fetch(self):
        with self._lock:
            self.reads += 1
        time.sleep(0.05)                           # a real network round-trip, so concurrent callers overlap
        return self.value


class FakeIMD:
    """Accepts only tokens in `valid`; 401 for anything else. Records every token it was sent."""

    def __init__(self, valid):
        self.valid, self.seen, self._lock = set(valid), [], threading.Lock()

    def client(self):
        imd = self

        class C:
            def __init__(self, headers=None, **kw): self.headers = headers or {}
            def __enter__(self): return self
            def __exit__(self, *a): return False

            def get(self, url, params=None):
                tok = self.headers.get("Authorization", "").removeprefix("Bearer ")
                with imd._lock:
                    imd.seen.append(tok)
                ok = tok in imd.valid
                return _Resp(200 if ok else 401, ROW if ok else {"error": "Invalid or expired JWT token"})
        return C


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)

    def json(self):
        return self._p


@pytest.fixture
def world(monkeypatch):
    """A manager wired to a rotatable secret store (cache TTL 1 h, so only a RENEWAL can see a rotation early)."""
    now = time.time()
    old, new = imd_token(7, now + 3600, "a"), imd_token(7, now + 7200, "b")
    store = SecretStore(old)
    mgr = IMDTokenManager(source=CachedExternalSource("aws-ssm", store.fetch, ttl_s=3600))
    monkeypatch.setattr(imd_auth, "manager", mgr)
    monkeypatch.setenv("IMD_API_KEY", KEY)
    return {"mgr": mgr, "store": store, "old": old, "new": new}


def _provider():
    p = IMDObservationProvider()
    p.clear_cache()
    return p


# ---------------------------------------------------------------- 1. valid token -> IMD request succeeds
def test_valid_token_request_succeeds_without_any_renewal(world, monkeypatch):
    imd = FakeIMD({world["old"]})
    monkeypatch.setattr(httpx, "Client", imd.client())
    scen, meta = _provider().get()
    assert meta.detail["observed_rainfall_mm"] == 7.2
    assert imd.seen == [world["old"]] and world["store"].reads == 1
    s = world["mgr"].status()
    assert s.state == VALID and s.expiry_basis == "jwt_exp" and s.refresh_status == "idle"


# ---------------------------------------------------------------- 2. expiring token -> refresh ahead of expiry
def test_expiring_token_is_refreshed_before_it_lapses(world, monkeypatch):
    now = time.time()
    world["store"].value = expiring = imd_token(7, now + 120, "e")     # 2 min left
    world["mgr"]._source().invalidate()
    assert world["mgr"].status().state == EXPIRING_SOON
    world["store"].value = world["new"]                                 # a fresh token has been placed in the store
    imd = FakeIMD({expiring, world["new"]})
    monkeypatch.setattr(httpx, "Client", imd.client())
    _provider().get()
    assert imd.seen == [world["new"]]                                   # the NEW token was used, proactively
    s = world["mgr"].status()
    assert s.state == VALID and s.refresh_status == "renewed"


def test_expiring_token_with_nothing_newer_keeps_working(world, monkeypatch):
    world["store"].value = expiring = imd_token(7, time.time() + 120, "e")
    world["mgr"]._source().invalidate()
    imd = FakeIMD({expiring})
    monkeypatch.setattr(httpx, "Client", imd.client())
    _provider().get()
    assert imd.seen == [expiring] and world["mgr"].status().refresh_status == "idle"   # not reported as a failure


# ---------------------------------------------------------------- 3. 401 -> refresh -> retry once -> success
def test_401_triggers_renewal_and_one_retry_that_succeeds(world, monkeypatch):
    _provider().get.__self__  # noqa: B018 -- provider constructed; the token is read on the first request
    world["mgr"].token()                                                # store read once: holds OLD
    world["store"].value = world["new"]                                 # rotated in the store; cache still says OLD
    imd = FakeIMD({world["new"]})                                       # IMD now rejects OLD
    monkeypatch.setattr(httpx, "Client", imd.client())
    scen, meta = _provider().get()
    assert imd.seen == [world["old"], world["new"]]                     # original request, then exactly one retry
    assert meta.detail["observed_rainfall_mm"] == 7.2
    s = world["mgr"].status()
    assert s.state == VALID and s.refresh_status == "renewed" and s.last_rejected_at is None


# ---------------------------------------------------------------- 4. 401 -> refresh fails -> fallback
def test_401_with_no_newer_token_fails_then_auto_falls_back(world, monkeypatch):
    imd = FakeIMD(set())                                                # IMD rejects everything we have
    monkeypatch.setattr(httpx, "Client", imd.client())
    with pytest.raises(ProviderUnavailable, match="renewal found no newer token"):
        _provider().get()
    assert imd.seen == [world["old"]]                                   # no retry with the same rejected token
    s = world["mgr"].status()
    assert s.state == EXPIRED and s.refresh_status == "failed"

    class Ok:
        def __init__(self, sid, st): self.sid, self.st = sid, st
        def get(self, *a, **k):
            from floodnet.data.fixtures import synthetic_pilot
            scen = next(x for x in synthetic_pilot()["scenarios"] if x.id == "cloudburst")
            return scen, RainfallSourceMeta(self.st, "fake", "2026-09-21T06:00:00+00:00", 180, 5, "ESTIMATED", scen.provenance, {})
    from floodnet.data.fixtures import synthetic_pilot
    demo = next(x for x in synthetic_pilot()["scenarios"] if x.id == "cloudburst")
    _, _, status = SourceManager().resolve({LIVE_ID: _provider(), IMD_SRI_ID: Ok(IMD_SRI_ID, "radar_image_derived"),
                                            ECMWF_ID: Ok(ECMWF_ID, "ecmwf_forecast")},
                                           [(LIVE_ID, LIVE), (IMD_SRI_ID, RADAR), (ECMWF_ID, FORECAST)], None, 10800, demo)
    assert status["label"] == RADAR and status["attempts"][0]["ok"] is False   # fallback ONLY after renewal failed


# ---------------------------------------------------------------- 5. simultaneous 401s -> ONE refresh
def test_simultaneous_401s_share_one_renewal(world, monkeypatch):
    world["mgr"].token()                                                # warm: store read #1 (OLD)
    world["store"].value = world["new"]
    imd = FakeIMD({world["new"]})
    monkeypatch.setattr(httpx, "Client", imd.client())
    barrier = threading.Barrier(5)

    def one():
        p = _provider()
        barrier.wait()
        return p.get()[1].detail["observed_rainfall_mm"]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: one(), range(5)))
    assert results == [7.2] * 5                                         # every request completed on IMD LIVE
    assert world["store"].reads == 2                                    # 1 warm read + exactly ONE renewal read
    assert imd.seen.count(world["new"]) == 5                            # all five reused the single new token
    assert set(imd.seen) <= {world["old"], world["new"]}


# ---------------------------------------------------------------- 6. malformed -> rejected, never sent
def test_malformed_token_in_the_store_is_never_sent_or_accepted_as_a_renewal(world, monkeypatch):
    imd = FakeIMD(set())
    monkeypatch.setattr(httpx, "Client", imd.client())
    world["mgr"].token()
    world["store"].value = "broken token with spaces"
    with pytest.raises(ProviderUnavailable):
        _provider().get()
    assert "broken token with spaces" not in imd.seen
    world["mgr"]._source().invalidate()
    assert world["mgr"].status().state == UNAVAILABLE


# ---------------------------------------------------------------- 7. 403 -> access error, NOT a token refresh
def test_403_is_a_configuration_error_and_does_not_trigger_renewal(world, monkeypatch):
    world["mgr"].token()
    reads_before = world["store"].reads

    class Forbidden(FakeIMD):
        def client(self):
            base = super().client()

            class C(base):
                def get(self, url, params=None):
                    return _Resp(403, {"error": "IP address 203.0.113.9 not authorized"})
            return C
    monkeypatch.setattr(httpx, "Client", Forbidden(set()).client())
    with pytest.raises(ProviderUnavailable, match="IP address not authorized"):
        _provider().get()
    s = world["mgr"].status()
    assert world["store"].reads == reads_before                         # no renewal read happened
    assert s.state == UNAVAILABLE and s.rejected_by == "key_or_ip" and s.refresh_status == "idle"


# ---------------------------------------------------------------- the real IMD token format
def test_the_imd_portal_token_format_yields_an_exact_expiry():
    exp = 1_789_968_972                                                  # the shape observed on 2026-09-21
    tok = imd_token(12345, exp)
    assert imd_auth.jwt_expiry(tok) == exp
    assert imd_auth.jwt_expiry("not.a.token.at.all") is None
    assert imd_auth.jwt_expiry("x" * 104) is None


def test_no_credential_reaches_the_public_status(world, monkeypatch):
    monkeypatch.setattr(httpx, "Client", FakeIMD({world["new"]}).client())
    world["mgr"].token()
    world["store"].value = world["new"]
    _provider().get()
    blob = json.dumps(world["mgr"].status().to_public_dict())
    for secret in (world["old"], world["new"], KEY, imd_auth.fingerprint(world["new"])):
        assert secret not in blob
