"""Route-impact reassessment of active rescue trips (DECISIONS D-42; docs/LIVE_OPERATIONS.md §5).

assess()  pure: where is the vehicle (fresh on-route GPS, fresh off-route GPS, or the labelled planned estimate), is
          the rest of its route still usable for THIS vehicle at the minutes it will be on each road (closures and
          forecast known now), and what does the arrival-time router offer instead.
apply()   the decision rules that keep this from flapping: a proposal is created once per route, a rejected route is
          not proposed again (the trip is flagged for a person instead), optional improvements have a cooldown,
          escalations are recorded once. Every change is written to the audit trail.
decide() / acknowledge()  a dispatcher accepts or rejects; the driver acknowledges the accepted route.

Roads the vehicle has already driven are never re-checked: a forecast change behind it changes nothing. A route is a
candidate with lower modelled exposure, never a guarantee of safety; with no feasible route the trip is escalated to a
person and no alternative is invented.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import networkx as nx

from ..access.arrival_router import _Ctx, travel_min
from ..access.events import ClosureBook
from ..access.road_status import BLOCKING, Forecast, SegmentContext
from ..contracts import RoadGraph
from ..response import planner
from ..routing.router import _cached_graph as _graph, _snap
from . import eta
from . import incident as inc
from .navigation import distance_m, gps_freshness, parse_time, snap_to_route
from .store import OpsStore
from .trips import TERMINAL, active_trips, get_trip

ON_ROUTE_M = 60.0                 # ASSUMED: within this (+ reported accuracy) of the route the vehicle is on it
NEAR_ROAD_M = 150.0               # ASSUMED: farther than this from any road node the fix is off the pilot network
OPTIONAL_MIN_GAIN_MIN = 3.0       # ASSUMED thresholds for proposing a non-required change
OPTIONAL_UNCERTAIN_DROP = 2
OPTIONAL_MAX_EXTRA_MIN = 2.0
OPTIONAL_COOLDOWN_MIN = 5.0
OPEN = "OPEN"

_SEG_KEYS = ("seg_id", "name", "enter_t_min", "exit_t_min", "to_node", "status", "depth_cm")


def _leg_nodes(leg: dict) -> list[int]:
    return [int(leg["from_node"])] + [int(s["to_node"]) for s in leg["segments"]]


# ------------------------------------------------------------------ position
def resolve_position(trip: dict, now_t: float, now_wall: Optional[datetime], roads: RoadGraph,
                     fix: Optional[dict], G: Optional[nx.DiGraph] = None) -> dict:
    G = G if G is not None else _graph(roads)
    legs = trip["legs"]
    if trip.get("kind") == "direct":
        i, node, t, lag = _direct_schedule(trip, now_t)
    else:
        i, node, t = planner._position_at(legs, now_t)
        i, node, t, lag = _clamp_to_stage(trip, i, int(node), float(t), now_t)
    planned = {"source": "PLANNED_ESTIMATE", "leg_index": i, "node": int(node), "t_min": max(float(t), now_t),
               "freshness": "NONE", "note": lag or "no GPS fix; position estimated from the planned schedule"}
    if not fix:
        return planned
    recorded = parse_time(fix.get("recorded_at"))
    age = (now_wall - recorded).total_seconds() if (now_wall and recorded) else None
    fresh = gps_freshness(age)
    base = {"fix": {k: fix.get(k) for k in ("lon", "lat", "accuracy_m", "recorded_at", "source")},
            "age_s": None if age is None else round(age, 1), "freshness": fresh}
    if fresh != "FRESH":
        return {**planned, **base, "note": f"GPS {fresh} ({age:.0f} s old); position estimated from the planned schedule"
                if age is not None else "GPS time unknown; position estimated from the planned schedule"}
    lon, lat = float(fix["lon"]), float(fix["lat"])
    if i >= len(legs):
        return {**planned, **base, "note": "the planned route is finished"}
    leg = legs[i]
    snap = snap_to_route(roads, leg["from_node"], leg["segments"], lon, lat)
    tol = ON_ROUTE_M + float(fix.get("accuracy_m") or 0.0)
    if snap["seg_index"] is not None and snap["distance_m"] <= tol:
        k = snap["seg_index"]
        s = leg["segments"][k]
        rest = (1.0 - snap["fraction"]) * (s["exit_t_min"] - s["enter_t_min"])
        t_end = round(now_t + max(rest, 0.0), 2)
        # the road the vehicle is on now: a reroute keeps it, so the same fix is not "off route" afterwards
        on_seg = {**{key: s.get(key) for key in _SEG_KEYS}, "enter_t_min": round(now_t, 2), "exit_t_min": t_end,
                  "from_node": int(leg["from_node"] if k == 0 else leg["segments"][k - 1]["to_node"])}
        return {**base, "source": "GPS_ON_ROUTE", "leg_index": i, "node": int(s["to_node"]),
                "t_min": t_end, "distance_to_route_m": snap["distance_m"], "on_segment": on_seg,
                "note": f"on the planned route ({snap['distance_m']:.0f} m from it)"}
    nearest = _snap(roads, [(lon, lat)])[0]
    d_road = _distance_to_roads_at(roads, G, nearest, lon, lat)
    if d_road > NEAR_ROAD_M:
        return {**planned, **base, "note": f"GPS fix is off the pilot road network (nearest road {d_road:.0f} m "
                                           "away); position estimated from the planned schedule"}
    return {**base, "source": "GPS_OFF_ROUTE", "leg_index": i, "node": int(nearest), "t_min": now_t,
            "distance_to_route_m": snap["distance_m"],
            "note": f"{snap['distance_m']:.0f} m from the planned route; rerouting from the nearest road"}


_NOT_MOVING = ("PLANNED", "ACCEPTED")


def _direct_schedule(trip: dict, now_t: float) -> tuple[int, int, float, Optional[str]]:
    """Where a direct trip's vehicle is assumed to be without a fresh GPS fix (QA 2026-10-11, H1). Before the driver
    reports EN_ROUTE it has not moved: the whole route is re-checked from the start. After, the planned schedule runs
    from the minute EN_ROUTE was reported, not from when the dispatcher pressed Start; if the schedule says it has
    arrived but the driver has not reported ARRIVED, the last road is still re-checked."""
    legs, stage = trip["legs"], trip.get("stage")
    if stage in _NOT_MOVING:
        return 0, int(legs[0]["from_node"]), now_t, ("not started: the driver has not reported en route; the whole "
                                                      "route is re-checked from the start")
    t_go = max((float(h["t_min"]) for h in trip.get("stage_history", []) if h["stage"] == "EN_ROUTE"),
               default=float(legs[0]["depart_t_min"]))
    shift = max(0.0, t_go - float(legs[0]["depart_t_min"]))
    i, node, t = planner._position_at(legs, now_t - shift)
    if i >= len(legs):
        last = legs[-1]
        segs = last["segments"]
        start = int(segs[-2]["to_node"]) if len(segs) > 1 else int(last["from_node"])
        return len(legs) - 1, start, now_t, ("behind schedule: arrival not reported by the driver; the last road is "
                                             "re-checked")
    return i, int(node), float(t) + shift, None


_BEFORE_PICKUP = ("ASSIGNED", "EN_ROUTE")
_BEFORE_LOADING = ("ASSIGNED", "EN_ROUTE", "AT_PICKUP")
_LOADED = ("LOADED", "AT_SHELTER")


def _clamp_to_stage(trip: dict, i: int, node: int, t: float, now_t: float) -> tuple[int, int, float, Optional[str]]:
    """The schedule may run ahead of (or behind) what the driver reported. People are only on board after LOADED, so
    before that the remaining route always includes the settlement leg; after it, never (QA finding H2). When the
    schedule is ahead, the vehicle is assumed at the START of the reported leg, so the whole leg is re-checked."""
    legs, stage = trip["legs"], trip.get("stage", "ASSIGNED")
    kinds = [l.get("kind") for l in legs]
    i_set = kinds.index("to_settlement") if "to_settlement" in kinds else 0
    i_sh = kinds.index("to_shelter") if "to_shelter" in kinds else len(legs) - 1
    cap = (kinds.index("pickup") if "pickup" in kinds and stage in _BEFORE_PICKUP else i_set) if stage in _BEFORE_LOADING else None
    if cap is not None and i > cap:
        return cap, int(legs[cap]["from_node"]), now_t, (f"behind schedule: the driver has not reported reaching "
                                                          f"{legs[cap].get('to', 'the next stop')}; the whole leg is re-checked")
    if stage == "AT_SETTLEMENT" and i != i_sh:
        return i_sh, int(legs[i_sh]["from_node"]), now_t, "at the settlement (driver report)"
    if stage in _LOADED and i < i_sh:
        return i_sh, int(legs[i_sh]["from_node"]), now_t, "people on board (driver report); shelter leg re-checked"
    return i, node, t, None


def _distance_to_roads_at(roads: RoadGraph, G: nx.DiGraph, node: int, lon: float, lat: float) -> float:
    """Distance from (lon, lat) to the roads that meet at `node` (the nearest routable node)."""
    segs = [{"seg_id": G.edges[node, v]["seg_id"], "to_node": v} for v in G.successors(node)]
    segs += [{"seg_id": G.edges[u, node]["seg_id"], "to_node": node} for u in G.predecessors(node)]
    if not segs:
        return distance_m([lon, lat], [float(x) for x in roads.node_lonlat[node]])
    return float(snap_to_route(roads, node, segs, lon, lat)["distance_m"])


# ------------------------------------------------------------------ the current route, re-checked
def _recheck(trip: dict, pos: dict, G: nx.DiGraph, cx: _Ctx) -> dict:
    legs, segs, t = trip["legs"], [], pos["t_min"]
    for j in range(pos["leg_index"], len(legs)):
        leg = legs[j]
        nodes = _leg_nodes(leg)
        if j == pos["leg_index"]:
            nodes = nodes[nodes.index(pos["node"]):] if pos["node"] in nodes else nodes
        for a, b in zip(nodes[:-1], nodes[1:]):
            if not G.has_edge(a, b):
                continue
            e = G.edges[a, b]
            seg = cx.seg[e["seg_id"]]
            dt = travel_min(e["length_m"], seg.highway, cx.vehicle)
            st = cx.status(e["seg_id"], t, t + dt, "arrival")
            segs.append({"leg_index": j, "seg_id": seg.seg_id, "name": seg.name, "enter_t_min": round(t, 2),
                         "exit_t_min": round(t + dt, 2), **st})
            t += dt
        t += float(leg.get("dwell_min", 0.0))
    return {"segments": segs, "eta_t_min": round(t, 2), "signature": [s["seg_id"] for s in segs]}


def _summary(segs: list[dict], eta: float) -> dict:
    return {"eta_t_min": round(eta, 2), "blocking": sum(s["status"] in BLOCKING for s in segs),
            "uncertain": sum(s["status"] in ("UNCERTAIN", "UNKNOWN") for s in segs),
            "closures": sum(s["status"] == "CLOSED" for s in segs), "roads": len(segs)}


def _with_flood_delay(segs: list[dict], eta_t: float, start_t: float, vehicle: str, roads: RoadGraph, G: nx.DiGraph,
                      forecast: Forecast, seg_ctx: SegmentContext, closures: ClosureBook) -> dict:
    """The MODELLED FLOOD DELAY on a remaining route (eta.py), shown beside the free-flow ETA. Decisions above still
    use the free-flow ETA and the road statuses; the delay is information for the dispatcher (D-49)."""
    if not segs:
        return {"flood_delay_min": 0.0, "eta_with_flood_delay_min": round(eta_t, 2)}
    b = eta.breakdown(roads, G, segs, vehicle=vehicle, dispatch_t_min=start_t, forecast=forecast, seg_ctx=seg_ctx,
                      closures=closures)
    d = b["flood_delay_min"]
    return {"flood_delay_min": d, "eta_with_flood_delay_min": None if d is None else round(eta_t + d, 2)}


def _candidate(trip: dict, pos: dict, rt: planner.Router) -> tuple[Optional[dict], Optional[str]]:
    legs, node, t, out = trip["legs"], pos["node"], pos["t_min"], []
    for j in range(pos["leg_index"], len(legs)):
        leg = legs[j]
        r = rt.leg(node, int(leg["to_node"]), trip["vehicle_type"], t)
        if not r["reachable"]:
            return None, f"no feasible route to {leg.get('to', 'the next stop')}: {r.get('unreachable_reason', 'unreachable')}"
        dwell = float(leg.get("dwell_min", 0.0))
        out.append({**leg, "from_node": int(node), "depart_t_min": round(t, 2), "arrive_t_min": round(r["arrival_t_min"], 2),
                    "done_t_min": round(r["arrival_t_min"] + dwell, 2), "travel_time_min": r["travel_time_min"],
                    "length_m": r.get("length_m", 0.0), "route": r["route"] if r["route"] is not None else leg.get("route"),
                    "status_counts": r["status_counts_at_arrival"],
                    "segments": [{k: s[k] for k in _SEG_KEYS} for s in r["segments"]]})
        node, t = int(leg["to_node"]), r["arrival_t_min"] + dwell
    segs = [s for leg in out for s in leg["segments"]]
    cand = {"legs": out, "from_leg_index": pos["leg_index"], "signature": [s["seg_id"] for s in segs],
            "eta_t_min": round(t, 2), "summary": _summary(segs, t), "prefix_segments": 0}
    on = pos.get("on_segment")
    if on and out:                                 # keep the road being driven at the start of the new route
        first = out[0]
        out[0] = {**first, "from_node": on["from_node"], "depart_t_min": on["enter_t_min"],
                  "segments": [{key: on.get(key) for key in _SEG_KEYS}] + first["segments"]}
        cand["prefix_segments"] = 1                # not part of the comparison (the current route excludes it too)
    return cand, None


def assess(trip: dict, *, now_t: float, now_wall: Optional[datetime], roads: RoadGraph, G: nx.DiGraph,
           forecast: Forecast, seg_ctx: SegmentContext, closures: ClosureBook, fix: Optional[dict] = None,
           destination_ok: tuple[bool, str] = (True, "")) -> dict:
    pos = resolve_position(trip, now_t, now_wall, roads, fix, G)
    out = {"trip_id": trip["trip_id"], "assessed_t_min": now_t, "route_version": trip.get("route_version", 1),
           "position": pos, "issues": [], "hazards_ahead": [], "candidate": None, "comparison": None}
    if trip.get("stage") == "ARRIVED":            # QA M2: the driver is at the destination; nothing to reroute
        return {**out, "decision": "NO_CHANGE", "reason": "arrived at the destination (driver report)"}
    if pos["leg_index"] >= len(trip["legs"]):
        return {**out, "decision": "NO_CHANGE", "reason": "no remaining route"}
    cx = _Ctx(roads, forecast, seg_ctx, closures, trip["vehicle_type"], now_t)
    on = pos.get("on_segment")
    if on and cx.status(on["seg_id"], now_t, max(on["exit_t_min"], now_t), "arrival")["status"] in BLOCKING:
        # QA M1: the road being driven closed (or reached the limit): re-check and reroute from its start
        pos = {**pos, "node": on["from_node"], "t_min": now_t, "on_segment": None,
               "note": pos["note"] + "; the road being driven is now closed or at risk, re-checked from its start"}
        out["position"] = pos
    cur = _recheck(trip, pos, G, cx)
    issues = [s for s in cur["segments"] if s["status"] in BLOCKING]
    out["issues"] = issues
    out["hazards_ahead"] = [{k: s.get(k) for k in ("seg_id", "name", "status", "depth_cm", "enter_t_min", "reason")}
                            for s in cur["segments"] if s["status"] != "USABLE"]
    if not destination_ok[0]:
        return {**out, "decision": "ESCALATE", "reason": f"destination unavailable: {destination_ok[1]}; operator decision"}
    rt = planner.Router(roads, G, forecast, seg_ctx, closures)
    cand, why_not = _candidate(trip, pos, rt)
    cur_sum = _summary(cur["segments"], cur["eta_t_min"])
    flood = lambda segs, eta_t: _with_flood_delay(segs, eta_t, pos["t_min"], trip["vehicle_type"], roads, G, forecast,
                                                  seg_ctx, closures)
    out["comparison"] = {"current": {**cur_sum, "signature": cur["signature"], **flood(cur["segments"], cur["eta_t_min"])},
                         "candidate": ({**cand["summary"], "signature": cand["signature"],
                                        **flood([s for leg in cand["legs"] for s in leg["segments"]][cand["prefix_segments"]:],
                                                cand["eta_t_min"])}
                                       if cand else None)}
    out["candidate"] = cand
    off_route = pos["source"] == "GPS_OFF_ROUTE"
    if issues or off_route:
        if cand is None:
            return {**out, "decision": "ESCALATE", "reason": f"{why_not}; operator decision needed"}
        what = "; ".join(f"{s['name'] or 'unnamed road ' + s['seg_id']}: {s['status']} ({s['reason']})" for s in issues[:3])
        reason = (f"the vehicle is {pos['distance_to_route_m']:.0f} m off the planned route" if off_route and not issues
                  else f"remaining route blocked for {trip['vehicle_type']}: {what}")
        return {**out, "decision": "REQUIRED", "reason": reason}
    if cand is None or cand["signature"] == cur["signature"]:
        return {**out, "decision": "NO_CHANGE", "reason": "the remaining route is still the best feasible one"}
    gain = cur_sum["eta_t_min"] - cand["summary"]["eta_t_min"]
    drop = cur_sum["uncertain"] - cand["summary"]["uncertain"]
    if gain >= OPTIONAL_MIN_GAIN_MIN or (drop >= OPTIONAL_UNCERTAIN_DROP and -gain <= OPTIONAL_MAX_EXTRA_MIN):
        return {**out, "decision": "OPTIONAL",
                "reason": f"alternative arrives {gain:+.1f} min earlier with {drop:+d} fewer uncertain roads"}
    return {**out, "decision": "NO_CHANGE", "reason": "alternatives are not materially better"}


# ------------------------------------------------------------------ rules that turn assessments into proposals
def _public_assessment(a: dict) -> dict:
    return {k: a.get(k) for k in ("assessed_t_min", "decision", "reason", "position", "hazards_ahead", "comparison",
                                  "route_version")}


def apply(store: OpsStore, trip_id: str, a: dict, *, trigger: dict, now_t: float) -> dict:
    with store.transaction():
        trip = get_trip(store, trip_id)
        if trip["stage"] in TERMINAL or trip.get("route_version", 1) != a.get("route_version", 1):
            return {"action": "stale_assessment", "proposal": None}          # QA finding M3
        trip = {**trip, "last_assessment": _public_assessment(a)}
        decision = a["decision"]
        esc = trip.get("escalation")
        if decision != "ESCALATE" and esc and esc.get("open"):
            trip["escalation"] = {**esc, "open": False, "closed_t_min": now_t,
                                  "closed_reason": f"a feasible route exists again at T+{now_t:g}"}
        if decision == "NO_CHANGE":
            store.put("trips", trip_id, trip, actor="system", action="assess")
            return {"action": "none", "proposal": None}
        if decision == "ESCALATE":
            esc = trip.get("escalation")
            if esc and esc.get("open") and esc.get("reason") == a["reason"]:
                store.put("trips", trip_id, trip, actor="system", action="assess")
                return {"action": "deduplicated", "proposal": None}
            trip["escalation"] = {"open": True, "reason": a["reason"], "t_min": now_t, "trigger": trigger}
            store.put("trips", trip_id, trip, actor="system", action="escalate")
            return {"action": "escalated", "proposal": None}
        sig = a["candidate"]["signature"]
        key = _finding_key(a)
        props = trip["proposals"]
        if any(p["status"] == OPEN and _same_finding(p, decision, key, sig) for p in props):
            store.put("trips", trip_id, trip, actor="system", action="assess")
            return {"action": "deduplicated", "proposal": None}
        if any(p["status"] == "REJECTED" and _same_finding(p, decision, key, sig) for p in props):
            trip["attention"] = (f"a {decision.lower()} reroute was rejected by the dispatcher and the finding remains "
                                 f"({a['reason']}); operator decision needed")
            store.put("trips", trip_id, trip, actor="system", action="attention")
            return {"action": "suppressed_rejected", "proposal": None}
        if decision == "OPTIONAL":
            last = [p["created_t_min"] for p in props if p["kind"] == "OPTIONAL"]
            if last and now_t - max(last) < OPTIONAL_COOLDOWN_MIN:
                store.put("trips", trip_id, trip, actor="system", action="assess")
                return {"action": "suppressed_cooldown", "proposal": None}
        for p in props:
            if p["status"] == OPEN:
                p["status"] = "SUPERSEDED"
        cur = (a.get("comparison") or {}).get("current") or {}
        proposal = {"proposal_id": f"P{len(props) + 1}", "kind": decision, "status": OPEN, "created_t_min": now_t,
                    "trigger": trigger, "reason": a["reason"], "issues": a.get("issues", []),
                    "comparison": a.get("comparison"), "position": a.get("position"), "signature": sig, "finding": key,
                    "from_leg_index": a["candidate"].get("from_leg_index", 0), "new_legs": a["candidate"]["legs"],
                    "old_route": {"route_version": trip.get("route_version", 1), "signature": cur.get("signature"),
                                  "eta_t_min": cur.get("eta_t_min")}}
        trip["proposals"] = props + [proposal]
        store.put("trips", trip_id, trip, actor="system", action="propose")
        return {"action": "created", "proposal": proposal}


def _finding_key(a: dict) -> list:
    if a["decision"] == "REQUIRED":
        if a.get("issues"):
            return sorted({i["seg_id"] for i in a["issues"]})
        return ["off-route"]
    return list(a["candidate"]["signature"])


def _same_finding(p: dict, kind: str, key: list, sig: list) -> bool:
    """Is a new finding the one proposal `p` was about? REQUIRED: no blocking road beyond those p already named (a
    rejected detour is not offered again as the vehicle moves on; a NEW blocked road is). OPTIONAL: the new route is
    what remains of p's route."""
    if p["kind"] != kind:
        return False
    if kind == "REQUIRED":
        return set(key) <= set(p.get("finding") or p["signature"])
    old = p["signature"]
    return list(sig) == old[len(old) - len(sig):] if len(sig) <= len(old) else False


