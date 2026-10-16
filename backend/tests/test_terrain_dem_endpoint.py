"""GET /api/terrain/dem -- the 3D terrain view must draw the SAME DEM the flood solver runs on, losslessly."""
from __future__ import annotations

import base64
import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from floodnet import config
from floodnet.api import state
from floodnet.api.main import app, dem_payload
from floodnet.contracts import Grid
from floodnet.data.load import REQUIRED

REAL_PILOT_BUILT = all((config.DATA_PROCESSED / f).exists() for f in REQUIRED)
needs_pilot = pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
client = TestClient(app)


def _decode(d: dict) -> np.ndarray:
    return np.frombuffer(base64.b64decode(d["z_base64"]), dtype="<f4").reshape(d["shape"])


@needs_pilot
def test_endpoint_returns_the_simulators_own_dem_bit_for_bit():
    r = client.get("/api/terrain/dem")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    d = r.json()
    model_z = state.get_pilot()["terrain"].z                     # the array run_simulation() is given
    z = _decode(d)
    assert z.shape == model_z.shape == (d["grid"]["ny"], d["grid"]["nx"])
    assert np.array_equal(z, np.asarray(model_z, dtype="<f4"))    # round-trip tolerance: exactly 0
    assert d["sha256"] == hashlib.sha256(np.ascontiguousarray(model_z, dtype="<f4").tobytes()).hexdigest()
    # and it is the file the pilot loads, not a second dataset
    on_disk = np.load(config.DATA_PROCESSED / "terrain.npz")["z"]
    assert np.array_equal(z, on_disk.astype("<f4"))
    assert d["provenance"]["tag"] == "REAL" and "MCGM" in d["provenance"]["source"]
    assert d["units"] == "m" and "mTHD" in d["datum"] and "unverified" in d["datum"]
    assert d["row0"] == "south" and d["dtype"] == "float32" and d["byte_order"] == "little"


@needs_pilot
def test_bounds_are_the_model_grids_and_match_the_flood_overlay():
    d = client.get("/api/terrain/dem").json()
    g = state.get_pilot()["terrain"].grid
    assert d["grid"] == g.to_dict()
    assert d["bbox_lonlat"] == state.grid_bbox_lonlat(g) == client.get("/api/terrain").json()["bbox_lonlat"]
    w, s, e, n = d["bbox_lonlat"]
    pw, ps, pe, pn = config.PILOT_BBOX_LONLAT
    assert w <= pw and s <= ps and e >= pe and n >= pn            # the pilot box lies inside the DEM
    assert d["z_min"] == pytest.approx(float(np.min(_decode(d)))) and d["z_max"] == pytest.approx(float(np.max(_decode(d))))


@needs_pilot
def test_lonlat_affine_places_overlays_within_a_cell_fraction():
    d = client.get("/api/terrain/dem").json()
    g = state.get_pilot()["terrain"].grid
    a = d["lonlat_to_grid"]
    assert a["max_residual_m"] < 0.5                              # << the 10 m cell
    rng = np.random.default_rng(7)
    gx, gy = rng.uniform(0, g.nx * g.res, 50), rng.uniform(0, g.ny * g.res, 50)
    lon, lat = state.xy_to_lonlat(g.x0 + gx, g.y0 + gy)
    px = a["x"][0] * lon + a["x"][1] * lat + a["x"][2]
    py = a["y"][0] * lon + a["y"][1] * lat + a["y"][2]
    assert float(np.max(np.hypot(px - gx, py - gy))) < 0.5


@needs_pilot
def test_forecast_frames_change_water_not_terrain():
    before = client.get("/api/terrain/dem").json()["sha256"]
    run = client.post("/api/simulate", json={"scenario_id": "heavy", "horizon_min": 30}).json()
    grids = []
    for t in (0, 15, 30):
        f = client.get(f"/api/simulation/{run['run_id']}/frame/{t}").json()
        assert f["depth_grid"]["grid"] == client.get("/api/terrain/dem").json()["grid"]   # 1 pixel == 1 DEM cell
        grids.append(f["depth_grid"]["png_base64"])
    assert len(set(grids)) > 1                                    # the water overlay evolves ...
    assert client.get("/api/terrain/dem").json()["sha256"] == before   # ... the terrain does not


def test_nodata_is_transmitted_as_nan_and_counted_never_filled():
    class T:  # minimal stand-in for contracts.Terrain
        pass
    t = T()
    t.grid = Grid(x0=272000.0, y0=2103000.0, res=10.0, nx=3, ny=2)
    t.z = np.array([[1.0, np.nan, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
    t.building = np.zeros((2, 3), bool)
    from floodnet.provenance import Provenance, Tag
    t.provenance = Provenance(Tag.SYNTHETIC, "unit-test grid", "not terrain data")
    d = dem_payload(t)
    z = _decode(d)
    assert d["nodata_count"] == 1 and np.isnan(z[0, 1])
    assert d["z_min"] == 1.0 and d["z_max"] == 6.0
    assert np.array_equal(z[np.isfinite(z)], t.z[np.isfinite(t.z)])
