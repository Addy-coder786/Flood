"""Automatic IMD token renewal through IMD's token endpoint (POST /api/oauth/token.php, JSON {email, password}).

A fake token endpoint and a fake IMD data API stand in for the real ones; the real endpoint is exercised only by the
opt-in live test in test_imd_live_smoke.py. Nothing here uses real credentials.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from floodnet.rainfall import imd_auth
from floodnet.rainfall.imd_auth import EXPIRED, EXPIRING_SOON, UNAVAILABLE, VALID, IMDTokenManager
from floodnet.rainfall.imd_token_issuer import DEFAULT_TOKEN_URL, IMDTokenIssuer
from floodnet.rainfall.imd_token_sources import CachedExternalSource
from floodnet.rainfall.provider import IMDObservationProvider, ProviderUnavailable

from .test_imd_token_renewal import KEY, ROW, FakeIMD, _Resp, imd_token

EMAIL, PASSWORD = "observer@example.invalid", "correct-horse-battery-staple"


class FakeTokenEndpoint:
    """Stand-in for IMD's token endpoint. Hands out a fresh token per call; records every request body."""

    def __init__(self, status=200, lifetime_s=3600, delay_s=0.0, error="Invalid credentials"):
        self.status, self.lifetime_s, self.delay_s, self.error = status, lifetime_s, delay_s, error
        self.bodies, self.urls, self.issued = [], [], []
        self._lock = threading.Lock()

    def post(self, url, body):
        with self._lock:
            self.urls.append(url)
            self.bodies.append(dict(body))
            n = len(self.bodies)
        time.sleep(self.delay_s)
        if self.status != 200:
            return self.status, {"error": self.error}
        tok = imd_token(7, time.time() + self.lifetime_s, chr(ord("m") + n % 10))
        with self._lock:
            self.issued.append(tok)
        return 200, {"access_token": tok, "token_type": "Bearer", "expires_in": self.lifetime_s}


class NoSource(CachedExternalSource):
    def __init__(self, value=None):
        self.value = value
        super().__init__("aws-ssm", lambda: self.value, ttl_s=3600)


def _world(monkeypatch, endpoint, source_value=None):
    issuer = IMDTokenIssuer(credentials=lambda: (EMAIL, PASSWORD), http_post=endpoint.post)
    src = NoSource(source_value)
    mgr = IMDTokenManager(source=src, issuer=issuer)
    monkeypatch.setattr(imd_auth, "manager", mgr)
    monkeypatch.setenv("IMD_API_KEY", KEY)
    return mgr, issuer, src


def _provider():
    p = IMDObservationProvider()
    p.clear_cache()
    return p


class IssuedTokensOnly(FakeIMD):
    """IMD data API that accepts exactly the tokens the fake token endpoint has issued."""

    def __init__(self, endpoint, reject_all=False):
        super().__init__(set())
        self.endpoint, self.reject_all = endpoint, reject_all

    def client(self):
        base, imd = super().client(), self

        class C(base):
            def get(self, url, params=None):
                tok = self.headers.get("Authorization", "").removeprefix("Bearer ")
                with imd._lock:
                    imd.seen.append(tok)
                ok = not imd.reject_all and tok in imd.endpoint.issued
                return _Resp(200 if ok else 401, ROW if ok else {"error": "Invalid or expired JWT token"})
        return C


# ---------------------------------------------------------------- 1. token generation success
def test_token_generation_success_sends_json_credentials_and_serves_live_data(monkeypatch):
    ep = FakeTokenEndpoint()
    mgr, issuer, _ = _world(monkeypatch, ep)                       # no token anywhere yet
    imd = IssuedTokensOnly(ep)
    monkeypatch.setattr(httpx, "Client", imd.client())
    scen, meta = _provider().get()
    assert meta.detail["observed_rainfall_mm"] == 7.2
    assert ep.urls == [DEFAULT_TOKEN_URL] and ep.bodies == [{"email": EMAIL, "password": PASSWORD}]
    assert imd.seen == ep.issued                                   # the generated token was used
    s = mgr.status()
    assert s.state == VALID and s.source == imd_auth.ISSUED_SOURCE and s.refresh_status == "renewed"
    assert s.auto_renewal is True and "token endpoint" in s.to_public_dict()["renewal_method"]


# ---------------------------------------------------------------- 2. token generation failure -> fallback, backoff
def test_token_generation_failure_is_reported_and_backs_off(monkeypatch):
    ep = FakeTokenEndpoint(status=401)
    mgr, issuer, _ = _world(monkeypatch, ep)
    monkeypatch.setattr(httpx, "Client", IssuedTokensOnly(ep).client())
    with pytest.raises(ProviderUnavailable, match="refused the account credentials"):
        _provider().get()
    s = mgr.status()
    assert s.state == UNAVAILABLE and s.refresh_status == "failed"
    with pytest.raises(ProviderUnavailable):
        _provider().get()
    assert issuer.requests == 1                                    # a wrong password is not retried at once


