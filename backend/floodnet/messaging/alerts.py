"""Subscriber advisories: lifecycle, versions, deliveries and provider callbacks (DECISIONS D-44, LIVE_OPERATIONS.md §6).

Alert states
  DRAFT -> REVIEW_REQUIRED -> APPROVED -> SENDING -> SENT | PARTIAL | FAILED
  CANCELLED (only before anything was published) and RESOLVED (a "no longer active" follow-up to previous recipients).
  Model-derived content is never sent without a person's approval: approve() only works from REVIEW_REQUIRED, and every
  new version (refresh with a material change) goes back to REVIEW_REQUIRED.

Versions: a new version only for a material change (status level, affected localities, affected road set, confirmed
closure ids; targeting.signature). Otherwise refresh() answers "no material change".

Deliveries: one record per (alert, version, subscriber, channel), created with store.insert_unique, so processing the
same send twice never queues a second message. The record (with its idempotency key) is stored, and claimed
QUEUED -> SUBMITTED in a transaction, before the provider is called. A timeout or ambiguous error becomes UNKNOWN and is
never retried blindly. Statuses only move forward (providers.can_advance); provider callbacks are de-duplicated on
(provider, message id, status). A subscriber gets at most one message per alert per COOLDOWN_MIN unless the level rises
or the alert is resolved; opted-out subscribers are never sent anything (SUPPRESSED).

Texts are English only. TRANSLATION_NOTE: Hindi/Marathi need a human-reviewed translation before use.
"""
from __future__ import annotations

import hashlib
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from ..ops.store import OpsStore, iso, utc_now
from . import providers as pv
from . import subscribers as subs
from . import targeting as tg

COLL, DELIVERIES, TARGETS, CALLBACKS, INDEX = "alerts", "deliveries", "alert_targets", "provider_callbacks", "delivery_index"
BANNER = "FloodNet advisory (student prototype, not an official warning)"
STOP_TEXT = "Reply STOP to unsubscribe"
STATUS_WORDS = {"watch": "Flooding possible – plan ahead", "advisory": "Flooding likely – avoid the area",
                "ended": "No longer active"}
COOLDOWN_MIN = 30
SMS_MAX_CHARS = 300
CHANNELS = ("sms", "whatsapp")
TRANSLATION_NOTE = ("TODO: messages are English only. Hindi and Marathi texts need a human-reviewed translation "
                    "before they may be sent; machine translation is not used for safety messages.")
BMC_PRECAUTIONS_SHORT = "Don't walk through flowing water. Avoid contact with flood water. Stay away from power lines."
LEVEL_RANK = {None: 0, "watch": 1, "advisory": 2, "ended": 0}
IST = timezone(timedelta(hours=5, minutes=30))

GSM_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
                "¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM_MAP = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "•": "*", "…": "...", "[": "(", "]": ")",
            "{": "(", "}": ")", "|": "/", "~": "-", "^": "", "\\": "/", "`": "'", "€": "EUR"}


class StateError(ValueError):
    """The requested transition is not allowed in the alert's current state (HTTP 409)."""


class NoForecast(ValueError):
    """No forecast has been published to the ops store (HTTP 422)."""


# ----------------------------------------------------------------------------- text
def gsm_safe(text: str) -> str:
    out = []
    for ch in text:
        ch = _GSM_MAP.get(ch, ch)
        if all(c in GSM_BASIC for c in ch):
            out.append(ch)
            continue
        folded = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
        out.append("".join(c for c in folded if c in GSM_BASIC))
    return "".join(out)


def _places(zone: dict, n_roads: int, with_locality: bool = True) -> str:
    names = []
    for s in sorted(zone["segments"], key=lambda s: (s["basis"] != "CONFIRMED_CLOSURE", -(s["max_depth_cm"] or 0))):
        if s["name"] not in names:
            names.append(s["name"])
    locs = [subs.LOCALITY_BY_ID[l]["name"] for l in zone["localities"] if l in subs.LOCALITY_BY_ID]
    road_txt = ", ".join(names[:n_roads]) + (f" +{len(names) - n_roads} more" if len(names) > n_roads > 0 else "")
    loc_txt = " and ".join(locs[:2])
    if n_roads and road_txt and with_locality and loc_txt:
        return f"{road_txt} near {loc_txt}"
    if n_roads and road_txt:
        return road_txt
    return loc_txt or "the Dadar pilot area"


