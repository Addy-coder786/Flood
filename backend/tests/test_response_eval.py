"""Replay and evaluation (floodnet/response/evaluate.py): determinism, judging against an independent outcome,
prediction-quality scoring, and the API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from floodnet.access.road_status import Forecast, SegmentContext
from floodnet.api import state
from floodnet.api.main import app
from floodnet.response.evaluate import DEMO_SCRIPT, evaluate, prediction_quality, replay

T = tuple(float(x) for x in range(0, 125, 5))
DRY = Forecast(T, {}, label="dry forecast")
NO_CTX = SegmentContext(frozenset(), frozenset())
client = TestClient(app)


@pytest.fixture(scope="module")
def roads():
    return state.get_pilot()["roads"]


def test_replay_is_deterministic_and_sound(roads):
    a = replay(roads, NO_CTX, DRY, DRY, "arrival")
    b = replay(roads, NO_CTX, DRY, DRY, "arrival")
    assert [s["plan_version"] for s in a["steps"]] == [s["plan_version"] for s in b["steps"]]
    assert [x["assignment_id"] for x in a["ledger"]] == [x["assignment_id"] for x in b["ledger"]]
    assert a["metrics"]["invariant_violations_total"] == 0 and a["metrics"]["known_closure_violations"] == 0
    assert a["metrics"]["missions_dispatched"] > 0 and a["metrics"]["people_served_by_horizon"] > 0
    assert any("closure" in e for s in a["steps"] for e in s["events"])             # the scripted closure happened
    assert len(a["road_events"]) == 1 and len(a["response_events"]) == 2


def test_outcome_flooding_makes_planned_legs_invalid(roads):
    # The planner sees a dry forecast; in the outcome every road is 70 cm deep from T+20 (over the 60 cm truck
    # limit). Missions planned after T+20 are therefore invalid by the outcome, and the judge must say so.
    k = sum(1 for t in T if t < 20)
    flooded = Forecast(T, {s.seg_id: [0.0] * k + [70.0] * (len(T) - k) for s in roads.segments}, label="flooded outcome")
    r = replay(roads, NO_CTX, DRY, flooded, "arrival")
    m = r["metrics"]
    assert m["legs_invalid_by_outcome"] > 0 and m["missions_invalid_by_outcome"] > 0
    assert m["blocked_examples"][0]["outcome_status"] == "AT_RISK"
    # With a forecast that knows the flooding, arrival-aware routing plans no mission into it
    r2 = replay(roads, NO_CTX, flooded, flooded, "arrival")
    assert r2["metrics"]["legs_invalid_by_outcome"] == 0


def test_prediction_quality_scores_arrival_vs_current(roads):
    # One road floods at T+20 in both forecast and outcome: at t=0 with lead 30 the arrival-aware predictor sees it,
    # the current-status predictor ("stays as now") misses it.
    sid = roads.segments[0].seg_id
    k = sum(1 for t in T if t < 20)
    f = Forecast(T, {sid: [0.0] * k + [70.0] * (len(T) - k)})
    pq = prediction_quality(roads, NO_CTX, f, f, [], [0], [30], "truck")
    assert pq["arrival_aware"]["hits"] == 1 and pq["arrival_aware"]["misses"] == 0
    assert pq["current_status"]["misses"] == 1 and pq["current_status"]["hits"] == 0
    assert pq["arrival_aware"]["probability_of_detection"] == 1.0 and pq["current_status"]["probability_of_detection"] == 0.0
    assert pq["arrival_aware"]["cases"] == len(roads.segments)


def test_evaluate_bundle_shape(roads):
    out = evaluate(roads, NO_CTX, DRY, DRY, {**DEMO_SCRIPT, "replan_t_min": [0, 30], "prediction_lead_min": [15]})
    assert set(out["modes"]) == {"arrival", "current"} and "comparison" in out and out["same_run"]
    assert out["comparison"]["known_closure_violations"] == {"arrival_aware": 0, "current_status": 0}


def test_api_evaluate_with_client_series():
    body = {"series_t_min": list(T), "streets_cm": {}, "outcome_series_t_min": list(T), "outcome_streets_cm": {}}
    r = client.post("/api/response/evaluate", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tag"].startswith("SIMULATED") and "ledgers" not in j and "disclaimer" in j
    assert j["prediction"]["segments"] == len(state.get_pilot()["roads"].segments)
    assert client.post("/api/response/evaluate", json={"series_t_min": list(T), "streets_cm": {}}).status_code == 422
