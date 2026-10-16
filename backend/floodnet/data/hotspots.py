"""MCGM layer 344 Flooding Spots (polygons) within the pilot bbox. REAL, MCGM. Raw saved to data/raw/mcgm_gis/."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import MCGM_RAW
from ..provenance import Provenance, Tag, MCGM_SNAPSHOT
from .contours import MAPSERVER, HEADERS

log = logging.getLogger(__name__)
HOTSPOT_LAYER = 344
DEFAULT_CACHE = MCGM_RAW / "flooding_spots_pilot.json"

PROVENANCE = Provenance(Tag.REAL, MCGM_SNAPSHOT,
                        "layer 344 Flooding Spots (MCGM-designated chronic flooding locations); names with "
                        "'(Delete)' or '(Tackled)' flagged inactive; DEPTH/STRETCH are MCGM free-text attributes")


def fetch_hotspots(bbox_lonlat, cache_path: Path | str = DEFAULT_CACHE, timeout_s: float = 60.0) -> list[dict]:
    cache_path = Path(cache_path)
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as fh:
            return json.load(fh)["features"]
    import httpx
    w, s, e, n = bbox_lonlat
    params = {"where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
              "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "outSR": 4326, "f": "json",
              "resultRecordCount": 2000}
    with httpx.Client(headers=HEADERS, timeout=timeout_s) as client:
        r = client.get(f"{MAPSERVER}/{HOTSPOT_LAYER}/query", params=params)
        r.raise_for_status()
        js = r.json()
    if "error" in js:
        raise RuntimeError(f"ArcGIS error: {js['error']}")
    feats = js.get("features", [])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"source": f"{MAPSERVER}/{HOTSPOT_LAYER}", "bbox_lonlat": list(bbox_lonlat),
                   "spatialReference": {"wkid": 4326}, "features": feats}, fh)
    log.info("hotspots: fetched %d, cached to %s", len(feats), cache_path)
    return feats


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def hotspots_to_records(feats: list[dict]) -> list[dict]:
    from shapely.geometry import Polygon
    recs = []
    for ft in feats:
        a = ft.get("attributes", {})
        rings = ft.get("geometry", {}).get("rings", [])
        poly = [[float(p[0]), float(p[1])] for p in rings[0]] if rings else []
        if len(poly) >= 3:
            c = Polygon(poly).centroid; cen = [c.x, c.y]
        elif poly:
            cen = poly[0]
        else:
            cen = None
        name = str(a.get("NAME") or "")
        recs.append({
            "name": name, "location": a.get("LOCATION"), "ward": a.get("WARD"), "affect_road": a.get("AFFECT_ROAD"),
            "depth_attr": a.get("DEPTH"), "stretch_m": _num(a.get("STRETCH")),
            "active": not any(k in name for k in ("(Delete)", "(Tackled)")),
            "lonlat_centroid": cen, "polygon_lonlat": poly,
        })
    return recs


def build_hotspots(bbox_lonlat, cache_path: Path | str = DEFAULT_CACHE) -> list[dict]:
    try:
        feats = fetch_hotspots(bbox_lonlat, cache_path)
    except Exception as ex:  # noqa: BLE001
        log.error("hotspots: fetch failed (%s); returning empty list", ex)
        return []
    return hotspots_to_records(feats)
