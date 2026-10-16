"""ESTIMATED hydraulic attributes for MCGM conduits (Manning n, inlet capacity, manhole storage, full-bore capacity).

Everything here is a RULE applied to REAL geometry; every number is tagged ESTIMATED and cited.

Citations
---------
* Chow, V.T. (1959) *Open-Channel Hydraulics*, Table 5-6: concrete, finished n = 0.011-0.015 (normal 0.013);
  brick/masonry n = 0.012-0.018 (normal 0.015); corrugated metal n = 0.021-0.030 (normal 0.024).
* Manning (1891) full-bore formula Q = (1/n) A R^(2/3) S^(1/2), SI units.
* CPHEEO (2019) *Manual on Storm Water Drainage Systems*, MoHUA India, Part A ch. 5: manhole internal
  diameter 0.9-1.5 m; minimum conduit gradient for self-cleansing ~1 in 1000 (0.1 %).
* FHWA HEC-22 (2013) *Urban Drainage Design Manual*, ch. 4: a single kerb/grate inlet on grade captures
  roughly 30-90 L/s at typical Indian road cross-falls; we use 0.05 m3/s as a mid-range single-inlet value.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..contracts import DrainageNetwork
from ..provenance import Provenance, Tag

# --- ESTIMATED constants -------------------------------------------------------------------------
MANNING_N_BY_SHAPE = {          # Chow 1959 Table 5-6; MCGM CIRC = RCC pipe, RECT/OREC/ARCH = concrete/brick box or arch
    "CIRC": 0.013,
    "RECT": 0.013,
    "OREC": 0.013,
    "ARCH": 0.015,              # older brick arches (Mumbai's British-era arch drains) - masonry, Chow "normal"
}
MANNING_N_DEFAULT = 0.013
INLET_CAPACITY_DEFAULT_M3S = 0.05     # FHWA HEC-22 single grate/kerb inlet on grade, mid-range (ESTIMATED)
STORAGE_AREA_DEFAULT_M2 = 1.2         # ~1.2 m internal diameter manhole plan area, CPHEEO 2019 (ESTIMATED)
MIN_SLOPE = 0.001                     # 0.1 % floor for zero/negative recorded slopes (CPHEEO self-cleansing min)


def manning_n_for(shape: str) -> float:
    """Manning n by MCGM conduit shape (Chow 1959). Unknown shapes -> concrete 0.013."""
    return MANNING_N_BY_SHAPE.get(str(shape).upper().strip(), MANNING_N_DEFAULT)


def inlet_capacity_default_m3s() -> float:
    """Max surface-capture rate per node when no inlet data exist (FHWA HEC-22, ESTIMATED)."""
    return INLET_CAPACITY_DEFAULT_M3S


def storage_area_default_m2() -> float:
    """Manhole plan area used for node storage when no chamber data exist (CPHEEO 2019, ESTIMATED)."""
    return STORAGE_AREA_DEFAULT_M2


def section_area_and_radius(shape: str, width_m: float, height_m: float) -> tuple[float, float]:
    """Full-flow area A (m2) and hydraulic radius R (m). CIRC: D = width. Others: rectangle W x H."""
    shape = str(shape).upper().strip()
    if shape == "CIRC":
        d = float(width_m) if width_m > 0 else float(height_m)
        a = math.pi * d * d / 4.0
        return a, d / 4.0
    w, h = float(width_m), float(height_m)
    if w <= 0 or h <= 0:
        return 0.0, 0.0
    a = w * h
    return a, a / (2.0 * w + 2.0 * h)


def full_bore_capacity_m3s(shape: str, width_m: float, height_m: float, slope: float, n: float | None = None) -> float:
    """Manning full-bore capacity Q = (1/n) A R^(2/3) S^(1/2). Slope floored at MIN_SLOPE."""
    n = manning_n_for(shape) if n is None else float(n)
    a, r = section_area_and_radius(shape, width_m, height_m)
    if a <= 0 or r <= 0:
        return 0.0
    s = max(float(slope), MIN_SLOPE)
    return (1.0 / n) * a * (r ** (2.0 / 3.0)) * math.sqrt(s)


def refine_attributes(net: DrainageNetwork) -> DrainageNetwork:
    """Return a copy of `net` with edge_n / edge_slope / edge_capacity_m3s recomputed from the rules above.

    Also fills node_storage_area_m2 and node_inlet_cap_m3s where they are <= 0 / NaN, and records the
    ESTIMATED provenance in net.provenance["roughness"] and ["capacity"].
    """
    e = net.n_edges
    n_arr = np.array([manning_n_for(s) for s in net.edge_shape], dtype=np.float32)
    slope = np.asarray(net.edge_slope, dtype=np.float64).copy()
    length = np.maximum(np.asarray(net.edge_length_m, dtype=np.float64), 1.0)
    geom_slope = (np.asarray(net.edge_us_invert, dtype=np.float64) - np.asarray(net.edge_ds_invert, dtype=np.float64)) / length
    bad = ~np.isfinite(slope) | (slope <= 0)
    slope[bad] = geom_slope[bad]
    slope = np.where(np.isfinite(slope) & (slope > 0), slope, MIN_SLOPE)
    slope = np.maximum(slope, MIN_SLOPE)
    cap = np.array([full_bore_capacity_m3s(net.edge_shape[k], float(net.edge_width_m[k]), float(net.edge_height_m[k]),
                                           float(slope[k]), float(n_arr[k])) for k in range(e)], dtype=np.float32)

    storage = np.asarray(net.node_storage_area_m2, dtype=np.float32).copy()
    storage[~np.isfinite(storage) | (storage <= 0)] = STORAGE_AREA_DEFAULT_M2
    inlet = np.asarray(net.node_inlet_cap_m3s, dtype=np.float32).copy()
    inlet[~np.isfinite(inlet) | (inlet <= 0)] = INLET_CAPACITY_DEFAULT_M3S

    prov = dict(net.provenance)
    prov["roughness"] = Provenance(Tag.ESTIMATED, "Chow (1959) Open-Channel Hydraulics, Table 5-6",
                                   "Manning n by shape: CIRC/RECT/OREC concrete 0.013, ARCH masonry 0.015").to_dict()
    prov["capacity"] = Provenance(Tag.ESTIMATED, "Manning full-bore formula on MCGM geometry",
                                  f"Q=(1/n)A R^(2/3) S^(1/2); slope floored at {MIN_SLOPE}; CIRC R=D/4, box R=WH/(2W+2H)").to_dict()
    prov["inlet_storage"] = Provenance(Tag.ESTIMATED, "FHWA HEC-22 (2013); CPHEEO (2019)",
                                       f"inlet {INLET_CAPACITY_DEFAULT_M3S} m3/s, manhole plan area {STORAGE_AREA_DEFAULT_M2} m2 where missing").to_dict()
    return replace(net, edge_n=n_arr, edge_slope=slope.astype(np.float32), edge_capacity_m3s=cap,
                   node_storage_area_m2=storage, node_inlet_cap_m3s=inlet, provenance=prov)