def _basis(zone: dict) -> str:
    kinds = {s["basis"] for s in zone["segments"]}
    parts = []
    if "MODEL_ESTIMATE" in kinds:
        parts.append("model estimate")
    if "CONFIRMED_CLOSURE" in kinds:
        parts.append("confirmed road closure")
    if "REPORTED" in kinds:
        parts.append("unconfirmed public report")
    return ", ".join(parts)


def _when(now: datetime, window_min: float) -> str:
    return f"Next {window_min:g} min from {now.astimezone(IST):%H:%M} IST"


def build_texts(level: str, zone: dict, link: str, now: datetime, window_min: float) -> dict:
    """SMS (GSM 7-bit basic characters, <= 300 chars) and WhatsApp bodies. Both carry the banner, the public link and
    the STOP text, and never a phone number other than the 112 emergency number."""
    status = STATUS_WORDS[level]
    if level == "ended":
        place = _places(zone, 1)
        sms_opts = [[BANNER, f"{status}: {place}.", f"Details: {link}", STOP_TEXT],
                    [BANNER, f"{status}.", f"Details: {link}", STOP_TEXT]]
        wa = "\n".join([f"*{BANNER}*", f"*{status}*: {_places(zone, 3)}.",
                        f"Updated {now.astimezone(IST):%d %b %H:%M} IST. Stay careful: water can remain on roads.",
                        f"Details: {link}", STOP_TEXT])
    else:
        when, basis = _when(now, window_min), _basis(zone)
        sms_opts = [[BANNER, f"{status}: {_places(zone, n, loc)}.", f"{when}, {basis}.", f"Details: {link}",
                     "Emergency 112", STOP_TEXT] for n, loc in ((2, True), (1, True), (1, False), (0, True))]
        sms_opts.append([BANNER, f"{status}.", f"Details: {link}", "Emergency 112", STOP_TEXT])
        depth = zone.get("max_depth_cm")
        why = []
        if depth:
            why.append(f"modelled water up to {depth:.0f} cm on {sum(s['basis'] == 'MODEL_ESTIMATE' for s in zone['segments'])} road(s)")
        if zone["closures"]:
            why.append(f"{len(zone['closures'])} confirmed road closure(s)")
        if zone["reported"]:
            why.append(f"{len(zone['reported'])} unconfirmed public report(s)")
        wa = "\n".join([f"*{BANNER}*", f"*{status}*", f"Where: {_places(zone, 4)}", f"When: {when} ({basis})",
                        "Why: " + "; ".join(why) + ".", f"Do (BMC advice): {BMC_PRECAUTIONS_SHORT}",
                        f"Details and updates: {link}", "Emergency: 112 (ERSS), BMC 1916", STOP_TEXT])
    sms = ""
    for lines in sms_opts:
        sms = gsm_safe("\n".join(lines))
        if len(sms) <= SMS_MAX_CHARS:
            break
    return {"sms": sms, "whatsapp": wa, "language": "en"}


# ----------------------------------------------------------------------------- helpers
def _now(now: Optional[datetime]) -> datetime:
    return now or utc_now()


def _get(store: OpsStore, alert_id: str) -> dict:
    a = store.get(COLL, alert_id)
    if a is None:
        raise KeyError(alert_id)
    return a


def _history(a: dict, state: str, actor: str, now: datetime, note: str = "") -> None:
    a.setdefault("state_history", []).append({"state": state, "at": iso(now), "actor": str(actor)[:60],
                                              "note": str(note or "")[:200]})
    a["state"] = state


def _published(a: dict) -> bool:
    return any(v.get("published_at") for v in a.get("versions", []))


def _change_summary(prev: Optional[dict], zone: dict, level: str) -> str:
    if prev is None:
        return "First issue"
    parts = []
    if LEVEL_RANK[level] > LEVEL_RANK[prev["level"]]:
        parts.append(f"Status raised to {STATUS_WORDS[level]}")
    elif LEVEL_RANK[level] < LEVEL_RANK[prev["level"]]:
        parts.append(f"Status lowered to {STATUS_WORDS[level]}")
    old = {s["seg_id"] for s in prev["zone"]["segments"]}
    new = {s["seg_id"] for s in zone["segments"]}
    if new - old:
        parts.append(f"{len(new - old)} road(s) added")
    if old - new:
        parts.append(f"{len(old - new)} road(s) no longer affected")
    if set(zone["closures"]) - set(prev["zone"]["closures"]):
        parts.append("confirmed road closure added")
    if set(prev["zone"]["closures"]) - set(zone["closures"]):
        parts.append("road closure lifted")
    if set(zone["localities"]) != set(prev["zone"]["localities"]) and not parts:
        parts.append("affected localities changed")
    return "; ".join(parts) or "Updated"