def decide(store: OpsStore, trip_id: str, proposal_id: str, decision: str, *, actor: str, now_t: float,
           reason: str = "") -> dict:
    decision = decision.upper()
    if decision not in ("ACCEPT", "REJECT"):
        raise ValueError("decision must be ACCEPT or REJECT")
    with store.transaction():
        trip = get_trip(store, trip_id)
        if trip["stage"] in TERMINAL:
            raise ValueError(f"trip {trip_id} is finished")
        p = next((x for x in trip["proposals"] if x["proposal_id"] == proposal_id), None)
        if p is None:
            raise KeyError(f"unknown proposal {proposal_id}")
        if p["status"] != OPEN:
            newer = [x["proposal_id"] for x in trip["proposals"] if x["status"] == OPEN]
            if p["status"] == "SUPERSEDED" and newer:
                raise ValueError(f"proposal {proposal_id} was replaced by {newer[-1]} after new information arrived; "
                                 f"review {newer[-1]}")
            raise ValueError(f"proposal {proposal_id} is {p['status']}, not open")
        p.update(decided_by=actor, decided_t_min=now_t, decision_reason=reason.strip())
        if decision == "REJECT":
            p["status"] = "REJECTED"
            return store.put("trips", trip_id, trip, actor=actor, action="reject_proposal")
        p["status"] = "ACCEPTED"
        trip["legs"] = trip["legs"][:p["from_leg_index"]] + p["new_legs"]
        trip["route_version"] = trip.get("route_version", 1) + 1
        trip["awaiting_ack"] = proposal_id
        trip["attention"] = None
        if trip.get("escalation"):
            trip["escalation"] = {**trip["escalation"], "open": False}
        return store.put("trips", trip_id, trip, actor=actor, action="accept_proposal")


