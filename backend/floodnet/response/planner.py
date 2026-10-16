"""Priority-based allocation of teams, vehicles and shelter places over arrival-aware missions, with replanning
that preserves history (MISC-04 Phase 2; docs/RESPONSE_PLANNER.md, DECISIONS.md D-27 .. D-29).

A mission = [vehicle -> team (pickup, if apart)] -> settlement (load) -> shelter (unload). Each leg is routed with
the Phase 1 arrival-aware router at the minute it starts, so the trip to the shelter is checked against the
forecast 15-30 minutes after dispatch, not against the map at dispatch.

Allocation (transparent greedy, not an optimiser):
  repeat: take the highest-priority settlement with unmet need; for every free (team, vehicle) pair whose team has
  every capability the settlement requires (D-34; names normalised lower/trimmed) and every shelter with free
  places, build the mission; keep the one that finishes first (ties: more people, fewest surplus capabilities so
  specialists stay free, then team / vehicle / shelter id); reserve seats and places;
  the team and vehicle become free at the shelter when it finishes. Stop when nothing feasible is left, then
  explain every remaining unmet need. Shuttle trips fall out naturally (the pair becomes free again).

Replanning at time `now` (the operational rules):
  COMPLETED     history: counts as served; the team/vehicle are where the mission ended.
  DISPATCHED / IN_PROGRESS  underway: never re-allocated. Remaining legs are re-routed from the estimated position
                (the node the vehicle reaches next) with the closures and forecast known now. If no route exists,
                a DISPATCHED assignment (nobody on board yet) is CANCELLED by the planner with the reason, the pair
                is freed at that position and the people return to the unmet pool; an IN_PROGRESS one (people on
                board) is kept with its planned legs and flagged `attention` -- only the operator cancels it (D-33).
  ACCEPTED      a commitment: kept if its mission is still feasible from its planned departure (>= now), else
                CANCELLED with the reason and re-planned.
  PROPOSED      re-computed every time.
Every change is listed with its reason; nothing in the history is edited.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Optional

import networkx as nx

from .. import config
from ..access.arrival_router import route_one
from ..access.events import STALE_AFTER_MIN, ClosureBook
from ..access.road_status import Forecast, SegmentContext
from ..contracts import RoadGraph
from ..routing.router import _snap
from . import fixtures as fx
from .ledger import COMMITTED, UNDERWAY, check_invariants

MAX_ASSIGNMENTS_PER_PLAN = 40


class Router:
    def __init__(self, roads: RoadGraph, G: nx.DiGraph, forecast: Forecast, seg_ctx: SegmentContext,
                 closures: ClosureBook, mode: str = "arrival"):
        self.roads, self.G, self.forecast, self.ctx, self.closures, self.mode = roads, G, forecast, seg_ctx, closures, mode
        self._node_cache: dict[tuple[float, float], int] = {}

    def node(self, lonlat) -> int:
        key = (round(float(lonlat[0]), 7), round(float(lonlat[1]), 7))
        if key not in self._node_cache:
            self._node_cache[key] = _snap(self.roads, [key])[0]
        return self._node_cache[key]

    def leg(self, o: int, d: int, vehicle_type: str, depart: float) -> dict:
        if o == d:
            return {"reachable": True, "route": None, "segments": [], "travel_time_min": 0.0, "arrival_t_min": depart,
                    "status_counts_at_arrival": {}, "valid_at_arrival": True, "blocked_at_arrival": [], "length_m": 0.0}
        return route_one(self.roads, self.G, o, d, vehicle=vehicle_type, dispatch_t_min=depart,
                         forecast=self.forecast, seg_ctx=self.ctx, closures=self.closures, mode=self.mode)


# ------------------------------------------------------------------ state from fixtures + events
def _norm_caps(values) -> list[str]:
    """Capability names compared lower-cased and trimmed, duplicates dropped, order kept (D-34)."""
    out: list[str] = []
    for v in values or []:
        c = str(v).strip().lower()
        if c and c not in out:
            out.append(c)
    return out


_SUBJECT_KEY = {"vehicle_unavailable": "vehicle_id", "vehicle_available": "vehicle_id",
                "shelter_update": "shelter_id", "settlement_update": "settlement_id"}
_SUBJECT_TABLE = {"vehicle_id": "vehicles", "shelter_id": "shelters", "settlement_id": "settlements"}


def _subject(e: dict) -> tuple[str, str]:
    key = _SUBJECT_KEY[e["kind"]]
    return _SUBJECT_TABLE[key], e[key]


def _apply_group(st: dict, group: list[dict], now: float, log: list[str]) -> None:
    """Apply the reports on one subject that describe the same moment (same applies_from and received). When they
    disagree the more conservative value wins (D-32): larger occupancy, smaller total_capacity, not-open status,
    unavailable over available, more people needing help; the conflict is logged with both event ids."""
    table, sid = _subject(group[0])
    rec = st[table][sid]
    chosen, picks = group[-1], []
    if table == "vehicles":
        down = [e for e in group if e["kind"] == "vehicle_unavailable"]
        if down:
            chosen = down[0]
            rec.update(available=False, unavailable_reason=chosen["reason"] or "reported unavailable")
            if len(down) < len(group):
                picks.append("unavailable")
        else:
            rec.update(available=True, unavailable_reason="")
    elif table == "shelters":
        for f, pick in (("occupancy", max), ("total_capacity", min)):
            vals = [(e[f], e) for e in group if f in e]
            if vals:
                v, e = pick(vals, key=lambda x: x[0])
                rec[f] = v
                if len({x for x, _ in vals}) > 1:
                    picks.append(f"{f} {v}"); chosen = e
        stat = [(e["status"], e) for e in group if "status" in e]
        if stat:
            closed = [(x, e) for x, e in stat if x != "open"]
            v, e = closed[0] if closed else stat[0]
            rec["status"] = v
            if len({x for x, _ in stat}) > 1:
                picks.append(f"status {v}"); chosen = e
    else:
        vals = [(e["people_needing_help"], e) for e in group if "people_needing_help" in e]
        if vals:
            v, e = max(vals, key=lambda x: x[0])
            rec["people_needing_help"] = v
            if len({x for x, _ in vals}) > 1:
                picks.append(f"people_needing_help {v}"); chosen = e
    if picks:
        ids = [e["event_id"] for e in group]
        log.append(f"conflict between {', '.join(ids[:-1])} and {ids[-1]} on {sid} at T+{group[0]['applies_from_t_min']:g}: "
                   f"used the more conservative value ({', '.join(picks)})")
    age = now - chosen["received_t_min"]
    stale = age > STALE_AFTER_MIN[chosen["source_tier"]]
    rec.update(last_report_t_min=chosen["received_t_min"], last_report_event_id=chosen["event_id"], stale=stale)
    if stale:
        log.append(f"{sid} report {chosen['event_id']} is stale: {age:g} min old (received T+{chosen['received_t_min']:g} "
                   f"by {chosen['source_tier']}, threshold {STALE_AFTER_MIN[chosen['source_tier']]} min); last known value kept")


def build_state(resources: dict, events: list[dict], now: float) -> dict:
    """Resources as they are known at `now`: only confirmed (non-PUBLIC) events received by then whose applies_from
    has come are applied, in (applies_from, received, event_id) order so the result never depends on the array
    order (D-32). PUBLIC reports are listed as `events_unconfirmed`, reports for a later time as
    `events_scheduled`, reports not yet received as `events_pending`. Each shelter / vehicle / settlement records
    its last report and whether it is stale; a stale value is kept, never zeroed."""
    st = {k: {r["id"]: dict(r) for r in resources[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    for r in list(st["vehicles"].values()) + list(st["teams"].values()):
        # a resource may arrive already unavailable (registry state, D-43); the demo fixtures never are
        down = r.get("available", True) is False
        r.update(available=not down, unavailable_reason=r.get("unavailable_reason", "") if down else "")
    for table in ("settlements", "shelters", "vehicles"):
        for r in st[table].values():
            r.update(last_report_t_min=None, last_report_event_id=None, stale=False)
    for sl in st["settlements"].values():
        sl["required_capabilities"] = _norm_caps(sl.get("required_capabilities"))
    for t in st["teams"].values():
        t["capabilities"] = _norm_caps(t.get("capabilities"))
    log: list[str] = []
    known = [e for e in events if e["received_t_min"] <= now]
    unconfirmed = [e for e in known if e.get("source_tier", "OPERATOR") == "PUBLIC"]
    confirmed = [e for e in known if e not in unconfirmed]
    used = sorted((e for e in confirmed if e["applies_from_t_min"] <= now),
                  key=lambda e: (e["applies_from_t_min"], e["received_t_min"], e["event_id"]))
    i = 0
    while i < len(used):
        j = i + 1
        moment = (_subject(used[i]), used[i]["applies_from_t_min"], used[i]["received_t_min"])
        while j < len(used) and (_subject(used[j]), used[j]["applies_from_t_min"], used[j]["received_t_min"]) == moment:
            j += 1
        _apply_group(st, used[i:j], now, log)
        i = j
    st["events_used"] = used
    st["events_scheduled"] = [e for e in confirmed if e["applies_from_t_min"] > now]
    st["events_unconfirmed"] = [
        {**e, "status": "UNCONFIRMED",
         "explanation": f"{e['event_id']} {e['kind']} {_subject(e)[1]} by PUBLIC: UNCONFIRMED; received T+{e['received_t_min']:g}, "
                        f"applies T+{e['applies_from_t_min']:g}; not applied: a PUBLIC report never changes a resource; "
                        "record an operator report to confirm"} for e in unconfirmed]
    st["events_pending"] = [e for e in events if e["received_t_min"] > now]
    st["log"] = log
    return st


# ------------------------------------------------------------------ missions
def _mission_legs(rt: Router, team: dict, vehicle: dict, settlement: dict, shelter: dict, depart: float) -> dict:
    """Legs with absolute times, or {'feasible': False, 'reason': ...}."""
    legs, t, pos = [], depart, rt.node(vehicle["lonlat"])
    stops = [("pickup", team["lonlat"], team["name"], fx.TEAM_PICKUP_MIN)] if rt.node(team["lonlat"]) != pos else []
    stops += [("to_settlement", settlement["lonlat"], settlement["name"], fx.LOAD_MIN),
              ("to_shelter", shelter["lonlat"], shelter["name"], fx.UNLOAD_MIN)]
    for kind, lonlat, label, dwell in stops:
        d = rt.node(lonlat)
        r = rt.leg(pos, d, vehicle["type"], t)
        if not r["reachable"]:
            why = r.get("unreachable_reason", "")
            cat = ("no feasible route: confirmed closures" if "closure" in why and "forecast" not in why else
                   "no feasible route: forecast flooding" if "closure" not in why else
                   "no feasible route: closures and flooding")
            return {"feasible": False, "reason": cat, "detail": f"{kind.replace('_', ' ')} leg: {why}"}
        if r["arrival_t_min"] > rt.forecast.horizon_t_min + 1e-9:
            return {"feasible": False, "reason": "travel time too long: mission runs past the forecast horizon",
                    "detail": f"{label} reached at T+{r['arrival_t_min']:.0f}, horizon T+{rt.forecast.horizon_t_min:g}"}
        legs.append({"kind": kind, "to": label, "from_node": pos, "to_node": d, "depart_t_min": round(t, 2),
                     "arrive_t_min": round(r["arrival_t_min"], 2), "done_t_min": round(r["arrival_t_min"] + dwell, 2),
                     "dwell_min": dwell, "travel_time_min": r["travel_time_min"], "length_m": r.get("length_m", 0.0),
                     "route": r["route"], "status_counts": r["status_counts_at_arrival"],
                     "segments": [{k: s[k] for k in ("seg_id", "name", "enter_t_min", "exit_t_min", "to_node", "status", "depth_cm")}
                                  for s in r["segments"]]})
        t, pos = r["arrival_t_min"] + dwell, d
    return {"feasible": True, "legs": legs, "done_t_min": legs[-1]["done_t_min"], "end_node": pos}


def _position_at(legs: list[dict], now: float) -> tuple[int, int, float]:
    """(index of the first leg not finished, node the vehicle is at or reaches next, time it is there)."""
    for i, leg in enumerate(legs):
        if now < leg["depart_t_min"]:
            return i, leg["from_node"], leg["depart_t_min"]
        if now < leg["arrive_t_min"]:
            for s in leg["segments"]:
                if now < s["exit_t_min"]:
                    return i, s["to_node"], s["exit_t_min"]
            return i, leg["to_node"], leg["arrive_t_min"]
        if now < leg["done_t_min"]:
            return i + 1, leg["to_node"], leg["done_t_min"]
    return len(legs), legs[-1]["to_node"], legs[-1]["done_t_min"]


def _reroute_underway(rt: Router, a: dict, state: dict, now: float) -> dict:
    """Re-route the remaining legs of an underway assignment from the vehicle's estimated position."""
    legs = a["legs"]
    i, pos, t = _position_at(legs, now)
    if i >= len(legs):
        # Nothing left to route. Overdue only if the planned completion is really in the past; a vehicle still in
        # its final dwell (unloading at the shelter) is on schedule (regression: tests/test_planner_dwell.py).
        if now < legs[-1]["done_t_min"]:
            return {"ok": True, "legs": legs, "changed": False}
        return {"ok": True, "legs": legs, "changed": False,
                "attention": f"planned completion T+{legs[-1]['done_t_min']:.0f} has passed; confirm completion or cancel"}
    vehicle = state["vehicles"][a["vehicle_id"]]
    new_legs, changed = legs[:i], False
    for leg in legs[i:]:
        if leg["kind"] == "pickup" and pos == leg["to_node"]:
            new_legs.append(leg); pos, t = leg["to_node"], leg["done_t_min"]; continue
        r = rt.leg(pos, leg["to_node"], vehicle["type"], t)
        if not r["reachable"]:
            return {"ok": False, "reason": f"{leg['kind'].replace('_', ' ')} leg to {leg['to']} has no route: "
                                          f"{r.get('unreachable_reason', 'unreachable')}", "freed_at": (pos, t)}
        same = [s["seg_id"] for s in r["segments"]] == [s["seg_id"] for s in leg["segments"] if s["enter_t_min"] >= t - 1e-9]
        if not same or abs(r["arrival_t_min"] - leg["arrive_t_min"]) > 1e-6:
            changed = True
        new_legs.append({**leg, "from_node": pos, "depart_t_min": round(t, 2), "arrive_t_min": round(r["arrival_t_min"], 2),
                         "done_t_min": round(r["arrival_t_min"] + leg["dwell_min"], 2), "travel_time_min": r["travel_time_min"],
                         "route": r["route"] if r["route"] is not None else leg["route"], "status_counts": r["status_counts_at_arrival"],
                         "segments": [{k: s[k] for k in ("seg_id", "name", "enter_t_min", "exit_t_min", "to_node", "status", "depth_cm")}
                                      for s in r["segments"]]})
        pos, t = leg["to_node"], r["arrival_t_min"] + leg["dwell_min"]
    return {"ok": True, "legs": new_legs, "changed": changed}


