import numpy as np
import pytest

from floodnet.contracts import Grid
from floodnet.drainage.fixture import tiny_network
from floodnet.drainage.hydraulics import GraphDrainage
from floodnet.drainage.scenarios import apply_blockage
from floodnet.drainage.attributes import full_bore_capacity_m3s, manning_n_for, refine_attributes

GRID = Grid(x0=0.0, y0=0.0, res=10.0, nx=40, ny=20)
DT = 5.0


def run(net, q_in=0.2, steps=360, node=0):
    model = GraphDrainage(net)
    total_in = 0.0
    surch = np.zeros(net.n_nodes)
    for _ in range(steps):
        cap = model.inlet_capacity_m3(DT)
        v = np.zeros(net.n_nodes)
        v[node] = q_in * DT           # bypass inlet limit: sustained forced inflow
        model.add_inflow(v)
        total_in += v.sum()
        surch += model.step(DT)
    return model, total_in, surch


def test_mass_conservation():
    net = tiny_network(GRID)
    model, total_in, surch = run(net)
    residual = total_in - (model.stored_m3() + surch.sum() + model.outflow_m3())
    assert abs(residual) < 1e-6
    assert abs(model.mass_error_m3()) < 1e-6


def test_undersized_chain_surcharges():
    net = tiny_network(GRID)
    assert float(net.edge_capacity_m3s.max()) < 0.2
    model, _, surch = run(net, q_in=0.2)
    assert surch.sum() > 0
    assert model.surcharging()[0]


def test_outfall_accumulates_outflow():
    net = tiny_network(GRID)
    model, _, _ = run(net, q_in=0.02, steps=400)
    assert model.outflow_m3() > 0
    assert model.V[net.node_is_outfall].sum() == 0.0


def test_blockage_increases_surcharge_and_cause():
    net = tiny_network(GRID)
    _, _, s_clear = run(net, q_in=0.1)
    blocked = apply_blockage(net, {"mode": "fraction", "fraction": 0.8})
    assert blocked is not net and np.all(blocked.edge_blockage == pytest.approx(0.8)) and np.all(net.edge_blockage == 0)
    assert blocked.provenance["blockage"]["tag"] == "SYNTHETIC"
    model_b, _, s_blocked = run(blocked, q_in=0.1)
    assert s_blocked.sum() > s_clear.sum()
    assert model_b.cause()[0] == "blockage"


def test_cause_overcapacity_and_downstream():
    net = tiny_network(GRID)
    model, _, _ = run(net, q_in=0.2)
    assert model.cause()[0] in ("overcapacity", "downstream")
    # fill node 1 fully so node 0 is limited by downstream free volume
    m2 = GraphDrainage(net)
    v = np.zeros(net.n_nodes); v[1] = m2.vfull[1] * 5; v[0] = m2.vfull[0] * 2
    m2.add_inflow(v); m2.step(DT)
    m2.add_inflow(v); m2.step(DT)
    assert m2.cause()[0] == "downstream"


def test_inlet_capacity_zero_at_outfalls():
    net = tiny_network(GRID)
    model = GraphDrainage(net)
    cap = model.inlet_capacity_m3(DT)
    assert np.all(cap[net.node_is_outfall] == 0)
    assert np.all(cap[~net.node_is_outfall] > 0)
    assert np.all(cap[~net.node_is_outfall] <= net.node_inlet_cap_m3s[~net.node_is_outfall].astype(np.float64) * DT + 1e-6)


def test_blockage_modes():
    net = tiny_network(GRID)
    e = apply_blockage(net, {"mode": "edges", "edge_ids": ["E0", "E5"], "fraction": 0.9})
    assert e.edge_blockage[0] == pytest.approx(0.9) and e.edge_blockage[1] == 0
    r = apply_blockage(net, {"mode": "random", "fraction": 0.6, "share": 0.5, "seed": 1})
    assert int((r.edge_blockage > 0).sum()) == 3
    n = apply_blockage(net, {"mode": "none"})
    assert n.edge_blockage.sum() == 0


def test_manning_and_refine():
    q = full_bore_capacity_m3s("CIRC", 0.3, 0.3, 0.005, 0.013)
    assert q == pytest.approx(0.0684, rel=0.02)
    assert manning_n_for("RECT") == 0.013 and manning_n_for("ARCH") == 0.015
    net = tiny_network(GRID)
    r = refine_attributes(net)
    assert r.provenance["roughness"]["tag"] == "ESTIMATED"
    assert np.allclose(r.edge_capacity_m3s, net.edge_capacity_m3s, rtol=1e-4)


def test_edge_util_bounded():
    net = tiny_network(GRID)
    model, _, _ = run(net, q_in=0.2, steps=50)
    u = model.edge_util()
    assert np.all(u >= 0) and np.all(u <= 1 + 1e-9)
    assert model.edge_flow_m3s().shape == (net.n_edges,)
