"""Resource registry (DECISIONS D-43): teams, vehicles and shelters with a verified, timestamped status.

Status words follow the NIMS resource-status idea (FEMA NIMS 2017 pp. 91-92): AVAILABLE, ASSIGNED, OUT_OF_SERVICE with
a reason (MECHANICAL, REST, PERSONNEL). This is a design borrowed from NIMS, not NIMS compliance. ASSIGNED here means
"committed to a task outside FloodNet's plan": the planner treats it as unavailable. FloodNet's own missions stay in
the assignment ledger.

The registry starts from the demonstration fixtures (SIMULATED numbers on real places) and is changed only by
validated updates from the dispatcher or a registered responder within its scope. Every update keeps the time it
describes (observed_at) and the time it arrived (received_at); an update older than the one already applied is stored
but not applied; nothing expires -- staleness is a flag (STALE_AFTER_MIN, ASSUMED). No government database is
connected: none is available to this project.

Registration and import (2026-10-11). The dispatcher adds records (source "dispatcher entry") or imports a JSON/CSV
list (source "import: <label>"): every row is validated against the existing vocabularies -- capabilities named by the
demonstration fixtures (the planner's only capability words), vehicle types with a depth limit in
config.VEHICLE_LIMIT_CM -- and the pilot area; a dry run previews per-row errors and a commit is all-or-nothing. A row
that does not say when it was observed has freshness UNKNOWN, never a guessed time.

Candidate shelters (2026-10-11). A volunteer (scoped volunteer token) or the dispatcher on a volunteer's behalf submits
a place; the dispatcher records the owner's permission, then a suitability checklist with the MODELLED flood depth on
the nearest road from the forecast published to live operations (or UNKNOWN), then confirms (it becomes a registry
shelter, source "volunteer-submitted, verified by dispatcher") or rejects it with a reason. Candidates live in their
own collection, so a candidate can never reach the planner or the shelter list before it is confirmed.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np

from .. import config
from ..response import fixtures as fx
from . import incident as inc
from .auth import hash_token, new_token, token_matches
from .navigation import _local, parse_time
from .store import OpsStore, iso, utc_now

STATUSES = ("AVAILABLE", "ASSIGNED", "OUT_OF_SERVICE")
OUT_OF_SERVICE_REASONS = ("MECHANICAL", "REST", "PERSONNEL")
SHELTER_STATUSES = ("open", "closed")
ROLES = ("field_team", "shelter_staff", "volunteer")
STALE_AFTER_MIN = 60.0                 # ASSUMED: a resource not confirmed for an hour is flagged stale
MAX_CLOCK_SKEW = timedelta(minutes=2)
SEED_SOURCE = "SIMULATED seed (floodnet/response/fixtures.py): demonstration values, not verified"

FIELDS = {"team": {"status", "out_of_service_reason", "capabilities", "members", "lonlat"},
          "vehicle": {"status", "out_of_service_reason", "passenger_capacity", "lonlat"},
          "shelter": {"occupancy", "total_capacity", "status"}}
ROLE_FIELDS = {"field_team": {"team": {"status", "out_of_service_reason", "lonlat"},
                              "vehicle": {"status", "out_of_service_reason", "lonlat"}},
               "shelter_staff": {"shelter": {"occupancy", "total_capacity", "status"}}}

# ------------------------------------------------------------------ registration vocabularies
# The planner's capability words are whatever the fixtures use (D-34); no new words are invented here.
CAPABILITIES = tuple(sorted({c.strip().lower() for t in fx.TEAMS for c in t["capabilities"]}
                            | {c.strip().lower() for s in fx.SETTLEMENTS for c in s.get("required_capabilities", [])}))
# Vehicle types the router knows a depth limit for; "pedestrian" is a travel mode, not a vehicle.
VEHICLE_TYPES = {k: v for k, v in config.VEHICLE_LIMIT_CM.items() if k != "pedestrian"}
DEPTH_LIMIT_SOURCE = "ASSUMED working limit (config.VEHICLE_LIMIT_CM), not a manufacturer or agency figure"
PILOT_BBOX = config.PILOT_BBOX_LONLAT                  # (west, south, east, north)
DISPATCHER_SOURCE = "dispatcher entry"
CANDIDATE_SOURCE = "volunteer-submitted, verified by dispatcher"
RECORD_FIELDS = {
    "team": {"required": ("name", "capabilities", "members", "base", "lonlat"),
             "optional": ("organisation", "status", "out_of_service_reason")},
    "vehicle": {"required": ("name", "type", "passenger_capacity", "base", "lonlat"),
                "optional": ("organisation", "status", "out_of_service_reason")},
    "shelter": {"required": ("name", "lonlat", "total_capacity"),
                "optional": ("occupancy", "status", "address")},
}
IMPORT_COLUMNS = ("kind", "name", "lon", "lat", "base", "organisation", "capabilities", "members", "type",
                  "passenger_capacity", "total_capacity", "occupancy", "status", "out_of_service_reason", "address",
                  "observed_at")
IMPORT_MAX_ROWS = 500
IMPORT_MAX_CHARS = 200_000
NAME_MAX, BASE_MAX, ORG_MAX, ADDRESS_MAX, DESCRIPTION_MAX, NOTE_MAX = 120, 120, 80, 200, 500, 300
_ID_PREFIX = {"team": "T", "vehicle": "V", "shelter": "SH"}

# ------------------------------------------------------------------ candidate shelters
CANDIDATE_STATES = ("SUBMITTED", "PERMISSION_CONFIRMED", "SUITABILITY_CHECKED", "CONFIRMED", "REJECTED")
PERMISSION_METHODS = ("IN_PERSON", "PHONE", "WRITTEN", "MESSAGE", "OTHER")
CHECK_ITEMS = ("access", "water_sanitation", "accessibility")
CHECK_ANSWERS = ("YES", "NO", "UNKNOWN")
MAX_OPEN_CANDIDATES_PER_SUBMITTER = 20          # ASSUMED: keeps one volunteer token from flooding the queue
NEAREST_ROAD_MAX_M = 150.0                      # ASSUMED: farther than this, the street depth says nothing about the site
SITE_CHECK_NOTE = ("MODELLED street depth on the nearest road, from the forecast published to live operations; not a "
                   "measurement at the building and not a statement that the site stays dry.")
# Ten or more digits with any separators (spaces, hyphens, dots, brackets, +): a phone number. Personal numbers are
# not needed anywhere here (QA 2026-10-11 L5: "(982) 001-2345" and "98200.12345" got through). A pattern, not a guarantee.
_PHONE = re.compile(r"(?:\d[\s\-.()+]{0,3}){10,}")


class NotFound(KeyError, ValueError):
    """Unknown id: a 404 at the API (KeyError), still a ValueError for callers that validate."""

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def seed_from_fixtures(store: OpsStore) -> int:
    """Create the registry from the demonstration fixtures once; later calls change nothing."""
    n = 0
    for kind, rows in (("team", fx.TEAMS), ("vehicle", fx.VEHICLES), ("shelter", fx.SHELTERS)):
        for r in rows:
            body = {**r, "kind": kind, "source": SEED_SOURCE, "verified": False, "last_update": None}
            if kind != "shelter":
                body.update(status="AVAILABLE", out_of_service_reason=None)
            _, created = store.insert_unique("resources", r["id"], body, actor="system", action="seed")
            n += created
    return n


def _freshness(r: dict, now_wall: datetime) -> dict:
    """CURRENT / STALE from the observation time of the last applied update; UNKNOWN when nobody has said when."""
    lu = r.get("last_update") or {}
    observed = parse_time(lu.get("observed_at")) if lu.get("observed_at") else None
    out = {"observed_at": lu.get("observed_at"), "received_at": lu.get("received_at")}
    if observed is None:
        why = ("SIMULATED seed, never confirmed" if not lu else "the source did not say when this was observed")
        return {**out, "age_min": None, "stale": False, "freshness": "UNKNOWN", "freshness_note": why}
    age = (now_wall - observed).total_seconds() / 60.0
    stale = age > STALE_AFTER_MIN
    return {**out, "age_min": round(age, 1), "stale": stale, "freshness": "STALE" if stale else "CURRENT",
            "freshness_note": f"observed {round(age)} min ago" + (f" (older than {STALE_AFTER_MIN:g} min)" if stale else "")}


def _view(r: dict, now_wall: datetime) -> dict:
    out = {k: v for k, v in r.items() if not k.startswith("_")}
    out.update(_freshness(r, now_wall))
    if r.get("kind") == "vehicle" and r.get("type") in config.VEHICLE_LIMIT_CM:
        out.update(depth_limit_cm=config.VEHICLE_LIMIT_CM[r["type"]], depth_limit_source=DEPTH_LIMIT_SOURCE)
    return out


def list_resources(store: OpsStore, now_wall: Optional[datetime] = None) -> list[dict]:
    now_wall = now_wall or utc_now()
    return [_view(r, now_wall) for r in store.list("resources")]


def get_resource(store: OpsStore, rid: str, now_wall: Optional[datetime] = None) -> dict:
    r = store.get("resources", rid)
    if r is None:
        raise ValueError(f"unknown resource {rid}")
    return _view(r, now_wall or utc_now())


def vocabulary() -> dict:
    return {"capabilities": list(CAPABILITIES),
            "capabilities_note": "the capability words the planner and the demonstration settlements use (D-34)",
            "vehicle_types": [{"type": t, "depth_limit_cm": v, "source": DEPTH_LIMIT_SOURCE} for t, v in VEHICLE_TYPES.items()],
            "statuses": list(STATUSES), "out_of_service_reasons": list(OUT_OF_SERVICE_REASONS),
            "shelter_statuses": list(SHELTER_STATUSES), "roles": list(ROLES), "pilot_bbox": list(PILOT_BBOX),
            "stale_after_min": STALE_AFTER_MIN, "record_fields": RECORD_FIELDS, "import_columns": list(IMPORT_COLUMNS),
            "import_max_rows": IMPORT_MAX_ROWS, "candidate_states": list(CANDIDATE_STATES),
            "permission_methods": list(PERMISSION_METHODS), "check_items": list(CHECK_ITEMS),
            "check_answers": list(CHECK_ANSWERS), "nearest_road_max_m": NEAREST_ROAD_MAX_M,
            "note": "status words borrowed from NIMS; not NIMS compliance. Seed records are SIMULATED."}


# ------------------------------------------------------------------ responders
def register_responder(store: OpsStore, *, name: str, role: str, scope: list[str],
                       actor: str = "dispatcher") -> tuple[dict, str]:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if role == "volunteer":
        if scope:
            raise ValueError("a volunteer has no resource scope: a volunteer token can only submit candidate shelters")
    else:
        unknown = [x for x in scope if store.get("resources", x) is None]
        if not scope or unknown:
            raise ValueError(f"scope must list known resources (unknown: {unknown})")
    token = new_token()
    rid = f"RP{store.next_seq('responder')}"
    doc = {"responder_id": rid, "name": str(name)[:80], "role": role, "scope": list(scope),
           "token_hash": hash_token(token), "active": True}
    return store.put("responders", rid, doc, actor=actor, action="register"), token


def responder_for_token(store: OpsStore, token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    for r in store.list("responders", lambda r: r.get("active")):
        if token_matches(token, r["token_hash"]):
            return r
    return None


def revoke_responder(store: OpsStore, responder_id: str, *, actor: str = "dispatcher",
                     now_wall: Optional[datetime] = None) -> dict:
    with store.transaction():
        r = store.get("responders", responder_id)
        if r is None:
            raise NotFound(f"unknown responder {responder_id}")
        return store.put("responders", responder_id, {**r, "active": False, "revoked_at": iso(now_wall or utc_now())},
                         actor=actor, action="revoke")


def public_responder(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "token_hash" and not k.startswith("_")}


# ------------------------------------------------------------------ updates
def _validate(kind: str, fields: dict, current: dict) -> dict:
    allowed = FIELDS[kind]
    bad = sorted(set(fields) - allowed)
    if bad:
        raise ValueError(f"field(s) {bad} cannot be updated on a {kind}; allowed: {sorted(allowed)}")
    out = {}
    if "status" in fields:
        st = str(fields["status"]).strip()
        if kind == "shelter":
            st = st.lower()
            if st not in SHELTER_STATUSES:
                raise ValueError(f"shelter status must be one of {SHELTER_STATUSES}")
        else:
            st = st.upper()
            if st not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
            if st == "OUT_OF_SERVICE":
                reason = str(fields.get("out_of_service_reason") or "").upper()
                if reason not in OUT_OF_SERVICE_REASONS:
                    raise ValueError(f"OUT_OF_SERVICE needs a reason: one of {OUT_OF_SERVICE_REASONS}")
                out["out_of_service_reason"] = reason
            else:
                out["out_of_service_reason"] = None
        out["status"] = st
    for f, lo in (("occupancy", 0), ("total_capacity", 1), ("members", 1), ("passenger_capacity", 1)):
        if f in fields:
            try:
                v = int(fields[f])
            except (TypeError, ValueError, OverflowError):   # QA L2: 1e400 is inf
                raise ValueError(f"{f} must be a whole number") from None
            if v < lo or v > 100_000:
                raise ValueError(f"{f} must be between {lo} and 100000")
            out[f] = v
    if "capabilities" in fields:
        caps = fields["capabilities"]
        if not isinstance(caps, list) or not all(isinstance(c, str) and c.strip() for c in caps):
            raise ValueError("capabilities must be a list of names")
        out["capabilities"] = list(dict.fromkeys(c.strip().lower() for c in caps))
    if "lonlat" in fields:
        ll = fields["lonlat"]
        try:
            ok = isinstance(ll, (list, tuple)) and len(ll) == 2 and -180 <= float(ll[0]) <= 180 and -90 <= float(ll[1]) <= 90
        except (TypeError, ValueError, OverflowError):   # QA L2: 1e400 is inf
            ok = False
        if not ok:
            raise ValueError("lonlat must be [lon, lat] numbers")
        out["lonlat"] = [float(ll[0]), float(ll[1])]
    return out


def submit_update(store: OpsStore, *, actor: str, role: str, resource_id: str, fields: dict, observed_at,
                  now_wall: Optional[datetime] = None, client_ref: Optional[str] = None) -> dict:
    now_wall = now_wall or utc_now()
    with store.transaction():
        if client_ref:
            dup = store.list("resource_updates", lambda u: u["actor"] == actor and u.get("client_ref") == client_ref)
            if dup:
                return dup[0]
        r = store.get("resources", resource_id)
        if r is None:
            raise ValueError(f"unknown resource {resource_id}")
        kind = r["kind"]
        if role != "dispatcher":
            if role not in ROLE_FIELDS or kind not in ROLE_FIELDS[role]:
                raise PermissionError(f"a {role} cannot update a {kind}")
            responder = store.get("responders", actor.split(":", 1)[-1]) if actor.startswith("responder:") else None
            if responder is None or resource_id not in responder.get("scope", []):
                raise PermissionError(f"{actor} may not update {resource_id}")
            extra = sorted(set(fields) - ROLE_FIELDS[role][kind])
            if extra:
                raise PermissionError(f"a {role} cannot change {extra}; ask the dispatcher")
        clean = _validate(kind, fields, r)
        observed = parse_time(observed_at) or now_wall
        if observed > now_wall + MAX_CLOCK_SKEW:
            raise ValueError("observed_at is in the future")
        prev = r.get("last_update")
        applied, note = True, "applied"
        if prev and prev.get("observed_at") and observed < parse_time(prev["observed_at"]):
            applied, note = False, (f"not applied: observed {iso(observed)}, older than the update already applied "
                                    f"({prev['observed_at']}); kept for the record")
        uid = f"RU{store.next_seq('resource_update')}"
        rec = {"update_id": uid, "resource_id": resource_id, "kind": kind, "fields": clean, "actor": actor, "role": role,
               "observed_at": iso(observed), "received_at": iso(now_wall), "client_ref": client_ref, "applied": applied,
               "note": note}
        store.put("resource_updates", uid, rec, actor=actor, action="update" if applied else "update_not_applied")
        if applied:
            store.put("resources", resource_id,
                      {**r, **clean, "verified": True, "source": f"update {uid} by {actor}",
                       "last_update": {"update_id": uid, "by": actor, "observed_at": iso(observed),
                                       "received_at": iso(now_wall)}},
                      actor=actor, action="apply_update")
        return rec


# ------------------------------------------------------------------ registration (dispatcher entry, import)
def _text(value: Any, key: str, maxlen: int, *, required: bool = False, phone_check: bool = False) -> str:
    s = "" if value is None else str(value).strip()
    if required and not s:
        raise ValueError(f"{key} is required")
    if len(s) > maxlen:
        raise ValueError(f"{key} must be at most {maxlen} characters")
    if phone_check and _PHONE.search(s):
        raise ValueError(f"{key}: do not record phone numbers here")
    return s


def check_text(key: str, value: Any, maxlen: int, required: bool = False) -> str:
    """Free text from a person: trimmed, length-limited, no phone numbers."""
    return _text(value, key, maxlen, required=required, phone_check=True)


def _pilot_lonlat(value: Any) -> list[float]:
    try:
        ok = isinstance(value, (list, tuple)) and len(value) == 2
        lon, lat = (float(value[0]), float(value[1])) if ok else (None, None)
    except (TypeError, ValueError, OverflowError):   # QA L2: 1e400 is inf
        ok = False
    if not ok:
        raise ValueError("lonlat must be [lon, lat] numbers")
    w, s, e, n = PILOT_BBOX
    if not (w <= lon <= e and s <= lat <= n):
        raise ValueError(f"[{lon:.5f}, {lat:.5f}] is outside the pilot area {list(PILOT_BBOX)} (west, south, east, north)")
    return [round(lon, 6), round(lat, 6)]


def validate_record(kind: str, data: dict) -> tuple[dict, list[str]]:
    """A new team / vehicle / shelter -> (clean record, errors). Every problem is reported, not only the first."""
    if kind not in RECORD_FIELDS:
        return {}, [f"kind must be one of {tuple(RECORD_FIELDS)}, got {str(kind)[:20]!r}"]
    spec = RECORD_FIELDS[kind]
    allowed = set(spec["required"]) | set(spec["optional"])
    errors: list[str] = []
    extra = sorted(set(data) - allowed)
    if extra:
        errors.append(f"field(s) {extra} are not part of a {kind} record; allowed: {sorted(allowed)}")
    missing = [f for f in spec["required"] if f not in data or data[f] in (None, "", [])]
    clean: dict = {}

    def attempt(fn):
        try:
            fn()
        except (ValueError, OverflowError) as e:
            errors.append(str(e))

    attempt(lambda: clean.__setitem__("name", _text(data.get("name"), "name", NAME_MAX, required=True)))
    attempt(lambda: clean.__setitem__("lonlat", _pilot_lonlat(data.get("lonlat"))))
    for f in [m for m in missing if m not in ("name", "lonlat")]:
        errors.append(f"{f} is required for a {kind}")
    for key, maxlen in (("base", BASE_MAX), ("organisation", ORG_MAX), ("address", ADDRESS_MAX)):
        if key in allowed and data.get(key) not in (None, ""):
            attempt(lambda key=key, maxlen=maxlen: clean.__setitem__(key, check_text(key, data.get(key), maxlen)))
    numeric = {k: data[k] for k in ("members", "passenger_capacity", "total_capacity", "occupancy")
               if k in allowed and data.get(k) not in (None, "")}
    for k, v in numeric.items():
        attempt(lambda k=k, v=v: clean.update(_validate(_NUMERIC_KIND[k], {k: v}, {})))
    if kind == "team" and "capabilities" in data and data["capabilities"] not in (None, "", []):
        def caps():
            c = _validate("team", {"capabilities": data["capabilities"]}, {})["capabilities"]
            unknown = [x for x in c if x not in CAPABILITIES]
            if unknown:
                raise ValueError(f"unknown capabilit{'y' if len(unknown) == 1 else 'ies'} {unknown}; known: {list(CAPABILITIES)}")
            clean["capabilities"] = c
        attempt(caps)
    if kind == "vehicle" and data.get("type") not in (None, ""):
        t = str(data["type"]).strip().lower()
        if t in VEHICLE_TYPES:
            clean["type"] = t
        else:
            errors.append(f"type must be a known vehicle type with a depth limit: {list(VEHICLE_TYPES)}")
    status = data.get("status")
    if kind in ("team", "vehicle"):
        st = {"status": status or "AVAILABLE", "out_of_service_reason": data.get("out_of_service_reason")}
        attempt(lambda: clean.update(_validate(kind, st, {})))
    elif kind == "shelter":
        attempt(lambda: clean.update(_validate("shelter", {"status": status or "open"}, {})))
        if "occupancy" not in numeric:
            clean["occupancy"] = 0
        if isinstance(clean.get("occupancy"), int) and isinstance(clean.get("total_capacity"), int) \
                and clean["occupancy"] > clean["total_capacity"]:
            errors.append(f"occupancy {clean['occupancy']} must not exceed total_capacity {clean['total_capacity']}")
    return clean, errors


_NUMERIC_KIND = {"members": "team", "passenger_capacity": "vehicle", "total_capacity": "shelter", "occupancy": "shelter"}


def _observed(value: Any, now_wall: datetime, *, default_now: bool) -> Optional[datetime]:
    observed = parse_time(value) if value not in (None, "") else None
    if observed is None and default_now:
        observed = now_wall
    if observed is not None and observed > now_wall + MAX_CLOCK_SKEW:
        raise ValueError("observed_at is in the future")
    return observed


def _names(store: OpsStore, kind: str) -> set[str]:
    return {str(r.get("name", "")).strip().lower() for r in store.list("resources", lambda r: r.get("kind") == kind)}


def _new_id(store: OpsStore, kind: str) -> str:
    while True:
        rid = f"{_ID_PREFIX[kind]}{store.next_seq('registry_' + kind)}"
        if store.get("resources", rid) is None:
            return rid


def _insert(store: OpsStore, kind: str, clean: dict, *, actor: str, source: str, observed: Optional[datetime],
            now_wall: datetime, client_ref: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    rid = _new_id(store, kind)
    prov = {"tag": "REPORTED", "source": source, "note": "entered by an authorised person; not checked by FloodNet"}
    body = {**clean, **(extra or {}), "id": rid, "kind": kind, "source": source, "registered_source": source,
            "verified": True, "entered_by": actor, "client_ref": client_ref,
            "location_provenance": prov, "numbers_provenance": prov,
            "last_update": {"update_id": f"registration of {rid}", "by": actor,
                            "observed_at": iso(observed) if observed else None, "received_at": iso(now_wall)}}
    return store.put("resources", rid, body, actor=actor, action="register")


def register_resource(store: OpsStore, kind: str, data: dict, *, actor: str = "dispatcher",
                      source: str = DISPATCHER_SOURCE, observed_at: Any = None, now_wall: Optional[datetime] = None,
                      client_ref: Optional[str] = None) -> dict:
    """One record entered by the dispatcher. observed_at defaults to now: the person entering it vouches for it now."""
    now_wall = now_wall or utc_now()
    clean, errors = validate_record(kind, data if isinstance(data, dict) else {})
    if errors:
        raise ValueError("; ".join(errors))
    observed = _observed(observed_at, now_wall, default_now=True)
    ref = str(client_ref)[:80] if client_ref else None
    with store.transaction():
        if ref:
            dup = store.list("resources", lambda r: r.get("entered_by") == actor and r.get("client_ref") == ref)
            if dup:
                return _view(dup[0], now_wall)
        if clean["name"].lower() in _names(store, kind):
            raise ValueError(f"a {kind} named {clean['name']!r} already exists; names must be unique per kind")
        return _view(_insert(store, kind, clean, actor=actor, source=source, observed=observed, now_wall=now_wall,
                             client_ref=ref), now_wall)


def parse_import(text: str, fmt: str) -> list[dict]:
    """JSON (a list of objects, or {"records": [...]}) or CSV with a header row including `kind`. File-level problems
    raise ValueError; row-level problems are left to import_records."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the import is empty")
    if len(text) > IMPORT_MAX_CHARS:
        raise ValueError(f"the import is larger than {IMPORT_MAX_CHARS} characters")
    fmt = str(fmt).lower()
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})") from None
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            data = data["records"]
        if not isinstance(data, list):
            raise ValueError('JSON must be a list of records or {"records": [...]}')
        rows = data
    elif fmt == "csv":
        reader = csv.DictReader(io.StringIO(text.strip().lstrip("\ufeff")))
        header = [h.strip().lower() for h in (reader.fieldnames or []) if h]
        if "kind" not in header:
            raise ValueError(f"the CSV needs a 'kind' column (team, vehicle or shelter); columns: {list(IMPORT_COLUMNS)}")
        rows = []
        for row in reader:
            clean = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if any(clean.values()):
                rows.append(clean)
    else:
        raise ValueError("format must be json or csv")
    if not rows:
        raise ValueError("no rows found")
    if len(rows) > IMPORT_MAX_ROWS:
        raise ValueError(f"at most {IMPORT_MAX_ROWS} rows per import, got {len(rows)}")
    return rows


