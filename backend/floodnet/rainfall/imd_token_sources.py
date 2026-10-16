"""Where the IMD bearer token comes FROM -- a small pluggable abstraction, so the core never knows (or cares)
whether the value lives in an environment variable, a file, or a cloud secret store.

Every source answers one question, `read() -> TokenReading | None`, and is re-asked on every IMD request, so a
rotated value is picked up while the server runs (no restart, no frontend or image rebuild). Sources never log,
return in errors, or otherwise expose the value.

Selected by `IMD_TOKEN_PROVIDER` (default `env`):
  env                  IMD_API_TOKEN from the environment; if that value came from the repo-root .env at start-up,
                       the file is re-read so editing .env is a hot reload (local development).
  file                 IMD_API_TOKEN_FILE: a file holding only the token (Docker/Kubernetes secret mount).
  aws-secretsmanager   AWS Secrets Manager secret IMD_TOKEN_SECRET_ID (optional JSON key IMD_TOKEN_SECRET_KEY).
  aws-ssm              AWS SSM Parameter Store SecureString IMD_TOKEN_SSM_PARAMETER.
The AWS sources import boto3 lazily (optional dependency) and cache the value for IMD_TOKEN_CACHE_S seconds
(default 60) so rotation shows up within a minute without calling AWS on every request. Credentials for AWS
itself come from the standard AWS chain (instance role); nothing AWS-specific lives in the core logic.
A token set through the protected admin endpoint always takes precedence over the configured source.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

TOKEN_ENV, TOKEN_FILE_ENV, PROVIDER_ENV = "IMD_API_TOKEN", "IMD_API_TOKEN_FILE", "IMD_TOKEN_PROVIDER"


@dataclass(frozen=True)
class TokenReading:
    value: str
    source: str                          # label safe to show: "environment", ".env", "token file", "aws-ssm", ...
    dated: Optional[float] = None        # epoch seconds the value is known to date from (e.g. file mtime), if any
    expires_at: Optional[float] = None   # expiry stated by the issuer (IMD `expires_in`), if any


def _read_dotenv_value(path: Path, name: str) -> Optional[str]:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.partition("=")[2].strip().strip('"').strip("'") or None
    except OSError:
        return None
    return None


class TokenSource:
    name = "base"

    def read(self) -> Optional[TokenReading]:  # pragma: no cover - interface
        raise NotImplementedError

    def invalidate(self) -> None:
        """Drop any cached value so the next read goes to the backing store (used by token renewal)."""


class EnvTokenSource(TokenSource):
    name = "env"

    def __init__(self, dotenv_path: Optional[Path]):
        self._dotenv = dotenv_path
        self._dotenv_at_start = _read_dotenv_value(dotenv_path, TOKEN_ENV) if dotenv_path else None

    def read(self) -> Optional[TokenReading]:
        env = os.environ.get(TOKEN_ENV) or None
        if env is None:
            return None
        # config.py copies .env into the environment once. If the environment still holds THAT value, re-read the
        # file (hot reload). An explicitly set variable (Docker --env, a test) differs from it and always wins.
        if self._dotenv is not None and env == self._dotenv_at_start:
            live = _read_dotenv_value(self._dotenv, TOKEN_ENV)
            if live:
                return TokenReading(live, ".env")
        return TokenReading(env, "environment")


class FileTokenSource(TokenSource):
    name = "file"

    def __init__(self, path: Optional[str]):
        self._path = path

    def read(self) -> Optional[TokenReading]:
        if not self._path:
            return None
        try:
            p = Path(self._path)
            value = p.read_text(encoding="utf-8").strip()
            return TokenReading(value, "token file", p.stat().st_mtime) if value else None
        except OSError:
            log.warning("%s is set but the token file could not be read", TOKEN_FILE_ENV)
            return None


class CachedExternalSource(TokenSource):
    """Wraps a fetch function for a remote secret store; caches the value for `ttl_s`. Fetch failures never raise
    to the caller (the manager then reports UNAVAILABLE and FloodNet falls back) and never include the value."""

    def __init__(self, name: str, fetch: Callable[[], Optional[str]], ttl_s: float = 60.0, clock=time.time):
        self.name, self._fetch, self._ttl, self._clock = name, fetch, ttl_s, clock
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()      # single-flight: one store round-trip at a time
        self._cached: Optional[TokenReading] = None
        self._at = -1e18

    def invalidate(self) -> None:
        with self._lock:
            self._at = -1e18

    def read(self) -> Optional[TokenReading]:
        with self._lock:
            if self._clock() - self._at < self._ttl:
                return self._cached
        with self._fetch_lock:                   # concurrent readers wait for the one in-flight fetch ...
            with self._lock:
                if self._clock() - self._at < self._ttl:
                    return self._cached          # ... and reuse its result instead of fetching again
            try:
                value = (self._fetch() or "").strip()
            except Exception as ex:  # noqa: BLE001 -- a secret-store outage must not take the forecast down
                log.warning("IMD token could not be read from %s (%s)", self.name, type(ex).__name__)
                value = ""
            with self._lock:
                self._cached = TokenReading(value, self.name) if value else None
                self._at = self._clock()
                return self._cached


def _aws_secretsmanager_fetch() -> Optional[str]:
    import boto3  # optional dependency, only when this provider is selected
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["IMD_TOKEN_SECRET_ID"])["SecretString"]
    key = os.environ.get("IMD_TOKEN_SECRET_KEY")
    return json.loads(raw)[key] if key else raw


def _aws_ssm_fetch() -> Optional[str]:
    import boto3  # optional dependency, only when this provider is selected
    return boto3.client("ssm").get_parameter(Name=os.environ["IMD_TOKEN_SSM_PARAMETER"],
                                             WithDecryption=True)["Parameter"]["Value"]


EXTERNAL_FETCHERS: dict[str, Callable[[], Optional[str]]] = {
    "aws-secretsmanager": _aws_secretsmanager_fetch,
    "aws-ssm": _aws_ssm_fetch,
}


def build_source(dotenv_path: Optional[Path]) -> TokenSource:
    """The configured source. `env` also honours IMD_API_TOKEN_FILE when set (file first), which keeps the
    earlier deployment instructions working."""
    kind = (os.environ.get(PROVIDER_ENV) or "env").strip().lower()
    if kind in EXTERNAL_FETCHERS:
        try:
            ttl = float(os.environ.get("IMD_TOKEN_CACHE_S", "60"))
        except ValueError:
            ttl = 60.0
        return CachedExternalSource(kind, EXTERNAL_FETCHERS[kind], ttl_s=ttl)
    if kind == "file":
        return FileTokenSource(os.environ.get(TOKEN_FILE_ENV))
    if kind != "env":
        log.warning("unknown %s=%r; using the environment", PROVIDER_ENV, kind)
    return ChainSource([FileTokenSource(os.environ.get(TOKEN_FILE_ENV)), EnvTokenSource(dotenv_path)])


class ChainSource(TokenSource):
    name = "chain"

    def __init__(self, sources: list[TokenSource]):
        self._sources = sources

    def invalidate(self) -> None:
        for s in self._sources:
            s.invalidate()

    def read(self) -> Optional[TokenReading]:
        for s in self._sources:
            r = s.read()
            if r is not None:
                return r
        return None
