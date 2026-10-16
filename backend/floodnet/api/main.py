"""FloodNet FastAPI app (Mumbai Hindmata/Dadar pilot).

Run: cd backend && .venv/Scripts/python -m uvicorn floodnet.api.main:app --port 8000
Every data-bearing response carries `provenance`. Missing sibling modules -> HTTP 503 with a clear message.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Optional

import numpy as np
from datetime import datetime, timezone
import os
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..ops.auth import require_dispatcher      # D-39: sending alerts is a dispatcher action

from .. import config
from ..contracts import SimulationResult, Frame
from ..drainage.hydraulics import MIN_DEPTH_M  # read-only: solver's own storage floor (frozen-core file,
# not modified here) -- see _node_ground_floor() below, which mirrors GraphDrainage's own
# `ground = max(node_ground, node_invert + MIN_DEPTH_M)` so derived fill/freeboard match the engine's HGL cap.
from . import state
from .png import grayscale_png_base64, depth_png_base64
from ..analysis.hotspots import flood_intelligence, segment_timing
from ..rainfall.source_manager import active_source_metadata
from .schemas import SimulateRequest, ReplayRequest, CompareRequest, RouteRequest, TTSRequest, AccessRouteRequest, ResponsePlanRequest, ResponseEvaluateRequest, WhatsAppSendRequest

log = logging.getLogger("floodnet.api")
FLOOD_SEG_CM = 15.0  # a segment counts as "flooded" at >= 15 cm (minor band, see config.SEVERITY_BANDS_CM)

@asynccontextmanager
async def _lifespan(_app):
    # Keep the IMD token fresh in the background when IMD account credentials are configured (no-op otherwise).
    # FLOODNET_IMD_AUTO_RENEW=0 disables it; request-time renewal (before expiry / after a 401) still applies.
    # Not on Vercel: a function instance is frozen between requests, so a background thread cannot be relied on
    # there. Every IMD-backed request obtains / renews the token itself (imd_auth.token_for_request).
    if os.environ.get("FLOODNET_IMD_AUTO_RENEW", "1") != "0" and not config.ON_VERCEL:
        from ..rainfall.imd_auth import manager as imd
        imd.start_keeper()
    yield


app = FastAPI(title="FloodNet API", version="0.1.0", lifespan=_lifespan,
              description="Urban flood nowcasting for the Mumbai Hindmata/Dadar pilot")
# FLOODNET_CORS_ORIGINS: comma-separated allowed origins for a separately hosted frontend, e.g.
# "https://floodnet.vercel.app". Unset = "*" (no cookies/credentials are used, so this is safe for a
# read-mostly prototype API); X-TTS-* headers are exposed so the briefing can show the voice in use.
_cors = [o.strip() for o in os.environ.get("FLOODNET_CORS_ORIGINS", "*").split(",") if o.strip()] or ["*"]
app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-TTS-Voice", "X-TTS-Language"])


def _index_response():
    idx = config.FRONTEND_DIR / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
    return JSONResponse({"message": "FloodNet API is running; frontend/index.html not found", "docs": "/docs"})


# ----------------------------------------------------------------------------- SPA client-side routes
# frontend-react/src/App.jsx owns two pushState targets (BASE + '/' for the landing page, BASE + '/dashboard'
# for the command centre) -- neither is a real file on disk, so the StaticFiles mount below 404s them on a
# hard refresh or a direct/shared link (Vite's own dev server papers over this via appType:'spa'
# historyApiFallback; this production server needs the equivalent explicitly). These two exact routes are
# registered BEFORE the mount so they win route resolution ahead of it, and are the ONLY paths handled this
# way -- deliberately not a generic "/static/{path:path}" catch-all, which would also swallow 404s for
# genuinely missing static assets. Registered before /api/*, /docs, /openapi.json are even defined, so those
# are unaffected either way (different top-level paths, no overlap).
# On Vercel the frontend is registered with app.frontend("/") at the end of this module instead (served from the
# CDN, SPA fallback included), so none of these local-server routes exist there.
if not config.ON_VERCEL:
    @app.get("/static/", include_in_schema=False)
    @app.get("/static/dashboard", include_in_schema=False)
    @app.get("/static/driver", include_in_schema=False)          # driver trip page (D-40); token in the URL fragment
    def spa_fallback():
        return _index_response()

    @app.get("/static/alerts/{public_id}", include_in_schema=False)     # public alert page (D-45)
    def spa_public_alert(public_id: str):
        return _index_response()

    if config.FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")


# ----------------------------------------------------------------------------- errors
@app.exception_handler(state.ModuleMissing)
async def _module_missing(_: Request, exc: state.ModuleMissing):
    return JSONResponse(status_code=503, content={"error": "module_missing", "detail": str(exc),
                                                  "hint": "A component from another agent is not available yet; the server is up."})


def _pilot() -> dict:
    return state.get_pilot()


def _f(x) -> float:
    return float(x) if x is not None and np.isfinite(x) else 0.0


# ----------------------------------------------------------------------------- root / static
if not config.ON_VERCEL:          # on Vercel "/" is the frontend (app.frontend at the end of this module)
    @app.get("/", include_in_schema=False)
    def root():
        return _index_response()


@app.get("/api/health")
def health():
    return {"ok": True, "data_mode": state.data_mode(), "runs": state.list_runs()}


@app.get("/health", include_in_schema=False)
def health_plain():
    """Liveness for load balancers / Docker HEALTHCHECK: answers without touching the pilot or any upstream."""
    return {"ok": True}


@app.get("/api/data-status")
def data_status():
    """Compact source-health report. Makes NO upstream call: it reports what the last attempts found, the IMD
    token state, the cache and the source the last `auto` run used -- so it is cheap to poll. Never contains a
    credential (see imd_auth.TokenStatus.to_public_dict)."""
    from ..rainfall.imd_auth import manager as imd
    from ..rainfall.provider import LIVE_ID, IMD_SRI_ID, ECMWF_ID
    h = state.source_manager.health()
    try:
        _pilot()
        engine = {"ok": True, "pilot": state.data_mode()}
    except Exception as ex:  # noqa: BLE001
        engine = {"ok": False, "reason": type(ex).__name__}
    auth = imd.status().to_public_dict()
    src = h["sources"]
    from ..rainfall.source_manager import active_source_metadata
    latest = state.latest_run()
    active = active_source_metadata((latest.provenance or {}).get("rainfall_source")) if latest is not None else None
    return {"imd_auth_status": auth["state"], "imd_refresh_status": auth["refresh_status"],
            "imd_auto_renewal": auth["auto_renewal"],
            "imd_token_expires_at": auth["expires_at"], "imd_token_expiry_basis": auth["expiry_basis"],
            "active_rainfall_source": active["source_label"] if active else None,
            "active_source_timestamp": active["timestamp"] if active else None,
            "fallback_active": bool(active and active["status"] != "ok"),
            "active_source": active,
            "backend": {"ok": True, "time": datetime.now(timezone.utc).isoformat()},
            "engine": engine,
            "imd_auth": auth,
            "imd_live": src.get(LIVE_ID, {"ok": None, "reason": "not tried yet"}),
            "radar": src.get(IMD_SRI_ID, {"ok": None, "reason": "not tried yet"}),
            "ecmwf": src.get(ECMWF_ID, {"ok": None, "reason": "not tried yet"}),
            "cache": h["cache"], "current_rainfall_source": h["current"],
            "priority": ["LIVE", "RADAR-DERIVED", "FORECAST", "CACHED", "DEMO"]}


def _require_admin(request: Request) -> None:
    """Admin endpoints exist only when FLOODNET_ADMIN_TOKEN is set, and need it as `X-Admin-Token`."""
    import hmac
    expected = os.environ.get("FLOODNET_ADMIN_TOKEN") or ""
    if not expected:
        raise HTTPException(404, "not found")                       # feature off: do not advertise it
    supplied = request.headers.get("X-Admin-Token") or ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(403, "forbidden")


@app.get("/api/admin/egress-ip", include_in_schema=False)
def egress_ip(request: Request):
    """The public IP this backend's OUTBOUND requests come from -- the address the IMD API key must be registered
    for (on Vercel: the project's Static IP). Asks two public what-is-my-IP services; returns only the IP.
    Protected like the other admin endpoints so it cannot be used to make this server send requests at will."""
    _require_admin(request)
    import httpx
    seen = {}
    for name, url in (("ipify", "https://api.ipify.org"), ("aws", "https://checkip.amazonaws.com")):
        try:
            ip = httpx.get(url, timeout=8.0).text.strip()
            if 7 <= len(ip) <= 45 and all(ch in "0123456789abcdefABCDEF.:" for ch in ip):
                seen[name] = ip
        except Exception as ex:  # noqa: BLE001
            seen[name] = f"unavailable ({type(ex).__name__})"
    ips = sorted({v for v in seen.values() if not v.startswith("unavailable")})
    return {"backend_public_egress_ip": ips[0] if len(ips) == 1 else None, "observed": seen,
            "consistent": len(ips) == 1, "vercel": config.ON_VERCEL,
            "vercel_region": os.environ.get("VERCEL_REGION")}


@app.post("/api/admin/imd-token", include_in_schema=False)
async def rotate_imd_token(request: Request):
    """Rotate the IMD bearer token in the RUNNING server (no restart, no frontend rebuild). Disabled unless
    FLOODNET_ADMIN_TOKEN is set; callers must send it as `X-Admin-Token`. The new IMD token is read from the
    JSON body {"token": "..."}; it is never echoed, logged or returned."""
    from ..rainfall.imd_auth import manager as imd
    _require_admin(request)
    try:
        body = await request.json()
        token = str(body["token"]).strip()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "body must be JSON: {\"token\": \"<IMD bearer token>\"}") from None
    if not (20 <= len(token) <= 4096) or any(c.isspace() for c in token):
        raise HTTPException(422, "token has an implausible length or contains whitespace")
    imd.reset()
    imd.set_runtime_token(token)
    from ..rainfall.provider import list_providers, LIVE_ID
    list_providers()[LIVE_ID].clear_cache()
    return {"ok": True, "imd_auth": imd.status().to_public_dict()}


@app.get("/api/status")
def status():
    """Richer health check: also lists the rainfall sources available today (see floodnet.rainfall)."""
    from ..rainfall.provider import provider_status
    return {"ok": True, "data_mode": state.data_mode(), "runs": state.list_runs(),
            "rainfall_providers": provider_status()}


@app.get("/api/provenance")
def provenance():
    p = _pilot()
    return {**state.pilot_provenance(p), "attribution": state.ATTRIBUTION}


# ----------------------------------------------------------------------------- meta / topology / terrain
@app.get("/api/meta")
def meta():
    p = _pilot()
    g = p["terrain"].grid
    return {"pilot": {"name": config.PILOT_NAME, "bbox_lonlat": list(config.PILOT_BBOX_LONLAT),
                      "grid_bbox_lonlat": state.grid_bbox_lonlat(g), "crs": config.CRS_COMPUTE,
                      "grid": {"x0": g.x0, "y0": g.y0, "res": g.res, "nx": g.nx, "ny": g.ny}},
            "data_mode": state.data_mode(),
            "n_nodes": int(p["net"].n_nodes), "n_edges": int(p["net"].n_edges),
            "n_road_segments": int(len(p["roads"].segments)) if p.get("roads") is not None else 0,
            "scenario_ids": list(p["scenarios"].keys()),
            "severity_bands_cm": config.SEVERITY_BANDS_CM, "vehicle_limit_cm": config.VEHICLE_LIMIT_CM,
            "frame_dt_min": config.FRAME_DT_S // 60, "horizon_min": config.HORIZON_S // 60,
            "provenance": state.pilot_provenance(p), "attribution": state.ATTRIBUTION}


@app.get("/api/topology")
def topology():
    p = _pilot(); net = p["net"]
    lon, lat = state.xy_to_lonlat(net.node_x, net.node_y)
    nodes = [{"id": str(net.node_id[k]), "lon": _f(lon[k]), "lat": _f(lat[k]), "ground_m": _f(net.node_ground[k]),
              "invert_m": _f(net.node_invert[k]), "is_outfall": bool(net.node_is_outfall[k])}
             for k in range(net.n_nodes)]
    edges = []
    for e in range(net.n_edges):
        us, ds = int(net.edge_us[e]), int(net.edge_ds[e])
        edges.append({"id": str(net.edge_id[e]), "us": str(net.node_id[us]), "ds": str(net.node_id[ds]),
                      "geom": [[_f(lon[us]), _f(lat[us])], [_f(lon[ds]), _f(lat[ds])]],
                      "shape": str(net.edge_shape[e]), "width_m": _f(net.edge_width_m[e]),
                      "height_m": _f(net.edge_height_m[e]), "length_m": _f(net.edge_length_m[e]),
                      "capacity_m3s": _f(net.edge_capacity_m3s[e]), "status": str(net.edge_status[e]),
                      "blockage": _f(net.edge_blockage[e])})
    return {"nodes": nodes, "edges": edges, "provenance": state.pilot_provenance(p)["network"],
            "data_mode": state.data_mode()}


@app.get("/api/terrain")
def terrain():
    p = _pilot(); t = p["terrain"]; g = t.grid
    png, zmin, zmax = grayscale_png_base64(t.z)
    return {"grid": g.to_dict(), "bbox_lonlat": state.grid_bbox_lonlat(g), "z_min": zmin, "z_max": zmax,
            "png_base64": png, "provenance": t.provenance.to_dict(),
            "impervious_mean": _f(np.nanmean(t.impervious)), "building_fraction": _f(np.mean(t.building)),
            "data_mode": state.data_mode()}


_dem_payload_cache: dict = {}


def dem_payload(t) -> dict:
    """The model's OWN terrain array, for the 3D terrain view -- no second dataset, no resampling, no scaling.

    ENCODING (lossless): `z_base64` is base64 of the raw little-endian IEEE-754 float32 bytes of `Terrain.z`,
    row-major [ny, nx], row 0 = SOUTH (the Grid convention), one value per model cell CENTRE, metres above the
    datum in `datum`. Decoding reproduces the simulator's values bit-for-bit; `sha256` is the hash of those bytes
    so any consumer (and tests) can prove it. Non-finite cells, if any ever exist, are transmitted as NaN and
    counted in `nodata_count` -- never filled here.

    `lonlat_to_grid` is a least-squares AFFINE map lon/lat -> metres east/north of the grid's SW corner, so the
    browser can place lon/lat overlays (streets) in the model's own UTM frame without a projection library; its
    worst-case error over the pilot is reported as `max_residual_m`.
    """
    import base64
    import hashlib
    g = t.grid
    key = (id(t.z), g.nx, g.ny)
    if key in _dem_payload_cache:
        return _dem_payload_cache[key]
    z = np.ascontiguousarray(t.z, dtype="<f4")
    raw = z.tobytes()
    finite = np.isfinite(z)

    # affine lon/lat -> grid metres, fitted on a lattice of the grid's own cell centres
    jj, ii = np.meshgrid(np.linspace(0, g.ny - 1, 12), np.linspace(0, g.nx - 1, 12), indexing="ij")
    gx, gy = (ii.ravel() + 0.5) * g.res, (jj.ravel() + 0.5) * g.res
    lon, lat = state.xy_to_lonlat(g.x0 + gx, g.y0 + gy)
    A = np.column_stack([lon, lat, np.ones_like(lon)])
    cx, *_ = np.linalg.lstsq(A, gx, rcond=None)
    cy, *_ = np.linalg.lstsq(A, gy, rcond=None)
    resid = float(np.max(np.hypot(A @ cx - gx, A @ cy - gy)))

    out = {
        "grid": g.to_dict(), "bbox_lonlat": state.grid_bbox_lonlat(g), "shape": [int(g.ny), int(g.nx)],
        "dtype": "float32", "byte_order": "little", "row0": "south", "cell_registration": "centre",
        "units": "m", "datum": "mTHD (Town Hall Datum); offset to mean sea level unverified",
        "encoding": "base64(raw IEEE-754 float32, row-major [ny, nx]) -- lossless, identical to the simulation DEM",
        "z_base64": base64.b64encode(raw).decode("ascii"), "sha256": hashlib.sha256(raw).hexdigest(),
        "z_min": float(np.nanmin(z)), "z_max": float(np.nanmax(z)),
        "z_p01": float(np.nanpercentile(z, 1)), "z_p99": float(np.nanpercentile(z, 99)),
        "nodata_count": int((~finite).sum()),
        "building_base64": base64.b64encode(np.ascontiguousarray(t.building, dtype=np.uint8).tobytes()).decode("ascii"),
        "lonlat_to_grid": {"x": [float(v) for v in cx], "y": [float(v) for v in cy], "max_residual_m": resid},
        "provenance": t.provenance.to_dict(),
    }
    _dem_payload_cache.clear()
    _dem_payload_cache[key] = out
    return out


@app.get("/api/terrain/dem")
def terrain_dem():
    """Exact simulation DEM for the 3D terrain view (see `dem_payload`). Static for the life of the pilot."""
    return JSONResponse(dem_payload(_pilot()["terrain"]), headers={"Cache-Control": "public, max-age=3600"})


# ----------------------------------------------------------------------------- roads / hotspots / scenarios
def _seg_feature(seg, props: dict) -> dict:
    coords = np.asarray(seg.lonlat, dtype=float).tolist()
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"seg_id": seg.seg_id, "name": seg.name, "highway": seg.highway, **props}}


@app.get("/api/roads")
def roads():
    p = _pilot(); r = p.get("roads")
    if r is None:
        raise state.ModuleMissing("roads not present in pilot bundle (floodnet.data.osm / build_pilot)")
    feats = [_seg_feature(s, {"length_m": _f(s.length_m), "oneway": bool(s.oneway)}) for s in r.segments]
    return {"type": "FeatureCollection", "features": feats, "provenance": r.provenance.to_dict(),
            "attribution": state.ATTRIBUTION[0], "data_mode": state.data_mode()}


HOTSPOT_PROV = "MCGM Flooding Spots layer 344 (REAL, vintage ~2017)"


@app.get("/api/hotspots")
def hotspots():
    p = _pilot(); hs = p.get("hotspots") or []
    feats = []
    for h in hs:
        lon = h.get("lon", h.get("centroid_lon")); lat = h.get("lat", h.get("centroid_lat"))
        cen = h.get("lonlat_centroid") or h.get("centroid")
        if lon is None and cen:
            lon, lat = cen[0], cen[1]
        if lon is None and h.get("x") is not None:
            lon, lat = state.xy_to_lonlat(h["x"], h["y"])
        props = {"name": h.get("name", ""), "location": h.get("location", ""), "ward": h.get("ward", ""),
                 "affect_road": h.get("affect_road", ""), "stretch_m": h.get("stretch_m"),
                 "depth_attr": h.get("depth_attr", h.get("depth", "")), "active": h.get("active", True),
                 "provenance": h.get("provenance", HOTSPOT_PROV)}
        if lon is not None:
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [_f(lon), _f(lat)]},
                          "properties": {**props, "kind": "centroid"}})
        poly = h.get("polygon_lonlat") or h.get("polygon")
        if poly:
            ring = np.asarray(poly, dtype=float).tolist()
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                          "properties": {**props, "kind": "polygon"}})
    return {"type": "FeatureCollection", "features": feats, "provenance": {"tag": "REAL", "source": HOTSPOT_PROV},
            "data_mode": state.data_mode()}


@app.get("/api/scenarios")
def scenarios():
    p = _pilot()
    from ..rainfall.provider import get_source_meta
    out = []
    for s in p["scenarios"].values():
        d = s.to_dict()
        meta = get_source_meta(s.id)
        d["source"] = meta.to_dict() if meta is not None else None
        out.append(d)
    return out


# ----------------------------------------------------------------------------- simulation serialisation
def _frame_index(res: SimulationResult, t_min: float) -> int:
    ts = np.array([f.t_s for f in res.frames]); k = int(np.argmin(np.abs(ts - float(t_min) * 60.0)))
    return k


def _street_stats(f: Frame) -> tuple[int, float]:
    if not f.street_depth_m:
        return 0, 0.0
    v = np.array(list(f.street_depth_m.values()), dtype=float) * 100.0
    return int(np.sum(v >= FLOOD_SEG_CM)), _f(v.max()) if v.size else 0.0


def _frame_max_cm(f: Frame) -> float:
    return _f(np.nanmax(f.depth) * 100.0) if f.depth.size else 0.0


_BASELINE_BLOCKAGE_PROVENANCE = {
    "tag": "SYNTHETIC", "source": "baseline (no blockage applied)",
    "note": "Drainage assumed clear (0% blockage). FloodNet has no live drain-condition telemetry; this is "
            "always an operator-set scenario assumption, never an observation."}


def _blockage_provenance(res: SimulationResult) -> dict:
    """Explicit, always-present blockage provenance for a run.

    When a non-'none' blockage mode was applied, floodnet.drainage.scenarios.apply_blockage already writes a
    correctly SYNTHETIC-tagged entry at res.provenance['network']['blockage'] -- this just also surfaces it at
    the top level for easier frontend access (nested entry is left untouched).

    When mode='none', api/state.py's apply_blockage short-circuits before ever calling
    drainage.scenarios.apply_blockage (see state.py:268-270), so no blockage provenance is written anywhere in
    that case. Rather than leave the key silently absent, construct an equally explicit baseline entry here
    (main.py only -- state.py is not touched)."""
    net_blockage = (res.provenance.get("network") or {}).get("blockage")
    if net_blockage is not None:
        return dict(net_blockage)
    return dict(_BASELINE_BLOCKAGE_PROVENANCE)


def summarize(res: SimulationResult) -> dict:
    frames = res.frames
    frames_t_min = [f.t_s / 60.0 for f in frames]
    max_cm = [_frame_max_cm(f) for f in frames]
    surch = [int(np.sum(f.node_surcharging)) for f in frames]
    flooded = [_street_stats(f)[0] for f in frames]
    mb = res.mass_balance
    peak_idx = max(range(len(max_cm)), key=lambda i: max_cm[i]) if max_cm else None
    # New dict, not a mutation of res.provenance -- that dict object is shared across every cached copy of this
    # run (state.py's _run_scenario_cached + copy.copy(cached)), so writing into it in place would leak across
    # unrelated requests that happen to share the same (scenario_id, blockage, horizon) cache entry.
    provenance = dict(res.provenance)
    provenance["blockage"] = _blockage_provenance(res)
    return {"run_id": res.run_id, "scenario_id": res.scenario.id, "scenario_name": res.scenario.name,
            "blockage": res.blockage, "frames_t_min": frames_t_min,
            "mass_balance": {k: _f(v) for k, v in vars(mb).items()},
            "runtime_s": _f(res.runtime_s), "n_frames": len(frames),
            "summary": {"max_depth_cm": max(max_cm) if max_cm else 0.0,
                        "peak_depth_t_min": frames_t_min[peak_idx] if peak_idx is not None else None,
                        "peak_surcharging_nodes": max(surch) if surch else 0,
                        "peak_flooded_segments": max(flooded) if flooded else 0,
                        "total_surcharge_m3": _f(sum(float(f.node_surcharge_m3.sum()) for f in frames)),
                        "peak_rain_mm_h": _f(max((f.rain_mm_h for f in frames), default=0.0))},
            "notes": list(res.notes), "provenance": provenance, "data_mode": state.run_data_mode(res),
            "active_source": active_source_metadata((res.provenance or {}).get("rainfall_source"))}


def _severity(cm: float) -> str:
    try:
        from ..streets.aggregate import severity
        return severity(cm)
    except Exception:  # noqa: BLE001
        for lim, label in config.SEVERITY_BANDS_CM:
            if cm < lim:
                return label
        return "critical"


def _passable(cm: float, vehicle: str) -> bool:
    try:
        from ..streets.aggregate import passable
        return bool(passable(cm, vehicle))
    except Exception:  # noqa: BLE001
        return cm < config.VEHICLE_LIMIT_CM.get(vehicle, 30)


def _streets_geojson(roads_graph, street_depth_m: dict) -> dict:
    try:
        from ..streets.aggregate import streets_geojson
        return streets_geojson(roads_graph, street_depth_m)
    except Exception:  # noqa: BLE001
        pass
    feats = []
    if roads_graph is not None:
        for s in roads_graph.segments:
            cm = _f(street_depth_m.get(s.seg_id, 0.0)) * 100.0
            feats.append(_seg_feature(s, {"depth_cm": round(cm, 1), "severity": _severity(cm),
                                          "passable_car": _passable(cm, "car"),
                                          "passable_ambulance": _passable(cm, "ambulance")}))
    return {"type": "FeatureCollection", "features": feats}


@lru_cache(maxsize=state.MAX_RUNS + 4)
def _run_max_depth_m(run_id: str) -> float:
    """The scale used across every frame of a run must be the run-wide max, not the per-frame max (else the
    colour scale would jump around while scrubbing) -- but recomputing max() over every frame's full grid on
    *every* single-frame request (the frontend does this on every timeline scrub) was pure repeated work: the
    result is fixed the moment a run finishes. Cached by run_id, which floodnet.simulation.engine mints as a
    fresh uuid4 per run, so a cache hit always belongs to the same immutable SimulationResult."""
    res = state.get_run(run_id)
    if res is None:
        return 0.0
    return max((float(np.nanmax(fr.depth)) for fr in res.frames), default=0.0)


def _node_ground_floor(net) -> np.ndarray:
    """Per-node storage ceiling, matching drainage.hydraulics.GraphDrainage's own
    `ground = max(node_ground, node_invert + MIN_DEPTH_M)` (hydraulics.py:62) exactly, so fill_frac/freeboard_m
    derived from the already-serialized node_hgl stay consistent with the solver's own HGL cap."""
    return np.maximum(np.asarray(net.node_ground, dtype=np.float64),
                       np.asarray(net.node_invert, dtype=np.float64) + MIN_DEPTH_M)


def _node_fill_state(net, f: Frame, ground_floor: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per-node derived fill fraction / freeboard / surcharging(fixed) / state, for one frame.

    Bug fix (surcharging): node_surcharging reflects only the LAST 5 s solver sub-step of the 300 s frame
    interval, while node_surcharge_m3 accumulates the WHOLE interval -- a node that surcharged earlier in the
    interval but drained by the final sub-step reports surcharging=False despite surcharge_m3 > 0. Treat a
    node as surcharging for display if either signal says so (Frame itself is not modified).

    at_capacity uses the solver's own "full and blocking upstream flow" threshold (hydraulics.py:160,
    `V >= vfull * 0.999`), reproduced here as fill_frac >= 0.999 since fill_frac is HGL-derived from the same
    invert/ground geometry."""
    invert = np.asarray(net.node_invert, dtype=np.float64)
    span = np.maximum(ground_floor - invert, 1e-9)
    fill_frac = np.clip((np.asarray(f.node_hgl, dtype=np.float64) - invert) / span, 0.0, 1.0)
    freeboard_m = ground_floor - np.asarray(f.node_hgl, dtype=np.float64)
    surcharging_fixed = np.asarray(f.node_surcharging, dtype=bool) | (np.asarray(f.node_surcharge_m3, dtype=np.float64) > 1e-9)
    at_capacity = (~surcharging_fixed) & (fill_frac >= 0.999)
    state_arr = np.where(surcharging_fixed, "surcharging", np.where(at_capacity, "at_capacity", "normal"))
    return fill_frac, freeboard_m, surcharging_fixed, state_arr


def serialize_frame(res: SimulationResult, k: int) -> dict:
    p = _pilot(); net = p["net"]; f = res.frames[k]
    lon, lat = state.xy_to_lonlat(net.node_x, net.node_y)
    ground_floor = _node_ground_floor(net)
    fill_frac, freeboard_m, surcharging_fixed, node_state = _node_fill_state(net, f, ground_floor)
    nodes = [{"id": str(net.node_id[n]), "lon": _f(lon[n]), "lat": _f(lat[n]), "hgl_m": _f(f.node_hgl[n]),
              "surcharging": bool(surcharging_fixed[n]), "surcharge_m3": _f(f.node_surcharge_m3[n]),
              "cause": str(f.node_cause[n]), "fill_frac": _f(fill_frac[n]), "freeboard_m": _f(freeboard_m[n]),
              "state": str(node_state[n])} for n in range(net.n_nodes)]
    edges = [{"id": str(net.edge_id[e]), "util": _f(f.edge_util[e]), "flow_m3s": _f(f.edge_flow_m3s[e]),
              "edge_capacity_m3s": _f(net.edge_capacity_m3s[e]), "blockage": _f(net.edge_blockage[e])}
             for e in range(net.n_edges)]
    run_max_m = _run_max_depth_m(res.run_id)
    png, vmax = depth_png_base64(f.depth, max_depth_m=run_max_m if run_max_m > 0 else None)
    return {"run_id": res.run_id, "t_min": f.t_s / 60.0, "rain_mm_h": _f(f.rain_mm_h),
            "streets": _streets_geojson(p.get("roads"), f.street_depth_m),
            "nodes": nodes, "edges": edges,
            "depth_grid": {"grid": res.grid.to_dict(), "bbox_lonlat": state.grid_bbox_lonlat(res.grid),
                           "png_base64": png, "max_depth_cm": _frame_max_cm(f), "scale_max_cm": vmax * 100.0},
            "provenance": res.provenance, "data_mode": state.run_data_mode(res)}


def _get_run_or_404(run_id: str) -> SimulationResult:
    r = state.get_run(run_id)
    if r is None:
        raise _run_not_found(run_id)
    return r


def _run_not_found(run_id: Optional[str]) -> HTTPException:
    """Runs live in the memory of the instance that computed them. On a serverless host (Vercel) a follow-up
    request can reach a different or freshly started instance, so a missing run is reported with a stable code:
    the frontend client then re-runs the same simulation once and retries (frontend-react/src/api/client.js)."""
    return HTTPException(404, {"code": "run_not_found", "run_id": run_id,
                               "message": f"run {run_id!r} is not held by this server instance "
                                          f"(it keeps the last {state.MAX_RUNS} runs in memory); run the simulation again"})


async def _simulate(scenario_id: str, blockage: dict, horizon_min: int) -> SimulationResult:
    from ..rainfall.provider import (LIVE_ID, ECMWF_ID, RADAR_ID, IMERG_ID, SPATIAL_SYNTHETIC_ID, IMD_SRI_ID,
                                     ProviderUnavailable)
    p = _pilot()
    # RADAR_ID is admitted here on purpose even though it can never produce a run: the radar provider always
    # raises ProviderUnavailable -> HTTP 503 with an explicit "ACCESS PENDING ..." reason. Excluding it would
    # instead 404 as "unknown scenario_id", which reads as "no such thing" rather than the truth ("real
    # integration boundary, deliberately not connected"). See docs/DECISIONS.md D-14.
    # IMERG_ID is likewise admitted: it is a real, CC0-licensed gridded satellite source that 503s with an
    # explicit "set EARTHDATA_TOKEN" reason when uncredentialed -- again the honest answer, not a 404.
    _SOURCE_IDS = (LIVE_ID, ECMWF_ID, RADAR_ID, IMERG_ID, SPATIAL_SYNTHETIC_ID, IMD_SRI_ID, state.AUTO_ID, state.DEMO_ID)
    if scenario_id not in _SOURCE_IDS and scenario_id not in p["scenarios"]:
        raise HTTPException(404, f"unknown scenario_id {scenario_id!r}; "
                                 f"available: {list(p['scenarios']) + list(_SOURCE_IDS)}")
    try:
        return await run_in_threadpool(state.run_scenario, scenario_id, blockage, horizon_min)
    except state.ModuleMissing:
        raise
    except ProviderUnavailable as e:
        # LIVE/ECMWF requested but not usable right now (no IMD_API_KEY, the upstream call failed, or an
        # insufficient/malformed response) -- 503, not a silent fallback to synthetic data still labelled as
        # the requested live/forecast source. See docs/LIVE_RAINFALL_AUDIT.md and docs/ECMWF_OPENMETEO_AUDIT.md.
        raise HTTPException(503, f"rainfall provider unavailable: {e}")
    except ValueError as e:
        raise HTTPException(422, str(e))


# ----------------------------------------------------------------------------- simulation endpoints
# Longest forecast horizon accepted. On Vercel a request must finish inside the function's maxDuration (300 s on
# Hobby); a 720-min run measured ~235-240 s on one fast laptop core, so it is refused there with a clear message
# instead of being cut off mid-run. FLOODNET_MAX_HORIZON_MIN overrides.
MAX_HORIZON_MIN = int(os.environ.get("FLOODNET_MAX_HORIZON_MIN") or (360 if config.ON_VERCEL else 720))


def _check_horizon(horizon_min: int) -> None:
    if horizon_min > MAX_HORIZON_MIN:
        raise HTTPException(422, f"horizon_min {horizon_min} exceeds {MAX_HORIZON_MIN} min, the longest forecast "
                                 "this deployment can finish within its request time limit")


async def _complete(res: SimulationResult, summary: Optional[dict] = None) -> dict:
    """Self-contained response (api/bundle.py): everything the dashboard renders, in THIS response, so nothing
    afterwards depends on which server instance computed the run."""
    from .bundle import self_contained

    def build() -> dict:
        try:
            alert, alert_error = _alert_payload(res), None
        except ValueError as e:                                   # a run that cannot yield a CAP draft
            alert, alert_error = None, str(e)
        return self_contained(res, summary or summarize(res), _series_payload(res, include_streets=False),
                              _hotspots_payload(res),
                              alert, alert_error, _pilot())
    return await run_in_threadpool(build)


@app.post("/api/simulate")
async def simulate(req: SimulateRequest):
    _check_horizon(req.horizon_min)
    res = await _simulate(req.scenario_id, req.blockage.to_spec(), req.horizon_min)
    return await _complete(res)


@app.get("/api/simulate/{run_id}")
def get_simulate(run_id: str):
    """GET analog of POST /api/simulate's result: re-fetch an existing cached run's summary cheaply,
    without re-running the physics."""
    res = _get_run_or_404(run_id)
    return summarize(res)


def _check_t_min(t_min: float) -> None:
    if t_min < 0:
        raise HTTPException(422, f"t_min must be >= 0, got {t_min!r}")


@app.get("/api/simulation/{run_id}/frame/{t_min}")
def frame(run_id: str, t_min: float):
    _check_t_min(t_min)
    res = _get_run_or_404(run_id)
    return serialize_frame(res, _frame_index(res, t_min))


_CAUSE_KEYS = ("overcapacity", "downstream", "blockage")


@app.get("/api/simulation/{run_id}/series")
def series(run_id: str):
    return _series_payload(_get_run_or_404(run_id))


def _series_payload(res: SimulationResult, include_streets: bool = True) -> dict:
    """include_streets=False omits the per-street series: POST /api/simulate drops it anyway (bundle.self_contained
    rebuilds it in the browser from the packed street depths), and building it was ~110k float conversions per
    request (docs/PERFORMANCE_OPTIMIZATION.md, 2026-10-11). GET .../series keeps the full payload."""
    fr = res.frames
    p = _pilot(); net = p["net"]
    ground_floor = _node_ground_floor(net)

    # Aggregate drainage-distress signals that already exist per-frame (edge_util, node_cause) but were
    # previously invisible in this series -- e.g. edges can sit at/above full-bore capacity for many minutes
    # before any node actually surcharges, which is exactly the lead time surcharging_count alone misses.
    cause_counts: dict[str, list[int]] = {c: [] for c in _CAUSE_KEYS}
    edges_at_capacity: list[int] = []
    edges_near_capacity: list[int] = []  # descriptive only: "conduits at or above 80% of design flow" -- NOT a
    # validated distress threshold the way node fill=0.999 is; never present this as a severity/danger line.
    mean_edge_util: list[float] = []
    max_edge_util: list[float] = []
    nodes_at_capacity: list[int] = []
    surcharging_count: list[int] = []
    for f in fr:
        cause_arr = np.asarray(f.node_cause)
        for c in _CAUSE_KEYS:
            cause_counts[c].append(int(np.sum(cause_arr == c)))
        util = np.asarray(f.edge_util, dtype=np.float64)
        edges_at_capacity.append(int(np.sum(util >= 0.999)))
        edges_near_capacity.append(int(np.sum(util >= 0.8)))
        mean_edge_util.append(_f(np.mean(util)) if util.size else 0.0)
        max_edge_util.append(_f(np.max(util)) if util.size else 0.0)
        fill_frac, _fb, surcharging_fixed, _st = _node_fill_state(net, f, ground_floor)
        nodes_at_capacity.append(int(np.sum((~surcharging_fixed) & (fill_frac >= 0.999))))
        surcharging_count.append(int(np.sum(surcharging_fixed)))

    # surcharging_count uses the same interval-aware definition serialize_frame() reports per node
    # (raw flag OR surcharge_m3 > 0), so the timeline and the map never disagree about the same frame.
    # The raw last-sub-step flag is preserved separately as surcharging_count_instant rather than dropped:
    # it is what the solver's node_surcharging array literally contains, and older validation figures
    # (docs/VALIDATION.md) were computed from it.
    out = {"run_id": res.run_id, "t_min": [f.t_s / 60.0 for f in fr], "rain_mm_h": [_f(f.rain_mm_h) for f in fr],
            "surcharging_count": surcharging_count,
            "surcharging_count_instant": [int(np.sum(f.node_surcharging)) for f in fr],
            "flooded_segments": [_street_stats(f)[0] for f in fr],
            "max_depth_cm": [_frame_max_cm(f) for f in fr],
            "surcharge_m3": [_f(f.node_surcharge_m3.sum()) for f in fr],
            "cause_counts": cause_counts, "edges_at_capacity": edges_at_capacity,
            "edges_near_capacity": edges_near_capacity, "mean_edge_util": mean_edge_util,
            "max_edge_util": max_edge_util, "nodes_at_capacity": nodes_at_capacity}
    if include_streets:
        seg_ids = sorted({sid for f in fr for sid in f.street_depth_m.keys()})
        out["streets"] = {sid: [round(_f(f.street_depth_m.get(sid, 0.0)) * 100.0, 1) for f in fr] for sid in seg_ids}
    out["provenance"] = res.provenance
    out["data_mode"] = state.run_data_mode(res)
    return out


# ----------------------------------------------------------------------------- explain a road segment
_DOMINANT_CAUSE_MAP = {"overcapacity": "drainage_overcapacity", "blockage": "drainage_blockage",
                       "downstream": "drainage_downstream_backup"}


def _segment_midpoint_xy(seg) -> tuple[float, float]:
    """Geometric midpoint (by arc length) of a RoadSegment's polyline, in EPSG:32643."""
    xy = np.asarray(seg.xy, dtype=float)
    if len(xy) == 0:
        return 0.0, 0.0
    if len(xy) == 1:
        return float(xy[0, 0]), float(xy[0, 1])
    step = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(step)])
    total = float(cum[-1])
    if total <= 0:
        return float(xy[0, 0]), float(xy[0, 1])
    half = total / 2.0
    k = int(np.searchsorted(cum, half))
    k = max(1, min(k, len(xy) - 1))
    span = cum[k] - cum[k - 1]
    t = (half - cum[k - 1]) / span if span > 0 else 0.0
    x = xy[k - 1, 0] + t * (xy[k, 0] - xy[k - 1, 0])
    y = xy[k - 1, 1] + t * (xy[k, 1] - xy[k - 1, 1])
    return float(x), float(y)


