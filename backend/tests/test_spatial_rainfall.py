"""Spatial ([T, ny, nx]) rainfall: contract, regridding, runoff, engine coupling, providers.

These tests exist because SR-01 (gridded, high-resolution rainfall) was structurally impossible before this
capability: `RainfallScenario` could only carry one number per timestep, so rainfall was spatially uniform
for every provider, radar or otherwise (decision D-15). The two things that most need guarding are:

  1. **Backward compatibility.** A uniform scenario must behave EXACTLY as before -- same code path, same
     numbers. Several tests below assert that directly.
  2. **Honesty.** A field derived from a source too coarse to resolve the pilot must SAY so rather than
     looking like a resolved field. `describe_effective_resolution()` is asserted on, not just the shape.
"""
import numpy as np
import pytest

from floodnet.contracts import Grid, RainfallScenario
from floodnet.data.fixtures import synthetic_pilot
from floodnet.provenance import Provenance, Tag
from floodnet.rainfall import gridded
from floodnet.rainfall.provider import (IMERGSatelliteProvider, SyntheticSpatialProvider,
                                        ProviderUnavailable, IMERG_ID, SPATIAL_SYNTHETIC_ID)
from floodnet.simulation.engine import run_simulation
from floodnet.terrain.runoff import runoff_fn


@pytest.fixture(scope="module")
def pilot():
    return synthetic_pilot()


def _prov(tag=Tag.SYNTHETIC):
    return Provenance(tag, "unit test", "invented for testing")


# ------------------------------------------------------------------ contract: uniform mode unchanged
def test_uniform_scenario_is_not_spatial(pilot):
    sc = pilot["scenarios"][0]
    assert sc.is_spatial is False
    assert sc.intensity_field_at(0.0) is None
    assert sc.intensity_field_at(1e9) is None
    assert sc.to_dict()["spatial"] is False
    # the historic key set must still be present -- other code and the frontend depend on it
    assert {"id", "name", "description", "t_min", "intensity_mm_h", "total_mm", "provenance"} <= set(sc.to_dict())


def test_uniform_intensity_at_matches_legacy_lookup(pilot):
    """intensity_at() was reimplemented via _index_at(); it must be exactly the old searchsorted behaviour."""
    sc = pilot["scenarios"][0]

    def legacy(t):
        k = int(np.searchsorted(sc.t_s, t, side="right") - 1)
        if k < 0 or k >= len(sc.intensity_mm_h):
            return 0.0
        return float(sc.intensity_mm_h[k])

    for t in np.arange(-100.0, 8000.0, 13.7):
        assert sc.intensity_at(float(t)) == legacy(float(t))


# ------------------------------------------------------------------ contract: spatial mode validation
def test_field_and_grid_must_be_set_together():
    g = Grid(x0=0.0, y0=0.0, res=10.0, nx=3, ny=2)
    t = np.array([0.0, 300.0])
    with pytest.raises(ValueError, match="must be set together"):
        RainfallScenario(id="x", name="x", t_s=t, intensity_mm_h=np.zeros(2), provenance=_prov(),
                         intensity_field_mm_h=np.zeros((2, 2, 3)))
    with pytest.raises(ValueError, match="must be set together"):
        RainfallScenario(id="x", name="x", t_s=t, intensity_mm_h=np.zeros(2), provenance=_prov(), field_grid=g)


def test_field_shape_is_validated():
    g = Grid(x0=0.0, y0=0.0, res=10.0, nx=3, ny=2)
    t = np.array([0.0, 300.0])
    with pytest.raises(ValueError, match=r"\[T, ny, nx\]"):
        RainfallScenario(id="x", name="x", t_s=t, intensity_mm_h=np.zeros(2), provenance=_prov(),
                         intensity_field_mm_h=np.zeros((2, 3)), field_grid=g)
    with pytest.raises(ValueError, match="timesteps"):
        RainfallScenario(id="x", name="x", t_s=t, intensity_mm_h=np.zeros(2), provenance=_prov(),
                         intensity_field_mm_h=np.zeros((5, 2, 3)), field_grid=g)
    with pytest.raises(ValueError, match="field_grid"):
        RainfallScenario(id="x", name="x", t_s=t, intensity_mm_h=np.zeros(2), provenance=_prov(),
                         intensity_field_mm_h=np.zeros((2, 9, 9)), field_grid=g)


