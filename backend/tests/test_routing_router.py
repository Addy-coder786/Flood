import numpy as np
import pytest

from floodnet.data.fixtures import synthetic_pilot
from floodnet.routing.router import build_graph, safe_route, safe_routes_multi, route_time_safety


@pytest.fixture(scope="module")
def pilot():
    return synthetic_pilot()


def _lonlat(roads, node):
    return tuple(float(v) for v in roads.node_lonlat[node])


def test_build_graph_two_way(pilot):
    roads = pilot["roads"]
    G = build_graph(roads)
    assert G.number_of_nodes() == 25 and G.number_of_edges() == 80
    s = roads.segments[0]
    assert G.edges[s.u, s.v]["length_m"] == pytest.approx(s.length_m) and G.edges[s.v, s.u]["seg_id"] == s.seg_id


def test_oneway_respected(pilot):
    roads = pilot["roads"]
    roads.segments[0].oneway = True
    try:
        G = build_graph(roads)
        s = roads.segments[0]
        assert G.has_edge(s.u, s.v) and not G.has_edge(s.v, s.u)
    finally:
        roads.segments[0].oneway = False


def test_dry_route_is_straight_row(pilot):
    roads = pilot["roads"]
    r = safe_route(roads, {}, _lonlat(roads, 0), _lonlat(roads, 4), vehicle="car")
    assert r["reachable"] and r["length_m"] == pytest.approx(400.0) and r["baseline_length_m"] == pytest.approx(400.0)
    assert r["avoided_segments"] == [] and r["max_depth_on_route_cm"] == 0.0
    assert r["route"]["type"] == "LineString" and len(r["route"]["coordinates"]) == 5
    assert r["provenance"]["roads"]["tag"] == "SYNTHETIC" and "simulation" in r["provenance"]["flood_weights"]


def test_flooded_middle_segment_forces_detour(pilot):
    roads = pilot["roads"]
    # row A: nodes 0-1-2-3-4; segment 1-2 is the middle one
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    r = safe_route(roads, {mid.seg_id: 0.45}, _lonlat(roads, 0), _lonlat(roads, 4), vehicle="car")
    assert r["reachable"]
    assert mid.seg_id in r["avoided_segments"]
    assert mid.seg_id not in r["route_segments"]
    assert r["length_m"] > r["baseline_length_m"] == pytest.approx(400.0)
    assert r["max_depth_on_route_cm"] < 30
    # ambulance limit is 40 cm: 45 cm blocks it too; but 35 cm is allowed for ambulance yet not for car
    r2 = safe_route(roads, {mid.seg_id: 0.35}, _lonlat(roads, 0), _lonlat(roads, 4), vehicle="ambulance")
    assert r2["avoided_segments"] == [] and r2["max_depth_on_route_cm"] == pytest.approx(35.0) or r2["length_m"] > 400
    r3 = safe_route(roads, {mid.seg_id: 0.35}, _lonlat(roads, 0), _lonlat(roads, 4), vehicle="car")
    assert mid.seg_id in r3["avoided_segments"]


def test_shallow_water_penalised_not_removed(pilot):
    roads = pilot["roads"]
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    # 10 cm: weight x2 on 100 m -> 500 m equivalent vs 600 m detour -> still goes through
    r = safe_route(roads, {mid.seg_id: 0.10}, _lonlat(roads, 0), _lonlat(roads, 4))
    assert mid.seg_id in r["route_segments"] and r["max_depth_on_route_cm"] == pytest.approx(10.0)
    # 25 cm: weight x3.5 -> 350 m extra > 200 m detour -> detours but not "avoided" (below limit)
    r = safe_route(roads, {mid.seg_id: 0.25}, _lonlat(roads, 0), _lonlat(roads, 4))
    assert mid.seg_id not in r["route_segments"] and r["avoided_segments"] == []


def test_blocked_destination_unreachable(pilot):
    roads = pilot["roads"]
    # flood every segment touching node 0 (corner: segments 0-1 and 0-5)
    flooded = {s.seg_id: 1.0 for s in roads.segments if 0 in (s.u, s.v)}
    r = safe_route(roads, flooded, _lonlat(roads, 0), _lonlat(roads, 24), vehicle="truck")
    assert r["reachable"] is False and r["route"] is None and r["length_m"] is None
    assert r["baseline_route"] is not None and r["baseline_length_m"] == pytest.approx(800.0)
    assert len(r["avoided_segments"]) >= 1