def test_token_endpoint_200_without_access_token_is_a_failure(monkeypatch):
    mgr, issuer, _ = _world(monkeypatch, FakeTokenEndpoint())
    issuer._post = lambda url, body: (200, {"message": "ok"})
    assert mgr.token_for_request() is None
    assert mgr.status().refresh_status == "failed"


# ---------------------------------------------------------------- 3. expiry-triggered renewal (< 10 min left)
def test_token_near_expiry_is_regenerated_before_it_lapses(monkeypatch):
    ep = FakeTokenEndpoint()
    old = imd_token(7, time.time() + 300, "o")                    # 5 min left
    mgr, issuer, _ = _world(monkeypatch, ep, source_value=old)
    assert mgr.status().state == EXPIRING_SOON
    imd = IssuedTokensOnly(ep)
    monkeypatch.setattr(httpx, "Client", imd.client())
    _provider().get()
    assert issuer.requests == 1 and imd.seen == ep.issued          # the old token was never sent
    assert mgr.status().state == VALID


def test_expiry_from_expires_in_drives_renewal_for_an_opaque_token(monkeypatch):
    now = [1_000_000.0]
    opaque = iter(["opaque-token-number-one-xxxxxxxx", "opaque-token-number-two-xxxxxxxx"])
    issuer = IMDTokenIssuer(credentials=lambda: (EMAIL, PASSWORD),
                            http_post=lambda u, b: (200, {"access_token": next(opaque), "expires_in": 3600}))
    mgr = IMDTokenManager(source=NoSource(), issuer=issuer, clock=lambda: now[0])
    monkeypatch.setenv("IMD_API_KEY", KEY)
    assert mgr.token_for_request() == "opaque-token-number-one-xxxxxxxx"
    s = mgr.status()
    assert s.expiry_basis == "issuer_expires_in" and s.state == VALID
    now[0] += 3600 - 300                                           # 5 min before the stated expiry
    assert mgr.status().state == EXPIRING_SOON
    assert mgr.token_for_request() == "opaque-token-number-two-xxxxxxxx" and issuer.requests == 2


# ---------------------------------------------------------------- 4 + 5. 401 -> one generation -> one retry -> success
def test_401_triggers_one_generation_and_one_successful_retry(monkeypatch):
    ep = FakeTokenEndpoint()
    stale = imd_token(7, time.time() + 1800, "z")                 # looks valid, but IMD no longer accepts it
    mgr, issuer, _ = _world(monkeypatch, ep, source_value=stale)
    imd = IssuedTokensOnly(ep)
    monkeypatch.setattr(httpx, "Client", imd.client())
    scen, meta = _provider().get()
    assert imd.seen == [stale, ep.issued[0]]                       # original request, then exactly one retry
    assert issuer.requests == 1 and meta.detail["observed_rainfall_mm"] == 7.2
    assert mgr.status().state == VALID and mgr.status().refresh_status == "renewed"


# ---------------------------------------------------------------- 6. retry failure -> no second retry, fallback
def test_retry_failure_stops_after_one_retry(monkeypatch):
    ep = FakeTokenEndpoint()
    stale = imd_token(7, time.time() + 1800, "z")
    mgr, issuer, _ = _world(monkeypatch, ep, source_value=stale)
    imd = IssuedTokensOnly(ep, reject_all=True)
    monkeypatch.setattr(httpx, "Client", imd.client())
    with pytest.raises(ProviderUnavailable, match="401"):
        _provider().get()
    assert len(imd.seen) == 2 and issuer.requests == 1             # one generation, one retry, then give up
    assert mgr.status().state == EXPIRED                           # the generated token was rejected too


# ---------------------------------------------------------------- 7. concurrent renewals -> single flight
def test_simultaneous_401s_share_one_token_generation(monkeypatch):
    ep = FakeTokenEndpoint(delay_s=0.05)
    stale = imd_token(7, time.time() + 1800, "z")
    mgr, issuer, _ = _world(monkeypatch, ep, source_value=stale)
    monkeypatch.setattr(httpx, "Client", IssuedTokensOnly(ep).client())
    barrier = threading.Barrier(6)

    def one():
        p = _provider()
        barrier.wait()
        return p.get()[1].detail["observed_rainfall_mm"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: one(), range(6))) == [7.2] * 6
    assert issuer.requests == 1


def test_simultaneous_failures_also_share_one_generation(monkeypatch):
    ep = FakeTokenEndpoint(status=500, delay_s=0.05)
    mgr, issuer, _ = _world(monkeypatch, ep)
    barrier = threading.Barrier(6)

    def one():
        barrier.wait()
        return mgr.token_for_request()
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: one(), range(6))) == [None] * 6
    assert issuer.requests == 1                                    # no storm against IMD on failure either


