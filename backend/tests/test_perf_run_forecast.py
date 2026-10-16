"""Run Forecast performance changes (2026-10-11): each optimisation is proven output-identical, and every cache is
proven to (a) actually be reused and (b) never serve old rainfall or old source labels as current.

See docs/PERFORMANCE_OPTIMIZATION.md, "2026-10-11 Run Forecast profiling", for the measurements behind each change.
The physics itself (terrain/surface.py, drainage/hydraulics.py, simulation/engine.py) is NOT touched by these
changes; the only physics-adjacent change is that an identical physics input is not recomputed.
"""
from __future__ import annotations

import zlib
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import floodnet.api.main as main
import floodnet.simulation.engine as engine
from floodnet.api import bundle, state
from floodnet.provenance import Provenance, Tag
from floodnet.rainfall import provider as prov
from floodnet.rainfall.provider import ECMWF_ID, LIVE_ID, RainfallSourceMeta

HORIZON = 10          # minutes; the real pilot, kept short so the suite stays fast


# ----------------------------------------------------------------------------- helpers
def _scenario(sid=ECMWF_ID, mm_h=(40.0, 60.0, 20.0), tag=Tag.NWP, note="stub forecast", name="ECMWF stub"):
    t_s = np.arange(len(mm_h), dtype=float) * 300.0
    from floodnet.contracts import RainfallScenario
    return RainfallScenario(id=sid, name=name, t_s=t_s, intensity_mm_h=np.array(mm_h, dtype=float),
                            provenance=Provenance(tag, "test stub provider", note))


def _meta(scen, timestamp="2026-10-11T00:00:00+00:00", retrieved="2026-10-11T00:05:00+00:00"):
    return RainfallSourceMeta(source_type="ecmwf_forecast", source_name="stub ECMWF", timestamp=timestamp,
                              forecast_horizon_min=HORIZON, resolution_min=5, data_mode=scen.provenance.tag.value,
                              provenance=scen.provenance, detail={"retrieved_at": retrieved})


class _StubProvider:
    """Returns a queue of (scenario, meta) pairs, one per call -- like a provider whose TTL cache expired between
    calls (new pair) or did not (the same pair again)."""

    def __init__(self, *pairs):
        self.pairs = list(pairs)
        self.calls = 0

    def get(self, scenario_id=None, **kwargs):
        pair = self.pairs[min(self.calls, len(self.pairs) - 1)]
        self.calls += 1
        return pair


@pytest.fixture
def physics_calls(monkeypatch):
    """Counts real physics runs (simulation.engine.run_simulation is resolved at call time by api/state.py)."""
    calls = []
    real = engine.run_simulation

    def counting(*a, **k):
        calls.append(a[2].id)                     # scenario id
        return real(*a, **k)
    monkeypatch.setattr(engine, "run_simulation", counting)
    clear = getattr(state, "clear_physics_memo", None)
    if clear:
        clear()
    yield calls
    if clear:
        clear()


def _use_provider(monkeypatch, sid, stub):
    real = prov.list_providers

    def patched():
        out = real()
        out[sid] = stub
        return out
    monkeypatch.setattr(prov, "list_providers", patched)


def _frames_equal(a, b) -> None:
    assert len(a.frames) == len(b.frames)
    for fa, fb in zip(a.frames, b.frames):
        assert fa.t_s == fb.t_s and fa.rain_mm_h == fb.rain_mm_h
        for name in ("depth", "node_hgl", "node_surcharging", "node_surcharge_m3", "edge_flow_m3s", "edge_util"):
            assert np.array_equal(getattr(fa, name), getattr(fb, name)), name
        assert list(fa.node_cause) == list(fb.node_cause)
        assert fa.street_depth_m == fb.street_depth_m
    assert vars(a.mass_balance) == vars(b.mass_balance)


# ----------------------------------------------------------------------------- physics reuse for provider runs
def test_identical_provider_rainfall_is_not_simulated_twice(monkeypatch, physics_calls):
    scen = _scenario(); stub = _StubProvider((scen, _meta(scen)))      # provider TTL cache: same pair twice
    _use_provider(monkeypatch, ECMWF_ID, stub)
    a = state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)
    b = state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)
    assert stub.calls == 2                       # the provider is still asked every time (its TTL decides freshness)
    assert physics_calls == [ECMWF_ID]           # ... but the identical physics input is computed once
    assert a.run_id != b.run_id                  # each request still gets its own run
    _frames_equal(a, b)


