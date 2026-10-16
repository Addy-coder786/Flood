"""ETA breakdown for a route (DECISIONS D-49; docs/LIVE_OPERATIONS.md §10).

Three parts, each labelled with what it is:
  ASSUMED ROAD SPEEDS    the router's travel time: speeds assumed by road type (config.ASSUMED_SPEED_KMH). No traffic.
  MODELLED FLOOD DELAY   extra minutes from driving through forecast water: the speed on a road is capped by the
                         Pregnolato et al. (2017) depth-disruption curve at the forecast depth for the minutes the
                         vehicle is (now, later) on it. The curve was fitted for cars; applying it to trucks and
                         ambulances is an ASSUMPTION, and so is the MIN_WATER_SPEED_KMH floor. Not modelled for people
                         on foot.
  LIVE TRAFFIC           when a live sample exists for a road (floodnet/traffic/tomtom.py, TomTom Flow Segment Data):
                         the assumed travel time on that road is scaled by free_flow / current speed. Only sampled
                         roads get a traffic delay; traffic never shortens the ETA (live free-flow speeds exceed the
                         ASSUMED speeds). Without a sample the traffic part is UNKNOWN, never guessed.
  flood vs traffic       on one road the slower cause governs: the road's time is free flow + max(flood delay,
                         traffic delay), so the two are reported separately and never added on top of each other.

Because a slower vehicle reaches later roads later, every road is re-checked at the DELAYED minutes; roads whose
status changes are listed. The route itself is still chosen by the arrival-time router (floodnet/access/
arrival_router.py) on free-flow times; this module never changes a route. An ETA is a modelled estimate, never a
guarantee.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import networkx as nx

from .. import config
from ..access.arrival_router import speed_kmh
from ..access.events import ClosureBook
from ..access.road_status import Forecast, SegmentContext, classify
from ..contracts import RoadGraph
from .navigation import parse_time

PREGNOLATO_SOURCE = ("Pregnolato M., Ford A., Wilkinson S.M., Dawson R.J. (2017) The impact of flooding on road "
                     "transport: a depth-disruption function. Transportation Research Part D 55, 67-81, "
                     "doi:10.1016/j.trd.2017.06.020")
MIN_WATER_SPEED_KMH = 5.0          # ASSUMED floor (walking pace) where the curve gives less, for vehicles rated deeper
FORECAST_STALE_MIN = 60.0          # ASSUMED: a live-input forecast published more than an hour ago is STALE
_SIMULATED_WORDS = ("SYNTHETIC", "SIMULATED", "SCENARIO", "DEMO", "TEST", "HISTORICAL", "REPLAY")
_LIVE_WORDS = ("LIVE", "IMD", "OPEN-METEO", "OPENMETEO", "ECMWF", "NOWCAST")
_STALE_WORDS = ("CACHED", "CACHE", "STALE")


def _has_word(text: str, words) -> bool:
    """Whole words only (QA 2026-10-11: "LATEST" contains "TEST")."""
    return any(re.search(rf"(?<![A-Z0-9]){re.escape(w)}(?![A-Z0-9])", text) for w in words)


def pregnolato_speed_kmh(depth_mm: float) -> float:
    """Pregnolato et al. 2017 Eq. 4 (R^2 0.95): maximum speed (km/h) that keeps a 2WD passenger car under control in
    standing water `depth_mm` deep, fitted to 10 points from 0 mm / 88 km/h to 300 mm / 0 km/h. The quadratic itself
    never reaches 0 (minimum ~2 km/h near 307 mm): callers treat >= 300 mm as impassable, as the paper does
    (docs/RESEARCH_NAVIGATION_TRAFFIC.md)."""
    d = max(float(depth_mm), 0.0)
    return 0.0009 * d * d - 0.5529 * d + 86.9448


def speed_in_water_kmh(road_kmh: float, depth_cm: Optional[float]) -> float:
    """The assumed road speed, capped by the depth-disruption curve; never below MIN_WATER_SPEED_KMH in water."""
    if not depth_cm or depth_cm <= 0:
        return float(road_kmh)
    mm = float(depth_cm) * 10.0
    curve = pregnolato_speed_kmh(mm) if mm < 300.0 else 0.0
    return float(min(road_kmh, max(curve, MIN_WATER_SPEED_KMH)))


def forecast_input_label(meta: Optional[dict], now_wall: Optional[datetime]) -> dict:
    """What the forecast behind a flood delay is: SIMULATED (synthetic, scenario, replayed rainfall), MODELLED (a model
    run on live rainfall inputs), STALE (live inputs, published more than FORECAST_STALE_MIN ago) or UNKNOWN."""
    if not meta or not meta.get("data_mode"):
        return {"label": "UNKNOWN", "detail": "no published forecast", "age_min": None}
    mode = str(meta["data_mode"])
    up = mode.upper()
    published = parse_time(meta.get("_updated_at")) if meta.get("_updated_at") else None
    age = round((now_wall - published).total_seconds() / 60.0, 1) if (published and now_wall) else None
    if _has_word(up, _SIMULATED_WORDS):
        return {"label": "SIMULATED", "detail": f"forecast from {mode}", "age_min": age}
    if _has_word(up, _STALE_WORDS):
        return {"label": "STALE", "detail": f"{mode}: a cached input, not current observations", "age_min": age}
    if _has_word(up, _LIVE_WORDS):
        if age is not None and age > FORECAST_STALE_MIN:
            return {"label": "STALE", "detail": f"{mode}; shared with operations {age:.0f} min ago", "age_min": age}
        return {"label": "MODELLED", "detail": f"model run on {mode} (age counted from when it was shared with operations)",
                "age_min": age}
    return {"label": "UNKNOWN", "detail": f"forecast input not recognised ({mode})", "age_min": age}


def traffic_factor(sample: Optional[dict]) -> Optional[float]:
    """free_flow / current for a live sample with usable speeds (>= 1, traffic only adds time); None otherwise."""
    if not sample:
        return None
    try:
        cur, free = float(sample.get("current_kmh") or 0), float(sample.get("free_flow_kmh") or 0)
    except (TypeError, ValueError):
        return None
    if cur <= 0 or free <= 0:
        return None
    return max(1.0, free / cur)


def breakdown(roads: RoadGraph, G: nx.DiGraph, segments: list[dict], *, vehicle: str, dispatch_t_min: float,
              forecast: Forecast, seg_ctx: SegmentContext, closures: ClosureBook,
              traffic: Optional[dict] = None) -> dict:
    """ETA parts for a routed segment list (each with seg_id, enter_t_min, exit_t_min, to_node, status).
    `traffic`: live samples by seg_id (tomtom.sample()["segments"]) or None/{} when there is no live traffic."""
    seg_by_id = {s.seg_id: s for s in roads.segments}
    limit = float(config.VEHICLE_LIMIT_CM[vehicle])
    walking = vehicle == "pedestrian"
    t_free = t = float(dispatch_t_min)
    delay, slowed, unknown_depth, changes, per_seg = 0.0, 0, 0, [], []
    live = traffic or {}
    t_delay, sampled, levels = 0.0, 0, {"normal": 0, "moderate": 0, "heavy": 0}
    for s in segments:
        seg = seg_by_id[s["seg_id"]]
        road = speed_kmh(seg.highway, vehicle)
        free_dt = float(seg.length_m) / 1000.0 / road * 60.0
        t_free += free_dt
        depth = None if walking else forecast.depth_cm(seg.seg_id, t, t + free_dt)
        dt = free_dt
        if depth is None and not walking:
            unknown_depth += 1
        elif depth and depth > 0:
            dt = float(seg.length_m) / 1000.0 / speed_in_water_kmh(road, depth) * 60.0
            later = forecast.depth_cm(seg.seg_id, t, t + dt)     # the slower crossing sees more of the forecast
            if later is None:                    # QA L4: it runs past the forecast end: keep the known depth, say so
                unknown_depth += 1
            else:
                depth = later
            dt = float(seg.length_m) / 1000.0 / speed_in_water_kmh(road, depth) * 60.0
        flood_min = max(0.0, dt - free_dt)
        if flood_min > 1e-9:
            slowed += 1
            delay += flood_min
        factor = traffic_factor(live.get(seg.seg_id))
        level = (live.get(seg.seg_id) or {}).get("level") if factor is not None else None
        traffic_min = free_dt * (factor - 1.0) if factor is not None else 0.0
        if factor is not None:
            sampled += 1
            t_delay += traffic_min
            if level in levels:
                levels[level] += 1
        dt = free_dt + max(flood_min, traffic_min)         # the slower cause governs; never both on top of each other
        st = classify(seg.seg_id, t, t + dt, forecast=forecast, ctx=seg_ctx, closures=closures, limit_cm=limit,
                      vehicle=vehicle)
        if st["status"] != s.get("status"):
            changes.append({"seg_id": seg.seg_id, "name": seg.name, "status_free_flow": s.get("status"),
                            "status_delayed": st["status"], "enter_t_min_delayed": round(t, 2),
                            "reason": st.get("reason", "")})
        per_seg.append({"seg_id": seg.seg_id, "free_min": round(free_dt, 2), "min": round(dt, 2),
                        "depth_cm": None if depth is None else round(float(depth), 1),
                        "flood_min": round(flood_min, 2), "traffic_min": round(traffic_min, 2), "traffic_level": level})
        t += dt
    free = t_free - float(dispatch_t_min)
    flood = None if walking else round(delay, 2)
    total = t - float(dispatch_t_min)
    components = [
        {"key": "base", "label": "ASSUMED ROAD SPEEDS", "minutes": round(free, 2),
         "detail": f"travel time at speeds assumed by road type ({config.ASSUMED_DEFAULT_SPEED_KMH:g} km/h default)"},
        ({"key": "flood", "label": "NOT MODELLED", "minutes": None,
          "detail": "the depth-speed curve is for vehicles; walking through water is not modelled"} if walking else
         {"key": "flood", "label": "MODELLED FLOOD DELAY", "minutes": flood,
          "detail": (f"{slowed} road(s) slowed by forecast water (Pregnolato et al. 2017 depth-speed curve"
                     f"{'; ASSUMED for this vehicle type' if vehicle != 'car' else ''})"
                     + (f"; depth unknown on {unknown_depth} road(s)" if unknown_depth else ""))}),
        ({"key": "traffic", "label": "LIVE TRAFFIC", "minutes": round(t_delay, 2),
          "detail": (f"{sampled} of {len(segments)} road(s) sampled live (TomTom): {levels['heavy']} heavy, "
                     f"{levels['moderate']} moderate, {levels['normal']} normal; unsampled roads add no traffic delay")}
         if sampled else
         {"key": "traffic", "label": "UNKNOWN", "minutes": None,
          "detail": ("live traffic unavailable; using estimated travel time" if traffic is not None else
                     "no live traffic feed is configured; congestion is not included in this ETA")}),
    ]
    return {"free_flow_min": round(free, 2), "flood_delay_min": flood, "eta_min": round(total, 2),
            "eta_without_traffic_min": round(free + delay, 2),      # assumed speeds + modelled flood delay only
            "traffic_delay_min": round(t_delay, 2) if sampled else None, "traffic_sampled": sampled,
            "traffic_unsampled": len(segments) - sampled, "traffic_levels": levels if sampled else None,
            "arrival_t_min": round(t, 2), "segments_slowed": slowed, "unknown_depth_segments": unknown_depth,
            "status_changes_when_delayed": changes, "components": components, "segments": per_seg,
            "method": "free-flow router time + depth-speed cap on forecast water at the delayed minutes",
            "source": PREGNOLATO_SOURCE,
            "note": "A modelled estimate from a flood forecast, not a measured travel time and not a guarantee."}
