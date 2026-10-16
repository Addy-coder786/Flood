"""GraphDrainage: simplified, explainable, mass-conserving graph hydraulics implementing `DrainageModel`.

THIS IS NOT A SAINT-VENANT / SWMM DYNAMIC-WAVE SOLVER. It is a storage-node / capacity-edge scheme chosen for
speed (3 h at dt = 5 s on ~1,600 edges in seconds) and for explainability of every surcharge.

State
-----
V[N]      stored volume per node (m3).           Vfull = storage_area * (ground - invert), floored at 0.1 m depth.
HGL[N]    = invert + V / storage_area, capped at ground (a node at HGL == ground is "full").

Edge flow (per step, each edge us -> ds)
----------------------------------------
    cap_eff = edge_capacity_m3s * (1 - edge_blockage)
    head    = HGL_us - HGL_ds                              (driving head, m)
    drop    = max(edge_us_invert - edge_ds_invert, 0.01)   (design fall of the conduit, m)
    Q       = 0                                   if head <= 0     (no reverse flow in the MVP)
    Q       = cap_eff * min(1, sqrt(head / drop)) otherwise
i.e. Manning's Q ~ S^(1/2) with the HGL slope in place of the bed slope, capped at full-bore. Then Q*dt is limited
so that (a) a node cannot send more than it stores (shared pro-rata across its outgoing edges) and (b) a node cannot
receive more than its free volume Vfull - V (shared pro-rata across incoming edges) unless it is an outfall, which
accepts everything and accumulates `outflow_m3()`.

Backflow / tidal lock is NOT modelled explicitly: it is represented as a blocked (or full) downstream node which
drives head -> 0 and therefore upstream surcharge with cause "downstream".

Ordering
--------
Edges are processed level by level (vectorised per level) in topological order of the graph's condensation
(networkx). Cycles collapse into one strongly connected component and share a level, so the scheme never fails on
imperfect connectivity; edges inside a cycle simply see the state from the start of their level.

Surcharge / cause
-----------------
After routing, any V > Vfull is returned to the surface (step() output, m3 per node) and V is set to Vfull.
cause(): "blockage" if any outgoing edge has blockage > 0.2; "downstream" if every outgoing edge was limited by the
downstream node's free volume (or by zero head against a full downstream node); else "overcapacity".
"""
from __future__ import annotations

import numpy as np
import networkx as nx

from ..contracts import DrainageNetwork

MIN_DEPTH_M = 0.1          # floor for (ground - invert) so every node has some storage
MIN_STORAGE_M2 = 0.5       # floor for manhole plan area
BLOCKAGE_CAUSE_THRESHOLD = 0.2
MIN_DROP_M = 0.01