def _find_segment(roads_graph, seg_id: str):
    for s in roads_graph.segments:
        if s.seg_id == seg_id:
            return s
    return None


def _peak_frame_index(res: SimulationResult, seg_id: str) -> int:
    """Frame at which this segment's depth peaks over the run (default t_min for /explain)."""
    depths = [float(f.street_depth_m.get(seg_id, 0.0)) for f in res.frames]
    return int(np.argmax(depths)) if depths else 0


@app.get("/api/simulation/{run_id}/explain/{seg_id}")
def explain(run_id: str, seg_id: str, t_min: Optional[float] = None):
    """Explain a road segment's flood state at a given time: its depth, the nearest drainage node and
    why it is (or isn't) surcharging, and the ground elevation under it.

    Default t_min (query param omitted): the frame where this segment's depth peaks over the run.
    Every value here is read from the already-computed SimulationResult / pilot data -- nothing here is a
    fabricated confidence score or an invented field the engine doesn't actually produce.
    """
    if t_min is not None:
        _check_t_min(t_min)
    res = _get_run_or_404(run_id)
    p = _pilot()
    roads_graph = p.get("roads")
    if roads_graph is None:
        raise state.ModuleMissing("roads not present in pilot bundle; cannot explain a road segment")
    seg = _find_segment(roads_graph, seg_id)
    if seg is None:
        raise HTTPException(404, f"unknown seg_id {seg_id!r}")

    k = _frame_index(res, t_min) if t_min is not None else _peak_frame_index(res, seg_id)
    f = res.frames[k]
    depth_cm = _f(f.street_depth_m.get(seg_id, 0.0)) * 100.0

    net = p["net"]
    mx, my = _segment_midpoint_xy(seg)
    from scipy.spatial import cKDTree
    tree = cKDTree(np.column_stack([net.node_x, net.node_y]))
    dist, node_idx = tree.query([mx, my])
    node_idx = int(node_idx)
    incident = (net.edge_us == node_idx) | (net.edge_ds == node_idx)
    utilization = _f(f.edge_util[incident].max()) if incident.any() else 0.0
    node_cause_raw = str(f.node_cause[node_idx])
    # Same interval-aware definition serialize_frame() and series() report (raw last-sub-step flag OR any
    # volume spilled during the 300 s interval). Previously this endpoint used the raw flag alone, so a node
    # that surcharged earlier in the interval but drained by the final sub-step showed as surcharging here
    # while the map and timeline showed it as surcharging -- the same segment disagreeing with itself across
    # panels. Measured undercount of the raw flag: +12 to +25 nodes per frame across 11 frames of a cloudburst
    # run. See _node_fill_state()'s docstring for the underlying cause.
    surcharging = (bool(f.node_surcharging[node_idx])
                   or float(f.node_surcharge_m3[node_idx]) > 1e-9)
    if node_cause_raw in _DOMINANT_CAUSE_MAP:
        dominant_cause = _DOMINANT_CAUSE_MAP[node_cause_raw]
    elif depth_cm > 0:
        dominant_cause = "surface_ponding_only"
    else:
        dominant_cause = "unknown"

    terrain = p["terrain"]; grid = terrain.grid
    jarr, iarr = grid.cell_of(np.array([mx]), np.array([my]))
    j, i = int(jarr[0]), int(iarr[0])
    if bool(grid.inside(np.array([j]), np.array([i]))[0]):
        ground_m = _f(terrain.z[j, i])
        terrain_note = "ground elevation of the terrain grid cell under the segment's midpoint"
    else:
        ground_m = None
        terrain_note = "segment midpoint falls outside the terrain grid; no elevation available"

    net_prov = state.pilot_provenance(p)["network"]
    return {"run_id": res.run_id, "seg_id": seg.seg_id, "seg_name": seg.name, "t_min": f.t_s / 60.0,
            "depth_cm": round(depth_cm, 2), "severity": _severity(depth_cm),
            "passable_car": _passable(depth_cm, "car"), "passable_ambulance": _passable(depth_cm, "ambulance"),
            "rainfall_mm_h": _f(f.rain_mm_h),
            "nearest_drainage_node": {"id": str(net.node_id[node_idx]), "distance_m": _f(dist),
                                      "surcharging": surcharging, "cause": node_cause_raw,
                                      "utilization": utilization},
            "dominant_cause": dominant_cause,
            # peak / peak time / onset for THIS segment over the whole run (street inspector)
            "timing": segment_timing(res, seg.seg_id),
            "terrain_context": {"ground_elevation_m": ground_m, "note": terrain_note},
            "provenance": {"roads": roads_graph.provenance.to_dict(), "network": net_prov,
                           "terrain": terrain.provenance.to_dict()}}


