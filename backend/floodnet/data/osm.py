"""OpenStreetMap (Overpass) roads + buildings -> RoadGraph, building mask, impervious fraction.

Geometry: REAL, (c) OpenStreetMap contributors, ODbL 1.0. Raw cached in data/raw/osm/.
Impervious fractions: ESTIMATED (building 1.0, <=6 m of road centreline 0.95, else 0.6 default for dense Mumbai fabric).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import contains_xy
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import unary_union

from ..config import DATA_RAW, CRS_GEO, CRS_COMPUTE
from ..contracts import Grid, RoadGraph, RoadSegment
from ..provenance import Provenance, Tag

log = logging.getLogger(__name__)

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
HEADERS = {"User-Agent": "Mozilla/5.0 (FloodNet; research use)"}
OSM_RAW = DATA_RAW / "osm"
HIGHWAYS = "motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street|service"
ROAD_BUFFER_M = 6.0
IMPERV_BUILDING, IMPERV_ROAD, IMPERV_DEFAULT = 1.0, 0.95, 0.6

OSM_PROVENANCE = Provenance(Tag.REAL, "OpenStreetMap contributors, ODbL 1.0 (Overpass API)",
                            "highway ways + building ways/relations (outer ways) in pilot bbox + margin; "
                            "geometry as mapped by volunteers, completeness not audited")
_T = Transformer.from_crs(CRS_GEO, CRS_COMPUTE, always_xy=True)


def _bbox_with_margin(bbox_lonlat, margin_m):
    from .mcgm import bbox_utm
    xmin, ymin, xmax, ymax = bbox_utm(bbox_lonlat, margin_m)
    tb = Transformer.from_crs(CRS_COMPUTE, CRS_GEO, always_xy=True)
    lons, lats = tb.transform([xmin, xmax], [ymin, ymax])
    return lons[0], lats[0], lons[1], lats[1]


def _overpass(query: str, cache_path: Path, timeout_s: float) -> dict:
    """POST one query to the mirrors in turn; cache the first success immediately."""
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    import httpx
    last = None
    for url in OVERPASS_URLS:
        try:
            with httpx.Client(headers=HEADERS, timeout=timeout_s) as client:
                r = client.post(url, data={"data": query})
                r.raise_for_status()
                js = r.json()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(js, fh)
            log.info("osm: %d elements from %s cached to %s", len(js.get("elements", [])), url, cache_path)
            return js
        except Exception as ex:  # noqa: BLE001
            last = ex; log.warning("osm: %s failed: %s", url, ex)
    raise RuntimeError(f"Overpass fetch failed on all mirrors: {last}")


def fetch_osm(bbox_lonlat, margin_m: float, cache_path: Path | str = OSM_RAW / "pilot_osm.json",
              timeout_s: float = 90.0, roads_only: bool = False) -> dict:
    """Roads and buildings are queried SEPARATELY (raw -> data/raw/osm/roads.json, buildings.json) and merged.
    A legacy combined cache (pilot_osm.json) is honoured if present. Buildings failing is non-fatal."""
    cache_path = Path(cache_path)
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    w, s, e, n = _bbox_with_margin(bbox_lonlat, margin_m)
    bb = f"{s},{w},{n},{e}"
    t = int(timeout_s)
    roads = _overpass(f'[out:json][timeout:{t}];way["highway"~"^({HIGHWAYS})$"]({bb});out body;>;out skel qt;',
                      OSM_RAW / "roads.json", timeout_s)
    elements = list(roads.get("elements", []))
    if not roads_only:
        try:
            b = _overpass(f'[out:json][timeout:{t}];way["building"]({bb});out body;>;out skel qt;',
                          OSM_RAW / "buildings.json", timeout_s)
            elements += b.get("elements", [])
        except Exception as ex:  # noqa: BLE001
            log.error("osm: buildings unavailable (%s); continuing with roads only", ex)
    return {"elements": elements}


def fetch_mcgm_road_centerlines(bbox_lonlat, margin_m: float, cache_path: Path | str = OSM_RAW.parent / "mcgm_gis" / "road_centerline_pilot.json",
                                timeout_s: float = 60.0) -> RoadGraph:
    """Fallback 1: MCGM layer 156 'Road Centerline' (REAL). Polylines in EPSG:32643 -> RoadGraph (no splitting at
    intersections beyond shared vertices; oneway unknown -> False)."""
    from .contours import MAPSERVER, HEADERS as H
    from .mcgm import bbox_utm
    cache_path = Path(cache_path)
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as fh:
            feats = json.load(fh)["features"]
    else:
        import httpx
        xmin, ymin, xmax, ymax = bbox_utm(bbox_lonlat, margin_m)
        params = {"where": "1=1", "geometry": f"{xmin},{ymin},{xmax},{ymax}", "geometryType": "esriGeometryEnvelope",
                  "inSR": 32643, "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "outSR": 32643,
                  "resultRecordCount": 2000, "f": "json"}
        with httpx.Client(headers=H, timeout=timeout_s) as client:
            r = client.get(f"{MAPSERVER}/156/query", params=params); r.raise_for_status(); js = r.json()
        if "error" in js:
            raise RuntimeError(js["error"])
        feats = js.get("features", [])
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump({"source": f"{MAPSERVER}/156", "spatialReference": {"wkid": 32643}, "features": feats}, fh)
    tb = Transformer.from_crs(CRS_COMPUTE, CRS_GEO, always_xy=True)
    gidx: dict[tuple, int] = {}; g_xy: list = []
    def gnode(x, y):
        k = (round(x, 1), round(y, 1))
        if k not in gidx: gidx[k] = len(g_xy); g_xy.append((x, y))
        return gidx[k]
    segs = []
    for ft in feats:
        a = ft.get("attributes", {})
        for p, path in enumerate(ft.get("geometry", {}).get("paths", [])):
            xy = np.asarray(path, dtype=float)
            if len(xy) < 2: continue
            lon, lat = tb.transform(xy[:, 0], xy[:, 1])
            name = str(a.get("ROAD_NAME") or a.get("NAME") or "")
            segs.append(RoadSegment(seg_id=f"mcgm156_{a.get('OBJECTID', len(segs))}_{p}", name=name, highway="mcgm",
                                    osm_way_id=-1, u=gnode(*xy[0]), v=gnode(*xy[-1]),
                                    length_m=float(np.sum(np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1])))),
                                    oneway=False, xy=xy, lonlat=np.column_stack([lon, lat])))
    xy = np.asarray(g_xy, dtype=float).reshape(-1, 2)
    lon, lat = tb.transform(xy[:, 0], xy[:, 1]) if len(xy) else ([], [])
    return RoadGraph(node_xy=xy, node_lonlat=np.column_stack([lon, lat]).reshape(-1, 2), segments=segs,
                     provenance=Provenance(Tag.REAL, "MCGM Road Centerline layer 156", "used because OSM/Overpass was unreachable at build time"))


def synthetic_lattice_roads(grid: Grid, spacing_m: float = 100.0) -> RoadGraph:
    """Fallback 2: SYNTHETIC lattice so the pipeline is never blocked. Never presented as Mumbai roads."""
    tb = Transformer.from_crs(CRS_COMPUTE, CRS_GEO, always_xy=True)
    xs = np.arange(grid.x0 + spacing_m / 2, grid.x0 + grid.nx * grid.res, spacing_m)
    ys = np.arange(grid.y0 + spacing_m / 2, grid.y0 + grid.ny * grid.res, spacing_m)
    nodes = [(x, y) for y in ys for x in xs]; idx = {n: k for k, n in enumerate(nodes)}
    segs = []
    for (x, y), k in idx.items():
        for nb in ((x + spacing_m, y), (x, y + spacing_m)):
            if nb in idx:
                xy = np.array([[x, y], nb]); lon, lat = tb.transform(xy[:, 0], xy[:, 1])
                segs.append(RoadSegment(seg_id=f"syn_{k}_{idx[nb]}", name="", highway="synthetic", osm_way_id=-1,
                                        u=k, v=idx[nb], length_m=spacing_m, oneway=False, xy=xy,
                                        lonlat=np.column_stack([lon, lat])))
    xy = np.asarray(nodes, dtype=float); lon, lat = tb.transform(xy[:, 0], xy[:, 1])
    return RoadGraph(node_xy=xy, node_lonlat=np.column_stack([lon, lat]), segments=segs,
                     provenance=Provenance(Tag.SYNTHETIC, "floodnet.data.osm.synthetic_lattice_roads",
                                           "OSM unreachable at build time; invented lattice, NOT Mumbai roads"))


def _split(js: dict):
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in js.get("elements", []) if el["type"] == "node"}
    roads, buildings = [], []
    for el in js.get("elements", []):
        if el["type"] != "way":
            continue
        tags = el.get("tags", {})
        if "highway" in tags:
            roads.append(el)
        elif "building" in tags:
            buildings.append(el)
    return nodes, roads, buildings


def build_road_graph(js: dict) -> RoadGraph:
    nodes, roads, _ = _split(js)
    # count how many road ways use each OSM node -> split at shared nodes (intersections) and way ends
    use = {}
    for w in roads:
        for nid in w["nodes"]:
            use[nid] = use.get(nid, 0) + 1
    gidx: dict[int, int] = {}
    g_lonlat: list[tuple[float, float]] = []

    def gnode(nid):
        if nid not in gidx:
            gidx[nid] = len(g_lonlat); g_lonlat.append(nodes[nid])
        return gidx[nid]

    segs: list[RoadSegment] = []
    for w in roads:
        tags = w.get("tags", {})
        ow = tags.get("oneway", "no") in ("yes", "true", "1", "-1")
        wn = [n for n in w["nodes"] if n in nodes]
        if len(wn) < 2:
            continue
        cut = [0] + [k for k in range(1, len(wn) - 1) if use[wn[k]] > 1] + [len(wn) - 1]
        for a, b in zip(cut[:-1], cut[1:]):
            part = wn[a:b + 1]
            if tags.get("oneway") == "-1":
                part = part[::-1]
            ll = np.array([nodes[n] for n in part], dtype=float)
            xs, ys = _T.transform(ll[:, 0], ll[:, 1])
            xy = np.column_stack([xs, ys])
            length = float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
            segs.append(RoadSegment(seg_id=f"{w['id']}_{a}", name=tags.get("name", ""), highway=tags.get("highway", ""),
                                    osm_way_id=int(w["id"]), u=gnode(part[0]), v=gnode(part[-1]),
                                    length_m=length, oneway=ow, xy=xy, lonlat=ll))
    ll = np.array(g_lonlat, dtype=float).reshape(-1, 2)
    if len(ll):
        xs, ys = _T.transform(ll[:, 0], ll[:, 1]); xy = np.column_stack([xs, ys])
    else:
        xy = np.zeros((0, 2))
    return RoadGraph(node_xy=xy, node_lonlat=ll, segments=segs, provenance=OSM_PROVENANCE)


def building_polygons(js: dict) -> list[Polygon]:
    nodes, _, buildings = _split(js)
    polys = []
    for w in buildings:
        wn = [n for n in w["nodes"] if n in nodes]
        if len(wn) < 4:
            continue
        ll = np.array([nodes[n] for n in wn], dtype=float)
        xs, ys = _T.transform(ll[:, 0], ll[:, 1])
        p = Polygon(np.column_stack([xs, ys]))
        if not p.is_valid:
            p = p.buffer(0)
        if not p.is_empty:
            polys.append(p)
    return polys


def _centres(grid: Grid):
    xc = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.res
    yc = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.res
    X, Y = np.meshgrid(xc, yc)
    return X.ravel(), Y.ravel()


def building_mask(js: dict, grid: Grid) -> np.ndarray:
    polys = building_polygons(js)
    mask = np.zeros(grid.ny * grid.nx, dtype=bool)
    if polys:
        X, Y = _centres(grid)
        for p in polys:  # bbox pre-filter then exact contains test
            minx, miny, maxx, maxy = p.bounds
            sel = np.where((X >= minx) & (X <= maxx) & (Y >= miny) & (Y <= maxy))[0]
            if len(sel):
                mask[sel] |= contains_xy(p, X[sel], Y[sel])
    return mask.reshape(grid.ny, grid.nx)


def impervious_fraction(js: dict, grid: Grid, bmask: np.ndarray | None = None,
                        roads: RoadGraph | None = None) -> tuple[np.ndarray, Provenance]:
    if bmask is None:
        bmask = building_mask(js, grid)
    if roads is None:
        roads = build_road_graph(js)
    imp = np.full((grid.ny, grid.nx), IMPERV_DEFAULT, dtype=np.float32)
    if roads.segments:
        lines = [LineString(s.xy) for s in roads.segments if len(s.xy) >= 2]
        buf = unary_union([l.buffer(ROAD_BUFFER_M) for l in lines])
        X, Y = _centres(grid)
        near = np.zeros(len(X), dtype=bool)
        geoms = list(buf.geoms) if isinstance(buf, MultiPolygon) else [buf]
        for g in geoms:
            minx, miny, maxx, maxy = g.bounds
            sel = np.where((X >= minx) & (X <= maxx) & (Y >= miny) & (Y <= maxy))[0]
            if len(sel):
                near[sel] |= contains_xy(g, X[sel], Y[sel])
        imp[near.reshape(grid.ny, grid.nx)] = IMPERV_ROAD
    imp[bmask] = IMPERV_BUILDING
    prov = Provenance(Tag.ESTIMATED, "floodnet.data.osm rule on OSM geometry",
                      f"building footprint {IMPERV_BUILDING}; within {ROAD_BUFFER_M:.0f} m of an OSM road centreline "
                      f"{IMPERV_ROAD}; elsewhere {IMPERV_DEFAULT} (assumed default for dense Mumbai urban fabric, "
                      "not calibrated)")
    return imp, prov


def roads_to_dict(rg: RoadGraph) -> dict:
    return {"node_xy": rg.node_xy.tolist(), "node_lonlat": rg.node_lonlat.tolist(),
            "segments": [{"seg_id": s.seg_id, "name": s.name, "highway": s.highway, "osm_way_id": s.osm_way_id,
                          "u": s.u, "v": s.v, "length_m": s.length_m, "oneway": s.oneway,
                          "xy": s.xy.tolist(), "lonlat": s.lonlat.tolist()} for s in rg.segments],
            "provenance": rg.provenance.to_dict()}


def roads_from_dict(d: dict) -> RoadGraph:
    p = d["provenance"]
    segs = [RoadSegment(seg_id=s["seg_id"], name=s["name"], highway=s["highway"], osm_way_id=s["osm_way_id"],
                        u=s["u"], v=s["v"], length_m=s["length_m"], oneway=s["oneway"],
                        xy=np.array(s["xy"], dtype=float).reshape(-1, 2),
                        lonlat=np.array(s["lonlat"], dtype=float).reshape(-1, 2)) for s in d["segments"]]
    return RoadGraph(node_xy=np.array(d["node_xy"], dtype=float).reshape(-1, 2),
                     node_lonlat=np.array(d["node_lonlat"], dtype=float).reshape(-1, 2),
                     segments=segs, provenance=Provenance(Tag(p["tag"]), p["source"], p.get("note", "")))
