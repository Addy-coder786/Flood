"""Seeded scenario generator, held-out seeds, multi-seed aggregation, information-availability check and evidence
exports for the response evaluation (decision D-35; acceptance E1-E8 in the Agent D test matrix).

Written test-first: every test here fails until floodnet.response.evaluate grows
    generate_script, DEV_SEEDS, HELD_OUT_SEEDS, evaluate_many, check_information_availability,
    export_rows, export_csv, strip_timing
and the API accepts `seed` / `seeds`. The module is imported as a namespace (not `from ... import name`) so that
each test fails for its own reason instead of the whole file erroring at collection.

Everything here is SIMULATED evidence: forecast and outcome are FloodNet runs, never observations. Scripts are kept
short (two replans, one lead) because every replay on the real pilot costs 1-2 s.
"""
from __future__ import annotations

import copy
import json
import math
import statistics

import pytest
from fastapi.testclient import TestClient

from floodnet import config
from floodnet.access.road_status import BLOCKING, Forecast, SegmentContext
from floodnet.api import state
from floodnet.api.main import app
from floodnet.response import evaluate as ev
from floodnet.response.fixtures import demo_resources

T = tuple(float(x) for x in range(0, 125, 5))          # 0..120 min, as in test_response_eval.py
T60 = tuple(float(x) for x in range(0, 65, 5))         # 0..60 min: keeps API-side replays short
DRY = Forecast(T, {}, label="dry forecast")
DRY60 = Forecast(T60, {}, label="dry forecast (60 min)")
NO_CTX = SegmentContext(frozenset(), frozenset())
SHORT = {"replan_t_min": [0, 30], "prediction_lead_min": [15]}
CLOSURE_KINDS = {"closure_on_underway", "closure_random"}
RESOURCE_KINDS = {"vehicle_unavailable", "shelter_update", "settlement_update"}
ALLOWED_TIERS = {"OPERATOR", "FIELD_TEAM", "PUBLIC"}
client = TestClient(app)


@pytest.fixture(scope="module")
def roads():
    return state.get_pilot()["roads"]


@pytest.fixture(scope="module")
def resources():
    return demo_resources()


def _short(script: dict) -> dict:
    """The generated script with the replay shortened (keeps the events, the seed and the generator version)."""
    return {**script, **SHORT}


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def _walk_keys(obj, found: set, path: str = "") -> set:
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(str(k))
            _walk_keys(v, found, f"{path}.{k}")
    elif isinstance(obj, list):
        for v in obj:
            _walk_keys(v, found, path)
    return found


# ---------------------------------------------------------------------------------------------- E1 / E7
def test_same_seed_gives_identical_script_and_evaluation(roads, resources):
    """E1 + E7: same seed -> byte-identical script; two evaluations of it differ only in timing fields."""
    s1 = ev.generate_script(7, roads, resources, horizon_t_min=120)
    s2 = ev.generate_script(7, roads, resources, horizon_t_min=120)
    assert _canon(s1) == _canon(s2)
    assert s1["seed"] == 7 and s1["generator_version"]
    for key in ("replan_t_min", "events", "prediction_lead_min", "prediction_vehicle"):   # DEMO_SCRIPT shape
        assert key in s1, key
    a = ev.evaluate(roads, NO_CTX, DRY, DRY, _short(s1))
    b = ev.evaluate(roads, NO_CTX, DRY, DRY, _short(s2))
    a_s, b_s = ev.strip_timing(a), ev.strip_timing(b)
    assert _canon(a_s) == _canon(b_s)
    keys = _walk_keys(a_s, set())
    assert not {k for k in keys if k.startswith("replan_ms")}, "timing fields must be stripped"
    assert "processed_at" not in keys
    assert a_s["script"]["seed"] == 7


def test_different_seeds_give_different_scripts(roads, resources):
    """E1: seeds 1..5 do not all produce the same events."""
    scripts = [ev.generate_script(s, roads, resources, horizon_t_min=120) for s in range(1, 6)]
    assert [s["seed"] for s in scripts] == [1, 2, 3, 4, 5]
    assert len({_canon(s["events"]) for s in scripts}) >= 2
    assert len({s["generator_version"] for s in scripts}) == 1


