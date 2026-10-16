"""WhatsApp alerts through Twilio (docs/ALERTS_WHATSAPP.md, DECISIONS.md D-31).

Sends a FloodNet TEST alert to the WhatsApp numbers listed in WHATSAPP_TO, and only to those. Every message starts
with BANNER: FloodNet never issues official warnings. Nothing here logs or returns a credential; recipient numbers are
returned masked.

Configuration (repo-root .env, backend only, never VITE_):
  TWILIO_ACCOUNT_SID                     AC...  (always needed: it is part of the API URL)
  TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET   preferred credentials, or
  TWILIO_AUTH_TOKEN                      the account's auth token (used when no API key pair is set)
  TWILIO_WHATSAPP_FROM                   e.g. whatsapp:+14155238886 (the Twilio sandbox number)
  WHATSAPP_TO                            comma-separated whatsapp:+91XXXXXXXXXX numbers that joined the sandbox

Twilio accepts a WhatsApp message (HTTP 201, status "queued") before WhatsApp has decided whether it can be delivered;
a recipient who has not joined the sandbox, or whose 24-hour window has closed, shows up afterwards as a failed
message with an error code. message_status() fetches that outcome.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from typing import Callable, Optional

BANNER = "⚠ FloodNet TEST ALERT (simulation, not an official warning)"
API = "https://api.twilio.com/2010-04-01"
MAX_BODY_CHARS = 1500            # Twilio's WhatsApp body limit is 1600; keep a margin
DEDUP_WINDOW_S = 300.0           # the same text is not sent twice within 5 minutes
RATE_WINDOW_S, RATE_MAX = 600.0, 20     # at most 20 messages (per recipient batch) every 10 minutes
TIMEOUT_S = 20.0
SID_RE = re.compile(r"^(SM|MM)[0-9a-f]{32}$")
NUMBER_RE = re.compile(r"^whatsapp:\+\d{8,15}$")

# Twilio error codes seen for WhatsApp, with a plain-language hint. The Twilio message is always shown as well.
HINTS = {
    63015: "this number has not joined the Twilio sandbox: send the join code from WhatsApp to the sandbox number",
    63016: "outside WhatsApp's 24-hour window: the recipient must message the sandbox number again first",
    21211: "the recipient number is not a valid phone number",
    21608: "trial accounts can only send to verified numbers",
    20003: "Twilio rejected the credentials (check TWILIO_ACCOUNT_SID and the key/secret or auth token)",
    63007: "this From number is not a WhatsApp sender on the account: use the sandbox number whatsapp:+14155238886",
    21654: "this Twilio account only sends WhatsApp messages from an approved template (ContentSid); Trial accounts cannot create templates: upgrade the account, or use another channel",
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def mask(number: str) -> str:
    digits = number.replace("whatsapp:", "")
    return f"whatsapp:{digits[:3]}{'•' * max(len(digits) - 7, 0)}{digits[-4:]}" if len(digits) > 7 else "whatsapp:••••"


def config() -> dict:
    """Read on every call, so a changed .env value takes effect after a backend restart without code changes."""
    account = _env("TWILIO_ACCOUNT_SID")
    key, secret, token = _env("TWILIO_API_KEY_SID"), _env("TWILIO_API_KEY_SECRET"), _env("TWILIO_AUTH_TOKEN")
    sender = _env("TWILIO_WHATSAPP_FROM")
    to = [n.strip() for n in _env("WHATSAPP_TO").split(",") if n.strip()]
    if key and secret:
        auth, auth_kind = (key, secret), "api_key"
    elif account and token:
        auth, auth_kind = (account, token), "auth_token"
    else:
        auth, auth_kind = None, None
    missing = []
    if not account.startswith("AC"):
        missing.append("TWILIO_ACCOUNT_SID (starts with AC; an SK... value is an API key, not the account)")
    if auth is None:
        missing.append("TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET, or TWILIO_AUTH_TOKEN")
    if not NUMBER_RE.match(sender):
        missing.append("TWILIO_WHATSAPP_FROM (format whatsapp:+14155238886)")
    bad = [n for n in to if not NUMBER_RE.match(n)]
    if not to:
        missing.append("WHATSAPP_TO (comma-separated, format whatsapp:+91XXXXXXXXXX)")
    elif bad:
        missing.append(f"WHATSAPP_TO has {len(bad)} number(s) not in the format whatsapp:+<country><number>")
    return {"account": account, "auth": auth, "auth_kind": auth_kind, "from": sender,
            "to": [n for n in to if NUMBER_RE.match(n)], "missing": missing}


def status() -> dict:
    c = config()
    return {"configured": not c["missing"], "missing": c["missing"], "auth": c["auth_kind"],
            "from": mask(c["from"]) if c["from"] else None, "sandbox": c["from"] == "whatsapp:+14155238886",
            "recipients": [mask(n) for n in c["to"]], "banner": BANNER,
            "limits": {"dedup_window_s": DEDUP_WINDOW_S, "max_messages_per_10_min": RATE_MAX, "max_chars": MAX_BODY_CHARS}}


def compose(lines: list[str]) -> str:
    """BANNER first, always; then the caller's lines, trimmed to the WhatsApp limit."""
    body = "\n".join([BANNER] + [str(l).strip() for l in lines if str(l).strip()])
    return body if len(body) <= MAX_BODY_CHARS else body[:MAX_BODY_CHARS - 1] + "…"