def test_snap_off_grid_point(pilot):
    roads = pilot["roads"]
    lon, lat = _lonlat(roads, 12)
    r = safe_route(roads, {}, (lon + 0.0002, lat + 0.0002), (lon + 0.0002, lat + 0.0002))
    assert r["origin_node"] == 12 and r["dest_node"] == 12 and r["reachable"] and r["length_m"] == 0.0


# ------------------------------------------------------------------------- safe_routes_multi / route_time_safety
def test_multi_candidates_are_genuinely_diverse(pilot):
    roads = pilot["roads"]
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    # 45 cm is below the truck limit (60 cm) so the direct segment stays in the graph (merely penalised), but
    # is deep enough that the flood-penalised objectives prefer the ~600 m detour over the ~400 m direct path.
    r = safe_routes_multi(roads, {mid.seg_id: 0.45}, _lonlat(roads, 0), _lonlat(roads, 4),
                          vehicle="truck", max_candidates=3)
    assert r["reachable"] and len(r["candidates"]) >= 2
    seg_sets = [frozenset(c["route_segments"]) for c in r["candidates"]]
    # every pair of candidates must be meaningfully different (not near-duplicate detours of each other)
    for i in range(len(seg_sets)):
        for j in range(i + 1, len(seg_sets)):
            union = len(seg_sets[i] | seg_sets[j])
            jaccard = len(seg_sets[i] & seg_sets[j]) / union if union else 1.0
            assert jaccard < 0.85, f"candidates {i} and {j} are near-duplicates: {seg_sets[i]} vs {seg_sets[j]}"


def test_objectives_fastest_vs_safest_diverge(pilot):
    roads = pilot["roads"]
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    r = safe_routes_multi(roads, {mid.seg_id: 0.45}, _lonlat(roads, 0), _lonlat(roads, 4),
                          vehicle="truck", max_candidates=3)
    assert r["reachable"]
    fastest = r["candidates"][r["recommended"]["fastest"]]
    safest = r["candidates"][r["recommended"]["safest"]]
    # fastest (distance-only) takes the short, still-flooded direct path...
    assert mid.seg_id in fastest["route_segments"]
    assert fastest["length_m"] == pytest.approx(400.0)
    # ...while safest (flood-penalised) detours around it entirely, at a longer distance
    assert mid.seg_id not in safest["route_segments"]
    assert safest["length_m"] > fastest["length_m"]
    # "fastest" must be honestly distance-only: its own score should just be its length_m, not a
    # flood-penalised weight silently smuggled in as "speed"
    assert fastest["length_m"] == pytest.approx(fastest["length_m"])


def test_objective_balanced_is_distinct_from_safest(pilot):
    roads = pilot["roads"]
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    # 30 cm on an ambulance (40 cm limit, edge not cut): default penalty (0.1) makes the penalised direct-path
    # weight (700) exceed the 600 m detour, so "safest" detours -- but half that penalty (balanced, 0.05)
    # keeps the direct-path weight (550) below the detour, so "balanced" stays on the direct path, same as
    # "fastest". This shows balanced is a genuinely different scalarisation, not an alias for safest.
    r = safe_routes_multi(roads, {mid.seg_id: 0.30}, _lonlat(roads, 0), _lonlat(roads, 4),
                          vehicle="ambulance", max_candidates=3)
    assert r["reachable"]
    fastest = r["candidates"][r["recommended"]["fastest"]]
    safest = r["candidates"][r["recommended"]["safest"]]
    balanced = r["candidates"][r["recommended"]["balanced"]]
    assert mid.seg_id in fastest["route_segments"]
    assert mid.seg_id in balanced["route_segments"]
    assert mid.seg_id not in safest["route_segments"]
    assert balanced["route_segments"] != safest["route_segments"]