def _row_to_record(raw: Any) -> tuple[str, dict, Any, list[str]]:
    if not isinstance(raw, dict):
        return "", {}, None, ["the row must be an object"]
    kind = str(raw.get("kind") or "").strip().lower()
    data = {str(k).strip().lower(): v for k, v in raw.items() if k not in ("kind", "observed_at") and v not in (None, "")}
    if "lon" in data or "lat" in data:
        data["lonlat"] = [data.pop("lon", None), data.pop("lat", None)]
    if isinstance(data.get("capabilities"), str):
        data["capabilities"] = [c for c in re.split(r"[;|,]", data["capabilities"]) if c.strip()]
    return kind, data, raw.get("observed_at"), []


def import_records(store: OpsStore, rows: list, *, label: str, actor: str = "dispatcher", commit: bool = False,
                   now_wall: Optional[datetime] = None) -> dict:
    """Validate every row (dry run) and, with commit=True and no row in error, insert them all in one transaction."""
    now_wall = now_wall or utc_now()
    label = _text(label, "label", 80, required=True)
    if len(rows) > IMPORT_MAX_ROWS:
        raise ValueError(f"at most {IMPORT_MAX_ROWS} rows per import, got {len(rows)}")
    source = f"import: {label}"
    with store.transaction():
        seen = {k: _names(store, k) for k in RECORD_FIELDS}
        preview = []
        for i, raw in enumerate(rows, 1):
            kind, data, obs_raw, errors = _row_to_record(raw)
            clean: dict = {}
            observed = None
            if not errors:
                clean, errors = validate_record(kind, data)
                try:
                    observed = _observed(obs_raw, now_wall, default_now=False)
                except ValueError as e:
                    errors.append(str(e))
            name = clean.get("name") or (str(raw.get("name"))[:NAME_MAX] if isinstance(raw, dict) and raw.get("name") else None)
            if not errors and kind in seen:
                if clean["name"].lower() in seen[kind]:
                    errors.append(f"a {kind} named {clean['name']!r} already exists (in the registry or earlier in this file)")
                else:
                    seen[kind].add(clean["name"].lower())
            preview.append({"row": i, "kind": kind or None, "name": name, "errors": errors,
                            "record": clean if not errors else None,
                            "observed_at": iso(observed) if observed else None,
                            "freshness": ("CURRENT" if observed and (now_wall - observed).total_seconds() / 60 <= STALE_AFTER_MIN
                                          else "STALE" if observed else "UNKNOWN")})
        invalid = sum(1 for p in preview if p["errors"])
        out = {"label": label, "source": source, "rows": preview, "valid": len(preview) - invalid, "invalid": invalid,
               "committed": False, "ids": []}
        if not commit:
            return out
        if invalid:
            raise ValueError(f"{invalid} row(s) have errors; nothing was imported")
        for p in preview:
            obs = parse_time(p["observed_at"]) if p["observed_at"] else None
            out["ids"].append(_insert(store, p["kind"], p["record"], actor=actor, source=source, observed=obs,
                                      now_wall=now_wall)["id"])
        out["committed"] = True
        return out


