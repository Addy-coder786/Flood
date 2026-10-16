"""Rescue trips (DECISIONS D-41): a trip is a dispatched assignment, not a second model of the mission.

The server keeps what only the field knows: the trip stage, GPS fixes, driver reports and accepted reroutes. The
dispatcher's ledger keeps the allocation; its status follows the stage through ledger_status_for_stage(). Stages only
move forward; ABORTED is a dispatcher decision with a reason. A driver token opens one trip and is shown once.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .. import config
from . import incident as inc
from .auth import hash_token, new_token, token_matches
from .navigation import parse_time
from .store import OpsStore, iso, utc_now

STAGES = ("ASSIGNED", "EN_ROUTE", "AT_PICKUP", "AT_SETTLEMENT", "LOADED", "AT_SHELTER", "COMPLETED", "ABORTED")
# A direct trip (D-49) is one route from A to B started on the Roads page, not a planner mission: the dispatcher
# starts it (PLANNED), the driver accepts it, drives, arrives and completes it.
DIRECT_STAGES = ("PLANNED", "ACCEPTED", "EN_ROUTE", "ARRIVED", "COMPLETED", "ABORTED")
TERMINAL = ("COMPLETED", "ABORTED")
_LEDGER = {"ASSIGNED": "DISPATCHED", "EN_ROUTE": "DISPATCHED", "AT_PICKUP": "DISPATCHED", "AT_SETTLEMENT": "DISPATCHED",
           "LOADED": "IN_PROGRESS", "AT_SHELTER": "IN_PROGRESS", "COMPLETED": "COMPLETED", "ABORTED": "CANCELLED"}
REPORT_KINDS = ("blockage", "high_water", "delay", "diversion", "other")
POSITION_SOURCES = ("DEVICE_GPS", "SIMULATED")
MAX_CLOCK_SKEW = timedelta(minutes=2)


class NoFeasibleRoute(ValueError):
    """No route for this vehicle under the closures and forecast known now: a person decides (escalation)."""


def ledger_status_for_stage(stage: str) -> str:
    return _LEDGER[stage]


def stages_for(trip: dict) -> tuple[str, ...]:
    return DIRECT_STAGES if trip.get("kind") == "direct" else STAGES


def ledger_status_for_trip(trip: dict) -> Optional[str]:
    """The planner ledger status a trip implies; None for a direct trip, which has no planner assignment."""
    return None if trip.get("kind") == "direct" else _LEDGER[trip["stage"]]


def _is_dispatcher(actor: str) -> bool:
    return actor == "dispatcher" or actor.startswith("dispatcher:")


def active_trips(store: OpsStore) -> list[dict]:
    return store.list("trips", lambda t: t["stage"] not in TERMINAL)


def direct_leg(rt, origin, dest, *, vehicle_type: str, depart_t_min: float, to_name: str = "destination") -> dict:
    """One leg from `origin` to `dest` (lon, lat) by the arrival-time router `rt` (a planner.Router), in the shape of
    a mission leg so the impact rules (impact.py) treat it the same. Raises NoFeasibleRoute with the router's reason."""
    o, d = rt.node(origin), rt.node(dest)
    if o == d:
        raise ValueError("origin and destination snap to the same road point; choose points further apart")
    r = rt.leg(o, d, vehicle_type, float(depart_t_min))
    if not r["reachable"]:
        raise NoFeasibleRoute(r.get("unreachable_reason") or "no route under the closures and forecast known now")
    return {"kind": "to_destination", "to": to_name, "from_node": int(o), "to_node": int(d),
            "depart_t_min": round(float(depart_t_min), 2), "arrive_t_min": round(r["arrival_t_min"], 2),
            "done_t_min": round(r["arrival_t_min"], 2), "dwell_min": 0.0, "travel_time_min": r["travel_time_min"],
            "length_m": r.get("length_m", 0.0), "route": r["route"], "status_counts": r["status_counts_at_arrival"],
            "segments": [{k: s[k] for k in ("seg_id", "name", "enter_t_min", "exit_t_min", "to_node", "status", "depth_cm")}
                         for s in r["segments"]]}


