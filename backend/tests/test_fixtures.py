import numpy as np
import pytest

from floodnet.data.fixtures import synthetic_pilot
from floodnet.provenance import Tag
from floodnet.config import PILOT_BBOX_LONLAT


@pytest.fixture(scope="module")
def pilot():
    return synthetic_pilot()


def test_terrain_shapes_and_provenance(pilot):
    t = pilot["terrain"]
    assert t.z.shape == (60, 60) and t.impervious.shape == (60, 60) and t.building.shape == (60, 60)
    assert t.z.dtype == np.float32 and t.building.dtype == bool
    assert 29.5 < t.z.min() < 30.5 and t.z.max() < 37
    assert t.z[30, 30] < t.z[30, 0] and t.z[30, 30] < t.z[0, 30]        # bowl
    assert np.isclose(t.impervious.mean(), 0.8) and t.building.sum() > 0
    for p in (t.provenance, t.impervious_provenance, t.building_provenance):
        assert p.tag == Tag.SYNTHETIC and "not Mumbai" in p.note


def test_network_chain(pilot):
    net = pilot["net"]
    assert 6 <= net.n_nodes <= 8 and net.n_edges == net.n_nodes - 1
    assert net.node_is_outfall.sum() == 1 and net.node_is_outfall[-1]
    assert np.all(net.edge_width_m == np.float32(0.4))
    assert np.all(net.edge_capacity_m3s > 0) and np.all(net.edge_capacity_m3s < 0.2)
    assert np.all(net.edge_us_invert > net.edge_ds_invert)
    assert np.all(net.node_invert < net.node_ground)
    assert np.all(net.node_inlet_cap_m3s == np.float32(0.05)) and np.all(net.node_storage_area_m2 == np.float32(1.5))
    g = pilot["terrain"].grid
    assert np.all(g.inside(net.node_cell_j, net.node_cell_i))
    assert all(v["tag"] == "SYNTHETIC" for v in net.provenance.values())


def test_roads_lattice_and_lonlat(pilot):
    roads = pilot["roads"]
    assert roads.node_xy.shape == (25, 2) and roads.node_lonlat.shape == (25, 2)
    assert len(roads.segments) == 40
    assert all(s.name.startswith("Synthetic Rd") for s in roads.segments)
    assert len({s.seg_id for s in roads.segments}) == 40
    assert roads.provenance.tag == Tag.SYNTHETIC
    w, s, e, n = PILOT_BBOX_LONLAT
    lon, lat = roads.node_lonlat[:, 0], roads.node_lonlat[:, 1]
    assert (lon > w - 0.05).all() and (lon < e + 0.05).all() and (lat > s - 0.05).all() and (lat < n + 0.05).all()
    seg = roads.segments[0]
    assert seg.xy.shape == (2, 2) and seg.lonlat.shape == (2, 2) and abs(seg.length_m - 100.0) < 1e-6


def test_scenarios(pilot):
    sc = {s.id: s for s in pilot["scenarios"]}
    assert set(sc) == {"moderate", "cloudburst"}
    assert sc["moderate"].intensity_at(0) == 20 and sc["moderate"].intensity_at(7199) == 20 and sc["moderate"].intensity_at(7200) == 0
    assert sc["cloudburst"].intensity_at(0) == 120 and sc["cloudburst"].intensity_at(45 * 60) == 0
    assert all(s.provenance.tag == Tag.SYNTHETIC for s in sc.values())
    assert pilot["hotspots"] == []
