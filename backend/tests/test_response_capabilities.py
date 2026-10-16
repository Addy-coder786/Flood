"""Team capability matching (DECISION_MATRIX D-34; Agent D matrix section 3, C1-C6).

Written BEFORE the implementation (Phase 3, tests first). Interface per D-34: settlements carry
`required_capabilities: list[str]` (default [], fixtures set ["evacuation"]); hard constraint team.capabilities
superset of required; unmet reason "no team with capability 'water rescue'" when no team qualifies, otherwise the
existing categories with `detail` naming the qualifying teams; tie-break on equal done time and people:
fewest surplus capabilities -> team id -> vehicle id -> shelter id; names normalised lower/trim;
GET /api/response/resources exposes required_capabilities; explanation mentions capabilities when required.

Two fixture tiers: tiny_world() (a 4-node straight road with DISTINCT coordinates, 500 m residential segments both
ways = 2 min each; everything hand-checkable, exact ties constructible, runs in milliseconds) and the real pilot
network as in tests/test_response_planner.py.
"""
from __future__ import annotations

import copy

import numpy as np
import pytest
from fastapi.testclient import TestClient

from floodnet.access.events import ClosureBook
from floodnet.access.road_status import Forecast, SegmentContext
from floodnet.api import state
from floodnet.api.main import app
from floodnet.contracts import RoadGraph, RoadSegment
from floodnet.provenance import Provenance, Tag
from floodnet.response import fixtures as fx
from floodnet.response.ledger import parse_ledger, parse_response_events
from floodnet.response.planner import Router, plan
from floodnet.routing.router import build_graph
from tests.test_response_planner import run_plan

NO_CTX = SegmentContext(frozenset(), frozenset())
TINY_T = tuple(float(x) for x in range(0, 65, 5))
client = TestClient(app)


# ------------------------------------------------------------------ tiny world
def tiny_roads() -> RoadGraph:
    """Nodes 0-1-2-3 west to east along lat 19.0 N, 500 m apart (lon step 0.005 deg ~ 526 m; length_m is set to
    500 exactly). Two-way residential segments so every node is in one strongly connected component and
    routing.router._snap can snap to any of them."""
    from pyproj import Transformer
    from floodnet.config import CRS_COMPUTE, CRS_GEO
    lonlat = np.array([[72.8400 + 0.005 * i, 19.0] for i in range(4)])
    fwd = Transformer.from_crs(CRS_GEO, CRS_COMPUTE, always_xy=True)
    x, y = fwd.transform(lonlat[:, 0], lonlat[:, 1])
    node_xy = np.column_stack([x, y])
    segs = []
    for i in range(3):
        segs.append(RoadSegment(seg_id=f"e{i}", name=f"Road {i}", highway="residential", osm_way_id=0, u=i, v=i + 1,
                                length_m=500.0, oneway=False, xy=node_xy[i:i + 2].copy(), lonlat=lonlat[i:i + 2].copy()))
    return RoadGraph(node_xy=node_xy, node_lonlat=lonlat, segments=segs,
                     provenance=Provenance(tag=Tag.SYNTHETIC, source="hand-built capability test network"))


def tiny_world(*, required=None, t1_caps=("evacuation", "water rescue"), t2_caps=("evacuation",), people=60):
    """Settlement S1 at node 2, shelter SH1 at node 3, both teams and both trucks at node 0 (same base, so the
    pickup leg is skipped and the two (team, vehicle) pairs are exact ties). Mission = 0->2 (4 min) + load 10 +
    2->3 (2 min) + unload 5 -> done T+21 for either pair."""
    roads = tiny_roads()
    ll = roads.node_lonlat.tolist()
    res = {"tag": "SIMULATED tiny world", "priority_rule": fx.PRIORITY_RULE,
           "assumed_minutes": {"team_pickup": fx.TEAM_PICKUP_MIN, "load_at_settlement": fx.LOAD_MIN, "unload_at_shelter": fx.UNLOAD_MIN},
           "settlements": [{"id": "S1", "name": "Settlement one", "lonlat": ll[2], "people_needing_help": people,
                            "priority": "critical", "need": "evacuation to shelter",
                            **({"required_capabilities": list(required)} if required is not None else {})}],
           "shelters": [{"id": "SH1", "name": "Shelter one", "lonlat": ll[3], "total_capacity": 200, "occupancy": 0, "status": "open"}],
           "teams": [{"id": "T1", "name": "Team one", "lonlat": ll[0], "capabilities": list(t1_caps), "members": 6},
                     {"id": "T2", "name": "Team two", "lonlat": ll[0], "capabilities": list(t2_caps), "members": 4}],
           "vehicles": [{"id": "V1", "name": "Truck one", "type": "truck", "passenger_capacity": 25, "lonlat": ll[0]},
                        {"id": "V2", "name": "Truck two", "type": "truck", "passenger_capacity": 25, "lonlat": ll[0]}]}
    return roads, res


