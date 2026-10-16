"""Point-to-point rescue trips started from the Roads page (DECISIONS D-49): route preview with an ETA breakdown and
freshness, Start Trip, the PLANNED -> ACCEPTED -> EN_ROUTE -> ARRIVED -> COMPLETED lifecycle, GPS visible to the
dispatcher, closure-triggered rerouting from the vehicle's position and escalation when nothing is feasible.
Written before the direct-trip code existed. Test network: tests/ops_net.py."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from floodnet.access.events import ClosureBook, parse_events
from floodnet.api import ops_api
from floodnet.api.main import app
from floodnet.ops import impact
from floodnet.ops import store as ops_store
from floodnet.ops import trips as tr
from floodnet.ops.store import OpsStore
from floodnet.response import planner
from tests.ops_net import G, LONLAT, NO_CTX, ROADS, SEG_IDS, T, forecast, mid

TOKEN = "test-dispatcher-token"
D = {"X-FloodNet-Token": TOKEN}
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
O, DEST = LONLAT[0].tolist(), LONLAT[2].tolist()


# ------------------------------------------------------------------ module level
@pytest.fixture
def store(tmp_path):
    return OpsStore(tmp_path / "ops.sqlite3")


def _leg(fc=None, vehicle="car"):
    rt = planner.Router(ROADS, G, fc or forecast(), NO_CTX, ClosureBook([], 0))
    return tr.direct_leg(rt, O, DEST, vehicle_type=vehicle, depart_t_min=0.0, to_name="Destination")


def test_direct_leg_is_one_routed_leg_to_the_destination():
    leg = _leg()
    assert leg["kind"] == "to_destination" and leg["to"] == "Destination"
    assert [s["seg_id"] for s in leg["segments"]] == ["s01", "s12"]
    assert leg["route"]["type"] == "LineString" and leg["length_m"] > 1000


def test_direct_leg_with_no_feasible_route_raises_with_the_reason():
    fc = forecast(s12=80.0, m14=80.0, w03=80.0)                      # every way to node 2 flooded for a car
    with pytest.raises(tr.NoFeasibleRoute, match="vehicle limit|block"):
        _leg(fc)


def test_start_direct_trip_lifecycle_and_idempotency(store):
    trip, token = tr.create_direct_trip(store, _leg(), vehicle_type="car", now_t=0, names={"origin": "Base",
                                        "destination": "Destination"}, incident_id="INC-x", client_ref="start-1")
    assert trip["kind"] == "direct" and trip["stage"] == "PLANNED" and token
    assert trip["assignment_id"] is None and trip["incident_id"] == "INC-x"
    again, tok2 = tr.create_direct_trip(store, _leg(), vehicle_type="car", now_t=0, client_ref="start-1",
                                        incident_id="INC-x")
    assert again["trip_id"] == trip["trip_id"] and tok2 is None                 # a double click is one trip
    tid = trip["trip_id"]
    for st in ("ACCEPTED", "EN_ROUTE", "ARRIVED", "COMPLETED"):
        trip = tr.set_stage(store, tid, st, actor=f"driver:{tid}", now_t=1)
    assert [h["stage"] for h in trip["stage_history"]] == ["PLANNED", "ACCEPTED", "EN_ROUTE", "ARRIVED", "COMPLETED"]
    assert tr.ledger_status_for_trip(trip) is None                            # not a planner assignment


def test_direct_and_mission_stage_vocabularies_do_not_mix(store):
    trip, _ = tr.create_direct_trip(store, _leg(), vehicle_type="car", now_t=0)
    with pytest.raises(ValueError, match="stage must be one of"):
        tr.set_stage(store, trip["trip_id"], "LOADED", actor="dispatcher", now_t=1)
    assert tr.stages_for(trip)[-1] == "ABORTED" and "ARRIVED" in tr.stages_for(trip)
    assert "ARRIVED" not in tr.stages_for({"kind": "mission"}) and "ARRIVED" not in tr.stages_for({})


def test_a_vehicle_on_an_active_trip_cannot_start_another(store):
    tr.create_direct_trip(store, _leg(), vehicle_type="truck", now_t=0, vehicle_id="V1")
    with pytest.raises(ValueError, match="already on trip"):
        tr.create_direct_trip(store, _leg(), vehicle_type="truck", now_t=0, vehicle_id="V1")


def test_closure_on_an_active_direct_trip_proposes_a_reroute_from_the_gps_position(store):
    trip, _ = tr.create_direct_trip(store, _leg(), vehicle_type="car", now_t=0)
    tid = trip["trip_id"]
    tr.set_stage(store, tid, "EN_ROUTE", actor=f"driver:{tid}", now_t=0.5)
    lon, lat = mid(0, 1)
    fix = {"lon": lon, "lat": lat, "accuracy_m": 5, "recorded_at": NOW.isoformat()}
    closures = ClosureBook(parse_events([{"event_id": "E1", "seg_id": "s12", "kind": "closure",
                                          "received_t_min": 1.0, "source_tier": "OPERATOR"}]), 1.0)
    a = impact.assess(store.get("trips", tid), now_t=1.0, now_wall=NOW, roads=ROADS, G=G, forecast=forecast(),
                      seg_ctx=NO_CTX, closures=closures, fix=fix)
    assert a["position"]["source"] == "GPS_ON_ROUTE"
    assert a["decision"] == "REQUIRED" and "South Rd: CLOSED" in a["reason"]
    assert [i["seg_id"] for i in a["issues"]] == ["s12"]
    assert "s12" not in a["candidate"]["signature"]
    out = impact.apply(store, tid, a, trigger={"kind": "road_event", "id": "E1"}, now_t=1.0)
    p = out["proposal"]
    assert p["old_route"]["signature"] == ["s12"] or "s12" in p["old_route"]["signature"]
    assert p["comparison"]["candidate"]["eta_with_flood_delay_min"] is not None
    impact.decide(store, tid, p["proposal_id"], "ACCEPT", actor="dispatcher", now_t=1.5)
    t2 = store.get("trips", tid)
    assert t2["route_version"] == 2 and t2["proposals"][0]["status"] == "ACCEPTED"
    assert "s12" not in [s["seg_id"] for s in t2["legs"][-1]["segments"]]
    # found while writing these tests: the same fix, still on the road being driven, must not now read as "off
    # route" and raise a second REQUIRED reroute (a rerouting loop)
    again = impact.assess(t2, now_t=1.6, now_wall=NOW, roads=ROADS, G=G, forecast=forecast(), seg_ctx=NO_CTX,
                          closures=closures, fix=fix)
    assert again["position"]["source"] == "GPS_ON_ROUTE" and again["decision"] == "NO_CHANGE"


# ------------------------------------------------------------------ API
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.delenv("VERCEL", raising=False)
    ops_store.reset_store_for_tests()
    monkeypatch.setattr(ops_api, "_ctx", lambda: (ROADS, G, NO_CTX, set(SEG_IDS)))
    yield TestClient(app)
    ops_store.reset_store_for_tests()


def _publish(c, data_mode="SYNTHETIC rainfall: test", **depths):
    streets = {sid: depths.get(sid, [0.0] * len(T)) for sid in SEG_IDS}
    r = c.post("/api/ops/forecast", headers=D, json={"run_id": "test", "data_mode": data_mode,
                                                     "series_t_min": list(T), "streets_cm": streets})
    assert r.status_code == 200, r.text


BODY = {"origin": O, "dest": DEST, "vehicle_type": "car", "names": {"origin": "Base", "destination": "Hospital"}}


def test_preview_needs_a_published_forecast(client):
    r = client.post("/api/ops/trips/preview", headers=D, json=BODY)
    assert r.status_code == 409 and "forecast" in r.json()["detail"]


def test_preview_shows_route_eta_breakdown_hazards_and_freshness(client):
    _publish(client, s01=[12.0] * len(T))
    r = client.post("/api/ops/trips/preview", headers=D, json=BODY)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["route"]["reachable"] and j["route"]["length_m"] > 1000 and j["route"]["route"]["type"] == "LineString"
    assert {c["key"] for c in j["eta"]["components"]} == {"base", "flood", "traffic"}
    assert j["freshness"]["traffic"]["label"] in ("UNAVAILABLE", "LIVE")      # never UNKNOWN-as-live; no key here
    assert j["freshness"]["forecast"]["label"] == "SIMULATED"
    assert j["vehicle"]["type"] == "car" and j["vehicle"]["limit_cm"] == 30
    assert "guarantee" in j["disclaimer"]
    assert all(h["status"] != "USABLE" for h in j["route"]["hazards"])


def test_preview_with_every_path_blocked_escalates_and_start_is_refused(client):
    _publish(client, s12=[80.0] * len(T), m14=[80.0] * len(T), w03=[80.0] * len(T))
    j = client.post("/api/ops/trips/preview", headers=D, json=BODY).json()
    assert j["route"]["reachable"] is False and j["decision"] == "ESCALATE"
    r = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "client_ref": "x1"})
    assert r.status_code == 409 and "person" in r.json()["detail"]


def test_registry_vehicle_type_and_availability_are_used(client):
    _publish(client)
    j = client.post("/api/ops/trips/preview", headers=D, json={**BODY, "vehicle_type": None, "vehicle_id": "V1"}).json()
    assert j["vehicle"]["id"] == "V1" and j["vehicle"]["type"] == "truck" and j["vehicle"]["limit_cm"] == 60
    assert "SIMULATED" in j["vehicle"]["source"]
    upd = client.post("/api/ops/resources/V1/updates", headers=D,
                      json={"fields": {"status": "OUT_OF_SERVICE", "out_of_service_reason": "MECHANICAL"}})
    assert upd.status_code == 200, upd.text
    r = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "vehicle_type": None, "vehicle_id": "V1"})
    assert r.status_code == 409 and "OUT_OF_SERVICE" in r.json()["detail"]


def test_start_trip_driver_gps_closure_reroute_and_completion(client):
    _publish(client)
    r = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "client_ref": "btn-1", "incident_id": "INC-test1"})
    assert r.status_code == 200, r.text
    j = r.json()
    tid, token = j["trip"]["trip_id"], j["driver_link"].split("t=", 1)[1]
    assert j["trip"]["stage"] == "PLANNED" and j["trip"]["kind"] == "direct" and j["trip"]["ledger_status"] is None
    assert j["trip"]["eta"]["components"]
    dup = client.post("/api/ops/trips/direct", headers=D, json={**BODY, "client_ref": "btn-1", "incident_id": "INC-test1"})
    assert dup.json()["trip"]["trip_id"] == tid and dup.json()["driver_link"] is None
    drv = {"X-FloodNet-Token": token}
    v = client.get("/api/ops/driver/trip", headers=drv).json()
    assert v["next_stages"] == ["ACCEPTED", "EN_ROUTE", "ARRIVED", "COMPLETED"] and v["destination"] == "Hospital"
    for st in ("ACCEPTED", "EN_ROUTE"):
        assert client.post("/api/ops/driver/stage", headers=drv, json={"stage": st}).status_code == 200
    lon, lat = mid(0, 1)
    assert client.post("/api/ops/driver/position", headers=drv, json={"lon": lon, "lat": lat, "accuracy_m": 6}).status_code == 200
    seen = next(t for t in client.get("/api/ops/trips", headers=D).json()["trips"] if t["trip_id"] == tid)
    assert seen["position"]["freshness"] == "FRESH" and seen["position"]["recorded_at"]
    assert seen["remaining"][0]["segments"][0]["seg_id"] in ("s01", "s12")      # the map marks the next road
    client.post("/api/ops/clock", headers=D, json={"mode": "REPLAY", "t_min": 1})
    ev = {"event_id": "E1", "seg_id": "s12", "kind": "closure", "received_t_min": 1, "source_tier": "OPERATOR"}
    rs = client.post("/api/ops/road-events/sync", headers=D, json={"events": [ev]})
    assert rs.status_code == 200 and any(x["trip_id"] == tid and x["decision"] == "REQUIRED" for x in rs.json()["reassessed"])
    trip = client.get(f"/api/ops/trips/{tid}", headers=D).json()["trip"]
    p = trip["proposals"][-1]
    assert p["status"] == "OPEN" and "South Rd: CLOSED" in p["reason"] and p["finding"] == ["s12"]
    acc = client.post(f"/api/ops/trips/{tid}/proposals/{p['proposal_id']}/decide", headers=D, json={"decision": "ACCEPT"})
    assert acc.status_code == 200 and acc.json()["trip"]["route_version"] == 2
    assert client.get("/api/ops/driver/trip", headers=drv).json()["route_change"]["proposal_id"] == p["proposal_id"]
    for st in ("ARRIVED", "COMPLETED"):
        assert client.post("/api/ops/driver/stage", headers=drv, json={"stage": st}).status_code == 200
    done = client.get(f"/api/ops/trips/{tid}", headers=D).json()["trip"]
    assert done["stage"] == "COMPLETED" and len(done["proposals"]) == 1          # history kept


def test_flood_delay_shows_in_the_trip_eta(client):
    _publish(client, s01=[20.0] * len(T), s12=[20.0] * len(T))
    j = client.post("/api/ops/trips/preview", headers=D, json={**BODY, "vehicle_type": "truck"}).json()
    assert j["eta"]["flood_delay_min"] > 0 and j["eta"]["eta_min"] > j["eta"]["free_flow_min"]


# ----------------------------------------------------------------------------- live traffic in the preview (2026-10-11)
def _fake_sample(levels):
    """A tomtom.sample() stand-in: live values for the given seg_ids, nothing else."""
    def sample(points, **kw):
        segs = {}
        for sid, _lon, _lat in points:
            if sid in levels:
                cur, free, lvl = levels[sid]
                segs[sid] = {"current_kmh": cur, "free_flow_kmh": free, "ratio": cur / free, "level": lvl,
                             "confidence": 0.9, "road_closure": False, "sampled_at": "2026-10-11T10:00:00Z",
                             "source": "tomtom", "from_cache": False}
        return {"mode": "LIVE" if segs else "UNAVAILABLE", "provider": "tomtom", "fetched_at": "2026-10-11T10:00:00Z",
                "ttl_s": 60, "failures": len(points) - len(segs), "note": "fake", "segments": segs}
    return sample


def test_preview_without_a_traffic_key_says_live_traffic_unavailable(client, monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    _publish(client)
    j = client.post("/api/ops/trips/preview", headers=D, json=BODY).json()
    assert j["traffic"]["mode"] == "UNAVAILABLE" and "using estimated travel time" in j["traffic"]["note"]
    assert j["freshness"]["traffic"]["label"] == "UNAVAILABLE"
    assert next(c for c in j["eta"]["components"] if c["key"] == "traffic")["label"] == "UNKNOWN"
    assert j["eta"]["traffic_delay_min"] is None


def test_preview_with_live_traffic_shows_levels_delay_and_both_warnings(client, monkeypatch):
    """Heavy traffic AND flood risk on the same road: both are shown; the route is still chosen by flood rules."""
    from floodnet.traffic import tomtom
    monkeypatch.setattr(tomtom, "sample", _fake_sample({"s01": (8.0, 40.0, "heavy"), "s12": (36.0, 40.0, "normal")}))
    _publish(client, s12=[45.0] * len(T))                               # s12 UNCERTAIN for a truck (within 20 cm of 60)
    j = client.post("/api/ops/trips/preview", headers=D, json={**BODY, "vehicle_type": "truck"}).json()
    tr = j["traffic"]
    assert tr["mode"] == "LIVE" and tr["segments"]["s01"]["level"] == "heavy" and tr["sampled"] == 2
    assert j["freshness"]["traffic"]["label"] == "LIVE" and j["freshness"]["traffic"]["fetched_at"]
    comp = next(c for c in j["eta"]["components"] if c["key"] == "traffic")
    assert comp["label"] == "LIVE TRAFFIC" and comp["minutes"] > 0
    assert j["eta"]["eta_min"] > j["eta"]["eta_without_traffic_min"] >= j["eta"]["free_flow_min"]
    assert [h["seg_id"] for h in j["route"]["hazards"]] == ["s12"]           # the flood warning is still there
    assert [s["seg_id"] for s in j["route"]["segments"]] == ["s01", "s12"]  # the flood-safe route is unchanged


def test_live_traffic_never_makes_an_unsafe_road_the_recommended_route(client, monkeypatch):
    """A free-flowing road that the flood rules call AT_RISK is still avoided; the detour is recommended."""
    from floodnet.traffic import tomtom
    monkeypatch.setattr(tomtom, "sample", _fake_sample({"s12": (40.0, 40.0, "normal"), "m14": (5.0, 40.0, "heavy"),
                                                        "n45": (5.0, 40.0, "heavy"), "e25": (5.0, 40.0, "heavy")}))
    _publish(client, s12=[80.0] * len(T))                               # s12 flooded for a car: AT_RISK
    j = client.post("/api/ops/trips/preview", headers=D, json=BODY).json()
    assert "s12" not in [s["seg_id"] for s in j["route"]["segments"]]
    assert j["eta"]["traffic_delay_min"] > 0                           # the slow detour is reported honestly


def test_preview_refresh_traffic_bypasses_the_cache(client, monkeypatch):
    from floodnet.traffic import tomtom
    seen = []
    def sample(points, **kw):
        seen.append(bool(kw.get("refresh")))
        return _fake_sample({})(points)
    monkeypatch.setattr(tomtom, "sample", sample)
    _publish(client)
    client.post("/api/ops/trips/preview", headers=D, json=BODY)
    client.post("/api/ops/trips/preview", headers=D, json={**BODY, "refresh_traffic": True})
    assert seen == [False, True]