def same_start(trip: dict, incident_id, origin, dest) -> bool:
    """Is `trip` the trip a repeated Start Trip click (same key) asked for: same incident and the same two points?"""
    pts = lambda p: [round(float(x), 6) for x in p] if p else None  # noqa: E731
    return (trip.get("incident_id") == incident_id and pts(trip.get("origin")) == pts(origin)
            and pts(trip.get("dest")) == pts(dest))


def create_direct_trip(store: OpsStore, leg: dict, *, vehicle_type: str, now_t: float, names: Optional[dict] = None,
                       actor: str = "dispatcher", incident_id: Optional[str] = None, vehicle_id: Optional[str] = None,
                       client_ref: Optional[str] = None, eta: Optional[dict] = None,
                       origin=None, dest=None) -> tuple[dict, Optional[str]]:
    """Start a direct trip (stage PLANNED). Returns (trip, driver token); a repeated `client_ref` (a double click)
    returns the first trip and no token. A registry vehicle can be on one active trip at a time."""
    if vehicle_type not in config.VEHICLE_LIMIT_CM:
        raise ValueError(f"unknown vehicle type {vehicle_type!r}")
    with store.transaction():
        if client_ref:
            same = [t for t in store.list("trips", lambda t: t.get("client_ref") == client_ref)
                    if same_start(t, incident_id, origin, dest)]
            if same:
                return same[0], None
        if vehicle_id:
            busy = [t for t in active_trips(store) if t.get("vehicle_id") == vehicle_id]
            if busy:
                raise ValueError(f"vehicle {vehicle_id} is already on trip {busy[0]['trip_id']} ({busy[0]['stage']})")
        token = new_token()
        trip_id = f"TR{store.next_seq('trip')}"
        trip = {"trip_id": trip_id, "kind": "direct", "incident_id": incident_id, "assignment_id": None,
                "plan_version": None, "team_id": None, "vehicle_id": vehicle_id, "vehicle_type": vehicle_type,
                "settlement_id": None, "shelter_id": None, "people": None, "names": dict(names or {}),
                "origin": list(origin) if origin else None, "dest": list(dest) if dest else None,
                "legs": [dict(leg)], "route_version": 1, "eta": eta, "client_ref": client_ref,
                "stage": "PLANNED", "created_t_min": float(now_t),
                "stage_history": [{"stage": "PLANNED", "t_min": float(now_t), "at": iso(utc_now()), "actor": actor,
                                   "reason": "trip started by the dispatcher"}],
                "driver_token_hash": hash_token(token), "last_position": None, "proposals": [], "escalation": None,
                "attention": None, "last_assessment": None}
        return store.put("trips", trip_id, trip, actor=actor, action="create_direct"), token


