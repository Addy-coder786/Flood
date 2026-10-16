"""Tests for the CAP 1.2 alert-draft generator (floodnet.alerts.cap) and GET /api/simulation/{id}/alert.

Two layers:
  1. Pure unit tests over a hand-built SimulationResult with KNOWN numbers, so we can assert that every
     value in the message is traceable to the run and that the CAP enumerations, element order, date-time
     format and polygon/circle formats match the OASIS CAP 1.2 specification.
  2. An API contract test on a real (short-horizon) run, following the 503-tolerant pattern used by
     test_api_smoke.py / test_api_contracts.py.

The honesty properties (status=Draft, issued=False, no dissemination claim, model-derived severity) are
asserted, not assumed -- they are the whole point of this module.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from floodnet import config
from floodnet.alerts.cap import (ALERT_THRESHOLD_CM, CAP_NS, IST, MAX_LISTED_SEGMENT_IDS,
                                 build_cap_alert)
from floodnet.api.main import app
from floodnet.contracts import (Frame, Grid, MassBalance, RainfallScenario, RoadGraph, RoadSegment,
                                SimulationResult)
from floodnet.provenance import Provenance, Tag

client = TestClient(app)

# Values the CAP 1.2 specification enumerates. Anything outside these sets is a spec violation.
CAP_STATUS = {"Actual", "Exercise", "System", "Test", "Draft"}
CAP_MSGTYPE = {"Alert", "Update", "Cancel", "Ack", "Error"}
CAP_SCOPE = {"Public", "Restricted", "Private"}
CAP_CATEGORY = {"Geo", "Met", "Safety", "Security", "Rescue", "Fire", "Health", "Env", "Transport",
                "Infra", "CBRNE", "Other"}
CAP_RESPONSE_TYPE = {"Shelter", "Evacuate", "Prepare", "Execute", "Avoid", "Monitor", "Assess",
                     "AllClear", "None"}
CAP_URGENCY = {"Immediate", "Expected", "Future", "Past", "Unknown"}
CAP_SEVERITY = {"Extreme", "Severe", "Moderate", "Minor", "Unknown"}
CAP_CERTAINTY = {"Observed", "Likely", "Possible", "Unlikely", "Unknown"}

# CAP DateTime: "YYYY-MM-DDThh:mm:ssXzh:zm"; the alphabetic "Z" designator MUST NOT be used.
CAP_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

ALERT_SEQUENCE = ["identifier", "sender", "sent", "status", "msgType", "source", "scope", "restriction",
                  "addresses", "code", "note", "references", "incidents", "info"]
INFO_SEQUENCE = ["language", "category", "event", "responseType", "urgency", "severity", "certainty",
                 "audience", "eventCode", "effective", "onset", "expires", "senderName", "headline",
                 "description", "instruction", "web", "contact", "parameter", "resource", "area"]
AREA_SEQUENCE = ["areaDesc", "polygon", "circle", "geocode", "altitude", "ceiling"]

FIXED_NOW = datetime(2026, 9, 10, 14, 30, 0, tzinfo=IST)


# ----------------------------------------------------------------------------- fixture run
def _road(seg_id: str, name: str, lon0: float, lat0: float) -> RoadSegment:
    lonlat = np.array([[lon0, lat0], [lon0 + 0.001, lat0 + 0.001]], dtype=float)
    return RoadSegment(seg_id=seg_id, name=name, highway="residential", osm_way_id=1, u=0, v=1,
                       length_m=120.0, oneway=False, xy=np.zeros((2, 2)), lonlat=lonlat)


def _frame(t_s: float, street_cm: dict, surcharging: np.ndarray, surch_m3: np.ndarray,
           grid_max_cm: float, rain: float) -> Frame:
    n = len(surcharging)
    depth = np.zeros((4, 4), dtype=np.float32)
    depth[0, 0] = grid_max_cm / 100.0
    return Frame(t_s=t_s, depth=depth,
                 node_hgl=np.zeros(n, dtype=np.float32), node_surcharging=surcharging,
                 node_surcharge_m3=surch_m3, node_cause=np.array([""] * n),
                 edge_flow_m3s=np.zeros(1, dtype=np.float32), edge_util=np.zeros(1, dtype=np.float32),
                 street_depth_m={k: v / 100.0 for k, v in street_cm.items()}, rain_mm_h=rain)


@pytest.fixture()
def flooding_run() -> tuple[SimulationResult, RoadGraph]:
    """A run whose numbers are known exactly, so the generated message can be checked against them.

    Three 5-minute frames. Segment depths in cm:
        t=0   S1 2, S2 0, S3 0        -> nothing at/above the 15 cm threshold
        t=5   S1 22, S2 16, S3 4      -> onset here; 2 segments affected
        t=10  S1 71, S2 33, S3 9      -> PEAK: max 71 cm ('critical' band), 2 segments affected
    Surcharging: 1 node flagged at t=5, 2 nodes at t=10 (one of them only via surcharge_m3 > 0).
    """
    scen = RainfallScenario(id="unit_storm", name="Unit test storm",
                            t_s=np.array([0.0, 300.0, 600.0]),
                            intensity_mm_h=np.array([10.0, 90.0, 40.0]),
                            provenance=Provenance(Tag.SYNTHETIC, "unit test design storm",
                                                  "invented for this test only"))
    surch_none = np.array([False, False, False])
    frames = [
        _frame(0.0, {"S1": 2.0, "S2": 0.0, "S3": 0.0}, surch_none, np.zeros(3), 3.0, 10.0),
        _frame(300.0, {"S1": 22.0, "S2": 16.0, "S3": 4.0},
               np.array([True, False, False]), np.zeros(3), 30.0, 90.0),
        _frame(600.0, {"S1": 71.0, "S2": 33.0, "S3": 9.0},
               np.array([True, False, False]), np.array([0.0, 5.0, 0.0]), 88.0, 40.0),
    ]
    res = SimulationResult(
        run_id="unittestrun", scenario=scen, blockage={"mode": "none"},
        grid=Grid(x0=0.0, y0=0.0, res=10.0, nx=4, ny=4), frames=frames,
        mass_balance=MassBalance(1.0, 0.5, 0.2, 0.2, 0.1, 0.0, 0.0),
        runtime_s=1.0,
        provenance={"rainfall": scen.provenance.to_dict()})
    roads = RoadGraph(node_xy=np.zeros((2, 2)), node_lonlat=np.zeros((2, 2)),
                      segments=[_road("S1", "Test Marg", 72.840, 19.015),
                                _road("S2", "Second Road", 72.845, 19.018),
                                _road("S3", "", 72.850, 19.021)],
                      provenance=Provenance(Tag.REAL, "unit test roads"))
    return res, roads


# ----------------------------------------------------------------------------- structure / spec
def test_cap_structure_and_enumerations(flooding_run):
    res, roads = flooding_run
    draft = build_cap_alert(res, None, roads, data_mode="REAL", now=FIXED_NOW)
    a = draft.alert

    for required in ("identifier", "sender", "sent", "status", "msgType", "scope"):
        assert a.get(required), required
    assert a["status"] in CAP_STATUS and a["msgType"] in CAP_MSGTYPE and a["scope"] in CAP_SCOPE
    # <restriction> is conditional on scope=Restricted -- if we use one we must supply the other.
    assert (a["scope"] == "Restricted") == bool(a.get("restriction"))
    # CAP: identifier and sender "MUST NOT include spaces, commas or restricted characters (< and &)".
    for token in (a["identifier"], a["sender"]):
        assert not any(ch in token for ch in " ,<&"), token
    assert CAP_DT_RE.match(a["sent"]), a["sent"]

    assert len(a["info"]) == 1
    info = a["info"][0]
    for required in ("category", "event", "urgency", "severity", "certainty"):
        assert info.get(required), required
    assert set(info["category"]) <= CAP_CATEGORY
    assert set(info["responseType"]) <= CAP_RESPONSE_TYPE
    assert info["urgency"] in CAP_URGENCY
    assert info["severity"] in CAP_SEVERITY
    assert info["certainty"] in CAP_CERTAINTY
    for t in ("effective", "onset", "expires"):
        assert CAP_DT_RE.match(info[t]), (t, info[t])

    assert len(info["area"]) == 1
    assert info["area"][0]["areaDesc"]
    for p in info["parameter"]:
        assert set(p) == {"valueName", "value"}
        assert p["valueName"] and p["value"]


def test_status_is_draft_and_never_claims_issuance(flooding_run):
    """The single most important property of this module."""
    res, roads = flooding_run
    draft = build_cap_alert(res, None, roads, data_mode="REAL", now=FIXED_NOW)
    a = draft.alert
    # CAP 1.2 defines Draft as "A preliminary template or draft, not actionable in its current form".
    assert a["status"] == "Draft"
    assert a["scope"] == "Restricted"
    assert "FLOODNET-MODEL-FORECAST-DRAFT-NOT-ISSUED" in a["code"]
    params = {p["valueName"]: p["value"] for p in a["info"][0]["parameter"]}
    assert params["FloodNet:messageClass"] == "MODEL_FORECAST_DRAFT_NOT_ISSUED"
    assert params["FloodNet:issuingAuthority"].startswith("NONE")
    assert params["FloodNet:approvalStatus"].startswith("UNAPPROVED")
    assert params["FloodNet:disseminationChannels"].startswith("NONE")
    xml = draft.to_xml()
    assert "NOT AN ISSUED WARNING" in xml
    # No fabricated authority endorsement or contact channel anywhere in the message.
    for forbidden in ("<web>", "<contact>"):
        assert forbidden not in xml, forbidden


def test_every_number_comes_from_the_run(flooding_run):
    res, roads = flooding_run
    draft = build_cap_alert(res, None, roads, data_mode="REAL", now=FIXED_NOW)
    mb = draft.model_basis
    params = {p["valueName"]: p["value"] for p in draft.alert["info"][0]["parameter"]}

    assert mb["run_id"] == res.run_id and params["FloodNet:runId"] == res.run_id
    assert mb["peak_frame_index"] == 2                      # the t=10 min frame
    assert mb["peak_t_min"] == 10.0 and params["FloodNet:peakTMin"] == "10"
    assert mb["peak_depth_cm"] == 71.0                      # max street depth in that frame
    assert params["FloodNet:peakDepthCm"] == "71.0"
    assert mb["peak_grid_depth_cm"] == 88.0                 # grid max is reported separately, not conflated
    assert mb["model_severity_band"] == "critical"          # 71 cm >= 60 cm -> critical band
    assert draft.alert["info"][0]["severity"] == "Extreme"  # critical -> CAP Extreme
    assert mb["onset_t_min"] == 5.0                         # first frame at/above 15 cm
    assert mb["affected_segment_count"] == 2                # S1 71 cm, S2 33 cm (S3 9 cm is below)
    assert [s["seg_id"] for s in mb["affected_segments"]] == ["S1", "S2"]
    assert mb["surcharging_nodes_at_peak"] == 2             # 1 flagged + 1 via surcharge_m3 > 0
    assert mb["surcharging_nodes_run_peak"] == 2
    assert mb["peak_rainfall_mm_h"] == 90.0                 # the run's peak, not the peak frame's
    assert mb["rainfall_provenance"]["tag"] == "SYNTHETIC"
    assert params["FloodNet:rainfallProvenanceTag"] == "SYNTHETIC"
    # ...and the real numbers reach the human-readable description.
    desc = draft.alert["info"][0]["description"]
    assert "71.0 cm" in desc and "90.0 mm/h" in desc and res.run_id in desc
    assert "WORKING THRESHOLDS, NOT CITED GUIDANCE" in desc  # config.py's own label, carried through


def test_times_derive_from_the_forecast_frames(flooding_run):
    res, roads = flooding_run
    draft = build_cap_alert(res, None, roads, data_mode="REAL", now=FIXED_NOW)
    info = draft.alert["info"][0]
    # This scenario has no absolute start time, so t=0 anchors to the drafting moment and says so.
    assert info["effective"] == "2026-09-10T14:30:00+05:30"
    assert info["onset"] == "2026-09-10T14:35:00+05:30"      # anchor + onset_t_min (5 min)
    assert info["expires"] == "2026-09-10T14:40:00+05:30"    # anchor + last frame (10 min)
    assert "no absolute start time" in draft.model_basis["time_anchor_basis"]
    assert info["urgency"] == "Expected"                     # onset is 5 min out -> "within next hour"


def test_time_anchor_uses_a_real_rainfall_timestamp_when_the_run_has_one(flooding_run):
    res, roads = flooding_run
    res.provenance = dict(res.provenance)
    res.provenance["rainfall_source"] = {"timestamp": "2026-09-10T09:00:00+00:00"}
    draft = build_cap_alert(res, None, roads, data_mode="REAL", now=FIXED_NOW)
    info = draft.alert["info"][0]
    assert info["expires"] == "2026-09-10T14:40:00+05:30"    # 09:00Z = 14:30 IST, + 10 min of frames
    assert "rainfall source timestamp" in draft.model_basis["time_anchor_basis"]
    # Onset (14:35 IST) is already in the past relative to `now` (14:30)? No -- it is 5 min ahead.
    assert info["urgency"] == "Expected"


def test_certainty_tracks_the_rainfall_provenance_tag(flooding_run):
    res, roads = flooding_run
    assert build_cap_alert(res, None, roads, now=FIXED_NOW).alert["info"][0]["certainty"] == "Possible"

    res.provenance = {"rainfall": {"tag": "NWP", "source": "ECMWF via Open-Meteo", "note": ""}}
    assert build_cap_alert(res, None, roads, now=FIXED_NOW).alert["info"][0]["certainty"] == "Possible"

    res.provenance = {"rainfall": {"tag": "REAL", "source": "IMD station", "note": ""}}
    assert build_cap_alert(res, None, roads, now=FIXED_NOW).alert["info"][0]["certainty"] == "Likely"
    # A DEMONSTRATION pilot must never imply confidence, whatever the rainfall tag says.
    d = build_cap_alert(res, None, roads, data_mode="DEMONSTRATION", now=FIXED_NOW)
    assert d.alert["info"][0]["certainty"] == "Unknown"
    # FloodNet never observes flooding, so "Observed" must never be emitted.
    for tag in ("REAL", "NWP", "SYNTHETIC", "ESTIMATED", "DEMONSTRATION", "NONSENSE"):
        res.provenance = {"rainfall": {"tag": tag, "source": "x", "note": ""}}
        assert build_cap_alert(res, None, roads, now=FIXED_NOW).alert["info"][0]["certainty"] != "Observed"


def test_dry_run_is_not_dressed_up_as_an_alert(flooding_run):
    """Nothing crosses the threshold -> Minor / Unlikely / Unknown urgency, no onset, no invented streets."""
    res, roads = flooding_run
    res.frames = [_frame(0.0, {"S1": 1.0, "S2": 0.0, "S3": 0.0}, np.zeros(3, dtype=bool), np.zeros(3),
                         1.0, 2.0),
                  _frame(300.0, {"S1": 3.0, "S2": 1.0, "S3": 0.0}, np.zeros(3, dtype=bool), np.zeros(3),
                         4.0, 3.0)]
    draft = build_cap_alert(res, None, roads, now=FIXED_NOW)
    info = draft.alert["info"][0]
    assert info["severity"] == "Minor"
    assert info["certainty"] == "Unlikely"
    assert info["urgency"] == "Unknown"
    assert info.get("onset") is None
    assert draft.model_basis["affected_segment_count"] == 0
    assert info["responseType"] == ["Monitor"]
    # With no flooded segments there is no defensible polygon; CAP <circle> over the pilot bbox instead.
    area = info["area"][0]
    assert "polygon" not in area and len(area["circle"]) == 1
    lat_lon, radius = area["circle"][0].split(" ")
    lat, lon = (float(v) for v in lat_lon.split(","))
    w, s, e, n = config.PILOT_BBOX_LONLAT
    assert s < lat < n and w < lon < e and float(radius) > 0


def test_polygon_format_matches_the_specification(flooding_run):
    res, roads = flooding_run
    area = build_cap_alert(res, None, roads, now=FIXED_NOW).alert["info"][0]["area"][0]
    assert "circle" not in area
    pairs = area["polygon"][0].split(" ")
    # "A minimum of 4 coordinate pairs MUST be present and the first and last pairs ... MUST be the same."
    assert len(pairs) >= 4
    assert pairs[0] == pairs[-1]
    for p in pairs:
        lat, lon = (float(v) for v in p.split(","))
        # CAP pairs are [latitude],[longitude] -- Mumbai is ~19 N, ~72.8 E, so the order is checkable.
        assert 18.5 < lat < 19.5 and 72.5 < lon < 73.5, p
    assert "not a claim that every point inside the boundary floods" in area["areaDesc"] or \
           "not a claim that every point inside the boundary floods" in \
           {p["valueName"]: p["value"] for p in
            build_cap_alert(res, None, roads, now=FIXED_NOW).alert["info"][0]["parameter"]}[
               "FloodNet:areaDerivation"]


def test_real_street_names_and_ids_only(flooding_run):
    res, roads = flooding_run
    draft = build_cap_alert(res, None, roads, now=FIXED_NOW)
    area_desc = draft.alert["info"][0]["area"][0]["areaDesc"]
    assert "Test Marg" in area_desc and "Second Road" in area_desc
    params = {p["valueName"]: p["value"] for p in draft.alert["info"][0]["parameter"]}
    assert params["FloodNet:affectedSegmentIds"].split(" ") == ["S1", "S2"]
    # S3 never reaches the threshold and must not appear anywhere in the message.
    assert "S3" not in params["FloodNet:affectedSegmentIds"]


def test_segment_id_list_is_capped_but_says_so(flooding_run):
    res, roads = flooding_run
    many = {f"X{i}": 40.0 for i in range(MAX_LISTED_SEGMENT_IDS + 7)}
    res.frames = [_frame(0.0, many, np.zeros(3, dtype=bool), np.zeros(3), 40.0, 50.0)]
    roads.segments = [_road(k, f"Road {k}", 72.84, 19.01) for k in many]
    draft = build_cap_alert(res, None, roads, now=FIXED_NOW)
    params = {p["valueName"]: p["value"] for p in draft.alert["info"][0]["parameter"]}
    assert len(params["FloodNet:affectedSegmentIds"].split(" ")) == MAX_LISTED_SEGMENT_IDS
    assert "complete list" in params["FloodNet:affectedSegmentIdsNote"]
    assert len(draft.model_basis["affected_segments"]) == len(many)   # the JSON keeps all of them


def test_works_without_roads(flooding_run):
    """roads is another agent's module; the generator must degrade honestly, not crash or invent.

    Depths still come from the run (Frame.street_depth_m is keyed by seg_id and is part of the result),
    so segments are still counted -- but with no RoadGraph there are no names and no geometry, so the
    message must fall back to a <circle> and must not invent street names.
    """
    res, _ = flooding_run
    draft = build_cap_alert(res, None, None, now=FIXED_NOW)
    assert draft.model_basis["affected_segment_count"] == 2
    assert all(not s["named"] for s in draft.model_basis["affected_segments"])
    area = draft.alert["info"][0]["area"][0]
    assert "polygon" not in area and "circle" in area
    assert "carries a name in the OpenStreetMap extract" in area["areaDesc"]
    assert "Test Marg" not in draft.to_xml()
    ET.fromstring(draft.to_xml())


def test_empty_run_is_refused(flooding_run):
    res, roads = flooding_run
    res.frames = []
    with pytest.raises(ValueError):
        build_cap_alert(res, None, roads, now=FIXED_NOW)


# ----------------------------------------------------------------------------- XML serialisation
def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _assert_in_sequence(children: list[str], sequence: list[str]) -> None:
    """CAP's schema uses <sequence>, so children must appear in the specified order."""
    idx = [sequence.index(c) for c in children]
    assert idx == sorted(idx), (children, idx)


