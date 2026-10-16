"""Sanity checks on data/processed/pilot/ (skipped when it has not been built)."""
import numpy as np
import pytest

from floodnet.config import DATA_PROCESSED
from floodnet.data.load import load_pilot, REQUIRED

pytestmark = pytest.mark.skipif(
    not all((DATA_PROCESSED / f).exists() for f in REQUIRED),
    reason="processed pilot data not built (run floodnet.data.build_pilot)")


@pytest.fixture(scope="module")
def pilot():
    return load_pilot()


def test_counts_positive(pilot):
    assert pilot["net"].n_nodes > 0 and pilot["net"].n_edges > 0
    assert pilot["terrain"].z.size > 0
    assert len(pilot["scenarios"]) >= 4


def test_terrain_no_nan(pilot):
    t = pilot["terrain"]
    assert not np.isnan(t.z).any()
    assert t.z.shape == (t.grid.ny, t.grid.nx) == t.impervious.shape == t.building.shape
    assert t.impervious.min() >= 0 and t.impervious.max() <= 1


def test_edges_valid(pilot):
    n = pilot["net"]
    assert n.edge_us.min() >= 0 and n.edge_us.max() < n.n_nodes
    assert n.edge_ds.min() >= 0 and n.edge_ds.max() < n.n_nodes
    assert (n.edge_length_m > 0).all() and (n.edge_slope > 0).all() and (n.edge_capacity_m3s > 0).all()


def test_outfalls_are_sinks(pilot):
    n = pilot["net"]
    outdeg = np.bincount(n.edge_us, minlength=n.n_nodes)
    assert (outdeg[n.node_is_outfall] == 0).all()
    assert (outdeg[~n.node_is_outfall] > 0).all()
    assert n.node_is_outfall.any()


def test_provenance_labels(pilot):
    n = pilot["net"]
    assert n.provenance["geometry"]["tag"] == "REAL"
    assert n.provenance["roughness"]["tag"] == "ESTIMATED"
    assert pilot["scenarios"]["july2005"].provenance.tag.value == "REAL"
    assert pilot["scenarios"]["cloudburst"].provenance.tag.value == "SYNTHETIC"