def _http_post(url: str, auth: tuple, data: dict) -> tuple[int, dict]:
    import httpx
    r = httpx.post(url, auth=auth, data=data, timeout=TIMEOUT_S)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


def _http_get(url: str, auth: tuple) -> tuple[int, dict]:
    import httpx
    r = httpx.get(url, auth=auth, timeout=TIMEOUT_S)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


def _error(payload: dict, http_status: int) -> dict:
    code = payload.get("code") or payload.get("error_code")
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    msg = payload.get("message") or payload.get("error_message") or f"HTTP {http_status}"
    return {"code": code, "message": str(msg)[:200], "hint": HINTS.get(code, "")}


class WhatsAppSender:
    def __init__(self, post: Callable = _http_post, get: Callable = _http_get, clock: Callable[[], float] = time.monotonic):
        self._post, self._get, self._clock = post, get, clock
        self._lock = threading.Lock()
        self._recent: dict[str, float] = {}
        self._sent_at: list[float] = []

    def send(self, lines: list[str], kind: str = "test") -> dict:
        c = config()
        if c["missing"]:
            return {"sent": 0, "skipped": "not configured", "missing": c["missing"], "results": []}
        body = compose(lines)
        digest = hashlib.sha256(body.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            self._recent = {k: t for k, t in self._recent.items() if now - t < DEDUP_WINDOW_S}
            self._sent_at = [t for t in self._sent_at if now - t < RATE_WINDOW_S]
            if digest in self._recent:
                return {"sent": 0, "skipped": f"identical alert already sent in the last {DEDUP_WINDOW_S / 60:.0f} min", "results": []}
            if len(self._sent_at) + len(c["to"]) > RATE_MAX:
                return {"sent": 0, "skipped": f"rate limit: at most {RATE_MAX} messages every {RATE_WINDOW_S / 60:.0f} min", "results": []}
            self._recent[digest] = now
            self._sent_at.extend([now] * len(c["to"]))
        url = f"{API}/Accounts/{c['account']}/Messages.json"
        results = []
        for to in c["to"]:
            try:
                code, payload = self._post(url, c["auth"], {"From": c["from"], "To": to, "Body": body})
            except Exception as ex:  # noqa: BLE001 -- never echo the request (it carries credentials in the client)
                results.append({"to": mask(to), "ok": False, "error": {"code": None, "message": f"Twilio unreachable ({type(ex).__name__})", "hint": ""}})
                continue
            if code in (200, 201):
                results.append({"to": mask(to), "ok": True, "sid": payload.get("sid"), "status": payload.get("status")})
            else:
                results.append({"to": mask(to), "ok": False, "error": _error(payload, code)})
        n_ok = sum(r["ok"] for r in results)
        if n_ok < len(results):                 # only accepted messages count towards de-duplication and the rate limit
            with self._lock:
                if n_ok == 0:
                    self._recent.pop(digest, None)
                for _ in range(len(results) - n_ok):
                    if now in self._sent_at:
                        self._sent_at.remove(now)
        return {"sent": n_ok, "kind": kind, "chars": len(body), "results": results}

    def message_status(self, sid: str) -> dict:
        if not SID_RE.match(sid or ""):
            raise ValueError("not a Twilio message SID")
        c = config()
        if c["missing"]:
            return {"sid": sid, "status": None, "error": {"code": None, "message": "not configured", "hint": ""}}
        code, payload = self._get(f"{API}/Accounts/{c['account']}/Messages/{sid}.json", c["auth"])
        if code != 200:
            return {"sid": sid, "status": None, "error": _error(payload, code)}
        err = _error(payload, code) if payload.get("error_code") else None
        return {"sid": sid, "status": payload.get("status"), "error": err}


sender = WhatsAppSender()