def test_xml_namespace_and_element_order(flooding_run):
    res, roads = flooding_run
    xml = build_cap_alert(res, None, roads, now=FIXED_NOW).to_xml()
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml)
    assert root.tag == f"{{{CAP_NS}}}alert"
    _assert_in_sequence([_local(c.tag) for c in root], ALERT_SEQUENCE)
    infos = root.findall(f"{{{CAP_NS}}}info")
    assert len(infos) == 1
    _assert_in_sequence([_local(c.tag) for c in infos[0]], INFO_SEQUENCE)
    areas = infos[0].findall(f"{{{CAP_NS}}}area")
    assert len(areas) == 1
    _assert_in_sequence([_local(c.tag) for c in areas[0]], AREA_SEQUENCE)
    # <parameter> children are valueName then value.
    for p in infos[0].findall(f"{{{CAP_NS}}}parameter"):
        assert [_local(c.tag) for c in p] == ["valueName", "value"]


def test_xml_and_json_forms_agree(flooding_run):
    res, roads = flooding_run
    draft = build_cap_alert(res, None, roads, now=FIXED_NOW)
    root = ET.fromstring(draft.to_xml())
    info = root.find(f"{{{CAP_NS}}}info")
    assert root.findtext(f"{{{CAP_NS}}}identifier") == draft.alert["identifier"]
    assert root.findtext(f"{{{CAP_NS}}}status") == draft.alert["status"]
    assert info.findtext(f"{{{CAP_NS}}}severity") == draft.alert["info"][0]["severity"]
    assert info.findtext(f"{{{CAP_NS}}}urgency") == draft.alert["info"][0]["urgency"]
    assert info.findtext(f"{{{CAP_NS}}}expires") == draft.alert["info"][0]["expires"]
    xml_params = {p.findtext(f"{{{CAP_NS}}}valueName"): p.findtext(f"{{{CAP_NS}}}value")
                  for p in info.findall(f"{{{CAP_NS}}}parameter")}
    json_params = {p["valueName"]: p["value"] for p in draft.alert["info"][0]["parameter"]}
    assert xml_params == json_params


