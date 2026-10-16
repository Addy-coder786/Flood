"""Blockage scenarios: return a COPY of a DrainageNetwork with `edge_blockage` set. All results are SYNTHETIC inputs.

spec modes
----------
{"mode": "none"}
{"mode": "fraction", "fraction": 0.5}                                   all edges
{"mode": "edges", "edge_ids": [...], "fraction": 0.9}                    listed edges only
{"mode": "random", "fraction": 0.6, "share": 0.3, "seed": 1}             a random `share` of edges
{"mode": "near", "lonlat": [lon, lat], "radius_m": 300, "fraction": 0.9} edges with us or ds node within radius
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..config import CRS_COMPUTE, CRS_GEO
from ..contracts import DrainageNetwork
from ..provenance import Provenance, Tag


def _frac(spec: dict) -> float:
    return float(np.clip(float(spec.get("fraction", 0.0)), 0.0, 1.0))


def lonlat_to_xy(lon: float, lat: float) -> tuple[float, float]:
    from pyproj import Transformer
    tr = Transformer.from_crs(CRS_GEO, CRS_COMPUTE, always_xy=True)
    x, y = tr.transform(lon, lat)
    return float(x), float(y)


def apply_blockage(net: DrainageNetwork, spec: dict | None) -> DrainageNetwork:
    spec = dict(spec or {"mode": "none"})
    mode = str(spec.get("mode", "none")).lower()
    E = net.n_edges
    b = np.zeros(E, dtype=np.float32)
    affected = 0
    if mode == "none":
        pass
    elif mode == "fraction":
        b[:] = _frac(spec); affected = E
    elif mode == "edges":
        wanted = set(str(e) for e in spec.get("edge_ids", []))
        m = np.array([str(e) in wanted for e in net.edge_id], dtype=bool)
        b[m] = _frac(spec); affected = int(m.sum())
    elif mode == "random":
        rng = np.random.default_rng(int(spec.get("seed", 0)))
        share = float(np.clip(float(spec.get("share", 0.3)), 0.0, 1.0))
        k = int(round(share * E))
        idx = rng.choice(E, size=k, replace=False) if k > 0 else np.array([], dtype=int)
        b[idx] = _frac(spec); affected = k
    elif mode == "near":
        lon, lat = spec["lonlat"]
        x, y = lonlat_to_xy(float(lon), float(lat))
        r = float(spec.get("radius_m", 300.0))
        d2 = (np.asarray(net.node_x, dtype=np.float64) - x) ** 2 + (np.asarray(net.node_y, dtype=np.float64) - y) ** 2
        near = d2 <= r * r
        m = near[np.asarray(net.edge_us)] | near[np.asarray(net.edge_ds)]
        b[m] = _frac(spec); affected = int(m.sum())
    else:
        raise ValueError(f"unknown blockage mode {mode!r}")

    prov = dict(net.provenance)
    prov["blockage"] = {**Provenance(Tag.SYNTHETIC, "scenario input (apply_blockage)",
                                     f"mode={mode}, affected_edges={affected}/{E}").to_dict(), "spec": spec}
    return replace(net, edge_blockage=b, provenance=prov)