class GraphDrainage:
    """See module docstring. Construct after applying blockage scenarios (cap_eff is fixed at init)."""

    def __init__(self, net: DrainageNetwork):
        self.net = net
        N, E = net.n_nodes, net.n_edges
        self.N, self.E = N, E
        # ---- node geometry
        self.storage = np.maximum(np.nan_to_num(np.asarray(net.node_storage_area_m2, dtype=np.float64), nan=1.0), MIN_STORAGE_M2)
        self.invert = np.asarray(net.node_invert, dtype=np.float64).copy()
        ground = np.asarray(net.node_ground, dtype=np.float64)
        self.ground = np.maximum(ground, self.invert + MIN_DEPTH_M)
        self.is_outfall = np.asarray(net.node_is_outfall, dtype=bool).copy()
        self.vfull = self.storage * (self.ground - self.invert)
        self.inlet_cap = np.nan_to_num(np.asarray(net.node_inlet_cap_m3s, dtype=np.float64), nan=0.0)
        # ---- edge geometry
        self.us = np.asarray(net.edge_us, dtype=np.int64)
        self.ds = np.asarray(net.edge_ds, dtype=np.int64)
        blockage = np.clip(np.nan_to_num(np.asarray(net.edge_blockage, dtype=np.float64), nan=0.0), 0.0, 1.0)
        self.blockage = blockage
        self.cap_eff = np.maximum(np.nan_to_num(np.asarray(net.edge_capacity_m3s, dtype=np.float64), nan=0.0), 0.0) * (1.0 - blockage)
        self.drop = np.maximum(np.asarray(net.edge_us_invert, dtype=np.float64) - np.asarray(net.edge_ds_invert, dtype=np.float64), MIN_DROP_M)
        self.out_cap_sum = np.bincount(self.us, weights=self.cap_eff, minlength=N) if E else np.zeros(N)
        self.node_has_blocked_out = np.zeros(N, dtype=bool)
        if E:
            np.logical_or.at(self.node_has_blocked_out, self.us, blockage > BLOCKAGE_CAUSE_THRESHOLD)
        self.n_out = np.bincount(self.us, minlength=N) if E else np.zeros(N, dtype=int)
        # ---- topological levels (via condensation so cycles cannot break us)
        self.levels = self._edge_levels()
        # ---- state
        self.V = np.zeros(N, dtype=np.float64)
        self._q = np.zeros(E, dtype=np.float64)
        self._ds_limited = np.zeros(E, dtype=bool)
        self._surcharging = np.zeros(N, dtype=bool)
        self._cause = np.full(N, "", dtype=object)
        self._outflow = 0.0
        self._inflow_total = 0.0
        self._surcharge_total = 0.0

    # ------------------------------------------------------------------ setup
    def _edge_levels(self) -> list[np.ndarray]:
        """Groups of edge indices, ordered so every upstream node is complete before its outgoing edges run."""
        if self.E == 0:
            return []
        g = nx.DiGraph()
        g.add_nodes_from(range(self.N))
        g.add_edges_from(zip(self.us.tolist(), self.ds.tolist()))
        cond = nx.condensation(g)                       # DAG of SCCs; cond.graph["mapping"]: node -> scc id
        mapping = cond.graph["mapping"]
        scc_level = {}
        for c in nx.topological_sort(cond):
            scc_level[c] = max((scc_level[p] + 1 for p in cond.predecessors(c)), default=0)
        node_level = np.array([scc_level[mapping[n]] for n in range(self.N)], dtype=np.int64)
        edge_level = node_level[self.us]
        order = np.argsort(edge_level, kind="stable")
        lv_sorted = edge_level[order]
        bounds = np.flatnonzero(np.diff(lv_sorted)) + 1
        return [np.ascontiguousarray(chunk) for chunk in np.split(order, bounds)]

    # ------------------------------------------------------------------ protocol
    def inlet_capacity_m3(self, dt_s: float) -> np.ndarray:
        free = np.maximum(self.vfull - self.V, 0.0)
        cap = np.minimum(self.inlet_cap * dt_s, free + self.out_cap_sum * dt_s)
        cap[self.is_outfall] = 0.0
        return np.maximum(cap, 0.0)

    def add_inflow(self, volume_m3: np.ndarray) -> None:
        v = np.maximum(np.nan_to_num(np.asarray(volume_m3, dtype=np.float64), nan=0.0), 0.0)
        # anything handed to an outfall goes straight out (outfalls have zero inlet capacity, so this is defensive)
        out = v[self.is_outfall].sum()
        self._outflow += out
        self._inflow_total += v.sum()
        v = np.where(self.is_outfall, 0.0, v)
        self.V += v

    def hgl(self) -> np.ndarray:
        return np.minimum(self.invert + self.V / self.storage, self.ground)

    def step(self, dt_s: float) -> np.ndarray:
        V, storage, invert, ground, vfull = self.V, self.storage, self.invert, self.ground, self.vfull
        q = self._q
        q[:] = 0.0
        self._ds_limited[:] = False
        eps = 1e-12
        for idx in self.levels:
            us, ds = self.us[idx], self.ds[idx]
            hgl = np.minimum(invert + V / storage, ground)
            head = hgl[us] - hgl[ds]
            cap = self.cap_eff[idx]
            qe = np.where(head > 0, cap * np.minimum(1.0, np.sqrt(np.maximum(head, 0.0) / self.drop[idx])), 0.0)
            vol = qe * dt_s
            # (a) cannot send more than stored: pro-rata over outgoing edges of the same node
            demand_us = np.bincount(us, weights=vol, minlength=self.N)[us]
            f_us = np.where(demand_us > eps, np.minimum(1.0, V[us] / np.maximum(demand_us, eps)), 1.0)
            vol = vol * f_us
            # (b) cannot receive more than free volume unless outfall: pro-rata over incoming edges
            free = np.maximum(vfull - V, 0.0)
            free = np.where(self.is_outfall, np.inf, free)
            demand_ds = np.bincount(ds, weights=vol, minlength=self.N)[ds]
            f_ds = np.where(demand_ds > eps, np.minimum(1.0, free[ds] / np.maximum(demand_ds, eps)), 1.0)
            vol_final = vol * f_ds
            # transfer
            V -= np.bincount(us, weights=vol_final, minlength=self.N)
            recv = np.bincount(ds, weights=vol_final, minlength=self.N)
            out_here = recv[self.is_outfall].sum()
            recv[self.is_outfall] = 0.0
            V += recv
            self._outflow += out_here
            q[idx] = vol_final / dt_s
            ds_full = (V[ds] >= vfull[ds] * 0.999) & ~self.is_outfall[ds]
            self._ds_limited[idx] = ((f_ds < 1.0 - 1e-9) & (vol > eps)) | ((head <= 0) & ds_full)
        np.maximum(V, 0.0, out=V)
        # surcharge
        excess = np.maximum(V - vfull, 0.0)
        excess[self.is_outfall] = 0.0
        V -= excess
        self._surcharge_total += excess.sum()
        self._surcharging = excess > 1e-9
        self._update_cause()
        return excess.copy()

    def _update_cause(self) -> None:
        cause = np.full(self.N, "", dtype=object)
        s = self._surcharging
        if not s.any():
            self._cause = cause
            return
        n_ds_lim = np.bincount(self.us, weights=self._ds_limited.astype(np.float64), minlength=self.N) if self.E else np.zeros(self.N)
        all_ds = (self.n_out > 0) & (n_ds_lim >= self.n_out)
        cause[s] = "overcapacity"
        cause[s & all_ds] = "downstream"
        cause[s & self.node_has_blocked_out] = "blockage"
        self._cause = cause

    def edge_flow_m3s(self) -> np.ndarray:
        return self._q.copy()

    def edge_util(self) -> np.ndarray:
        return np.where(self.cap_eff > 0, self._q / np.maximum(self.cap_eff, 1e-12), 0.0)

    def surcharging(self) -> np.ndarray:
        return self._surcharging

    def cause(self) -> np.ndarray:
        return self._cause

    def stored_m3(self) -> float:
        return float(self.V.sum())

    def outflow_m3(self) -> float:
        return float(self._outflow)

    # ------------------------------------------------------------------ diagnostics
    def surcharged_total_m3(self) -> float:
        return float(self._surcharge_total)

    def inflow_total_m3(self) -> float:
        return float(self._inflow_total)

    def mass_error_m3(self) -> float:
        """inflow - (stored + surcharged + outflow); should be ~0."""
        return self._inflow_total - (self.stored_m3() + self._surcharge_total + self._outflow)
