"""Flooding-spots validation: synthetic bowl sanity (minimum -> DETECTED, rim -> MISSED) + real hotspot smoke test."""
import numpy as np
import pytest

from floodnet.config import DATA_PROCESSED
from floodnet.data.fixtures import synthetic_pilot

fs = pytest.importorskip("floodnet.validation.flooding_spots")

HORIZON_S = 1800


def _lonlat(grid, i, j):
    from pyproj import Transformer
    inv = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    x = grid.x0 + (i + 0.5) * grid.res
    y = grid.y0 + (j + 0.5) * grid.res
    lon, lat = inv.transform(x, y)
    return [float(lon), float(lat)]


@pytest.fixture(scope="module")
def synthetic_run():
    pytest.importorskip("floodnet.terrain.runoff"); pytest.importorskip("floodnet.terrain.surface")
    pytest.importorskip("floodnet.drainage.hydraulics")
    pilot = synthetic_pilot()
    res = fs.run_pilot_scenario(pilot, "cloudburst", horizon_min=HORIZON_S // 60)
    return pilot, res


def test_bowl_minimum_detected_rim_missed(synthetic_run):
    pilot, res = synthetic_run
    g = pilot["terrain"].grid
    z = pilot["terrain"].z
    jmin, imin = np.unravel_index(np.argmin(z), z.shape)
    spots = [
        {"name": "bowl minimum", "active": True, "lonlat_centroid": _lonlat(g, int(imin), int(jmin))},
        {"name": "rim corner (Delete)", "active": False, "lonlat_centroid": _lonlat(g, 2, 2)},
        {"name": "rim corner active", "active": True, "lonlat_centroid": _lonlat(g, 2, 2)},
    ]
    rep = fs.evaluate_spots(pilot, res, spots)
    by = {s["name"]: s for s in rep["spots"]}
    assert by["bowl minimum"]["class"] == "DETECTED", by["bowl minimum"]
    assert by["rim corner active"]["class"] == "MISSED", by["rim corner active"]
    assert by["bowl minimum"]["footprint"].startswith("radius")
    assert by["bowl minimum"]["onset_min"]["15cm"] is not None
    assert by["rim corner active"]["onset_min"]["5cm"] is None
    assert by["bowl minimum"]["cell_depth_percentile"] > by["rim corner active"]["cell_depth_percentile"]
    # inactive spot is reported but not counted
    assert rep["n_active"] == 2 and rep["n_total"] == 3
    assert rep["active_counts"] == {"DETECTED": 1, "MARGINAL": 0, "MISSED": 1}
    # nearest node / road are populated
    assert by["bowl minimum"]["nearest_node"]["dist_m"] < 50
    assert by["bowl minimum"]["nearest_road"] is not None
    # base rate is a fraction and the markdown renders without error
    assert 0.0 <= rep["base_rate"]["frac_cells_ge_threshold_envelope"] <= 1.0
    md = fs.render_markdown({"generated": "test", "commit": "x", "horizon_min": 30, "runs": [rep], "chitale": None})
    assert "DETECTED" in md and "MISSED" in md


def test_polygon_footprint_used_when_present(synthetic_run):
    pilot, res = synthetic_run
    g = pilot["terrain"].grid
    z = pilot["terrain"].z
    jmin, imin = np.unravel_index(np.argmin(z), z.shape)
    ring = [_lonlat(g, int(imin) + di, int(jmin) + dj) for di, dj in ((-3, -3), (3, -3), (3, 3), (-3, 3))]
    spot = {"name": "poly", "active": True, "polygon_lonlat": ring, "lonlat_centroid": _lonlat(g, int(imin), int(jmin))}
    mask, kind = fs.footprint_mask(g, spot)
    assert kind == "polygon" and 20 <= mask.sum() <= 49
    assert mask[jmin, imin]


def test_chitale_terms_run_on_synthetic(synthetic_run):
    pilot, res = synthetic_run
    m = fs.chitale_match(pilot, res, terms=("nonexistent-road",))
    assert m[0]["class"] == "NO_ROAD_MATCH"


@pytest.mark.skipif(not (DATA_PROCESSED / "hotspots.json").exists(), reason="real pilot not built")
def test_real_hotspots_footprints_load():
    from floodnet.data.load import load_hotspots, load_terrain
    hs = load_hotspots()
    grid = load_terrain().grid
    assert len(hs) >= 1
    n_active = sum(1 for h in hs if h.get("active"))
    assert 1 <= n_active <= len(hs)
    for h in hs:
        mask, kind = fs.footprint_mask(grid, h)
        assert mask.sum() >= 1, h["name"]
        assert kind in ("polygon",) or kind.startswith("radius")
