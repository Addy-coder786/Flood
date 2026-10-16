"""Agent A tests: runoff, StorageCellSurface, DEM providers. Uses the SYNTHETIC bowl terrain (not Mumbai)."""
from __future__ import annotations

import time
import numpy as np
import pytest

from floodnet.contracts import Grid
from floodnet.provenance import Tag
from floodnet.terrain.dem_provider import SyntheticBowlProvider, LocalNPZProvider, OpenTopographyProvider
from floodnet.terrain.runoff import runoff_fn, C_IMP, C_PERV
from floodnet.terrain.surface import StorageCellSurface


def bowl(n=40, seed=1, noise=0.02, building_mask=None):
    g = Grid(0.0, 0.0, 10.0, n, n)
    t = SyntheticBowlProvider(depth_m=3.0, noise=noise, seed=seed, building_mask=building_mask).get_terrain(g)
    return g, t


def test_synthetic_provider_is_labelled():
    g, t = bowl()
    assert t.provenance.tag == Tag.SYNTHETIC and "not Mumbai" in t.provenance.note
    assert t.z.shape == (40, 40) and t.building.dtype == bool
    with pytest.raises(NotImplementedError):
        OpenTopographyProvider().get_terrain(g)


def test_mass_conservation_600s():
    g, t = bowl()
    s = StorageCellSurface(t)
    s.add_runoff(np.full((40, 40), 0.05))
    v0 = s.total_volume_m3()
    tt = 0.0
    while tt < 600:
        s.step(5.0); tt += 5.0
    assert abs(s.total_volume_m3() - v0) / v0 < 1e-6
    assert s.depth.min() >= 0.0
    assert s.infiltrated_m3() == 0.0


def test_water_moves_to_bowl_minimum():
    g, t = bowl()
    s = StorageCellSurface(t)
    s.add_runoff(np.full((40, 40), 0.05))
    jm, im = np.unravel_index(np.argmin(t.z), t.z.shape)
    d_min0, d_rim0 = s.depth[jm, im], s.depth[0, 0]
    for _ in range(120):
        s.step(5.0)
    assert s.depth[jm, im] > d_min0 + 0.1
    assert s.depth[0, 0] < d_rim0 - 0.04
    # the pond around the minimum should have a nearly flat water surface
    H = t.z + s.depth
    jj, ii = np.ogrid[:40, :40]
    pond = (np.hypot(jj - jm, ii - im) <= 4) & (s.depth > 0.05)
    assert pond.sum() > 10 and np.ptp(H[pond]) < 0.1


def test_building_wall_blocks_flow():
    n = 40
    mask = np.zeros((n, n), dtype=bool)
    mask[:, 20] = True                      # full-height wall down column 20
    g, t = bowl(n, building_mask=mask)
    s = StorageCellSurface(t)
    r = np.zeros((n, n)); r[:, :20] = 0.1   # water only on the west side
    s.add_runoff(r)
    v0 = s.total_volume_m3()
    for _ in range(120):
        s.step(5.0)
    assert s.depth[:, 21:].max() == 0.0     # nothing crossed the wall
    assert s.depth[:, 20].max() == 0.0      # nothing on the wall
    assert abs(s.total_volume_m3() - v0) / v0 < 1e-6


def test_take_volume_never_exceeds_available_and_add_volume_sums_duplicates():
    g, t = bowl()
    s = StorageCellSurface(t)
    s.add_volume(np.array([5, 5, 7]), np.array([5, 5, 9]), np.array([10.0, 10.0, 3.0]))
    assert np.isclose(s.depth[5, 5], 20.0 / g.cell_area) and np.isclose(s.depth[7, 9], 3.0 / g.cell_area)
    # request more than available, incl. duplicates in the same cell and an empty cell
    j = np.array([5, 5, 7, 1]); i = np.array([5, 5, 9, 1])
    req = np.array([15.0, 15.0, 100.0, 5.0])
    got = s.take_volume(j, i, req)
    assert np.all(got <= req + 1e-12)
    assert np.isclose(got[0] + got[1], 20.0) and np.isclose(got[2], 3.0) and got[3] == 0.0
    assert s.depth.min() >= 0.0 and np.isclose(s.total_volume_m3(), 0.0)


def test_runoff_zero_on_buildings_and_total_volume():
    n = 40
    mask = np.zeros((n, n), dtype=bool); mask[10:15, 10:15] = True
    g, t = bowl(n, building_mask=mask)
    r = runoff_fn(36.0, 100.0, t)           # 36 mm/h for 100 s -> 1 mm rain depth
    d = 36.0 / 1000 / 3600 * 100.0
    assert r.shape == (n, n) and np.all(r[mask] == 0.0)
    n_b = mask.sum(); n_o = n * n - n_b
    imp = float(t.impervious[0, 0])
    expected = d * (imp * C_IMP + (1 - imp) * C_PERV) * n_o + d * C_IMP * n_b
    assert np.isclose(r.sum(), expected, rtol=1e-9)
    assert np.isclose(r.sum() * g.cell_area, expected * g.cell_area)
    # roof runoff lands adjacent to the block, not far away
    assert r[9, 12] > r[0, 0] and r[15, 12] > r[0, 0]
    assert np.all(runoff_fn(0.0, 5.0, t) == 0.0)


def test_local_npz_provider_roundtrip(tmp_path):
    import json
    g, t = bowl()
    np.savez(tmp_path / "terrain.npz", z=t.z, impervious=t.impervious, building=t.building)
    (tmp_path / "terrain.json").write_text(json.dumps({"grid": g.to_dict(),
                                                        "provenance": {"tag": "SYNTHETIC", "source": "x", "note": "y"}}))
    t2 = LocalNPZProvider(tmp_path / "terrain.npz", tmp_path / "terrain.json").get_terrain(g)
    assert np.array_equal(t2.z, t.z) and t2.provenance.tag == Tag.SYNTHETIC and t2.grid == g


def test_infiltration_accumulates():
    g, t = bowl()
    s = StorageCellSurface(t, infiltration_rate_mm_h=36.0)   # 1 mm / 100 s on pervious 40%
    s.add_runoff(np.full((40, 40), 0.05))
    v0 = s.total_volume_m3()
    for _ in range(20):
        s.step(5.0)
    assert s.infiltrated_m3() > 0
    assert abs(v0 - s.total_volume_m3() - s.infiltrated_m3()) / v0 < 1e-6


def test_timing_pilot_size_grid():
    """220x210 grid, 3 h simulated with 5 s outer steps; prints wall-clock (target < 90 s, not asserted)."""
    g = Grid(0.0, 0.0, 10.0, 210, 220)
    t = SyntheticBowlProvider(depth_m=5.0, noise=0.05, seed=3).get_terrain(g)
    s = StorageCellSurface(t)
    t0 = time.time()
    tt = 0.0
    rain_in = 0.0
    while tt < 3 * 3600:
        i_mm_h = 60.0 if tt < 3600 else 0.0
        r = runoff_fn(i_mm_h, 5.0, t)
        rain_in += r.sum() * g.cell_area
        s.add_runoff(r)
        s.step(5.0)
        tt += 5.0
    wall = time.time() - t0
    print(f"\n[timing] 220x210 grid, 3 h @ dt=5 s: {wall:.1f} s wall-clock, {s.substeps} sub-steps, "
          f"max depth {s.depth.max():.2f} m")
    assert abs(s.total_volume_m3() - rain_in) / rain_in < 1e-6
    assert wall < 600