# ----------------------------------------------------------------------------- flood hotspot intelligence
@app.get("/api/simulation/{run_id}/hotspots")
def hotspots(run_id: str, top: int = 5):
    """Operational summary derived from the finished run: max depth, earliest onset, peak time, flooded area,
    affected road length / intersections and the ranked top hotspots (floodnet/analysis/hotspots.py)."""
    return _hotspots_payload(_get_run_or_404(run_id), top)


def _hotspots_payload(res: SimulationResult, top: int = 5) -> dict:
    p = _pilot()
    out = flood_intelligence(res, p.get("roads"), p.get("terrain"), top_n=max(1, min(int(top), 20)))
    out.update({"run_id": res.run_id, "data_mode": state.run_data_mode(res)})
    return out


# ----------------------------------------------------------------------------- CAP alert draft
@app.get("/api/simulation/{run_id}/alert")
def alert_draft(run_id: str, format: str = "json"):  # noqa: A002 -- query param name is part of the contract
    """Draft an OASIS CAP 1.2 message from a completed run, for review by an authorised alerting authority.

    NOT an issued warning and NOT a dissemination endpoint. FloodNet is not a designated alerting authority:
    it has no NDMA SACHET credential, no integration path into one, no subscriber list and no cell-broadcast
    or SMS channel. This endpoint produces a *file* -- CAP `status = Draft`, which the specification itself
    defines as "A preliminary template or draft, not actionable in its current form" -- that an authorised
    officer can read, edit and, under their own authority, issue through their own system. See
    floodnet/alerts/cap.py for the specification references and the field-by-field derivation.

    `?format=xml` returns the CAP wire format (application/cap+xml) instead of the JSON envelope; the XML is
    byte-identical to the `cap_xml` string in the JSON response.
    """
    res = _get_run_or_404(run_id)
    if format not in ("json", "xml"):
        raise HTTPException(422, f"format must be 'json' or 'xml', got {format!r}")
    try:
        out = _alert_payload(res)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if format == "xml":
        return Response(content=out["cap_xml"], media_type="application/cap+xml",
                        headers={"Content-Disposition":
                                 f'attachment; filename="floodnet-cap-draft-{res.run_id}.xml"'})
    return out