# ------------------------------------------------------------------ candidate shelters
def _candidate_view(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}


def get_candidate(store: OpsStore, cid: str) -> dict:
    c = store.get("shelter_candidates", cid)
    if c is None:
        raise NotFound(f"unknown candidate {cid}")
    return _candidate_view(c)


def list_candidates(store: OpsStore, actor: Optional[str] = None) -> list[dict]:
    return [_candidate_view(c) for c in store.list("shelter_candidates", lambda c: actor is None or c.get("actor") == actor)]


def volunteer_view(c: dict) -> dict:
    """What a volunteer sees of their own submission: no dispatcher notes, no permission details."""
    return {k: c.get(k) for k in ("candidate_id", "name", "state", "received_at")} | {
        "rejection_reason": (c.get("rejection") or {}).get("reason")}


def submit_candidate(store: OpsStore, data: dict, *, actor: str, submitted_by: str,
                     now_wall: Optional[datetime] = None, client_ref: Optional[str] = None) -> dict:
    now_wall = now_wall or utc_now()
    allowed = {"name", "lonlat", "address", "description", "capacity_estimate", "observed_at"}
    extra = sorted(set(data) - allowed)
    if extra:
        raise ValueError(f"field(s) {extra} are not part of a candidate; allowed: {sorted(allowed)}")
    name = check_text("name", data.get("name"), NAME_MAX, required=True)
    lonlat = _pilot_lonlat(data.get("lonlat"))
    address = check_text("address", data.get("address"), ADDRESS_MAX)
    description = check_text("description", data.get("description"), DESCRIPTION_MAX)
    cap = None
    if data.get("capacity_estimate") not in (None, ""):
        if not _is_int_ge1(data["capacity_estimate"]) or int(data["capacity_estimate"]) > 100_000:
            raise ValueError("capacity_estimate must be a whole number between 1 and 100000")
        cap = int(data["capacity_estimate"])
    observed = _observed(data.get("observed_at"), now_wall, default_now=True)
    ref = str(client_ref)[:80] if client_ref else None
    with store.transaction():
        if ref:
            dup = store.list("shelter_candidates", lambda c: c.get("actor") == actor and c.get("client_ref") == ref)
            if dup:
                return _candidate_view(dup[0])
        if actor != "dispatcher":
            open_ = store.list("shelter_candidates", lambda c: c.get("actor") == actor and c["state"] not in ("CONFIRMED", "REJECTED"))
            if len(open_) >= MAX_OPEN_CANDIDATES_PER_SUBMITTER:
                raise ValueError(f"{len(open_)} open submissions already (limit {MAX_OPEN_CANDIDATES_PER_SUBMITTER}); "
                                 "wait for the dispatcher to check them")
        cid = f"CS{store.next_seq('shelter_candidate')}"
        doc = {"candidate_id": cid, "state": "SUBMITTED", "name": name, "lonlat": lonlat, "address": address,
               "description": description, "capacity_estimate": cap, "actor": actor, "submitted_by": submitted_by,
               "observed_at": iso(observed), "received_at": iso(now_wall), "client_ref": ref,
               "permission": None, "suitability": None, "rejection": None, "shelter_id": None,
               "history": [{"state": "SUBMITTED", "at": iso(now_wall), "by": actor}],
               "note": "candidate only: not a shelter and not used by the planner until a dispatcher confirms it"}
        return _candidate_view(store.put("shelter_candidates", cid, doc, actor=actor, action="candidate_submit"))


