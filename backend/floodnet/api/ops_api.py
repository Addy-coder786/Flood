"""Live-operations API (DECISIONS D-38 to D-43): incident clock, published forecast, road-event sync, rescue trips,
driver endpoints, resource registry. Design: docs/LIVE_OPERATIONS.md.

Roles (floodnet/ops/auth.py): dispatcher endpoints use require_dispatcher; driver endpoints need the trip's own token
(`Authorization: Bearer <token>`), which opens that trip only; responder endpoints need a registered responder token.
Errors: store unavailable 503, bad input 422, unknown id 404, wrong role 401/403.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
import math

from pydantic import BaseModel, Field

from ..ops import auth, clock, impact, incident as inc, registry as rg, trips as tr
from ..ops.navigation import gps_freshness, maneuvers, parse_time
from ..ops.store import OpsUnavailable, get_store, utc_now

router = APIRouter()

DISCLAIMER = ("Decision support only. A route is a candidate with lower modelled flood exposure at the expected arrival "
              "time, never a guarantee of safety. Follow what you see on the ground and official instructions.")


# ------------------------------------------------------------------ plumbing
def _store():
    try:
        s = get_store()
    except OpsUnavailable as e:
        raise HTTPException(503, str(e))
    rg.seed_from_fixtures(s)
    return s


def _ctx():
    """(roads, G, seg_ctx, known segment ids) of the loaded pilot. Tests replace this function."""
    from . import state
    from .main import _access_context
    from ..routing.router import _cached_graph
    p = state.get_pilot()
    roads = p.get("roads")
    if roads is None:
        raise HTTPException(503, "roads not present in pilot bundle")
    return roads, _cached_graph(roads), _access_context(p), {s.seg_id for s in roads.segments}


def _errors(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))
    except ValueError as e:
        raise HTTPException(422, str(e))


def _destination_check(store):
    def check(trip: dict) -> tuple[bool, str]:
        sh = store.get("resources", trip.get("shelter_id") or "")
        if not sh or trip["stage"] in ("AT_SHELTER",):
            return True, ""
        if sh.get("status", "open") != "open":
            return False, f"shelter {sh['id']} reported {sh['status']} ({sh.get('source', '')})"
        if sh.get("verified") and sh.get("occupancy", 0) >= sh.get("total_capacity", 1):
            return False, f"shelter {sh['id']} reported full ({sh['occupancy']}/{sh['total_capacity']})"
        return True, ""
    return check


def _reassess(store, trigger: dict, only: Optional[str] = None) -> list[dict]:
    roads, G, seg_ctx, _ = _ctx()
    return impact.reassess_all(store, trigger=trigger, now_t=clock.now_t_min(store), now_wall=utc_now(), roads=roads,
                               G=G, seg_ctx=seg_ctx, destination_check=_destination_check(store), only=only)


def _remaining_view(trip: dict, roads) -> list[dict]:
    """Remaining legs from the last assessed position, each with turn instructions from our own geometry."""
    la = trip.get("last_assessment") or {}
    pos = la.get("position") or {}
    i = int(pos.get("leg_index", 0) or 0)
    out = []
    for j in range(i, len(trip["legs"])):
        leg = trip["legs"][j]
        segs = leg["segments"]
        start = int(leg["from_node"])
        if j == i and pos.get("node") is not None:
            nodes = [int(leg["from_node"])] + [int(s["to_node"]) for s in segs]
            if int(pos["node"]) in nodes:
                k = nodes.index(int(pos["node"]))
                segs, start = segs[k:], int(pos["node"])
        out.append({"kind": leg["kind"], "to": leg.get("to"), "arrive_t_min": leg.get("arrive_t_min"),
                    "dwell_min": leg.get("dwell_min"), "route": leg.get("route"),
                    "segments": [{k: s.get(k) for k in ("seg_id", "name", "status", "depth_cm", "enter_t_min")} for s in segs],
                    "maneuvers": maneuvers(roads, start, segs, destination=leg.get("to") or "destination")})
    return out


def _position_view(trip: dict) -> Optional[dict]:
    p = trip.get("last_position")
    if not p:
        return None
    age = (utc_now() - parse_time(p["recorded_at"])).total_seconds()
    return {**{k: p[k] for k in ("lon", "lat", "accuracy_m", "recorded_at", "received_at", "source")},
            "age_s": round(age, 1), "freshness": gps_freshness(age)}


def _dispatcher_view(trip: dict, roads=None) -> dict:
    v = {k: val for k, val in trip.items() if k not in ("driver_token_hash",) and not k.startswith("_")}
    v["ledger_status"] = tr.ledger_status_for_trip(trip)
    v["kind"] = trip.get("kind", "mission")
    v["position"] = _position_view(trip)
    v["version"] = trip.get("_version")
    if roads is not None:
        v["remaining"] = _remaining_view(trip, roads)
    return v


def _driver_view(trip: dict, roads) -> dict:
    accepted = next((p for p in trip["proposals"] if p["proposal_id"] == trip.get("awaiting_ack")), None)
    la = trip.get("last_assessment") or {}
    names = trip.get("names") or {}
    stages = tr.stages_for(trip)
    return {"trip_id": trip["trip_id"], "kind": trip.get("kind", "mission"), "stage": trip["stage"],
            "route_version": trip.get("route_version", 1),
            "vehicle_type": trip["vehicle_type"], "people": trip.get("people"),
            "origin": names.get("origin"), "destination": names.get("destination"), "eta": trip.get("eta"),
            "team": names.get("team") or trip.get("team_id"), "vehicle": names.get("vehicle") or trip.get("vehicle_id"),
            "settlement": names.get("settlement") or trip.get("settlement_id"),
            "shelter": names.get("shelter") or trip.get("shelter_id"),
            "remaining": _remaining_view(trip, roads), "hazards_ahead": la.get("hazards_ahead", []),
            "assessed_t_min": la.get("assessed_t_min"), "position": _position_view(trip),
            "route_change": ({"proposal_id": accepted["proposal_id"], "reason": accepted["reason"],
                              "kind": accepted["kind"]} if accepted else None),
            "escalation": (trip.get("escalation") or {}).get("reason") if (trip.get("escalation") or {}).get("open") else None,
            "next_stages": [s for s in stages if s not in ("ABORTED",) and stages.index(s) > stages.index(trip["stage"])]
                           if trip["stage"] not in tr.TERMINAL else [],
            "location_sharing": {"interval_s": 15, "fresh_s": 30, "note": "Location is shared only while this page is open "
                                 "and on screen. Browsers stop GPS when the page is hidden or the phone is locked."},
            "disclaimer": DISCLAIMER}


def _driver(request: Request):
    store = _store()
    trip = tr.trip_for_token(store, auth.presented_token(request))
    if trip is None:
        raise HTTPException(401, "driver link not valid (it may have been replaced by the dispatcher)")
    return store, trip


# ------------------------------------------------------------------ models
class ClockReq(BaseModel):
    mode: Literal["REPLAY", "LIVE"] = "REPLAY"
    t_min: float = Field(ge=0)


class ForecastReq(BaseModel):
    run_id: Optional[str] = None
    data_mode: str = "UNKNOWN"
    series_t_min: list[float]
    streets_cm: dict[str, list[float]]


class EventsReq(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)


class TripReq(BaseModel):
    assignment: dict[str, Any]
    names: dict[str, str] = Field(default_factory=dict)
    incident_id: Optional[str] = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class PreviewReq(BaseModel):
    origin: list[float] = Field(min_length=2, max_length=2)
    dest: list[float] = Field(min_length=2, max_length=2)
    vehicle_type: Optional[str] = None
    vehicle_id: Optional[str] = Field(default=None, max_length=40)
    names: dict[str, str] = Field(default_factory=dict)
    refresh_traffic: bool = False                 # bypass the 60 s live-traffic cache (the panel's Refresh button)



class DirectTripReq(PreviewReq):
    client_ref: Optional[str] = Field(default=None, max_length=80)
    incident_id: Optional[str] = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class StageReq(BaseModel):
    stage: str
    reason: str = ""


class PositionReq(BaseModel):
    lon: float
    lat: float
    accuracy_m: Optional[float] = None
    recorded_at: Optional[Any] = None
    source: Optional[str] = None


class ReportReq(BaseModel):
    kind: str
    seg_id: Optional[str] = None
    note: str = Field(default="", max_length=300)
    client_ref: Optional[str] = Field(default=None, max_length=80)


class DecideReq(BaseModel):
    decision: Literal["ACCEPT", "REJECT"]
    reason: str = Field(default="", max_length=300)


class AckReq(BaseModel):
    proposal_id: str


class ResponderReq(BaseModel):
    name: str = Field(max_length=80)
    role: str
    scope: list[str]


class UpdateReq(BaseModel):
    fields: dict[str, Any]
    observed_at: Optional[str] = None
    client_ref: Optional[str] = Field(default=None, max_length=80)


# ------------------------------------------------------------------ dispatcher: incident
@router.get("/api/ops/status")
def ops_status(_who: str = Depends(auth.require_dispatcher)):
    s = _store()
    fc = inc.get_forecast(s)
    return {"available": True, "clock": {k: v for k, v in clock.get_clock(s).items() if not k.startswith("_")},
            "now_t_min": clock.now_t_min(s), "forecast": fc[1] if fc else None,
            "active_trips": len(tr.active_trips(s)), "road_events": len(inc.list_road_events(s)),
            "note": "shared incident store on this server (DECISIONS D-38)"}


@router.post("/api/ops/clock")
def ops_clock(req: ClockReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    c = _errors(clock.set_clock, s, req.mode, req.t_min)
    return {"clock": {k: v for k, v in c.items() if not k.startswith("_")}, "now_t_min": clock.now_t_min(s)}


@router.post("/api/ops/forecast")
async def ops_forecast(req: ForecastReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    d = _errors(inc.publish_forecast, s, run_id=req.run_id, data_mode=req.data_mode, series_t_min=req.series_t_min,
                streets_cm=req.streets_cm)
    res = await run_in_threadpool(_reassess, s, {"kind": "forecast", "forecast_version": d["forecast_version"]})
    return {"forecast_version": d["forecast_version"], "reassessed": res}


@router.post("/api/ops/road-events/sync")
async def ops_sync_events(req: EventsReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    _, _, _, known = _ctx()
    bad = sorted({str(e.get("seg_id")) for e in req.events} - known)
    if bad:
        raise HTTPException(422, f"events refer to unknown road segments: {bad[:5]}")
    out = _errors(inc.sync_dispatcher_events, s, req.events)
    res = await run_in_threadpool(_reassess, s, {"kind": "road_events", "ids": out["added"]}) if out["added"] else []
    return {**out, "reassessed": res}


@router.get("/api/ops/road-events")
def ops_road_events(_who: str = Depends(auth.require_dispatcher)):
    return {"events": [{k: v for k, v in e.items() if not k.startswith("_")} for e in inc.list_road_events(_store())]}


@router.get("/api/ops/audit")
def ops_audit(collection: Optional[str] = None, id: Optional[str] = None, _who: str = Depends(auth.require_dispatcher)):
    rows = _store().audit(collection, id, limit=500)
    for r in rows:                                # never hand out token hashes (QA finding)
        r["body"] = {k: v for k, v in r["body"].items() if not k.endswith("token_hash")}
    return {"audit": rows}


# ------------------------------------------------------------------ dispatcher: trips
@router.post("/api/ops/trips")
async def ops_create_trip(req: TripReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    vid = req.assignment.get("vehicle_id")
    veh = s.get("resources", vid or "")
    vtype = (veh or {}).get("type") or req.assignment.get("vehicle_type")
    if not vtype:
        raise HTTPException(422, f"unknown vehicle {vid}")
    trip, token = _errors(tr.create_trip, s, req.assignment, vehicle_type=vtype, now_t=clock.now_t_min(s), names=req.names,
                          incident_id=req.incident_id)
    await run_in_threadpool(_reassess, s, {"kind": "trip_created"}, trip["trip_id"])
    roads = _ctx()[0]
    return {"trip": _dispatcher_view(s.get("trips", trip["trip_id"]), roads), "created": token is not None,
            "driver_link": f"driver#t={token}" if token else None,
            "note": "the driver link is shown once; use 'new driver link' to replace it"}


MAX_SNAP_M = 300.0          # ASSUMED: a point farther than this from any pilot road is outside the routable area


def _vehicle_info(s, req: PreviewReq) -> dict:
    """The vehicle a direct trip would use: a registry record (type, status, source, freshness) or, without one, a
    vehicle type the dispatcher chose."""
    from .. import config
    if req.vehicle_id:
        rec = s.get("resources", req.vehicle_id)
        if not rec or rec.get("kind") != "vehicle":
            raise HTTPException(404, f"unknown vehicle {req.vehicle_id}")
        view = rg.get_resource(s, req.vehicle_id)
        busy = [t for t in tr.active_trips(s) if t.get("vehicle_id") == req.vehicle_id]
        return {"id": rec["id"], "name": rec.get("name"), "type": rec["type"],
                "limit_cm": config.VEHICLE_LIMIT_CM.get(rec["type"]), "status": rec.get("status", "UNKNOWN"),
                "out_of_service_reason": rec.get("out_of_service_reason"), "source": rec.get("source", ""),
                "stale": view.get("stale", False), "age_min": view.get("age_min"),
                "freshness": view.get("freshness"), "on_trip": busy[0]["trip_id"] if busy else None}
    if req.vehicle_type not in config.VEHICLE_LIMIT_CM:
        raise HTTPException(422, f"choose a registered vehicle or a vehicle type ({sorted(config.VEHICLE_LIMIT_CM)})")
    return {"id": None, "name": None, "type": req.vehicle_type, "limit_cm": config.VEHICLE_LIMIT_CM[req.vehicle_type],
            "status": None, "source": "vehicle type chosen by the dispatcher (no registry record)", "stale": False,
            "age_min": None, "on_trip": None}


def _vehicle_label(veh: dict) -> str:
    """QA 2026-10-11 M7: never a reassuring label for a record nobody has confirmed."""
    if not veh["id"]:
        return "NOT TRACKED"
    if veh["stale"] or veh.get("freshness") == "STALE":
        return "STALE"
    if "SIMULATED" in (veh.get("source") or ""):
        return "SIMULATED"
    if veh.get("freshness") in (None, "UNKNOWN"):
        return "UNKNOWN"
    return "REGISTRY"


def _check_points(req: PreviewReq) -> None:
    """QA 2026-10-11 L1: NaN/inf coordinates were a 500. Checked here, not in the model: FastAPI's 422 body echoes the
    input, and JSON cannot carry inf/NaN."""
    for label, p in (("origin", req.origin), ("destination", req.dest)):
        if not all(math.isfinite(x) for x in p) or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            raise HTTPException(422, f"the {label} must be finite [lon, lat] coordinates")


TRAFFIC_MAX_SAMPLES = 40          # TomTom calls per preview: one per sampled road, evenly spread along the route


def _traffic_for_route(segments: list[dict], roads, refresh: bool) -> dict:
    """Live traffic for the roads of a route (floodnet/traffic/tomtom.py). Never raises: any failure is an honest
    UNAVAILABLE block. Only sampled roads carry a level; the rest are listed in `unsampled`."""
    from ..traffic import tomtom
    from .traffic_api import midpoint
    seg_by_id = {sg.seg_id: sg for sg in roads.segments}
    ids = [sg["seg_id"] for sg in segments]
    if len(ids) > TRAFFIC_MAX_SAMPLES:          # evenly spread samples; unsampled roads add no delay
        step = len(ids) / TRAFFIC_MAX_SAMPLES
        ids = [ids[int(i * step)] for i in range(TRAFFIC_MAX_SAMPLES)]
    points = [(sid, *midpoint(seg_by_id[sid].lonlat)) for sid in ids if sid in seg_by_id]
    try:
        out = tomtom.sample(points, refresh=refresh) if points else {"mode": "UNAVAILABLE", "segments": {}, "note": "no roads to sample"}
    except Exception as ex:  # noqa: BLE001 -- the provider must never break a route preview
        out = {"mode": "UNAVAILABLE", "segments": {}, "note": f"Live traffic unavailable — {type(ex).__name__}; using estimated travel time"}
    segs = {k: {key: v.get(key) for key in ("level", "current_kmh", "free_flow_kmh", "ratio", "confidence", "road_closure", "from_cache")}
            for k, v in (out.get("segments") or {}).items()}
    live = out.get("mode") == "LIVE" and bool(segs)
    note = out.get("note") or ""
    if not live and "using estimated travel time" not in note:
        note = (note + "; " if note else "") + "using estimated travel time"
    return {"mode": "LIVE" if live else "UNAVAILABLE", "provider": out.get("provider", "tomtom"),
            "fetched_at": out.get("fetched_at") if live else None, "ttl_s": out.get("ttl_s", 60), "note": note,
            "failures": out.get("failures", 0), "sampled": len(segs), "total": len(segments),
            "unsampled": [sg["seg_id"] for sg in segments if sg["seg_id"] not in segs], "segments": segs,
            "raw_segments": out.get("segments") or {}}


def _preview(s, req: PreviewReq) -> dict:
    """Route, ETA breakdown, hazards and freshness for a direct trip, all computed here (never from client geometry)."""
    _check_points(req)
    from ..access.arrival_router import route_one
    from ..ops import eta as eta_mod
    from ..ops.navigation import distance_m
    from ..response import planner
    fc = inc.get_forecast(s)
    if fc is None:
        raise HTTPException(409, "no forecast published to the operations store yet: run a forecast first")
    forecast, meta = fc
    roads, G, seg_ctx, _ = _ctx()
    veh = _vehicle_info(s, req)
    now_t, now_wall = clock.now_t_min(s), utc_now()
    book = inc.closures(s, now_t)
    rt = planner.Router(roads, G, forecast, seg_ctx, book)
    for label, p in (("origin", req.origin), ("destination", req.dest)):
        n = rt.node(p)
        if distance_m([float(p[0]), float(p[1])], [float(x) for x in roads.node_lonlat[n]]) > MAX_SNAP_M:
            raise HTTPException(422, f"the {label} is outside the pilot road network (more than {MAX_SNAP_M:g} m "
                                     "from a mapped road)")
    o, d = rt.node(req.origin), rt.node(req.dest)
    if o == d:
        raise HTTPException(422, "origin and destination snap to the same road point; choose points further apart")
    r = rt.leg(o, d, veh["type"], now_t)
    in_force = [e for e in book.used if e.kind == "closure"]
    freshness = {
        "forecast": {**eta_mod.forecast_input_label(meta, now_wall), "run_id": meta.get("run_id"),
                     "forecast_version": meta.get("forecast_version"), "published_at": meta.get("_updated_at")},
        "closures": {"label": "OPERATIONS LOG", "events_known": len(book.used), "closure_reports": len(in_force),
                     "latest_received_t_min": max((e.received_t_min for e in book.used), default=None),
                     "detail": "closures and reopenings received by the decision time"},
        "traffic": {"label": "UNAVAILABLE", "detail": "no route to sample"},
        "vehicle": {"label": _vehicle_label(veh), "age_min": veh["age_min"], "detail": veh["source"]},
    }
    out = {"dispatch_t_min": now_t, "clock": {k: v for k, v in clock.get_clock(s).items() if not k.startswith("_")},
           "vehicle": veh, "freshness": freshness, "alternatives": [], "warnings": [], "disclaimer": DISCLAIMER}
    if veh["id"] and veh["status"] != "AVAILABLE":
        out["warnings"].append(f"{veh['id']} is {veh['status']}"
                               + (f" ({veh['out_of_service_reason']})" if veh.get("out_of_service_reason") else ""))
    if veh["on_trip"]:
        out["warnings"].append(f"{veh['id']} is already on trip {veh['on_trip']}")
    if veh["stale"]:
        out["warnings"].append(f"{veh['id']} status not confirmed for {veh['age_min']:.0f} min (stale)")
    if not r["reachable"]:
        why = r.get("unreachable_reason") or "no route"
        return {**out, "route": {"reachable": False, "reason": why}, "eta": None, "decision": "ESCALATE",
                "traffic": {"mode": "UNAVAILABLE", "note": "no route to sample; using estimated travel time", "segments": {}},
                "decision_text": f"No feasible route for a {veh['type']} at T+{now_t:g} min: {why}. A person must "
                                 "decide (another vehicle type, wait, or a different destination)."}
    hazards = [{k: sg.get(k) for k in ("seg_id", "name", "status", "depth_cm", "enter_t_min", "reason")}
               for sg in r["segments"] if sg["status"] != "USABLE"]
    # live traffic (D-54): sampled only for the roads of THIS route, after the flood rules chose it; it changes the
    # ETA, never the route
    traffic = _traffic_for_route(r["segments"], roads, req.refresh_traffic)
    raw = traffic.pop("raw_segments")
    freshness["traffic"] = ({"label": "LIVE", "detail": f"TomTom, {traffic['sampled']} of {traffic['total']} roads sampled",
                             "fetched_at": traffic["fetched_at"], "ttl_s": traffic["ttl_s"]} if traffic["mode"] == "LIVE"
                            else {"label": "UNAVAILABLE", "detail": traffic["note"]})
    b = eta_mod.breakdown(roads, G, r["segments"], vehicle=veh["type"], dispatch_t_min=now_t, forecast=forecast,
                          seg_ctx=seg_ctx, closures=book, traffic=raw if traffic["mode"] == "LIVE" else {})
    late_block = [c for c in b["status_changes_when_delayed"] if c["status_delayed"] in ("AT_RISK", "CLOSED")]
    route = {"reachable": True, "route": r["route"], "length_m": r["length_m"], "travel_time_min": r["travel_time_min"],
             "arrival_t_min": r["arrival_t_min"], "status_counts": r["status_counts_at_arrival"], "hazards": hazards,
             "segments": [{k: sg.get(k) for k in ("seg_id", "name", "status", "depth_cm", "enter_t_min", "exit_t_min")}
                          for sg in r["segments"]]}
    cur = route_one(roads, G, o, d, vehicle=veh["type"], dispatch_t_min=now_t, forecast=forecast, seg_ctx=seg_ctx,
                    closures=book, mode="current")
    if cur["reachable"] and [x["seg_id"] for x in cur["segments"]] != [x["seg_id"] for x in r["segments"]]:
        out["alternatives"].append({
            "label": "Planned on conditions at dispatch only", "route": cur["route"], "length_m": cur["length_m"],
            "travel_time_min": cur["travel_time_min"], "valid_at_arrival": cur["valid_at_arrival"],
            "blocked_at_arrival": len(cur["blocked_at_arrival"]),
            "why_not": (f"meets {len(cur['blocked_at_arrival'])} road(s) closed or at the vehicle limit by the time "
                        "it gets there" if not cur["valid_at_arrival"] else
                        f"{cur['travel_time_min'] - r['travel_time_min']:+.1f} min; more uncertain or unknown roads")})
    caution = late_block or any(h["status"] in ("UNCERTAIN", "UNKNOWN") for h in hazards)
    text = ("Candidate route with lower modelled flood exposure at the expected arrival time."
            if not caution else
            "Candidate route, with cautions: " + "; ".join(
                [f"{c['name'] or c['seg_id']} becomes {c['status_delayed']} if water slows the vehicle" for c in late_block[:2]]
                + [f"{h['name'] or h['seg_id']} is {h['status']}" for h in hazards if h["status"] in ("UNCERTAIN", "UNKNOWN")][:2]))
    return {**out, "route": route, "eta": b, "traffic": traffic, "decision": "CAUTION" if caution else "OK",
            "decision_text": text, "_leg_parts": (rt, o, d)}


@router.post("/api/ops/trips/preview")
async def ops_trip_preview(req: PreviewReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    p = await run_in_threadpool(_preview, s, req)
    p.pop("_leg_parts", None)
    return p


@router.post("/api/ops/trips/direct")
async def ops_start_direct_trip(req: DirectTripReq, _who: str = Depends(auth.require_dispatcher)):
    """Start Trip: the route is computed again here at the decision time; nothing the browser drew is trusted."""
    s = _store()
    _check_points(req)
    if req.client_ref:
        same = [t for t in s.list("trips", lambda t: t.get("client_ref") == req.client_ref)
                if tr.same_start(t, req.incident_id, req.origin, req.dest)]
        if same:                                   # a repeated click: the same trip, no second driver link
            return {"trip": _dispatcher_view(same[0], _ctx()[0]), "created": False, "driver_link": None}
    p = await run_in_threadpool(_preview, s, req)
    veh = p["vehicle"]
    if p["decision"] == "ESCALATE":
        raise HTTPException(409, p["decision_text"])
    if veh["id"] and veh["status"] != "AVAILABLE":
        raise HTTPException(409, f"{veh['id']} is {veh['status']}"
                                 + (f" ({veh['out_of_service_reason']})" if veh.get("out_of_service_reason") else "")
                                 + "; choose an available vehicle")
    rt, o, d = p.pop("_leg_parts")
    names = {k: str(v)[:80] for k, v in req.names.items() if k in ("origin", "destination", "vehicle", "team")}
    if veh.get("name"):                            # QA L8: the record's name, never one the browser sent
        names["vehicle"] = f"{veh['id']} {veh['name']}"
    leg = _errors(tr.direct_leg, rt, req.origin, req.dest, vehicle_type=veh["type"], depart_t_min=p["dispatch_t_min"],
                  to_name=names.get("destination") or "destination")
    eta_view = {k: p["eta"][k] for k in ("free_flow_min", "flood_delay_min", "eta_min", "arrival_t_min", "components",
                                         "status_changes_when_delayed", "source", "note", "traffic_delay_min",
                                         "eta_without_traffic_min")}
    eta_view["forecast_input"] = p["freshness"]["forecast"]
    trip, token = _errors(tr.create_direct_trip, s, leg, vehicle_type=veh["type"], now_t=p["dispatch_t_min"], names=names,
                          incident_id=req.incident_id, vehicle_id=veh["id"], client_ref=req.client_ref, eta=eta_view,
                          origin=req.origin, dest=req.dest)
    await run_in_threadpool(_reassess, s, {"kind": "trip_created"}, trip["trip_id"])
    return {"trip": _dispatcher_view(s.get("trips", trip["trip_id"]), _ctx()[0]), "created": token is not None,
            "driver_link": f"driver#t={token}" if token else None, "preview": {k: v for k, v in p.items()},
            "note": "the driver link is shown once; use 'new driver link' to replace it"}


@router.get("/api/ops/trips")
def ops_trips(_who: str = Depends(auth.require_dispatcher)):
    s = _store()
    roads = _ctx()[0]                             # active trips carry their remaining route (map: next road, D-49)
    return {"trips": [_dispatcher_view(t, roads if t["stage"] not in tr.TERMINAL else None) for t in s.list("trips")],
            "now_t_min": clock.now_t_min(s)}


@router.get("/api/ops/trips/{trip_id}")
def ops_trip(trip_id: str, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    t = _errors(tr.get_trip, s, trip_id)
    return {"trip": _dispatcher_view(t, _ctx()[0]), "reports": tr.reports_for(s, trip_id),
            "audit": [{k: a[k] for k in ("seq", "at", "actor", "action", "version")} for a in s.audit("trips", trip_id)]}


@router.post("/api/ops/trips/{trip_id}/stage")
def ops_stage(trip_id: str, req: StageReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    t = _errors(tr.set_stage, s, trip_id, req.stage, actor="dispatcher", now_t=clock.now_t_min(s), reason=req.reason)
    return {"trip": _dispatcher_view(t)}


@router.post("/api/ops/trips/{trip_id}/driver-link")
def ops_driver_link(trip_id: str, _who: str = Depends(auth.require_dispatcher)):
    token = _errors(tr.rotate_driver_token, _store(), trip_id)
    return {"driver_link": f"driver#t={token}", "note": "the previous driver link no longer works"}


@router.post("/api/ops/trips/{trip_id}/simulate-position")
async def ops_simulate_position(trip_id: str, _who: str = Depends(auth.require_dispatcher)):
    """Demonstration only: record a SIMULATED fix where the plan says the vehicle is now, then reassess."""
    from ..response.planner import _position_at
    s = _store()
    t = _errors(tr.get_trip, s, trip_id)
    roads = _ctx()[0]
    _, node, _ = _position_at(t["legs"], clock.now_t_min(s))
    lon, lat = (float(x) for x in roads.node_lonlat[int(node)])
    p = _errors(tr.record_position, s, trip_id, {"lon": lon, "lat": lat, "accuracy_m": 5, "source": "SIMULATED"},
                actor="dispatcher", now_wall=utc_now())
    res = await run_in_threadpool(_reassess, s, {"kind": "gps", "source": "SIMULATED"}, trip_id)
    return {"position": p, "reassessed": res}


@router.post("/api/ops/trips/{trip_id}/reassess")
async def ops_reassess(trip_id: str, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    _errors(tr.get_trip, s, trip_id)
    return {"reassessed": await run_in_threadpool(_reassess, s, {"kind": "manual"}, trip_id)}


@router.post("/api/ops/trips/{trip_id}/proposals/{proposal_id}/decide")
async def ops_decide(trip_id: str, proposal_id: str, req: DecideReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    _errors(impact.decide, s, trip_id, proposal_id, req.decision, actor="dispatcher", now_t=clock.now_t_min(s),
            reason=req.reason)
    if req.decision == "ACCEPT":                  # the accepted route is checked again now (it may have closed since)
        await run_in_threadpool(_reassess, s, {"kind": "route_accepted", "proposal_id": proposal_id}, trip_id)
    return {"trip": _dispatcher_view(s.get("trips", trip_id), _ctx()[0])}


@router.post("/api/ops/trips/{trip_id}/report")
async def ops_dispatcher_report(trip_id: str, req: ReportReq, _who: str = Depends(auth.require_dispatcher)):
    s = _store()
    out = _errors(tr.report, s, trip_id, req.kind, actor="dispatcher", now_t=clock.now_t_min(s), known_segments=_ctx()[3],
                  seg_id=req.seg_id, note=req.note, client_ref=req.client_ref)
    res = await run_in_threadpool(_reassess, s, {"kind": "road_event", "id": out["road_event"]["event_id"]}) if out["road_event"] else []
    return {**out, "reassessed": res}


# ------------------------------------------------------------------ driver (trip token)
@router.get("/api/ops/driver/trip")
def driver_trip(request: Request):
    s, t = _driver(request)
    return _driver_view(t, _ctx()[0])


@router.post("/api/ops/driver/position")
async def driver_position(req: PositionReq, request: Request):
    s, t = _driver(request)
    actor = f"driver:{t['trip_id']}"
    _errors(tr.record_position, s, t["trip_id"], req.model_dump(), actor=actor, now_wall=utc_now())
    await run_in_threadpool(_reassess, s, {"kind": "gps", "trip_id": t["trip_id"]}, t["trip_id"])
    return _driver_view(s.get("trips", t["trip_id"]), _ctx()[0])


@router.post("/api/ops/driver/stage")
def driver_stage(req: StageReq, request: Request):
    s, t = _driver(request)
    if req.stage == "ABORTED":
        raise HTTPException(403, "only the dispatcher can abort a trip; report the problem instead")
    t = _errors(tr.set_stage, s, t["trip_id"], req.stage, actor=f"driver:{t['trip_id']}", now_t=clock.now_t_min(s))
    return _driver_view(t, _ctx()[0])


@router.post("/api/ops/driver/report")
async def driver_report(req: ReportReq, request: Request):
    s, t = _driver(request)
    out = _errors(tr.report, s, t["trip_id"], req.kind, actor=f"driver:{t['trip_id']}", now_t=clock.now_t_min(s),
                  known_segments=_ctx()[3], seg_id=req.seg_id, note=req.note, client_ref=req.client_ref)
    if out["road_event"]:
        await run_in_threadpool(_reassess, s, {"kind": "road_event", "id": out["road_event"]["event_id"],
                                               "origin": f"driver:{t['trip_id']}"})
    return {"report": out["report"], "road_event_id": out["road_event"]["event_id"] if out["road_event"] else None,
            "trip": _driver_view(s.get("trips", t["trip_id"]), _ctx()[0])}


@router.post("/api/ops/driver/ack")
def driver_ack(req: AckReq, request: Request):
    s, t = _driver(request)
    t = _errors(impact.acknowledge, s, t["trip_id"], req.proposal_id, actor=f"driver:{t['trip_id']}")
    return _driver_view(t, _ctx()[0])


# ------------------------------------------------------------------ resource registry
@router.get("/api/ops/resources")
def ops_resources(_who: str = Depends(auth.require_dispatcher)):
    s = _store()
    return {"resources": rg.list_resources(s), "stale_after_min": rg.STALE_AFTER_MIN,
            "note": "seed values are SIMULATED demonstration data until a verified update replaces them"}


@router.post("/api/ops/responders")
def ops_register_responder(req: ResponderReq, _who: str = Depends(auth.require_dispatcher)):
    r, token = _errors(rg.register_responder, _store(), name=req.name, role=req.role, scope=req.scope)
    return {"responder": {k: v for k, v in r.items() if k != "token_hash" and not k.startswith("_")}, "token": token,
            "note": "the token is shown once"}


@router.get("/api/ops/responders")
def ops_responders(_who: str = Depends(auth.require_dispatcher)):
    return {"responders": [{k: v for k, v in r.items() if k != "token_hash" and not k.startswith("_")}
                           for r in _store().list("responders")]}


@router.post("/api/ops/resources/{resource_id}/updates")
def ops_resource_update(resource_id: str, req: UpdateReq, request: Request):
    s = _store()
    host = request.client.host if request.client else None
    token = auth.presented_token(request)
    if auth.dispatcher_allowed(token, host, request.headers.get("host"), request.headers.get("origin")):
        actor, role = "dispatcher", "dispatcher"
    else:
        who = rg.responder_for_token(s, token)
        if who is None:
            raise HTTPException(401, "dispatcher or registered responder token required")
        actor, role = f"responder:{who['responder_id']}", who["role"]
    rec = _errors(rg.submit_update, s, actor=actor, role=role, resource_id=resource_id, fields=req.fields,
                  observed_at=req.observed_at, now_wall=utc_now(), client_ref=req.client_ref)
    return {"update": rec, "resource": rg.get_resource(s, resource_id)}


from . import registry_api as _registry_api  # noqa: E402  (last: registry_api imports this module lazily)
router.include_router(_registry_api.router)