def _shelter_room(SH: dict, reserved: Counter, sid: str) -> int:
    sh = SH[sid]
    return sh["total_capacity"] - sh["occupancy"] - reserved[sid] if sh.get("status", "open") == "open" else 0


def _redirect_shelter(rt: Router, a: dict, legs: list[dict], SH: dict, reserved: Counter, V: dict) -> Optional[dict]:
    """Re-aim the shelter leg at another open shelter with room, from where that leg starts. Earliest done wins."""
    last = legs[-1]
    vehicle_type = V[a["vehicle_id"]]["type"]
    best = None
    for sh in SH.values():
        if sh["id"] == a["shelter_id"] or _shelter_room(SH, reserved, sh["id"]) < a["people"]:
            continue
        r = rt.leg(last["from_node"], rt.node(sh["lonlat"]), vehicle_type, last["depart_t_min"])
        if not r["reachable"] or r["arrival_t_min"] > rt.forecast.horizon_t_min + 1e-9:
            continue
        leg = {**last, "to": sh["name"], "to_node": rt.node(sh["lonlat"]), "arrive_t_min": round(r["arrival_t_min"], 2),
               "done_t_min": round(r["arrival_t_min"] + last["dwell_min"], 2), "travel_time_min": r["travel_time_min"],
               "route": r["route"], "status_counts": r["status_counts_at_arrival"],
               "segments": [{k: s[k] for k in ("seg_id", "name", "enter_t_min", "exit_t_min", "to_node", "status", "depth_cm")}
                            for s in r["segments"]]}
        if best is None or leg["done_t_min"] < best["legs"][-1]["done_t_min"]:
            best = {"shelter_id": sh["id"], "legs": legs[:-1] + [leg]}
    return best


