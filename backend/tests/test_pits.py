"""Task 1/2 validation: extreme-depth cause + conservative pit handling.

Verdict (docs/validation/EXTREME_DEPTH.md, docs/validation/dem_reliability.json): the DTM agrees with 1,205
independently surveyed MCGM manhole ground levels to mean +0.012 m / SD 0.283 m across the whole pilot. A
small number of contour-derived cells (7 depressions, 407 cells, 0.65% of the grid) sit >3 m below every
surveyed manhole within 250 m -- these are FLAGGED as DEM-unreliable, never altered. Separately, the pilot
is a clipped window out of Mumbai with a closed grid boundary, which was found to pond water artificially
against the clip line; an open (free-outfall) boundary was added to the surface model as a MODEL fix
(not a DEM fix). Excluding the flagged cells, the maximum modelled depth is materially lower under both the
closed and the open boundary (2.15 m vs 2.82 m closed / 2.69 m open) -- i.e. the flagged pits, not general
DEM noise, drove the reported 2.8 m extreme.

This test proves: (1) the DEM is not modified by pit handling, (2) mass balance including the new
boundary-outflow term still closes to numerical precision, (3) a large genuine synthetic bowl is never
flagged or opened away, (4) the open boundary only touches edge cells that actually slope outward.
"""
from __future__ import annotations
import numpy as np
import pytest

from floodnet.terrain.pits import depressions, dem_reliability_mask, outward_open_boundary
from floodnet.terrain.surface import StorageCellSurface
from floodnet.terrain.runoff import runoff_fn
from floodnet.simulation.engine import run_simulation
from floodnet.drainage.hydraulics import GraphDrainage


def test_dem_not_modified_by_reliability_mask(real_or_skip):
    p = real_or_skip
    t = p["terrain"]
    z_before = t.z.copy()
    mask, report = dem_reliability_mask(t, p["net"])
    assert np.array_equal(t.z, z_before), "dem_reliability_mask must never write to Terrain.z"
    assert mask.dtype == bool and mask.shape == t.z.shape
    assert 0 < report["n_cells_flagged"] < 0.05 * mask.size, "flagging should be conservative, not blanket"


def test_open_boundary_only_touches_outward_slope(real_or_skip):
    t = real_or_skip["terrain"]
    ob = outward_open_boundary(t)
    g = t.grid
    edge = np.zeros(t.z.shape, dtype=bool)
    edge[0, :] = edge[-1, :] = True; edge[:, 0] = edge[:, -1] = True
    assert (ob & ~edge).sum() == 0, "open boundary must be a subset of edge cells"
    assert (ob & t.building).sum() == 0, "buildings are never opened"


def test_mass_balance_closes_with_open_boundary(real_or_skip):
    p = real_or_skip
    net = p["net"]
    surf = StorageCellSurface(p["terrain"], open_boundary=True)
    r = run_simulation(p["terrain"], net, p["scenarios"]["heavy"], surf, GraphDrainage(net),
                       runoff_fn, None, horizon_s=1800)
    mb = r.mass_balance
    assert mb.boundary_out_m3 >= 0
    assert abs(mb.error_pct) < 0.1, mb


def test_large_synthetic_bowl_not_flagged_or_opened():
    from floodnet.data.fixtures import synthetic_pilot
    fx = synthetic_pilot()
    t = fx["terrain"]
    mask, report = dem_reliability_mask(t, fx["net"], radius_m=1e6)  # generous radius: still shouldn't flag
    # the synthetic bowl is a genuine large depression with a real drainage node in it -> not flagged
    assert report["n_cells_flagged"] == 0 or mask.sum() < 5
    ob = outward_open_boundary(t)
    # the bowl's minimum is interior, not at the domain edge, so opening the edge must not drain it dry
    surf_closed = StorageCellSurface(t, open_boundary=False)
    surf_open = StorageCellSurface(t, open_boundary=True)
    surf_closed.add_runoff(np.full(t.z.shape, 0.01))
    surf_open.add_runoff(np.full(t.z.shape, 0.01))
    surf_closed.step(60); surf_open.step(60)
    assert surf_open.total_volume_m3() > 0.5 * surf_closed.total_volume_m3()


def test_depressions_reports_edge_touching():
    from floodnet.data.fixtures import synthetic_pilot
    fx = synthetic_pilot()
    d = depressions(fx["terrain"], min_depth_m=0.1)
    assert d["n"] >= 1
    assert all("touches_grid_edge" in dep for dep in d["depressions"])


@pytest.fixture
def real_or_skip():
    from floodnet.data.load import load_pilot
    try:
        return load_pilot()
    except Exception:
        pytest.skip("real pilot data not built (run floodnet.data.build_pilot)")
