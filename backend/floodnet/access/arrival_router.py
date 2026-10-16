"""Arrival-aware routing vs current-status-only routing (MISC-04 Phase 1; docs/ROAD_ACCESS.md).

Both use the same road graph, the same ASSUMED speeds (config.ASSUMED_SPEED_KMH) and the same closure events known
at the decision time. They differ only in WHEN each road is checked:
  current   every road is checked as it is at the dispatch time (the flood frame "now", closures in force now).
            This is the baseline the competition asks us to beat: a normal planner looking at today's map.
  arrival   each road is checked over the minutes the vehicle is expected to be on it (dispatch time + travel
            time accumulated along the route), against the forecast frames for those minutes.

Search: Dijkstra over (cost, arrival time) labels. Cost = travel minutes x ACCESS_COST_FACTOR[status]; AT_RISK and
CLOSED roads are not entered. The vehicle never waits for water to recede. With time-dependent statuses a label-
setting search is a heuristic, not a proof of optimality; the chosen route is re-checked segment by segment
afterwards and that check is what the response reports.

Afterwards the current-status route is ALSO checked at its real arrival times, which shows whether "open now" would
still have been open when the vehicle got there. A recommended route is a candidate with lower modelled flood
exposure, never a guarantee of safety.
"""
from __future__ import annotations

import heapq
from collections import Counter
from typing import Callable, Optional

import networkx as nx

from .. import config
from ..contracts import RoadGraph
from ..routing.router import _path_geometry
from .events import ClosureBook
from .road_status import BLOCKING, Forecast, SegmentContext, classify


def speed_kmh(highway: str, vehicle: str) -> float:
    if vehicle == "pedestrian":
        return config.ASSUMED_PEDESTRIAN_SPEED_KMH
    return config.ASSUMED_SPEED_KMH.get(highway, config.ASSUMED_DEFAULT_SPEED_KMH)


def travel_min(length_m: float, highway: str, vehicle: str) -> float:
    return float(length_m) / 1000.0 / speed_kmh(highway, vehicle) * 60.0


class _Ctx:
    """Everything a status check needs, bound once per request."""

    def __init__(self, roads: RoadGraph, forecast: Forecast, seg_ctx: SegmentContext, closures: ClosureBook,
                 vehicle: str, dispatch_t_min: float):
        if vehicle not in config.VEHICLE_LIMIT_CM:
            raise KeyError(f"unknown vehicle '{vehicle}'; known: {sorted(config.VEHICLE_LIMIT_CM)}")
        self.seg = {s.seg_id: s for s in roads.segments}
        self.forecast, self.seg_ctx, self.closures = forecast, seg_ctx, closures
        self.vehicle, self.limit = vehicle, float(config.VEHICLE_LIMIT_CM[vehicle])
        self.dispatch = float(dispatch_t_min)
        self._no_closures = ClosureBook([], self.dispatch)

    def status(self, seg_id: str, t0: float, t1: float, mode: str) -> dict:
        if mode == "current":                        # the map as it is at dispatch, whatever the travel time
            t0 = t1 = self.dispatch
        elif mode == "closures_only":                # diagnostics: ignore the flood forecast entirely
            c = self.closures.closed_during(seg_id, t0, t1, vehicle=self.vehicle)
            return {"status": "CLOSED"} if c else {"status": "USABLE"}
        closures = self._no_closures if mode == "forecast_only" else self.closures   # diagnostics: ignore closures
        return classify(seg_id, t0, t1, forecast=self.forecast, ctx=self.seg_ctx, closures=closures,
                        limit_cm=self.limit, vehicle=self.vehicle)


def _search(G: nx.DiGraph, o: int, d: int, cx: _Ctx, mode: str,
            skip: Callable[[dict], bool] = lambda st: st["status"] in BLOCKING) -> tuple[Optional[list[int]], Counter]:
    best = {o: 0.0}
    prev: dict[int, int] = {}
    heap = [(0.0, cx.dispatch, o)]
    blocked: Counter = Counter()
    while heap:
        cost, t, u = heapq.heappop(heap)
        if cost > best.get(u, float("inf")):
            continue
        if u == d:
            path = [d]
            while path[-1] != o:
                path.append(prev[path[-1]])
            return path[::-1], blocked
        for v, e in G[u].items():
            seg = cx.seg[e["seg_id"]]
            dt = travel_min(e["length_m"], seg.highway, cx.vehicle)
            st = cx.status(e["seg_id"], t, t + dt, mode)
            if skip(st):
                blocked[st["status"]] += 1
                continue
            nc = cost + dt * config.ACCESS_COST_FACTOR.get(st["status"], 1.0)
            if nc < best.get(v, float("inf")):
                best[v], prev[v] = nc, u
                heapq.heappush(heap, (nc, t + dt, v))
    return None, blocked


def _timeline(G: nx.DiGraph, path: list[int], cx: _Ctx) -> list[dict]:
    """The path checked segment by segment at the minutes the vehicle is expected to be on each road."""
    out, t = [], cx.dispatch
    for a, b in zip(path[:-1], path[1:]):
        e = G.edges[a, b]
        seg = cx.seg[e["seg_id"]]
        dt = travel_min(e["length_m"], seg.highway, cx.vehicle)
        st = cx.status(e["seg_id"], t, t + dt, "arrival")
        out.append({"seg_id": seg.seg_id, "name": seg.name, "highway": seg.highway,
                    "length_m": round(float(seg.length_m), 1), "enter_t_min": round(t, 2),
                    "exit_t_min": round(t + dt, 2), "to_node": int(b), **st})
        t += dt
    return out