def create_trip(store: OpsStore, assignment: dict, *, vehicle_type: str, now_t: float,
                names: Optional[dict] = None, actor: str = "dispatcher",
                incident_id: Optional[str] = None) -> tuple[dict, Optional[str]]:
    """Trip for an ACCEPTED or DISPATCHED assignment. Returns (trip, driver token); the token only on creation --
    asking again for an assignment that already has an active trip returns that trip and None."""
    if assignment.get("status") not in ("ACCEPTED", "DISPATCHED"):
        raise ValueError(f"only ACCEPTED or DISPATCHED assignments become trips (got {assignment.get('status')})")
    if not assignment.get("legs"):
        raise ValueError("the assignment has no route legs; replan first")
    if vehicle_type not in config.VEHICLE_LIMIT_CM:
        raise ValueError(f"unknown vehicle type {vehicle_type!r}")
    with store.transaction():
        # assignment ids (A1, A2 ...) repeat in every new incident: a trip belongs to one incident
        existing = [t for t in active_trips(store) if t["assignment_id"] == assignment["assignment_id"]
                    and t.get("incident_id") == incident_id]
        if existing:
            return existing[0], None
        vid = assignment.get("vehicle_id")
        busy = [t for t in active_trips(store) if t.get("kind") == "direct" and vid and t.get("vehicle_id") == vid]
        if busy:                                   # QA 2026-10-11 H2: one vehicle, one active trip
            raise ValueError(f"vehicle {vid} is on direct trip {busy[0]['trip_id']} ({busy[0]['stage']}); finish or "
                             "abort it first, or replan with another vehicle")
        token = new_token()
        trip_id = f"TR{store.next_seq('trip')}"
        legs = [{k: v for k, v in leg.items()} for leg in assignment["legs"]]
        trip = {"trip_id": trip_id, "incident_id": incident_id, "assignment_id": assignment["assignment_id"],
                "plan_version": assignment.get("plan_version"), "team_id": assignment.get("team_id"),
                "vehicle_id": assignment.get("vehicle_id"), "vehicle_type": vehicle_type,
                "settlement_id": assignment.get("settlement_id"), "shelter_id": assignment.get("shelter_id"),
                "people": assignment.get("people"), "names": dict(names or {}), "legs": legs, "route_version": 1,
                "stage": "ASSIGNED", "created_t_min": float(now_t),
                "stage_history": [{"stage": "ASSIGNED", "t_min": float(now_t), "at": iso(utc_now()), "actor": actor, "reason": ""}],
                "driver_token_hash": hash_token(token), "last_position": None, "proposals": [], "escalation": None,
                "attention": None, "last_assessment": None}
        return store.put("trips", trip_id, trip, actor=actor, action="create"), token


def get_trip(store: OpsStore, trip_id: str) -> dict:
    t = store.get("trips", trip_id)
    if t is None:
        raise KeyError(f"unknown trip {trip_id}")
    return t


