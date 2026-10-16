"""SYNTHETIC pilot fixture: a bowl-shaped catchment with an undersized drainage chain and a lattice of roads.

EVERYTHING here is tagged Tag.SYNTHETIC / Tag.DEMONSTRATION. The geometry is placed in EPSG:32643 near Dadar
only so that lon/lat conversions land inside Mumbai for the map; it is NOT Mumbai terrain, drainage or roads.
"""
from __future__ import annotations

import numpy as np

from ..contracts import Grid, Terrain, DrainageNetwork, RoadGraph, RoadSegment, RainfallScenario
from ..provenance import Provenance, Tag
from ..config import CRS_COMPUTE, CRS_GEO, RAIN_DT_S

NOTE = "synthetic fixture — not Mumbai"
_SYN_ORIGIN_LONLAT = (72.840, 19.015)     # south-west corner; near Dadar so the map renders in the pilot area


def _transformers():
    from pyproj import Transformer
    fwd = Transformer.from_crs(CRS_GEO, CRS_COMPUTE, always_xy=True)
    inv = Transformer.from_crs(CRS_COMPUTE, CRS_GEO, always_xy=True)
    return fwd, inv


def _prov(kind: str, tag: Tag = Tag.SYNTHETIC) -> Provenance:
    return Provenance(tag=tag, source=f"floodnet.data.fixtures.synthetic_pilot ({kind})", note=NOTE)


def _manning_capacity(width: np.ndarray, slope: np.ndarray, n: np.ndarray) -> np.ndarray:
    area = np.pi * (width / 2.0) ** 2
    r_h = width / 4.0
    return (1.0 / n) * area * r_h ** (2.0 / 3.0) * np.sqrt(slope)


def synthetic_terrain(res_m: float = 10.0, nx: int = 60, ny: int = 60, seed: int = 0) -> Terrain:
    fwd, _ = _transformers()
    x0, y0 = fwd.transform(*_SYN_ORIGIN_LONLAT)
    grid = Grid(x0=float(x0), y0=float(y0), res=float(res_m), nx=int(nx), ny=int(ny))
    rng = np.random.default_rng(seed)
    ii, jj = np.meshgrid(np.arange(nx), np.arange(ny))
    cx, cy = (nx - 1) / 2.0, (ny - 1) / 2.0
    r = np.sqrt(((ii - cx) / (nx / 2.0)) ** 2 + ((jj - cy) / (ny / 2.0)) ** 2)   # 0 centre, ~1 at edge midpoints
    z = 30.0 + 3.0 * r ** 2 + rng.normal(0.0, 0.03, size=(ny, nx))
    impervious = np.full((ny, nx), 0.8, dtype=np.float32)
    building = np.zeros((ny, nx), dtype=bool)
    # a few building blocks, kept off the road lattice (cols/rows 10,20,30,40,50) and the drainage row (j = ny//2)
    for (j0, i0) in [(12, 12), (42, 22), (22, 42), (12, 44)]:
        if j0 + 4 < ny and i0 + 4 < nx:
            building[j0:j0 + 4, i0:i0 + 4] = True
    return Terrain(grid=grid, z=z.astype(np.float32), impervious=impervious, building=building,
                   provenance=_prov("bowl DEM: z = 30 + 3*r^2 + N(0,0.03)"),
                   impervious_provenance=_prov("impervious 0.8 uniform"),
                   building_provenance=_prov("four 40 m building blocks"))