def _route_block(roads: RoadGraph, G: nx.DiGraph, path: Optional[list[int]], cx: _Ctx, blocked: Counter) -> dict:
    if path is None:
        return {"reachable": False, "route": None, "segments": [], "blocked_edges_by_status": dict(blocked)}
    geom, length, _ = _path_geometry(roads, G, path)
    segs = _timeline(G, path, cx)
    arrival = segs[-1]["exit_t_min"] if segs else cx.dispatch
    counts = Counter(s["status"] for s in segs)
    bad = [s for s in segs if s["status"] in BLOCKING]
    return {"reachable": True, "route": geom, "length_m": round(length, 1),
            "travel_time_min": round(arrival - cx.dispatch, 2), "arrival_t_min": round(arrival, 2),
            "status_counts_at_arrival": {k: counts.get(k, 0) for k in ("USABLE", "UNCERTAIN", "UNKNOWN", "AT_RISK", "CLOSED")},
            "valid_at_arrival": not bad,
            "blocked_at_arrival": [{k: s[k] for k in ("seg_id", "name", "status", "depth_cm", "enter_t_min", "reason")}
                                   for s in bad],
            "segments": segs, "blocked_edges_by_status": dict(blocked)}


def _unreachable_reason(G: nx.DiGraph, o: int, d: int, cx: _Ctx) -> str:
    if _search(G, o, d, cx, "closures_only")[0] is not None:
        return ("every path crosses a road whose forecast depth reaches the vehicle limit by the time the vehicle "
                "would get there")
    if _search(G, o, d, cx, "forecast_only")[0] is not None:
        return "confirmed road closures block every path"
    return "confirmed closures and forecast flooding together block every path"


def route_one(roads: RoadGraph, G: nx.DiGraph, o: int, d: int, *, vehicle: str, dispatch_t_min: float,
              forecast: Forecast, seg_ctx: SegmentContext, closures: ClosureBook, mode: str = "arrival") -> dict:
    """One route by one method ("arrival" or "current"), checked road by road at its real arrival times."""
    cx = _Ctx(roads, forecast, seg_ctx, closures, vehicle, dispatch_t_min)
    path, blocked = _search(G, o, d, cx, mode)
    block = _route_block(roads, G, path, cx, blocked)
    if not block["reachable"]:
        block["unreachable_reason"] = (_unreachable_reason(G, o, d, cx) if mode == "arrival"
                                       else "roads closed or flooded at the dispatch time block every path")
    return block


def plan_access(roads: RoadGraph, G: nx.DiGraph, o: int, d: int, *, vehicle: str, dispatch_t_min: float,
                forecast: Forecast, seg_ctx: SegmentContext, closures: ClosureBook) -> dict:
    cx = _Ctx(roads, forecast, seg_ctx, closures, vehicle, dispatch_t_min)
    a_path, a_blocked = _search(G, o, d, cx, "arrival")
    c_path, c_blocked = _search(G, o, d, cx, "current")
    arrival = _route_block(roads, G, a_path, cx, a_blocked)
    current = _route_block(roads, G, c_path, cx, c_blocked)
    if not arrival["reachable"]:
        arrival["unreachable_reason"] = _unreachable_reason(G, o, d, cx)
    if not current["reachable"]:
        current["unreachable_reason"] = "roads closed or flooded at the dispatch time block every path"
    return {"arrival_aware": arrival, "current_status": current, "comparison": _compare(arrival, current)}


def _compare(a: dict, c: dict) -> dict:
    out = {"same_route": bool(a["reachable"] and c["reachable"] and
                              [s["seg_id"] for s in a["segments"]] == [s["seg_id"] for s in c["segments"]]),
           "current_status_valid_at_arrival": c.get("valid_at_arrival") if c["reachable"] else None,
           "current_status_blocked_count": len(c.get("blocked_at_arrival", [])),
           "extra_travel_min": (round(a["travel_time_min"] - c["travel_time_min"], 2)
                                if a["reachable"] and c["reachable"] else None)}
    n = out["current_status_blocked_count"]
    if not a["reachable"] and not c["reachable"]:
        s = "Neither method finds a usable route."
    elif not a["reachable"]:
        s = (f"The current-status route looks open now but crosses {n} road(s) closed or at/over the vehicle limit "
             "by arrival; no arrival-aware route exists.") if n else "No arrival-aware route exists."
    elif not c["reachable"]:
        s = "Roads blocked at dispatch time stop the current-status planner; the arrival-aware route is shown."
    elif out["same_route"]:
        s = "Both methods choose the same route."
    elif n:
        s = (f"The current-status route crosses {n} road(s) closed or at/over the vehicle limit by the time the "
             f"vehicle reaches them; the arrival-aware route avoids them ({out['extra_travel_min']:+.1f} min).")
    else:
        s = (f"Routes differ; the current-status route stays valid at arrival. The arrival-aware route trades "
             f"{out['extra_travel_min']:+.1f} min for fewer uncertain or unknown roads.")
    out["summary"] = s
    return out