def _is_int_ge1(v: Any) -> bool:
    try:
        return int(v) >= 1 and float(v) == int(v)
    except (TypeError, ValueError, OverflowError):   # QA L2: 1e400 is inf
        return False


def _advance(store: OpsStore, cid: str, need: tuple[str, ...], new_state: str, changes: dict, *, actor: str,
             action: str, now_wall: datetime) -> dict:
    with store.transaction():
        c = store.get("shelter_candidates", cid)
        if c is None:
            raise NotFound(f"unknown candidate {cid}")
        if c["state"] not in need:
            raise ValueError(f"candidate {cid} is {c['state']}; this step needs {' or '.join(need)}")
        changes = changes(c) if callable(changes) else changes
        hist = list(c.get("history", [])) + [{"state": new_state, "at": iso(now_wall), "by": actor}]
        return _candidate_view(store.put("shelter_candidates", cid, {**c, **changes, "state": new_state, "history": hist},
                                         actor=actor, action=action))


def record_permission(store: OpsStore, cid: str, data: dict, *, actor: str = "dispatcher",
                      now_wall: Optional[datetime] = None) -> dict:
    """Who granted permission (role, optional name -- no phone number), how and when."""
    now_wall = now_wall or utc_now()
    role = check_text("granted_by_role", data.get("granted_by_role"), 80, required=True)
    name = check_text("granted_by_name", data.get("granted_by_name"), 80)
    method = str(data.get("method") or "").strip().upper()
    if method not in PERMISSION_METHODS:
        raise ValueError(f"method must be one of {PERMISSION_METHODS}")
    if data.get("granted_at") in (None, ""):
        raise ValueError("granted_at is required: when the owner or manager gave permission")
    granted = parse_time(data["granted_at"])
    if granted > now_wall + MAX_CLOCK_SKEW:
        raise ValueError("granted_at is in the future")
    note = check_text("note", data.get("note"), NOTE_MAX)
    perm = {"granted_by_role": role, "granted_by_name": name, "method": method, "granted_at": iso(granted),
            "note": note, "recorded_by": actor, "recorded_at": iso(now_wall)}
    return _advance(store, cid, ("SUBMITTED",), "PERMISSION_CONFIRMED", {"permission": perm}, actor=actor,
                    action="candidate_permission", now_wall=now_wall)


