"""Independent QA (2026-10-11, review of D-49..D-51) reproductions for direct trips, copied from the reviewer's scratch
tests. Each asserts the correct behaviour; all failed against the code they reviewed (RED), see docs/LIVE_OPERATIONS.md
§11."""
import json

import pytest
from fastapi.testclient import TestClient

from floodnet.access.events import ClosureBook, parse_events
from floodnet.api import ops_api
from floodnet.api.main import app
from floodnet.ops import eta, impact
from floodnet.ops import store as ops_store
from floodnet.ops import trips as tr
from floodnet.response import planner
from tests.ops_net import G, LONLAT, NO_CTX, ROADS, SEG_IDS, T, forecast, mid

TOKEN = "test-dispatcher-token"
D = {"X-FloodNet-Token": TOKEN}
O, DEST = LONLAT[0].tolist(), LONLAT[2].tolist()
BODY = {"origin": O, "dest": DEST, "vehicle_type": "car", "names": {"origin": "Base", "destination": "Hospital"}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.delenv("VERCEL", raising=False)
    ops_store.reset_store_for_tests()
    monkeypatch.setattr(ops_api, "_ctx", lambda: (ROADS, G, NO_CTX, set(SEG_IDS)))
    yield TestClient(app, raise_server_exceptions=False)
    ops_store.reset_store_for_tests()


def _publish(c, data_mode="SYNTHETIC rainfall: test", **depths):
    streets = {sid: depths.get(sid, [0.0] * len(T)) for sid in SEG_IDS}
    r = c.post("/api/ops/forecast", headers=D, json={"run_id": "test", "data_mode": data_mode,
                                                     "series_t_min": list(T), "streets_cm": streets})
    assert r.status_code == 200, r.text


def _raw(c, path, text):
    return c.post(path, headers={**D, "content-type": "application/json"}, content=text)


# ---------------------------------------------------------------- 1. NaN / Infinity coordinates
def test_nan_coordinates_are_refused_with_422(client):
    _publish(client)
    body = json.dumps({**BODY, "origin": [float("nan"), float("nan")]})       # json.dumps writes NaN
    r = _raw(client, "/api/ops/trips/preview", body)
    print("preview NaN ->", r.status_code, r.text[:300])
    r2 = _raw(client, "/api/ops/trips/direct", json.dumps({**BODY, "origin": [O[0], float("nan")], "client_ref": "n1"}))
    print("direct [lon, NaN] ->", r2.status_code, r2.text[:300])
    lst = client.get("/api/ops/trips", headers=D)
    print("GET trips afterwards ->", lst.status_code, lst.text[:200])
    assert r.status_code == 422 and r2.status_code == 422 and lst.status_code == 200


def test_infinity_coordinates_are_refused_with_422(client):
    _publish(client)
    r = _raw(client, "/api/ops/trips/preview", json.dumps({**BODY, "dest": [float("inf"), DEST[1]]}))
    print("preview inf ->", r.status_code, r.text[:300])
    assert r.status_code == 422


# ---------------------------------------------------------------- 2. PLANNED / ACCEPTED trip: schedule runs ahead
def test_planned_trip_not_started_still_rechecks_first_road(client):
    """Driver has not accepted (stage PLANNED, no GPS). 3 minutes later the FIRST road (s01) closes."""
    _publish(client)
    r = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "client_ref": "p-1"})
    assert r.status_code == 200, r.text
    tid = r.json()["trip"]["trip_id"]
    drv = {"Authorization": "Bearer " + r.json()["driver_link"].split("t=", 1)[1]}
    client.post("/api/ops/clock", headers=D, json={"mode": "REPLAY", "t_min": 3})
    ev = {"event_id": "E1", "seg_id": "s01", "kind": "closure", "received_t_min": 3, "source_tier": "OPERATOR"}
    rs = client.post("/api/ops/road-events/sync", headers=D, json={"events": [ev]}).json()
    mine = [x for x in rs["reassessed"] if x["trip_id"] == tid]
    trip = client.get(f"/api/ops/trips/{tid}", headers=D).json()["trip"]
    view = client.get("/api/ops/driver/trip", headers=drv).json()
    print("stage:", trip["stage"], "| reassess:", mine)
    print("assessed position:", trip["last_assessment"]["position"])
    print("driver remaining roads:", [s["seg_id"] for leg in view["remaining"] for s in leg["segments"]])
    print("driver first maneuver:", (view["remaining"][0]["maneuvers"] or [{}])[0] if view["remaining"] else None)
    assert mine and mine[0]["decision"] in ("REQUIRED", "ESCALATE"), "closure on the not-yet-driven first road ignored"


