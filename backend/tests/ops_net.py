"""A small hand-built road network with real-looking coordinates for the live-operations tests (SYNTHETIC test
fixture, not pilot data). Two east-west roads joined by three lanes, every segment two-way:

    3 --North Rd-- 4 --North Rd-- 5          y = 19.015
    |              |              |
  West Ln        Mid Ln        East Ln
    |              |              |
    0 --South Rd-- 1 --South Rd-- 2          y = 19.010
  x = 72.840     72.845         72.850

Residential roads at the ASSUMED 15 km/h: South Rd 0->2 (about 1.05 km) takes about 4.2 min; the detour through the
north road is about 2.2 km.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
from pyproj import Transformer

from floodnet import config
from floodnet.access.road_status import Forecast, SegmentContext
from floodnet.contracts import RoadGraph, RoadSegment
from floodnet.provenance import Provenance, Tag
from floodnet.routing.router import build_graph

_fwd = Transformer.from_crs(config.CRS_GEO, config.CRS_COMPUTE, always_xy=True)

LONLAT = np.array([[72.840, 19.010], [72.845, 19.010], [72.850, 19.010],
                   [72.840, 19.015], [72.845, 19.015], [72.850, 19.015]])
XY = np.column_stack(_fwd.transform(LONLAT[:, 0], LONLAT[:, 1]))

EDGES = [("s01", 0, 1, "South Rd"), ("s12", 1, 2, "South Rd"), ("n34", 3, 4, "North Rd"), ("n45", 4, 5, "North Rd"),
         ("w03", 0, 3, "West Ln"), ("m14", 1, 4, "Mid Ln"), ("e25", 2, 5, "East Ln")]


def _seg(sid, u, v, name):
    xy = np.array([XY[u], XY[v]])
    return RoadSegment(seg_id=sid, name=name, highway="residential", osm_way_id=0, u=u, v=v,
                       length_m=float(np.hypot(*(XY[v] - XY[u]))), oneway=False, xy=xy,
                       lonlat=np.array([LONLAT[u], LONLAT[v]]))


ROADS = RoadGraph(node_xy=XY, node_lonlat=LONLAT, segments=[_seg(*e) for e in EDGES],
                  provenance=Provenance(tag=Tag.SYNTHETIC, source="hand-built ops test network"))
G: nx.DiGraph = build_graph(ROADS)
NO_CTX = SegmentContext(frozenset(), frozenset())
T = tuple(float(x) for x in range(0, 125, 5))           # frames every 5 min, T+0 .. T+120
SEG_IDS = [e[0] for e in EDGES]


def forecast(**depths) -> Forecast:
    """Dry everywhere unless a segment is given a list (one value per frame) or a constant."""
    rows = {}
    for sid in SEG_IDS:
        v = depths.get(sid, 0.0)
        rows[sid] = list(v) if isinstance(v, (list, tuple)) else [float(v)] * len(T)
    return Forecast(T, rows)


def flood_from(t_min: float, cm: float = 80.0) -> list[float]:
    return [0.0 if t < t_min else cm for t in T]


def mid(a: int, b: int, north_m: float = 0.0) -> tuple[float, float]:
    """A point halfway along the road a-b, optionally moved north by `north_m` metres (lon, lat)."""
    lon, lat = (LONLAT[a] + LONLAT[b]) / 2
    return float(lon), float(lat + north_m / 110_540.0)