def record_suitability(store: OpsStore, cid: str, data: dict, *, actor: str = "dispatcher", site_check: dict,
                       now_wall: Optional[datetime] = None) -> dict:
    """The dispatcher's checklist plus the server's modelled depth at the site (site_flood_check), stored together."""
    now_wall = now_wall or utc_now()
    out = {}
    for item in CHECK_ITEMS:
        v = str(data.get(item) or "").strip().upper()
        if v not in CHECK_ANSWERS:
            raise ValueError(f"{item} must be one of {CHECK_ANSWERS}")
        out[item] = v
    if not _is_int_ge1(data.get("capacity_estimate")) or int(data["capacity_estimate"]) > 100_000:
        raise ValueError("capacity_estimate must be a whole number between 1 and 100000")
    out["capacity_estimate"] = int(data["capacity_estimate"])
    out["notes"] = check_text("notes", data.get("notes"), NOTE_MAX)
    out.update(modelled_flood_depth=dict(site_check), recorded_by=actor, recorded_at=iso(now_wall))
    return _advance(store, cid, ("PERMISSION_CONFIRMED",), "SUITABILITY_CHECKED", {"suitability": out}, actor=actor,
                    action="candidate_suitability", now_wall=now_wall)


def confirm_candidate(store: OpsStore, cid: str, data: dict, *, actor: str = "dispatcher",
                      now_wall: Optional[datetime] = None) -> dict:
    """SUITABILITY_CHECKED -> CONFIRMED: create the registry shelter and link it, in one transaction."""
    now_wall = now_wall or utc_now()
    extra = sorted(set(data) - {"total_capacity", "occupancy"})
    if extra:
        raise ValueError(f"field(s) {extra} cannot be set when confirming; allowed: ['occupancy', 'total_capacity']")
    with store.transaction():
        c = store.get("shelter_candidates", cid)
        if c is None:
            raise NotFound(f"unknown candidate {cid}")
        if c["state"] != "SUITABILITY_CHECKED":
            raise ValueError(f"candidate {cid} is {c['state']}; this step needs SUITABILITY_CHECKED")
        cap = data.get("total_capacity")
        rec = {"name": c["name"], "lonlat": c["lonlat"], "status": "open",
               "total_capacity": cap if cap not in (None, "") else c["suitability"]["capacity_estimate"],
               "occupancy": data.get("occupancy") if data.get("occupancy") not in (None, "") else 0}
        if c.get("address"):
            rec["address"] = c["address"]
        clean, errors = validate_record("shelter", rec)
        if errors:
            raise ValueError("; ".join(errors))
        if clean["name"].lower() in _names(store, "shelter"):
            raise ValueError(f"a shelter named {clean['name']!r} already exists; reject this candidate or rename the shelter")
        sh = _insert(store, "shelter", clean, actor=actor, source=f"{CANDIDATE_SOURCE} (candidate {cid})",
                     observed=now_wall, now_wall=now_wall,
                     extra={"candidate_id": cid, "modelled_flood_depth": (c.get("suitability") or {}).get("modelled_flood_depth")})
        return _advance(store, cid, ("SUITABILITY_CHECKED",), "CONFIRMED", {"shelter_id": sh["id"]}, actor=actor,
                        action="candidate_confirm", now_wall=now_wall)