def test_planned_trip_after_schedule_end_is_never_reassessed(client):
    _publish(client)
    r = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "client_ref": "p-2"})
    tid = r.json()["trip"]["trip_id"]
    client.post("/api/ops/clock", headers=D, json={"mode": "REPLAY", "t_min": 10})      # planned arrival ~T+4.2
    ev = {"event_id": "E2", "seg_id": "s12", "kind": "closure", "received_t_min": 10, "source_tier": "OPERATOR"}
    rs = client.post("/api/ops/road-events/sync", headers=D, json={"events": [ev]}).json()
    mine = [x for x in rs["reassessed"] if x["trip_id"] == tid]
    print("PLANNED trip at T+10 after s12 closed:", mine)
    assert mine and mine[0]["decision"] != "NO_CHANGE"


# ---------------------------------------------------------------- 3. mission trip double-books a vehicle on a direct trip
def test_mission_trip_refuses_vehicle_already_on_direct_trip(client):
    _publish(client)
    r = client.post("/api/ops/trips/direct", headers=D,
                    json={**BODY, "vehicle_type": None, "vehicle_id": "V1", "client_ref": "d-1"})
    assert r.status_code == 200, r.text
    rt = planner.Router(ROADS, G, forecast(), NO_CTX, ClosureBook([], 0))
    leg = tr.direct_leg(rt, O, DEST, vehicle_type="truck", depart_t_min=0.0)
    a = {"assignment_id": "A1", "status": "ACCEPTED", "vehicle_id": "V1", "team_id": "T1", "legs": [leg]}
    m = client.post("/api/ops/trips", headers=D, json={"assignment": a, "incident_id": "INC-1"})
    trips = client.get("/api/ops/trips", headers=D).json()["trips"]
    active_v1 = [(t["trip_id"], t["kind"], t["stage"]) for t in trips if t.get("vehicle_id") == "V1"
                 and t["stage"] not in ("COMPLETED", "ABORTED")]
    print("mission trip create ->", m.status_code, "| active trips on V1:", active_v1)
    assert len(active_v1) == 1, "V1 is on two active trips"


# ---------------------------------------------------------------- 4. client_ref reuse across incidents / requests
def test_client_ref_reuse_returns_finished_trip_from_other_incident(client):
    _publish(client)
    r = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "client_ref": "same", "incident_id": "INC-a"})
    tid = r.json()["trip"]["trip_id"]
    client.post(f"/api/ops/trips/{tid}/stage", headers=D, json={"stage": "ABORTED", "reason": "test"})
    other = {**BODY, "dest": LONLAT[5].tolist(), "client_ref": "same", "incident_id": "INC-b"}
    r2 = client.post("/api/ops/trips/direct", headers=D, json=other)
    j = r2.json()
    print("second start ->", r2.status_code, j["trip"]["trip_id"], j["trip"]["stage"], j["trip"]["incident_id"],
          "created:", j["created"])
    assert j["trip"]["trip_id"] != tid


# ---------------------------------------------------------------- 5. ARRIVED trip still gets reroute proposals
def test_arrived_trip_gets_no_reroute_proposal(client):
    _publish(client)
    r = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "client_ref": "a-1"})
    tid = r.json()["trip"]["trip_id"]
    drv = {"Authorization": "Bearer " + r.json()["driver_link"].split("t=", 1)[1]}
    for st in ("ACCEPTED", "EN_ROUTE", "ARRIVED"):
        assert client.post("/api/ops/driver/stage", headers=drv, json={"stage": st}).status_code == 200
    client.post("/api/ops/clock", headers=D, json={"mode": "REPLAY", "t_min": 1})
    ev = {"event_id": "E3", "seg_id": "s12", "kind": "closure", "received_t_min": 1, "source_tier": "OPERATOR"}
    rs = client.post("/api/ops/road-events/sync", headers=D, json={"events": [ev]}).json()
    trip = client.get(f"/api/ops/trips/{tid}", headers=D).json()["trip"]
    print("ARRIVED trip reassess:", [x for x in rs["reassessed"] if x["trip_id"] == tid])
    print("proposals:", [(p["proposal_id"], p["kind"], p["status"]) for p in trip["proposals"]])
    assert not trip["proposals"]