def _alert_payload(res: SimulationResult) -> dict:
    """The CAP 1.2 DRAFT for a run (raises ValueError when the run cannot yield one)."""
    p = _pilot()
    from ..alerts.cap import CAP_NS, build_cap_alert
    draft = build_cap_alert(res, p["net"], p.get("roads"), data_mode=state.run_data_mode(res))
    xml = draft.to_xml()
    provenance = dict(res.provenance)
    provenance["blockage"] = _blockage_provenance(res)
    return {"run_id": res.run_id, "cap_namespace": CAP_NS, "cap_version": "1.2",
            "cap": draft.alert, "cap_xml": xml, "model_basis": draft.model_basis,
            "issued": False, "message_class": "MODEL_FORECAST_DRAFT_NOT_ISSUED",
            "disclaimer": draft.model_basis["disclaimer"],
            "xml_url": f"/api/simulation/{res.run_id}/alert?format=xml",
            "provenance": provenance, "data_mode": state.run_data_mode(res)}


# ----------------------------------------------------------------------------- guided-briefing voice
# ADDITIVE and outside the scientific pipeline: this endpoint touches no model, no rainfall, no drainage and
# no routing code. It exists solely so the Guided Briefing's neural voice key stays server-side -- the
# browser posts already-approved narration text (built from real simulation state in the frontend) and gets
# audio back. See floodnet/tts.py for the provider contract and the no-fabrication boundary.
@app.get("/api/tts/status")
def tts_status():
    """Whether the neural voice is configured. Cheap, no upstream call, never returns the key."""
    from .. import tts
    return {"available": tts.is_configured(), "reason": tts.unavailable_reason(),
            "languages": sorted(tts.LANG_TAGS)}


