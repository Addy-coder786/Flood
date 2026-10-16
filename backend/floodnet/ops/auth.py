"""Who may do what in live operations (DECISIONS D-39).

  dispatcher  FLOODNET_DISPATCHER_TOKEN (backend environment only, never a VITE_ variable). When it is not set the
              dispatcher is whoever uses this machine (loopback clients only) -- never on Vercel.
  responder   a field team or shelter-staff account registered by the dispatcher; token shown once, stored hashed.
  driver      one trip's token (in the driver link); opens that trip and nothing else.
  public      no token; only the public alert endpoints.
Tokens arrive as `Authorization: Bearer <token>` or `X-FloodNet-Token: <token>`.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def new_token() -> str:
    return secrets.token_urlsafe(24)            # 192 bits


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: Optional[str], token_hash: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def presented_token(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or ""
    if h.lower().startswith("bearer "):
        return h[7:].strip() or None
    return (request.headers.get("x-floodnet-token") or "").strip() or None


def _hostname(value: Optional[str]) -> Optional[str]:
    """'127.0.0.1:8000' / '[::1]:8000' / 'http://localhost:5173' -> the bare host name, lower-cased."""
    if not value:
        return None
    v = value.strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):
        return v[1:v.index("]")] if "]" in v else v
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def dispatcher_allowed(token: Optional[str], client_host: Optional[str], host: Optional[str] = None,
                       origin: Optional[str] = None) -> bool:
    """With FLOODNET_DISPATCHER_TOKEN: the token decides. Without it (local use only, never on Vercel): the TCP client,
    the Host header and any Origin header must all be this machine. The Host check defeats DNS rebinding and tunnels;
    the Origin check stops another web page open in the dispatcher's browser from acting as the dispatcher
    (QA finding H1, 2026-10-11)."""
    configured = (os.environ.get("FLOODNET_DISPATCHER_TOKEN") or "").strip()
    if configured:
        return bool(token) and hmac.compare_digest(hash_token(token), hash_token(configured))
    if os.environ.get("VERCEL") == "1":
        return False
    if client_host not in LOOPBACK:
        return False
    if host is not None and _hostname(host) not in LOOPBACK:
        return False
    if origin and origin != "null" and _hostname(origin) not in LOOPBACK:
        return False
    return True


def require_dispatcher(request: Request) -> str:
    """FastAPI dependency: 401 unless the caller is the dispatcher."""
    host = request.client.host if request.client else None
    if not dispatcher_allowed(presented_token(request), host, request.headers.get("host"), request.headers.get("origin")):
        configured = bool((os.environ.get("FLOODNET_DISPATCHER_TOKEN") or "").strip())
        raise HTTPException(401, "dispatcher token required" if configured or os.environ.get("VERCEL") == "1"
                            else "dispatcher actions are only allowed from this machine unless FLOODNET_DISPATCHER_TOKEN is set")
    return "dispatcher"