def test_no_alphabetic_timezone_designator(flooding_run):
    """CAP: 'Alphabetic timezone designators such as "Z" MUST NOT be used.'"""
    res, roads = flooding_run
    root = ET.fromstring(build_cap_alert(res, None, roads, now=FIXED_NOW).to_xml())
    info = root.find(f"{{{CAP_NS}}}info")
    for tag in ("sent",):
        assert CAP_DT_RE.match(root.findtext(f"{{{CAP_NS}}}{tag}"))
    for tag in ("effective", "onset", "expires"):
        assert CAP_DT_RE.match(info.findtext(f"{{{CAP_NS}}}{tag}"))


def test_urgency_is_immediate_when_already_over_threshold(flooding_run):
    res, roads = flooding_run
    res.frames[0].street_depth_m["S1"] = 0.40      # 40 cm at t=0
    draft = build_cap_alert(res, None, roads, now=FIXED_NOW)
    assert draft.model_basis["onset_t_min"] == 0.0
    assert draft.alert["info"][0]["urgency"] == "Immediate"


def test_urgency_is_future_for_a_distant_onset(flooding_run):
    res, roads = flooding_run
    res.frames[1].t_s = 2.0 * 3600.0               # onset pushed to +2 h
    res.frames[2].t_s = 2.5 * 3600.0
    draft = build_cap_alert(res, None, roads, now=FIXED_NOW)
    assert draft.alert["info"][0]["urgency"] == "Future"
    assert draft.alert["info"][0]["expires"] == _iso(FIXED_NOW + timedelta(minutes=150))


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


