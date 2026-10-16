"""Serverless-safe runs: POST /api/simulate returns the COMPLETE result, so nothing afterwards needs the server
instance that computed it (the Vercel bug: run_not_found when a follow-up request reached another instance)."""
from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import floodnet.api.main as main
from floodnet.api import state
from floodnet.api.bundle import unpack

HORIZON = 30


@pytest.fixture(scope="module")
def instance_a():
    """One simulation on 'instance A'; returns its response and A's in-memory copy (for comparison only)."""
    c = TestClient(main.app)
    r = c.post("/api/simulate", json={"scenario_id": "cloudburst", "horizon_min": HORIZON})
    assert r.status_code == 200, r.text
    body = r.json()
    return body, state.get_run(body["run_id"]), len(r.content)


def _forget_all_runs(monkeypatch):
    """Become 'instance B': a process that has never seen the run."""
    monkeypatch.setattr(state, "_runs", type(state._runs)())


def test_response_is_complete_and_matches_the_engine(instance_a):
    body, res, _size = instance_a
    assert body["self_contained"] is True
    b = body["bundle"]
    assert b["t_min"] == body["frames_t_min"] and len(b["t_min"]) == len(res.frames)
    assert np.array_equal(unpack(b["nodes"]["hgl_m"]), np.stack([f.node_hgl for f in res.frames]).astype(np.float32))
    assert np.array_equal(unpack(b["edges"]["util"]), np.stack([f.edge_util for f in res.frames]).astype(np.float32))
    streets = unpack(b["streets"]["depth_m"])
    k = len(res.frames) - 1
    for s, sid in enumerate(b["streets"]["seg_ids"][:200]):
        assert abs(streets[k, s] - res.frames[k].street_depth_m.get(sid, 0.0)) < 1e-6
    for key in ("series", "hotspots", "summary", "provenance", "explain_provenance"):
        assert body[key] is not None, key
    assert body["alert"] is not None or body["alert_error"]
    assert "streets" not in body["series"]                     # rebuilt in the browser from the bundle


def test_another_instance_can_serve_everything_the_ui_does_next(instance_a, monkeypatch):
    body, _res, _size = instance_a
    _forget_all_runs(monkeypatch)
    c = TestClient(main.app)
    # the old follow-up request is exactly what failed on Vercel ...
    r = c.get(f"/api/simulation/{body['run_id']}/frame/0")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "run_not_found"
    # ... and the UI no longer needs it: routing on instance B works from the depths the client sends
    b = body["bundle"]
    depth = unpack(b["streets"]["depth_m"])[-1]
    wet = {sid: float(d) for sid, d in zip(b["streets"]["seg_ids"], depth) if d > 0}
    route = c.post("/api/route", json={"origin": [72.838, 19.013], "dest": [72.852, 19.027], "t_min": b["t_min"][-1],
                                       "run_id": body["run_id"], "street_depth_m": wet})
    assert route.status_code == 200, route.text
    assert route.json()["depth_source"] == "simulation frame"
    series_cm = {sid: [round(float(unpack(b["streets"]["depth_m"])[k, s]) * 100, 1) for k in range(len(b["t_min"]))]
                 for s, sid in enumerate(b["streets"]["seg_ids"]) if sid in wet}
    alt = c.post("/api/route/alternatives", json={
        "origin": [72.838, 19.013], "dest": [72.852, 19.027], "t_min": b["t_min"][-1], "run_id": body["run_id"],
        "street_depth_m": wet, "series_t_min": b["t_min"], "streets_cm": series_cm})
    assert alt.status_code == 200, alt.text
    assert all("time_safety" in cand for cand in alt.json().get("candidates", []))


def test_no_request_falls_back_to_this_instances_last_run(instance_a, monkeypatch):
    body, _res, _size = instance_a
    c = TestClient(main.app)                                   # this instance DOES hold the run ...
    r = c.post("/api/route", json={"origin": [72.838, 19.013], "dest": [72.852, 19.027], "t_min": 30})
    assert r.status_code == 200
    assert r.json()["depth_source"] == "no run: dry network assumed"   # ... but without depths it is never used


def test_response_size_is_practical(instance_a):
    _body, _res, size = instance_a
    assert size < 4_500_000                                    # Vercel's response-body limit


def test_horizon_beyond_the_deployment_limit_is_refused_clearly(monkeypatch):
    monkeypatch.setattr(main, "MAX_HORIZON_MIN", 60)
    r = TestClient(main.app).post("/api/simulate", json={"scenario_id": "cloudburst", "horizon_min": 120})
    assert r.status_code == 422 and "time limit" in r.json()["detail"]


def test_compare_returns_the_blocked_run_complete(monkeypatch):
    r = TestClient(main.app).post("/api/compare", json={
        "scenario_id": "cloudburst", "horizon_min": 20, "blockage": {"mode": "fraction", "fraction": 0.5}})
    assert r.status_code == 200, r.text
    blocked = r.json()["blocked"]
    assert blocked["complete"]["self_contained"] is True and blocked["complete"]["run_id"] == blocked["run_id"]
    json.dumps(r.json())                                       # serialisable


def test_a_failed_simulation_is_an_error_not_a_partial_result(monkeypatch):
    async def boom(*_a, **_k):
        raise state.ModuleMissing("engine unavailable")
    monkeypatch.setattr(main, "_simulate", boom)
    r = TestClient(main.app).post("/api/simulate", json={"scenario_id": "cloudburst", "horizon_min": 30})
    assert r.status_code == 503 and "bundle" not in r.text