# ---------------------------------------------------------------- 6. flood-delay comparison: candidate includes current road
def test_reroute_comparison_counts_current_road_only_in_candidate(tmp_path):
    from datetime import datetime, timezone
    from floodnet.ops.store import OpsStore
    s = OpsStore(tmp_path / "x.sqlite3")
    fc = forecast(s01=[25.0] * len(T))                 # water on the road being driven (truck: limit 60)
    rt = planner.Router(ROADS, G, fc, NO_CTX, ClosureBook([], 0))
    leg = tr.direct_leg(rt, O, DEST, vehicle_type="truck", depart_t_min=0.0)
    trip, _ = tr.create_direct_trip(s, leg, vehicle_type="truck", now_t=0)
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    lon, lat = mid(0, 1)
    fix = {"lon": lon, "lat": lat, "accuracy_m": 5, "recorded_at": now.isoformat(), "source": "DEVICE_GPS"}
    book = ClosureBook(parse_events([{"event_id": "E1", "seg_id": "s12", "kind": "closure", "received_t_min": 0.5,
                                      "source_tier": "OPERATOR"}]), 1.0)
    a = impact.assess(trip, now_t=1.0, now_wall=now, roads=ROADS, G=G, forecast=fc, seg_ctx=NO_CTX, closures=book, fix=fix)
    cur, cand = a["comparison"]["current"], a["comparison"]["candidate"]
    print("decision", a["decision"], "| current:", {k: cur[k] for k in ("eta_t_min", "flood_delay_min", "signature")})
    print("candidate:", {k: cand[k] for k in ("eta_t_min", "flood_delay_min", "signature")},
          "| candidate leg0 roads:", [x["seg_id"] for x in a["candidate"]["legs"][0]["segments"]])
    # the road being driven (s01) is in neither signature, but its flood delay is added only to the candidate
    assert "s01" not in cand["signature"] and cand["flood_delay_min"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------- 7. ETA near the end of the forecast
def test_flood_delay_not_silently_zero_near_forecast_horizon():
    fc = forecast(s01=[20.0] * len(T))                 # truck limit 60: road usable but slowed
    segs = [{"seg_id": "s01", "status": "USABLE"}]
    b = eta.breakdown(ROADS, G, segs, vehicle="truck", dispatch_t_min=117.5, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0))
    early = eta.breakdown(ROADS, G, segs, vehicle="truck", dispatch_t_min=60.0, forecast=fc, seg_ctx=NO_CTX,
                          closures=ClosureBook([], 0))
    print("at T+60:", early["flood_delay_min"], early["segments"], "| at T+117.5:", b["flood_delay_min"], b["segments"],
          "unknown:", b["unknown_depth_segments"])
    assert b["flood_delay_min"] > 0 or b["unknown_depth_segments"] > 0


# ---------------------------------------------------------------- 8. forecast label for a cached run
def test_cached_run_label():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    meta = {"data_mode": "CACHED rainfall: IMD live observation on MIXED pilot data", "_updated_at": "2026-10-11T09:59:00Z"}
    assert eta.forecast_input_label(meta, now)["label"] == "STALE"          # cached input is not current
    meta2 = {"data_mode": "LIVE rainfall: IMD latest nowcast on REAL pilot data", "_updated_at": "2026-10-11T09:59:00Z"}
    assert eta.forecast_input_label(meta2, now)["label"] == "MODELLED"      # "LATEST" is not "TEST"


# ---------------------------------------------------------------- 9. closure on the road being driven
def test_closure_on_the_road_being_driven_is_flagged(tmp_path):
    from datetime import datetime, timezone
    from floodnet.ops.store import OpsStore
    s = OpsStore(tmp_path / "y.sqlite3")
    fc = forecast()
    rt = planner.Router(ROADS, G, fc, NO_CTX, ClosureBook([], 0))
    leg = tr.direct_leg(rt, O, DEST, vehicle_type="car", depart_t_min=0.0)
    trip, _ = tr.create_direct_trip(s, leg, vehicle_type="car", now_t=0)
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    lon, lat = mid(0, 1)                                  # GPS: halfway along s01 (first road)
    fix = {"lon": lon, "lat": lat, "accuracy_m": 5, "recorded_at": now.isoformat(), "source": "DEVICE_GPS"}
    book = ClosureBook(parse_events([{"event_id": "E1", "seg_id": "s01", "kind": "closure", "received_t_min": 0.5,
                                      "source_tier": "OPERATOR"}]), 1.0)
    a = impact.assess(trip, now_t=1.0, now_wall=now, roads=ROADS, G=G, forecast=fc, seg_ctx=NO_CTX, closures=book, fix=fix)
    print("closure on the road being driven ->", a["decision"], "|", a["reason"], "| hazards:", a["hazards_ahead"])
    assert a["decision"] != "NO_CHANGE" or a["hazards_ahead"]


# ---------------------------------------------------------------- 10. vehicle freshness UNKNOWN shown as REGISTRY
def test_imported_vehicle_with_unknown_freshness_is_not_labelled_registry(client):
    _publish(client)
    rows = [{"kind": "vehicle", "name": "Boat van", "type": "truck", "passenger_capacity": 6, "base": "Dadar",
             "lon": 72.8425, "lat": 19.0102}]
    imp = client.post("/api/ops/registry/import", headers=D,
                      json={"label": "ngo list", "format": "json", "text": json.dumps(rows), "commit": True}).json()
    vid = imp["ids"][0]
    res = {r["id"]: r for r in client.get("/api/ops/resources", headers=D).json()["resources"]}
    p = client.post("/api/ops/trips/preview", headers=D, json={**BODY, "vehicle_type": None, "vehicle_id": vid}).json()
    print("registry freshness:", res[vid]["freshness"], "| preview vehicle chip:", p["freshness"]["vehicle"],
          "| warnings:", p["warnings"])
    assert p["freshness"]["vehicle"]["label"] != "REGISTRY" or res[vid]["freshness"] != "UNKNOWN"
