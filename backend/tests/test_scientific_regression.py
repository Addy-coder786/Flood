"""Scientific regression guards for the coupled chain (rainfall -> runoff -> surface -> drainage -> street depth).

These are CONSISTENCY checks -- determinism, conservation, physical bounds, monotonic response to forcing. They
are not accuracy tests: FloodNet has no independent street-level depth observations to validate against, and
nothing here pretends otherwise. They run on the small SYNTHETIC pilot so the whole 0-180 min window is cheap;
the real-pilot replay check at the end is skipped when the pilot has not been built.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import networkx as nx
import numpy as np
import pytest

from floodnet import config
from floodnet.data.fixtures import synthetic_pilot
from floodnet.drainage.hydraulics import GraphDrainage
from floodnet.simulation.engine import run_simulation
from floodnet.streets.aggregate import make_street_fn
from floodnet.terrain.runoff import runoff_fn
from floodnet.terrain.surface import StorageCellSurface


def _simulate(pilot, scenario, horizon_s=1800, blockage=None):
    net = copy.deepcopy(pilot["net"])
    blockage = blockage or {"mode": "none"}
    if blockage["mode"] != "none":
        from floodnet.drainage.scenarios import apply_blockage
        net = apply_blockage(net, blockage) or net
    terrain = pilot["terrain"]
    return run_simulation(terrain, net, scenario, StorageCellSurface(terrain), GraphDrainage(net), runoff_fn,
                          street_fn=make_street_fn(pilot["roads"], terrain.grid), blockage=blockage, horizon_s=horizon_s)


@pytest.fixture(scope="module")
def pilot():
    p = synthetic_pilot()
    p["by_id"] = {s.id: s for s in p["scenarios"]}
    return p


@pytest.fixture(scope="module")
def full(pilot):
    """The whole 0-180 min forecast window, once."""
    return _simulate(pilot, pilot["by_id"]["cloudburst"], horizon_s=config.HORIZON_S)


def test_forecast_covers_0_to_180_min_in_even_5_min_frames(full):
    t = np.array([f.t_s for f in full.frames])
    assert t[0] == 0 and t[-1] == pytest.approx(180 * 60)
    assert len(t) == config.HORIZON_S // config.FRAME_DT_S + 1 == 37
    assert np.allclose(np.diff(t), config.FRAME_DT_S)                 # stable, strictly increasing timestep


def test_every_field_is_finite_and_physically_bounded(full):
    for f in full.frames:
        for name in ("depth", "node_hgl", "node_surcharge_m3", "edge_flow_m3s", "edge_util"):
            a = np.asarray(getattr(f, name), dtype=float)
            assert np.isfinite(a).all(), f"{name} has NaN/Inf at t={f.t_s}"
        assert np.asarray(f.depth).min() >= 0
        assert np.asarray(f.node_surcharge_m3).min() >= 0
        assert np.asarray(f.edge_util).min() >= 0
        assert all(np.isfinite(v) and v >= 0 for v in f.street_depth_m.values())
        assert np.isfinite(f.rain_mm_h) and f.rain_mm_h >= 0


def test_water_is_conserved_over_the_full_window(full):
    mb = full.mass_balance
    assert mb.rain_in_m3 > 0
    assert abs(mb.error_pct) < 1.0
    parts = mb.surface_stored_m3 + mb.network_stored_m3 + mb.outfall_out_m3 + mb.infiltration_m3 + mb.abstraction_m3
    assert parts == pytest.approx(mb.rain_in_m3 - mb.error_m3, rel=1e-6, abs=1e-3)   # inflow == stores + outflows


def test_the_chain_is_exercised_end_to_end(full):
    """rain -> runoff on the surface -> drainage carries flow to an outfall -> streets report depth."""
    assert max(f.rain_mm_h for f in full.frames) > 0
    assert max(float(np.asarray(f.depth).max()) for f in full.frames) > 0
    assert max(float(np.asarray(f.edge_flow_m3s).max()) for f in full.frames) > 0
    assert full.mass_balance.outfall_out_m3 > 0
    assert max(max(f.street_depth_m.values(), default=0.0) for f in full.frames) > 0


def test_replay_is_deterministic(pilot):
    a = _simulate(pilot, pilot["by_id"]["cloudburst"])
    b = _simulate(pilot, pilot["by_id"]["cloudburst"])
    for fa, fb in zip(a.frames, b.frames):
        assert np.array_equal(fa.depth, fb.depth)
        assert np.array_equal(fa.node_hgl, fb.node_hgl)
        assert fa.street_depth_m == fb.street_depth_m
    assert a.mass_balance.rain_in_m3 == b.mass_balance.rain_in_m3


def test_more_rain_gives_more_water_everywhere_it_is_counted(pilot):
    base = pilot["by_id"]["moderate"]
    doubled = replace(base, id="moderate_x2", intensity_mm_h=base.intensity_mm_h * 2.0)
    lo, hi = _simulate(pilot, base), _simulate(pilot, doubled)
    assert hi.mass_balance.rain_in_m3 == pytest.approx(2.0 * lo.mass_balance.rain_in_m3, rel=1e-6)
    assert hi.mass_balance.surface_stored_m3 > lo.mass_balance.surface_stored_m3
    peak = lambda r: max(max(f.street_depth_m.values(), default=0.0) for f in r.frames)  # noqa: E731
    assert peak(hi) >= peak(lo)
    surcharge = lambda r: sum(float(np.asarray(f.node_surcharge_m3).sum()) for f in r.frames)  # noqa: E731
    assert surcharge(hi) >= surcharge(lo)


def test_zero_rain_stays_dry(pilot):
    base = pilot["by_id"]["moderate"]
    dry = _simulate(pilot, replace(base, id="dry", intensity_mm_h=base.intensity_mm_h * 0.0))
    assert dry.mass_balance.rain_in_m3 == 0
    assert max(float(np.asarray(f.depth).max()) for f in dry.frames) == 0
    assert not any(bool(np.asarray(f.node_surcharging).any()) for f in dry.frames)


def test_blockage_scenario_holds_water_back(pilot):
    clear = _simulate(pilot, pilot["by_id"]["cloudburst"])
    blocked = _simulate(pilot, pilot["by_id"]["cloudburst"], blockage={"mode": "fraction", "fraction": 0.8})
    assert blocked.mass_balance.outfall_out_m3 < clear.mass_balance.outfall_out_m3
    assert blocked.mass_balance.surface_stored_m3 > clear.mass_balance.surface_stored_m3
    assert abs(blocked.mass_balance.error_pct) < 1.0


def test_drainage_graph_is_directed_and_every_node_drains_to_an_outfall(pilot):
    net = pilot["net"]
    g = nx.DiGraph()
    g.add_nodes_from(range(len(net.node_id)))
    g.add_edges_from(zip(np.asarray(net.edge_us).tolist(), np.asarray(net.edge_ds).tolist()))
    outfalls = set(np.flatnonzero(np.asarray(net.node_is_outfall)).tolist())
    assert outfalls
    reaches = set(outfalls)
    for o in outfalls:
        reaches |= nx.ancestors(g, o)
    assert reaches == set(g.nodes)                                     # no orphaned sub-network
    assert np.all(np.asarray(net.edge_capacity_m3s) > 0)               # capacity computed for every conduit


REAL_PILOT_BUILT = all((config.DATA_PROCESSED / f).exists() for f in ("network.json", "terrain.npz", "scenarios.json"))


@pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
def test_26_july_2005_replay_runs_on_the_real_pilot_and_is_labelled_real():
    from fastapi.testclient import TestClient
    from floodnet.api.main import app
    r = TestClient(app).post("/api/simulate", json={"scenario_id": "july2005", "horizon_min": 30})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["provenance"]["rainfall"]["tag"] == "REAL"
    assert b["n_frames"] == 7 and abs(b["mass_balance"]["error_pct"]) < 0.5
    assert b["summary"]["peak_rain_mm_h"] > 50                          # the recorded storm, not a placeholder
    assert b["summary"]["max_depth_cm"] >= 0