# ---------------------------------------------------------------------------------------------- E2
@pytest.mark.parametrize("seed", [0, 3, 11, 19])
def test_generated_events_are_valid_and_within_horizon(roads, resources, seed):
    horizon = 120
    seg_ids = {s.seg_id for s in roads.segments}
    ids = {k: {r["id"] for r in resources[k]} for k in ("settlements", "shelters", "vehicles")}
    script = ev.generate_script(seed, roads, resources, horizon_t_min=horizon, n_closures=2, n_resource_events=2)
    events = script["events"]
    assert events == sorted(events, key=lambda e: e["t"]), "events must be sorted by t"
    kinds = [e["kind"] for e in events]
    assert all(k in CLOSURE_KINDS | RESOURCE_KINDS for k in kinds), kinds
    assert sum(k in CLOSURE_KINDS for k in kinds) == 2
    assert sum(k in RESOURCE_KINDS for k in kinds) == 2
    for e in events:
        assert 0 <= e["t"] <= horizon, e
        if "applies_from_t_min" in e:                                      # ties with D-32: a report never arrives
            assert e["applies_from_t_min"] >= e["t"], e                   # after the time it describes is used
        if e["kind"] == "closure_random":
            assert e["seg_id"] in seg_ids, e
            assert e["source_tier"] in ALLOWED_TIERS, e
        elif e["kind"] == "closure_on_underway":
            assert isinstance(e["which"], int) and e["which"] >= 0, e
        elif e["kind"] == "vehicle_unavailable":
            assert e["vehicle_id"] in ids["vehicles"], e
        elif e["kind"] == "shelter_update":
            assert e["shelter_id"] in ids["shelters"], e
        elif e["kind"] == "settlement_update":
            assert e["settlement_id"] in ids["settlements"], e
    rt = script["replan_t_min"]
    assert rt and rt[0] == 0 and rt == sorted(rt) and len(set(rt)) == len(rt) and rt[-1] <= horizon
    assert script["prediction_lead_min"] and all(L > 0 for L in script["prediction_lead_min"])
    assert script["prediction_vehicle"] in config.VEHICLE_LIMIT_CM
    assert "SIMULATED" in json.dumps(script).upper()


# ---------------------------------------------------------------------------------------------- E3
def test_dev_and_held_out_seeds_are_disjoint_and_documented():
    dev, held = set(ev.DEV_SEEDS), set(ev.HELD_OUT_SEEDS)
    assert dev == set(range(0, 20)) and held == set(range(100, 120))
    assert not (dev & held) and len(held) >= 20
    # held-out seeds are reserved for the final report: no test module may use them as a fixture value
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    for path in here.glob("test_*.py"):
        if path.name == pathlib.Path(__file__).name:
            continue
        assert "HELD_OUT_SEEDS" not in path.read_text(encoding="utf-8"), f"{path.name} must not touch held-out seeds"


# ---------------------------------------------------------------------------------------------- E4
def test_evaluate_many_aggregates_match_per_seed(roads):
    out = ev.evaluate_many(roads, NO_CTX, DRY60, DRY60, [1, 2], horizon_t_min=60)
    assert set(out) >= {"per_seed", "aggregate", "information_check"}
    per = out["per_seed"]
    assert [p["seed"] for p in per] == [1, 2]
    for p in per:
        assert "comparison" in p and isinstance(p["metrics"], dict)
    assert out["aggregate"], "aggregate must not be empty"
    for name, agg in out["aggregate"].items():
        assert set(agg) >= {"mean", "min", "max", "std"}, name
        vals = [float(p["metrics"][name]) for p in per]
        assert agg["mean"] == pytest.approx(statistics.fmean(vals), abs=1e-6), name
        assert agg["min"] == pytest.approx(min(vals)) and agg["max"] == pytest.approx(max(vals)), name
        spread = {statistics.pstdev(vals), statistics.stdev(vals)}
        assert any(agg["std"] == pytest.approx(s, abs=1e-6) for s in spread), (name, agg["std"], spread)
    ic = out["information_check"]
    assert ic["ok"] is True and ic["violations"] == []


# ---------------------------------------------------------------------------------------------- E5
def test_information_check_passes_on_a_real_replay_and_flags_a_tampered_result(roads):
    # DEMO_SCRIPT's closure arrives at T+10 (pinned by test_response_eval), so three replans suffice
    script = {**ev.DEMO_SCRIPT, "replan_t_min": [0, 10, 30], "prediction_lead_min": [15]}
    result = ev.evaluate(roads, NO_CTX, DRY, DRY, script)
    check = ev.check_information_availability(result)
    assert check["ok"] is True and check["violations"] == []
    assert result["forecast"] == DRY.label, "one issued forecast per replay, never re-issued between steps"

    arrival = result["modes"]["arrival"]
    assert len(arrival["road_events"]) == 1
    closure = arrival["road_events"][0]
    step_idx = next(i for i, s in enumerate(arrival["steps"]) if any("closure" in e for e in s["events"]))
    assert arrival["steps"][step_idx]["t_min"] == closure["received_t_min"]

    # Tamper: the closure is now recorded as received AFTER the decision that used it (both sides moved so that
    # any honest check -- whether it records used input ids or the max received time per step -- must notice).
    tampered = copy.deepcopy(result)
    t_arr = tampered["modes"]["arrival"]
    t_arr["road_events"][0]["received_t_min"] = closure["received_t_min"] + 5
    t_arr["steps"][step_idx]["t_min"] = closure["received_t_min"] - 5
    flagged = ev.check_information_availability(tampered)
    assert flagged["ok"] is False
    assert flagged["violations"] and all(isinstance(v, (str, dict)) for v in flagged["violations"])


