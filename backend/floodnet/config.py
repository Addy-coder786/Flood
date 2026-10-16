"""Single source of truth for pilot geometry, CRS, time and paths. Do not duplicate these elsewhere."""
from __future__ import annotations
import os
from pathlib import Path

# --- Paths --------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal, dependency-free `.env` loader (KEY=VALUE per line, '#' comments, blank lines skipped).
    Never overrides a variable already set in the real environment. Used for IMD_API_KEY etc. -- see
    `.env.example`. No new package added; pyproject.toml's dependency list is deliberately fixed."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = val.strip().strip('"').strip("'")


_load_dotenv(REPO_DIR / ".env")
DATA_RAW = REPO_DIR / "data" / "raw"
DATA_PROCESSED = REPO_DIR / "data" / "processed" / "pilot"
MCGM_RAW = DATA_RAW / "mcgm_gis"
FRONTEND_LEGACY_DIR = REPO_DIR / "frontend"              # original vanilla-JS dashboard (kept as a fallback)
FRONTEND_REACT_DIST = REPO_DIR / "frontend-react" / "dist"  # `cd frontend-react && npm run build` output
# Serve the built React app once it exists; fall back to the legacy static dashboard so the API server
# never breaks in an environment where `npm run build` hasn't been run yet (e.g. a fresh clone).
FRONTEND_DIR = FRONTEND_REACT_DIST if FRONTEND_REACT_DIST.is_dir() else FRONTEND_LEGACY_DIR

# --- Runtime environment -------------------------------------------------------------------------------------
# Vercel sets VERCEL=1 at build and run time. There the frontend is served from the CDN at "/" (api/main.py),
# the deployment filesystem is read-only, and only /tmp is writable (per instance, not shared, not persistent).
ON_VERCEL = os.environ.get("VERCEL") == "1"
# Recreatable caches (last good rainfall field, decoded radar frames). A per-instance warm cache on Vercel;
# FLOODNET_STATE_DIR overrides. Nothing here is required: a cold instance simply starts without them.
STATE_DIR = Path(os.environ.get("FLOODNET_STATE_DIR") or ("/tmp/floodnet" if ON_VERCEL else REPO_DIR / "data" / "interim"))

# --- CRS ----------------------------------------------------------------------
# ALL computation in EPSG:32643 (WGS84 / UTM 43N, metres). Lon/lat (EPSG:4326) only at API/UI boundary.
CRS_COMPUTE = "EPSG:32643"
CRS_GEO = "EPSG:4326"

# --- Pilot area: Hindmata / Dadar-Parel-Matunga (D-02) -------------------------
# bbox in lon/lat; inferred by research worker 2, contains 1,607 MCGM nodes / 1,973 20-cm contours (lead-verified).
PILOT_NAME = "Hindmata / Dadar pilot"
PILOT_BBOX_LONLAT = (72.835, 19.010, 72.855, 19.030)   # (west, south, east, north)
PILOT_MARGIN_M = 150.0                                 # extra margin for roads/network continuity

# --- Grid ---------------------------------------------------------------------
GRID_RES_M = 10.0          # default cell size (5.0 optional)
# Grid convention: z[j, i]; x = x0 + (i + 0.5) * res ; y = y0 + (j + 0.5) * res ; row 0 = SOUTH edge.

# --- Time ---------------------------------------------------------------------
HORIZON_S = 3 * 3600       # 0-3 h forward window
FRAME_DT_S = 300           # output frame every 5 min -> 37 frames incl. t=0
RAIN_DT_S = 300            # rainfall series resolution
MAX_SURFACE_DT_S = 5.0     # upper bound on internal explicit timestep (solver may go lower for stability)

# --- Units (documented, enforced by contracts) ---------------------------------
# rainfall mm/h | runoff m3/s | volumes m3 | depth m internally, cm at API | flow & capacity m3/s | time s internally, min at API

# --- Provisional severity bands (cm). WORKING THRESHOLDS, NOT CITED GUIDANCE (see DECISIONS.md) -----
SEVERITY_BANDS_CM = [(5, "clear"), (15, "minor"), (30, "moderate"), (60, "severe")]  # >=60 -> "critical"
VEHICLE_LIMIT_CM = {"pedestrian": 60, "motorcycle": 30, "car": 30, "ambulance": 40, "truck": 60}

# --- Arrival-time road access (MISC-04 Phase 1, floodnet/access/). ASSUMED values, NOT measured or cited ---------
# (DECISIONS.md D-24/D-25). Urban speeds during heavy rain by OSM highway class; no slowdown in water is modelled.
ASSUMED_SPEED_KMH = {"trunk": 30.0, "primary": 25.0, "secondary": 20.0, "tertiary": 20.0, "unclassified": 15.0,
                     "residential": 15.0, "living_street": 10.0, "service": 10.0}
ASSUMED_DEFAULT_SPEED_KMH = 15.0
ASSUMED_PEDESTRIAN_SPEED_KMH = 5.0
# A forecast depth within this many cm BELOW the vehicle limit is UNCERTAIN: the contour-derived DTM has ~0.2 m
# vertical precision (README), so the model cannot tell such a road from one over the limit.
ACCESS_UNCERTAINTY_BAND_CM = 20.0
# Route-cost multipliers on travel time (ASSUMED weights, not calibrated): prefer USABLE roads over these.
ACCESS_COST_FACTOR = {"USABLE": 1.0, "UNCERTAIN": 2.0, "UNKNOWN": 1.5}
