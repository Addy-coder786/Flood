"""Flood-aware routing on the RoadGraph.

Edge weight = length_m * (1 + penalty_per_cm * depth_cm); edges whose depth >= VEHICLE_LIMIT_CM[vehicle] are removed.
A flood-agnostic `baseline` route is always computed for comparison.

safe_route() is the original single-route entry point (POST /api/route), unchanged. safe_routes_multi() and
route_time_safety() below are additive: multi-candidate (safest/fastest-by-distance/balanced) routing and
time-aware safety-through-T+X evaluation, for POST /api/route/alternatives.

ROUTING-CORE RESTRICTION (2026-09-14, P0 fix): `_snap()` used to pick the literal nearest node in the whole
RoadGraph, including ~24% of pilot nodes that are dangling one-edge stubs (driveways/service-road cul-de-sacs).
On the real MCGM/OSM pilot network this let an origin/destination snap onto a node whose only edge, combined
with real OSM `oneway` tagging, puts it in a small strongly-connected component (SCC) with no directed path
back into the main network -- proven empirically: `docs/validation/demo_check.json`'s routing_probe returned
`NetworkXNoPath` even for the *unweighted baseline* route (no flood logic involved). Root cause + evidence:
`docs/validation/routing_topology.json` (`backend/scripts/routing_topology_diagnostic.py`). Fix: `_snap()` now
only offers nodes in the graph's largest SCC (the "routable core") as candidates. Two nodes in the same SCC
are mutually reachable by definition, so this *guarantees* a baseline route exists between any two snapped
points -- without adding a single edge, inventing any road, or altering any OSM/MCGM geometry; it only changes
which existing node a given lon/lat binds to. This is the same practice OSRM/GraphHopper/Valhalla use for
exactly this reason. A destination can still become genuinely unreachable once flood weighting removes edges
(`reachable: False` is the correct, honest outcome then, not a bug).
"""
from __future__ import annotations

import numpy as np
import networkx as nx

from ..contracts import RoadGraph
from ..config import CRS_COMPUTE, CRS_GEO, VEHICLE_LIMIT_CM

DEFAULT_PENALTY_PER_CM = 0.1

_graph_cache: dict[int, nx.DiGraph] = {}
_tree_cache: dict[int, tuple] = {}   # id(roads) -> (pyproj Transformer, scipy cKDTree, core node-index array)


def clear_caches() -> None:
    """Call whenever a `roads` object is replaced (floodnet.api.state.set_pilot/reset_pilot) -- the caches
    below are keyed by id(roads), safe only while the cached object stays referenced elsewhere (normally
    _pilot["roads"] for the process lifetime); otherwise a freed id() could be reused by an unrelated object."""
    _graph_cache.clear()
    _tree_cache.clear()


def build_graph(roads: RoadGraph) -> nx.DiGraph:
    """Directed graph; two-way segments get both directions. Edge attrs: length_m, seg_id, reversed."""
    G = nx.DiGraph()
    for k, (x, y) in enumerate(np.asarray(roads.node_xy)):
        G.add_node(k, x=float(x), y=float(y))
    for seg in roads.segments:
        G.add_edge(seg.u, seg.v, length_m=float(seg.length_m), seg_id=seg.seg_id, reversed=False)
        if not seg.oneway:
            G.add_edge(seg.v, seg.u, length_m=float(seg.length_m), seg_id=seg.seg_id, reversed=True)
    return G


def _cached_graph(roads: RoadGraph) -> nx.DiGraph:
    """build_graph() itself stays a plain, uncached function (tests call it directly expecting a fresh
    graph); this wrapper is what safe_route() actually uses, since `roads` is the same immutable pilot
    object on every /api/route call and rebuilding the whole DiGraph each time was pure repeated work."""
    key = id(roads)
    G = _graph_cache.get(key)
    if G is None:
        G = build_graph(roads)
        _graph_cache[key] = G
    return G


def _routable_core(roads: RoadGraph) -> np.ndarray:
    """Node indices in the directed graph's largest strongly connected component -- the subset of nodes
    guaranteed mutually reachable from one another. See the routing-core-restriction note at the top of this
    module for why snapping is restricted to this set rather than to the literal nearest node."""
    G = _cached_graph(roads)
    comps = list(nx.strongly_connected_components(G))
    if not comps:
        return np.arange(len(roads.node_xy))
    return np.array(sorted(max(comps, key=len)), dtype=int)


def _cached_tree(roads: RoadGraph):
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    key = id(roads)
    t = _tree_cache.get(key)
    if t is None:
        core = _routable_core(roads)
        fwd = Transformer.from_crs(CRS_GEO, CRS_COMPUTE, always_xy=True)
        tree = cKDTree(np.asarray(roads.node_xy, dtype=float)[core])
        t = (fwd, tree, core)
        _tree_cache[key] = t
    return t


