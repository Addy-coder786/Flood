"""Response planner (floodnet/response/): allocation by priority, no double-booking, no shelter overflow, unmet
needs with reasons, replanning that keeps history, and the API. Runs on the real pilot road network with a dry
forecast (every road USABLE) unless a test floods roads on purpose."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from floodnet.access.events import ClosureBook, parse_events
from floodnet.access.road_status import Forecast, SegmentContext
from floodnet.api import state
from floodnet.api.main import app
from floodnet.response import fixtures as fx
from floodnet.response.ledger import check_invariants, parse_ledger, parse_response_events
from floodnet.response.planner import Router, build_state, plan
from floodnet.routing.router import _cached_graph

T = tuple(float(x) for x in range(0, 185, 5))
DRY = Forecast(T, {})
NO_CTX = SegmentContext(frozenset(), frozenset())
client = TestClient(app)


@pytest.fixture(scope="module")
def roads():
    return state.get_pilot()["roads"]


def router(roads, forecast=DRY, road_events=(), now=0.0, mode="arrival"):
    return Router(roads, _cached_graph(roads), forecast, NO_CTX, ClosureBook(parse_events(list(road_events)), now), mode)


def run_plan(roads, now=0.0, ledger=(), response_events=(), road_events=(), forecast=DRY, version=0):
    res = fx.demo_resources()
    known = {k: {r["id"] for r in res[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    known.update({"vehicle_id": known["vehicles"], "shelter_id": known["shelters"], "settlement_id": known["settlements"]})
    return plan(res, parse_response_events(list(response_events), known), parse_ledger(list(ledger), known),
                router(roads, forecast, road_events, now), now, version)


def test_first_plan_serves_by_priority_and_is_sound(roads):
    out = run_plan(roads)
    assert out["invariant_violations"] == []
    props = [a for a in out["assignments"] if a["status"] == "PROPOSED"]
    assert props and props[0]["settlement_id"] == "S1"                          # critical first
    order = [a["settlement_id"] for a in props]
    assert order == sorted(order, key=lambda s: fx.PRIORITY_ORDER[next(x for x in fx.SETTLEMENTS if x["id"] == s)["priority"]])
    s = out["summary"]
    assert s["people_needing_help"] == 215 and s["covered_proposed"] + s["unmet"] == 215
    # 230 places minus 20 occupied = 210 free: 5 people cannot have a shelter place
    assert s["unmet"] == 5 and [r["reason"] for r in out["unmet"]["S4"]["reasons"]] == ["shelter capacity exhausted"]
    assert all(a["people"] <= 25 for a in props)                                 # vehicle capacity respected
    assert all(leg["arrive_t_min"] >= leg["depart_t_min"] for a in props for leg in a["legs"])


def test_no_double_booking_and_no_overflow_are_checked(roads):
    out = run_plan(roads)
    a = copy.deepcopy([x for x in out["assignments"] if x["status"] == "PROPOSED"][:2])
    a[1]["team_id"] = a[0]["team_id"]; a[1]["vehicle_id"] = a[0]["vehicle_id"]
    a[1]["legs"][0]["depart_t_min"] = a[0]["legs"][0]["depart_t_min"]          # same pair, same time
    shelters = {sh["id"]: dict(sh) for sh in fx.SHELTERS}
    problems = check_invariants(a, shelters)
    assert any("double-booked" in p for p in problems)
    big = copy.deepcopy(a[:1]); big[0]["people"] = 500
    assert any("over capacity" in p for p in check_invariants(big, shelters))


def test_vehicle_unavailable_removes_it_and_explains(roads):
    ev = [{"kind": "vehicle_unavailable", "vehicle_id": "V1", "received_t_min": 0, "reason": "engine flooded"},
          {"kind": "vehicle_unavailable", "vehicle_id": "V2", "received_t_min": 0}]
    out = run_plan(roads, response_events=ev)
    assert out["summary"]["covered_proposed"] == 0
    assert all([r["reason"] for r in u["reasons"]] == ["no available vehicle"] for u in out["unmet"].values())
    out1 = run_plan(roads, response_events=ev[:1])
    assert {a["vehicle_id"] for a in out1["assignments"]} == {"V2"}


def test_future_events_are_not_used(roads):
    ev = [{"kind": "vehicle_unavailable", "vehicle_id": "V1", "received_t_min": 30}]
    st = build_state(fx.demo_resources(), parse_response_events(ev, {"vehicle_id": {"V1"}}), now=10)
    assert st["vehicles"]["V1"]["available"] and len(st["events_pending"]) == 1


def test_shelter_full_event_moves_people_to_unmet(roads):
    ev = [{"kind": "shelter_update", "shelter_id": "SH2", "received_t_min": 0, "occupancy": 150}]
    out = run_plan(roads, response_events=ev)
    assert out["summary"]["covered_proposed"] == 60 and out["summary"]["unmet"] == 155
    assert out["unmet"]["S2"]["reasons"][0]["reason"] == "shelter capacity exhausted"


def test_replan_keeps_completed_and_cancels_blocked_underway(roads):
    first = run_plan(roads)
    a1, a2 = [a for a in first["assignments"] if a["status"] == "PROPOSED"][:2]
    done = {**a1, "status": "COMPLETED", "history": a1["history"] + [
        {"status": "ACCEPTED", "t_min": 1}, {"status": "DISPATCHED", "t_min": 2}, {"status": "IN_PROGRESS", "t_min": 5},
        {"status": "COMPLETED", "t_min": a1["planned_done_t_min"]}]}
    underway = {**a2, "status": "DISPATCHED", "history": a2["history"] + [{"status": "ACCEPTED", "t_min": 1}, {"status": "DISPATCHED", "t_min": 2}]}
    # from `now` on every road is 70 cm deep (over the 60 cm truck limit): no route is left anywhere
    now = underway["legs"][0]["depart_t_min"] + 0.5
    k = sum(1 for t in T if t <= now)
    flooded = Forecast(T, {seg.seg_id: [0.0] * k + [70.0] * (len(T) - k) for seg in roads.segments})
    out = run_plan(roads, now=now, ledger=[done, underway], forecast=flooded, version=1)
    by_id = {a["assignment_id"]: a for a in out["assignments"]}
    assert by_id[done["assignment_id"]]["status"] == "COMPLETED"              # history preserved
    assert out["summary"]["served"] == a1["people"]
    assert out["plan_version"] == 2
    u = by_id[underway["assignment_id"]]
    assert u["status"] == "CANCELLED"
    assert u["history"][-1]["by"] == "planner" and "no route" in u["history"][-1]["reason"]
    assert any(c["change"] == "cancelled" for c in out["changes"])
    assert out["summary"]["covered_proposed"] == 0 and out["summary"]["unmet"] == 215 - a1["people"]
    assert out["invariant_violations"] == []


def test_underway_mission_is_redirected_when_its_shelter_fills(roads):
    first = run_plan(roads)
    a1 = [a for a in first["assignments"] if a["status"] == "PROPOSED"][0]
    assert a1["shelter_id"] == "SH1"
    underway = {**a1, "status": "DISPATCHED", "history": a1["history"] + [{"status": "ACCEPTED", "t_min": 1}, {"status": "DISPATCHED", "t_min": 2}]}
    ev = [{"kind": "shelter_update", "shelter_id": "SH1", "received_t_min": 3, "occupancy": 80}]
    out = run_plan(roads, now=3, ledger=[underway], response_events=ev, version=1)
    u = next(a for a in out["assignments"] if a["assignment_id"] == a1["assignment_id"])
    assert u["status"] == "DISPATCHED" and u["shelter_id"] == "SH2"
    assert any(c["change"] == "redirected" for c in out["changes"])
    assert out["invariant_violations"] == []
    # every shelter full: the underway mission is kept but flagged for the operator, never silently dropped
    ev2 = ev + [{"kind": "shelter_update", "shelter_id": "SH2", "received_t_min": 3, "occupancy": 150}]
    out2 = run_plan(roads, now=3, ledger=[underway], response_events=ev2, version=1)
    u2 = next(a for a in out2["assignments"] if a["assignment_id"] == a1["assignment_id"])
    assert u2["status"] == "DISPATCHED" and "operator decision" in u2["attention"]
    assert out2["invariant_violations"] == []


def test_overdue_underway_mission_is_flagged(roads):
    first = run_plan(roads)
    a1 = [a for a in first["assignments"] if a["status"] == "PROPOSED"][0]
    underway = {**a1, "status": "IN_PROGRESS", "history": a1["history"] + [{"status": "ACCEPTED", "t_min": 1}, {"status": "DISPATCHED", "t_min": 2}, {"status": "IN_PROGRESS", "t_min": 4}]}
    out = run_plan(roads, now=a1["planned_done_t_min"] + 10, ledger=[underway], version=1)
    u = next(a for a in out["assignments"] if a["assignment_id"] == a1["assignment_id"])
    assert u["status"] == "IN_PROGRESS" and "confirm completion" in u["attention"]


def test_later_commitment_is_pushed_when_an_earlier_mission_gets_longer(roads):
    """A1 (underway) and A3 (accepted, same pair, planned to leave when A1 was due back): a closure that makes
    A1 longer must push A3's departure, never let the pair be booked twice."""
    first = run_plan(roads)
    props = [a for a in first["assignments"] if a["status"] == "PROPOSED"]
    a1 = props[0]
    a3 = next(a for a in props[1:] if a["team_id"] == a1["team_id"] and a["vehicle_id"] == a1["vehicle_id"])
    underway = {**a1, "status": "DISPATCHED", "history": a1["history"] + [{"status": "ACCEPTED", "t_min": 1}, {"status": "DISPATCHED", "t_min": 2}]}
    accepted = {**a3, "status": "ACCEPTED", "history": a3["history"] + [{"status": "ACCEPTED", "t_min": 1}]}
    now = 5.0
    seg = next(s for l in underway["legs"] for s in l["segments"] if s["enter_t_min"] > now)
    out = run_plan(roads, now=now, ledger=[underway, accepted], road_events=[{"seg_id": seg["seg_id"], "kind": "closure", "received_t_min": now}], version=1)
    by_id = {a["assignment_id"]: a for a in out["assignments"]}
    u, c = by_id[a1["assignment_id"]], by_id[a3["assignment_id"]]
    assert u["status"] == "DISPATCHED" and c["status"] == "ACCEPTED"
    assert c["legs"][0]["depart_t_min"] >= u["legs"][-1]["done_t_min"] - 1e-6
    assert out["invariant_violations"] == []


