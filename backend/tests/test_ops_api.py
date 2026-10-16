"""Live-operations API through the real app (D-38..D-43): roles, the trip flow from dispatcher to driver and back, and
a closure reported en route turning into a reroute the dispatcher accepts and the driver acknowledges.
The pilot road context is replaced by the small test network (tests/ops_net.py)."""
import pytest
from fastapi.testclient import TestClient

from floodnet.api import ops_api
from floodnet.api.main import app
from floodnet.ops import store as ops_store
from tests.ops_net import G, NO_CTX, ROADS, SEG_IDS, T
from tests.test_ops_trips import assignment

TOKEN = "test-dispatcher-token"
D = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.delenv("VERCEL", raising=False)
    ops_store.reset_store_for_tests()
    monkeypatch.setattr(ops_api, "_ctx", lambda: (ROADS, G, NO_CTX, set(SEG_IDS)))
    yield TestClient(app)
    ops_store.reset_store_for_tests()


def _publish(c, **depths):
    streets = {sid: depths.get(sid, [0.0] * len(T)) for sid in SEG_IDS}
    r = c.post("/api/ops/forecast", headers=D, json={"run_id": "test", "data_mode": "SYNTHETIC TEST",
                                                     "series_t_min": list(T), "streets_cm": streets})
    assert r.status_code == 200, r.text


def _trip(c):
    r = c.post("/api/ops/trips", headers=D, json={"assignment": assignment(), "names": {"shelter": "Test shelter"}})
    assert r.status_code == 200, r.text
    j = r.json()
    return j["trip"]["trip_id"], j["driver_link"].split("t=", 1)[1]


def test_store_unavailable_on_vercel_is_a_503(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    ops_store.reset_store_for_tests()
    assert client.get("/api/ops/status", headers=D).status_code == 503


def test_dispatcher_endpoints_need_the_dispatcher(client):
    assert client.get("/api/ops/status").status_code == 401
    assert client.get("/api/ops/status", headers=D).status_code == 200


def test_driver_token_opens_its_own_trip_only(client):
    _publish(client)
    tid, token = _trip(client)
    drv = {"Authorization": f"Bearer {token}"}
    v = client.get("/api/ops/driver/trip", headers=drv).json()
    assert v["trip_id"] == tid and v["stage"] == "ASSIGNED" and v["shelter"] == "Test shelter"
    assert v["remaining"][0]["maneuvers"][0]["type"] == "depart"
    assert "driver_token_hash" not in str(v) and token not in str(v)
    assert "guarantee" in v["disclaimer"]
    assert client.get("/api/ops/driver/trip", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/ops/trips", headers=drv).status_code == 401              # not a dispatcher
    assert client.post("/api/ops/driver/stage", headers=drv, json={"stage": "ABORTED"}).status_code == 403


def test_closure_reported_en_route_becomes_an_accepted_and_acknowledged_reroute(client):
    _publish(client)
    tid, token = _trip(client)
    drv = {"Authorization": f"Bearer {token}"}
    client.post("/api/ops/clock", headers=D, json={"mode": "REPLAY", "t_min": 1})
    assert client.post("/api/ops/driver/stage", headers=drv, json={"stage": "EN_ROUTE"}).status_code == 200
    r = client.post("/api/ops/driver/report", headers=drv, json={"kind": "blockage", "seg_id": "s12", "note": "tree down"})
    assert r.status_code == 200 and r.json()["road_event_id"].startswith("F")
    trip = client.get(f"/api/ops/trips/{tid}", headers=D).json()["trip"]
    p = trip["proposals"][-1]
    assert p["kind"] == "REQUIRED" and p["status"] == "OPEN" and "s12" not in p["signature"]
    assert p["trigger"]["kind"] == "road_event"
    acc = client.post(f"/api/ops/trips/{tid}/proposals/{p['proposal_id']}/decide", headers=D, json={"decision": "ACCEPT"})
    assert acc.status_code == 200 and acc.json()["trip"]["route_version"] == 2
    v = client.get("/api/ops/driver/trip", headers=drv).json()
    assert v["route_change"]["proposal_id"] == p["proposal_id"]
    v = client.post("/api/ops/driver/ack", headers=drv, json={"proposal_id": p["proposal_id"]}).json()
    assert v["route_change"] is None
    roads_ahead = [s["seg_id"] for leg in v["remaining"] for s in leg["segments"]]
    assert "s12" not in roads_ahead
    actions = [a["action"] for a in client.get(f"/api/ops/trips/{tid}", headers=D).json()["audit"]]
    assert "propose" in actions and "accept_proposal" in actions and "acknowledge_route" in actions


def test_repeated_gps_updates_do_not_create_duplicate_proposals(client):
    _publish(client)
    tid, token = _trip(client)
    drv = {"Authorization": f"Bearer {token}"}
    client.post("/api/ops/road-events/sync", headers=D,
                json={"events": [{"event_id": "C1", "seg_id": "s12", "kind": "closure", "received_t_min": 0}]})
    for _ in range(3):
        assert client.post("/api/ops/driver/position", headers=drv,
                           json={"lon": 72.8405, "lat": 19.0100, "accuracy_m": 5}).status_code == 200
    props = client.get(f"/api/ops/trips/{tid}", headers=D).json()["trip"]["proposals"]
    assert len([p for p in props if p["status"] == "OPEN"]) == 1


def test_sync_rejects_unknown_roads(client):
    r = client.post("/api/ops/road-events/sync", headers=D,
                    json={"events": [{"event_id": "C1", "seg_id": "nope", "kind": "closure", "received_t_min": 0}]})
    assert r.status_code == 422


def test_responder_updates_its_shelter_and_a_full_shelter_escalates_the_trip(client):
    _publish(client)
    tid, _ = _trip(client)
    reg = client.post("/api/ops/responders", headers=D, json={"name": "SH1 desk", "role": "shelter_staff", "scope": ["SH1"]})
    tok = reg.json()["token"]
    assert "token_hash" not in reg.text
    r = client.post("/api/ops/resources/SH1/updates", headers={"Authorization": f"Bearer {tok}"},
                    json={"fields": {"occupancy": 80}})
    assert r.status_code == 200 and r.json()["resource"]["verified"] is True
    assert client.post("/api/ops/resources/SH2/updates", headers={"Authorization": f"Bearer {tok}"},
                       json={"fields": {"occupancy": 1}}).status_code == 403
    client.post(f"/api/ops/trips/{tid}/reassess", headers=D)
    trip = client.get(f"/api/ops/trips/{tid}", headers=D).json()["trip"]
    assert trip["escalation"]["open"] and "full" in trip["escalation"]["reason"]


def test_simulated_position_is_labelled(client):
    _publish(client)
    tid, _ = _trip(client)
    r = client.post(f"/api/ops/trips/{tid}/simulate-position", headers=D)
    assert r.status_code == 200 and r.json()["position"]["source"] == "SIMULATED"
