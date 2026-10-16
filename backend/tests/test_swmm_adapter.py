"""Task 4: SWMM .inp authoring must be structurally correct, independent of whether pyswmm can execute.

pyswmm 2.1.0 IS installed in this environment (backend/.venv); these tests therefore also exercise a real
run on the tiny undersized fixture. If pyswmm is ever unavailable, the run assertions skip and only the
authoring/structural checks remain -- per the task instruction, execution unavailability must never be
faked, only reported.
"""
from __future__ import annotations
import pytest

from floodnet.drainage.fixture import tiny_network
from floodnet.data.fixtures import synthetic_pilot
from floodnet.data.load import load_pilot
from floodnet.contracts import Grid
from floodnet.validation.swmm_adapter import write_inp, validate_inp, _split_multi_inlet_outfalls


def _scenario():
    from floodnet.data.scenarios import scenarios
    return scenarios()["cloudburst"]


def test_inp_valid_for_tiny_fixture(tmp_path):
    grid = Grid(x0=0.0, y0=0.0, res=10.0, nx=20, ny=20)
    net = tiny_network(grid)
    out = write_inp(net, _scenario(), tmp_path / "tiny.inp", horizon_min=60)
    problems = validate_inp(out)
    assert problems == [], problems


def test_inp_valid_for_real_pilot(tmp_path):
    try:
        pilot = load_pilot()
    except Exception:
        pytest.skip("real pilot data not built")
    net = pilot["net"]
    out = write_inp(net, pilot["scenarios"]["heavy"], tmp_path / "real.inp", horizon_min=30)
    problems = validate_inp(out)
    assert problems == [], problems


def test_multi_inlet_outfalls_split():
    pilot = load_pilot() if _has_pilot() else synthetic_pilot()
    net = pilot["net"]
    is_out_swmm, collectors = _split_multi_inlet_outfalls(net)
    # every collector's source node was a real outfall with >1 inlet, and is no longer an outfall for SWMM
    for c in collectors:
        assert net.node_is_outfall[c["node_idx"]]
        assert not is_out_swmm[c["node_idx"]]
    # SWMM's single-outfall-link rule is now satisfiable: no junction (in the SWMM sense) is an outfall
    # with >1 inlet, because every such node was reclassified
    import numpy as np
    indeg = {}
    for d in net.edge_ds:
        indeg[int(d)] = indeg.get(int(d), 0) + 1
    for i in range(net.n_nodes):
        if is_out_swmm[i]:
            assert indeg.get(i, 0) <= 1, "a node exported as a SWMM OUTFALL must have at most one inlet"


def _has_pilot() -> bool:
    try:
        load_pilot(); return True
    except Exception:
        return False


def test_pyswmm_runs_tiny_undersized_fixture(tmp_path):
    pyswmm = pytest.importorskip("pyswmm", reason="pyswmm not installed; authoring-only mode")
    grid = Grid(x0=0.0, y0=0.0, res=10.0, nx=20, ny=20)
    net = tiny_network(grid)  # deliberately undersized 0.3 m pipes
    from floodnet.data.scenarios import scenarios
    scen = scenarios()["cloudburst"]  # 120 mm/h peak -> should overwhelm 0.3 m pipes
    inp = write_inp(net, scen, tmp_path / "tiny.inp", horizon_min=60,
                    direct_inflow_c=0.9, catchment_area_ha=5.0)
    from pyswmm import Simulation, Nodes
    flooded_any = False
    with Simulation(str(inp)) as sim:
        node_ids = [n.nodeid for n in Nodes(sim)]
        nobj = [Nodes(sim)[nid] for nid in node_ids]  # index once: Nodes(sim) itself is a one-shot iterator
        for _ in sim:
            for n in nobj:
                if n.flooding > 0:
                    flooded_any = True
        assert sim.flow_routing_error < 5.0  # percent; loose bound, just checking the run didn't diverge
    assert flooded_any, "an undersized-pipe fixture under a 120 mm/h storm should flood at least one node"