def test_reused_physics_is_identical_to_a_fresh_computation(monkeypatch, physics_calls):
    scen = _scenario(); meta = _meta(scen)
    _use_provider(monkeypatch, ECMWF_ID, _StubProvider((scen, meta)))
    state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)              # populates the memo
    reused = state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)
    if hasattr(state, "clear_physics_memo"):
        state.clear_physics_memo()
    fresh = state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)       # recomputed from scratch
    _frames_equal(reused, fresh)
    assert reused.provenance == fresh.provenance
    assert reused.scenario is not None and reused.scenario.id == fresh.scenario.id == ECMWF_ID
    sr, sf = main.summarize(reused), main.summarize(fresh)
    for k in ("run_id", "runtime_s"):
        sr.pop(k); sf.pop(k)
    sr["active_source"].pop("age_seconds", None); sf["active_source"].pop("age_seconds", None)
    assert sr == sf


def test_new_rainfall_data_is_always_simulated(monkeypatch, physics_calls):
    s1 = _scenario(mm_h=(40.0, 60.0, 20.0)); s2 = _scenario(mm_h=(40.0, 90.0, 20.0))
    _use_provider(monkeypatch, ECMWF_ID, _StubProvider((s1, _meta(s1)), (s2, _meta(s2, timestamp="2026-10-11T01:00:00+00:00"))))
    a = state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)
    b = state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)
    assert physics_calls == [ECMWF_ID, ECMWF_ID]
    assert max(f.rain_mm_h for f in b.frames) == 90.0 and max(f.rain_mm_h for f in a.frames) == 60.0


def test_reused_physics_never_carries_the_previous_source_labels(monkeypatch, physics_calls):
    """Same numbers re-fetched later: the physics may be reused, the source timestamp/retrieval time may not."""
    scen = _scenario()
    old, new = _meta(scen), _meta(scen, timestamp="2026-10-11T03:00:00+00:00", retrieved="2026-10-11T03:02:00+00:00")
    _use_provider(monkeypatch, ECMWF_ID, _StubProvider((scen, old), (scen, new)))
    state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)
    b = state.run_scenario(ECMWF_ID, {"mode": "none"}, HORIZON)
    assert b.provenance["rainfall_source"] == new.to_dict()
    assert main.summarize(b)["active_source"]["timestamp"] == new.timestamp


@pytest.mark.parametrize("change", ["source_id", "data_mode", "provenance_note", "blockage", "horizon", "time_window"])
def test_physics_reuse_key_separates_source_mode_window_and_inputs(monkeypatch, physics_calls, change):
    base = _scenario()
    other, blockage, horizon = base, {"mode": "none"}, HORIZON
    if change == "source_id":
        other = replace(base, id="auto")
    elif change == "data_mode":
        other = replace(base, provenance=Provenance(Tag.SYNTHETIC, base.provenance.source, base.provenance.note))
    elif change == "provenance_note":
        other = replace(base, provenance=Provenance(base.provenance.tag, base.provenance.source, "a different fetch"))
    elif change == "blockage":
        blockage = {"mode": "fraction", "fraction": 0.5}
    elif change == "horizon":
        horizon = HORIZON - 5
    elif change == "time_window":
        other = replace(base, t_s=base.t_s + 300.0)
    p = state.get_pilot()
    a = state._run_physics(base, {"mode": "none"}, HORIZON, p)
    b = state._run_physics(other, blockage, horizon, p)
    assert len(physics_calls) == 2, change
    assert b.scenario is other


def test_live_imd_runs_reuse_physics_but_ask_the_provider_every_time(monkeypatch, physics_calls):
    """IMD integration is untouched: the provider (and its own 10-min TTL) is consulted on every request."""
    scen = _scenario(sid=LIVE_ID, tag=Tag.REAL, note="stub IMD observation"); stub = _StubProvider((scen, _meta(scen)))
    _use_provider(monkeypatch, LIVE_ID, stub)
    a = state.run_scenario(LIVE_ID, {"mode": "none"}, HORIZON)
    b = state.run_scenario(LIVE_ID, {"mode": "none"}, HORIZON)
    assert stub.calls == 2 and physics_calls == [LIVE_ID]
    assert a.provenance == b.provenance and b.provenance["data_mode"] == state.run_data_mode(b)


def test_swapping_the_pilot_drops_reused_physics(monkeypatch, physics_calls):
    scen = _scenario()
    p = state.get_pilot()
    state._run_physics(scen, {"mode": "none"}, HORIZON, p)
    monkeypatch.setattr(state, "_runs", type(state._runs)())
    state._clear_derived_caches()
    state._run_physics(scen, {"mode": "none"}, HORIZON, p)
    assert len(physics_calls) == 2