def trip_for_token(store: OpsStore, token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    for t in store.list("trips"):
        if token_matches(token, t["driver_token_hash"]):
            return t
    return None


def rotate_driver_token(store: OpsStore, trip_id: str, actor: str = "dispatcher") -> str:
    token = new_token()
    with store.transaction():                     # read and write together: a GPS fix in between is not lost (QA M3)
        t = get_trip(store, trip_id)
        store.put("trips", trip_id, {**t, "driver_token_hash": hash_token(token)}, actor=actor, action="rotate_driver_token")
    return token


def set_stage(store: OpsStore, trip_id: str, stage: str, *, actor: str, now_t: float, reason: str = "") -> dict:
    with store.transaction():
        t = get_trip(store, trip_id)
        stages = stages_for(t)
        if stage not in stages:
            raise ValueError(f"stage must be one of {stages}")
        if t["stage"] in TERMINAL:
            raise ValueError(f"trip {trip_id} is finished ({t['stage']})")
        if stage == "ABORTED":
            if not _is_dispatcher(actor):
                raise ValueError("only the dispatcher can abort a trip")
            if not reason.strip():
                raise ValueError("aborting a trip needs a reason")
        elif stages.index(stage) <= stages.index(t["stage"]):
            raise ValueError(f"stages only move forward: {t['stage']} -> {stage} would go backwards or repeat")
        hist = t["stage_history"] + [{"stage": stage, "t_min": float(now_t), "at": iso(utc_now()), "actor": actor,
                                      "reason": reason.strip()}]
        return store.put("trips", trip_id, {**t, "stage": stage, "stage_history": hist}, actor=actor, action=f"stage:{stage}")


def record_position(store: OpsStore, trip_id: str, fix: dict, *, actor: str, now_wall: Optional[datetime] = None) -> dict:
    """Validate and keep one GPS fix with the device time and the server receipt time. An older fix never replaces
    a newer one as the trip's last position."""
    now_wall = now_wall or utc_now()
    try:
        lon, lat = float(fix["lon"]), float(fix["lat"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("lon and lat (numbers) are required") from None
    if not -90 <= lat <= 90:
        raise ValueError("latitude out of range")
    if not -180 <= lon <= 180:
        raise ValueError("longitude out of range")
    acc = fix.get("accuracy_m")
    acc = None if acc is None else float(acc)
    if acc is not None and not 0 <= acc <= 10_000:
        raise ValueError("accuracy_m out of range")
    recorded = parse_time(fix.get("recorded_at")) or now_wall
    if recorded > now_wall + MAX_CLOCK_SKEW:
        raise ValueError("recorded_at is in the future (device clock?)")
    source = str(fix.get("source") or "DEVICE_GPS").upper()
    if source not in POSITION_SOURCES:
        raise ValueError(f"source must be one of {POSITION_SOURCES}")
    if source == "SIMULATED" and not _is_dispatcher(actor):
        raise ValueError("SIMULATED positions may only be sent by the dispatcher (demonstration replay)")
    with store.transaction():
        t = get_trip(store, trip_id)
        if t["stage"] in TERMINAL:
            raise ValueError(f"trip {trip_id} is finished ({t['stage']})")
        p = {"trip_id": trip_id, "lon": lon, "lat": lat, "accuracy_m": acc, "recorded_at": iso(recorded),
             "received_at": iso(now_wall), "source": source, "actor": actor}
        pid = f"{trip_id}:{store.next_seq('position')}"
        store.put("positions", pid, p, actor=actor, action="position")
        last = t.get("last_position")
        if last is None or parse_time(last["recorded_at"]) <= recorded:
            store.put("trips", trip_id, {**t, "last_position": p}, actor=actor, action="last_position")
    return p


def _restriction_for(vehicle_type: str) -> list[str]:
    """High water reported by this vehicle binds every vehicle whose ASSUMED wading limit is no higher."""
    limit = config.VEHICLE_LIMIT_CM[vehicle_type]
    types = sorted(v for v, cm in config.VEHICLE_LIMIT_CM.items() if cm <= limit)
    return [] if len(types) == len(config.VEHICLE_LIMIT_CM) else types


def report(store: OpsStore, trip_id: str, kind: str, *, actor: str, now_t: float, known_segments: set,
           seg_id: Optional[str] = None, note: str = "", client_ref: Optional[str] = None,
           name: Optional[str] = None) -> dict:
    """A driver's observation. Blockage and high water on a road become a FIELD_TEAM closure (binding, D-32) and so
    reach every planner and active trip; delays and diversions are recorded for the dispatcher."""
    if kind not in REPORT_KINDS:
        raise ValueError(f"report kind must be one of {REPORT_KINDS}")
    t = get_trip(store, trip_id)
    if t["stage"] in TERMINAL:                   # a finished trip's link cannot create binding closures (QA M4)
        raise ValueError(f"trip {trip_id} is finished ({t['stage']}); reports are closed")
    if client_ref:
        dup = store.list("trip_reports", lambda r: r["trip_id"] == trip_id and r.get("client_ref") == client_ref)
        if dup:
            ev = store.get("road_events", dup[0]["road_event_id"]) if dup[0].get("road_event_id") else None
            return {"report": dup[0], "road_event": ev}
    road_event = None
    if kind in ("blockage", "high_water"):
        if not seg_id or seg_id not in known_segments:
            raise ValueError("a blockage or high-water report must name a known road")
        body = {"seg_id": seg_id, "kind": "closure", "received_t_min": float(now_t), "note": note[:300],
                "client_ref": client_ref, "name": name, "incident_id": t.get("incident_id"),
                "vehicle_types": _restriction_for(t["vehicle_type"]) if kind == "high_water" else []}
        road_event = inc.add_field_road_event(store, body, origin=actor, tier="FIELD_TEAM")
    rid = f"{trip_id}:R{store.next_seq('trip_report')}"
    rec = {"report_id": rid, "trip_id": trip_id, "kind": kind, "seg_id": seg_id, "note": note[:300],
           "t_min": float(now_t), "actor": actor, "road_event_id": road_event["event_id"] if road_event else None,
           "client_ref": client_ref}
    store.put("trip_reports", rid, rec, actor=actor, action=f"report:{kind}")
    return {"report": rec, "road_event": road_event}


def reports_for(store: OpsStore, trip_id: str) -> list[dict]:
    return store.list("trip_reports", lambda r: r["trip_id"] == trip_id)