def _compute_zone(store: OpsStore, window_min: float, locality: Optional[str], roads=None) -> tuple[dict, dict, float]:
    from ..ops.clock import now_t_min
    from ..ops.incident import closures, get_forecast
    got = get_forecast(store)
    if got is None:
        raise NoForecast("no forecast has been published to the live-operations store; publish one first")
    forecast, meta = got
    now_t = now_t_min(store)
    zone = tg.impact_zone(forecast, closures(store, now_t), now_t, window_min, roads=roads)
    if locality:
        zone["segments"] = [s for s in zone["segments"] if s["locality_id"] == locality]
        zone["localities"] = [locality] if zone["segments"] else []
        keep = {s.get("closure_event_id") for s in zone["segments"]} | {s.get("report_event_id") for s in zone["segments"]}
        zone["closures"] = [c for c in zone["closures"] if c in keep]
        zone["reported"] = [r for r in zone["reported"] if r in keep]
        depths = [s["max_depth_cm"] or 0 for s in zone["segments"]]
        zone["max_depth_cm"] = max(depths) if depths else None
        zone["level"] = (None if not zone["segments"] else "advisory"
                         if zone["closures"] or max(depths) >= tg.ADVISORY_CM else "watch")
        zone["signature"] = tg.signature(zone)
    fmeta = {"run_id": meta.get("run_id"), "data_mode": meta.get("data_mode"), "label": meta.get("label"),
             "forecast_version": meta.get("forecast_version"), "published_at": meta.get("_updated_at")}
    return zone, fmeta, now_t


def _new_version(a: dict, zone: dict, fmeta: dict, now: datetime, *, kind: str = "update") -> dict:
    prev = a["versions"][-1] if a.get("versions") else None
    level = "ended" if kind == "resolution" else zone["level"]
    v = {"version": len(a.get("versions", [])) + 1, "kind": kind, "created_at": iso(now), "level": level,
         "signature": zone["signature"] if kind != "resolution" else "resolved", "zone": zone, "forecast": fmeta,
         "texts": build_texts(level, zone, a["link"], now, a["window_min"]),
         "change_summary": "No longer active" if kind == "resolution" else _change_summary(prev, zone, level),
         "published_at": None}
    a.setdefault("versions", []).append(v)
    a["current_version"] = v["version"]
    return v