def test_spatial_scenario_exposes_field_and_area_mean():
    g = Grid(x0=0.0, y0=0.0, res=10.0, nx=2, ny=2)
    t = np.array([0.0, 300.0])
    field = np.array([[[0.0, 10.0], [20.0, 30.0]],
                      [[4.0, 4.0], [4.0, 4.0]]])
    sc = RainfallScenario(id="s", name="s", t_s=t, intensity_mm_h=field.reshape(2, -1).mean(axis=1),
                          provenance=_prov(), intensity_field_mm_h=field, field_grid=g)
    assert sc.is_spatial is True
    np.testing.assert_allclose(sc.intensity_field_at(0.0), field[0])
    np.testing.assert_allclose(sc.intensity_field_at(400.0), field[1])
    assert sc.intensity_at(0.0) == pytest.approx(15.0)      # area mean of frame 0
    d = sc.to_dict()
    assert d["spatial"] is True and d["field"]["shape"] == [2, 2, 2]
    assert d["field"]["peak_cell_mm_h"] == pytest.approx(30.0)
    assert "AREA MEAN" in d["field"]["note"]


def test_returned_field_is_read_only():
    """The engine gets a view, not a copy, so it must not be able to corrupt the stored field."""
    g = Grid(x0=0.0, y0=0.0, res=10.0, nx=2, ny=2)
    field = np.ones((1, 2, 2))
    sc = RainfallScenario(id="s", name="s", t_s=np.array([0.0]), intensity_mm_h=np.array([1.0]),
                          provenance=_prov(), intensity_field_mm_h=field, field_grid=g)
    view = sc.intensity_field_at(0.0)
    with pytest.raises(ValueError):
        view[0, 0] = 999.0


# ------------------------------------------------------------------ runoff
def test_uniform_field_reproduces_scalar_runoff(pilot):
    """A constant field must give the same runoff as the scalar path -- this is the backward-compat anchor."""
    terr = pilot["terrain"]
    scalar = runoff_fn(50.0, 5.0, terr)
    field = runoff_fn(np.full((terr.grid.ny, terr.grid.nx), 50.0), 5.0, terr)
    assert float(scalar.sum()) == pytest.approx(float(field.sum()), rel=1e-12)
    np.testing.assert_allclose(scalar, field, atol=1e-15)


