"""Planner policy for missions with people on board, the ledger-carried plan version guard, and the
resources-override bug in _redirect_shelter (DECISION_MATRIX D-33; Agent C T14, T15, T16).

Written BEFORE the implementation (Phase 3, tests first). Runs the real planner on the pilot network as
tests/test_response_planner.py does.

T15 (DISPATCHED + no route -> cancelled by the planner) is deliberately NOT re-tested here: it stays pinned by
tests/test_response_planner.py::test_replan_keeps_completed_and_cancels_blocked_underway, which must keep passing
unchanged -- the planner cancels only while nobody is on board.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from floodnet.access.road_status import Forecast
from floodnet.api import state
from floodnet.api.main import app
from floodnet.response import fixtures as fx
from floodnet.response.ledger import parse_ledger, parse_response_events
from floodnet.response.planner import plan
from tests.test_response_planner import DRY, T, router, run_plan

client = TestClient(app)


@pytest.fixture(scope="module")
def roads():
    return state.get_pilot()["roads"]


def _proposed(out):
    return [a for a in out["assignments"] if a["status"] == "PROPOSED"]


def _flooded_from(roads, now, depth=70.0):
    k = sum(1 for t in T if t <= now)
    return Forecast(T, {seg.seg_id: [0.0] * k + [depth] * (len(T) - k) for seg in roads.segments})


# ------------------------------------------------------------------ T14
def test_t14_in_progress_mission_with_no_route_is_kept_and_flagged_not_cancelled(roads):
    first = run_plan(roads)
    a2 = _proposed(first)[1]
    at_site = next(l["arrive_t_min"] for l in a2["legs"] if l["kind"] == "to_settlement")
    underway = {**a2, "status": "IN_PROGRESS", "history": a2["history"] + [
        {"status": "ACCEPTED", "t_min": 1}, {"status": "DISPATCHED", "t_min": a2["legs"][0]["depart_t_min"]},
        {"status": "IN_PROGRESS", "t_min": at_site}]}
    now = at_site + 1.0                                          # loading at the settlement: people are on board
    out = run_plan(roads, now=now, ledger=[underway], forecast=_flooded_from(roads, now), version=1)
    u = next(a for a in out["assignments"] if a["assignment_id"] == a2["assignment_id"])
    assert u["status"] == "IN_PROGRESS"
    assert "no feasible route" in u["attention"] and "operator decision" in u["attention"]
    assert u["history"][-1]["status"] == "IN_PROGRESS"           # nothing appended by the planner
    kinds = {c["assignment_id"]: c["change"] for c in out["changes"]}
    assert kinds.get(a2["assignment_id"]) == "attention"
    assert not any(c["change"] == "cancelled" for c in out["changes"])
    assert out["summary"]["covered_committed"] == a2["people"]
    assert out["summary"]["assignments_cancelled_now"] == 0
    assert out["invariant_violations"] == []


# ------------------------------------------------------------------ T16
BODY = {"now_t_min": 0, "series_t_min": list(T), "streets_cm": {}}


def test_t16_planner_stamps_plan_version_on_every_assignment_it_writes():
    j1 = client.post("/api/response/plan", json=BODY).json()
    assert j1["plan_version"] == 1
    assert all(a["plan_version"] == 1 for a in j1["assignments"])
    j2 = client.post("/api/response/plan", json={**BODY, "ledger": j1["assignments"], "plan_version": 1}).json()
    assert j2["plan_version"] == 2 and j2["based_on_plan_version"] == 1
    assert max(a["plan_version"] for a in j2["assignments"]) == 2


def test_t16_ledger_newer_than_plan_version_is_rejected_with_422():
    j1 = client.post("/api/response/plan", json=BODY).json()
    ledger = [{**a, "plan_version": 3} for a in j1["assignments"]]    # a ledger that came out of plan v3
    r = client.post("/api/response/plan", json={**BODY, "ledger": ledger, "plan_version": 2})
    assert r.status_code == 422, r.text
    assert "newer" in str(r.json().get("detail", "")).lower()
    ok = client.post("/api/response/plan", json={**BODY, "ledger": ledger, "plan_version": 3})
    assert ok.status_code == 200, ok.text
    assert ok.json()["plan_version"] == 4 and ok.json()["based_on_plan_version"] == 3


def test_t16_legacy_ledger_without_plan_version_still_plans():
    j1 = client.post("/api/response/plan", json=BODY).json()
    ledger = [{k: v for k, v in a.items() if k != "plan_version"} for a in j1["assignments"]]
    r = client.post("/api/response/plan", json={**BODY, "ledger": ledger, "plan_version": 1})
    assert r.status_code == 200, r.text
    assert r.json()["plan_version"] == 2


# ------------------------------------------------------------------ resources override (D-33 last bullet)
def _run_with(resources, roads, now=0.0, ledger=(), response_events=(), version=0):
    known = {k: {r["id"] for r in resources[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    known.update({"vehicle_id": known["vehicles"], "shelter_id": known["shelters"], "settlement_id": known["settlements"]})
    return plan(resources, parse_response_events(list(response_events), known), parse_ledger(list(ledger), known),
                router(roads, DRY, (), now), now, version)


def test_redirect_uses_request_resources_not_fixture_globals(roads):
    res = copy.deepcopy(fx.demo_resources())
    rename = {"V1": "VA", "V2": "VB"}
    for v in res["vehicles"]:
        v["id"] = rename[v["id"]]
    first = _run_with(res, roads)
    a1 = _proposed(first)[0]
    assert a1["vehicle_id"] in rename.values() and a1["shelter_id"] == "SH1"
    underway = {**a1, "status": "DISPATCHED", "history": a1["history"] + [{"status": "ACCEPTED", "t_min": 1}, {"status": "DISPATCHED", "t_min": 2}]}
    ev = [{"kind": "shelter_update", "shelter_id": "SH1", "received_t_min": 3, "occupancy": 80}]
    out = _run_with(res, roads, now=3, ledger=[underway], response_events=ev, version=1)   # must not raise
    u = next(a for a in out["assignments"] if a["assignment_id"] == a1["assignment_id"])
    assert u["status"] == "DISPATCHED" and u["shelter_id"] == "SH2"
    assert out["invariant_violations"] == []