def _snap(roads: RoadGraph, lonlat_points: list[tuple[float, float]]) -> list[int]:
    """Snaps each lonlat point to the nearest node in the routable core (see _routable_core) -- never to a
    node outside it, even if a literally-closer node exists just outside the core (e.g. a driveway stub)."""
    fwd, tree, core = _cached_tree(roads)
    pts = np.asarray(lonlat_points, dtype=float)
    x, y = fwd.transform(pts[:, 0], pts[:, 1])
    _, idx = tree.query(np.column_stack([x, y]))
    return [int(core[i]) for i in np.atleast_1d(idx)]


def _path_geometry(roads: RoadGraph, G: nx.DiGraph, path: list[int]) -> tuple[dict, float, list[str]]:
    seg_by_id = {s.seg_id: s for s in roads.segments}
    coords: list[list[float]] = []
    length = 0.0
    seg_ids: list[str] = []
    for a, b in zip(path[:-1], path[1:]):
        e = G.edges[a, b]
        seg = seg_by_id[e["seg_id"]]
        ll = np.asarray(seg.lonlat, dtype=float)
        if e["reversed"]:
            ll = ll[::-1]
        pts = ll.round(7).tolist()
        if coords and coords[-1] == pts[0]:
            pts = pts[1:]
        coords.extend(pts)
        length += float(seg.length_m)
        seg_ids.append(seg.seg_id)
    if len(path) == 1:
        p = np.asarray(roads.node_lonlat[path[0]], dtype=float).round(7).tolist()
        coords = [p, p]
    return {"type": "LineString", "coordinates": coords}, length, seg_ids


def safe_route(roads: RoadGraph, street_depth_m: dict[str, float], origin_lonlat, dest_lonlat,
               vehicle: str = "car", penalty_per_cm: float | None = None) -> dict:
    if vehicle not in VEHICLE_LIMIT_CM:
        raise KeyError(f"unknown vehicle '{vehicle}'; known: {sorted(VEHICLE_LIMIT_CM)}")
    limit = VEHICLE_LIMIT_CM[vehicle]
    penalty = DEFAULT_PENALTY_PER_CM if penalty_per_cm is None else float(penalty_per_cm)
    G = _cached_graph(roads)
    o, d = _snap(roads, [tuple(origin_lonlat), tuple(dest_lonlat)])

    # baseline: shortest by length, flood ignored
    try:
        base_path = nx.shortest_path(G, o, d, weight="length_m")
    except nx.NetworkXNoPath:
        base_path = None
    base_geom, base_len, base_segs = (_path_geometry(roads, G, base_path) if base_path else (None, None, []))

    # flood-aware graph
    H = nx.DiGraph(); H.add_nodes_from(G.nodes(data=True))
    depth_cm_of: dict[str, float] = {}
    for a, b, e in G.edges(data=True):
        d_cm = float(street_depth_m.get(e["seg_id"], 0.0)) * 100.0
        depth_cm_of[e["seg_id"]] = d_cm
        if d_cm >= limit:
            continue
        H.add_edge(a, b, weight=e["length_m"] * (1.0 + penalty * d_cm), **e)
    try:
        path = nx.shortest_path(H, o, d, weight="weight")
        reachable = True
    except nx.NetworkXNoPath:
        path, reachable = None, False

    if path:
        geom, length, segs = _path_geometry(roads, H, path)
        max_depth = max([depth_cm_of.get(s, 0.0) for s in segs], default=0.0)
    else:
        geom, length, segs, max_depth = None, None, [], None

    avoided = [s for s in base_segs if depth_cm_of.get(s, 0.0) >= limit]
    base_max = max([depth_cm_of.get(s, 0.0) for s in base_segs], default=0.0) if base_path else None
    return {"route": geom, "length_m": length, "max_depth_on_route_cm": max_depth, "route_segments": segs,
            "avoided_segments": avoided, "baseline_route": base_geom, "baseline_length_m": base_len,
            "baseline_max_depth_cm": base_max, "baseline_segments": base_segs,
            "reachable": reachable, "vehicle": vehicle, "vehicle_limit_cm": limit,
            "origin_node": o, "dest_node": d,
            "provenance": {"roads": roads.provenance.to_dict(), "flood_weights": "derived from simulation frame"}}