def test_unchanged_proposals_keep_their_ids(roads):
    first = run_plan(roads)
    second = run_plan(roads, now=0, ledger=first["assignments"], version=1)
    assert [a["assignment_id"] for a in second["assignments"]] == [a["assignment_id"] for a in first["assignments"]]
    assert second["changes"] == []


def test_accepted_commitment_is_kept_when_feasible(roads):
    first = run_plan(roads)
    a1 = [a for a in first["assignments"] if a["status"] == "PROPOSED"][0]
    acc = {**a1, "status": "ACCEPTED", "history": a1["history"] + [{"status": "ACCEPTED", "t_min": 1, "reason": "operator"}]}
    out = run_plan(roads, now=1, ledger=[acc], version=1)
    kept = next(a for a in out["assignments"] if a["assignment_id"] == a1["assignment_id"])
    assert kept["status"] == "ACCEPTED" and kept["people"] == a1["people"]
    assert out["summary"]["covered_committed"] == a1["people"]


def test_ledger_validation():
    known = {k: {r["id"] for r in fx.demo_resources()[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    base = {"assignment_id": "A1", "settlement_id": "S1", "team_id": "T1", "vehicle_id": "V1", "shelter_id": "SH1", "people": 5}
    with pytest.raises(ValueError):
        parse_ledger([{**base, "status": "COMPLETED", "legs": [{}], "history": [{"status": "PROPOSED"}, {"status": "COMPLETED"}]}], known)
    with pytest.raises(ValueError):
        parse_ledger([{**base, "status": "ACCEPTED"}], known)                    # no legs
    with pytest.raises(ValueError):
        parse_ledger([{**base, "team_id": "T9", "status": "PROPOSED"}], known)


def test_api_resources_and_plan():
    r = client.get("/api/response/resources").json()
    assert r["tag"].startswith("SIMULATED") and len(r["settlements"]) == 4
    assert all(s["location_provenance"]["tag"] == "REAL" for s in r["settlements"] + r["shelters"] + r["teams"])
    assert client.post("/api/response/plan", json={}).status_code == 422
    body = {"now_t_min": 0, "series_t_min": list(T), "streets_cm": {}}
    j = client.post("/api/response/plan", json=body).json()
    assert j["plan_version"] == 1 and j["invariant_violations"] == [] and "simulated" in j["disclaimer"]
    # the same ledger sent back unchanged: proposals are stable, nothing is reported as a change
    j2 = client.post("/api/response/plan", json={**body, "ledger": j["assignments"], "plan_version": 1}).json()
    assert j2["plan_version"] == 2 and [c for c in j2["changes"] if c["change"] != "proposed"] == []
    assert len([c for c in j2["changes"] if c["change"] == "proposed"]) == 0
    bad = client.post("/api/response/plan", json={**body, "response_events": [{"kind": "vehicle_unavailable", "vehicle_id": "V7", "received_t_min": 0}]})
    assert bad.status_code == 422