# ---------------------------------------------------------------- 8. 403 never renews
def test_403_does_not_trigger_token_generation(monkeypatch):
    ep = FakeTokenEndpoint()
    valid = imd_token(7, time.time() + 1800, "v")
    mgr, issuer, _ = _world(monkeypatch, ep, source_value=valid)

    class Forbidden(FakeIMD):
        def client(self):
            base = super().client()

            class C(base):
                def get(self, url, params=None):
                    return _Resp(403, {"error": "IP address 203.0.113.9 not authorized"})
            return C
    monkeypatch.setattr(httpx, "Client", Forbidden(set()).client())
    with pytest.raises(ProviderUnavailable):
        _provider().get()
    assert issuer.requests == 0
    s = mgr.status()
    assert s.state == UNAVAILABLE and s.rejected_by == "key_or_ip"


# ---------------------------------------------------------------- AWS-held token stays a working fallback
def test_configured_token_source_is_used_when_generation_fails(monkeypatch):
    ep = FakeTokenEndpoint(status=503)
    stale, rotated = imd_token(7, time.time() + 1800, "z"), imd_token(7, time.time() + 3600, "r")
    mgr, issuer, src = _world(monkeypatch, ep, source_value=stale)
    mgr.token()
    src.value = rotated                                            # an operator rotated the token in SSM
    imd = FakeIMD({rotated})
    monkeypatch.setattr(httpx, "Client", imd.client())
    _provider().get()
    assert imd.seen == [stale, rotated] and issuer.requests == 1


def test_credentials_can_come_from_aws_secrets_manager(monkeypatch):
    from floodnet.rainfall import imd_token_issuer as iss
    monkeypatch.setenv("IMD_CREDENTIALS_PROVIDER", "aws-secretsmanager")
    monkeypatch.setenv("IMD_CREDENTIALS_SECRET_ID", "floodnet/imd")
    monkeypatch.setitem(iss.CREDENTIAL_PROVIDERS, "aws-secretsmanager", lambda: (EMAIL, PASSWORD))
    ep = FakeTokenEndpoint()
    issuer = IMDTokenIssuer(http_post=ep.post)
    assert issuer.configured() and issuer.issue().value == ep.issued[0]
    assert ep.bodies == [{"email": EMAIL, "password": PASSWORD}]


# ---------------------------------------------------------------- 9. no credential leakage
def test_no_email_password_token_or_header_leaks(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    ep = FakeTokenEndpoint(status=401, error=f"Invalid credentials for {EMAIL}")   # IMD echoing the email
    mgr, issuer, _ = _world(monkeypatch, ep)
    monkeypatch.setattr(httpx, "Client", IssuedTokensOnly(ep).client())
    with pytest.raises(ProviderUnavailable) as err:
        _provider().get()
    ok_ep = FakeTokenEndpoint()
    mgr2, _, _ = _world(monkeypatch, ok_ep)
    monkeypatch.setattr(httpx, "Client", IssuedTokensOnly(ok_ep).client())
    _provider().get()
    from floodnet.api.main import data_status as data_status_endpoint
    data_status = json.dumps(data_status_endpoint(), default=str)
    blobs = [str(err.value), caplog.text, json.dumps(mgr.status().to_public_dict()),
             json.dumps(mgr2.status().to_public_dict()), data_status]
    for blob in blobs:
        for secret in (EMAIL, PASSWORD, ok_ep.issued[0], "Bearer ", KEY):
            assert secret not in blob


# ---------------------------------------------------------------- RENEWING state + non-blocking pre-warm
def test_status_reports_renewing_while_a_generation_is_in_flight(monkeypatch):
    ep = FakeTokenEndpoint(delay_s=0.3)
    mgr, issuer, _ = _world(monkeypatch, ep)
    t = threading.Thread(target=mgr.token_for_request)
    t.start()
    time.sleep(0.1)
    assert mgr.status().state == imd_auth.RENEWING and mgr.status().refresh_status == "refreshing"
    t.join()
    assert mgr.status().state == VALID and issuer.requests == 1


def test_background_prewarm_does_not_block_and_survives_an_unreachable_imd(monkeypatch):
    def down(url, body):
        raise httpx.ConnectError("unreachable")
    issuer = IMDTokenIssuer(credentials=lambda: (EMAIL, PASSWORD), http_post=down)
    mgr = IMDTokenManager(source=NoSource(), issuer=issuer)
    monkeypatch.setenv("IMD_API_KEY", KEY)
    t0 = time.monotonic()
    mgr.start_keeper(interval_s=3600)
    assert time.monotonic() - t0 < 0.5                              # start-up is never held up by IMD
    deadline = time.monotonic() + 10
    while mgr.status().refresh_status != "failed" and time.monotonic() < deadline:
        time.sleep(0.02)                                           # wait for the renewal to finish, not just start
    assert issuer.requests == 1 and mgr._keeper.is_alive() and mgr.status().refresh_status == "failed"


def test_background_prewarm_obtains_a_token_before_any_request(monkeypatch):
    ep = FakeTokenEndpoint()
    mgr, issuer, _ = _world(monkeypatch, ep)
    mgr.start_keeper(interval_s=3600)
    deadline = time.monotonic() + 10
    while mgr.status().state != VALID and time.monotonic() < deadline:
        time.sleep(0.02)
    assert mgr.token() == ep.issued[0] and mgr.status().state == VALID

