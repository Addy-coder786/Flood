"""Server-side IMD credential + token-state manager.

THE PROBLEM: api.imd.gov.in needs an IP-bound key (X-API-Key) AND a short-lived bearer token (about 1 h).

RENEWAL: when IMD account credentials are configured (IMD_EMAIL / IMD_PASSWORD, or a credential store; see
imd_token_issuer.py) a new token is generated from IMD's token endpoint `POST /api/oauth/token.php`
  * before the current one expires (under 10 min left), and
  * after an IMD 401, followed by exactly one retry of the original request (provider.py).
Renewal is single-flight: concurrent callers share one in-flight renewal and its outcome. After a failed
generation, new attempts back off for a while (15 min if the credentials were refused), so a wrong password
cannot hammer the account. Without credentials, renewal falls back to re-reading the configured token source.
This module also:

  * reads the token from a pluggable source (imd_token_sources.py: environment / .env / file / AWS Secrets Manager
    / SSM), re-asked on every request, so a rotated token is used with no restart and no frontend rebuild;
  * works out expiry where knowable: a JWT's `exp` claim, else (time the value dates from) + IMD_TOKEN_TTL_MIN
    (default 60 -- an ASSUMPTION, reported as such);
  * remembers what IMD itself said: a 401 marks the CURRENT token as rejected; a 403 marks the current KEY / server
    IP as rejected (rotating the token would not help, so the state says what to fix);
  * refuses malformed tokens (whitespace, control characters, implausible length) without sending them;
  * reports one of four states -- VALID | EXPIRING_SOON | EXPIRED | UNAVAILABLE -- plus how it knows.

Nothing here ever returns, logs or raises a credential value; `fingerprint()` is a short SHA-256 prefix used only
in-process for "did it change?" comparisons and is never serialised.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import config
from .imd_token_issuer import IMDTokenIssuer, TokenIssueError
from .imd_token_sources import TokenReading, TokenSource, _read_dotenv_value, build_source

log = logging.getLogger(__name__)

KEY_ENV, TOKEN_ENV, TTL_ENV = "IMD_API_KEY", "IMD_API_TOKEN", "IMD_TOKEN_TTL_MIN"
DEFAULT_TTL_MIN = 60.0
EXPIRING_SOON_S = 10 * 60
ISSUED_SOURCE = "IMD token endpoint (auto-renewed)"
ISSUE_BACKOFF_S = {"credentials_rejected": 15 * 60, "not_configured": 15 * 60}   # others: 60 s
NO_NEWER = "Token renewal found no newer token in the configured source."
MIN_TOKEN_LEN, MAX_TOKEN_LEN = 20, 8192

VALID, EXPIRING_SOON, EXPIRED, UNAVAILABLE = "VALID", "EXPIRING_SOON", "EXPIRED", "UNAVAILABLE"
RENEWING = "RENEWING"                   # a renewal is in flight right now (reported while it runs)


def fingerprint(secret: Optional[str]) -> Optional[str]:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:10] if secret else None


def _b64_json(part: str) -> Optional[dict]:
    try:
        obj = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        return None


def jwt_expiry(token: str) -> Optional[float]:
    """`exp` (epoch seconds) read from the token itself, or None. Two formats are understood:
      * a standard JWT  header.payload.signature;
      * the IMD API portal's own token (observed 2026-09-21): base64url(JSON {"uid": <int>, "exp": <epoch>})
        + "." + a 48-byte base64url signature -- no iat / iss / aud / refresh claim.
    The signature is never checked: this is scheduling, not authentication."""
    parts = token.split(".")
    payload = _b64_json(parts[1]) if len(parts) == 3 else _b64_json(parts[0]) if len(parts) == 2 else None
    exp = payload.get("exp") if payload else None
    return float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None


def malformed_reason(token: str) -> Optional[str]:
    if not (MIN_TOKEN_LEN <= len(token) <= MAX_TOKEN_LEN):
        return "token has an implausible length"
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token):
        return "token contains whitespace or control characters"
    return None


@dataclass
class TokenStatus:
    state: str
    detail: str
    source: Optional[str]
    expires_at: Optional[float]
    expiry_basis: str                       # "jwt_exp" | "loaded_at+ttl" | "unknown"
    seconds_left: Optional[float]
    key_configured: bool
    last_ok_at: Optional[float]
    last_rejected_at: Optional[float]
    rejected_by: Optional[str] = None       # "token" (401) | "key_or_ip" (403) | None

    refresh_status: str = "idle"            # idle | refreshing | renewed | failed
    last_refresh_at: Optional[float] = None
    auto_renewal: bool = False              # IMD account credentials configured -> tokens generated automatically

    def to_public_dict(self) -> dict:
        """Safe for an API response: states and times only, never a credential or a fingerprint."""
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None  # noqa: E731
        return {"state": self.state, "detail": self.detail, "token_source": self.source,
                "key_configured": self.key_configured, "expires_at": iso(self.expires_at),
                "expiry_basis": self.expiry_basis,
                "minutes_left": None if self.seconds_left is None else round(self.seconds_left / 60.0, 1),
                "last_accepted_at": iso(self.last_ok_at), "last_rejected_at": iso(self.last_rejected_at),
                "rejected_by": self.rejected_by, "refresh_status": self.refresh_status,
                "last_refresh_at": iso(self.last_refresh_at),
                "auto_renewal": self.auto_renewal,
                "renewal_method": "IMD token endpoint with the account credentials" if self.auto_renewal else
                "reload from the configured secret source (IMD account credentials not configured)"}


class IMDTokenManager:
    def __init__(self, dotenv_path: Optional[Path] = None, clock=time.time, source: Optional[TokenSource] = None,
                 issuer: Optional[IMDTokenIssuer] = None):
        self._dotenv = dotenv_path if dotenv_path is not None else config.REPO_DIR / ".env"
        # .env's IMD_API_KEY as config.py copied it into the environment at start-up (see api_key())
        self._key_dotenv_at_start = _read_dotenv_value(self._dotenv, KEY_ENV) if self._dotenv else None
        self._clock = clock
        self._lock = threading.Lock()
        self._explicit_source = source
        self._runtime: Optional[TokenReading] = None
        self._first_seen: dict[str, float] = {}         # token fingerprint -> first observed
        self._rejected: dict[str, float] = {}            # token fingerprint -> IMD 401 time
        self._rejected_keys: dict[str, float] = {}       # key fingerprint -> IMD 403 time
        self._last_ok_at: Optional[float] = None
        # single-flight renewal: one renewal at a time; concurrent callers wait for and reuse its result
        self._renew_lock = threading.Lock()
        self._refresh_status = "idle"
        self._last_refresh_at: Optional[float] = None
        self._refresh_detail: Optional[str] = None
        self._renew_gen = 0                              # completed renewals; lets waiters reuse an outcome
        self._last_renew_result: Optional[str] = None
        self._issuer = issuer if issuer is not None else IMDTokenIssuer()
        self._issued: Optional[TokenReading] = None      # token generated from the account credentials
        self._issue_blocked_until = 0.0
        self._keeper: Optional[threading.Thread] = None

    def auto_renewal(self) -> bool:
        return self._issuer.configured()

    # ------------------------------------------------------------------ renewal
    def renew(self, rejected: Optional[str], proactive: bool = False) -> Optional[str]:
        """Obtain a token different from `rejected`, or None.

        Single-flight: one renewal runs at a time. A caller that arrives while one is running waits and then REUSES
        its outcome (success or failure) instead of starting another, so N simultaneous 401s cost one renewal.

        Order: (1) generate a new token from IMD's token endpoint with the account credentials, if configured;
        (2) otherwise, or if that failed, re-read the configured token source bypassing its cache."""
        with self._lock:
            gen = self._renew_gen
        with self._renew_lock:                  # later callers block here until the running renewal finishes
            with self._lock:
                shared, result = self._renew_gen != gen, self._last_renew_result
            if shared:                          # a renewal completed while we waited: use its outcome
                return result if result and result != rejected and self._token_not_known_bad(result) else None
            current = self.token()
            if current and current != rejected and self._token_not_known_bad(current):
                return current                  # a rotation already produced a different, usable token
            with self._lock:
                self._refresh_status = "refreshing"
                self._runtime = None if self._runtime is not None and self._runtime.value == rejected else self._runtime
            fresh, why = None, None
            if self._issuer.configured():
                fresh, why = self._generate()
            if fresh is None:
                with self._lock:
                    if self._issued is not None and self._issued.value == rejected:
                        self._issued = None     # a rejected generated token must not hide the configured source
                try:
                    self._source().invalidate()
                except Exception:  # noqa: BLE001
                    pass
                cand = self.token()
                if cand and cand != rejected and self._token_not_known_bad(cand):
                    fresh = cand
            with self._lock:
                # a proactive look-ahead with no credentials that finds nothing newer is not a failure
                idle = proactive and why is None
                self._refresh_status = "renewed" if fresh else ("idle" if idle else "failed")
                self._refresh_detail = None if fresh else (why or NO_NEWER)
                self._last_refresh_at = self._clock()
                self._renew_gen += 1
                self._last_renew_result = fresh
            log.info("IMD token renewal %s (value not logged)", "succeeded" if fresh else "failed")
            return fresh

    def _generate(self) -> tuple[Optional[str], Optional[str]]:
        """One token-generation request (unless backing off after a recent failure). Returns (token, None) or
        (None, reason). The new token replaces the current one atomically (a single reference assignment)."""
        now = self._clock()
        with self._lock:
            if now < self._issue_blocked_until:
                return None, self._refresh_detail or "automatic IMD token generation is backing off after a failure"
        try:
            issued = self._issuer.issue()
        except TokenIssueError as ex:
            with self._lock:
                self._issue_blocked_until = now + ISSUE_BACKOFF_S.get(ex.kind, 60)
            log.warning("automatic IMD token generation failed: %s", ex)      # the message holds no secret
            return None, f"Automatic IMD token generation failed: {ex}"
        bad = malformed_reason(issued.value)
        if bad:
            with self._lock:
                self._issue_blocked_until = now + 60
            return None, f"Automatic IMD token generation returned an unusable token ({bad})"
        exp = now + issued.expires_in if issued.expires_in else None
        with self._lock:
            self._issued = TokenReading(issued.value, ISSUED_SOURCE, now, exp)
            self._issue_blocked_until = 0.0
        return issued.value, None

    def _expiry(self, token: str) -> Optional[float]:
        exp = jwt_expiry(token)
        if exp is None:
            issued = self._issued
            if issued is not None and issued.value == token:
                exp = issued.expires_at
        return exp

    def _token_not_known_bad(self, token: str) -> bool:
        if malformed_reason(token):
            return False
        with self._lock:
            if fingerprint(token) in self._rejected:
                return False
        exp = self._expiry(token)
        return exp is None or exp > self._clock()

    def token_for_request(self) -> Optional[str]:
        """The token to send now. If the current one is KNOWN to be unusable (rejected by IMD, or its expiry has
        passed), or missing while credentials are configured, renew first. Under 10 min left: renew ahead of
        expiry, and keep using the current token if renewal cannot produce a newer one."""
        tok = self.token()
        if tok and self._token_not_known_bad(tok):
            exp = self._expiry(tok)
            if exp is not None and exp - self._clock() <= EXPIRING_SOON_S:
                return self.renew(tok, proactive=True) or tok
            return tok
        return self.renew(tok)

    def refresh_detail(self) -> str:
        with self._lock:
            return self._refresh_detail or NO_NEWER

    def start_keeper(self, interval_s: float = 60.0) -> None:
        """Background renewal, so the token stays fresh even when nobody is requesting IMD data. Only runs when
        account credentials are configured; each tick is just `token_for_request()` (no IMD data call)."""
        if self._keeper is not None or not self.auto_renewal():
            return

        def loop():
            while True:
                try:
                    if self.api_key() and self.auto_renewal():
                        self.token_for_request()
                except Exception as ex:  # noqa: BLE001 -- the keeper must never die
                    log.warning("IMD token keeper: %s", type(ex).__name__)
                time.sleep(interval_s)
        self._keeper = threading.Thread(target=loop, name="imd-token-keeper", daemon=True)
        self._keeper.start()

    # ------------------------------------------------------------------ credentials (server-side only)
    def api_key(self) -> Optional[str]:
        """Same hot-reload rule as the token (imd_token_sources.EnvTokenSource): config.py copies .env into the
        environment once, so while the environment still holds THAT start-up value, re-read the file. A key
        replaced in .env (e.g. a new key for a new IP) is then used on the next IMD request without a restart.
        An explicitly set environment variable (Docker --env, Vercel, a test) differs from it and always wins."""
        env = os.environ.get(KEY_ENV) or None
        if env is not None and self._dotenv is not None and env == self._key_dotenv_at_start:
            live = _read_dotenv_value(self._dotenv, KEY_ENV)
            if live:
                return live
        return env

    def _source(self) -> TokenSource:
        # rebuilt per call (cheap) so IMD_TOKEN_PROVIDER / IMD_API_TOKEN_FILE changes take effect without restart;
        # the AWS source keeps its cache on the class instance it owns, so it is built once per configuration
        if self._explicit_source is not None:
            return self._explicit_source
        kind = (os.environ.get("IMD_TOKEN_PROVIDER") or "env", os.environ.get("IMD_API_TOKEN_FILE"))
        with self._lock:
            if getattr(self, "_source_key", None) != kind:
                self._source_obj, self._source_key = build_source(self._dotenv), kind
            return self._source_obj

    def _locate(self) -> Optional[TokenReading]:
        if self._runtime is not None:
            return self._runtime
        if self._issued is not None:
            return self._issued
        return self._source().read()

    def token(self) -> Optional[str]:
        r = self._locate()
        if r is None or malformed_reason(r.value):
            return None                                  # a malformed token is never sent
        with self._lock:
            self._first_seen.setdefault(fingerprint(r.value), self._clock())
        return r.value

    def set_runtime_token(self, token: str) -> None:
        """Rotate the token in the running process (admin endpoint). Also rewrites IMD_API_TOKEN_FILE when one is
        configured, so the rotation survives a restart."""
        token = token.strip()
        with self._lock:
            self._runtime = TokenReading(token, "runtime (admin endpoint)", self._clock())
        path = os.environ.get("IMD_API_TOKEN_FILE")
        if path:
            try:
                Path(path).write_text(token, encoding="utf-8")
            except OSError:
                log.warning("token accepted in memory but the token file could not be written")
        log.info("IMD token rotated at run time (value not logged)")

    def reset(self) -> None:
        """Forget everything learned at run time (test isolation; also used after an admin rotation)."""
        with self._lock:
            self._runtime = self._last_ok_at = None
            self._first_seen.clear()
            self._rejected.clear()
            self._rejected_keys.clear()
            self._source_key = None
            self._refresh_status, self._last_refresh_at, self._refresh_detail = "idle", None, None
            self._issued, self._last_renew_result, self._issue_blocked_until = None, None, 0.0

    # ------------------------------------------------------------------ what IMD told us
    def report_success(self) -> None:
        with self._lock:
            self._last_ok_at = self._clock()

    def report_auth_failure(self, token: Optional[str], status_code: int = 401) -> None:
        """401 -> this token is rejected. 403 -> the key / server IP is rejected (a new token would not help)."""
        with self._lock:
            if status_code == 403:
                key = self.api_key()
                if key:
                    self._rejected_keys[fingerprint(key)] = self._clock()
            elif token:
                self._rejected[fingerprint(token)] = self._clock()

    # ------------------------------------------------------------------ state
    def status(self) -> TokenStatus:
        s = self._status()
        with self._lock:
            s.refresh_status, s.last_refresh_at = self._refresh_status, self._last_refresh_at
            failed_why = self._refresh_detail if self._refresh_status == "failed" else None
        s.auto_renewal = self.auto_renewal()
        if failed_why and s.state not in (VALID, EXPIRING_SOON):
            s.detail = f"{s.detail}; {failed_why}"
        if s.refresh_status == "refreshing" and s.rejected_by != "key_or_ip":
            s.state, s.detail = RENEWING, "a new IMD token is being obtained"
        return s

    def _status(self) -> TokenStatus:
        key = self.api_key()
        reading = self._locate()
        now = self._clock()
        tok = reading.value if reading else None
        source = reading.source if reading else None
        with self._lock:
            last_ok = self._last_ok_at
            key_rejected_at = self._rejected_keys.get(fingerprint(key)) if key else None
            fp = fingerprint(tok)
            rejected_at = self._rejected.get(fp) if fp else None
            if tok and not malformed_reason(tok):
                self._first_seen.setdefault(fp, now)
            first_seen = self._first_seen.get(fp) if fp else None

        if key and not tok and self.auto_renewal():
            with self._lock:
                failed = self._refresh_status == "failed"
            if failed:
                return TokenStatus(UNAVAILABLE, "no IMD token", source, None, "unknown", None, True, last_ok, None)
            return TokenStatus(EXPIRED, "no current IMD token; one is generated automatically from the IMD account "
                               "credentials on the next request", source, None, "unknown", None, True, last_ok, None)
        if not key or not tok:
            missing = [n for n, ok in ((KEY_ENV, bool(key)), (TOKEN_ENV, bool(tok))) if not ok]
            return TokenStatus(UNAVAILABLE, f"{' and '.join(missing)} not available on the server", source, None,
                               "unknown", None, bool(key), last_ok, None)
        bad = malformed_reason(tok)
        if bad:
            return TokenStatus(UNAVAILABLE, f"{bad}; it was not sent to IMD", source, None, "unknown", None, True,
                               last_ok, None)
        if key_rejected_at is not None:
            return TokenStatus(UNAVAILABLE, "IMD refused the API key or this server's IP (HTTP 403); a new token will "
                               "not fix this -- check the key's IP binding", source, None, "unknown", None, True,
                               last_ok, key_rejected_at, "key_or_ip")
        if rejected_at is not None:
            return TokenStatus(EXPIRED, "IMD rejected this token (HTTP 401); rotate it (docs/DEPLOYMENT.md)", source,
                               None, "unknown", 0.0, True, last_ok, rejected_at, "token")
        exp = jwt_expiry(tok)
        basis = "jwt_exp"
        if exp is None and reading.expires_at is not None:
            exp, basis = reading.expires_at, "issuer_expires_in"
        if exp is None:
            try:
                ttl_s = float(os.environ.get(TTL_ENV, DEFAULT_TTL_MIN)) * 60.0
            except ValueError:
                ttl_s = DEFAULT_TTL_MIN * 60.0
            dated = reading.dated if reading.dated is not None else first_seen
            exp, basis = dated + ttl_s, "loaded_at+ttl"
        left = exp - now
        if left <= 0:
            note = "assumed token lifetime elapsed (not confirmed by IMD)" if basis == "loaded_at+ttl" else "token lifetime elapsed"
            return TokenStatus(EXPIRED, f"{note}; rotate it (docs/DEPLOYMENT.md)", source, exp, basis, left, True,
                               last_ok, None)
        state = EXPIRING_SOON if left <= EXPIRING_SOON_S else VALID
        return TokenStatus(state, "expiry read from the token" if basis == "jwt_exp"
                           else "expiry estimated from when the token was loaded", source, exp, basis, left, True,
                           last_ok, None)

    def usable(self) -> bool:
        """False only when a request is certain to fail: nothing configured, malformed, key/IP refused, this exact
        token already rejected, or a real JWT `exp` in the past. An ESTIMATED expiry never blocks a request --
        IMD's answer is the authority."""
        s = self.status()
        if s.state == UNAVAILABLE or s.last_rejected_at is not None:
            return False
        return not (s.state == EXPIRED and s.expiry_basis in ("jwt_exp", "issuer_expires_in"))


manager = IMDTokenManager()
