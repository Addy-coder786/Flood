"""TomTom Traffic Flow, "Flow Segment Data" v4: current vs free-flow speed for the road fragment nearest a point.

Verified against the official doc on 2026-10-11 (WebFetch of
https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data, page says
"Service version: 4", "Last edit: 2025.04.17"):
  GET https://api.tomtom.com/traffic/services/4/flowSegmentData/{style}/{zoom}/{format}
      ?key=...&point={lat},{lon}&unit=kmph|mph&thickness=1..20&openLr=true|false
  style: absolute | relative | relative0 | relative0-dark | relative-delay | reduced-sensitivity;  zoom: 0..22;
  format: xml | json | jsonp.  `point` is "latitude,longitude" (EPSG:4326).  `unit` default kmph.
  Response `flowSegmentData`: currentSpeed (int, requested unit), freeFlowSpeed (int), currentTravelTime (s),
  freeFlowTravelTime (s), confidence (float 0..1, 1 = full confidence), roadClosure (bool), frc (FRC0..FRC6),
  coordinates, optional openlr.  Codes: 200, 400, 403 (key not valid), 405, 429 (rate limit), 500, 503, 596.
  [UNVERIFIED] whether `unit=KMPH` upper-case is accepted (doc shows lower-case `kmph`; we send lower-case).
  [UNVERIFIED] TomTom REST API terms (caching, storage, safety-critical use): the terms page could not be fetched
  (docs/RESEARCH_NAVIGATION_TRAFFIC.md). Results are cached for TRAFFIC_CACHE_TTL_S only and never persisted.

Honesty rules: the key comes from the TOMTOM_API_KEY env var only and is never logged, returned or placed in the
URL (it travels as a query parameter handed to the HTTP client). A segment whose call fails, times out or returns
an invalid body is SKIPPED and counted in `failures`; nothing here ever invents a speed. Missing or zero speeds give
level "unknown". Level thresholds (ratio = currentSpeed / freeFlowSpeed) are ASSUMED, not a TomTom definition:
  normal >= 0.8;  moderate 0.5..0.8;  heavy < 0.5.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

PROVIDER = "tomtom"
TRAFFIC_CACHE_TTL_S = 60
MAX_POINTS = 40
TIMEOUT_S = 4.0
MAX_WORKERS = 8
ENV_KEY = "TOMTOM_API_KEY"
# style "absolute" / zoom 10 / json: one call per point; zoom only shifts the returned segment shape, not the speeds.
ENDPOINT = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

NOTE_NO_KEY = "Live traffic unavailable — TOMTOM_API_KEY not configured; using estimated travel time"
NOTE_ALL_FAILED = ("Live traffic unavailable — every TomTom request failed or timed out; "
                   "using estimated travel time")
NOTE_LIVE = ("Live traffic from TomTom Flow Segment Data (not a flood forecast). Levels use ASSUMED thresholds on "
             "current/free-flow speed: normal >= 0.8, moderate >= 0.5, heavy < 0.5.")

# ASSUMED thresholds on ratio = current_kmh / free_flow_kmh (see module docstring)
LEVEL_NORMAL_MIN = 0.8
LEVEL_MODERATE_MIN = 0.5

# seg_id -> {"sampled_at": datetime, "segment": dict}; module-level, per process
_cache: dict[str, dict[str, Any]] = {}


def clear_cache() -> None:
    _cache.clear()


def _key() -> Optional[str]:
    k = os.environ.get(ENV_KEY, "").strip()
    return k or None


def _httpx_get(url: str, params: dict, timeout: float) -> tuple[int, str]:
    """Default transport. Returns (status_code, body_text). Tests pass a fake instead; never called from tests."""
    import httpx
    r = httpx.get(url, params=params, timeout=timeout)
    return r.status_code, r.text


def level_for(ratio: Optional[float]) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= LEVEL_NORMAL_MIN:
        return "normal"
    if ratio >= LEVEL_MODERATE_MIN:
        return "moderate"
    return "heavy"


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_body(text: str) -> Optional[dict]:
    """The `flowSegmentData` object, or None when the body is not valid Flow Segment Data JSON."""
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict) or not isinstance(body.get("flowSegmentData"), dict):
        return None
    return body["flowSegmentData"]


def _segment(d: dict, sampled_at: datetime) -> dict:
    cur, free = _num(d.get("currentSpeed")), _num(d.get("freeFlowSpeed"))
    ratio = round(cur / free, 3) if cur is not None and free is not None and free > 0 and cur > 0 else None
    return {"current_kmh": cur, "free_flow_kmh": free, "ratio": ratio, "level": level_for(ratio),
            "confidence": _num(d.get("confidence")),
            "road_closure": bool(d["roadClosure"]) if isinstance(d.get("roadClosure"), bool) else None,
            "sampled_at": sampled_at.isoformat(), "source": PROVIDER, "from_cache": False}


def _fetch_one(http_get: Callable, key: str, seg_id: str, lon: float, lat: float, now: datetime) -> Optional[dict]:
    """One Flow Segment Data call. None on any failure (logged without the URL or the key)."""
    params = {"point": f"{lat:g},{lon:g}", "unit": "kmph", "key": key}
    try:
        status, text = http_get(ENDPOINT, params, TIMEOUT_S)
    except Exception as e:                       # timeouts, connection errors, transport bugs: skip, never guess
        log.info("tomtom: %s failed: %s", seg_id, type(e).__name__)
        return None
    if status != 200:
        log.info("tomtom: %s HTTP %s", seg_id, status)
        return None
    d = parse_body(text)
    if d is None:
        log.info("tomtom: %s invalid body", seg_id)
        return None
    return _segment(d, now)


def sample(points: list[tuple[str, float, float]], *, http_get: Optional[Callable] = None,
           now: Optional[datetime] = None, refresh: bool = False) -> dict:
    """Live speeds for (seg_id, lon, lat) points: at most MAX_POINTS, one TomTom call each (thread pool), 60 s
    per-segment cache. See the module docstring for the contract and the honesty rules."""
    if len(points) > MAX_POINTS:
        raise ValueError(f"at most {MAX_POINTS} points per call, got {len(points)}")
    now = now or datetime.now(timezone.utc)
    http_get = http_get or _httpx_get
    base = {"provider": PROVIDER, "fetched_at": now.isoformat(), "ttl_s": TRAFFIC_CACHE_TTL_S}
    key = _key()
    if key is None:
        return {**base, "mode": "UNAVAILABLE", "segments": {}, "failures": 0, "note": NOTE_NO_KEY}

    segments: dict[str, dict] = {}
    todo = []
    for seg_id, lon, lat in points:
        hit = None if refresh else _cache.get(seg_id)
        if hit is not None and (now - hit["sampled_at"]).total_seconds() < TRAFFIC_CACHE_TTL_S:
            segments[seg_id] = {**hit["segment"], "from_cache": True}
        else:
            todo.append((seg_id, float(lon), float(lat)))

    failures = 0
    if todo:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as pool:
            results = list(pool.map(lambda p: _fetch_one(http_get, key, p[0], p[1], p[2], now), todo))
        for (seg_id, _, _), seg in zip(todo, results):
            if seg is None:
                failures += 1
                continue
            _cache[seg_id] = {"sampled_at": now, "segment": seg}
            segments[seg_id] = seg

    if not segments and failures:
        return {**base, "mode": "UNAVAILABLE", "segments": {}, "failures": failures, "note": NOTE_ALL_FAILED}
    note = NOTE_LIVE if not failures else (f"{failures} of {len(points)} segments unavailable (request failed, "
                                           f"timed out or invalid); shown values are live. " + NOTE_LIVE)
    return {**base, "mode": "LIVE", "segments": segments, "failures": failures, "note": note}


def status(now: Optional[datetime] = None) -> dict:
    """Configuration and cache state; no network."""
    now = now or datetime.now(timezone.utc)
    configured = _key() is not None
    fresh = sum(1 for h in _cache.values() if (now - h["sampled_at"]).total_seconds() < TRAFFIC_CACHE_TTL_S)
    return {"configured": configured, "provider": PROVIDER, "mode": "LIVE" if configured else "UNAVAILABLE",
            "cache_ttl_s": TRAFFIC_CACHE_TTL_S, "cached_segments": fresh,
            "note": NOTE_LIVE if configured else NOTE_NO_KEY}
