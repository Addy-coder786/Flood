"""OASIS Common Alerting Protocol (CAP) v1.2 draft generation from a FloodNet `SimulationResult`.

Specification followed: "Common Alerting Protocol Version 1.2", OASIS Standard, 1 July 2010
(http://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html). Element names, required/optional
cardinality, element ORDER (the schema uses <sequence>), the enumerated values and the coordinate and
date-time formats below were all read from that document -- none of them are invented here.

Relevant literal constraints from the specification that this module implements:
  * namespace              urn:oasis:names:tc:emergency:cap:1.2
  * <identifier>/<sender>  "MUST NOT include spaces, commas or restricted characters (< and &)"
  * date-times             "YYYY-MM-DDThh:mm:ssXzh:zm"; "Alphabetic timezone designators such as 'Z'
                           MUST NOT be used."
  * <polygon>              "a whitespace-delimited list of [WGS 84] coordinate pairs" in
                           "[latitude],[longitude]" order; "A minimum of 4 coordinate pairs MUST be
                           present and the first and last pairs of coordinates MUST be the same."
  * <circle>               "a central point given as a [WGS 84] coordinate pair followed by a space
                           character and a radius value in kilometers."
  * <status>               Actual | Exercise | System | Test | Draft
  * <msgType>              Alert | Update | Cancel | Ack | Error
  * <scope>                Public | Restricted | Private   (<restriction> used when scope=Restricted)
  * <category>             Geo | Met | Safety | Security | Rescue | Fire | Health | Env | Transport |
                           Infra | CBRNE | Other
  * <responseType>         Shelter | Evacuate | Prepare | Execute | Avoid | Monitor | Assess | AllClear | None
  * <urgency>              Immediate | Expected | Future | Past | Unknown
  * <severity>             Extreme | Severe | Moderate | Minor | Unknown
  * <certainty>            Observed | Likely | Possible | Unlikely | Unknown

HONESTY CONTRACT (see the package docstring)
--------------------------------------------
Every message produced here is `status = Draft`, which CAP 1.2 itself defines as "A preliminary template
or draft, not actionable in its current form". FloodNet is not a designated alerting authority, does not
transmit anything, and has no SACHET/cell-broadcast/SMS path. `scope = Restricted` with an explicit
`<restriction>` says the draft is for the reviewing authority, not for public dissemination by FloodNet.

Every number in the generated message is read out of the `SimulationResult` handed in. There are no
example/placeholder values anywhere in this file.

WHY A DRAFT IS THE ONLY HONEST OUTPUT (researched 2026-09-10, sources in the module's report)
----------------------------------------------------------------------------------------------
India's national CAP platform is NDMA SACHET (https://sachet.ndma.gov.in/About), conceptualised and
funded by NDMA and "Developed and maintained by C-DOT", which states it "Complies to ITU-T x.1303 Common
Alerting Protocol (CAP) Standard". Its published workflow has two distinct roles: **Alert Generating
Agencies** (NDMA/C-DOT training material names IMD, CWC, INCOIS, DGRE and FSI) draft warnings, and an
**Alert Authorizing Agency** -- the concerned SDMA, or MHA -- authorises them before dissemination
through authorised telecom service providers. Platform access is OTP / trusted-device gated.

NDMA publishes exactly one integration document, "CAP XML Feed Integration Guide for Agencies", and it is
about CONSUMING the feed ("This guide describes how external agencies should consume the CAP XML feed
efficiently"). No public interface for a third party to SUBMIT an alert was found anywhere on
sachet.ndma.gov.in, ndma.gov.in, cdot.in or dot.gov.in. This module therefore does not attempt one, and
contains no SACHET endpoint, credential or client.

The draft-then-authorise split is the recognised international pattern, not a workaround:
  * OASIS CAP 1.2 provides the `Draft` status value for precisely this.
  * WMO's own wis2box CAP tooling splits "CAP composer" (may only submit for moderation) from
    "CAP approver" (approves and publishes).
  * US IPAWS names third-party "CAP Alert Origination Tools" built by "Alert Origination Service
    Providers"; the alerting authority holds the credentials, the tool does not.
  * Sahana SAMBRO (ITU-T X.1303 deployments in Myanmar/Philippines) carries the approving officer in a
    custom `sahana:approver` CAP <parameter> because CAP has no approver element. FloodNet mirrors that
    with `FloodNet:approvalStatus`.

No published India-specific CAP profile (an analogue of the US IPAWS profile or Canada's CAP-CP) could be
found, so this module claims CAP 1.2 conformance only, and does NOT claim conformance to any Indian
profile. Where SACHET's live feed shows a de-facto convention, we follow it: `language = en-IN`, polygon
based targeting, and no <geocode> (no Indian geocode valueName scheme was observed in live SACHET or IMD
CAP messages, so inventing one would be a fabrication).
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np

from .. import config
from ..contracts import DrainageNetwork, Frame, RoadGraph, SimulationResult

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

# CAP <identifier>/<sender> "MUST NOT include spaces, commas or restricted characters (< and &)".
CAP_SENDER = "floodnet.prototype"
CAP_SENDER_NAME = ("FloodNet urban flood nowcasting prototype -- NOT a designated alerting "
                   "authority; this system does not issue public warnings")
CAP_SOURCE = "FloodNet coupled 2D-surface / drainage-network nowcast model"

# <code> is defined by CAP as "Any user-defined flag or special code". This is the machine-readable flag
# a downstream reader can key on to refuse to treat the message as an issued warning.
CAP_DRAFT_CODE = "FLOODNET-MODEL-FORECAST-DRAFT-NOT-ISSUED"

# Namespace prefix for our <parameter> valueNames. CAP puts no constraint on valueName beyond the
# acronym-casing SHOULD, so a vendor-ish prefix keeps them from colliding with any profile's parameters.
PARAM_PREFIX = "FloodNet"

# Mumbai local time. CAP forbids the alphabetic "Z" designator, so every timestamp carries a numeric offset.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# A road segment counts as "flooded" at or above this depth. This is the SAME number the rest of the API
# uses (api/main.py::FLOOD_SEG_CM = 15.0) -- it is the second entry of config.SEVERITY_BANDS_CM, i.e. the
# depth at which a segment stops being classed "minor" and becomes "moderate" or worse.
ALERT_THRESHOLD_CM = float(config.SEVERITY_BANDS_CM[1][0])

# How many affected seg_ids are listed in the CAP <parameter>. The complete list always travels in the
# JSON `model_basis`; a CAP message with 700 ids in one element is unreadable, not more honest.
MAX_LISTED_SEGMENT_IDS = 25

# --- Model severity band -> CAP <severity>. -----------------------------------------------------------
# IMPORTANT: config.py labels its own bands verbatim
#   "Provisional severity bands (cm). WORKING THRESHOLDS, NOT CITED GUIDANCE (see DECISIONS.md)"
# so the CAP <severity> below is a MODEL-DERIVED classification, not an official flood-severity
# classification from NDMA, IMD, CWC or MCGM. The generated message says so in <description>, in
# <parameter> FloodNet:severityBasis, and in the JSON `model_basis`. Do not present it as official.
_SEVERITY_BY_BAND = {"clear": "Minor", "minor": "Minor", "moderate": "Moderate",
                     "severe": "Severe", "critical": "Extreme"}

_EVENT_BY_BAND = {"clear": "Urban Flooding Outlook - No Street Flooding Threshold Exceeded",
                  "minor": "Urban Street Flooding - Minor",
                  "moderate": "Urban Street Flooding - Moderate",
                  "severe": "Urban Street Flooding - Severe",
                  "critical": "Urban Street Flooding - Critical"}

# --- Rainfall provenance tag -> CAP <certainty>. -------------------------------------------------------
# CAP defines certainty as the certainty of the EVENT. FloodNet never observes street flooding, so
# "Observed" is never emitted: the flood itself is always a model result. The tags are floodnet.provenance.Tag.
_CERTAINTY_BY_RAIN_TAG = {
    "REAL": "Likely",          # rainfall is a real observation, persisted forward; the flood is modelled
    "NWP": "Possible",         # numerical weather prediction input -- forecast rainfall, forecast flood
    "ESTIMATED": "Possible",
    "SYNTHETIC": "Possible",   # design storm: a hypothetical rainfall, so a hypothetical flood
    "DEMONSTRATION": "Unknown",
}


# ----------------------------------------------------------------------------- small helpers
def _cap_dt(dt: datetime) -> str:
    """CAP DateTime: 'YYYY-MM-DDThh:mm:ssXzh:zm'. 'Z' MUST NOT be used; UTC MUST be '-00:00'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    dt = dt.replace(microsecond=0)
    s = dt.isoformat()
    if s.endswith("+00:00"):
        s = s[:-6] + "-00:00"
    return s


