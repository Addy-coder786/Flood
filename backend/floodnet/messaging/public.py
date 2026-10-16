"""The public alert page snapshot (DECISIONS D-45, docs/LIVE_OPERATIONS.md §7).

A WHITELIST: every snapshot is a fresh dict built field by field; nothing internal (subscribers, deliveries, approver,
operator event ids, notes, road ids, trips, vehicles, audit metadata) is ever copied across. Only published versions
are visible; an unknown id, a cancelled alert and an unpublished version all give None (the API turns that into one
identical 404).

Wording: FloodNet's own status words, never an authority's level names or "alert"/"warning"/colours:
  watch    "Flooding possible – plan ahead"
  advisory "Flooding likely – avoid the area"
  ended    "No longer active"
Contacts and precautions are the verified ones from docs/LIVE_OPERATIONS_RESEARCH.md §8 only.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..ops.store import OpsStore, iso, utc_now
from . import alerts as al

STALE_AFTER_MIN = 60        # ASSUMED: no update for more than 60 min -> "stale"
EXPIRES_AFTER_H = 6         # ASSUMED: a version expires 6 h after its publication unless updated
CONTEXT_CLASSES = ("trunk", "primary", "secondary", "tertiary")
CONTEXT_PAD_M = 300.0
MAX_CONTEXT_ROADS = 250       # keeps the public JSON small on a phone connection

STATUS_TEXT = dict(al.STATUS_WORDS)
PRECAUTIONS = {
    "items": ["Don't walk through flowing water", "only half a metre of flood water can carry a car away",
              "Avoid contact with flood water", "Stay away from power lines"],
    "source": "Source: BMC Disaster Management",
    "url": "https://dm.mcgm.gov.in/",
}
CONTACTS = [
    {"number": "112", "name": "Emergency Response Support System (MHA ERSS)",
     "source": "Ministry of Home Affairs", "source_url": "https://www.mha.gov.in/en/commoncontent/emergency-response-support-system-erss"},
    {"number": "1916", "name": "BMC Emergency Operation Centre",
     "source": "BMC Disaster Management (BMC CONNECT directory)", "source_url": "https://dm.mcgm.gov.in/"},
]
OFFICIAL_LINKS = [
    {"name": "SACHET - official alerts (NDMA)", "url": "https://sachet.ndma.gov.in/"},
    {"name": "BMC Disaster Management", "url": "https://dm.mcgm.gov.in/"},
]
DISCLAIMER = ("FloodNet is a student prototype for the Hindmata / Dadar area of Mumbai and not an official warning "
              "service. Road water depths are computer-model estimates and can be wrong. For official alerts follow "
              "SACHET (NDMA) and BMC Disaster Management. In an emergency call 112.")
UNCERTAINTY = ("Water depths on roads are a computer-model estimate, not measurements, and may be wrong in either "
               "direction; conditions can change quickly. Road closures are shown only where an operator confirmed them.")
ATTRIBUTION = "Map data (c) OpenStreetMap contributors (ODbL)"

_map_cache: dict[tuple, dict] = {}

_DEMO_WORDS = ("SYNTHETIC", "SCENARIO", "DEMO", "SIMULATED", "HISTORICAL", "REPLAY", "TEST")


def locality_phrase(names: list[str], show: int = 3) -> str:
    """'Near A, B and C (+10 more areas).' -- readable on a phone however many areas are affected."""
    if not names:
        return ""
    head = names[:show]
    text = head[0] if len(head) == 1 else ", ".join(head[:-1]) + " and " + head[-1]
    more = len(names) - len(head)
    return f"Near {text}" + (f" (+{more} more areas)." if more else ".")


def demonstration_note(data_mode: Optional[str]) -> Optional[str]:
    """A plain warning when the advisory was computed from a demonstration storm, a replay or test data."""
    mode = str(data_mode or "").upper()
    if any(w in mode for w in _DEMO_WORDS):
        return ("Demonstration: this advisory was produced from a synthetic or historical storm used to show how "
                "FloodNet works, not from current rainfall. Do not act on it as a real alert.")
    return None


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _r(v: float) -> float:
    return round(float(v), 5)


def _roads(roads=None):
    if roads is not None:
        return roads
    from ..api import state
    return state.get_pilot()["roads"]


# ----------------------------------------------------------------------------- map
def _build_map(zone: dict, roads) -> dict:
    from shapely.geometry import LineString, box
    from shapely.ops import unary_union
    from ..api import state
    by_id = {s.seg_id: s for s in roads.segments}
    affected, lines = [], []
    for s in zone["segments"]:
        seg = by_id.get(s["seg_id"])
        if seg is None:
            continue
        lines.append(LineString(seg.xy))
        # depth_cm: the modelled maximum in the time window (whole cm, a MODEL ESTIMATE), shown when a reader taps
        # the road; None when the model gave none (e.g. a confirmed closure or a public report only).
        depth = s.get("max_depth_cm")
        affected.append({"type": "LineString", "name": s["name"], "class": s["basis"],
                         "depth_cm": int(round(depth)) if isinstance(depth, (int, float)) else None,
                         "coordinates": [[_r(x), _r(y)] for x, y in seg.lonlat]})
    if not lines:
        return {"affected_roads": [], "area_outline": None, "bbox": None, "context_roads": []}
    hull = unary_union(lines).buffer(50.0).convex_hull.simplify(5.0)
    hx, hy = hull.exterior.coords.xy
    lon, lat = state.xy_to_lonlat(list(hx), list(hy))
    outline = {"type": "Polygon", "coordinates": [[[_r(a), _r(b)] for a, b in zip(lon, lat)]]}
    minx, miny, maxx, maxy = hull.bounds
    frame = box(minx - CONTEXT_PAD_M, miny - CONTEXT_PAD_M, maxx + CONTEXT_PAD_M, maxy + CONTEXT_PAD_M)
    (w, e), (s_, n) = state.xy_to_lonlat([frame.bounds[0], frame.bounds[2]], [frame.bounds[1], frame.bounds[3]])
    context = []
    affected_ids = {x["seg_id"] for x in zone["segments"]}
    candidates = [seg for seg in roads.segments if seg.highway in CONTEXT_CLASSES and seg.seg_id not in affected_ids]
    candidates.sort(key=lambda seg: CONTEXT_CLASSES.index(seg.highway))     # trunk and primary first when capped
    for seg in candidates:
        if not LineString(seg.xy).intersects(frame):
            continue
        simple = LineString(seg.lonlat).simplify(0.0001)          # ~10 m; context only, 4 decimals (~11 m)
        context.append({"type": "LineString", "name": seg.name or "", "class": seg.highway,
                        "coordinates": [[round(x, 4), round(y, 4)] for x, y in simple.coords]})
        if len(context) >= MAX_CONTEXT_ROADS:
            break
    return {"affected_roads": affected, "area_outline": outline, "bbox": [_r(w), _r(s_), _r(e), _r(n)],
            "context_roads": context}


def _map_for(public_id: str, version: dict, roads) -> dict:
    key = (public_id, version["version"], version.get("signature"), id(roads))
    m = _map_cache.get(key)
    if m is None:
        if len(_map_cache) > 64:
            _map_cache.clear()
        m = _build_map(version["zone"], roads)
        _map_cache[key] = m
    return m


# ----------------------------------------------------------------------------- snapshot
def _evidence(v: dict) -> list[dict]:
    zone, out = v["zone"], []
    model = [s for s in zone["segments"] if s["basis"] == "MODEL_ESTIMATE"]
    if model:
        depth = max(s["max_depth_cm"] or 0 for s in model)
        mode = (v.get("forecast") or {}).get("data_mode") or "unknown"
        out.append({"kind": "model_estimate", "label": "Model estimate",
                    "time": (v.get("forecast") or {}).get("published_at") or v["created_at"],
                    "source": f"FloodNet flood model (student prototype; input data mode: {mode})",
                    "detail": f"Modelled water up to {depth:.0f} cm on {len(model)} road(s) in the time window"})
    closed = [s for s in zone["segments"] if s["basis"] == "CONFIRMED_CLOSURE"]
    if closed:
        out.append({"kind": "confirmed_closure", "label": "Confirmed road closure", "time": v["created_at"],
                    "source": "Road closures confirmed by operators, as recorded in FloodNet",
                    "detail": f"{len(closed)} road(s) confirmed closed"})
    reported = [s for s in zone["segments"] if s["basis"] == "REPORTED"]
    if reported:
        out.append({"kind": "reported", "label": "Reported (unconfirmed)", "time": v["created_at"],
                    "source": "Public reports recorded in FloodNet, not confirmed by an operator",
                    "detail": f"{len(reported)} road(s) reported closed"})
    return out


def _state(alert: dict, updated_at: datetime, expires_at: datetime, now: datetime) -> str:
    if alert.get("state") == "RESOLVED":
        return "resolved"
    if now > expires_at:
        return "expired"
    if now - updated_at > timedelta(minutes=STALE_AFTER_MIN):
        return "stale"
    return "active"


def build_snapshot(alert: dict, version: Optional[int] = None, *, now: Optional[datetime] = None,
                   roads=None) -> Optional[dict]:
    """The public view of one published version (latest when `version` is None), or None."""
    if alert.get("state") == "CANCELLED":
        return None
    published = [v for v in alert.get("versions", []) if v.get("published_at")]
    if not published:
        return None
    latest = published[-1]
    if version is None:
        v = latest
    else:
        hits = [x for x in published if x["version"] == int(version)]
        if not hits:
            return None
        v = hits[0]
    now = now or utc_now()
    updated_at = _parse(latest["published_at"])
    expires_at = updated_at + timedelta(hours=EXPIRES_AFTER_H)
    status = v["level"]
    zone = v["zone"]
    places = al._places(zone, 2)
    locs = [al.subs.LOCALITY_BY_ID[l]["name"] for l in zone["localities"] if l in al.subs.LOCALITY_BY_ID]
    created = _parse(v["created_at"])
    window_min = float(alert.get("window_min") or 60)
    if status == "ended":
        window = {"from": None, "to": None, "text": "No longer active"}
    else:
        window = {"from": iso(created), "to": iso(created + timedelta(minutes=window_min)),
                  "text": f"Next {window_min:g} min from {created.astimezone(al.IST):%d %b %H:%M} IST"}
    return {
        "public_id": str(alert["public_id"]),
        "version": int(v["version"]),
        "latest_version": int(latest["version"]),
        "is_latest": v["version"] == latest["version"],
        "issuer": "FloodNet (student prototype)",
        "headline": f"{STATUS_TEXT[status]}: {places}",
        "locality_text": (locality_phrase(locs) + " " if locs else "") + "Roads: " + al._places(zone, 6, False),
        "demonstration": demonstration_note((v.get("forecast") or {}).get("data_mode")),
        "status": status,
        "status_text": STATUS_TEXT[status],
        "state": _state(alert, updated_at, expires_at, now),
        "evidence": _evidence(v),
        "time_window": window,
        "uncertainty": UNCERTAINTY,
        "precautions": {"items": list(PRECAUTIONS["items"]), "source": PRECAUTIONS["source"], "url": PRECAUTIONS["url"]},
        "contacts": [dict(c) for c in CONTACTS],
        "official_links": [dict(l) for l in OFFICIAL_LINKS],
        "map": _map_for(alert["public_id"], v, _roads(roads)),
        "updates": [{"version": int(x["version"]), "time": x["published_at"], "what_changed": x["change_summary"],
                     "status_text": STATUS_TEXT[x["level"]]} for x in published],
        "updated_at": iso(updated_at),
        "expires_at": iso(expires_at),
        "disclaimer": DISCLAIMER,
        "attribution": ATTRIBUTION,
    }


def snapshot_for_public_id(store: OpsStore, public_id: str, v: Optional[int] = None, *,
                           now: Optional[datetime] = None, roads=None) -> Optional[dict]:
    if not public_id or len(public_id) > 64:
        return None
    alert = al.find_by_public_id(store, public_id)
    if alert is None:
        return None
    return build_snapshot(alert, v, now=now, roads=roads)