@app.post("/api/tts")
async def tts_synthesize(req: TTSRequest):
    from .. import tts
    try:
        out = await run_in_threadpool(tts.synthesize, req.text, req.lang)
    except tts.TTSUnavailable as e:
        # 503 with an honest reason, exactly like the rainfall providers. The frontend treats this as
        # "fall back to browser speech and show voice-unavailable", never as a reason to skip the briefing.
        raise HTTPException(503, f"text-to-speech unavailable: {e}")
    return Response(content=out.audio, media_type=out.media_type,
                    headers={"X-TTS-Voice": out.voice, "X-TTS-Language": out.language_code,
                             "Cache-Control": "no-store"})


@app.get("/api/nowcast")
def nowcast():
    res = state.latest_run()
    if res is None:
        raise HTTPException(404, "no simulation run yet; POST /api/simulate first")
    out = summarize(res); out["current_frame_index"] = 0
    return out


@app.post("/api/storm/replay")
async def storm_replay(req: ReplayRequest):
    p = _pilot(); ids = list(p["scenarios"].keys())
    sid = "july2005" if "july2005" in ids else next((i for i in ids if "2005" in i), None)
    if sid is None:
        raise HTTPException(404, f"no 26-July-2005 replay scenario available; scenarios: {ids}")
    _check_horizon(req.horizon_min)
    res = await _simulate(sid, req.blockage.to_spec(), req.horizon_min)
    out = await _complete(res); out["replay"] = True
    return out