# ----------------------------------------------------------------------------- response building
@pytest.fixture(scope="module")
def run():
    return state.run_scenario("heavy", {"mode": "none"}, HORIZON)


def test_bundle_arrays_decode_exactly_to_the_engine_values(run):
    p = state.get_pilot(); b = bundle.run_bundle(run, p); fr = run.frames
    run_max = max(float(np.nanmax(f.depth)) for f in fr)
    levels = np.stack([bundle.depth_levels(f.depth, run_max) for f in fr])
    assert np.array_equal(np.cumsum(bundle.unpack(b["depth"]["levels_delta"]).astype(np.int64), axis=0) % 256, levels)
    assert np.array_equal(bundle.unpack(b["nodes"]["hgl_m"]), np.stack([f.node_hgl for f in fr]).astype("<f4"))
    assert np.array_equal(bundle.unpack(b["nodes"]["surcharge_m3"]), np.stack([f.node_surcharge_m3 for f in fr]).astype("<f4"))
    assert np.array_equal(bundle.unpack(b["edges"]["util"]), np.stack([f.edge_util for f in fr]).astype("<f4"))
    assert np.array_equal(bundle.unpack(b["edges"]["flow_m3s"]), np.stack([f.edge_flow_m3s for f in fr]).astype("<f4"))
    seg_ids = b["streets"]["seg_ids"]
    assert np.array_equal(bundle.unpack(b["streets"]["depth_m"]),
                          np.array([[f.street_depth_m.get(s, 0.0) for s in seg_ids] for f in fr]).astype("<f4"))


def test_bundle_compression_level_is_not_the_slowest_setting(monkeypatch, run):
    """zlib level 9 took ~0.37 s of a ~0.67 s warm request for 1 % smaller output (measured 2026-10-11); the
    decoded arrays are byte-identical at any level (previous test)."""
    levels = []
    real = zlib.compress
    monkeypatch.setattr(bundle.zlib, "compress", lambda data, level=-1: (levels.append(level), real(data, level))[1])
    bundle.run_bundle(run, state.get_pilot())
    assert levels and max(levels) <= 6


def test_segment_context_is_static_and_computed_once_per_pilot(monkeypatch, run):
    p = state.get_pilot()
    if hasattr(bundle, "clear_caches"):
        bundle.clear_caches()
    first = bundle.run_bundle(run, p)["segments"]
    calls = []
    real = main._segment_midpoint_xy
    monkeypatch.setattr(main, "_segment_midpoint_xy", lambda seg: (calls.append(1), real(seg))[1])
    second = bundle.run_bundle(run, p)["segments"]
    assert second == first
    assert calls == []                           # nothing recomputed for the same pilot


def test_segment_context_matches_a_fresh_computation_and_follows_a_pilot_swap(run):
    p = state.get_pilot()
    cached = bundle.run_bundle(run, p)["segments"]
    if hasattr(bundle, "clear_caches"):
        bundle.clear_caches()
    assert bundle.run_bundle(run, p)["segments"] == cached
    swapped = dict(p, roads=replace(p["roads"], segments=p["roads"].segments[:50]))   # a different road set
    assert len(bundle.run_bundle(run, swapped)["segments"]["node_index"]) == 50


def test_simulate_response_series_is_unchanged_without_building_the_discarded_street_series(monkeypatch, run):
    full = main._series_payload(run)
    seen = []
    real = main._series_payload

    def spy(res, *a, **k):
        seen.append(k.get("include_streets", True))
        return real(res, *a, **k)
    monkeypatch.setattr(main, "_series_payload", spy)
    import asyncio
    out = asyncio.run(main._complete(run))
    assert out["series"] == {k: v for k, v in full.items() if k != "streets"}
    assert seen == [False]                        # the per-street series (dropped by bundle.self_contained) is not built


def test_simulate_endpoint_end_to_end_is_unchanged_for_the_default_scenario():
    """Two identical requests: everything except run_id-derived fields is identical (static-scenario memo + bundle)."""
    import json
    c = TestClient(main.app)
    bodies = []
    for _ in range(2):
        body = c.post("/api/simulate", json={"scenario_id": "heavy", "horizon_min": HORIZON}).json()
        for k in ("cap", "cap_xml"):
            body["alert"].pop(k, None)           # CAP identifier/sent time embed the run id and wall clock
        body["alert"]["model_basis"].pop("time_anchor", None)   # wall-clock anchor of the draft (cap.py)
        (body.get("active_source") or {}).pop("age_seconds", None)
        bodies.append(json.loads(json.dumps(body).replace(body["run_id"], "<run_id>")))
    assert bodies[0] == bodies[1]