def test_spatial_runoff_actually_varies(pilot):
    terr = pilot["terrain"]
    ny, nx = terr.grid.ny, terr.grid.nx
    field = np.zeros((ny, nx))
    field[:, nx // 2:] = 100.0            # rain on the eastern half only
    r = runoff_fn(field, 60.0, terr)
    west, east = r[:, : nx // 2], r[:, nx // 2:]
    assert float(east.sum()) > 0.0
    assert float(west.sum()) == pytest.approx(0.0, abs=1e-12) or float(east.sum()) > 10 * float(west.sum())


def test_runoff_rejects_wrong_shaped_field(pilot):
    with pytest.raises(ValueError, match="does not match terrain grid"):
        runoff_fn(np.ones((3, 3)), 5.0, pilot["terrain"])


def test_zero_field_returns_zero_runoff(pilot):
    r = runoff_fn(np.zeros((pilot["terrain"].grid.ny, pilot["terrain"].grid.nx)), 5.0, pilot["terrain"])
    assert float(np.abs(r).max()) == 0.0


# ------------------------------------------------------------------ regridding / honesty
def test_resample_preserves_a_uniform_source(pilot):
    g = pilot["terrain"].grid
    lon_c, lat_c = gridded.model_cell_lonlat(g)
    lons = np.linspace(lon_c.min() - 0.05, lon_c.max() + 0.05, 5)
    lats = np.linspace(lat_c.min() - 0.05, lat_c.max() + 0.05, 4)
    out = gridded.resample_to_model_grid(np.full((4, 5), 7.5), lons, lats, g)
    assert out.shape == (g.ny, g.nx)
    np.testing.assert_allclose(out, 7.5, rtol=1e-9)


def test_resample_is_never_negative(pilot):
    g = pilot["terrain"].grid
    lon_c, lat_c = gridded.model_cell_lonlat(g)
    lons = np.linspace(lon_c.min(), lon_c.max(), 4)
    lats = np.linspace(lat_c.min(), lat_c.max(), 4)
    src = np.array([[-3.0, 0.0, 2.0, 5.0]] * 4)      # a source with fill/no-data negatives
    assert float(gridded.resample_to_model_grid(src, lons, lats, g).min()) >= 0.0


def test_coarse_source_is_reported_as_not_resolving(pilot):
    """A 0.1 deg source (IMERG-like) over a sub-3 km pilot must be flagged as NOT resolving it.

    This is the honesty guard: a resampled field from such a source looks like a field but carries no
    intra-pilot information, and the code must say so rather than letting it pass as resolved."""
    g = pilot["terrain"].grid
    lon_c, lat_c = gridded.model_cell_lonlat(g)
    lons = np.arange(lon_c.min() - 0.2, lon_c.max() + 0.2, 0.1)
    lats = np.arange(lat_c.min() - 0.2, lat_c.max() + 0.2, 0.1)
    eff = gridded.describe_effective_resolution(lons, lats, g)
    assert eff["resolves_within_pilot"] is False
    assert eff["pilot_spans_source_pixels"][0] < 1.0

    fine_lons = np.arange(lon_c.min() - 0.01, lon_c.max() + 0.01, 0.001)   # ~100 m
    fine_lats = np.arange(lat_c.min() - 0.01, lat_c.max() + 0.01, 0.001)
    assert gridded.describe_effective_resolution(fine_lons, fine_lats, g)["resolves_within_pilot"] is True


def test_scenario_from_gridded_records_resolution_honesty(pilot):
    g = pilot["terrain"].grid
    lon_c, lat_c = gridded.model_cell_lonlat(g)
    lons = np.arange(lon_c.min() - 0.2, lon_c.max() + 0.2, 0.1)
    lats = np.arange(lat_c.min() - 0.2, lat_c.max() + 0.2, 0.1)
    fields = [np.full((lats.size, lons.size), 12.0)]
    sc = gridded.scenario_from_gridded("t", "t", np.array([0.0]), fields, lons, lats, g, _prov(Tag.REAL))
    assert sc.is_spatial
    assert "CANNOT resolve structure inside the pilot" in sc.provenance.note
    assert sc.intensity_mm_h[0] == pytest.approx(12.0)


# ------------------------------------------------------------------ synthetic spatial storm
def test_synthetic_moving_storm_is_labelled_synthetic_and_varies(pilot):
    g = pilot["terrain"].grid
    sc = gridded.synthetic_moving_storm(g, horizon_s=1800)
    assert sc.is_spatial and sc.provenance.tag == Tag.SYNTHETIC
    note = sc.provenance.note.lower()
    assert "not radar" in note and "not observed" in note and "not a nowcast" in note
    f = sc.intensity_field_at(900.0)
    assert float(f.max()) > float(f.min())          # genuinely varies in space
    assert sc.intensity_field_mm_h.shape == (len(sc.t_s), g.ny, g.nx)


def test_synthetic_storm_actually_moves(pilot):
    """The cell's centre of mass must track east (bearing 90) over time.

    Parameters are scaled to this 600 m x 600 m fixture domain deliberately: the function's real-pilot
    defaults (sigma 700 m at 4 m/s) describe a cell wider than this fixture that crosses and fully exits it
    within a few hundred seconds, which would leave an all-but-empty field to take a centroid of.
    """
    g = pilot["terrain"].grid
    sc = gridded.synthetic_moving_storm(g, horizon_s=1200, dt_s=60, sigma_m=150.0, speed_m_s=1.0)

    xs = np.arange(g.nx)

    def centroid_x(field):
        total = float(field.sum())
        assert total > 1e-6, "field is empty at this time; test times must bracket the cell's passage"
        return float((field.sum(axis=0) * xs).sum() / total)

    cx_early = centroid_x(sc.intensity_field_at(200.0))
    cx_late = centroid_x(sc.intensity_field_at(700.0))
    assert cx_late > cx_early, f"cell did not track east: {cx_early:.2f} -> {cx_late:.2f}"


# ------------------------------------------------------------------ engine coupling
def test_engine_rejects_field_on_a_mismatched_grid(pilot):
    from floodnet.terrain.surface import StorageCellSurface
    from floodnet.drainage.hydraulics import GraphDrainage
    terr, net = pilot["terrain"], pilot["net"]
    wrong = Grid(x0=terr.grid.x0, y0=terr.grid.y0, res=terr.grid.res, nx=terr.grid.nx + 1, ny=terr.grid.ny)
    sc = RainfallScenario(id="bad", name="bad", t_s=np.array([0.0]), intensity_mm_h=np.array([5.0]),
                          provenance=_prov(), intensity_field_mm_h=np.full((1, wrong.ny, wrong.nx), 5.0),
                          field_grid=wrong)
    with pytest.raises(ValueError, match="does not match the terrain grid"):
        run_simulation(terr, net, sc, StorageCellSurface(terr), GraphDrainage(net), runoff_fn,
                       horizon_s=300, frame_dt_s=300)


def test_spatial_run_produces_spatially_asymmetric_flooding(pilot):
    """End-to-end: rain on one half of the domain must flood that half more than the other.

    This is the real proof that the field reaches the physics rather than being averaged away somewhere."""
    from floodnet.terrain.surface import StorageCellSurface
    from floodnet.drainage.hydraulics import GraphDrainage
    terr, net = pilot["terrain"], pilot["net"]
    g = terr.grid
    t_s = np.arange(0, 1801, 300, dtype=float)
    field = np.zeros((len(t_s), g.ny, g.nx))
    field[:, :, : g.nx // 2] = 80.0                 # rain only on the WESTERN half
    sc = RainfallScenario(id="half", name="half", t_s=t_s,
                          intensity_mm_h=field.reshape(len(t_s), -1).mean(axis=1), provenance=_prov(),
                          intensity_field_mm_h=field, field_grid=g)
    r = run_simulation(terr, net, sc, StorageCellSurface(terr), GraphDrainage(net), runoff_fn,
                       horizon_s=1800, frame_dt_s=300)
    last = r.frames[-1].depth
    west = float(last[:, : g.nx // 2].sum())
    east = float(last[:, g.nx // 2:].sum())
    assert west > east, f"rain fell on the west but east={east:.4f} >= west={west:.4f}"
    assert abs(r.mass_balance.error_pct) < 0.1      # spatial path must still conserve mass


def test_spatial_and_equivalent_uniform_conserve_mass_alike(pilot):
    from floodnet.terrain.surface import StorageCellSurface
    from floodnet.drainage.hydraulics import GraphDrainage
    terr, net = pilot["terrain"], pilot["net"]
    g = terr.grid
    t_s = np.arange(0, 1201, 300, dtype=float)
    const = 30.0
    field = np.full((len(t_s), g.ny, g.nx), const)
    spatial = RainfallScenario(id="sp", name="sp", t_s=t_s, intensity_mm_h=np.full(len(t_s), const),
                               provenance=_prov(), intensity_field_mm_h=field, field_grid=g)
    uniform = RainfallScenario(id="un", name="un", t_s=t_s, intensity_mm_h=np.full(len(t_s), const),
                               provenance=_prov())
    outs = []
    for sc in (spatial, uniform):
        r = run_simulation(terr, net, sc, StorageCellSurface(terr), GraphDrainage(net), runoff_fn,
                           horizon_s=1200, frame_dt_s=300)
        outs.append(r)
    # a constant field and the equivalent scalar must agree closely (same physics, different summation order)
    assert outs[0].mass_balance.rain_in_m3 == pytest.approx(outs[1].mass_balance.rain_in_m3, rel=1e-9)
    assert float(outs[0].frames[-1].depth.sum()) == pytest.approx(float(outs[1].frames[-1].depth.sum()), rel=1e-6)


# ------------------------------------------------------------------ providers
def test_imerg_refuses_without_credentials(monkeypatch):
    monkeypatch.delenv(IMERGSatelliteProvider.TOKEN_ENV, raising=False)
    p = IMERGSatelliteProvider()
    assert p.is_configured() is False
    assert "Earthdata" in p.unavailable_reason()
    with pytest.raises(ProviderUnavailable):
        p.get()


def test_imerg_never_claims_to_be_ground_radar():
    """Guard against the single most dangerous mislabelling this provider could acquire."""
    doc = IMERGSatelliteProvider.__doc__
    assert "NOT ground radar" in doc and "NOT a radar nowcast" in doc


def test_imerg_requires_a_grid_even_when_configured(monkeypatch):
    monkeypatch.setenv(IMERGSatelliteProvider.TOKEN_ENV, "dummy-token-not-used")
    p = IMERGSatelliteProvider()
    try:
        import h5py  # noqa: F401
    except ImportError:
        pytest.skip("h5py not installed; provider correctly reports that instead of the grid requirement")
    with pytest.raises(ProviderUnavailable, match="grid"):
        p.get()


def test_synthetic_spatial_provider_requires_grid():
    with pytest.raises(ProviderUnavailable, match="grid"):
        SyntheticSpatialProvider().get()


def test_synthetic_spatial_provider_returns_labelled_field(pilot):
    scen, meta = SyntheticSpatialProvider().get(grid=pilot["terrain"].grid, horizon_s=1800)
    assert scen.is_spatial
    assert meta.data_mode == "SYNTHETIC"
    assert meta.detail["is_radar"] is False and meta.detail["is_observed"] is False
    assert meta.detail["spatial_field"] is True


def test_gridded_ids_are_registered():
    from floodnet.rainfall.provider import list_providers
    ids = list_providers()
    assert IMERG_ID in ids and SPATIAL_SYNTHETIC_ID in ids