# ----------------------------------------------------------------------------- lifecycle
def create_draft(store: OpsStore, *, window_min: float, public_base_url: str, actor: str, title: Optional[str] = None,
                 locality: Optional[str] = None, channels=CHANNELS, roads=None, now: Optional[datetime] = None) -> dict:
    now = _now(now)
    window_min = float(window_min)
    if not 5 <= window_min <= 360:
        raise ValueError("window_min must be between 5 and 360 minutes")
    if locality is not None and locality not in subs.LOCALITY_BY_ID:
        raise ValueError(f"unknown pilot locality; choose one of {sorted(subs.LOCALITY_BY_ID)}")
    channels = [c for c in channels]
    if not channels or any(c not in CHANNELS for c in channels):
        raise ValueError(f"channels must be a non-empty subset of {CHANNELS}")
    base = str(public_base_url or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("a public base URL (http:// or https://) is required to build the alert link")
    zone, fmeta, now_t = _compute_zone(store, window_min, locality, roads)
    if zone["level"] is None:
        raise ValueError("no affected roads in this window (no modelled depth >= "
                         f"{tg.WATCH_CM:g} cm and no closure); nothing to advise")
    public_id = secrets.token_urlsafe(16)
    with store.transaction():
        alert_id = f"AL{store.next_seq('alert')}"
        a = {"alert_id": alert_id, "public_id": public_id, "link": f"{base}/alerts/{public_id}",
             "public_base_url": base, "title": str(title or "")[:120] or None, "locality": locality,
             "channels": channels, "window_min": window_min, "now_t_min": now_t, "versions": [],
             "approved_version": None, "approved_by": None, "approver_name": None, "approved_at": None,
             "sent_versions": [], "resolved_at": None, "cancelled_at": None, "created_by": str(actor)[:60],
             "translation_note": TRANSLATION_NOTE}
        _new_version(a, zone, fmeta, now)
        if not a["title"]:
            a["title"] = f"{STATUS_WORDS[zone['level']]}: {_places(zone, 1)}"[:120]
        _history(a, "DRAFT", actor, now, "draft created from the published forecast")
        return store.put(COLL, alert_id, a, actor=actor, action="alert_draft")


def submit_review(store: OpsStore, alert_id: str, *, actor: str, note: str = "", now: Optional[datetime] = None) -> dict:
    with store.transaction():
        a = _get(store, alert_id)
        if a["state"] != "DRAFT":
            raise StateError(f"only a DRAFT can be submitted for review (state {a['state']})")
        _history(a, "REVIEW_REQUIRED", actor, _now(now), note or "model-derived content: review required")
        return store.put(COLL, alert_id, a, actor=actor, action="alert_submit_review")


def approve(store: OpsStore, alert_id: str, *, actor: str, approver_name: Optional[str] = None,
            now: Optional[datetime] = None, version: Optional[int] = None) -> dict:
    """`version` is the version the reviewer read; if the alert changed since (a refresh made a new version) the
    approval is refused so nobody approves text they have not seen (QA finding M8). The API always passes it."""
    now = _now(now)
    with store.transaction():
        a = _get(store, alert_id)
        if a["state"] != "REVIEW_REQUIRED":
            raise StateError(f"only an alert in REVIEW_REQUIRED can be approved (state {a['state']})")
        if version is not None and int(version) != int(a["current_version"]):
            raise StateError(f"version {version} was reviewed but the alert is now at version {a['current_version']}; "
                             "review the new version")
        a.update(approved_version=a["current_version"], approved_by=str(actor)[:60],
                 approver_name=str(approver_name)[:80] if approver_name else None, approved_at=iso(now))
        _history(a, "APPROVED", actor, now, f"version {a['current_version']} approved")
        return store.put(COLL, alert_id, a, actor=actor, action="alert_approve")


def cancel(store: OpsStore, alert_id: str, *, actor: str, reason: str = "", now: Optional[datetime] = None) -> dict:
    now = _now(now)
    with store.transaction():
        a = _get(store, alert_id)
        if a["state"] in ("CANCELLED", "RESOLVED") or _published(a):
            raise StateError("an alert can only be cancelled before anything was published; resolve it instead")
        a["cancelled_at"] = iso(now)
        _history(a, "CANCELLED", actor, now, reason)
        return store.put(COLL, alert_id, a, actor=actor, action="alert_cancel")


def refresh(store: OpsStore, alert_id: str, *, actor: str, roads=None, now: Optional[datetime] = None) -> dict:
    """Recomputes the impact zone; a new version only for a material change."""
    now = _now(now)
    a = _get(store, alert_id)
    if a["state"] in ("CANCELLED", "RESOLVED", "SENDING"):
        raise StateError(f"cannot refresh an alert in state {a['state']}")
    zone, fmeta, _ = _compute_zone(store, a["window_min"], a.get("locality"), roads)
    latest = a["versions"][-1]
    if zone["level"] is None:
        return {"changed": False, "reason": "no affected roads any more; consider resolving the alert", "alert": a}
    if zone["signature"] == latest["signature"]:
        return {"changed": False, "reason": "no material change (same level, localities, roads and closures)",
                "alert": a}
    with store.transaction():
        a = _get(store, alert_id)
        if a["state"] in ("CANCELLED", "RESOLVED", "SENDING") or a["versions"][-1]["signature"] != latest["signature"]:
            raise StateError(f"the alert changed while it was being refreshed (state {a['state']}); try again")
        v = _new_version(a, zone, fmeta, now)
        _history(a, "DRAFT" if a["state"] == "DRAFT" else "REVIEW_REQUIRED", actor, now,
                 f"version {v['version']}: {v['change_summary']}")
        a = store.put(COLL, alert_id, a, actor=actor, action="alert_new_version")
    return {"changed": True, "reason": v["change_summary"], "alert": a}


# ----------------------------------------------------------------------------- sending
def _default_factory(store: OpsStore) -> Callable:
    sim = pv.SimulatedProvider(store=store)
    return lambda channel, to, simulated: pv.choose_provider(channel, to, simulated=simulated, simulated_provider=sim)


def list_deliveries(store: OpsStore, alert_id: str) -> list[dict]:
    return store.list(DELIVERIES, lambda d: d.get("alert_id") == alert_id)


def get_targets(store: OpsStore, alert_id: str, version: int) -> list[dict]:
    doc = store.get(TARGETS, f"{alert_id}:v{version}")
    return doc["decisions"] if doc else []


def _set_status(store: OpsStore, d: dict, status: str, now: datetime, source: str, **fields) -> dict:
    d.update(fields)
    d["status"] = status
    d.setdefault("status_history", []).append({"status": status, "at": iso(now), "source": source})
    return store.put(DELIVERIES, d["delivery_id"], d, actor=source, action=f"delivery_{status.lower()}")


def _in_cooldown(store: OpsStore, alert_id: str, sub_id: str, version: int, level: str, now: datetime) -> bool:
    for d in list_deliveries(store, alert_id):
        if d["subscriber_id"] != sub_id or d["version"] == version or not d.get("submitted_at"):
            continue
        if d["status"] in ("SUPPRESSED", "REJECTED", "FAILED"):
            continue
        at = datetime.fromisoformat(d["submitted_at"].replace("Z", "+00:00"))
        if now - at < timedelta(minutes=COOLDOWN_MIN) and LEVEL_RANK.get(level, 0) <= LEVEL_RANK.get(d["level"], 0):
            return True
    return False


def _deliver(store: OpsStore, a: dict, v: dict, sub_id: str, channel: str, simulated: bool, factory: Callable,
             now: datetime, *, kind: str) -> dict:
    tag = f"v{v['version']}" if kind == "update" else "resolved"
    did = f"{a['alert_id']}:{tag}:{sub_id}:{channel}"
    base = {"delivery_id": did, "alert_id": a["alert_id"], "version": v["version"], "kind": kind,
            "subscriber_id": sub_id, "channel": channel, "level": v["level"], "idempotency_key": did,
            "status": "QUEUED", "status_history": [{"status": "QUEUED", "at": iso(now), "source": "floodnet"}],
            "provider": None, "mode": None, "mode_reason": None, "provider_message_id": None, "error": None,
            "reason": None, "submitted_at": None, "simulated": simulated}
    with store.transaction():
        d, created = store.insert_unique(DELIVERIES, did, base, actor="floodnet", action="delivery_queued")
        if not created and d["status"] == "SUBMITTED":           # an earlier attempt was interrupted mid-call
            return _set_status(store, d, "UNKNOWN", now, "floodnet",
                               reason="send was interrupted after submission; outcome unknown, not retried")
        if d["status"] != "QUEUED":
            return d
        if kind == "update" and _in_cooldown(store, a["alert_id"], sub_id, v["version"], v["level"], now):
            return _set_status(store, d, "SUPPRESSED", now, "floodnet",
                               reason=f"cooldown: already messaged about this alert within {COOLDOWN_MIN} min "
                                      "and the level did not rise")
        sdoc = store.get(subs.COLL, sub_id)
        if sdoc is None or sdoc.get("status") != "ACTIVE":
            return _set_status(store, d, "SUPPRESSED", now, "floodnet",
                               reason=f"subscriber {('opted out' if sdoc else 'missing')}; nothing sent")
        number = subs.reveal_number(store, sub_id)
        if number is None:
            return _set_status(store, d, "SUPPRESSED", now, "floodnet", reason="no number held (erased)")
        provider, mode, why = factory(channel, number, simulated)
        d = _set_status(store, d, "SUBMITTED", now, "floodnet", provider=getattr(provider, "name", "unknown"),
                        mode=mode, mode_reason=why, submitted_at=iso(now))
    try:
        res = provider.send(channel, number, v["texts"][channel], did)
    except Exception as ex:  # noqa: BLE001 -- the provider adapters already map errors; this is the last guard
        res = pv._exception_result(ex)
    status = res.get("status") if res.get("status") in ("ACCEPTED", "REJECTED", "FAILED", "UNKNOWN") else "UNKNOWN"
    mid = res.get("provider_message_id")
    with store.transaction():
        d = store.get(DELIVERIES, did)
        d = _set_status(store, d, status, now, d["provider"] or "provider", provider_message_id=mid,
                        error=res.get("error"))
        if mid:
            store.insert_unique(INDEX, f"{d['provider']}:{mid}", {"delivery_id": did}, actor="floodnet",
                                action="index")
    return d


def _summary(ds: list[dict]) -> dict:
    out: dict[str, int] = {}
    for d in ds:
        out[d["status"]] = out.get(d["status"], 0) + 1
    return out


def _outcome_state(ds: list[dict]) -> str:
    attempted = [d for d in ds if d["status"] != "SUPPRESSED"]
    ok = [d for d in attempted if d["status"] in pv.OK_STATES]
    if len(ok) == len(attempted):
        return "SENT"
    if not ok and all(d["status"] in pv.FAILURES for d in attempted):
        return "FAILED"
    return "PARTIAL"


def send(store: OpsStore, alert_id: str, *, actor: str, provider_factory: Optional[Callable] = None,
         roads=None, now: Optional[datetime] = None) -> dict:
    """Sends the approved version to eligible subscribers. Calling it again never sends twice."""
    now = _now(now)
    factory = provider_factory or _default_factory(store)
    with store.transaction():
        a = _get(store, alert_id)
        if a["state"] in ("SENT", "PARTIAL", "FAILED") and a.get("approved_version") in a.get("sent_versions", []):
            return {**a, "already_sent": True, "deliveries": _summary(
                [d for d in list_deliveries(store, alert_id) if d["version"] == a["approved_version"]])}
        if a["state"] not in ("APPROVED", "SENDING"):
            raise StateError(f"only an APPROVED alert can be sent (state {a['state']})")
        v = a["versions"][a["approved_version"] - 1]
        if not v.get("published_at"):
            v["published_at"] = iso(now)              # the public page exists before any message carries its link
        if a["state"] != "SENDING":
            _history(a, "SENDING", actor, now, f"sending version {v['version']}")
        a = store.put(COLL, alert_id, a, actor=actor, action="alert_sending")
    decisions = tg.decide(v["zone"], subs.targeting_records(store), channels=a["channels"], roads=roads)
    stored = [{k: x for k, x in d.items() if k not in ("distance_m", "network_m")} for d in decisions]   # QA M7
    store.put(TARGETS, f"{alert_id}:v{v['version']}", {"alert_id": alert_id, "version": v["version"],
                                                        "decisions": stored}, actor=actor, action="targeting")
    for dec in decisions:
        if dec["eligible"]:
            for ch in dec["channels"]:
                _deliver(store, a, v, dec["subscriber_id"], ch, dec["simulated"], factory, now, kind="update")
    ds = [d for d in list_deliveries(store, alert_id) if d["version"] == v["version"] and d["kind"] == "update"]
    with store.transaction():
        a = _get(store, alert_id)
        a["sent_versions"] = sorted(set(a.get("sent_versions", [])) | {v["version"]})
        _history(a, _outcome_state(ds), actor, now,
                 f"version {v['version']}: {sum(d['eligible'] for d in decisions)} eligible subscriber(s)")
        a = store.put(COLL, alert_id, a, actor=actor, action="alert_sent")
    return {**a, "already_sent": False, "deliveries": _summary(ds)}


def resolve(store: OpsStore, alert_id: str, *, actor: str, provider_factory: Optional[Callable] = None,
            note: str = "", now: Optional[datetime] = None) -> dict:
    """'No longer active': a final public version, and a follow-up only to subscribers a message was accepted for."""
    now = _now(now)
    factory = provider_factory or _default_factory(store)
    with store.transaction():
        a = _get(store, alert_id)
        if a["state"] in ("RESOLVED", "CANCELLED") or not _published(a):
            raise StateError(f"only a published, unresolved alert can be resolved (state {a['state']})")
        last = [v for v in a["versions"] if v.get("published_at")][-1]
        v = _new_version(a, last["zone"], last["forecast"], now, kind="resolution")
        v["published_at"] = iso(now)
        a["resolved_at"] = iso(now)
        _history(a, "RESOLVED", actor, now, note or "no longer active")
        a = store.put(COLL, alert_id, a, actor=actor, action="alert_resolve")
    prior = {(d["subscriber_id"], d["channel"], d.get("simulated", False)) for d in list_deliveries(store, alert_id)
             if d["kind"] == "update" and d["status"] in pv.OK_STATES}
    for sub_id, ch, simulated in sorted(prior):
        _deliver(store, a, v, sub_id, ch, simulated, factory, now, kind="resolution")
    ds = [d for d in list_deliveries(store, alert_id) if d["kind"] == "resolution"]
    return {**a, "deliveries": _summary(ds)}


# ----------------------------------------------------------------------------- callbacks and inbound
def apply_callback(store: OpsStore, *, provider: str, provider_message_id: str, status: Optional[str],
                   raw_status: str = "", error: Optional[dict] = None, now: Optional[datetime] = None,
                   delivery_id: Optional[str] = None) -> dict:
    """`delivery_id` (MSG91's CRQID echo of our id) is used only when the provider message id is unknown, and only
    for a delivery that went to the same provider."""
    now = _now(now)
    ref = provider_message_id or f"delivery:{delivery_id}"
    key = hashlib.sha256(f"{provider}|{ref}|{raw_status or status}".encode()).hexdigest()[:32]
    with store.transaction():
        _, created = store.insert_unique(CALLBACKS, key, {"provider": provider, "provider_message_id": provider_message_id,
                                                          "status": status, "raw_status": str(raw_status)[:40],
                                                          "received_at": iso(now)}, actor=f"provider:{provider}",
                                         action="callback")
        if not created:
            return {"duplicate": True, "matched": None, "applied": False}
        idx = store.get(INDEX, f"{provider}:{provider_message_id}") if provider_message_id else None
        d = store.get(DELIVERIES, idx["delivery_id"]) if idx else None
        if d is None and delivery_id:
            d = store.get(DELIVERIES, str(delivery_id))
            if d is not None and d.get("provider") != provider:
                d = None
        if d is None:
            return {"duplicate": False, "matched": False, "applied": False}
        if status is None or not pv.can_advance(d["status"], status):
            return {"duplicate": False, "matched": True, "applied": False, "status": d["status"]}
        fields = {"error": error} if error else {}
        d = _set_status(store, d, status, now, f"provider:{provider}", **fields)
        return {"duplicate": False, "matched": True, "applied": True, "status": d["status"]}


def simulate_callbacks(store: OpsStore, alert_id: str, sim: pv.SimulatedProvider) -> dict:
    applied = 0
    for d in list_deliveries(store, alert_id):
        if d.get("provider") != "simulated" or not d.get("provider_message_id"):
            continue
        for ev in sim.callbacks(d["provider_message_id"]):
            r = apply_callback(store, provider="simulated", provider_message_id=ev["provider_message_id"],
                               status=ev["status"], raw_status=ev["raw_status"])
            applied += bool(r.get("applied"))
    return {"applied": applied, "label": "SIMULATED"}


def handle_inbound(store: OpsStore, from_number: str, body: str) -> dict:
    if not subs.is_stop_keyword(body):
        return {"stop": False, "opted_out": False}
    r = subs.opt_out_by_number(store, from_number, method="inbound_stop")
    return {"stop": True, "opted_out": bool(r.get("matched"))}


# ----------------------------------------------------------------------------- reads
def get_alert(store: OpsStore, alert_id: str) -> dict:
    return _get(store, alert_id)


def list_alerts(store: OpsStore) -> list[dict]:
    return store.list(COLL)


def find_by_public_id(store: OpsStore, public_id: str) -> Optional[dict]:
    hits = store.list(COLL, lambda a: secrets.compare_digest(str(a.get("public_id", "")), str(public_id)))
    return hits[0] if hits else None


def dispatcher_view(store: OpsStore, a: dict) -> dict:
    """The dispatcher's alert detail: deliveries by subscriber id (no numbers), targeting reasons."""
    ds = list_deliveries(store, a["alert_id"])
    return {**{k: v for k, v in a.items() if not k.startswith("_")}, "version_doc": a.get("_version"),
            "deliveries": [{k: d.get(k) for k in ("delivery_id", "version", "kind", "subscriber_id", "channel", "status",
                                                    "mode", "mode_reason", "provider", "error", "reason",
                                                    "submitted_at", "simulated")} for d in ds],
            "delivery_summary": _summary(ds),
            "targets": {v["version"]: get_targets(store, a["alert_id"], v["version"]) for v in a["versions"]
                        if v.get("published_at") and v["kind"] == "update"}}
