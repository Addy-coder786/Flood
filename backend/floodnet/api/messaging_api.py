"""Subscriber advisories, provider webhooks and the public alert page API (DECISIONS D-44, D-45, D-46).
Design: docs/LIVE_OPERATIONS.md §6-§7, docs/MESSAGING_AND_PUBLIC_ALERTS.md. Include with app.include_router(router).

  dispatcher  /api/ops/subscribers..., /api/ops/alerts..., /api/ops/official-alerts/refresh, /api/ops/messaging/status
  providers   /api/ops/providers/... : signed (Twilio X-Twilio-Signature, WhatsApp X-Hub-Signature-256) or, for MSG91
              (no documented signature), a shared secret header. Unconfigured -> 403, unsigned -> 401, invalid -> 403.
  public      /api/public/alerts/{public_id}, /api/public/official-alerts (no token; whitelisted data only)
Errors: store unavailable or PII key missing 503, bad input 422, unknown id 404, wrong state 409.
No endpoint ever returns a phone number; messages go to the SIMULATED provider unless the live gate allows otherwise.
"""
from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from ..messaging import alerts as al
from ..messaging import devtest as dt
from ..messaging import providers as pv
from ..messaging import public as pub
from ..messaging import sachet
from ..messaging import subscribers as subs
from ..ops.auth import require_dispatcher
from ..ops.store import OpsUnavailable, get_store, iso, utc_now

