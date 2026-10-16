"""Who should get an advisory: the impact zone and per-subscriber eligibility (DECISIONS D-44, LIVE_OPERATIONS.md §6).

Impact zone for a window [now, now + window_min] (clipped to the forecast horizon):
  MODEL_ESTIMATE     modelled max depth on the road in the window >= WATCH_CM
  CONFIRMED_CLOSURE  a confirmed (OPERATOR / FIELD_TEAM / AUTHORITY) closure in force during the window (D-32)
  REPORTED           an unconfirmed PUBLIC closure report covering the window (never treated as confirmed)
Level: "advisory" if any modelled depth >= ADVISORY_CM or any confirmed closure; "watch" if anything else is affected;
None if nothing is.

A subscriber is eligible when: not erased, not opted out, verified, consent recorded for at least one of the alert's
channels, and either
  direct  an affected road lies within the subscriber's area radius (straight-line metres, EPSG:32643), or
  nearby  the road-network distance from the subscriber's snapped road node to an affected road's end node is
          <= NEARBY_NETWORK_M (undirected graph: a pedestrian or resident is not bound by one-way rules).
Every subscriber gets a decision with a reason, including why they were left out.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

from .subscribers import LOCALITY_BY_ID, PILOT_LOCALITIES

WATCH_CM = 15.0             # ASSUMED
ADVISORY_CM = 30.0          # ASSUMED
NEARBY_NETWORK_M = 600.0    # ASSUMED "nearby" buffer by road
SNAP_MAX_M = 300.0          # ASSUMED: a subscriber further than this from any pilot road node gets no "nearby" match
LOCALITY_MAX_M = 1000.0     # ASSUMED: an affected road is attributed to the nearest pilot locality within this distance
THRESHOLD_NOTE = ("ASSUMED thresholds, not calibrated for Mumbai: watch from 15 cm modelled depth, advisory from 30 cm. "
                  "Context only: Pregnolato et al. 2017 (Transp. Res. D 55:67-81) report about 30 cm as impassable for "
                  "2WD cars. The 600 m 'nearby by road' buffer is also ASSUMED.")
LOCALITY_IDS = frozenset(LOCALITY_BY_ID)
LEVEL_RANK = {None: 0, "watch": 1, "advisory": 2}

_ug_cache: dict[int, object] = {}
_loc_xy: Optional[list[tuple[str, float, float]]] = None


_ANY_VEHICLE = "__every_vehicle__"


def _roads(roads=None):
    if roads is not None:
        return roads
    from ..api import state
    return state.get_pilot()["roads"]


def _to_xy(lon: float, lat: float) -> tuple[float, float]:
    from ..api import state
    x, y = state.lonlat_to_xy(lon, lat)
    return float(x), float(y)


def _undirected(roads):
    key = id(roads)
    ug = _ug_cache.get(key)
    if ug is None:
        from ..routing.router import _cached_graph
        ug = _cached_graph(roads).to_undirected()
        _ug_cache.clear()
        _ug_cache[key] = ug
    return ug


def _locality_points() -> list[tuple[str, float, float]]:
    global _loc_xy
    if _loc_xy is None:
        _loc_xy = [(loc["id"], *_to_xy(*loc["lonlat"])) for loc in PILOT_LOCALITIES]
    return _loc_xy


def _nearest_locality(x: float, y: float) -> Optional[str]:
    best = min(((lid, (x - lx) ** 2 + (y - ly) ** 2) for lid, lx, ly in _locality_points()), key=lambda t: t[1])
    return best[0] if best[1] ** 0.5 <= LOCALITY_MAX_M else None


# ----------------------------------------------------------------------------- impact zone
def impact_zone(forecast, closures, now_t: float, window_min: float, *, roads=None) -> dict:
    """forecast: access.road_status.Forecast or None; closures: access.events.ClosureBook at decision time now_t."""
    roads = _roads(roads)
    by_id = {s.seg_id: s for s in roads.segments}
    now_t, window_min = float(now_t), float(window_min)
    if not window_min > 0:
        raise ValueError("window_min must be > 0")
    t0, t1 = now_t, now_t + window_min
    horizon = forecast.horizon_t_min if forecast is not None else None
    beyond = horizon is None or t0 > horizon
    found: dict[str, dict] = {}

    if not beyond:
        t1m = min(t1, horizon)
        for seg_id in forecast.streets_cm:
            if seg_id not in by_id:
                continue
            depth = forecast.depth_cm(seg_id, t0, t1m)
            if depth is not None and depth >= WATCH_CM:
                found[seg_id] = {"seg_id": seg_id, "basis": "MODEL_ESTIMATE", "max_depth_cm": round(depth, 1)}

    confirmed, reported = set(), set()
    for seg_id in sorted({e.seg_id for e in closures.used if e.kind == "closure"}):
        if seg_id not in by_id:
            continue
        # Only closures for EVERY vehicle are public road closures: a restriction for trucks (or a high-water report
        # from a car) does not mean the road is closed to residents (QA finding M6). The sentinel matches no type.
        c = closures.closed_during(seg_id, t0, t1, vehicle=_ANY_VEHICLE)
        if c is not None:
            confirmed.add(c.event_id)
            prev = found.get(seg_id, {})
            found[seg_id] = {"seg_id": seg_id, "basis": "CONFIRMED_CLOSURE", "closure_event_id": c.event_id,
                             "max_depth_cm": prev.get("max_depth_cm")}
            continue
        r = closures.unconfirmed_during(seg_id, t0, t1, vehicle=_ANY_VEHICLE)
        if r is not None:
            reported.add(r.event_id)
            if seg_id not in found:
                found[seg_id] = {"seg_id": seg_id, "basis": "REPORTED", "report_event_id": r.event_id,
                                 "max_depth_cm": None}

    segments, localities = [], set()
    for seg_id in sorted(found):
        seg = by_id[seg_id]
        mid = seg.xy[len(seg.xy) // 2]
        loc = _nearest_locality(float(mid[0]), float(mid[1]))
        if loc:
            localities.add(loc)
        segments.append({**found[seg_id], "name": seg.name or "unnamed road", "highway": seg.highway,
                         "length_m": round(float(seg.length_m), 1), "locality_id": loc})

    max_depth = max((s["max_depth_cm"] or 0.0 for s in segments), default=0.0)
    if not segments:
        level = None
    elif confirmed or max_depth >= ADVISORY_CM:
        level = "advisory"
    else:
        level = "watch"
    zone = {"level": level, "now_t_min": now_t, "window_min": window_min,
            "window": {"t0_min": t0, "t1_min": t1, "model_t1_min": None if beyond else min(t1, horizon),
                       "horizon_t_min": horizon, "beyond_horizon": beyond},
            "segments": segments, "closures": sorted(confirmed), "reported": sorted(reported),
            "localities": sorted(localities), "max_depth_cm": round(max_depth, 1) if segments else None,
            "thresholds": {"watch_cm": WATCH_CM, "advisory_cm": ADVISORY_CM, "note": THRESHOLD_NOTE}}
    zone["signature"] = signature(zone)
    return zone


def signature(zone: dict) -> str:
    """Material-change signature: level, affected localities, affected road set, confirmed closure ids."""
    key = {"level": zone["level"], "localities": sorted(zone["localities"]),
           "segments": sorted(s["seg_id"] for s in zone["segments"]), "closures": sorted(zone["closures"])}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]


# ----------------------------------------------------------------------------- eligibility
def _affected_geometry(zone: dict, roads):
    from shapely.geometry import LineString
    by_id = {s.seg_id: s for s in roads.segments}
    lines, nodes = [], set()
    for s in zone["segments"]:
        seg = by_id.get(s["seg_id"])
        if seg is None:
            continue
        lines.append((s, LineString(seg.xy)))
        nodes.update((int(seg.u), int(seg.v)))
    return lines, nodes


def decide(zone: dict, subscribers: Iterable[dict], *, channels: Iterable[str] = ("sms", "whatsapp"),
           roads=None) -> list[dict]:
    """One decision per subscriber record (subscribers.targeting_records): eligible, reason, channels, match."""
    from shapely.geometry import Point
    import networkx as nx
    roads = _roads(roads)
    channels = [c for c in channels]
    lines, nodes = _affected_geometry(zone, roads)
    network: Optional[dict] = None
    out = []
    for sub in subscribers:
        d = {"subscriber_id": sub["id"], "eligible": False, "reason": "", "channels": [], "match": None,
             "distance_m": None, "network_m": None, "simulated": bool(sub.get("simulated"))}
        out.append(d)
        consented = [c for c in channels if c in (sub.get("channels") or {})]
        if sub.get("status") == "ERASED":
            d["reason"] = "erased (no personal data held)"
            continue
        if sub.get("status") == "OPTED_OUT" or sub.get("opted_out_at"):
            d["reason"] = "opted out"
            continue
        if not sub.get("verified"):
            d["reason"] = "number not verified"
            continue
        if not consented:
            d["reason"] = f"no consent for the alert's channels ({', '.join(channels)})"
            continue
        if not lines:
            d["reason"] = "no affected roads in this alert"
            continue
        area = sub["area"]
        x, y = _to_xy(*area["lonlat"])
        p = Point(x, y)
        dist, nearest = min(((ln.distance(p), s) for s, ln in lines), key=lambda t: t[0])
        d["distance_m"] = round(dist, 1)
        radius = float(area["radius_m"])
        if dist <= radius:
            d.update(eligible=True, match="direct", channels=consented,
                     reason=f"affected road {nearest['name']} is inside the subscriber's area")
            continue
        if network is None:
            ug = _undirected(roads)
            network = nx.multi_source_dijkstra_path_length(ug, nodes, cutoff=NEARBY_NETWORK_M, weight="length_m")
        from ..routing.router import _snap
        node = _snap(roads, [tuple(area["lonlat"])])[0]
        nx_, ny_ = roads.node_xy[node]
        snap_m = ((float(nx_) - x) ** 2 + (float(ny_) - y) ** 2) ** 0.5
        net = network.get(node) if snap_m <= SNAP_MAX_M else None
        if net is not None and net <= NEARBY_NETWORK_M:
            d.update(eligible=True, match="nearby", channels=consented, network_m=round(net, 1),
                     reason=f"within the nearby buffer by road ({NEARBY_NETWORK_M:.0f} m, ASSUMED) of an affected road")
            continue
        why = "no affected road inside the area"
        why += (f"; more than {NEARBY_NETWORK_M:.0f} m by road" if snap_m <= SNAP_MAX_M
                else "; the area is off the pilot road network")
        d["reason"] = "outside area: " + why
    return out