def synthetic_network(grid: Grid, n_nodes: int = 7) -> DrainageNetwork:
    """Chain of manholes from the bowl centre eastwards to an outfall at the rim. Undersized 0.4 m pipes."""
    cx = grid.x0 + grid.nx * grid.res / 2.0
    cy = grid.y0 + grid.ny * grid.res / 2.0
    x_end = grid.x0 + (grid.nx - 0.5) * grid.res       # last column = rim
    xs = np.linspace(cx, x_end, n_nodes)
    ys = np.full(n_nodes, cy)
    # ground follows the bowl profile; invert slopes steadily down to the outfall (below ground everywhere)
    rnorm = (xs - cx) / (grid.nx * grid.res / 2.0)
    ground = (30.0 + 3.0 * rnorm ** 2).astype(np.float32)
    length = np.diff(xs).astype(np.float32)
    inv_top = 28.0
    inverts = inv_top - 0.004 * (xs - cx)             # 0.4 % slope
    node_id = np.array([f"SYN_N{k}" for k in range(n_nodes)], dtype=object)
    edge_id = np.array([f"SYN_E{k}" for k in range(n_nodes - 1)], dtype=object)
    edge_us = np.arange(n_nodes - 1)
    edge_ds = np.arange(1, n_nodes)
    us_inv = inverts[:-1].astype(np.float32)
    ds_inv = inverts[1:].astype(np.float32)
    slope = np.maximum((us_inv - ds_inv) / length, 1e-4).astype(np.float32)
    width = np.full(n_nodes - 1, 0.4, dtype=np.float32)
    n_man = np.full(n_nodes - 1, 0.013, dtype=np.float32)
    cap = _manning_capacity(width, slope, n_man).astype(np.float32)
    j, i = grid.cell_of(xs, ys)
    is_outfall = np.zeros(n_nodes, dtype=bool); is_outfall[-1] = True
    prov = {k: _prov(f"drainage {k}").to_dict() for k in ("geometry", "roughness", "inlet", "storage", "outfall")}
    return DrainageNetwork(
        node_id=node_id, node_x=xs.astype(np.float64), node_y=ys.astype(np.float64),
        node_ground=ground, node_invert=inverts.astype(np.float32), node_is_outfall=is_outfall,
        node_storage_area_m2=np.full(n_nodes, 1.5, dtype=np.float32),
        node_inlet_cap_m3s=np.full(n_nodes, 0.05, dtype=np.float32),
        node_cell_j=j.astype(int), node_cell_i=i.astype(int),
        edge_id=edge_id, edge_us=edge_us, edge_ds=edge_ds, edge_length_m=length,
        edge_shape=np.array(["CIRC"] * (n_nodes - 1), dtype=object), edge_width_m=width, edge_height_m=width.copy(),
        edge_us_invert=us_inv, edge_ds_invert=ds_inv, edge_slope=slope, edge_n=n_man, edge_capacity_m3s=cap,
        edge_blockage=np.zeros(n_nodes - 1, dtype=np.float32),
        edge_status=np.array(["Existing"] * (n_nodes - 1), dtype=object), provenance=prov)


def synthetic_roads(grid: Grid, n: int = 5) -> RoadGraph:
    """n x n lattice of two-way road segments spanning the grid interior."""
    _, inv = _transformers()
    span_x, span_y = grid.nx * grid.res, grid.ny * grid.res
    xs = grid.x0 + span_x * (np.arange(n) + 1) / (n + 1)
    ys = grid.y0 + span_y * (np.arange(n) + 1) / (n + 1)
    node_xy = np.array([(x, y) for y in ys for x in xs], dtype=np.float64)       # index = row*n + col
    lon, lat = inv.transform(node_xy[:, 0], node_xy[:, 1])
    node_lonlat = np.column_stack([lon, lat]).astype(np.float64)
    segments: list[RoadSegment] = []

    def add(u: int, v: int, name: str):
        xy = node_xy[[u, v]]
        ll = node_lonlat[[u, v]]
        segments.append(RoadSegment(seg_id=f"SYN_{len(segments):03d}", name=name, highway="residential",
                                    osm_way_id=-1, u=u, v=v, length_m=float(np.hypot(*(xy[1] - xy[0]))),
                                    oneway=False, xy=xy.copy(), lonlat=ll.copy()))

    for r in range(n):
        for c in range(n - 1):
            add(r * n + c, r * n + c + 1, f"Synthetic Rd {chr(65 + r)}{c + 1}")            # east-west
    for c in range(n):
        for r in range(n - 1):
            add(r * n + c, (r + 1) * n + c, f"Synthetic Rd {chr(65 + n + c)}{r + 1}")      # north-south
    return RoadGraph(node_xy=node_xy, node_lonlat=node_lonlat, segments=segments,
                     provenance=_prov(f"{n}x{n} road lattice"))


def synthetic_scenarios() -> list[RainfallScenario]:
    def make(sid, name, mm_h, minutes, desc):
        # trailing zero entry: contracts.intensity_at holds the last value indefinitely, so rain must be switched off
        t = np.arange(0, minutes * 60 + RAIN_DT_S, RAIN_DT_S, dtype=np.float64)
        inten = np.full(len(t), float(mm_h)); inten[-1] = 0.0
        return RainfallScenario(id=sid, name=name, t_s=t, intensity_mm_h=inten,
                                provenance=_prov(f"rainfall {sid}"), description=desc)
    return [make("moderate", "Moderate (synthetic)", 20, 120, "20 mm/h for 2 h — synthetic block storm"),
            make("cloudburst", "Cloudburst (synthetic)", 120, 45, "120 mm/h for 45 min — synthetic stress test")]


def synthetic_pilot(res_m: float = 10.0, nx: int = 60, ny: int = 60, seed: int = 0) -> dict:
    """Everything an end-to-end run needs, all SYNTHETIC. Keys: terrain, net, roads, hotspots, scenarios."""
    terrain = synthetic_terrain(res_m, nx, ny, seed)
    return {"terrain": terrain, "net": synthetic_network(terrain.grid), "roads": synthetic_roads(terrain.grid),
            "hotspots": [], "scenarios": synthetic_scenarios(),
            "provenance": _prov("bundle", Tag.DEMONSTRATION).to_dict()}