router = APIRouter()
PUBLIC_HEADERS = {"Cache-Control": "public, max-age=30", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"}
NOT_FOUND = {"detail": "not found"}
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")


# ------------------------------------------------------------------ plumbing
def _store():
    try:
        return get_store()
    except OpsUnavailable as e:
        raise HTTPException(503, str(e))


def _errors(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except subs.PIIUnavailable as e:
        raise HTTPException(503, str(e))
    except dt.NotAllowed as e:
        raise HTTPException(403, str(e))
    except (al.StateError, subs.Conflict) as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, f"not found: {str(e).strip(chr(39))}")
    except ValueError as e:
        raise HTTPException(422, str(e))


def provider_factory(store):
    """(channel, to_e164, simulated) -> (provider, mode, reason). SIMULATED unless providers.live_sending_allowed.
    Tests replace this function."""
    return al._default_factory(store)


def dev_test_provider_factory(store):
    """The same gate for the dispatcher's test sends (purpose="test": designated development test recipients count as
    allow-listed). Tests replace this function."""
    sim = pv.SimulatedProvider(store=store)
    return lambda channel, to, simulated: pv.choose_provider(channel, to, simulated=simulated, simulated_provider=sim,
                                                             purpose="test")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _public_base(request: Request) -> tuple[str, str]:
    configured = _env("FLOODNET_PUBLIC_BASE_URL").rstrip("/")
    if configured:
        return configured, "FLOODNET_PUBLIC_BASE_URL"
    origin = (request.headers.get("origin") or "").rstrip("/")
    if _ORIGIN.match(origin):
        return origin, "request Origin (local development)"
    return str(request.base_url).rstrip("/"), "request base URL (local development)"


def _signed_url(request: Request) -> str:
    """The URL Twilio signed: the configured public base + path (behind a proxy) or the URL as received."""
    base = _env("FLOODNET_PUBLIC_BASE_URL").rstrip("/")
    if base:
        q = f"?{request.url.query}" if request.url.query else ""
        return f"{base}{request.url.path}{q}"
    return str(request.url)


def _alert_out(store, a: dict) -> dict:
    return al.dispatcher_view(store, a)


# ------------------------------------------------------------------ subscribers (dispatcher)
@router.get("/api/ops/messaging/status")
def messaging_status(_: str = Depends(require_dispatcher)):
    tw = pv.twilio_env()
    return {"live_messaging_flag": _env("FLOODNET_LIVE_MESSAGING") == "1",
            "default_mode": "SIMULATED",
            "twilio_credentials_present": bool(tw["ok_account"]),
            "msg91_credentials_present": bool(_env("MSG91_AUTHKEY") and _env("MSG91_TEMPLATE_ID")),
            "public_base_url_configured": bool(_env("FLOODNET_PUBLIC_BASE_URL")),
            "allow_list_configured": bool(_env("FLOODNET_MESSAGING_ALLOWLIST")),
            "pii_key_configured": bool(_env("FLOODNET_PII_KEY")),
            "sms_sender_configured": bool(tw["sms_from"]) or bool(_env("MSG91_AUTHKEY") and _env("MSG91_TEMPLATE_ID")),
            "whatsapp_sender_configured": bool(tw["whatsapp_from"]),
            "dev_test": dt.status(),
            "sms_note": "Real SMS: BLOCKED until a sender is configured (TWILIO_SMS_FROM or the MSG91 keys). MSG91 needs "
                        "DLT registration and approved templates; Twilio's international route to India (random "
                        "numeric sender, no DLT) needs an upgraded, not trial, account.",
            "whatsapp_note": dt.CHANNEL_NOTES["whatsapp"],
            "note": "Live sends need FLOODNET_LIVE_MESSAGING=1, credentials, a public base URL and an allow-listed "
                    "recipient (or, for test sends, a designated development test recipient). " + al.TRANSLATION_NOTE}


@router.post("/api/ops/subscribers", status_code=201)
def create_subscriber(response: Response, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    view, created = _errors(subs.create, store, body or {}, actor=who)
    if not created:
        response.status_code = 200
    return {**view, "created": created}


@router.get("/api/ops/subscribers")
def list_subscribers(_: str = Depends(require_dispatcher)):
    store = _store()
    return {"subscribers": subs.list_subscribers(store), "notice_version": subs.NOTICE_VERSION,
            "retention_note": subs.RETENTION_NOTE, "localities": subs.PILOT_LOCALITIES}


@router.post("/api/ops/subscribers/seed-simulated", status_code=201)
def seed_subscribers(who: str = Depends(require_dispatcher)):
    store = _store()
    return {"subscribers": _errors(subs.seed_simulated, store, actor=who), "label": "SIMULATED"}


@router.post("/api/ops/subscribers/seed-dev-test", status_code=201)
def seed_dev_test(body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    """Designated development test recipients from FLOODNET_DEV_TEST_RECIPIENTS (server only; refused in production)."""
    if dt.production():
        raise HTTPException(403, f"seeding development test recipients is refused: {dt.production()}")
    store = _store()
    return _errors(dt.seed, store, actor=who, locality_id=str((body or {}).get("locality_id") or ""))


@router.patch("/api/ops/subscribers/{sub_id}")
def update_subscriber(sub_id: str, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    return _errors(subs.update, store, sub_id, body or {}, actor=who)


# ------------------------------------------------------------------ test sends (dispatcher; dev test recipients only)
@router.post("/api/ops/test-sends/preview")
def preview_test_send(body: dict = Body(default={}), _: str = Depends(require_dispatcher)):
    store = _store()
    body = body or {}
    return _errors(dt.preview, store, str(body.get("subscriber_id") or ""), str(body.get("channel") or "sms"),
                   dev_test_provider_factory(store))


@router.post("/api/ops/test-sends", status_code=201)
def create_test_send(response: Response, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    body = body or {}
    out = _errors(dt.send, store, str(body.get("subscriber_id") or ""), channel=str(body.get("channel") or "sms"),
                  idempotency_key=str(body.get("idempotency_key") or ""), text_sha256=str(body.get("text_sha256") or ""),
                  actor=who, factory=dev_test_provider_factory(store),
                  expected_mode=(str(body["expected_mode"]) if body.get("expected_mode") else None))
    if out.get("duplicate"):
        response.status_code = 200
    return out


@router.get("/api/ops/test-sends")
def list_test_sends(_: str = Depends(require_dispatcher)):
    store = _store()
    return {"sends": dt.list_sends(store), "status_meaning": pv.STATUS_MEANING, "text": dt.TEST_TEXT}


@router.post("/api/ops/test-sends/{delivery_id}/retry")
def retry_test_send(delivery_id: str, who: str = Depends(require_dispatcher)):
    store = _store()
    return _errors(dt.retry, store, delivery_id, actor=who, factory=dev_test_provider_factory(store))


@router.post("/api/ops/test-sends/{delivery_id}/refresh-status")
def refresh_test_send(delivery_id: str, _: str = Depends(require_dispatcher)):
    """Ask the provider for a LIVE test send's status (no callback can reach a local server)."""
    store = _store()
    return _errors(dt.refresh_status, store, delivery_id, provider=pv.TwilioProvider.from_env())


@router.post("/api/ops/test-sends/{delivery_id}/simulate-callbacks")
def simulate_test_callbacks(delivery_id: str, _: str = Depends(require_dispatcher)):
    store = _store()
    return _errors(dt.simulate_callbacks, store, delivery_id)


@router.post("/api/ops/subscribers/{sub_id}/opt-out")
def opt_out_subscriber(sub_id: str, who: str = Depends(require_dispatcher)):
    store = _store()
    return _errors(subs.opt_out, store, sub_id, method="dispatcher", actor=who)


@router.delete("/api/ops/subscribers/{sub_id}")
def erase_subscriber(sub_id: str, who: str = Depends(require_dispatcher)):
    store = _store()
    return _errors(subs.erase, store, sub_id, actor=who)


# ------------------------------------------------------------------ alerts (dispatcher)
@router.post("/api/ops/alerts/draft", status_code=201)
def draft_alert(request: Request, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    body = body or {}
    base, source = _public_base(request)
    # The dashboard may name its own base path (e.g. http://localhost:5173/static when the frontend is served under
    # /static/). Accepted only without FLOODNET_PUBLIC_BASE_URL and only from a local dashboard (localhost /
    # 127.0.0.1) on the request's own origin; production links come from FLOODNET_PUBLIC_BASE_URL.
    asked = str(body.get("public_base") or "").rstrip("/")
    origin = (request.headers.get("origin") or "").rstrip("/")
    if asked and not _env("FLOODNET_PUBLIC_BASE_URL"):
        from ..ops.auth import LOOPBACK, _hostname
        if origin and (asked == origin or asked.startswith(origin + "/")) and _ORIGIN.match(origin) \
                and _hostname(origin) in LOOPBACK:
            base, source = asked, "dashboard base path on the request origin (local development)"
        else:
            raise HTTPException(422, "public_base must be on the request's own origin")
    try:
        window = float(body.get("window_min", 60))
    except (TypeError, ValueError):
        raise HTTPException(422, "window_min must be a number")
    a = _errors(al.create_draft, store, window_min=window, public_base_url=base, actor=who,
                title=body.get("title"), locality=body.get("locality") or None,
                channels=body.get("channels") or al.CHANNELS)
    return {**_alert_out(store, a), "public_base_url_source": source}


@router.get("/api/ops/alerts")
def list_alerts(_: str = Depends(require_dispatcher)):
    store = _store()
    return {"alerts": [{k: a.get(k) for k in ("alert_id", "public_id", "link", "title", "state", "current_version",
                                               "approved_version", "sent_versions", "_updated_at")}
                       for a in al.list_alerts(store)]}


@router.get("/api/ops/alerts/{alert_id}")
def get_alert(alert_id: str, _: str = Depends(require_dispatcher)):
    store = _store()
    return _alert_out(store, _errors(al.get_alert, store, alert_id))


@router.post("/api/ops/alerts/{alert_id}/submit-review")
def submit_review(alert_id: str, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    return _alert_out(store, _errors(al.submit_review, store, alert_id, actor=who, note=(body or {}).get("note", "")))


@router.post("/api/ops/alerts/{alert_id}/approve")
def approve(alert_id: str, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    body = body or {}
    v = body.get("version")
    if not isinstance(v, int) or isinstance(v, bool):
        a = _errors(al.get_alert, store, alert_id)
        if a["state"] != "REVIEW_REQUIRED":           # wrong state first: same answer as before (409)
            _errors(al.approve, store, alert_id, actor=who, approver_name=body.get("approver_name"))
        raise HTTPException(422, "approve needs the 'version' you reviewed (QA M8)")
    return _alert_out(store, _errors(al.approve, store, alert_id, actor=who, approver_name=body.get("approver_name"),
                                     version=v))


@router.post("/api/ops/alerts/{alert_id}/refresh")
def refresh(alert_id: str, who: str = Depends(require_dispatcher)):
    store = _store()
    out = _errors(al.refresh, store, alert_id, actor=who)
    return {"changed": out["changed"], "reason": out["reason"], "alert": _alert_out(store, out["alert"])}


@router.post("/api/ops/alerts/{alert_id}/send")
def send(alert_id: str, who: str = Depends(require_dispatcher)):
    store = _store()
    out = _errors(al.send, store, alert_id, actor=who, provider_factory=provider_factory(store))
    return {**_alert_out(store, al.get_alert(store, alert_id)), "already_sent": out.get("already_sent", False),
            "delivery_counts": out.get("deliveries")}


@router.post("/api/ops/alerts/{alert_id}/cancel")
def cancel(alert_id: str, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    return _alert_out(store, _errors(al.cancel, store, alert_id, actor=who, reason=(body or {}).get("reason", "")))


@router.post("/api/ops/alerts/{alert_id}/resolve")
def resolve(alert_id: str, body: dict = Body(default={}), who: str = Depends(require_dispatcher)):
    store = _store()
    _errors(al.resolve, store, alert_id, actor=who, provider_factory=provider_factory(store),
            note=(body or {}).get("note", ""))
    return _alert_out(store, al.get_alert(store, alert_id))


@router.post("/api/ops/alerts/{alert_id}/simulate-callbacks")
def simulate_callbacks(alert_id: str, _: str = Depends(require_dispatcher)):
    store = _store()
    _errors(al.get_alert, store, alert_id)
    return al.simulate_callbacks(store, alert_id, pv.SimulatedProvider(store=store))


# ------------------------------------------------------------------ official alerts
@router.post("/api/ops/official-alerts/refresh")
def refresh_official(_: str = Depends(require_dispatcher)):
    url = _env("SACHET_RSS_URL")
    if not url:
        raise HTTPException(409, "SACHET_RSS_URL is not configured; no feed is fetched")
    store = _store()
    try:
        return sachet.refresh(store, url)
    except ValueError as e:
        raise HTTPException(502, f"SACHET feed: {e}")


@router.get("/api/public/official-alerts")
def official_alerts():
    store = _store()
    return JSONResponse({"label": sachet.LABEL, "items": sachet.list_cached(store),
                         "note": "Cached copy of the public SACHET (NDMA) feed. FloodNet does not issue official alerts."},
                        headers=PUBLIC_HEADERS)


# ------------------------------------------------------------------ public alert page
@router.get("/api/public/alerts/{public_id}")
def public_alert(public_id: str, v: Optional[str] = None):
    store = _store()
    version = None
    if v is not None:
        if not (v.isascii() and v.isdigit() and len(v) <= 6):
            return JSONResponse(NOT_FOUND, status_code=404, headers=PUBLIC_HEADERS)
        version = int(v)
    snap = pub.snapshot_for_public_id(store, public_id, version)
    if snap is None:
        return JSONResponse(NOT_FOUND, status_code=404, headers=PUBLIC_HEADERS)
    return JSONResponse(snap, headers=PUBLIC_HEADERS)


# ------------------------------------------------------------------ provider webhooks
async def _form(request: Request) -> dict:
    raw = (await request.body()).decode("utf-8", "replace")
    return dict(parse_qsl(raw, keep_blank_values=True))


def _check_twilio(request: Request, params: dict) -> None:
    token = _env("TWILIO_AUTH_TOKEN")
    if not token:
        raise HTTPException(403, "Twilio webhook not configured (TWILIO_AUTH_TOKEN)")
    sig = request.headers.get("x-twilio-signature")
    if not sig:
        raise HTTPException(401, "missing X-Twilio-Signature")
    if not pv.verify_twilio(_signed_url(request), params, sig, token):
        raise HTTPException(403, "invalid X-Twilio-Signature")


@router.post("/api/ops/providers/twilio/status")
async def twilio_status(request: Request):
    params = await _form(request)
    _check_twilio(request, params)
    store = _store()
    mid, raw = params.get("MessageSid") or params.get("SmsSid") or "", params.get("MessageStatus") or ""
    if not mid:
        raise HTTPException(422, "MessageSid missing")
    err = {"code": params.get("ErrorCode"), "message": "provider error code"} if params.get("ErrorCode") else None
    return al.apply_callback(store, provider="twilio", provider_message_id=mid, status=pv.map_twilio_status(raw),
                             raw_status=raw, error=err)


@router.post("/api/ops/providers/twilio/inbound")
async def twilio_inbound(request: Request):
    params = await _form(request)
    _check_twilio(request, params)
    store = _store()
    al.handle_inbound(store, params.get("From") or "", params.get("Body") or "")
    # Empty TwiML: no reply text, and nothing that tells the caller whether the number was known.
    return Response('<?xml version="1.0" encoding="UTF-8"?><Response></Response>', media_type="application/xml")


@router.post("/api/ops/providers/msg91/dlr")
async def msg91_dlr(request: Request):
    secret = _env("MSG91_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(403, "MSG91 webhook not configured (MSG91_WEBHOOK_SECRET)")
    presented = request.headers.get("x-floodnet-webhook-secret")
    if not presented:
        raise HTTPException(401, "missing X-FloodNet-Webhook-Secret")
    if not pv.verify_shared_secret(presented, secret):
        raise HTTPException(403, "invalid X-FloodNet-Webhook-Secret")
    # Authenticated from here on: always 200 (MSG91 auto-pauses a webhook that answers 4xx/5xx, docs/RESEARCH_
    # ALERTING_AND_INTERNATIONAL.md). Reports carry the recipient's number (telNum): it is never stored or logged.
    import json
    raw = await request.body()
    try:
        store = get_store()
    except OpsUnavailable:
        return {"results": [], "recorded": False, "note": "live-operations store unavailable; report not recorded"}
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        body = None
    reports = pv.msg91_reports(body) if body is not None else []
    if not reports:
        outcome = "unparseable" if body is None else "no_reports"
        store.put("provider_webhook_events", f"msg91-{store.next_seq('webhook_event')}",
                  {"provider": "msg91", "outcome": outcome, "bytes": len(raw), "received_at": iso(utc_now())},
                  actor="provider:msg91", action=f"webhook_{outcome}")
        return {"results": [], "recorded": True, "outcome": outcome}
    results = []
    for rep in reports:
        status, detail = pv.map_msg91_status(rep["status_code"])
        r = al.apply_callback(store, provider="msg91", provider_message_id=rep["request_id"], status=status,
                              raw_status=str(rep["status_code"])[:20], delivery_id=rep["crqid"],
                              error={"code": rep["status_code"], "message": detail} if status in pv.FAILURES else None)
        results.append(r)
    return {"results": results, "recorded": True}


@router.get("/api/ops/providers/whatsapp/webhook")
def whatsapp_verify(request: Request):
    expected = _env("WHATSAPP_VERIFY_TOKEN")
    q = request.query_params
    if not expected or q.get("hub.mode") != "subscribe" or not pv.verify_shared_secret(q.get("hub.verify_token"), expected):
        raise HTTPException(403, "verification failed")
    return PlainTextResponse(str(q.get("hub.challenge") or "")[:200])


@router.post("/api/ops/providers/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    secret = _env("WHATSAPP_APP_SECRET")
    if not secret:
        raise HTTPException(403, "WhatsApp webhook not configured (WHATSAPP_APP_SECRET)")
    sig = request.headers.get("x-hub-signature-256")
    if not sig:
        raise HTTPException(401, "missing X-Hub-Signature-256")
    raw = await request.body()
    if not pv.verify_whatsapp(raw, sig, secret):
        raise HTTPException(403, "invalid X-Hub-Signature-256")
    import json
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HTTPException(422, "JSON body expected")
    store = _store()
    n_status = n_stop = 0
    for entry in body.get("entry", []) if isinstance(body, dict) else []:
        for change in entry.get("changes", []):
            value = change.get("value", {}) or {}
            for st in value.get("statuses", []) or []:
                errs = st.get("errors") or []
                al.apply_callback(store, provider="whatsapp_cloud", provider_message_id=str(st.get("id") or ""),
                                  status=pv.map_whatsapp_status(st.get("status")), raw_status=str(st.get("status")),
                                  error={"code": errs[0].get("code"), "message": "provider error"} if errs else None)
                n_status += 1
            for msg in value.get("messages", []) or []:
                text = ((msg.get("text") or {}).get("body")) or ""
                if subs.is_stop_keyword(text):
                    al.handle_inbound(store, "+" + str(msg.get("from") or "").lstrip("+"), text)
                    n_stop += 1
    return {"statuses": n_status, "stops": n_stop}