def acknowledge(store: OpsStore, trip_id: str, proposal_id: str, *, actor: str) -> dict:
    with store.transaction():
        trip = get_trip(store, trip_id)
        p = next((x for x in trip["proposals"] if x["proposal_id"] == proposal_id), None)
        if p is None or p["status"] != "ACCEPTED":
            raise ValueError("only an accepted route can be acknowledged")
        p["status"] = "ACKNOWLEDGED"
        p["acknowledged_by"] = actor
        trip["awaiting_ack"] = None
        return store.put("trips", trip_id, trip, actor=actor, action="acknowledge_route")


def reassess_all(store: OpsStore, *, trigger: dict, now_t: float, now_wall: Optional[datetime], roads: RoadGraph,
                 G: nx.DiGraph, seg_ctx: SegmentContext, destination_check=None, only: Optional[str] = None) -> list[dict]:
    """Reassess every active trip (or one) against the published forecast and the road-event log known at now_t."""
    fc = inc.get_forecast(store)
    if fc is None:
        return [{"trip_id": t["trip_id"], "action": "none", "note": "no forecast published"} for t in active_trips(store)]
    forecast, _ = fc
    book = inc.closures(store, now_t)
    out = []
    for trip in active_trips(store):
        if only and trip["trip_id"] != only:
            continue
        dest = destination_check(trip) if destination_check else (True, "")
        a = assess(trip, now_t=now_t, now_wall=now_wall, roads=roads, G=G, forecast=forecast, seg_ctx=seg_ctx,
                   closures=book, fix=trip.get("last_position"), destination_ok=dest)
        res = apply(store, trip["trip_id"], a, trigger=trigger, now_t=now_t)
        out.append({"trip_id": trip["trip_id"], "decision": a["decision"], "reason": a["reason"], **res})
    return out
