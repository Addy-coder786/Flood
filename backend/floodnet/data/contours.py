"""MCGM layer 301 Contour_20CM -> DTM on the pilot grid.

fetch_contours: paged ArcGIS REST query (native EPSG:32643), raw JSON cached to data/raw/mcgm_gis/contours_pilot.json.
contours_to_dtm: densify polylines (~5 m), add manhole GROUND_LEV points, scipy griddata linear + nearest fill.
Fallbacks (each labelled): manhole points only (REAL, coarse) -> SyntheticSlope (SYNTHETIC, never called Mumbai terrain).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from scipy.interpolate import griddata

from ..config import MCGM_RAW
from ..contracts import Grid
from ..provenance import Provenance, Tag, MCGM_SNAPSHOT

log = logging.getLogger(__name__)

MAPSERVER = "https://prsrvgisapp.mcgm.gov.in/server/rest/services/mcgm/MCGMGIS_Departments_Master_All_Layers/MapServer"
CONTOUR_LAYER = 301
HEADERS = {"User-Agent": "Mozilla/5.0 (FloodNet; research use)"}
DEFAULT_CACHE = MCGM_RAW / "contours_pilot.json"


def fetch_contours(bbox_lonlat, margin_m: float, cache_path: Path | str = DEFAULT_CACHE,
                   timeout_s: float = 60.0) -> list[dict]:
    """Return Esri features [{attributes:{HEIGHT,LAYER}, geometry:{paths:[[[x,y],...]]}}] in EPSG:32643.
    Uses the cache if present; otherwise pages the server and writes the cache immediately."""
    cache_path = Path(cache_path)
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as fh:
            feats = json.load(fh)["features"]
        log.info("contours: loaded %d features from cache %s", len(feats), cache_path)
        return feats

    import httpx
    from .mcgm import bbox_utm
    from pyproj import Transformer
    xmin, ymin, xmax, ymax = bbox_utm(bbox_lonlat, margin_m)
    t = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    lons, lats = t.transform([xmin, xmax], [ymin, ymax])
    geom = f"{lons[0]},{lats[0]},{lons[1]},{lats[1]}"
    feats: list[dict] = []
    offset = 0
    with httpx.Client(headers=HEADERS, timeout=timeout_s) as client:
        while True:
            params = {"where": "1=1", "geometry": geom, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                      "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "outSR": 32643,
                      "resultOffset": offset, "resultRecordCount": 2000, "f": "json"}
            r = client.get(f"{MAPSERVER}/{CONTOUR_LAYER}/query", params=params)
            r.raise_for_status()
            js = r.json()
            if "error" in js:
                raise RuntimeError(f"ArcGIS error: {js['error']}")
            page = js.get("features", [])
            feats.extend(page)
            log.info("contours: page offset=%d got %d", offset, len(page))
            if not js.get("exceededTransferLimit") or not page:
                break
            offset += len(page)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"source": f"{MAPSERVER}/{CONTOUR_LAYER}", "bbox_lonlat_margin_m": [list(bbox_lonlat), margin_m],
                   "spatialReference": {"wkid": 32643}, "features": feats}, fh)
    log.info("contours: fetched %d features, cached to %s", len(feats), cache_path)
    return feats


def densify_contours(feats: list[dict], step_m: float = 5.0) -> np.ndarray:
    """-> [P,3] (x, y, z) points along all contour polylines every ~step_m."""
    pts = []
    for ft in feats:
        z = ft["attributes"].get("HEIGHT")
        if z is None:
            continue
        for path in ft.get("geometry", {}).get("paths", []):
            arr = np.asarray(path, dtype=float)
            if len(arr) < 2:
                for p in arr: pts.append((p[0], p[1], z))
                continue
            seg = np.hypot(np.diff(arr[:, 0]), np.diff(arr[:, 1]))
            cum = np.concatenate([[0.0], np.cumsum(seg)])
            if cum[-1] <= 0:
                pts.append((arr[0, 0], arr[0, 1], z)); continue
            s = np.arange(0.0, cum[-1], step_m)
            xs = np.interp(s, cum, arr[:, 0]); ys = np.interp(s, cum, arr[:, 1])
            pts.extend(zip(xs, ys, np.full(len(s), float(z))))
            pts.append((arr[-1, 0], arr[-1, 1], z))
    return np.asarray(pts, dtype=float).reshape(-1, 3)


def interpolate_points_to_grid(pts: np.ndarray, grid: Grid) -> np.ndarray:
    """griddata linear on cell centres, NaNs (outside hull) filled by nearest."""
    xc = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.res
    yc = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.res
    X, Y = np.meshgrid(xc, yc)
    # de-duplicate identical xy (griddata dislikes them)
    _, uniq = np.unique(np.round(pts[:, :2], 2), axis=0, return_index=True)
    p = pts[uniq]
    z = griddata(p[:, :2], p[:, 2], (X, Y), method="linear")
    nan = np.isnan(z)
    if nan.any():
        z[nan] = griddata(p[:, :2], p[:, 2], (X[nan], Y[nan]), method="nearest")
    return z.astype(np.float32)


def contours_to_dtm(contours: list[dict] | None, node_points: np.ndarray | None, grid: Grid,
                    step_m: float = 5.0) -> tuple[np.ndarray, Provenance]:
    """node_points: [N,3] (x, y, GROUND_LEV mTHD) manhole points (may be None). Returns (z[ny,nx] float32, Provenance)."""
    parts = []
    n_c = 0
    if contours:
        c = densify_contours(contours, step_m)
        n_c = len(c)
        if n_c: parts.append(c)
    n_m = 0
    if node_points is not None and len(node_points):
        parts.append(np.asarray(node_points, dtype=float)); n_m = len(node_points)
    if not parts:
        raise ValueError("contours_to_dtm: no input points")
    pts = np.vstack(parts)
    z = interpolate_points_to_grid(pts, grid)
    if n_c:
        prov = Provenance(Tag.REAL, MCGM_SNAPSHOT,
                          f"MCGM Contour_20CM (layer 301, {len(contours)} polylines densified to {n_c} pts @ {step_m} m) "
                          f"+ {n_m} manhole GROUND_LEV points, scipy griddata linear, nearest-fill outside hull; "
                          "elevations mTHD (Town Hall Datum), MSL offset UNVERIFIED")
    else:
        prov = Provenance(Tag.REAL, MCGM_SNAPSHOT,
                          f"FALLBACK: manhole GROUND_LEV points only ({n_m} pts, layer 6), no contours available; "
                          "coarser than contour DTM (typical spacing 30-60 m); linear griddata + nearest fill; mTHD")
    return z, prov


class SyntheticSlope:
    """Last-resort SYNTHETIC terrain: a gentle plane with a shallow bowl. NOT Mumbai terrain."""
    def __init__(self, base_m: float = 30.0, slope: float = 0.002, bowl_depth_m: float = 1.0):
        self.base, self.slope, self.bowl = base_m, slope, bowl_depth_m

    def build(self, grid: Grid) -> tuple[np.ndarray, Provenance]:
        i = np.arange(grid.nx); j = np.arange(grid.ny)
        I, J = np.meshgrid(i, j)
        x = (I + 0.5) * grid.res; y = (J + 0.5) * grid.res
        z = self.base + self.slope * x
        cx, cy = grid.nx * grid.res / 2, grid.ny * grid.res / 2
        r = np.hypot(x - cx, y - cy)
        z = z - self.bowl * np.exp(-(r / (0.2 * max(cx, cy))) ** 2)
        return z.astype(np.float32), Provenance(
            Tag.SYNTHETIC, "floodnet.data.contours.SyntheticSlope",
            "invented plane + bowl used only because no MCGM contours or manhole levels were available; "
            "NOT Mumbai terrain")