def reject_candidate(store: OpsStore, cid: str, reason: str, *, actor: str = "dispatcher",
                     now_wall: Optional[datetime] = None) -> dict:
    now_wall = now_wall or utc_now()
    reason = check_text("reason", reason, NOTE_MAX, required=True)
    return _advance(store, cid, ("SUBMITTED", "PERMISSION_CONFIRMED", "SUITABILITY_CHECKED"), "REJECTED",
                    {"rejection": {"reason": reason, "by": actor, "at": iso(now_wall)}}, actor=actor,
                    action="candidate_reject", now_wall=now_wall)


def _road_pairs(roads) -> tuple:
    """Every straight piece of every road polyline as arrays (start lon/lat, end lon/lat, segment index); cached."""
    cache = getattr(roads, "_registry_pairs", None)
    if cache is None:
        a, b, idx = [], [], []
        for i, seg in enumerate(roads.segments):
            ll = np.asarray(seg.lonlat, dtype=float)
            if len(ll) >= 2:
                a.append(ll[:-1]); b.append(ll[1:]); idx.append(np.full(len(ll) - 1, i))
        cache = (np.vstack(a), np.vstack(b), np.concatenate(idx)) if a else None
        try:
            object.__setattr__(roads, "_registry_pairs", cache)
        except (AttributeError, TypeError):
            pass
    return cache


