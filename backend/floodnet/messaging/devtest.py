"""Designated development test recipients and the dispatcher's "Send test SMS" (2026-10-11).

Who: ONLY the numbers in the server-side variable FLOODNET_DEV_TEST_RECIPIENTS, set by the project owner
  (comma-separated; each entry a number with an optional label after ':', e.g. "<number>:Owner"). Indian numbers may be
  written in any form normalize_input() accepts. Channels: FLOODNET_DEV_TEST_CHANNELS (default "sms"). Nothing here,
  in the frontend or in the docs holds a number; the API never returns one (last four digits only).
Seeding: a dispatcher action turns each configured number into an ordinary subscriber (encrypted in the PII vault) with
  consent method "designated development test recipient", verified, dev_test=True, in a pilot locality the dispatcher
  chooses (editable later). Refused in production (VERCEL=1 or FLOODNET_ENV=production), where the list is also ignored.
Test sends: only to a dev_test subscriber whose number is STILL in the variable, who is ACTIVE and has consent for the
  channel. The text is fixed (TEST_TEXT); the dispatcher sees it, its length and SMS segment count first, and the send
  must quote the preview's text_sha256. The provider is chosen by providers.choose_provider(purpose="test"): a real
  send only with FLOODNET_LIVE_MESSAGING=1, credentials and the recipient designated here or allow-listed (a test SMS
  has no link, so no public base URL is needed); otherwise the SimulatedProvider, and the record says SIMULATED.
Status: without a public address the provider cannot call back; refresh_status() asks the provider (Twilio) and
  moves the record forward only. ACCEPTED/SENT are never shown as delivered.
Idempotency: the browser sends one key per confirmation; the delivery id is TEST:<key> (store.insert_unique), so a
  double click returns the first record and never sends twice. A record left SUBMITTED (process died during the
  provider call) becomes UNKNOWN after STALE_SUBMITTED_S and is never re-sent blindly.
Retry: only FAILED with no provider message id (the provider never took it, so nothing was sent), at most
  MAX_ATTEMPTS attempts in all, each with its own provider idempotency key. REJECTED, UNKNOWN and failures after
  acceptance are not retried.
Records: collection `deliveries` (kind "test", alert_id None), so signed provider callbacks update them like advisory
  deliveries; every step is in the append-only audit with the dispatcher as actor.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

from ..ops.store import OpsStore, iso, utc_now
from . import alerts as al
from . import providers as pv
from . import subscribers as subs

ENV_VAR = "FLOODNET_DEV_TEST_RECIPIENTS"
CHANNELS_ENV = "FLOODNET_DEV_TEST_CHANNELS"
CONSENT_METHOD = "designated development test recipient"
# The project owner's wording for the real SMS test (2026-10-11): explicitly a test, not an emergency alert.
TEST_TEXT = "FloodNet SMS Test: Your SMS integration is working. This is a test message, not an emergency alert."
MAX_ATTEMPTS = 3
STALE_SUBMITTED_S = 120          # longer than the provider timeout (providers.TIMEOUT_S) with a wide margin
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
GSM_EXTENSION = frozenset("^{}\\[~]|€\f")
CHANNEL_NOTES = {
    "sms": ("Real SMS is BLOCKED until a sender is configured: Twilio needs TWILIO_SMS_FROM and, for India, an upgraded "
            "(not trial) account using Twilio's international route (random numeric sender, no DLT); MSG91 needs DLT "
            "registration and an approved template (MSG91_AUTHKEY, MSG91_TEMPLATE_ID)."),
    "whatsapp": ("WhatsApp free text is delivered only within 24 h of the recipient's last message to the sending "
                 "number (Twilio Sandbox: after sending 'join <word>', and the Sandbox session ends 3 days after "
                 "joining). Outside that window an approved template is needed, which FloodNet does not implement: "
                 "expect the send to fail."),
}
Conflict = subs.Conflict


class NotAllowed(Exception):
    """Refused by policy (production, not a designated recipient, opted out): HTTP 403."""


# ----------------------------------------------------------------------------- configuration
def production() -> Optional[str]:
    """The reason this is a production deployment, or None."""
    if os.environ.get("VERCEL") == "1":
        return "this is a production deployment (VERCEL=1)"
    if (os.environ.get("FLOODNET_ENV") or "").strip().lower() in ("production", "prod"):
        return "this is a production deployment (FLOODNET_ENV=production)"
    return None


def _entries() -> tuple[list[dict], int]:
    """([{number, label}], invalid count). INTERNAL: the numbers never leave this process."""
    valid, invalid = [], 0
    for part in (os.environ.get(ENV_VAR) or "").split(","):
        part = part.strip()
        if not part:
            continue
        number, _, label = part.partition(":")
        try:
            e164 = subs.normalize_input(number)
        except ValueError:
            invalid += 1
            continue
        if all(v["number"] != e164 for v in valid):
            valid.append({"number": e164, "label": subs._clean_label(label)})
    return valid, invalid


def channels() -> list[str]:
    out = []
    for ch in (os.environ.get(CHANNELS_ENV) or "sms").split(","):
        ch = ch.strip().lower()
        if ch in subs.CHANNELS and ch not in out:
            out.append(ch)
    return out or ["sms"]


def status() -> dict:
    """Counts only (never a number): what the dispatcher page shows."""
    valid, invalid = _entries()
    prod = production()
    return {"env_var": ENV_VAR, "configured": bool((os.environ.get(ENV_VAR) or "").strip()), "valid": len(valid),
            "invalid": invalid, "channels": channels(), "production": prod is not None,
            "seeding_allowed": prod is None and bool(valid),
            "note": prod or ("Development test recipients come only from the server's " + ENV_VAR + "; the "
                             "number is never shown here.")}


def dev_hmacs() -> set[str]:
    """Keyed hashes of the configured numbers (empty in production or without a PII key)."""
    if production():
        return set()
    try:
        return {subs.number_hmac(v["number"]) for v in _entries()[0]}
    except subs.PIIUnavailable:
        return set()


# ----------------------------------------------------------------------------- seeding
def seed(store: OpsStore, *, actor: str, locality_id: str) -> dict:
    why = production()
    if why:
        raise NotAllowed(f"seeding development test recipients is refused: {why}")
    if locality_id not in subs.LOCALITY_BY_ID:
        raise ValueError(f"unknown pilot locality; choose one of {sorted(subs.LOCALITY_BY_ID)}")
    valid, invalid = _entries()
    if not valid:
        raise Conflict(f"{ENV_VAR} is not set on the server or holds no valid number ({invalid} invalid entr"
                       f"{'y' if invalid == 1 else 'ies'}); nothing to seed")
    chans = {ch: {"method": CONSENT_METHOD} for ch in channels()}
    seeded, existing, skipped = [], [], []
    for entry in valid:
        sid = subs.find_by_number(store, entry["number"])
        if sid is None:
            view, _ = subs.create(store, {"number": entry["number"], "channels": chans, "language": "en",
                                          "area": {"locality_id": locality_id}, "verified": True},
                                  actor=actor, dev_test=True, label=entry["label"])
            seeded.append(view)
            continue
        doc = store.get(subs.COLL, sid) or {}
        if doc.get("status") == "ACTIVE" and not doc.get("simulated"):
            existing.append(subs.mark_dev_test(store, sid, actor=actor, label=entry["label"]))
        else:
            existing.append(subs.get_public_view(store, sid))
            st = doc.get("status")
            skipped.append({"subscriber_id": sid, "reason": ("opted out: left unchanged (an opt-out is never undone "
                                                             "by seeding)") if st == "OPTED_OUT"
                            else f"{st}: left unchanged"})
    return {"seeded": seeded, "existing": existing, "skipped": skipped, "invalid": invalid,
            "label": "designated development test recipients (server configuration)"}


# ----------------------------------------------------------------------------- text
def sms_info(text: str) -> dict:
    """Characters, encoding and SMS segments: GSM-7 160 (153 per part when split; extension characters take two
    septets), otherwise UCS-2 70 (67 per part)."""
    text = str(text or "")
    if all(c in al.GSM_BASIC or c in GSM_EXTENSION for c in text):
        enc, units, single, multi = "GSM-7", sum(2 if c in GSM_EXTENSION else 1 for c in text), 160, 153
    else:
        enc, units, single, multi = "UCS-2", len(text.encode("utf-16-le")) // 2, 70, 67
    segments = 0 if units == 0 else 1 if units <= single else math.ceil(units / multi)
    return {"characters": len(text), "encoding": enc, "units": units, "segments": segments,
            "per_segment": single if segments <= 1 else multi}


def preview_sha256(text: str = TEST_TEXT) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- eligibility and views
def _eligible(store: OpsStore, subscriber_id: str, channel: str) -> str:
    """The number for a permitted test send, or an exception. Never returned to a caller outside this module."""
    why = production()
    if why:
        raise NotAllowed(f"test sends are refused: {why}")
    doc = store.get(subs.COLL, str(subscriber_id))
    if doc is None:
        raise KeyError(str(subscriber_id))
    if not doc.get("dev_test"):
        raise NotAllowed("only a designated development test recipient (server configuration) can receive a test "
                         "send")
    if doc.get("status") != "ACTIVE":
        raise NotAllowed(f"the recipient has opted out or was erased (status {doc.get('status')}); nothing sent")
    if channel not in subs.CHANNELS:
        raise ValueError(f"channel must be one of {subs.CHANNELS}")
    if channel not in (doc.get("channels") or {}):
        raise ValueError(f"no consent recorded for {channel} for this recipient")
    number = subs.reveal_number(store, doc["id"])
    if number is None:
        raise NotAllowed("no number held for this recipient (erased)")
    if subs.number_hmac(number) not in dev_hmacs():
        raise NotAllowed(f"this recipient is no longer listed in {ENV_VAR}; test sends are refused")
    return number


def _retryable(d: dict) -> bool:
    return (d.get("status") == "FAILED" and not d.get("provider_message_id")
            and len(d.get("attempts") or []) < MAX_ATTEMPTS)


_VIEW_KEYS = ("delivery_id", "kind", "subscriber_id", "channel", "status", "mode", "mode_reason", "provider",
              "provider_message_id", "error", "reason", "submitted_at", "requested_by", "requested_at", "attempts",
              "label", "status_history", "text_sha256")


def view(store: OpsStore, d: dict, *, duplicate: Optional[bool] = None) -> dict:
    sub = subs.get_public_view(store, d["subscriber_id"]) or {}
    out = {k: d.get(k) for k in _VIEW_KEYS}
    out.update(number_masked=sub.get("number_masked"), recipient_label=sub.get("label"),
               status_meaning=pv.STATUS_MEANING.get(d.get("status"), ""), retryable=_retryable(d),
               channel_note=CHANNEL_NOTES.get(d.get("channel"), ""))
    if duplicate is not None:
        out["duplicate"] = duplicate
    return out


def preview(store: OpsStore, subscriber_id: str, channel: str, factory: Callable) -> dict:
    number = _eligible(store, subscriber_id, channel)
    provider, mode, why = factory(channel, number, False)
    sub = subs.get_public_view(store, str(subscriber_id))
    return {"subscriber_id": sub["id"], "number_masked": sub["number_masked"], "recipient_label": sub["label"],
            "channel": channel, "text": TEST_TEXT, "text_sha256": preview_sha256(), **sms_info(TEST_TEXT),
            "mode": mode, "mode_reason": why, "provider": getattr(provider, "name", "unknown"),
            "channel_note": CHANNEL_NOTES.get(channel, "")}


# ----------------------------------------------------------------------------- sending
def _set(store: OpsStore, d: dict, status: str, now: datetime, actor: str, action: str, **fields) -> dict:
    d.update(fields)
    d["status"] = status
    d.setdefault("status_history", []).append({"status": status, "at": iso(now), "source": str(actor)[:60]})
    return store.put(al.DELIVERIES, d["delivery_id"], d, actor=actor, action=action)


def _duplicate(store: OpsStore, d: dict, subscriber_id: str, channel: str, now: datetime) -> dict:
    if d.get("kind") != "test" or d.get("subscriber_id") != subscriber_id or d.get("channel") != channel:
        raise Conflict("this idempotency key was already used for a different test send; confirm again")
    if d["status"] == "SUBMITTED" and d.get("submitted_at"):
        at = datetime.fromisoformat(d["submitted_at"].replace("Z", "+00:00"))
        if now - at > timedelta(seconds=STALE_SUBMITTED_S):
            with store.transaction():
                d = store.get(al.DELIVERIES, d["delivery_id"])
                if d["status"] == "SUBMITTED":
                    d = _set(store, d, "UNKNOWN", now, "floodnet", "test_send_unknown",
                             reason="the send was interrupted after submission; outcome unknown, not retried")
    return view(store, d, duplicate=True)


def _call(store: OpsStore, did: str, provider, channel: str, number: str, attempt_key: str, now: datetime) -> dict:
    try:
        res = provider.send(channel, number, TEST_TEXT, attempt_key)
    except Exception as ex:  # noqa: BLE001 -- the adapters map errors already; this is the last guard
        res = pv._exception_result(ex)
    status = res.get("status") if res.get("status") in ("ACCEPTED", "REJECTED", "FAILED", "UNKNOWN") else "UNKNOWN"
    mid = res.get("provider_message_id")
    with store.transaction():
        d = store.get(al.DELIVERIES, did)
        d["attempts"][-1].update(status=status, provider_message_id=mid, error=res.get("error"), finished_at=iso(now))
        d = _set(store, d, status, now, d.get("provider") or "provider", f"test_send_{status.lower()}",
                 provider_message_id=mid, error=res.get("error"))
        if mid:
            store.insert_unique(al.INDEX, f"{d['provider']}:{mid}", {"delivery_id": did}, actor="floodnet",
                                action="index")
    return view(store, d, duplicate=False)


def send(store: OpsStore, subscriber_id: str, *, channel: str, idempotency_key: str, text_sha256: str, actor: str,
         factory: Callable, now: Optional[datetime] = None, expected_mode: Optional[str] = None) -> dict:
    """`expected_mode` is the mode the dispatcher's preview showed (QA 2026-10-11 M8): a send is refused when the mode
    now differs, and a LIVE send always needs expected_mode="LIVE" -- a real message only after a LIVE preview."""
    now = now or utc_now()
    key = str(idempotency_key or "")
    if not KEY_RE.match(key):
        raise ValueError("idempotency_key must be 8-80 characters: letters, digits, '-' or '_'")
    did = f"TEST:{key}"
    existing = store.get(al.DELIVERIES, did)
    if existing is not None:
        return _duplicate(store, existing, str(subscriber_id), channel, now)
    number = _eligible(store, subscriber_id, channel)
    if str(text_sha256 or "") != preview_sha256():
        raise Conflict("the message preview is out of date; preview again and confirm the text before sending")
    provider, mode, why = factory(channel, number, False)
    if (mode == "LIVE" and expected_mode != "LIVE") or (expected_mode and mode != expected_mode):
        raise Conflict(f"the sending mode is now {mode} but the preview showed {expected_mode or 'no mode'}; preview "
                       "again and confirm")
    base = {"delivery_id": did, "alert_id": None, "version": None, "kind": "test", "subscriber_id": str(subscriber_id),
            "channel": channel, "level": None, "idempotency_key": did, "status": "QUEUED",
            "status_history": [{"status": "QUEUED", "at": iso(now), "source": str(actor)[:60]}],
            "provider": None, "mode": None, "mode_reason": None, "label": None, "provider_message_id": None,
            "error": None, "reason": None, "submitted_at": None, "simulated": False, "attempts": [],
            "requested_by": str(actor)[:60], "requested_at": iso(now), "text_sha256": text_sha256}
    with store.transaction():
        d, created = store.insert_unique(al.DELIVERIES, did, base, actor=actor, action="test_send_requested")
        if not created:                                      # a concurrent double click won the insert
            return _duplicate(store, d, str(subscriber_id), channel, now)
        d["attempts"] = [{"n": 1, "idempotency_key": did, "at": iso(now), "mode": mode}]
        d = _set(store, d, "SUBMITTED", now, actor, "test_send_submitted", provider=getattr(provider, "name", "unknown"),
                 mode=mode, mode_reason=why, label=mode, submitted_at=iso(now))
    return _call(store, did, provider, channel, number, did, now)


def retry(store: OpsStore, delivery_id: str, *, actor: str, factory: Callable, now: Optional[datetime] = None) -> dict:
    now = now or utc_now()
    d = store.get(al.DELIVERIES, str(delivery_id))
    if d is None or d.get("kind") != "test":
        raise KeyError(str(delivery_id))
    if not _retryable(d):
        raise Conflict(f"cannot retry (status {d['status']}): only a test send that FAILED before the provider took "
                       f"it can be retried, at most {MAX_ATTEMPTS} attempts; UNKNOWN, REJECTED and later failures are "
                       "never retried because the message may have been sent or would be refused again")
    number = _eligible(store, d["subscriber_id"], d["channel"])
    with store.transaction():
        d = store.get(al.DELIVERIES, str(delivery_id))
        if not _retryable(d):
            raise Conflict(f"cannot retry (status {d['status']})")
        n = len(d["attempts"]) + 1
        key = f"{d['delivery_id']}:a{n}"
        provider, mode, why = factory(d["channel"], number, False)
        if mode != d.get("mode"):                  # QA M8: a retry never changes SIMULATED into LIVE (or back)
            raise Conflict(f"the sending mode changed from {d.get('mode')} to {mode}; start a new test send instead")
        d["attempts"].append({"n": n, "idempotency_key": key, "at": iso(now), "mode": mode})
        d = _set(store, d, "SUBMITTED", now, actor, "test_send_retry", provider=getattr(provider, "name", "unknown"),
                 mode=mode, mode_reason=why, label=mode, submitted_at=iso(now), error=None)
    return _call(store, d["delivery_id"], provider, d["channel"], number, key, now)


def refresh_status(store: OpsStore, delivery_id: str, *, provider, now: Optional[datetime] = None) -> dict:
    """Ask the provider for a LIVE test send's status and apply it like a callback (forward only)."""
    d = store.get(al.DELIVERIES, str(delivery_id))
    if d is None or d.get("kind") != "test":
        raise KeyError(str(delivery_id))
    if d.get("mode") != "LIVE" or not d.get("provider_message_id"):
        raise Conflict("only a LIVE test send the provider accepted has a provider status; SIMULATED sends do not")
    if d.get("provider") != "twilio" or provider is None or not hasattr(provider, "fetch_status"):
        raise Conflict(f"status lookup is implemented for Twilio only (this send used {d.get('provider')})")
    got = provider.fetch_status(d["provider_message_id"])
    if got["status"]:
        al.apply_callback(store, provider="twilio", provider_message_id=d["provider_message_id"], status=got["status"],
                          raw_status=got["raw_status"], error=got["error"], now=now)
    out = view(store, store.get(al.DELIVERIES, str(delivery_id)))
    out.update(provider_status=got["raw_status"] or None, provider_error=got["error"])
    return out


def list_sends(store: OpsStore, limit: int = 50) -> list[dict]:
    ds = store.list(al.DELIVERIES, lambda d: d.get("kind") == "test")
    ds.sort(key=lambda d: d.get("requested_at") or "", reverse=True)
    return [view(store, d) for d in ds[:limit]]


def simulate_callbacks(store: OpsStore, delivery_id: str) -> dict:
    """SIMULATED delivery reports for a SIMULATED test send (never for a live one)."""
    d = store.get(al.DELIVERIES, str(delivery_id))
    if d is None or d.get("kind") != "test":
        raise KeyError(str(delivery_id))
    if d.get("provider") != "simulated" or not d.get("provider_message_id"):
        raise Conflict("simulated delivery reports exist only for a SIMULATED test send the provider accepted")
    applied = 0
    for ev in pv.SimulatedProvider(store=store).callbacks(d["provider_message_id"]):
        r = al.apply_callback(store, provider="simulated", provider_message_id=ev["provider_message_id"],
                              status=ev["status"], raw_status=ev["raw_status"])
        applied += bool(r.get("applied"))
    return {"applied": applied, "label": "SIMULATED"}
