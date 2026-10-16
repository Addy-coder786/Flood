"""SYNTHETIC tiny drainage network for tests and demos: 5-node chain + 1 branch draining to 1 outfall.

Undersized 0.3 m circular pipes so a 0.2 m3/s sustained inflow surcharges. Node cells are placed on `grid`.
Layout (row j = ny//2, spaced `step` cells apart):   N0 -> N1 -> N2 -> N3 -> N4 -> OUT ; B0 -> N2
"""
from __future__ import annotations

import numpy as np

from ..contracts import DrainageNetwork, Grid
from ..provenance import Provenance, Tag
from .attributes import full_bore_capacity_m3s, manning_n_for, inlet_capacity_default_m3s, storage_area_default_m2

PIPE_D_M = 0.3
SLOPE = 0.005
DEPTH_M = 2.0       # ground above invert at every node


def tiny_network(grid: Grid, inlet_cap_m3s: float | None = None, pipe_d_m: float = PIPE_D_M) -> DrainageNetwork:
    step = max(1, min(5, (grid.nx - 3) // 6))
    j = grid.ny // 2
    cols = [1 + k * step for k in range(6)]                 # N0..N4, OUT
    cols = [min(c, grid.nx - 1) for c in cols]
    ids = ["N0", "N1", "N2", "N3", "N4", "OUT", "B0"]
    cj = np.array([j] * 6 + [min(j + step, grid.ny - 1)], dtype=int)
    ci = np.array(cols + [cols[2]], dtype=int)
    x = grid.x0 + (ci + 0.5) * grid.res
    y = grid.y0 + (cj + 0.5) * grid.res
    # inverts fall along the chain; branch B0 sits above N2
    spacing = step * grid.res
    inv = np.array([10.0 - SLOPE * spacing * k for k in range(6)] + [10.0 - SLOPE * spacing * 2 + SLOPE * spacing], dtype=np.float32)
    ground = inv + DEPTH_M
    is_out = np.array([False] * 5 + [True, False])
    N = 7
    # edges
    us = np.array([0, 1, 2, 3, 4, 6], dtype=int)
    ds = np.array([1, 2, 3, 4, 5, 2], dtype=int)
    E = len(us)
    length = np.hypot(x[us] - x[ds], y[us] - y[ds]).astype(np.float32)
    length = np.maximum(length, 1.0)
    us_inv, ds_inv = inv[us], inv[ds]
    slope = np.maximum((us_inv - ds_inv) / length, 1e-4).astype(np.float32)
    n = np.array([manning_n_for("CIRC")] * E, dtype=np.float32)
    cap = np.array([full_bore_capacity_m3s("CIRC", pipe_d_m, pipe_d_m, float(slope[k]), float(n[k])) for k in range(E)], dtype=np.float32)
    ic = inlet_capacity_default_m3s() if inlet_cap_m3s is None else float(inlet_cap_m3s)
    prov = Provenance(Tag.SYNTHETIC, "floodnet.drainage.fixture.tiny_network", "5-node chain + branch, 0.3 m pipes, for tests").to_dict()
    return DrainageNetwork(
        node_id=np.array(ids, dtype=object), node_x=x.astype(np.float64), node_y=y.astype(np.float64),
        node_ground=ground.astype(np.float32), node_invert=inv, node_is_outfall=is_out,
        node_storage_area_m2=np.full(N, storage_area_default_m2(), dtype=np.float32),
        node_inlet_cap_m3s=np.full(N, ic, dtype=np.float32), node_cell_j=cj, node_cell_i=ci,
        edge_id=np.array([f"E{k}" for k in range(E)], dtype=object), edge_us=us, edge_ds=ds,
        edge_length_m=length, edge_shape=np.array(["CIRC"] * E, dtype=object),
        edge_width_m=np.full(E, pipe_d_m, dtype=np.float32), edge_height_m=np.full(E, pipe_d_m, dtype=np.float32),
        edge_us_invert=us_inv.astype(np.float32), edge_ds_invert=ds_inv.astype(np.float32), edge_slope=slope,
        edge_n=n, edge_capacity_m3s=cap, edge_blockage=np.zeros(E, dtype=np.float32),
        edge_status=np.array(["Existing"] * E, dtype=object),
        provenance={"geometry": prov, "roughness": prov, "blockage": {"mode": "none"}},
    )