def tiny_plan(roads, res, forecast=None, now=0.0):
    forecast = forecast or Forecast(TINY_T, {})
    known = {k: {r["id"] for r in res[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    known.update({"vehicle_id": known["vehicles"], "shelter_id": known["shelters"], "settlement_id": known["settlements"]})
    rt = Router(roads, build_graph(roads), forecast, NO_CTX, ClosureBook([], now), "arrival")
    return plan(res, parse_response_events([], known), parse_ledger([], known), rt, now, 0)


def test_tiny_world_is_sound_and_hand_checkable():
    roads, res = tiny_world()
    out = tiny_plan(roads, res)
    props = [a for a in out["assignments"] if a["status"] == "PROPOSED"]
    assert out["invariant_violations"] == [] and props
    first = props[0]
    assert [l["kind"] for l in first["legs"]] == ["to_settlement", "to_shelter"]        # same base: no pickup leg
    assert first["legs"][0]["arrive_t_min"] == pytest.approx(4.0) and first["planned_done_t_min"] == pytest.approx(21.0)


# ------------------------------------------------------------------ C2 hard constraint
def test_c2_tiny_settlement_requiring_water_rescue_only_ever_gets_the_capable_team():
    roads, res = tiny_world(required=["water rescue"])
    out = tiny_plan(roads, res)
    props = [a for a in out["assignments"] if a["status"] == "PROPOSED"]
    assert props and {a["team_id"] for a in props} == {"T1"}
    assert out["summary"]["unmet"] == 0                           # T1 shuttles: 60 people in 3 trips by T+60
    assert out["invariant_violations"] == []


# ------------------------------------------------------------------ C3 unmet reason names the binding constraint
def test_c3_unmet_reason_names_the_missing_capability():
    roads, res = tiny_world(required=["boat"])
    out = tiny_plan(roads, res)
    assert "S1" in out["unmet"]
    assert out["unmet"]["S1"]["people"] == 60
    assert out["unmet"]["S1"]["reasons"][0]["reason"] == "no team with capability 'boat'"
    assert out["summary"]["covered_proposed"] == 0


def test_c3_when_the_only_capable_team_cannot_reach_the_detail_names_it():
    roads, res = tiny_world(required=["water rescue"])
    flooded = Forecast(TINY_T, {s.seg_id: [70.0] * len(TINY_T) for s in roads.segments})   # over the 60 cm truck limit
    out = tiny_plan(roads, res, forecast=flooded)
    r = out["unmet"]["S1"]["reasons"][0]
    assert r["reason"] == "no feasible route: forecast flooding"
    assert "T1" in r["detail"] and "T2" not in r["detail"]


# ------------------------------------------------------------------ C4 tie-break keeps the specialist free
def test_c4_tie_break_prefers_the_team_with_fewest_surplus_capabilities_then_ids():
    roads, res = tiny_world(required=["evacuation"])
    out = tiny_plan(roads, res)
    props = [a for a in out["assignments"] if a["status"] == "PROPOSED"]
    assert props[0]["team_id"] == "T2"                            # T1 would waste 'water rescue'
    assert props[0]["vehicle_id"] == "V1"                         # then lowest vehicle id
    # identical capabilities -> team id decides
    roads2, res2 = tiny_world(required=["evacuation"], t1_caps=("evacuation",), t2_caps=("evacuation",))
    out2 = tiny_plan(roads2, res2)
    assert [a for a in out2["assignments"] if a["status"] == "PROPOSED"][0]["team_id"] == "T1"


# ------------------------------------------------------------------ C1 normalisation and C6 explanation
def test_c1_capability_names_are_normalised_lower_and_trimmed():
    roads, res = tiny_world(required=["  Water Rescue "], t1_caps=("WATER RESCUE", "evacuation"), t2_caps=("evacuation",))
    out = tiny_plan(roads, res)
    s1 = next(s for s in out["state"]["settlements"] if s["id"] == "S1")
    assert s1["required_capabilities"] == ["water rescue"]
    t1 = next(t for t in out["state"]["teams"] if t["id"] == "T1")
    assert "water rescue" in t1["capabilities"]
    assert {a["team_id"] for a in out["assignments"] if a["status"] == "PROPOSED"} == {"T1"}


def test_c6_explanation_mentions_the_satisfied_capabilities_when_required():
    roads, res = tiny_world(required=["water rescue"])
    out = tiny_plan(roads, res)
    a = [x for x in out["assignments"] if x["status"] == "PROPOSED"][0]
    assert "water rescue" in a["explanation"]
    roads2, res2 = tiny_world()                                   # nothing required: no capability text
    a2 = [x for x in tiny_plan(roads2, res2)["assignments"] if x["status"] == "PROPOSED"][0]
    assert "capabilit" not in a2["explanation"]


# ------------------------------------------------------------------ real pilot: C1 endpoint, C2, C5
@pytest.fixture(scope="module")
def roads():
    return state.get_pilot()["roads"]


def test_c1_resources_endpoint_exposes_required_capabilities_for_every_settlement():
    r = client.get("/api/response/resources").json()
    for s in r["settlements"]:
        assert s["required_capabilities"] == ["evacuation"], s["id"]
    for t in r["teams"]:
        assert "evacuation" in t["capabilities"]


def test_c5_demo_plan_numbers_are_unchanged(roads):
    out = run_plan(roads)
    s = out["summary"]
    assert s["people_needing_help"] == 215 and s["covered_proposed"] + s["unmet"] == 215 and s["unmet"] == 5
    assert [r["reason"] for r in out["unmet"]["S4"]["reasons"]] == ["shelter capacity exhausted"]
    assert out["invariant_violations"] == []


def test_c2_real_pilot_every_settlement_requiring_water_rescue_excludes_the_police_team(roads):
    res = copy.deepcopy(fx.demo_resources())
    for s in res["settlements"]:
        s["required_capabilities"] = ["water rescue"]
    known = {k: {r["id"] for r in res[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    known.update({"vehicle_id": known["vehicles"], "shelter_id": known["shelters"], "settlement_id": known["settlements"]})
    from tests.test_response_planner import DRY, router
    out = plan(res, parse_response_events([], known), parse_ledger([], known), router(roads, DRY, (), 0.0), 0.0, 0)
    props = [a for a in out["assignments"] if a["status"] == "PROPOSED"]
    assert props and {a["team_id"] for a in props} == {"T1"}
    assert all(r["reason"] != "no team with capability 'water rescue'" for u in out["unmet"].values() for r in u["reasons"])
    assert out["invariant_violations"] == []