def _clean_token(s: str) -> str:
    """Strip the characters CAP forbids in <identifier>/<sender> (spaces, commas, '<', '&')."""
    out = []
    for ch in str(s):
        out.append("-" if ch in " ,<&" else ch)
    return "".join(out)


def _band_for_cm(cm: float) -> str:
    """Model severity band for a depth in cm, using config.SEVERITY_BANDS_CM.

    Same rule as floodnet.streets.aggregate.severity / api.main._severity: the first band whose limit the
    depth is BELOW wins, otherwise 'critical'. Reimplemented here (three lines) so this package has no
    import-time dependency on another agent's module.
    """
    for limit, label in config.SEVERITY_BANDS_CM:
        if cm < limit:
            return label
    return "critical"


def _f(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _surcharging_count(f: Frame) -> int:
    """Interval-aware surcharging node count.

    Identical definition to api/main.py::_node_fill_state: `node_surcharging` only reflects the final solver
    sub-step of a 5-minute frame, while `node_surcharge_m3` accumulates the whole interval, so a node that
    surcharged and drained inside one frame would otherwise be invisible. Either signal counts.
    """
    flag = np.asarray(f.node_surcharging, dtype=bool)
    vol = np.asarray(f.node_surcharge_m3, dtype=np.float64) > 1e-9
    return int(np.sum(flag | vol))


def _time_anchor(res: SimulationResult, now: datetime) -> tuple[datetime, str]:
    """Wall-clock time that simulation t=0 corresponds to, plus an honest label for how we got it.

    A live (IMD) or ECMWF run carries a real production/observation timestamp in
    res.provenance['rainfall_source']['timestamp'] (written by api/state.py from RainfallSourceMeta).
    A design-storm or historical-replay scenario has no absolute start time at all -- the SimulationResult
    contract stores only relative t_s -- so t=0 is anchored to the moment this draft is generated, and the
    message says exactly that rather than implying a forecast issue time it does not have.
    """
    src = res.provenance.get("rainfall_source") if isinstance(res.provenance, dict) else None
    ts = (src or {}).get("timestamp") if isinstance(src, dict) else None
    if isinstance(ts, str) and ts:
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(IST), f"rainfall source timestamp ({ts})"
    return now, ("draft generation time -- this scenario's rainfall has no absolute start time, so "
                 "forecast minute 0 is anchored to when the draft was produced")


# ----------------------------------------------------------------------------- model readout
def _street_peaks(res: SimulationResult) -> tuple[list[float], list[int]]:
    """Per-frame (max street depth in cm, count of segments at/above ALERT_THRESHOLD_CM)."""
    max_cm: list[float] = []
    n_flooded: list[int] = []
    for f in res.frames:
        if f.street_depth_m:
            v = np.asarray(list(f.street_depth_m.values()), dtype=np.float64) * 100.0
            max_cm.append(_f(np.nanmax(v)))
            n_flooded.append(int(np.sum(v >= ALERT_THRESHOLD_CM)))
        else:
            max_cm.append(0.0)
            n_flooded.append(0)
    return max_cm, n_flooded


def _segment_lookup(roads: Optional[RoadGraph]) -> dict:
    if roads is None:
        return {}
    return {s.seg_id: s for s in roads.segments}


def _affected_segments(frame: Frame, segs: dict) -> list[dict]:
    """Every road segment at/above the alert threshold in this frame, deepest first, with REAL ids/names."""
    out = []
    for seg_id, depth_m in (frame.street_depth_m or {}).items():
        cm = _f(depth_m) * 100.0
        if cm < ALERT_THRESHOLD_CM:
            continue
        seg = segs.get(seg_id)
        out.append({"seg_id": str(seg_id),
                    "name": (getattr(seg, "name", "") or "").strip() or str(seg_id),
                    "named": bool((getattr(seg, "name", "") or "").strip()),
                    "highway": str(getattr(seg, "highway", "") or ""),
                    "depth_cm": round(cm, 1),
                    "severity_band": _band_for_cm(cm)})
    out.sort(key=lambda d: -d["depth_cm"])
    return out


def _pilot_centroid_and_radius_km() -> tuple[float, float, float]:
    """Centroid (lat, lon) and half-diagonal radius (km) of config.PILOT_BBOX_LONLAT."""
    w, s, e, n = config.PILOT_BBOX_LONLAT
    lat = (s + n) / 2.0
    lon = (w + e) / 2.0
    # Local metric approximation, adequate for a ~2 km pilot box (this is a radius for a CAP <circle>,
    # not a computation the physics depends on).
    dy_km = (n - s) * 110.574 / 2.0
    dx_km = (e - w) * 111.320 * math.cos(math.radians(lat)) / 2.0
    return lat, lon, math.hypot(dx_km, dy_km)


def _area_geometry(affected: list[dict], segs: dict) -> tuple[Optional[str], Optional[str], str]:
    """Return (polygon_string, circle_string, method_note) for the CAP <area> block.

    A polygon is only emitted when a defensible one can actually be derived: the convex hull of the REAL
    OSM vertices of the segments the model puts at/above the alert threshold. That hull is an honest
    envelope ("everything the model flags lies inside this boundary"), NOT a claim that every point inside
    it floods -- which is exactly what the returned method note says, and what goes into the message.

    When there is nothing to hull (no flooded segments, missing road geometry, a degenerate hull, or no
    shapely) we fall back to a CAP <circle> over the declared pilot bounding box. We never invent a
    precise boundary.
    """
    pts: list[tuple[float, float]] = []   # (lon, lat)
    for a in affected:
        seg = segs.get(a["seg_id"])
        ll = getattr(seg, "lonlat", None) if seg is not None else None
        if ll is None:
            continue
        arr = np.asarray(ll, dtype=float)
        if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 2:
            continue
        for lon, lat in arr[:, :2]:
            if math.isfinite(lon) and math.isfinite(lat):
                pts.append((float(lon), float(lat)))

    if len(pts) >= 3:
        try:
            from shapely.geometry import MultiPoint
            hull = MultiPoint(pts).convex_hull
            ring = list(getattr(hull, "exterior", None).coords) if hull.geom_type == "Polygon" else []
        except Exception:  # noqa: BLE001 -- shapely absent or a degenerate hull; fall through to <circle>
            ring = []
        if len(ring) >= 4:
            if ring[0] != ring[-1]:
                ring = ring + [ring[0]]                       # CAP: first and last pair MUST be the same
            # CAP polygon coordinate pairs are "[latitude],[longitude]", whitespace-delimited.
            poly = " ".join(f"{lat:.6f},{lon:.6f}" for lon, lat in ring)
            return (poly, None,
                    "convex hull of the OpenStreetMap geometry of every road segment the model forecasts "
                    f"at or above {ALERT_THRESHOLD_CM:.0f} cm; an outer envelope, not a claim that every "
                    "point inside the boundary floods")

    lat, lon, radius_km = _pilot_centroid_and_radius_km()
    # CAP circle: "[latitude],[longitude] radius-in-km".
    return (None, f"{lat:.6f},{lon:.6f} {radius_km:.3f}",
            "no defensible flood boundary could be derived from the run, so the area falls back to a "
            "circle covering the declared pilot bounding box (config.PILOT_BBOX_LONLAT)")


# ----------------------------------------------------------------------------- the alert object
@dataclass(frozen=True)
class CapAlert:
    """A CAP 1.2 message in dict form plus the model readout it was derived from.

    `alert` mirrors the CAP element tree 1:1 (keys are CAP element names, in schema order); `to_xml()`
    serialises exactly that dict, so the JSON and XML forms can never drift apart.
    """
    alert: dict
    model_basis: dict = field(default_factory=dict)

    def to_xml(self, *, pretty: bool = True) -> str:
        root = ET.Element("alert", {"xmlns": CAP_NS})
        a = self.alert
        for tag in ("identifier", "sender", "sent", "status", "msgType", "source", "scope", "restriction"):
            if a.get(tag) is not None:
                ET.SubElement(root, tag).text = str(a[tag])
        for code in a.get("code", []) or []:
            ET.SubElement(root, "code").text = str(code)
        if a.get("note") is not None:
            ET.SubElement(root, "note").text = str(a["note"])

        for info in a.get("info", []) or []:
            ie = ET.SubElement(root, "info")
            if info.get("language") is not None:
                ET.SubElement(ie, "language").text = str(info["language"])
            for c in info.get("category", []) or []:
                ET.SubElement(ie, "category").text = str(c)
            ET.SubElement(ie, "event").text = str(info.get("event", ""))
            for rt in info.get("responseType", []) or []:
                ET.SubElement(ie, "responseType").text = str(rt)
            for tag in ("urgency", "severity", "certainty", "audience"):
                if info.get(tag) is not None:
                    ET.SubElement(ie, tag).text = str(info[tag])
            for tag in ("effective", "onset", "expires", "senderName", "headline", "description",
                        "instruction", "web", "contact"):
                if info.get(tag) is not None:
                    ET.SubElement(ie, tag).text = str(info[tag])
            for p in info.get("parameter", []) or []:
                pe = ET.SubElement(ie, "parameter")
                ET.SubElement(pe, "valueName").text = str(p.get("valueName", ""))
                ET.SubElement(pe, "value").text = str(p.get("value", ""))
            for area in info.get("area", []) or []:
                ae = ET.SubElement(ie, "area")
                ET.SubElement(ae, "areaDesc").text = str(area.get("areaDesc", ""))
                for poly in area.get("polygon", []) or []:
                    ET.SubElement(ae, "polygon").text = str(poly)
                for circ in area.get("circle", []) or []:
                    ET.SubElement(ae, "circle").text = str(circ)
                for g in area.get("geocode", []) or []:
                    ge = ET.SubElement(ae, "geocode")
                    ET.SubElement(ge, "valueName").text = str(g.get("valueName", ""))
                    ET.SubElement(ge, "value").text = str(g.get("value", ""))

        if pretty:
            ET.indent(root, space="  ")
        body = ET.tostring(root, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + ("\n" if not body.endswith("\n") else "")


# ----------------------------------------------------------------------------- the generator
def build_cap_alert(res: SimulationResult,
                    net: Optional[DrainageNetwork] = None,
                    roads: Optional[RoadGraph] = None,
                    *,
                    data_mode: str = "UNKNOWN",
                    now: Optional[datetime] = None) -> CapAlert:
    """Build a CAP 1.2 **draft** from a completed run. Every field comes from `res`.

    `now` exists so tests can pin the clock; it defaults to the real current time in IST.
    """
    now = (now.astimezone(IST) if now is not None and now.tzinfo is not None
           else (now.replace(tzinfo=IST) if now is not None else datetime.now(IST)))
    frames = res.frames
    if not frames:
        raise ValueError("cannot draft a CAP alert from a run with no frames")

    frames_t_min = [f.t_s / 60.0 for f in frames]
    anchor, anchor_note = _time_anchor(res, now)

    # ---- what the model actually says --------------------------------------------------------------
    street_max_cm, flooded_counts = _street_peaks(res)
    grid_max_cm = [_f(np.nanmax(f.depth) * 100.0) if f.depth.size else 0.0 for f in frames]

    have_streets = any(c > 0 for c in street_max_cm)
    basis_series = street_max_cm if have_streets else grid_max_cm
    peak_idx = int(np.argmax(basis_series))
    peak_frame = frames[peak_idx]
    peak_t_min = frames_t_min[peak_idx]
    peak_depth_cm = round(basis_series[peak_idx], 1)
    depth_basis = ("maximum forecast water depth on a road segment (streets/aggregate output)"
                   if have_streets else
                   "maximum forecast water depth over the terrain grid (no per-street depths in this run)")

    band = _band_for_cm(peak_depth_cm)
    segs = _segment_lookup(roads)
    affected = _affected_segments(peak_frame, segs)

    # First frame at/above the alert threshold -> the forecast onset (lead time from t=0).
    onset_idx = next((i for i, cm in enumerate(basis_series) if cm >= ALERT_THRESHOLD_CM), None)
    onset_t_min = frames_t_min[onset_idx] if onset_idx is not None else None

    # ---- CAP enumerations, derived (never hardcoded to an example) ----------------------------------
    severity = _SEVERITY_BY_BAND.get(band, "Unknown")
    if onset_t_min is None:
        urgency = "Unknown"                       # nothing crosses the threshold -> no onset to act on
    elif anchor + timedelta(minutes=onset_t_min) <= now:
        urgency = "Immediate"                     # already at/over threshold at the drafting moment
    elif onset_t_min <= 60.0:
        urgency = "Expected"                      # CAP: "responsive action SHOULD be taken soon (within next hour)"
    else:
        urgency = "Future"

    rain_prov = (res.provenance.get("rainfall") or {}) if isinstance(res.provenance, dict) else {}
    rain_tag = str(rain_prov.get("tag", "")).upper() or "UNKNOWN"
    certainty = _CERTAINTY_BY_RAIN_TAG.get(rain_tag, "Unknown")
    if str(data_mode).upper() == "DEMONSTRATION":
        certainty = "Unknown"                     # pilot itself is placeholder data; do not imply confidence
    if onset_t_min is None:
        certainty = "Unlikely"                    # CAP: "Not expected to occur" -- the modelled event does not happen

    # ---- times -------------------------------------------------------------------------------------
    effective = now
    onset = anchor + timedelta(minutes=onset_t_min) if onset_t_min is not None else None
    expires = anchor + timedelta(minutes=frames_t_min[-1])
    horizon_min = frames_t_min[-1] - frames_t_min[0]

    # ---- area --------------------------------------------------------------------------------------
    polygon, circle, area_method = _area_geometry(affected, segs)
    named = [a["name"] for a in affected if a["named"]]
    seen: set[str] = set()
    named_unique = [n for n in named if not (n in seen or seen.add(n))]
    if named_unique:
        streets_phrase = "; ".join(named_unique[:8]) + (f"; +{len(named_unique) - 8} more named streets"
                                                        if len(named_unique) > 8 else "")
        area_desc = (f"{config.PILOT_NAME}, Mumbai, Maharashtra, India -- {len(affected)} road segment(s) "
                     f"forecast at or above {ALERT_THRESHOLD_CM:.0f} cm at the forecast peak, including: "
                     f"{streets_phrase}")
    elif affected:
        area_desc = (f"{config.PILOT_NAME}, Mumbai, Maharashtra, India -- {len(affected)} road segment(s) "
                     f"forecast at or above {ALERT_THRESHOLD_CM:.0f} cm at the forecast peak (none of the "
                     f"affected segments carries a name in the OpenStreetMap extract)")
    else:
        area_desc = (f"{config.PILOT_NAME}, Mumbai, Maharashtra, India -- no road segment reaches "
                     f"{ALERT_THRESHOLD_CM:.0f} cm anywhere in this run")

    # ---- text --------------------------------------------------------------------------------------
    surcharging_peak = _surcharging_count(peak_frame)
    surcharging_run_peak = max((_surcharging_count(f) for f in frames), default=0)
    n_nodes = int(net.n_nodes) if net is not None else 0
    peak_rain = _f(max((f.rain_mm_h for f in frames), default=0.0))
    scenario_name = res.scenario.name or res.scenario.id

    disclaimer = ("NOT AN ISSUED WARNING. This is a machine-generated DRAFT produced by the FloodNet "
                  "prototype for review by an authorised alerting authority. FloodNet is not a designated "
                  "alerting authority, has not issued this message, does not transmit alerts, and has no "
                  "SMS, cell-broadcast or public-notification channel.")

    headline = (f"DRAFT (NOT ISSUED) - {_EVENT_BY_BAND.get(band, 'Urban Street Flooding')}, "
                f"{config.PILOT_NAME}: peak {peak_depth_cm:.0f} cm at forecast minute {peak_t_min:.0f}")

    if affected:
        impact_line = (f"At the forecast peak (minute {peak_t_min:.0f} of the run, i.e. "
                       f"{_cap_dt(anchor + timedelta(minutes=peak_t_min))}), {len(affected)} road segment(s) "
                       f"in the pilot area are at or above {ALERT_THRESHOLD_CM:.0f} cm, with a maximum of "
                       f"{peak_depth_cm:.1f} cm ({depth_basis}).")
    else:
        impact_line = (f"No road segment in the pilot area reaches {ALERT_THRESHOLD_CM:.0f} cm in this run. "
                       f"The maximum forecast depth is {peak_depth_cm:.1f} cm at minute {peak_t_min:.0f} "
                       f"({depth_basis}).")

    description = "\n".join([
        disclaimer,
        "",
        f"Model run: {res.run_id}. Rainfall input: {scenario_name} "
        f"[provenance tag {rain_tag}: {rain_prov.get('source', 'unrecorded')}]. "
        f"Peak rainfall intensity in the run: {peak_rain:.1f} mm/h.",
        f"Forecast window: minute {frames_t_min[0]:.0f} to minute {frames_t_min[-1]:.0f} "
        f"({horizon_min:.0f} min, {len(frames)} frames). Forecast minute 0 is anchored to {_cap_dt(anchor)} "
        f"({anchor_note}).",
        impact_line,
        (f"Forecast onset of the {ALERT_THRESHOLD_CM:.0f} cm threshold: minute {onset_t_min:.0f} "
         f"({_cap_dt(onset)})." if onset is not None else
         f"The {ALERT_THRESHOLD_CM:.0f} cm threshold is not crossed at any point in the forecast window."),
        f"Drainage: {surcharging_peak} of {n_nodes} modelled network nodes are surcharging at the forecast "
        f"peak frame ({surcharging_run_peak} at the worst frame of the run)."
        if n_nodes else
        f"Drainage: {surcharging_peak} modelled network nodes are surcharging at the forecast peak frame "
        f"({surcharging_run_peak} at the worst frame of the run).",
        f"Blockage assumption applied to the drainage network for this run: {res.blockage}.",
        "",
        f"Severity classification basis: the depth bands in FloodNet's config.SEVERITY_BANDS_CM, which that "
        f"file labels verbatim \"WORKING THRESHOLDS, NOT CITED GUIDANCE\". The CAP severity value "
        f"'{severity}' above is therefore MODEL-DERIVED and is NOT an official flood-severity "
        f"classification from NDMA, IMD, CWC or MCGM.",
        f"Affected-area geometry: {area_method}.",
    ])

    instruction = "\n".join([
        "FOR THE REVIEWING OFFICER (this draft has no other recipients):",
        "1. This message is CAP status=Draft. It is not actionable as it stands and has not been sent to "
        "anyone. FloodNet cannot and does not disseminate it.",
        "2. Verify the rainfall input and its provenance tag (see the FloodNet:rainfallProvenanceTag "
        "parameter) before treating any of the numbers as a basis for action. A SYNTHETIC tag means the "
        "rainfall is a design storm, not an observation or a forecast.",
        "3. If you judge a warning warranted, edit this draft under your own authority, replace the sender, "
        "senderName, status, scope, severity and area fields with your agency's own values, and put it "
        "through your own alerting workflow. In India that means an Alert Generating Agency raising it and "
        "the concerned Alert Authorizing Agency (SDMA, or MHA) authorising it on NDMA's SACHET/CAP platform "
        "before any dissemination. FloodNet has no integration with, credential for, or route into that "
        "platform, and no public interface to submit one exists.",
        "4. FloodNet holds no phone numbers, no subscriber lists and no cell-broadcast access. Nothing in "
        "this system can reach a citizen.",
        "5. This message claims conformance to OASIS CAP 1.2 only. No published India-specific CAP profile "
        "was found, so no conformance to one is claimed; validate against your own platform's rules.",
    ])

    params: list[dict] = [
        {"valueName": f"{PARAM_PREFIX}:messageClass", "value": "MODEL_FORECAST_DRAFT_NOT_ISSUED"},
        {"valueName": f"{PARAM_PREFIX}:issuingAuthority",
         "value": "NONE - FloodNet is not a designated alerting authority and has not issued this message"},
        # CAP has no element for "who approved this", so deployments carry it in a <parameter> -- Sahana
        # SAMBRO's ITU-T X.1303 deployments use `sahana:approver` for exactly this. Ours is always
        # unapproved: no officer has seen this draft at the moment it is generated.
        {"valueName": f"{PARAM_PREFIX}:approvalStatus",
         "value": "UNAPPROVED - no alert-authorizing officer has reviewed or approved this draft"},
        {"valueName": f"{PARAM_PREFIX}:disseminationChannels",
         "value": "NONE - FloodNet has no SMS, cell-broadcast, push or SACHET path and holds no "
                  "subscriber contact data"},
        {"valueName": f"{PARAM_PREFIX}:runId", "value": res.run_id},
        {"valueName": f"{PARAM_PREFIX}:scenarioId", "value": res.scenario.id},
        {"valueName": f"{PARAM_PREFIX}:rainfallSource", "value": scenario_name},
        {"valueName": f"{PARAM_PREFIX}:rainfallProvenanceTag", "value": rain_tag},
        {"valueName": f"{PARAM_PREFIX}:rainfallProvenanceSource",
         "value": str(rain_prov.get("source", "unrecorded"))},
        {"valueName": f"{PARAM_PREFIX}:dataMode", "value": str(data_mode)},
        {"valueName": f"{PARAM_PREFIX}:forecastHorizonMin", "value": f"{horizon_min:.0f}"},
        {"valueName": f"{PARAM_PREFIX}:forecastFrameCount", "value": str(len(frames))},
        {"valueName": f"{PARAM_PREFIX}:timeAnchorBasis", "value": anchor_note},
        {"valueName": f"{PARAM_PREFIX}:peakDepthCm", "value": f"{peak_depth_cm:.1f}"},
        {"valueName": f"{PARAM_PREFIX}:peakDepthBasis", "value": depth_basis},
        {"valueName": f"{PARAM_PREFIX}:peakTMin", "value": f"{peak_t_min:.0f}"},
        {"valueName": f"{PARAM_PREFIX}:alertThresholdCm", "value": f"{ALERT_THRESHOLD_CM:.0f}"},
        {"valueName": f"{PARAM_PREFIX}:affectedSegmentCount", "value": str(len(affected))},
        {"valueName": f"{PARAM_PREFIX}:surchargingNodeCountAtPeak", "value": str(surcharging_peak)},
        {"valueName": f"{PARAM_PREFIX}:surchargingNodeCountRunPeak", "value": str(surcharging_run_peak)},
        {"valueName": f"{PARAM_PREFIX}:peakRainfallMmH", "value": f"{peak_rain:.1f}"},
        {"valueName": f"{PARAM_PREFIX}:modelSeverityBand", "value": band},
        {"valueName": f"{PARAM_PREFIX}:severityBasis",
         "value": "config.SEVERITY_BANDS_CM - labelled in that file as \"WORKING THRESHOLDS, NOT CITED "
                  "GUIDANCE\"; model-derived, not an official classification"},
        {"valueName": f"{PARAM_PREFIX}:areaDerivation", "value": area_method},
    ]
    if affected:
        # Only the deepest MAX_LISTED_SEGMENT_IDS ids go on the wire -- a run can put several hundred
        # segments over the threshold and a CAP message is meant to be readable. The COMPLETE list (with
        # per-segment depths) is always in the JSON `model_basis.affected_segments`, and the parameter says
        # so rather than silently truncating.
        listed = affected[:MAX_LISTED_SEGMENT_IDS]
        params.append({"valueName": f"{PARAM_PREFIX}:affectedSegmentIds",
                       "value": " ".join(a["seg_id"] for a in listed)})
        if len(affected) > len(listed):
            params.append({"valueName": f"{PARAM_PREFIX}:affectedSegmentIdsNote",
                           "value": f"{len(listed)} deepest of {len(affected)} affected segments listed; "
                                    f"the complete list is in the FloodNet API response field "
                                    f"model_basis.affected_segments"})

    response_types = ["Avoid", "Monitor"] if affected else ["Monitor"]

    area: dict = {"areaDesc": area_desc}
    if polygon:
        area["polygon"] = [polygon]
    if circle:
        area["circle"] = [circle]

    alert_dict = {
        "identifier": _clean_token(f"FLOODNET-DRAFT-{res.run_id}-{now.strftime('%Y%m%dT%H%M%S')}"),
        "sender": CAP_SENDER,
        "sent": _cap_dt(now),
        "status": "Draft",       # CAP 1.2: "A preliminary template or draft, not actionable in its current form"
        "msgType": "Alert",
        "source": CAP_SOURCE,
        "scope": "Restricted",
        "restriction": ("Draft for review by an authorised alerting authority only. Not for public "
                        "dissemination by FloodNet, which has no authority or channel to disseminate it."),
        "code": [CAP_DRAFT_CODE],
        "note": disclaimer,
        "info": [{
            "language": "en-IN",
            # CAP categories: Met (rainfall-driven flooding) and Transport (the modelled impact is on roads).
            "category": ["Met", "Transport"],
            "event": _EVENT_BY_BAND.get(band, "Urban Street Flooding"),
            "responseType": response_types,
            "urgency": urgency,
            "severity": severity,
            "certainty": certainty,
            "audience": "Authorised alerting authority reviewing this draft. Not the general public.",
            "effective": _cap_dt(effective),
            "onset": _cap_dt(onset) if onset is not None else None,
            "expires": _cap_dt(expires),
            "senderName": CAP_SENDER_NAME,
            "headline": headline,
            "description": description,
            "instruction": instruction,
            # <web> and <contact> are deliberately omitted: FloodNet has no published public URL or
            # contact point for this prototype, and CAP marks both OPTIONAL. Inventing them would be a
            # fabrication.
            "parameter": params,
            "area": [area],
        }],
    }

    model_basis = {
        "run_id": res.run_id,
        "scenario_id": res.scenario.id,
        "scenario_name": scenario_name,
        "blockage": res.blockage,
        "frames_t_min": frames_t_min,
        "alert_threshold_cm": ALERT_THRESHOLD_CM,
        "severity_bands_cm": [list(b) for b in config.SEVERITY_BANDS_CM],
        "severity_bands_caveat": "config.SEVERITY_BANDS_CM is labelled in config.py as \"WORKING "
                                 "THRESHOLDS, NOT CITED GUIDANCE\" -- model-derived, not official.",
        "peak_frame_index": peak_idx,
        "peak_t_min": peak_t_min,
        "peak_depth_cm": peak_depth_cm,
        "peak_depth_basis": depth_basis,
        "peak_grid_depth_cm": round(max(grid_max_cm), 1) if grid_max_cm else 0.0,
        "peak_street_depth_cm": round(max(street_max_cm), 1) if street_max_cm else 0.0,
        "model_severity_band": band,
        "onset_t_min": onset_t_min,
        "time_anchor": _cap_dt(anchor),
        "time_anchor_basis": anchor_note,
        "peak_rainfall_mm_h": peak_rain,
        "rainfall_provenance": rain_prov,
        "affected_segments": affected,
        "affected_segment_count": len(affected),
        "peak_flooded_segment_count_over_run": max(flooded_counts) if flooded_counts else 0,
        "surcharging_nodes_at_peak": surcharging_peak,
        "surcharging_nodes_run_peak": surcharging_run_peak,
        "network_node_count": n_nodes,
        "area_derivation": area_method,
        "data_mode": str(data_mode),
        "cap_mapping": {"severity": severity, "urgency": urgency, "certainty": certainty,
                        "status": "Draft", "msgType": "Alert", "scope": "Restricted"},
        "disclaimer": disclaimer,
    }
    return CapAlert(alert=alert_dict, model_basis=model_basis)