@app.post("/api/compare")
async def compare(req: CompareRequest):
    spec = req.blockage.to_spec()
    if spec.get("mode", "none") == "none":
        raise HTTPException(422, "compare needs a blockage with mode != 'none'")
    _check_horizon(req.horizon_min)
    normal = await _simulate(req.scenario_id, {"mode": "none"}, req.horizon_min)
    blocked = await _simulate(req.scenario_id, spec, req.horizon_min)

    def side(res: SimulationResult) -> dict:
        # summarize() already builds a full, correctly-blockage-tagged provenance dict for this run (including
        # the explicit baseline entry when mode='none') -- reuse it here rather than re-deriving, so 'normal'
        # and 'blocked' each carry their own honest provenance instead of both being described by the
        # blocked-run's provenance (the previous top-level-only 'provenance' field did this).
        s = summarize(res)
        return {"run_id": res.run_id, "summary": s["summary"], "mass_balance": s["mass_balance"],
                "max_depth_cm": [_frame_max_cm(f) for f in res.frames],
                "surcharging_count": [int(np.sum(f.node_surcharging)) for f in res.frames],
                "flooded_segments": [_street_stats(f)[0] for f in res.frames],
                "provenance": s["provenance"]}
    n, b = side(normal), side(blocked)
    return {"scenario_id": req.scenario_id, "blockage": spec, "frames_t_min": [f.t_s / 60.0 for f in normal.frames],
            "normal": n, "blocked": {**b, "complete": await _complete(blocked)},
            "delta": {"max_depth_cm": b["summary"]["max_depth_cm"] - n["summary"]["max_depth_cm"],
                      "peak_flooded_segments": b["summary"]["peak_flooded_segments"] - n["summary"]["peak_flooded_segments"],
                      "peak_surcharging_nodes": b["summary"]["peak_surcharging_nodes"] - n["summary"]["peak_surcharging_nodes"]},
            "provenance": b["provenance"], "data_mode": state.run_data_mode(blocked)}


# ----------------------------------------------------------------------------- routing
def _route_depths(req: RouteRequest) -> tuple[Optional[SimulationResult], dict]:
    """Street depths to route on. Preferred: the frame's depths sent by the client from its self-contained run
    (works on any server instance). Fallback for the local server: the run held in this process's memory."""
    if req.street_depth_m is not None:
        return None, {str(k): float(v) for k, v in req.street_depth_m.items()}
    if not req.run_id:
        return None, {}                                  # no run on screen: dry network (never "this instance's last run")
    res: Optional[SimulationResult] = state.get_run(req.run_id)
    if res is None:
        raise _run_not_found(req.run_id)
    return res, res.frames[_frame_index(res, req.t_min)].street_depth_m


def _depth_provenance(req: RouteRequest, res: Optional[SimulationResult]) -> Optional[dict]:
    if res is not None:
        return res.provenance
    if req.street_depth_m is not None:
        return {"run_id": req.run_id, "t_min": req.t_min,
                "note": "frame street depths supplied by the client from its self-contained run"}
    return None


@app.post("/api/route")
async def route(req: RouteRequest):
    p = _pilot(); roads_graph = p.get("roads")
    if roads_graph is None:
        raise state.ModuleMissing("roads not present in pilot bundle; routing impossible")
    try:
        from ..routing.router import safe_route
    except Exception as e:  # noqa: BLE001
        raise state.ModuleMissing(f"floodnet.routing.router.safe_route unavailable (Agent F): {e}")
    res, depth = _route_depths(req)
    try:
        out: Any = await run_in_threadpool(safe_route, roads_graph, depth, tuple(req.origin), tuple(req.dest), req.vehicle)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    out = dict(out or {})
    has_depths = res is not None or req.street_depth_m is not None
    out.update({"t_min": req.t_min, "run_id": req.run_id or (res.run_id if res is not None else None),
                "vehicle": req.vehicle,
                "depth_source": "simulation frame" if has_depths else "no run: dry network assumed",
                "data_mode": state.run_data_mode(res) if res is not None else (req.data_mode or state.run_data_mode(None))})
    out.setdefault("provenance", {"roads": roads_graph.provenance.to_dict(), "depths": _depth_provenance(req, res)})
    return out