# ----------------------------------------------------------------------------- API contract
def _skip_if_503(r):
    if r.status_code == 503:
        body = r.json()
        assert body.get("error") == "module_missing"
        pytest.skip(f"module missing: {body['detail'][:120]}")


def _short_run() -> str:
    r = client.get("/api/scenarios"); _skip_if_503(r)
    sid = next((s["id"] for s in r.json() if s["id"] in ("heavy", "cloudburst")), r.json()[0]["id"])
    sim = client.post("/api/simulate",
                      json={"scenario_id": sid, "blockage": {"mode": "none"}, "horizon_min": 30})
    _skip_if_503(sim)
    assert sim.status_code == 200, sim.text
    return sim.json()["run_id"]


def test_alert_endpoint_contract():
    run_id = _short_run()
    r = client.get(f"/api/simulation/{run_id}/alert")
    _skip_if_503(r)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"run_id", "cap", "cap_xml", "model_basis", "issued", "message_class", "disclaimer",
            "provenance", "data_mode"} <= set(body)
    assert body["run_id"] == run_id
    assert body["issued"] is False
    assert body["message_class"] == "MODEL_FORECAST_DRAFT_NOT_ISSUED"
    assert body["cap_namespace"] == CAP_NS and body["cap_version"] == "1.2"
    assert body["cap"]["status"] == "Draft"
    assert body["data_mode"] in ("REAL", "DEMONSTRATION")
    assert body["provenance"].get("blockage") is not None      # same pattern as summarize()
    root = ET.fromstring(body["cap_xml"])
    assert root.tag == f"{{{CAP_NS}}}alert"
    assert body["model_basis"]["run_id"] == run_id
    # The threshold and bands actually used are declared, not hidden.
    assert body["model_basis"]["alert_threshold_cm"] == ALERT_THRESHOLD_CM
    assert body["model_basis"]["severity_bands_cm"] == [list(b) for b in config.SEVERITY_BANDS_CM]


def test_alert_endpoint_xml_format():
    run_id = _short_run()
    r = client.get(f"/api/simulation/{run_id}/alert?format=xml")
    _skip_if_503(r)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/cap+xml")
    assert "attachment" in r.headers.get("content-disposition", "")
    root = ET.fromstring(r.text)
    assert root.tag == f"{{{CAP_NS}}}alert"
    assert root.findtext(f"{{{CAP_NS}}}status") == "Draft"


def test_alert_endpoint_errors():
    assert client.get("/api/simulation/definitely-not-a-run/alert").status_code == 404
    run_id = _short_run()
    r = client.get(f"/api/simulation/{run_id}/alert?format=pdf")
    _skip_if_503(r)
    assert r.status_code == 422