# ------------------------------------------------------------------------- multi-candidate routing
# Everything below is ADDITIVE: safe_route() above is untouched and stays the single-route contract used by
# POST /api/route. safe_routes_multi() is a separate entry point (POST /api/route/alternatives) that returns
# several genuinely different candidate paths scored under three objectives built only from data the graph
# already carries -- no invented speed/traffic model:
#   - "safest":   minimises the existing flood-depth-penalised weight (same formula as safe_route's `route`)
#   - "fastest":  minimises length_m alone. This is a DISTANCE-only objective -- there is no travel-time or
#                 speed attribute anywhere in the graph, so this must never be relabelled "fastest by time"
#                 anywhere it surfaces (API field names below, frontend labels).
#   - "balanced": the same flood-depth-penalised formula as "safest" but with half the penalty-per-cm, i.e. a
#                 scalarisation that weighs distance more heavily relative to flood exposure than "safest"
#                 does, while still being pulled away from deep water more than pure "fastest" is.
# The hard vehicle-clearance cutoff (edges >= VEHICLE_LIMIT_CM[vehicle] deep) is applied once, to the shared
# graph H, before any of the three objective weights are computed -- so it applies identically to all three.
def _edge_seg_ids(H: nx.DiGraph, path: list[int]) -> frozenset[str]:
    return frozenset(H.edges[a, b]["seg_id"] for a, b in zip(path[:-1], path[1:]))


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return (len(a & b) / union) if union else 1.0


def _add_or_merge_candidate(candidates: list[dict], seg_sets: list[frozenset], path: list[int],
                             segset: frozenset, label: str | None, dup_jaccard_threshold: float) -> int:
    """Adds `path` as a new candidate, unless an existing candidate's segment set is already this similar
    (Jaccard >= dup_jaccard_threshold) to it -- in which case `path` is treated as a near-duplicate detour of
    that existing candidate and only its objective label (if any) is merged in, so the returned candidate
    pool stays genuinely diverse rather than padded with near-identical routes."""
    for i, existing in enumerate(seg_sets):
        if _jaccard(existing, segset) >= dup_jaccard_threshold:
            if label and label not in candidates[i]["objectives"]:
                candidates[i]["objectives"].append(label)
            return i
    candidates.append({"path": path, "objectives": [label] if label else []})
    seg_sets.append(segset)
    return len(candidates) - 1


def safe_routes_multi(roads: RoadGraph, street_depth_m: dict[str, float], origin_lonlat, dest_lonlat,
                      vehicle: str = "car", penalty_per_cm: float | None = None,
                      balanced_penalty_per_cm: float | None = None, max_candidates: int = 3,
                      dup_jaccard_threshold: float = 0.85) -> dict:
    """Like safe_route(), but returns up to `max_candidates` genuinely diverse routes, each scored under the
    safest / fastest(-by-distance) / balanced objectives described above. Does not call or modify safe_route().
    """
    if vehicle not in VEHICLE_LIMIT_CM:
        raise KeyError(f"unknown vehicle '{vehicle}'; known: {sorted(VEHICLE_LIMIT_CM)}")
    if max_candidates < 1:
        raise ValueError("max_candidates must be >= 1")
    limit = VEHICLE_LIMIT_CM[vehicle]
    penalty_safest = DEFAULT_PENALTY_PER_CM if penalty_per_cm is None else float(penalty_per_cm)
    penalty_balanced = (penalty_safest / 2.0) if balanced_penalty_per_cm is None else float(balanced_penalty_per_cm)

    G = _cached_graph(roads)
    o, d = _snap(roads, [tuple(origin_lonlat), tuple(dest_lonlat)])

    # One shared flood-aware graph, cutoff applied once; each edge carries all three objective weights so the
    # cutoff (which edges even exist in H) is identical no matter which weight key a shortest-path call uses.
    H = nx.DiGraph(); H.add_nodes_from(G.nodes(data=True))
    depth_cm_of: dict[str, float] = {}
    for a, b, e in G.edges(data=True):
        d_cm = float(street_depth_m.get(e["seg_id"], 0.0)) * 100.0
        depth_cm_of[e["seg_id"]] = d_cm
        if d_cm >= limit:
            continue
        H.add_edge(a, b, seg_id=e["seg_id"], reversed=e["reversed"], length_m=e["length_m"],
                   weight_safest=e["length_m"] * (1.0 + penalty_safest * d_cm),
                   weight_balanced=e["length_m"] * (1.0 + penalty_balanced * d_cm))

    objective_weight_key = {"safest": "weight_safest", "fastest": "length_m", "balanced": "weight_balanced"}
    candidates: list[dict] = []
    seg_sets: list[frozenset] = []

    # Seed the pool with each objective's true optimum first, so "recommended" (computed below) is always
    # exact for a reachable objective, never just "best of whatever the diversity search happened to find".
    for label, key in objective_weight_key.items():
        try:
            path = nx.shortest_path(H, o, d, weight=key)
        except nx.NetworkXNoPath:
            continue
        _add_or_merge_candidate(candidates, seg_sets, path, _edge_seg_ids(H, path), label, dup_jaccard_threshold)

    # Top up with additional loopless, increasing-weight alternates (Yen's algorithm via networkx) until we
    # have max_candidates genuinely diverse routes, bounding generator effort defensively.
    if candidates and len(candidates) < max_candidates:
        try:
            gen = nx.shortest_simple_paths(H, o, d, weight="weight_safest")
            for i, path in enumerate(gen):
                if i >= 15 or len(candidates) >= max_candidates:
                    break
                _add_or_merge_candidate(candidates, seg_sets, path, _edge_seg_ids(H, path), None, dup_jaccard_threshold)
        except nx.NetworkXNoPath:
            pass

    out_candidates = []
    for c in candidates:
        path = c["path"]
        geom, length, segs = _path_geometry(roads, H, path)
        w_safest = sum(H.edges[a, b]["weight_safest"] for a, b in zip(path[:-1], path[1:]))
        w_balanced = sum(H.edges[a, b]["weight_balanced"] for a, b in zip(path[:-1], path[1:]))
        max_depth = max([depth_cm_of.get(s, 0.0) for s in segs], default=0.0)
        out_candidates.append({
            "route": geom, "length_m": length, "route_segments": segs, "max_depth_on_route_cm": max_depth,
            "weight_safest": w_safest, "weight_balanced": w_balanced, "objectives": list(c["objectives"]),
        })

    recommended: dict[str, int | None] = {"safest": None, "fastest": None, "balanced": None}
    if out_candidates:
        recommended["safest"] = min(range(len(out_candidates)), key=lambda i: out_candidates[i]["weight_safest"])
        recommended["fastest"] = min(range(len(out_candidates)), key=lambda i: out_candidates[i]["length_m"])
        recommended["balanced"] = min(range(len(out_candidates)), key=lambda i: out_candidates[i]["weight_balanced"])

    return {"candidates": out_candidates, "recommended": recommended, "reachable": bool(out_candidates),
            "vehicle": vehicle, "vehicle_limit_cm": limit, "origin_node": o, "dest_node": d,
            "penalty_per_cm": {"safest": penalty_safest, "balanced": penalty_balanced},
            "objective_definitions": {
                "safest": "minimises flood-depth-penalised weight (length_m x (1 + penalty_per_cm x depth_cm))",
                "fastest": "minimises length_m alone -- distance-based; no travel-time/speed model exists",
                "balanced": "flood-depth-penalised weight using half of the 'safest' penalty_per_cm",
            },
            "provenance": {"roads": roads.provenance.to_dict(), "flood_weights": "derived from simulation frame"}}


