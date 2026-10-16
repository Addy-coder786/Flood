"""Messaging providers, delivery statuses, webhook signatures and the live-sending gate (DECISIONS D-44).

Interface: provider.send(channel, to_e164, body, idempotency_key) -> {status, provider_message_id, error, raw_status}
  status ACCEPTED  the provider took the message (NOT delivery: only a carrier receipt / WhatsApp "delivered" is)
         REJECTED  the provider refused it (validation, number, template ...)
         FAILED    the request never reached the provider (connection refused): nothing was sent
         UNKNOWN   timeout or an ambiguous error: it may or may not have been sent; never retried blindly

Adapters
  SimulatedProvider  the default. Deterministic, records an outbox WITHOUT full numbers, injected failures, simulated
                     status callbacks. Everything it "sends" is labelled SIMULATED.
  TwilioProvider     SMS + WhatsApp via Twilio's Messages API. IMPLEMENTED BUT UNVERIFIED live; Indian SMS needs DLT.
  Msg91Provider      SMS via MSG91 Flow API (POST https://control.msg91.com/api/v5/flow/, header `authkey`).
                     IMPLEMENTED BUT UNVERIFIED live; needs DLT-approved templates; request shape from the provider docs
                     as summarised in docs/LIVE_OPERATIONS_RESEARCH.md §6.

Live sending happens only when live_sending_allowed() says so: FLOODNET_LIVE_MESSAGING=1, provider credentials present,
FLOODNET_PUBLIC_BASE_URL configured and the recipient on FLOODNET_MESSAGING_ALLOWLIST (compared by keyed HMAC).
Otherwise choose_provider() returns the SimulatedProvider. Nothing here logs or returns a number or a credential.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from typing import Callable, Optional, Union

TWILIO_API = "https://api.twilio.com/2010-04-01"
MSG91_FLOW_URL = "https://control.msg91.com/api/v5/flow/"
TIMEOUT_S = 15.0

# ----------------------------------------------------------------------------- delivery statuses (forward only)
RANK = {"QUEUED": 0, "SUBMITTED": 1, "UNKNOWN": 2, "ACCEPTED": 3, "SENT": 4, "DELIVERED": 5, "READ": 6}
FAILURES = frozenset({"REJECTED", "FAILED"})
TERMINAL = frozenset({"REJECTED", "FAILED", "SUPPRESSED", "READ"})
OK_STATES = frozenset({"ACCEPTED", "SENT", "DELIVERED", "READ"})
DELIVERY_STATUSES = tuple(RANK) + ("REJECTED", "FAILED", "SUPPRESSED")
# What each status means, in words for the dispatcher (2026-10-11). ACCEPTED/SENT are NOT delivery.
STATUS_MEANING = {
    "QUEUED": "Queued in FloodNet; not yet handed to a provider.",
    "SUBMITTED": "Being handed to the provider now; if this persists the outcome is unknown.",
    "UNKNOWN": "Outcome unknown (timeout or interrupted): it may or may not have been sent. Never retried blindly.",
    "ACCEPTED": "Provider accepted the message. Not delivered yet: only a delivery report says it reached the phone.",
    "SENT": "Sent: the provider handed it to the carrier or WhatsApp. Not confirmed delivered.",
    "DELIVERED": "Delivered: the carrier or WhatsApp reported it reached the phone.",
    "READ": "Read: WhatsApp reported that the message was read.",
    "REJECTED": "Rejected by the provider (number, template or content refused); nothing was delivered.",
    "FAILED": "Failed: not delivered.",
    "SUPPRESSED": "Not sent by FloodNet (opted out, cooldown or erased).",
}


def can_advance(old: str, new: str) -> bool:
    """Statuses only move forward; a late or repeated callback never moves a delivery back."""
    if old in TERMINAL or old == new:
        return False
    if new in FAILURES:
        return RANK.get(old, 99) < RANK["DELIVERED"]
    if new not in RANK:
        return False
    return RANK[new] > RANK.get(old, 99)


_TWILIO = {"queued": "ACCEPTED", "accepted": "ACCEPTED", "scheduled": "ACCEPTED", "sending": "ACCEPTED",
           "sent": "SENT", "delivered": "DELIVERED", "read": "READ", "undelivered": "FAILED", "failed": "FAILED",
           "canceled": "FAILED"}
_WHATSAPP = {"sent": "SENT", "delivered": "DELIVERED", "read": "READ", "failed": "FAILED"}
_MSG91 = {0: ("SENT", "sent to the operator"), 1: ("DELIVERED", "delivered"), 2: ("FAILED", "failed"),
          9: ("REJECTED", "NDNC: number on the Do-Not-Call registry"), 16: ("REJECTED", "rejected"),
          25: ("REJECTED", "rejected"), 17: ("REJECTED", "blocked"), 20: ("REJECTED", "country blocked")}


def map_twilio_status(raw: str) -> Optional[str]:
    return _TWILIO.get(str(raw or "").strip().lower())


def map_whatsapp_status(raw: str) -> Optional[str]:
    return _WHATSAPP.get(str(raw or "").strip().lower())


def map_msg91_status(code) -> tuple[Optional[str], str]:
    try:
        c = int(str(code).strip())
    except (TypeError, ValueError):
        return None, "unknown MSG91 status"
    return _MSG91.get(c, (None, f"unknown MSG91 status {c}"))


def msg91_reports(body) -> list[dict]:
    """MSG91 delivery reports -> [{request_id, crqid, status_code}], from either documented shape:
      Webhook (New): one flat JSON object per recipient {requestId, telNum, CRQID, status, ...} (or a list of them)
      older form:    {"data": [{requestId, report: [{status, ...}, ...]}, ...]}
    Only these three fields are kept: telNum / number (the recipient's phone number) is dropped here and never
    stored or logged."""
    items = body.get("data", [body]) if isinstance(body, dict) else body
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        rid = str(it.get("requestId") or it.get("request_id") or "")[:100]
        nested = it.get("report")
        for rep in (nested if isinstance(nested, list) else [it]):
            if not isinstance(rep, dict) or rep.get("status") is None:
                continue
            crqid = rep.get("CRQID") or it.get("CRQID")
            out.append({"request_id": rid, "crqid": str(crqid)[:120] if crqid else None, "status_code": rep["status"]})
    return out


# ----------------------------------------------------------------------------- webhook signatures
def twilio_signature(url: str, params: Union[dict, list], auth_token: str) -> str:
    """Twilio X-Twilio-Signature: base64(HMAC-SHA1(auth_token, full URL + each POST param name+value, sorted by
    name)). Repeated names are sorted by value as well."""
    pairs = sorted(params.items() if isinstance(params, dict) else params)
    data = url + "".join(f"{k}{v}" for k, v in pairs)
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode("utf-8"), hashlib.sha1).digest()).decode()


def verify_twilio(url: str, params, signature: Optional[str], auth_token: Optional[str]) -> bool:
    if not signature or not auth_token:
        return False
    return hmac.compare_digest(twilio_signature(url, params, auth_token), signature.strip())


def verify_whatsapp(raw_body: bytes, header: Optional[str], app_secret: Optional[str]) -> bool:
    """Meta X-Hub-Signature-256: 'sha256=' + hex HMAC-SHA256 of the raw request body keyed with the app secret."""
    if not header or not app_secret or not header.startswith("sha256="):
        return False
    want = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header.strip())


def verify_shared_secret(presented: Optional[str], configured: Optional[str]) -> bool:
    """MSG91 documents no webhook signature: FloodNet requires X-FloodNet-Webhook-Secret = MSG91_WEBHOOK_SECRET."""
    if not presented or not configured:
        return False
    return hmac.compare_digest(presented.strip().encode(), configured.strip().encode())


# ----------------------------------------------------------------------------- helpers
_NUMBERISH = re.compile(r"\+?\d[\d\s\-]{6,}\d")


def scrub(text) -> str:
    """Removes anything that looks like a phone number from provider error text."""
    return _NUMBERISH.sub("[number removed]", str(text or ""))[:200]


def _result(status: str, mid: Optional[str] = None, error: Optional[dict] = None, raw: Optional[str] = None) -> dict:
    return {"status": status, "provider_message_id": mid, "error": error, "raw_status": raw}


def _exception_result(ex: BaseException) -> dict:
    name = type(ex).__name__
    if isinstance(ex, (ConnectionRefusedError,)) or name in ("ConnectError", "ConnectionRefusedError"):
        return _result("FAILED", error={"code": None, "message": f"provider unreachable ({name}); nothing was sent"})
    if isinstance(ex, TimeoutError) or "Timeout" in name:
        return _result("UNKNOWN", error={"code": None, "message": f"timeout ({name}); outcome unknown, not retried"})
    return _result("UNKNOWN", error={"code": None, "message": f"ambiguous provider error ({name}); not retried"})


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _masked(e164: str) -> str:
    return f"•••• {e164[-4:]}"


# ----------------------------------------------------------------------------- simulated provider
class SimulatedProvider:
    """Records what would be sent. `failures` maps an idempotency key to an outcome, or is a callable
    (channel, to_e164, idempotency_key) -> outcome | None. Outcomes: REJECTED, FAILED, TIMEOUT (-> UNKNOWN),
    FAIL_AFTER_ACCEPT (accepted, then a FAILED callback). With `store`, the outbox is also kept in the ops store
    (collection sim_outbox) so it survives between requests."""
    name = "simulated"
    mode = "SIMULATED"

    def __init__(self, failures: Union[dict, Callable, None] = None, store=None):
        self.failures = failures or {}
        self.store = store
        self.outbox: list[dict] = []
        self._outcome: dict[str, str] = {}

    def _failure(self, channel, to, key) -> Optional[str]:
        if callable(self.failures):
            return self.failures(channel, to, key)
        return self.failures.get(key)

    def send(self, channel: str, to_e164: str, body: str, idempotency_key: str, **_) -> dict:
        mid = "SIM-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:20]
        outcome = self._failure(channel, to_e164, idempotency_key)
        if outcome == "REJECTED":
            return _result("REJECTED", error={"code": "SIM-REJECT", "message": "simulated provider rejection"})
        if outcome == "FAILED":
            return _result("FAILED", error={"code": "SIM-FAIL", "message": "simulated provider failure; nothing sent"})
        if outcome == "TIMEOUT":
            return _result("UNKNOWN", error={"code": "SIM-TIMEOUT", "message": "simulated timeout; outcome unknown"})
        self._outcome[mid] = "FAILED" if outcome == "FAIL_AFTER_ACCEPT" else "DELIVERED"
        # a demonstration number (+9100000...) may be shown masked; a real number leaves no digits here (QA M7)
        shown = _masked(to_e164) if str(to_e164).startswith("+9100000") else "(real number withheld)"
        rec = {"label": "SIMULATED", "channel": channel, "to": shown, "body": body,
               "idempotency_key": idempotency_key, "provider_message_id": mid,
               "final_outcome": self._outcome[mid]}
        if not any(o["provider_message_id"] == mid for o in self.outbox):
            self.outbox.append(rec)
        if self.store is not None:
            self.store.insert_unique("sim_outbox", mid, rec, actor="simulated-provider", action="simulated_send")
        return _result("ACCEPTED", mid, raw="simulated-accepted")

    def callbacks(self, provider_message_id: str) -> list[dict]:
        outcome = self._outcome.get(provider_message_id)
        if outcome is None and self.store is not None:
            doc = self.store.get("sim_outbox", provider_message_id)
            outcome = doc.get("final_outcome") if doc else None
        if outcome is None:
            return []
        return [{"provider": self.name, "provider_message_id": provider_message_id, "status": s, "raw_status": s.lower(),
                 "simulated": True} for s in ("SENT", outcome)]


# ----------------------------------------------------------------------------- Twilio (UNVERIFIED live)
def _httpx_post(url: str, **kw):
    import httpx
    r = httpx.post(url, timeout=kw.pop("timeout", TIMEOUT_S), **kw)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


def _httpx_get(url: str, **kw):
    import httpx
    r = httpx.get(url, timeout=kw.pop("timeout", TIMEOUT_S), **kw)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {}


class TwilioProvider:
    name = "twilio"
    mode = "LIVE"

    def __init__(self, *, account_sid: str, auth: tuple, sms_from: str = "", whatsapp_from: str = "",
                 http_post: Callable = _httpx_post, status_callback_url: Optional[str] = None,
                 http_get: Callable = _httpx_get):
        self.account_sid, self.auth = account_sid, auth
        self.sms_from, self.whatsapp_from = sms_from, whatsapp_from
        self.http_post, self.status_callback_url, self.http_get = http_post, status_callback_url, http_get

    @classmethod
    def from_env(cls, http_post: Callable = _httpx_post, http_get: Callable = _httpx_get) -> "TwilioProvider":
        c = twilio_env()
        base = _env("FLOODNET_PUBLIC_BASE_URL").rstrip("/")
        return cls(account_sid=c["account"], auth=c["auth"], sms_from=c["sms_from"], whatsapp_from=c["whatsapp_from"],
                   http_post=http_post, status_callback_url=f"{base}/api/ops/providers/twilio/status" if base else None,
                   http_get=http_get)

    def fetch_status(self, provider_message_id: str) -> dict:
        """Ask Twilio for a message's status (GET Messages/{sid}.json): for a server that cannot be called back.
        {"status": FloodNet status or None, "raw_status": Twilio's word, "error": {"code", "message"} or None}."""
        try:
            code, payload = self.http_get(f"{TWILIO_API}/Accounts/{self.account_sid}/Messages/{provider_message_id}.json",
                                          auth=self.auth, timeout=TIMEOUT_S)
        except Exception as ex:  # noqa: BLE001 -- never echo the request
            return {"status": None, "raw_status": "", "error": {"code": None, "message": scrub(type(ex).__name__)}}
        payload = payload or {}
        if code != 200:
            return {"status": None, "raw_status": "", "error": {"code": payload.get("code"), "message": f"Twilio HTTP {code}"}}
        raw = str(payload.get("status") or "")
        err = ({"code": payload.get("error_code"), "message": scrub(payload.get("error_message") or "")}
               if payload.get("error_code") else None)
        return {"status": map_twilio_status(raw), "raw_status": raw, "error": err}

    def send(self, channel: str, to_e164: str, body: str, idempotency_key: str, **_) -> dict:
        if channel == "whatsapp":
            sender, to = self.whatsapp_from, "whatsapp:" + to_e164
        elif channel == "sms":
            sender, to = self.sms_from, to_e164
        else:
            return _result("REJECTED", error={"code": None, "message": f"unsupported channel {channel}"})
        if not sender:
            return _result("REJECTED", error={"code": None, "message": f"no Twilio sender configured for {channel}"})
        data = {"From": sender, "To": to, "Body": body}
        if self.status_callback_url:
            data["StatusCallback"] = self.status_callback_url
        try:
            code, payload = self.http_post(f"{TWILIO_API}/Accounts/{self.account_sid}/Messages.json",
                                           auth=self.auth, data=data, timeout=TIMEOUT_S)
        except Exception as ex:  # noqa: BLE001 -- never echo the request (credentials, number)
            return _exception_result(ex)
        payload = payload or {}
        if code in (200, 201) and payload.get("sid"):
            return _result("ACCEPTED", str(payload["sid"]), raw=str(payload.get("status") or ""))
        if code >= 500 or code == 429:
            return _result("UNKNOWN", error={"code": payload.get("code"), "message": f"Twilio HTTP {code}; not retried"})
        return _result("REJECTED", error={"code": payload.get("code"),
                                          "message": scrub(payload.get("message") or f"Twilio HTTP {code}")})


def twilio_env() -> dict:
    account = _env("TWILIO_ACCOUNT_SID")
    key, secret, token = _env("TWILIO_API_KEY_SID"), _env("TWILIO_API_KEY_SECRET"), _env("TWILIO_AUTH_TOKEN")
    auth = (key, secret) if key and secret else ((account, token) if account and token else None)
    return {"account": account, "auth": auth, "sms_from": _env("TWILIO_SMS_FROM"),
            "whatsapp_from": _env("TWILIO_WHATSAPP_FROM"), "ok_account": account.startswith("AC") and auth is not None}


# ----------------------------------------------------------------------------- MSG91 (UNVERIFIED live)
class Msg91Provider:
    name = "msg91"
    mode = "LIVE"

    def __init__(self, *, authkey: str, template_id: str, http_post: Callable = _httpx_post):
        self.authkey, self.template_id, self.http_post = authkey, template_id, http_post

    @classmethod
    def from_env(cls, http_post: Callable = _httpx_post) -> "Msg91Provider":
        return cls(authkey=_env("MSG91_AUTHKEY"), template_id=_env("MSG91_TEMPLATE_ID"), http_post=http_post)

    def send(self, channel: str, to_e164: str, body: str, idempotency_key: str,
             variables: Optional[dict] = None, **_) -> dict:
        if channel != "sms":
            return _result("REJECTED", error={"code": None, "message": "the MSG91 adapter is SMS-only"})
        # A DLT template has fixed text; only its variables are sent. Without explicit variables the body goes in VAR1,
        # which a real DLT template is unlikely to accept (UNVERIFIED).
        recipient = {"mobiles": to_e164.lstrip("+"), **(variables or {"VAR1": body})}
        # CRQID: "returned in reports and webhooks" (docs.msg91.com/sms/send-sms, per docs/RESEARCH_ALERTING_AND_
        # INTERNATIONAL.md): our delivery id, so a report can be matched even without requestId. Its placement at the
        # top level of the Flow request is [UNVERIFIED] against the live API.
        payload = {"template_id": self.template_id, "short_url": "0", "recipients": [recipient],
                   "CRQID": idempotency_key}
        try:
            code, resp = self.http_post(MSG91_FLOW_URL, headers={"authkey": self.authkey, "accept": "application/json",
                                                                 "content-type": "application/json"},
                                        json=payload, timeout=TIMEOUT_S)
        except Exception as ex:  # noqa: BLE001
            return _exception_result(ex)
        resp = resp or {}
        if code == 200 and str(resp.get("type")).lower() == "success":
            return _result("ACCEPTED", str(resp.get("message") or resp.get("request_id") or ""), raw="success")
        if code >= 500 or code == 429:
            return _result("UNKNOWN", error={"code": None, "message": f"MSG91 HTTP {code}; not retried"})
        return _result("REJECTED", error={"code": resp.get("code"), "message": scrub(resp.get("message") or f"HTTP {code}")})


# ----------------------------------------------------------------------------- the live gate
def _sms_provider_name() -> str:
    return (_env("FLOODNET_SMS_PROVIDER") or "twilio").lower()


def _credentials_present(channel: str) -> tuple[bool, str]:
    if channel == "sms" and _sms_provider_name() == "msg91":
        ok = bool(_env("MSG91_AUTHKEY") and _env("MSG91_TEMPLATE_ID"))
        return ok, "MSG91_AUTHKEY and MSG91_TEMPLATE_ID"
    c = twilio_env()
    sender = c["sms_from"] if channel == "sms" else c["whatsapp_from"]
    return (c["ok_account"] and bool(sender),
            "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN (or API key pair) and " +
            ("TWILIO_SMS_FROM" if channel == "sms" else "TWILIO_WHATSAPP_FROM"))


def _allow_list_hmacs() -> set[str]:
    from .subscribers import normalize_input, number_hmac
    out = set()
    for raw in _env("FLOODNET_MESSAGING_ALLOWLIST").split(","):
        if raw.strip():
            try:
                out.add(number_hmac(normalize_input(raw)))
            except ValueError:
                continue
    return out


def live_sending_allowed(channel: str, to_e164: str, *, purpose: str = "alert") -> tuple[bool, str]:
    """(allowed, reason). The reason never contains the number. purpose="test" (the dispatcher's "Send test SMS")
    also accepts the designated development test recipients of FLOODNET_DEV_TEST_RECIPIENTS (never in production);
    advisories (purpose="alert") use FLOODNET_MESSAGING_ALLOWLIST only."""
    if _env("FLOODNET_LIVE_MESSAGING") != "1":
        return False, "SIMULATED: live messaging is off (FLOODNET_LIVE_MESSAGING is not 1)"
    ok, names = _credentials_present(channel)
    if not ok:
        return False, f"SIMULATED: provider credentials missing for {channel} ({names})"
    # an advisory carries a link to its public page and wants delivery callbacks; a test SMS has no link and its
    # status can be asked for (TwilioProvider.fetch_status), so it does not need a public address
    if purpose != "test" and not _env("FLOODNET_PUBLIC_BASE_URL"):
        return False, "SIMULATED: FLOODNET_PUBLIC_BASE_URL is not configured"
    from .subscribers import number_hmac
    try:
        h = number_hmac(to_e164)
    except ValueError as ex:
        return False, f"SIMULATED: {ex}"
    via = "msg91" if channel == "sms" and _sms_provider_name() == "msg91" else "twilio"
    if h in _allow_list_hmacs():
        return True, f"LIVE: {channel} via {via}; recipient is on the allow-list"
    if purpose == "test":
        from .devtest import dev_hmacs
        if h in dev_hmacs():
            return True, f"LIVE: {channel} via {via}; designated development test recipient (test send)"
        return False, ("SIMULATED: recipient is neither on the allow-list nor a designated development test recipient "
                       "(FLOODNET_DEV_TEST_RECIPIENTS)")
    return False, "SIMULATED: recipient is not on the authorised test allow-list (FLOODNET_MESSAGING_ALLOWLIST)"


def choose_provider(channel: str, to_e164: str, *, simulated: bool, http_post: Optional[Callable] = None,
                    simulated_provider: Optional[SimulatedProvider] = None, purpose: str = "alert"):
    """(provider, mode, reason). SIMULATED unless every live condition holds; simulated subscribers never go live."""
    sim = simulated_provider or SimulatedProvider()
    if simulated:
        return sim, "SIMULATED", "SIMULATED: simulated subscriber"
    ok, why = live_sending_allowed(channel, to_e164, purpose=purpose)
    if not ok:
        return sim, "SIMULATED", why
    kw = {"http_post": http_post} if http_post is not None else {}
    if channel == "sms" and _sms_provider_name() == "msg91":
        return Msg91Provider.from_env(**kw), "LIVE", why
    return TwilioProvider.from_env(**kw), "LIVE", why
