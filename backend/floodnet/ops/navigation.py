"""Turn instructions, GPS snapping and GPS freshness from FloodNet's own road geometry (DECISIONS D-40).

No third-party navigation service is used: none accepts FloodNet's closures and arrival-time flood depths, and their
terms tie route results to their own maps (docs/LIVE_OPERATIONS_RESEARCH.md §5). Instructions come from the path the
arrival-time router chose: a new instruction where the bearing changes by 20 degrees or more, or the street name
changes. Distances use a local flat-earth approximation, adequate over a few kilometres.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from ..contracts import RoadGraph

GPS_FRESH_S = 30.0          # ASSUMED: a fix older than this is not used for rerouting
GPS_STALE_S = 120.0         # ASSUMED: older than this the signal is treated as lost
_M_PER_DEG_LAT = 110_540.0


def gps_freshness(age_s: Optional[float]) -> str:
    if age_s is None:
        return "NONE"
    if age_s <= GPS_FRESH_S:
        return "FRESH"
    if age_s <= GPS_STALE_S:
        return "STALE"
    return "LOST"


def parse_time(value) -> Optional[datetime]:
    """ISO 8601 text or epoch milliseconds (what the browser's Geolocation API gives) -> aware UTC datetime."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (OverflowError, OSError, ValueError):
        raise ValueError(f"not a valid time: {str(value)[:40]!r}") from None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local(lonlat: np.ndarray, lat0: float) -> np.ndarray:
    k = math.cos(math.radians(lat0)) * 111_320.0
    return np.column_stack([np.asarray(lonlat)[:, 0] * k, np.asarray(lonlat)[:, 1] * _M_PER_DEG_LAT])


def distance_m(a_lonlat, b_lonlat) -> float:
    lat0 = (a_lonlat[1] + b_lonlat[1]) / 2
    p = _local(np.array([a_lonlat, b_lonlat], dtype=float), lat0)
    return float(np.hypot(*(p[1] - p[0])))


def _seg_index(roads: RoadGraph) -> dict:
    cache = getattr(roads, "_ops_seg_index", None)
    if cache is None:
        cache = {s.seg_id: s for s in roads.segments}
        try:
            object.__setattr__(roads, "_ops_seg_index", cache)
        except (AttributeError, TypeError):
            pass
    return cache


def oriented_lonlat(roads: RoadGraph, seg_id: str, to_node: int) -> np.ndarray:
    """The segment's polyline in travel direction (ending at `to_node`)."""
    seg = _seg_index(roads)[seg_id]
    ll = np.asarray(seg.lonlat, dtype=float)
    end = np.asarray(roads.node_lonlat[int(to_node)], dtype=float)
    if np.hypot(*(ll[0] - end)) < np.hypot(*(ll[-1] - end)):
        ll = ll[::-1]
    return ll


def _bearing(p: np.ndarray, q: np.ndarray, lat0: float) -> float:
    a, b = _local(np.array([p, q]), lat0)
    dx, dy = b - a
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def turn_modifier(delta_deg: float) -> str:
    """Signed turn angle (positive = clockwise = right) -> straight / slight / left-right / u-turn."""
    d = abs(delta_deg)
    side = "right" if delta_deg > 0 else "left"
    if d < 20:
        return "straight"
    if d < 60:
        return f"slight {side}"
    if d < 150:
        return side
    return "u-turn"


_COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]


def _compass(bearing: float) -> str:
    return _COMPASS[int(((bearing + 22.5) % 360) // 45)]


def _name(roads: RoadGraph, seg_id: str) -> str:
    return (_seg_index(roads)[seg_id].name or "").strip() or "unnamed road"


def maneuvers(roads: RoadGraph, start_node: int, segments: list[dict], destination: str = "destination") -> list[dict]:
    """Instructions for a path given as trip segments ({seg_id, to_node}); each with the distance to the next one."""
    if not segments:
        return [{"type": "arrive", "modifier": None, "instruction": f"Arrive at {destination}", "street": None,
                 "distance_m": 0.0, "at_lonlat": [float(x) for x in roads.node_lonlat[int(start_node)]], "seg_index": 0}]
    lines = [oriented_lonlat(roads, s["seg_id"], s["to_node"]) for s in segments]
    lat0 = float(np.mean([l[:, 1].mean() for l in lines]))
    seg = _seg_index(roads)
    first_b = _bearing(lines[0][0], lines[0][1], lat0)
    out = [{"type": "depart", "modifier": None, "street": _name(roads, segments[0]["seg_id"]),
            "instruction": f"Head {_compass(first_b)} on {_name(roads, segments[0]['seg_id'])}",
            "distance_m": 0.0, "at_lonlat": lines[0][0].round(6).tolist(), "seg_index": 0}]
    for i, s in enumerate(segments):
        if i > 0:
            prev_b = _bearing(lines[i - 1][-2], lines[i - 1][-1], lat0)
            next_b = _bearing(lines[i][0], lines[i][1], lat0)
            delta = (next_b - prev_b + 540.0) % 360.0 - 180.0
            mod = turn_modifier(delta)
            new_name, old_name = _name(roads, s["seg_id"]), _name(roads, segments[i - 1]["seg_id"])
            if mod != "straight" or new_name != old_name:
                if mod == "straight":
                    typ, text = "continue", f"Continue onto {new_name}"
                elif mod == "u-turn":
                    typ, text = "turn", f"Make a U-turn onto {new_name}"
                elif mod.startswith("slight"):
                    typ, text = "turn", f"Keep {mod} onto {new_name}"
                else:
                    typ, text = "turn", f"Turn {mod} onto {new_name}"
                out.append({"type": typ, "modifier": mod, "street": new_name, "instruction": text, "distance_m": 0.0,
                            "at_lonlat": lines[i][0].round(6).tolist(), "seg_index": i})
        out[-1]["distance_m"] += float(seg[s["seg_id"]].length_m)
    for m in out:
        m["distance_m"] = round(m["distance_m"], 1)
    out.append({"type": "arrive", "modifier": None, "street": None, "instruction": f"Arrive at {destination}",
                "distance_m": 0.0, "at_lonlat": lines[-1][-1].round(6).tolist(), "seg_index": len(segments)})
    return out


def _point_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(distance, fraction along a-b) of the closest point."""
    ab = b - a
    L2 = float(ab @ ab)
    f = 0.0 if L2 == 0 else max(0.0, min(1.0, float((p - a) @ ab) / L2))
    return float(np.hypot(*(a + f * ab - p))), f


def snap_to_route(roads: RoadGraph, start_node: int, segments: list[dict], lon: float, lat: float) -> dict:
    """Closest point of the path to (lon, lat): distance in metres, which segment, and how far along it (0..1)."""
    best = {"distance_m": float("inf"), "seg_index": None, "fraction": 0.0}
    if not segments:
        return best
    p_ll = np.array([[lon, lat]], dtype=float)
    for i, s in enumerate(segments):
        ll = oriented_lonlat(roads, s["seg_id"], s["to_node"])
        pts = _local(np.vstack([ll, p_ll]), lat)
        line, p = pts[:-1], pts[-1]
        lengths = np.hypot(*np.diff(line, axis=0).T)
        total = float(lengths.sum()) or 1.0
        run = 0.0
        for j in range(len(line) - 1):
            d, f = _point_segment(p, line[j], line[j + 1])
            if d < best["distance_m"]:
                best = {"distance_m": d, "seg_index": i, "fraction": (run + f * lengths[j]) / total}
            run += float(lengths[j])
    best["distance_m"] = round(best["distance_m"], 1)
    return best
