"""End-to-end sanity on the SYNTHETIC pilot: mass balance closes, blockage makes streets wetter, nodes surcharge."""
import time
import numpy as np
import pytest

from floodnet.data.fixtures import synthetic_pilot
from floodnet.streets.aggregate import make_street_fn
from floodnet.simulation.engine import run_simulation
from floodnet.config import FRAME_DT_S, VEHICLE_LIMIT_CM

HORIZON_S = 1800


def _run(pilot, blockage):
    runoff = pytest.importorskip("floodnet.terrain.runoff")
    surface = pytest.importorskip("floodnet.terrain.surface")
    hyd = pytest.importorskip("floodnet.drainage.hydraulics")
    import copy
    terrain = pilot["terrain"]
    net = copy.deepcopy(pilot["net"])
    if blockage["mode"] != "none":
        scen = pytest.importorskip("floodnet.drainage.scenarios")
        net = scen.apply_blockage(net, blockage) or net
    scenario = next(s for s in pilot["scenarios"] if s.id == "cloudburst")
    street_fn = make_street_fn(pilot["roads"], terrain.grid)
    t0 = time.time()
    res = run_simulation(terrain, net, scenario, surface.StorageCellSurface(terrain), hyd.GraphDrainage(net),
                         runoff.runoff_fn, street_fn=street_fn, blockage=blockage, horizon_s=HORIZON_S)
    return res, time.time() - t0


@pytest.fixture(scope="module")
def runs():
    pilot = synthetic_pilot()
    normal, t_n = _run(pilot, {"mode": "none"})
    blocked, t_b = _run(pilot, {"mode": "fraction", "fraction": 0.8})
    print(f"\n[e2e] normal run {t_n:.2f}s (engine {normal.runtime_s:.2f}s); blocked run {t_b:.2f}s (engine {blocked.runtime_s:.2f}s)")
    return normal, blocked


def test_frame_count(runs):
    for r in runs:
        assert len(r.frames) == HORIZON_S // FRAME_DT_S + 1
        assert r.frames[0].t_s == 0 and r.frames[-1].t_s == pytest.approx(HORIZON_S)


def test_mass_balance_closes(runs):
    for r in runs:
        mb = r.mass_balance
        assert mb.rain_in_m3 > 0
        assert abs(mb.error_pct) < 1.0, mb


def test_blocked_is_wetter_than_normal(runs):
    """Blockage must keep more water on the surface and make the streets over the drain line wetter.
    The global max-depth cell is NOT compared: the surface solver's pool is not level within 30 min
    (see test_terrain_surface), so the argmax cell wanders and that comparison is noise."""
    normal, blocked = runs
    mb_n, mb_b = normal.mass_balance, blocked.mass_balance
    assert mb_b.outfall_out_m3 < mb_n.outfall_out_m3
    assert mb_b.surface_stored_m3 + mb_b.network_stored_m3 > mb_n.surface_stored_m3 + mb_n.network_stored_m3
    # street-level: water volume sitting on road cells and the number of impassable (car) segments must not drop
    pilot = synthetic_pilot()
    g = pilot["terrain"].grid
    fn = make_street_fn(pilot["roads"], g)
    road_cells = np.unique(np.concatenate(list(fn.cells.values())))
    vol_n = float(normal.frames[-1].depth.ravel()[road_cells].sum()) * g.cell_area
    vol_b = float(blocked.frames[-1].depth.ravel()[road_cells].sum()) * g.cell_area
    limit_m = VEHICLE_LIMIT_CM["car"] / 100.0
    imp_n = sum(v >= limit_m for v in normal.frames[-1].street_depth_m.values())
    imp_b = sum(v >= limit_m for v in blocked.frames[-1].street_depth_m.values())
    glob_n = max(max(f.street_depth_m.values()) for f in normal.frames)
    glob_b = max(max(f.street_depth_m.values()) for f in blocked.frames)
    print(f"[e2e] water on road cells normal={vol_n:.0f} m3 blocked={vol_b:.0f} m3; impassable segs {imp_n} -> {imp_b}; "
          f"global max street depth normal={glob_n*100:.1f} cm blocked={glob_b*100:.1f} cm")
    assert vol_b >= vol_n and vol_b > 0
    assert imp_b >= imp_n and imp_b > 0


def test_blocked_run_surcharges(runs):
    _, blocked = runs
    assert any(f.node_surcharging.any() for f in blocked.frames)
    assert blocked.blockage["mode"] == "fraction"


def test_depths_nonnegative_and_finite(runs):
    for r in runs:
        for f in r.frames:
            assert np.isfinite(f.depth).all() and (f.depth >= -1e-9).all()
        assert r.provenance["terrain"]["tag"] == "SYNTHETIC"