# ---------------------------------------------------------------------------------------------- E6
def _is_blocking(value) -> bool:
    """Accepts the honest encodings a row may use: bool, 'blocking'/'passable', or a road status string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().upper()
        if v in ("BLOCKING", "BLOCKED", "TRUE"):
            return True
        if v in ("PASSABLE", "FALSE"):
            return False
        return v in BLOCKING
    raise AssertionError(f"unexpected label encoding {value!r}")


def test_export_rows_count_and_labels_match_prediction_counts(roads):
    # one road floods at T+20 in both runs; one other road is unlinked from the grid -> UNKNOWN truth -> excluded
    wet, unknown = roads.segments[0].seg_id, roads.segments[1].seg_id
    k = sum(1 for t in T if t < 20)
    f = Forecast(T, {wet: [0.0] * k + [70.0] * (len(T) - k)}, label="one wet road")
    ctx = SegmentContext(frozenset({unknown}), frozenset())
    script = {**ev.DEMO_SCRIPT, "events": [], **SHORT}
    out = ev.evaluate(roads, ctx, f, f, script)
    pred = out["prediction"]
    decisions, leads = len(SHORT["replan_t_min"]), len(SHORT["prediction_lead_min"])
    assert pred["unknown_cases_excluded"] == decisions * leads

    rows = ev.export_rows(out)
    assert isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows)
    assert len(rows) == len(roads.segments) * decisions * leads
    required = {"seg_id", "sim_t_min", "lead_min", "label", "excluded", "tag", "arrival_aware", "current_status"}
    assert required <= set(rows[0]), set(rows[0])
    assert len({tuple(sorted(r)) for r in rows}) == 1, "every row has the same columns"
    assert all(r["tag"] == "SIMULATED" for r in rows)
    assert {r["sim_t_min"] for r in rows} == set(SHORT["replan_t_min"])
    assert {r["lead_min"] for r in rows} == set(SHORT["prediction_lead_min"])

    excluded = [r for r in rows if r["excluded"] is True]
    assert len(excluded) == decisions * leads and all(r["seg_id"] == unknown and r["label"] is None for r in excluded)
    scored = [r for r in rows if r["excluded"] is False]
    assert all(r["label"] is not None for r in scored)
    for method in ("arrival_aware", "current_status"):
        counts = {"hits": 0, "misses": 0, "false_alarms": 0, "correct_passable": 0}
        for r in scored:
            truth, p = _is_blocking(r["label"]), _is_blocking(r[method])
            counts["hits" if p and truth else "false_alarms" if p else "misses" if truth else "correct_passable"] += 1
        assert counts == {key: pred[method][key] for key in counts}, method
    assert sum(1 for r in scored if _is_blocking(r["label"])) == pred["outcome_blocking_cases"]


def test_export_csv_header_and_simulated_tag(roads):
    script = {**ev.DEMO_SCRIPT, "events": [], **SHORT}
    out = ev.evaluate(roads, NO_CTX, DRY, DRY, script)
    rows = ev.export_rows(out)
    csv_text = ev.export_csv(rows)
    assert isinstance(csv_text, str)
    lines = csv_text.strip().splitlines()
    assert len(lines) == len(rows) + 1
    header = lines[0].split(",")
    assert len(header) == len(set(header)) and {"tag", "seg_id", "sim_t_min", "lead_min", "excluded", "label"} <= set(header)
    assert ev.export_csv([]).strip().splitlines() == [lines[0]], "header is fixed, not derived from the rows"
    assert ev.export_csv(list(reversed(rows))).strip().splitlines()[0] == lines[0]
    tag_col = header.index("tag")
    assert all(line.split(",")[tag_col] == "SIMULATED" for line in lines[1:])


# ---------------------------------------------------------------------------------------------- E8
def _body(**extra) -> dict:
    return {"series_t_min": list(T60), "streets_cm": {}, "outcome_series_t_min": list(T60), "outcome_streets_cm": {}, **extra}


def test_api_evaluate_with_seed_echoes_it_and_default_is_unchanged():
    r = client.post("/api/response/evaluate", json=_body(seed=3))
    assert r.status_code == 200, r.text
    j = r.json()
    assert "seed" in j["script"], "seed must be echoed in the script (unknown body keys are ignored today)"
    assert j["script"]["seed"] == 3 and j["script"]["generator_version"]
    assert j["tag"].startswith("SIMULATED") and "ledgers" not in j
    r0 = client.post("/api/response/evaluate", json=_body())
    assert r0.status_code == 200, r0.text
    assert r0.json()["script"] == ev.DEMO_SCRIPT, "no seed -> the hand-written DEMO_SCRIPT, unchanged"


def test_api_rejects_too_many_seeds():
    r = client.post("/api/response/evaluate", json=_body(seeds=list(range(6))))
    assert r.status_code == 422, f"expected 422 for 6 seeds, got {r.status_code}"


def test_api_evaluate_with_seeds_returns_per_seed_and_aggregate():
    r = client.post("/api/response/evaluate", json=_body(seeds=[1, 2]))
    assert r.status_code == 200, r.text
    j = r.json()
    assert [p["seed"] for p in j["per_seed"]] == [1, 2]
    assert j["aggregate"] and j["information_check"]["ok"] is True
    assert "disclaimer" in j and "SIMULATED" in json.dumps(j).upper()
    assert math.isfinite(next(iter(j["aggregate"].values()))["mean"])