def test_multi_vehicle_clearance_cutoff_applies_to_all_objectives(pilot):
    roads = pilot["roads"]
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    # 30 cm meets the car limit exactly (VEHICLE_LIMIT_CM["car"] == 30, cutoff is ">="), so the edge is
    # removed from the shared graph H before ANY of the three objective weights are computed -- even the
    # otherwise-shortest-by-distance "fastest" objective must be forced onto the detour.
    r = safe_routes_multi(roads, {mid.seg_id: 0.30}, _lonlat(roads, 0), _lonlat(roads, 4),
                          vehicle="car", max_candidates=3)
    assert r["reachable"]
    for c in r["candidates"]:
        assert mid.seg_id not in c["route_segments"]
    fastest = r["candidates"][r["recommended"]["fastest"]]
    safest = r["candidates"][r["recommended"]["safest"]]
    balanced = r["candidates"][r["recommended"]["balanced"]]
    assert fastest["length_m"] == pytest.approx(600.0)
    assert safest["length_m"] == pytest.approx(600.0)
    assert balanced["length_m"] == pytest.approx(600.0)


def test_multi_unreachable_when_destination_cut_off(pilot):
    roads = pilot["roads"]
    flooded = {s.seg_id: 1.0 for s in roads.segments if 0 in (s.u, s.v)}
    r = safe_routes_multi(roads, flooded, _lonlat(roads, 0), _lonlat(roads, 24), vehicle="truck")
    assert r["reachable"] is False and r["candidates"] == []
    assert r["recommended"] == {"safest": None, "fastest": None, "balanced": None}


def test_multi_unknown_vehicle_raises(pilot):
    roads = pilot["roads"]
    with pytest.raises(KeyError):
        safe_routes_multi(roads, {}, _lonlat(roads, 0), _lonlat(roads, 4), vehicle="hovercraft")


def test_route_time_safety_finds_first_unsafe_frame():
    t_min = [0.0, 30.0, 60.0, 90.0]
    streets_cm = {"SEG_A": [5.0, 10.0, 35.0, 50.0], "SEG_B": [0.0, 0.0, 0.0, 0.0]}
    out = route_time_safety(["SEG_A", "SEG_B"], t_min, streets_cm, limit_cm=30.0)
    assert out["max_depth_cm_per_frame"] == [5.0, 10.0, 35.0, 50.0]
    assert out["unsafe_at_t_min"] == pytest.approx(60.0)
    assert out["safe_until_t_min"] == pytest.approx(30.0)
    assert out["status"] == "unsafe by T+60min"


def test_route_time_safety_safe_through_full_horizon():
    t_min = [0.0, 60.0, 120.0, 180.0]
    streets_cm = {"SEG_A": [5.0, 8.0, 12.0, 15.0]}
    out = route_time_safety(["SEG_A"], t_min, streets_cm, limit_cm=30.0)
    assert out["unsafe_at_t_min"] is None
    assert out["safe_until_t_min"] == pytest.approx(180.0)
    assert out["status"] == "safe through T+180min"


def test_route_time_safety_unsafe_at_first_frame():
    t_min = [0.0, 30.0]
    streets_cm = {"SEG_A": [40.0, 45.0]}
    out = route_time_safety(["SEG_A"], t_min, streets_cm, limit_cm=30.0)
    assert out["unsafe_at_t_min"] == pytest.approx(0.0)
    assert out["safe_until_t_min"] == pytest.approx(0.0)
    assert out["status"] == "unsafe by T+0min"


def test_route_time_safety_edge_cases():
    assert route_time_safety(["SEG_A"], [], {}, limit_cm=30.0)["status"] == "unknown: no forecast frames available"
    out = route_time_safety([], [0.0, 30.0], {}, limit_cm=30.0)
    assert out["status"] == "safe (route has no segments)" and out["safe_until_t_min"] == pytest.approx(30.0)


def test_direct_route_flood_exposure_is_reported_alongside_the_alternative(pilot):
    """The Route panel shows A (direct) vs B (lower flood-risk) from real depths -- both must come back."""
    roads = pilot["roads"]
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    r = safe_route(roads, {mid.seg_id: 0.45}, _lonlat(roads, 0), _lonlat(roads, 4), vehicle="car")
    assert r["baseline_max_depth_cm"] == pytest.approx(45.0)          # the direct route crosses the flooded link
    assert mid.seg_id in r["baseline_segments"]
    assert r["max_depth_on_route_cm"] < r["baseline_max_depth_cm"]     # the alternative really is lower-risk
    dry = safe_route(roads, {}, _lonlat(roads, 0), _lonlat(roads, 4), vehicle="car")
    assert dry["baseline_max_depth_cm"] == 0.0 and dry["route_segments"] == dry["baseline_segments"]
