"""Automatic IMD access-token generation from the account credentials.

IMD's token endpoint `POST https://api.imd.gov.in/api/oauth/token.php` takes the registered portal email and
password as a JSON body `{"email", "password"}` and returns an access token (about 3600 s). Checked 2026-09-21:
an empty body gets HTTP 400 "email and password required", a form-encoded body the same 400, and JSON with fake
credentials HTTP 401 "Invalid credentials". The token manager (imd_auth.py) calls `issue()` to renew the token
before it expires and after an IMD 401.

The credentials are backend-only secrets, kept apart from IMD_API_KEY and selected by IMD_CREDENTIALS_PROVIDER:
  env (default)        IMD_EMAIL + IMD_PASSWORD from the environment (the repo-root .env is loaded at start-up).
  aws-secretsmanager   secret IMD_CREDENTIALS_SECRET_ID holding JSON {"IMD_EMAIL": ..., "IMD_PASSWORD": ...}.
  aws-ssm              SecureString parameters IMD_EMAIL_SSM_PARAMETER + IMD_PASSWORD_SSM_PARAMETER.
Nothing here logs, returns in an error, or otherwise exposes the email, the password or the issued token.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

TOKEN_URL_ENV, DEFAULT_TOKEN_URL = "IMD_TOKEN_URL", "https://api.imd.gov.in/api/oauth/token.php"
EMAIL_ENV, PASSWORD_ENV, CRED_PROVIDER_ENV = "IMD_EMAIL", "IMD_PASSWORD", "IMD_CREDENTIALS_PROVIDER"
TIMEOUT_S = 20.0


@dataclass(frozen=True)
class IssuedToken:
    value: str
    expires_in: Optional[float]          # seconds, as stated by IMD; None if the response did not say
    token_type: Optional[str] = None     # e.g. "Bearer", as stated by IMD


class TokenIssueError(Exception):
    """kind: not_configured | credentials_rejected | http | network | bad_response. The message never contains a
    credential or a token."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ------------------------------------------------------------------ where the credentials come from
def _env_credentials() -> tuple[Optional[str], Optional[str]]:
    return os.environ.get(EMAIL_ENV) or None, os.environ.get(PASSWORD_ENV) or None


def _aws_secretsmanager_credentials() -> tuple[Optional[str], Optional[str]]:
    import boto3  # optional dependency, only when this provider is selected
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["IMD_CREDENTIALS_SECRET_ID"])["SecretString"]
    obj = json.loads(raw)
    return obj.get(EMAIL_ENV) or None, obj.get(PASSWORD_ENV) or None


def _aws_ssm_credentials() -> tuple[Optional[str], Optional[str]]:
    import boto3  # optional dependency, only when this provider is selected
    ssm = boto3.client("ssm")
    get = lambda name: ssm.get_parameter(Name=os.environ[name], WithDecryption=True)["Parameter"]["Value"]  # noqa: E731
    return get("IMD_EMAIL_SSM_PARAMETER") or None, get("IMD_PASSWORD_SSM_PARAMETER") or None


CREDENTIAL_PROVIDERS: dict[str, Callable[[], tuple[Optional[str], Optional[str]]]] = {
    "env": _env_credentials,
    "aws-secretsmanager": _aws_secretsmanager_credentials,
    "aws-ssm": _aws_ssm_credentials,
}


def credentials_configured() -> bool:
    """Cheap check with no network call: are credentials (or a credential store) configured at all?"""
    kind = (os.environ.get(CRED_PROVIDER_ENV) or "env").strip().lower()
    if kind == "aws-secretsmanager":
        return bool(os.environ.get("IMD_CREDENTIALS_SECRET_ID"))
    if kind == "aws-ssm":
        return bool(os.environ.get("IMD_EMAIL_SSM_PARAMETER") and os.environ.get("IMD_PASSWORD_SSM_PARAMETER"))
    email, password = _env_credentials()
    return bool(email and password)


# ------------------------------------------------------------------ the request
def _http_post(url: str, body: dict) -> tuple[int, object]:
    import httpx
    r = httpx.post(url, json=body, timeout=TIMEOUT_S)
    try:
        payload = r.json()
    except Exception:  # noqa: BLE001
        payload = None
    return r.status_code, payload


def _imd_error(payload: object, *secrets: Optional[str]) -> str:
    """IMD's own short error text (e.g. "Invalid credentials"), only if it cannot carry a secret."""
    msg = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(msg, str) or len(msg) > 120 or any(s and s in msg for s in secrets):
        return ""
    return f": {msg}"


class IMDTokenIssuer:
    def __init__(self, credentials: Optional[Callable[[], tuple[Optional[str], Optional[str]]]] = None,
                 http_post: Callable[[str, dict], tuple[int, object]] = _http_post, url: Optional[str] = None):
        self._credentials, self._post, self._url = credentials, http_post, url
        self._lock = threading.Lock()
        self.requests = 0                                 # token-generation requests sent (tests / diagnostics)
        self.last_http_status: Optional[int] = None       # safe diagnostics only: never the token itself
        self.last_token_type: Optional[str] = None
        self.last_expires_in: Optional[float] = None

    def configured(self) -> bool:
        return True if self._credentials is not None else credentials_configured()

    def _read_credentials(self) -> tuple[Optional[str], Optional[str]]:
        if self._credentials is not None:
            return self._credentials()
        kind = (os.environ.get(CRED_PROVIDER_ENV) or "env").strip().lower()
        try:
            return CREDENTIAL_PROVIDERS.get(kind, _env_credentials)()
        except Exception as ex:  # noqa: BLE001 -- a store outage is reported, never raised with details
            raise TokenIssueError("not_configured", f"IMD credentials could not be read from {kind} "
                                  f"({type(ex).__name__})") from None

    def issue(self) -> IssuedToken:
        email, password = self._read_credentials()
        if not (email and password):
            raise TokenIssueError("not_configured", f"{EMAIL_ENV} / {PASSWORD_ENV} are not configured on the server")
        url = self._url or os.environ.get(TOKEN_URL_ENV) or DEFAULT_TOKEN_URL
        with self._lock:
            self.requests += 1
        try:
            status, payload = self._post(url, {"email": email, "password": password})
        except Exception as ex:  # noqa: BLE001 -- the exception text may echo the request; report its type only
            raise TokenIssueError("network", f"IMD token endpoint unreachable ({type(ex).__name__})") from None
        self.last_http_status = status
        if status in (401, 403):
            raise TokenIssueError("credentials_rejected", "IMD token endpoint refused the account credentials "
                                  f"(HTTP {status}{_imd_error(payload, email, password)})")
        if status != 200:
            raise TokenIssueError("http", f"IMD token endpoint returned HTTP {status}{_imd_error(payload, email, password)}")
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise TokenIssueError("bad_response", "IMD token endpoint answered 200 without an access_token")
        exp_in = payload.get("expires_in")
        try:
            exp_in = float(exp_in) if exp_in is not None and not isinstance(exp_in, bool) else None
        except (TypeError, ValueError):
            exp_in = None
        log.info("IMD access token generated from the account credentials (value not logged)")
        tt = payload.get("token_type")
        issued = IssuedToken(token.strip(), exp_in if exp_in and exp_in > 0 else None,
                             tt if isinstance(tt, str) and len(tt) <= 20 else None)
        self.last_token_type, self.last_expires_in = issued.token_type, issued.expires_in
        return issued