# ------------------------------------------------------------------ the plan
def _signature(a: dict) -> tuple:
    return (a["settlement_id"], a["team_id"], a["vehicle_id"], a["shelter_id"], a["people"])


def plan(resources: dict, response_events: list[dict], ledger: list[dict], rt: Router, now: float,
         previous_version: int = 0) -> dict:
    t_start = time.perf_counter()
    state = build_state(resources, response_events, now)
    S, SH, T, V = state["settlements"], state["shelters"], state["teams"], state["vehicles"]
    changes: list[dict] = []
    log: list[str] = list(state["log"])
    new_version = previous_version + 1

    served = Counter()                 # people delivered (COMPLETED)
    covered = Counter()                # people in committed missions
    reserved = Counter()               # shelter places held by committed missions
    free_at: dict[str, tuple[float, int]] = {}       # resource id -> (time, node) it is next free
    kept: list[dict] = []
    next_id = 1 + max((int(a["assignment_id"][1:]) for a in ledger if a["assignment_id"][1:].isdigit()), default=0)

    def free(rid: str, t: float, node: int):
        if rid not in free_at or t > free_at[rid][0]:
            free_at[rid] = (t, node)

    def lonlat_of(node: int) -> list[float]:
        return [round(float(x), 7) for x in rt.roads.node_lonlat[node]]

    def where(rid: str, base: dict) -> dict:
        """The resource at the place it is next free (after its earlier missions), else at its base."""
        return {**base, "lonlat": lonlat_of(free_at[rid][1])} if rid in free_at else base

    for a in sorted(ledger, key=lambda a: a["legs"][0]["depart_t_min"] if a.get("legs") else 1e9):
        if a["status"] == "CANCELLED":
            kept.append(a); continue
        if a["status"] == "COMPLETED":
            served[a["settlement_id"]] += a["people"]
            SH[a["shelter_id"]]["occupancy"] += a["people"]
            end = a["legs"][-1]
            for rid in (a["team_id"], a["vehicle_id"]):
                free(rid, end["done_t_min"], end["to_node"])
            kept.append(a); continue
        if a["status"] == "PROPOSED":
            continue                                                    # re-computed below
        # ACCEPTED or underway: still feasible? A mission that has not left yet (accepted, or dispatched ahead of its
        # planned departure) leaves no earlier than its team and vehicle are free: when an earlier mission of the same
        # pair gets longer (re-route, redirect), this one is pushed back instead of overlapping it.
        earliest = max([now] + [free_at[rid][0] for rid in (a["team_id"], a["vehicle_id"]) if rid in free_at])
        if a["status"] in UNDERWAY and a["legs"][0]["depart_t_min"] <= now:
            r = _reroute_underway(rt, a, state, now)
        else:
            depart = max(earliest, a["legs"][0]["depart_t_min"])
            m = _mission_legs(rt, where(a["team_id"], T[a["team_id"]]), where(a["vehicle_id"], V[a["vehicle_id"]]),
                              S[a["settlement_id"]], SH[a["shelter_id"]], depart)
            r = ({"ok": True, "legs": m["legs"], "changed": [l["arrive_t_min"] for l in m["legs"]] != [l["arrive_t_min"] for l in a["legs"]]}
                 if m["feasible"] else {"ok": False, "reason": m["reason"], "freed_at": (a["legs"][0]["from_node"], depart)})
            if r["ok"] and not V[a["vehicle_id"]]["available"]:
                r = {"ok": False, "reason": f"vehicle {a['vehicle_id']} unavailable: {V[a['vehicle_id']]['unavailable_reason']}",
                     "freed_at": (a["legs"][0]["from_node"], depart)}
        if not r["ok"] and a["status"] == "IN_PROGRESS":
            # people are on board: the planner never cancels; the planned legs are kept and the operator decides (D-33)
            r = {"ok": True, "legs": a["legs"], "changed": False,
                 "attention": f"no feasible route from the estimated position at T+{now:g}: {r['reason']}; operator decision needed"}
        if r["ok"] and _shelter_room(SH, reserved, a["shelter_id"]) < a["people"]:
            alt = _redirect_shelter(rt, a, r["legs"], SH, reserved, V)
            if alt is not None:
                r = {"ok": True, "legs": alt["legs"], "changed": True, "shelter_id": alt["shelter_id"],
                     "note": f"redirected from {a['shelter_id']} to {alt['shelter_id']}: {a['shelter_id']} has no room"}
            elif a["status"] in UNDERWAY:
                note = f"destination {a['shelter_id']} has no room and no other shelter has room; operator decision needed"
                r = {**r, "attention": f"{r['attention']}; {note}" if r.get("attention") else note}
            else:
                r = {"ok": False, "reason": f"shelter capacity exhausted: {a['shelter_id']} has no room and no alternative",
                     "freed_at": (r["legs"][0]["from_node"], max(now, r["legs"][0]["depart_t_min"]))}
        if not r["ok"]:
            reason = f"cancelled by the planner at T+{now:g}: {r['reason']}"
            a = {**a, "status": "CANCELLED", "history": a["history"] + [{"status": "CANCELLED", "t_min": now, "reason": reason, "by": "planner"}]}
            changes.append({"assignment_id": a["assignment_id"], "change": "cancelled", "reason": r["reason"]})
            log.append(f"{a['assignment_id']} {reason}")
            node, t = r["freed_at"]
            for rid in (a["team_id"], a["vehicle_id"]):
                free(rid, t, node)
            kept.append(a); continue
        if r.get("note"):
            changes.append({"assignment_id": a["assignment_id"], "change": "redirected", "reason": r["note"]})
            log.append(f"{a['assignment_id']} {r['note']}")
        elif r["changed"]:
            changes.append({"assignment_id": a["assignment_id"], "change": "re-routed", "reason": "closures or forecast changed on the remaining legs"})
            log.append(f"{a['assignment_id']} re-routed from the estimated position at T+{now:g}")
        a = {**a, "legs": r["legs"], "shelter_id": r.get("shelter_id", a["shelter_id"])}
        a.pop("attention", None)
        if r.get("attention"):
            a["attention"] = r["attention"]
            changes.append({"assignment_id": a["assignment_id"], "change": "attention", "reason": r["attention"]})
            log.append(f"{a['assignment_id']} needs attention: {r['attention']}")
        covered[a["settlement_id"]] += a["people"]
        if not a.get("attention"):
            reserved[a["shelter_id"]] += a["people"]
        end = a["legs"][-1]
        for rid in (a["team_id"], a["vehicle_id"]):
            free(rid, end["done_t_min"], end["to_node"])
        kept.append(a)

    # resources not yet used start at their base, free now
    for rid, r in list(T.items()) + list(V.items()):
        if rid not in free_at:
            free_at[rid] = (now, rt.node(r["lonlat"]))

    prev_prop: dict[tuple, list[dict]] = {}
    for a in ledger:
        if a["status"] == "PROPOSED":
            prev_prop.setdefault(_signature(a), []).append(a)
    remaining = {sid: max(0, s["people_needing_help"] - served[sid] - covered[sid]) for sid, s in S.items()}
    unmet: dict[str, dict] = {}
    proposed: list[dict] = []
    order = sorted(S.values(), key=lambda s: (fx.PRIORITY_ORDER.get(s["priority"], 9), -remaining[s["id"]]))
    for s in order:
        sid = s["id"]
        while remaining[sid] > 0 and len(proposed) < MAX_ASSIGNMENTS_PER_PLAN:
            reasons: Counter = Counter()
            details: dict[str, str] = {}
            best = None
            required = s["required_capabilities"]
            avail_T = [t for t in T.values() if t["available"]]
            teams_ok = [t for t in avail_T if set(required) <= set(t["capabilities"])]      # hard constraint (D-34)
            if not teams_ok:
                missing = [c for c in required if not any(c in t["capabilities"] for t in avail_T)]
                key = (f"no team with capability '{missing[0]}'" if missing else
                       "no team with all of the capabilities " + ", ".join(f"'{c}'" for c in required))
                reasons[key] += 1
                down = [f"{t['id']} ({t['unavailable_reason']})" for t in T.values()
                        if not t["available"] and set(required) <= set(t["capabilities"])]
                if down:
                    details.setdefault(key, "qualifying but unavailable: " + "; ".join(down))
            shelters_open = [sh for sh in SH.values() if sh.get("status", "open") == "open"
                             and sh["total_capacity"] - sh["occupancy"] - reserved[sh["id"]] > 0]
            if not shelters_open:
                reasons["shelter capacity exhausted"] += 1
            vehicles_ok = [v for v in V.values() if v["available"]]
            if not vehicles_ok:
                reasons["no available vehicle"] += 1
            for team in teams_ok:
                surplus = len(set(team["capabilities"]) - set(required))
                for v in vehicles_ok:
                    t_free, node_t = free_at[team["id"]]
                    t_free_v, node_v = free_at[v["id"]]
                    depart = max(now, t_free, t_free_v)
                    team_here, veh_here = {**team, "lonlat": lonlat_of(node_t)}, {**v, "lonlat": lonlat_of(node_v)}
                    for sh in shelters_open:
                        people = min(remaining[sid], v["passenger_capacity"], sh["total_capacity"] - sh["occupancy"] - reserved[sh["id"]])
                        m = _mission_legs(rt, team_here, veh_here, s, sh, depart)
                        if not m["feasible"]:
                            reasons[m["reason"]] += 1; details.setdefault(m["reason"], m.get("detail", "")); continue
                        cand = (m["done_t_min"], -people, surplus, team["id"], v["id"], sh["id"], m, people)
                        if best is None or cand[:6] < best[:6]:
                            best = cand
            if best is None:
                qual = ("qualifying teams: " + ", ".join(t["id"] for t in teams_ok)) if teams_ok else ""
                unmet[sid] = {"people": remaining[sid],
                              "reasons": [{"reason": k, "count": n, "detail": "; ".join(x for x in (details.get(k, ""), qual) if x)}
                                          for k, n in reasons.most_common()]}
                break
            done, _, _, tid, vid, shid, m, people = best
            matches = prev_prop.get((sid, tid, vid, shid, people))
            old = matches.pop(0) if matches else None
            a = {"assignment_id": old["assignment_id"] if old else f"A{next_id}", "settlement_id": sid, "team_id": tid,
                 "vehicle_id": vid, "shelter_id": shid, "people": people, "status": "PROPOSED", "legs": m["legs"],
                 "planned_done_t_min": done, "is_new": old is None,
                 "history": old["history"] if old else [{"status": "PROPOSED", "t_min": now, "reason": f"plan v{previous_version + 1}", "by": "planner"}],
                 "explanation": (f"{S[sid]['priority']} priority; earliest completion T+{done:.0f} among feasible team/vehicle/"
                                 f"shelter combinations; {people} of {remaining[sid]} remaining people fit the vehicle and shelter"
                                 + (f"; team {tid} has the required capabilities: {', '.join(required)}" if required else ""))}
            next_id += 0 if old else 1
            proposed.append(a)
            remaining[sid] -= people
            reserved[shid] += people
            for rid in (tid, vid):
                free_at[rid] = (done, m["end_node"])
            log.append(f"{a['assignment_id']} proposed: {tid}+{vid} take {people} from {sid} to {shid}, done T+{done:.0f}")
        if remaining[sid] > 0 and sid not in unmet:
            unmet[sid] = {"people": remaining[sid], "reasons": [{"reason": "assignment limit per plan reached", "count": 1, "detail": ""}]}

    all_assignments = kept + proposed
    problems = check_invariants(all_assignments, SH)
    for a in proposed:
        if a.pop("is_new"):
            changes.append({"assignment_id": a["assignment_id"], "change": "proposed", "reason": a["explanation"]})
    for a in all_assignments:                       # every assignment this plan writes carries its version (D-33)
        a["plan_version"] = new_version
    for a in (x for lst in prev_prop.values() for x in lst):
        changes.append({"assignment_id": a["assignment_id"], "change": "proposal withdrawn", "reason": "no longer the best feasible use of the resources"})

    horizon = rt.forecast.horizon_t_min
    busy = Counter()
    for a in all_assignments:
        if a["status"] in COMMITTED or a["status"] == "PROPOSED":
            t0, t1 = max(now, a["legs"][0]["depart_t_min"]), a["legs"][-1]["done_t_min"]
            for rid in (a["team_id"], a["vehicle_id"]):
                busy[rid] += max(0.0, min(t1, horizon) - t0)
    window = max(horizon - now, 1e-9)
    need_total = sum(s["people_needing_help"] for s in S.values())
    summary = {"people_needing_help": need_total, "served": sum(served.values()), "covered_committed": sum(covered.values()),
               "covered_proposed": sum(a["people"] for a in proposed), "unmet": sum(u["people"] for u in unmet.values()),
               "assignments_proposed": len(proposed), "assignments_cancelled_now": sum(1 for c in changes if c["change"] == "cancelled"),
               "utilisation": {rid: round(min(1.0, busy[rid] / window), 2) for rid in list(T) + list(V)},
               "shelter_free_after_plan": {sid: sh["total_capacity"] - sh["occupancy"] - reserved[sid] for sid, sh in SH.items()},
               "replan_ms": round((time.perf_counter() - t_start) * 1000, 1)}
    return {"plan_version": new_version, "sim_t_min": now, "assignments": all_assignments, "unmet": unmet,
            "changes": changes, "log": log, "invariant_violations": problems, "summary": summary,
            "state": {"settlements": [{**s, "served": served[s["id"]], "covered": covered[s["id"]] + sum(a["people"] for a in proposed if a["settlement_id"] == s["id"]),
                                       "remaining": remaining[s["id"]]} for s in S.values()],
                      "shelters": [{**sh, "reserved": reserved[sh["id"]], "free": sh["total_capacity"] - sh["occupancy"] - reserved[sh["id"]]} for sh in SH.values()],
                      "teams": [{**t, "free_at_t_min": round(free_at[t["id"]][0], 1), "position_lonlat": lonlat_of(free_at[t["id"]][1])} for t in T.values()],
                      "vehicles": [{**v, "free_at_t_min": round(free_at[v["id"]][0], 1), "position_lonlat": lonlat_of(free_at[v["id"]][1])} for v in V.values()],
                      "events_used": state["events_used"], "events_pending": state["events_pending"],
                      "events_scheduled": state["events_scheduled"], "events_unconfirmed": state["events_unconfirmed"]}}