# ------------------------------------------------------------------------- time-aware route safety
def route_time_safety(route_segments: list[str], t_min: list[float], streets_cm: dict[str, list[float]],
                      limit_cm: float) -> dict:
    """Genuinely new capability, kept separate from safe_route()/safe_routes_multi() (which only ever look at
    one frame's depth): given a candidate route's segment ids and a run's full per-segment depth time series
    (exactly the shape GET /api/simulation/{run_id}/series already returns as `t_min` + `streets`), determine
    whether the route stays under the vehicle's clearance limit at every forecast frame.

    Returns "safe through T+<last frame>min" if the route never reaches limit_cm across the supplied frames,
    otherwise the first T+X minute mark at which some segment on the route reaches/exceeds limit_cm.
    """
    n = len(t_min)
    if n == 0:
        return {"status": "unknown: no forecast frames available", "safe_until_t_min": None,
                "unsafe_at_t_min": None, "max_depth_cm_per_frame": [], "vehicle_limit_cm": limit_cm}
    if not route_segments:
        return {"status": "safe (route has no segments)", "safe_until_t_min": t_min[-1],
                "unsafe_at_t_min": None, "max_depth_cm_per_frame": [0.0] * n, "vehicle_limit_cm": limit_cm}

    max_depth_per_frame = []
    for k in range(n):
        depths_k = [streets_cm[sid][k] for sid in route_segments if sid in streets_cm and k < len(streets_cm[sid])]
        max_depth_per_frame.append(max(depths_k) if depths_k else 0.0)

    unsafe_idx = next((k for k in range(n) if max_depth_per_frame[k] >= limit_cm), None)
    if unsafe_idx is None:
        horizon = t_min[-1]
        return {"status": f"safe through T+{horizon:.0f}min", "safe_until_t_min": horizon,
                "unsafe_at_t_min": None, "max_depth_cm_per_frame": max_depth_per_frame, "vehicle_limit_cm": limit_cm}
    unsafe_t = t_min[unsafe_idx]
    safe_until = t_min[unsafe_idx - 1] if unsafe_idx > 0 else 0.0
    return {"status": f"unsafe by T+{unsafe_t:.0f}min", "safe_until_t_min": safe_until,
            "unsafe_at_t_min": unsafe_t, "max_depth_cm_per_frame": max_depth_per_frame, "vehicle_limit_cm": limit_cm}