@app.post("/api/route/alternatives")
async def route_alternatives(req: RouteRequest):
    """Additive sibling of POST /api/route: returns up to req.n_candidates genuinely diverse route
    candidates, each scored under the safest / fastest(-by-distance) / balanced objectives (see
    floodnet.routing.router.safe_routes_multi's docstring for exactly what each objective means -- "fastest"
    is distance-only, there is no travel-time/speed model). Does not change POST /api/route's contract."""
    p = _pilot(); roads_graph = p.get("roads")
    if roads_graph is None:
        raise state.ModuleMissing("roads not present in pilot bundle; routing impossible")
    try:
        from ..routing.router import safe_routes_multi, route_time_safety
    except Exception as e:  # noqa: BLE001
        raise state.ModuleMissing(f"floodnet.routing.router.safe_routes_multi unavailable (Agent F): {e}")
    res, depth = _route_depths(req)
    try:
        out: Any = await run_in_threadpool(safe_routes_multi, roads_graph, depth, tuple(req.origin),
                                           tuple(req.dest), req.vehicle, None, None, req.n_candidates)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    out = dict(out or {})

    # Time-aware safety-through-T+X per candidate, only possible when a run (and therefore its full
    # per-segment series) is actually available -- with no run, depth is a dry-network assumption with no
    # time series behind it, so we honestly omit time_safety rather than fabricate one.
    t_min_series = streets_cm = None
    if req.series_t_min is not None and req.streets_cm is not None:        # supplied by the client (self-contained)
        t_min_series, streets_cm = list(req.series_t_min), dict(req.streets_cm)
    elif res is not None:                                                  # local server: run held in memory
        t_min_series = [f.t_s / 60.0 for f in res.frames]
        seg_ids_all = sorted({sid for f in res.frames for sid in f.street_depth_m.keys()})
        streets_cm = {sid: [round(float(f.street_depth_m.get(sid, 0.0)) * 100.0, 1) for f in res.frames]
                      for sid in seg_ids_all}
    if t_min_series is not None:
        limit = config.VEHICLE_LIMIT_CM.get(req.vehicle, 30)
        for cand in out.get("candidates", []):
            cand["time_safety"] = route_time_safety(cand.get("route_segments", []), t_min_series, streets_cm, limit)

    has_depths = res is not None or req.street_depth_m is not None
    out.update({"t_min": req.t_min, "run_id": req.run_id or (res.run_id if res is not None else None),
                "vehicle": req.vehicle,
                "depth_source": "simulation frame" if has_depths else "no run: dry network assumed",
                "data_mode": state.run_data_mode(res) if res is not None else (req.data_mode or state.run_data_mode(None))})
    out.setdefault("provenance", {"roads": roads_graph.provenance.to_dict(), "depths": _depth_provenance(req, res)})
    return out


# ----------------------------------------------------------------------------- arrival-time road access (MISC-04)
_access_ctx_cache: dict[int, Any] = {}


def _access_context(p: dict):
    """Which segments the model cannot see (no grid cell) and which cross DEM cells flagged unreliable; cached
    per pilot roads object like the other id(roads)-keyed caches."""
    from ..access.road_status import build_context, load_dem_mask
    roads_graph, grid = p["roads"], p["terrain"].grid
    hit = _access_ctx_cache.get(id(roads_graph))
    if hit is not None and hit[0] is roads_graph:        # the object is held, so its id cannot be reused
        return hit[1]
    fn = state.street_fn_for(roads_graph, grid)
    mask = load_dem_mask()
    if mask is not None and mask.shape != (grid.ny, grid.nx):
        mask = None
    ctx = build_context(getattr(fn, "cells", None) or {}, mask)
    _access_ctx_cache.clear()
    _access_ctx_cache[id(roads_graph)] = (roads_graph, ctx)
    return ctx


@app.post("/api/access/route")
async def access_route(req: AccessRouteRequest):
    """Arrival-aware route vs the current-status-only baseline, each road checked at the time the vehicle is
    expected to reach it, confirmed closures enforced. See floodnet/access/arrival_router.py and
    docs/ROAD_ACCESS.md. A recommended route is a candidate with lower modelled exposure, not a safe route."""
    p = _pilot(); roads_graph = p.get("roads")
    if roads_graph is None:
        raise state.ModuleMissing("roads not present in pilot bundle; routing impossible")
    from ..access.arrival_router import plan_access
    from ..access.events import ClosureBook, parse_events
    from ..access.road_status import Forecast
    from ..routing.router import _cached_graph, _snap

    res: Optional[SimulationResult] = None
    if req.series_t_min is not None and req.streets_cm is not None:      # self-contained run sent by the client
        t_series, streets = list(req.series_t_min), dict(req.streets_cm)
    elif req.run_id:                                                     # local server: run held in memory
        res = state.get_run(req.run_id)
        if res is None:
            raise _run_not_found(req.run_id)
        t_series = [f.t_s / 60.0 for f in res.frames]
        streets = {sid: [float(f.street_depth_m.get(sid, 0.0)) * 100.0 for f in res.frames]
                   for sid in {s for f in res.frames for s in f.street_depth_m}}
    else:
        raise HTTPException(422, "arrival-time road access needs a flood forecast: run a scenario first")
    if not t_series:
        raise HTTPException(422, "the forecast has no frames")

    decision_t = req.dispatch_t_min if req.decision_t_min is None else req.decision_t_min
    if decision_t > req.dispatch_t_min:
        raise HTTPException(422, "decision_t_min cannot be later than dispatch_t_min")
    try:
        events = parse_events(req.events)
    except ValueError as e:
        raise HTTPException(422, str(e))
    known = {s.seg_id for s in roads_graph.segments}
    unknown_ids = sorted({e.seg_id for e in events} - known)
    if unknown_ids:
        raise HTTPException(422, f"events refer to unknown road segments: {unknown_ids[:5]}")

    closures = ClosureBook(events, decision_t)
    data_mode = state.run_data_mode(res) if res is not None else (req.data_mode or state.run_data_mode(None))
    forecast = Forecast(tuple(t_series), streets, label=f"simulation run {req.run_id or '(client bundle)'} ({data_mode})")
    G = _cached_graph(roads_graph)
    o, d = _snap(roads_graph, [tuple(req.origin), tuple(req.dest)])
    try:
        out: Any = await run_in_threadpool(plan_access, roads_graph, G, o, d, vehicle=req.vehicle,
                                           dispatch_t_min=req.dispatch_t_min, forecast=forecast,
                                           seg_ctx=_access_context(p), closures=closures)
    except KeyError as e:
        raise HTTPException(422, str(e))

    dt = [b - a for a, b in zip(t_series[:-1], t_series[1:])]
    out.update({
        "vehicle": req.vehicle, "vehicle_limit_cm": config.VEHICLE_LIMIT_CM[req.vehicle],
        "dispatch_t_min": req.dispatch_t_min, "decision_t_min": decision_t,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "forecast": {"label": forecast.label, "run_id": req.run_id, "data_mode": data_mode,
                     "first_t_min": t_series[0], "horizon_t_min": t_series[-1],
                     "frame_dt_min": round(min(dt), 3) if dt else None,
                     "note": "simulated depths from FloodNet's physics model; not observed water levels"},
        "events_used": closures.annotated_used(),              # event dicts + derived status / stale / explanation (D-32)
        "events_ignored_future": [e.to_dict() for e in closures.ignored_future],
        "assumptions": {"tag": "ASSUMED", "speed_kmh_by_highway": config.ASSUMED_SPEED_KMH,
                        "default_speed_kmh": config.ASSUMED_DEFAULT_SPEED_KMH,
                        "pedestrian_speed_kmh": config.ASSUMED_PEDESTRIAN_SPEED_KMH,
                        "uncertainty_band_cm": config.ACCESS_UNCERTAINTY_BAND_CM,
                        "cost_factor_by_status": config.ACCESS_COST_FACTOR,
                        "note": "speeds are not measured; no slowdown in water; the vehicle never waits"},
        "disclaimer": ("Decision support only. A recommended route is a candidate with lower modelled flood "
                       "exposure at the expected arrival time, not a guarantee of safety."),
        "provenance": {"roads": roads_graph.provenance.to_dict()},
    })
    return out


# ----------------------------------------------------------------------------- response planner (MISC-04 Phase 2)
def _forecast_from(req) -> tuple:
    """The run's per-segment depth series, from the client bundle or this server's memory (same rule as
    /api/access/route)."""
    from ..access.road_status import Forecast
    res: Optional[SimulationResult] = None
    if req.series_t_min is not None and req.streets_cm is not None:
        t_series, streets = list(req.series_t_min), dict(req.streets_cm)
    elif req.run_id:
        res = state.get_run(req.run_id)
        if res is None:
            raise _run_not_found(req.run_id)
        t_series = [f.t_s / 60.0 for f in res.frames]
        streets = {sid: [float(f.street_depth_m.get(sid, 0.0)) * 100.0 for f in res.frames]
                   for sid in {s for f in res.frames for s in f.street_depth_m}}
    else:
        raise HTTPException(422, "a flood forecast is needed: run a scenario first")
    if not t_series:
        raise HTTPException(422, "the forecast has no frames")
    data_mode = state.run_data_mode(res) if res is not None else (req.data_mode or state.run_data_mode(None))
    return res, Forecast(tuple(t_series), streets, label=f"simulation run {req.run_id or '(client bundle)'} ({data_mode})"), data_mode




@app.get("/api/response/resources")
def response_resources():
    """The demo settlements, shelters, teams and vehicles: REAL locations, SIMULATED numbers
    (floodnet/response/fixtures.py)."""
    from ..response.fixtures import demo_resources
    return demo_resources()


