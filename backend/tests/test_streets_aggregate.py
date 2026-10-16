import numpy as np
import pytest

from floodnet.data.fixtures import synthetic_pilot
from floodnet.streets.aggregate import make_street_fn, severity, passable, streets_geojson
from floodnet.config import SEVERITY_BANDS_CM, VEHICLE_LIMIT_CM


@pytest.fixture(scope="module")
def pilot():
    return synthetic_pilot()


def test_flooded_cell_maps_to_its_segment(pilot):
    roads, grid = pilot["roads"], pilot["terrain"].grid
    street_fn = make_street_fn(roads, grid, buffer_m=6.0)
    seg = roads.segments[0]                                     # horizontal, y = const
    mid = seg.xy.mean(axis=0)
    j, i = grid.cell_of(np.array([mid[0]]), np.array([mid[1]]))
    depth = np.zeros((grid.ny, grid.nx), dtype=np.float32)
    depth[int(j[0]), int(i[0])] = 0.42
    out = street_fn(depth, grid)
    assert set(out) == {s.seg_id for s in roads.segments}
    assert out[seg.seg_id] == pytest.approx(0.42)
    # only segments that actually touch that cell see it
    touched = [sid for sid, v in out.items() if v > 0]
    assert seg.seg_id in touched and len(touched) <= 2
    # far-away segment stays dry
    far = roads.segments[-1]
    assert out[far.seg_id] == 0.0
    # every segment has some cells (buffer 6 m on a 10 m grid catches the centreline row)
    assert all(street_fn.cells[s.seg_id].size > 0 for s in roads.segments)


def test_no_cells_gives_zero(pilot):
    from floodnet.contracts import RoadGraph, RoadSegment
    roads, grid = pilot["roads"], pilot["terrain"].grid
    off = np.array([[grid.x0 - 500.0, grid.y0 - 500.0], [grid.x0 - 400.0, grid.y0 - 500.0]])
    seg = RoadSegment("OFF", "off-grid", "residential", -1, 0, 1, 100.0, False, off, off * 0)
    rg = RoadGraph(node_xy=off, node_lonlat=off * 0, segments=[seg], provenance=roads.provenance)
    fn = make_street_fn(rg, grid)
    assert fn(np.ones((grid.ny, grid.nx)), grid) == {"OFF": 0.0}


def test_severity_bands():
    assert severity(0) == "clear" and severity(4.9) == "clear"
    assert severity(5) == "minor" and severity(14.9) == "minor"
    assert severity(15) == "moderate" and severity(29.9) == "moderate"
    assert severity(30) == "severe" and severity(59.9) == "severe"
    assert severity(60) == "critical" and severity(200) == "critical"
    assert [b for _, b in SEVERITY_BANDS_CM] == ["clear", "minor", "moderate", "severe"]


def test_passable_thresholds():
    for v, lim in VEHICLE_LIMIT_CM.items():
        assert passable(lim - 0.1, v) and not passable(lim, v) and not passable(lim + 10, v)
    with pytest.raises(KeyError):
        passable(1, "hovercraft")


def test_streets_geojson(pilot):
    roads = pilot["roads"]
    sid = roads.segments[3].seg_id
    fc = streets_geojson(roads, {sid: 0.35})
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 40
    f = next(f for f in fc["features"] if f["properties"]["seg_id"] == sid)
    p = f["properties"]
    assert p["depth_cm"] == 35.0 and p["severity"] == "severe" and p["passable_car"] is False and p["passable_ambulance"] is True
    assert f["geometry"]["type"] == "LineString" and 72 < f["geometry"]["coordinates"][0][0] < 73
    dry = fc["features"][0]["properties"]
    assert dry["depth_cm"] == 0.0 and dry["severity"] == "clear" and dry["passable_car"] is True
    assert fc["provenance"]["roads"]["tag"] == "SYNTHETIC"