def _nearest_segment(roads, lon: float, lat: float) -> tuple[float, Any]:
    """(distance in metres, segment) of the road piece closest to the point; same local projection as navigation."""
    pairs = _road_pairs(roads)
    if pairs is None:
        return float("inf"), None
    a_ll, b_ll, idx = pairs
    origin = np.array([[lon, lat]], dtype=float)
    a = _local(a_ll - origin, lat)                  # the point sits at (0, 0)
    ab = _local(b_ll - origin, lat) - a
    l2 = np.einsum("ij,ij->i", ab, ab)
    t = np.clip(np.divide(-np.einsum("ij,ij->i", a, ab), l2, out=np.zeros_like(l2), where=l2 > 0), 0.0, 1.0)
    d = np.hypot(*(a + t[:, None] * ab).T)
    k = int(np.argmin(d))
    return float(d[k]), roads.segments[int(idx[k])]


def site_flood_check(store: OpsStore, lonlat, roads, seg_ctx=None) -> dict:
    """Modelled depth at a site: the largest depth over the published forecast on the nearest road segment within
    NEAREST_ROAD_MAX_M; UNKNOWN when nothing is published, no road is near, or the road is not linked to the model."""
    base = {"status": "UNKNOWN", "method": (f"nearest road within {NEAREST_ROAD_MAX_M:g} m (ASSUMED); largest modelled "
                                            "street depth over the published forecast"), "note": SITE_CHECK_NOTE}
    fc = inc.get_forecast(store)
    if fc is None:
        return {**base, "reason": "no forecast has been published to live operations"}
    if roads is None:
        return {**base, "reason": "the road network is not loaded on this server"}
    forecast, meta = fc
    best_d, best = _nearest_segment(roads, float(lonlat[0]), float(lonlat[1]))
    if best is None or best_d > NEAREST_ROAD_MAX_M:
        return {**base, "reason": (f"no road within {NEAREST_ROAD_MAX_M:g} m of the site (nearest "
                                   f"{best_d:.0f} m); the model gives street depths only")}
    name = best.name or "unnamed road"
    where = {"seg_id": best.seg_id, "road_name": name, "distance_m": round(best_d, 1)}
    if seg_ctx is not None and best.seg_id in getattr(seg_ctx, "unlinked", ()):
        return {**base, **where, "reason": f"the nearest road ({name}) is not linked to the flood model; depth not known"}
    row = forecast.streets_cm.get(best.seg_id)
    if row:
        k = max(range(len(row)), key=lambda i: row[i])
        depth, peak = float(row[k]), (float(forecast.t_min[k]) if row[k] > 0 else None)
    else:
        depth, peak = 0.0, None                         # absent from the published series: the model has it dry
    return {**base, **where, "status": "MODELLED", "reason": "", "max_depth_cm": round(depth, 1), "peak_t_min": peak,
            "window_t_min": [float(forecast.t_min[0]), float(forecast.t_min[-1])],
            "dem_flagged": bool(seg_ctx is not None and best.seg_id in getattr(seg_ctx, "dem_flagged", ())),
            "forecast_version": meta["forecast_version"], "forecast_label": meta["label"],
            "data_mode": meta["data_mode"], "run_id": meta["run_id"]}