@app.post("/api/response/plan")
async def response_plan(req: ResponsePlanRequest, request: Request):
    """Build or rebuild the response plan at sim time now_t_min from the client's ledger and event logs. See
    floodnet/response/planner.py for the allocation and replanning rules. Decision support only."""
    p = _pilot(); roads_graph = p.get("roads")
    if roads_graph is None:
        raise state.ModuleMissing("roads not present in pilot bundle; planning impossible")
    from ..access.events import ClosureBook, parse_events
    from ..response.fixtures import demo_resources
    from ..response.ledger import parse_ledger, parse_response_events
    from ..response.planner import Router, plan
    from ..routing.router import _cached_graph

    _, forecast, data_mode = _forecast_from(req)
    if req.resources_source == "registry":            # D-43: verified registry state instead of the demo fixtures
        from ..ops import auth as _auth
        _auth.require_dispatcher(request)            # QA 2026-10-11 M4: registry records are dispatcher data
        from ..ops import registry as _rg
        from ..ops.store import OpsUnavailable, get_store
        try:
            _st = get_store()
        except OpsUnavailable as e:
            raise HTTPException(503, str(e))
        _rg.seed_from_fixtures(_st)
        resources = _rg.planner_resources(_st)
    else:
        resources = demo_resources()
    known = {k: {r["id"] for r in resources[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    known.update({"vehicle_id": known["vehicles"], "shelter_id": known["shelters"], "settlement_id": known["settlements"]})
    try:
        road_events = parse_events(req.road_events)
        bad = sorted({e.seg_id for e in road_events} - {s.seg_id for s in roads_graph.segments})
        if bad:
            raise ValueError(f"road events refer to unknown road segments: {bad[:5]}")
        response_events = parse_response_events(req.response_events, known)
        ledger = parse_ledger(req.ledger, known, plan_version=req.plan_version)    # rejects a ledger newer than claimed (D-33)
    except ValueError as e:
        raise HTTPException(422, str(e))
    closures = ClosureBook(road_events, req.now_t_min)
    rt = Router(roads_graph, _cached_graph(roads_graph), forecast, _access_context(p), closures, req.mode)
    try:
        out: Any = await run_in_threadpool(plan, resources, response_events, ledger, rt, req.now_t_min, req.plan_version)
    except KeyError as e:
        raise HTTPException(422, str(e))
    out.update({
        "mode": req.mode, "based_on_plan_version": req.plan_version,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "forecast": {"label": forecast.label, "run_id": req.run_id, "data_mode": data_mode, "horizon_t_min": forecast.horizon_t_min,
                     "note": "simulated depths from FloodNet's physics model; not observed water levels"},
        "road_events_used": closures.annotated_used(),
        "road_events_ignored_future": [e.to_dict() for e in closures.ignored_future],
        "assumptions": {"tag": "ASSUMED / SIMULATED", "resources": resources["tag"], "priority_rule": resources["priority_rule"],
                        "minutes": resources["assumed_minutes"], "speed_kmh_by_highway": config.ASSUMED_SPEED_KMH,
                        "allocation": "transparent greedy by declared priority, earliest completion first; not an optimiser"},
        "disclaimer": ("Decision support only. Routes are candidates with lower modelled flood exposure at the expected arrival "
                       "time, not guaranteed safe; every headcount and capacity here is simulated demo data."),
    })
    return out


@app.post("/api/response/evaluate")
async def response_evaluate(req: ResponseEvaluateRequest):
    """Deterministic scripted replay with arrival-aware and current-status routing, judged against an independent
    outcome run; plus road-access prediction quality on every segment. SIMULATED evidence (floodnet/response/evaluate.py)."""
    from .schemas import ResponsePlanRequest
    from ..access.road_status import Forecast
    from ..response.evaluate import DEMO_SCRIPT, evaluate, evaluate_many, generate_script
    from ..response.fixtures import demo_resources
    p = _pilot(); roads_graph = p.get("roads")
    if roads_graph is None:
        raise state.ModuleMissing("roads not present in pilot bundle; evaluation impossible")
    _, forecast, _ = _forecast_from(ResponsePlanRequest(run_id=req.run_id, series_t_min=req.series_t_min, streets_cm=req.streets_cm,
                                                        data_mode=req.data_mode))
    if req.outcome_scenario_id:
        if config.ON_VERCEL:
            raise HTTPException(422, "on the serverless deployment send the outcome run's series instead of a scenario id")
        try:
            res = await run_in_threadpool(state.run_scenario, req.outcome_scenario_id, {"mode": "none"}, req.outcome_horizon_min)
        except KeyError as e:
            raise HTTPException(404, str(e))
        streets = {sid: [float(f.street_depth_m.get(sid, 0.0)) * 100.0 for f in res.frames] for sid in {s for f in res.frames for s in f.street_depth_m}}
        outcome = Forecast(tuple(f.t_s / 60.0 for f in res.frames), streets, label=f"outcome run {res.run_id} ({req.outcome_scenario_id})")
    else:
        _, outcome, _ = _forecast_from(ResponsePlanRequest(run_id=req.outcome_run_id, series_t_min=req.outcome_series_t_min,
                                                           streets_cm=req.outcome_streets_cm, data_mode=req.data_mode))
        outcome = Forecast(outcome.t_min, outcome.streets_cm, label=f"outcome {outcome.label}")
    horizon = int(min(forecast.horizon_t_min, outcome.horizon_t_min))
    if req.seeds is not None:                                   # D-35: several generated scripts, aggregated
        out: Any = await run_in_threadpool(evaluate_many, roads_graph, _access_context(p), forecast, outcome,
                                           list(req.seeds), horizon_t_min=horizon)
    else:
        script = DEMO_SCRIPT if req.seed is None else generate_script(req.seed, roads_graph, demo_resources(), horizon_t_min=horizon)
        out = await run_in_threadpool(evaluate, roads_graph, _access_context(p), forecast, outcome, script)
    if not req.include_ledgers:
        out.pop("ledgers", None)
        out.pop("prediction_cases", None)       # per-case detail (segments x decisions x leads); export_rows() needs evaluate() directly
    out["processed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out["disclaimer"] = ("SIMULATED replay: the outcome is another FloodNet run, not observed conditions. The numbers show how "
                         "the two methods differ under an imperfect forecast, not real-world accuracy.")
    return out


# ----------------------------------------------------------------------------- WhatsApp test alerts (Twilio)
@app.get("/api/alerts/whatsapp/status")
def whatsapp_status():
    """Is WhatsApp alerting configured? Names of missing settings only; recipients masked; no credential returned."""
    from ..alerts.whatsapp import status
    return status()


@app.post("/api/alerts/whatsapp/send")
async def whatsapp_send(req: WhatsAppSendRequest, _who: str = Depends(require_dispatcher)):
    """Send a FloodNet TEST alert to the configured WhatsApp numbers (never to a number supplied by the caller).
    De-duplicated and rate-limited; see floodnet/alerts/whatsapp.py."""
    from ..alerts.whatsapp import sender
    out = await run_in_threadpool(sender.send, req.lines, req.kind)
    if out.get("skipped") == "not configured":
        raise HTTPException(503, {"message": "WhatsApp alerts are not configured", "missing": out["missing"]})
    out["processed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


@app.get("/api/alerts/phone/status")
def phone_status():
    """Every phone channel (WhatsApp via Twilio, Telegram) with what is missing; no secret is returned."""
    from ..alerts import telegram, whatsapp
    w, t = whatsapp.status(), telegram.status()
    return {"configured": w["configured"] or t["configured"], "banner": whatsapp.BANNER, "whatsapp": w, "telegram": t}


@app.post("/api/alerts/phone/send")
async def phone_send(req: WhatsAppSendRequest, _who: str = Depends(require_dispatcher)):
    """One click, every configured phone channel: the same TEST alert to WhatsApp and/or Telegram recipients from
    the backend .env. 503 only when no channel is configured."""
    from ..alerts import telegram, whatsapp
    out: dict = {"kind": req.kind, "channels": {}}
    for name, mod in (("whatsapp", whatsapp), ("telegram", telegram)):
        if mod.status()["configured"]:
            out["channels"][name] = await run_in_threadpool(mod.sender.send, req.lines, req.kind)
    if not out["channels"]:
        raise HTTPException(503, {"message": "no phone channel is configured",
                                  "missing": {"whatsapp": whatsapp.status()["missing"], "telegram": telegram.status()["missing"]}})
    out["sent"] = sum(c.get("sent", 0) for c in out["channels"].values())
    out["processed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


@app.get("/api/alerts/whatsapp/message/{sid}")
async def whatsapp_message(sid: str):
    """Delivery outcome of one sent message (queued / sent / delivered / read / failed, with Twilio's error code)."""
    from ..alerts.whatsapp import sender
    try:
        return await run_in_threadpool(sender.message_status, sid)
    except ValueError as e:
        raise HTTPException(422, str(e))


# ----------------------------------------------------------------------------- live operations (D-38..D-43)
from .ops_api import router as _ops_router          # noqa: E402  (after the app exists; routes are independent)
app.include_router(_ops_router)
from .messaging_api import router as _messaging_router   # noqa: E402  (D-44..D-46)
app.include_router(_messaging_router)
from .traffic_api import router as _traffic_router       # noqa: E402  (live traffic, TomTom)
app.include_router(_traffic_router)


# ----------------------------------------------------------------------------- frontend on Vercel
# Registered last and at the lowest priority: every API route above wins over a frontend file. Vercel promotes this
# directory to its CDN at build time (pyproject.toml [tool.vercel.fastapi.static]); navigation requests for
# client-side routes such as /dashboard get index.html. The build is made with VITE_BASE=/ (vercel.json).
if config.ON_VERCEL:
    app.frontend("/", directory=str(config.FRONTEND_REACT_DIST), fallback="index.html", check_dir=False)