# ------------------------------------------------------------------ planner input
_PLANNER_DROP = {"kind", "last_update", "client_ref", "entered_by"}


def planner_resources(store: OpsStore, now_wall: Optional[datetime] = None) -> dict:
    """The registry in the shape of fixtures.demo_resources(): settlements stay the demonstration needs; teams,
    vehicles and shelters carry the registry state. Unavailable vehicles reach the planner as available=False.
    Records registered after the seed (dispatcher entry, import, confirmed candidates) are added; candidate shelters
    that are not CONFIRMED are in another collection and never reach the planner."""
    now_wall = now_wall or utc_now()
    base = fx.demo_resources()
    rows = {r["id"]: r for r in list_resources(store, now_wall)}
    keep = {"stale", "age_min", "verified", "source", "freshness"}
    # Direct trips (started on the Roads page, ops/trips.py kind "direct") commit a vehicle outside the planner's
    # ledger; mission trips are already in the ledger. Read from the store: no import of ops/trips.py (cycle).
    on_direct: dict[str, list[str]] = {}
    for t in store.list("trips", lambda t: t.get("kind") == "direct" and t.get("stage") not in ("COMPLETED", "ABORTED")):
        if t.get("vehicle_id"):
            on_direct.setdefault(t["vehicle_id"], []).append(t.get("trip_id") or "?")

    def unavailable(r, kind):
        reasons = []
        if kind in ("vehicle", "team") and r.get("status", "AVAILABLE") != "AVAILABLE":
            why = (f"out of service ({r.get('out_of_service_reason')})" if r["status"] == "OUT_OF_SERVICE"
                   else "assigned elsewhere (registry)")
            reasons.append(f"{why}; {r.get('source', '')}")
        if kind == "vehicle" and r.get("id") in on_direct:
            reasons.append(f"on rescue trip {', '.join(on_direct[r['id']])} (started on the Roads page)")
        if reasons:
            r.update(available=False, unavailable_reason="; ".join(reasons))
        return r

    def merged(kind_rows, kind):
        out = [unavailable({**r0, **{k: v for k, v in rows.get(r0["id"], {}).items() if k in FIELDS[kind] | keep}}, kind)
               for r0 in kind_rows]
        seeded = {r0["id"] for r0 in kind_rows}
        out += [unavailable({k: v for k, v in r.items() if k not in _PLANNER_DROP}, kind)
                for r in rows.values() if r.get("kind") == kind and r["id"] not in seeded]
        return out

    return {**base, "tag": ("registry state: SIMULATED seed on REAL locations + verified updates; records entered by an "
                            "authorised person are REPORTED"),
            "teams": merged(base["teams"], "team"), "vehicles": merged(base["vehicles"], "vehicle"), "shelters": merged(base["shelters"], "shelter")}
