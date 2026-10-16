"""Registry API through the real app (2026-10-11): dispatcher registration and import, the existing responder update
path end to end, responder-only endpoints, the volunteer role, and the candidate-shelter workflow with its audit.
The pilot road context is replaced by the synthetic test network (tests/ops_net.py). Written before
floodnet/api/registry_api.py existed."""
import pytest
from fastapi.testclient import TestClient

from floodnet.api import ops_api
from floodnet.api.main import app
from floodnet.ops import store as ops_store
from tests.ops_net import G, NO_CTX, ROADS, SEG_IDS, T

TOKEN = "test-dispatcher-token"
D = {"X-FloodNet-Token": TOKEN}
IN_PILOT = [72.8425, 19.0102]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.delenv("VERCEL", raising=False)
    ops_store.reset_store_for_tests()
    monkeypatch.setattr(ops_api, "_ctx", lambda: (ROADS, G, NO_CTX, set(SEG_IDS)))
    yield TestClient(app)
    ops_store.reset_store_for_tests()


def H(token):
    return {"X-FloodNet-Token": token}


def responder(c, role, scope, name="Desk"):
    r = c.post("/api/ops/responders", headers=D, json={"name": name, "role": role, "scope": scope})
    assert r.status_code == 200, r.text
    return r.json()["responder"]["responder_id"], r.json()["token"]


def resources(c):
    return {r["id"]: r for r in c.get("/api/ops/resources", headers=D).json()["resources"]}


# ------------------------------------------------------------------ vocabulary and dispatcher entry
def test_vocabulary_is_dispatcher_only_and_lists_the_known_values(client):
    assert client.get("/api/ops/registry/vocabulary").status_code == 401
    v = client.get("/api/ops/registry/vocabulary", headers=D).json()
    assert v["capabilities"] == ["evacuation", "water rescue"]
    assert {t["type"]: t["depth_limit_cm"] for t in v["vehicle_types"]}["truck"] == 60
    assert "pedestrian" not in {t["type"] for t in v["vehicle_types"]}
    assert v["pilot_bbox"] == [72.835, 19.010, 72.855, 19.030] and v["stale_after_min"] == 60.0
    assert "volunteer" in v["roles"] and v["candidate_states"][0] == "SUBMITTED"


def test_dispatcher_registers_records_and_bad_input_is_a_422(client):
    body = {"kind": "shelter", "record": {"name": "Ward hall", "lonlat": IN_PILOT, "total_capacity": 40, "occupancy": 0,
                                          "status": "open"}}
    assert client.post("/api/ops/registry/records", json=body).status_code == 401
    r = client.post("/api/ops/registry/records", headers=D, json=body)
    assert r.status_code == 200, r.text
    rec = r.json()["resource"]
    assert rec["id"] == "SH3" and rec["source"] == "dispatcher entry" and rec["freshness"] == "CURRENT"
    assert resources(client)["SH3"]["total_capacity"] == 40
    bad = client.post("/api/ops/registry/records", headers=D, json={"kind": "vehicle", "record": {
        "name": "Boat", "type": "boat", "passenger_capacity": 4, "base": "x", "lonlat": IN_PILOT}})
    assert bad.status_code == 422 and "type" in bad.json()["detail"]
    assert client.post("/api/ops/registry/records", headers=D, json={"kind": "settlement", "record": {}}).status_code == 422


def test_import_preview_then_atomic_commit(client):
    text = "kind,name,lon,lat,total_capacity\nshelter,Hall X,72.846,19.014,30\nshelter,Hall Y,72.99,19.014,30\n"
    p = client.post("/api/ops/registry/import", headers=D, json={"label": "list.csv", "format": "csv", "text": text})
    assert p.status_code == 200 and p.json()["committed"] is False
    assert p.json()["valid"] == 1 and p.json()["invalid"] == 1 and "pilot area" in p.json()["rows"][1]["errors"][0]
    c = client.post("/api/ops/registry/import", headers=D, json={"label": "list.csv", "format": "csv", "text": text, "commit": True})
    assert c.status_code == 422 and "nothing was imported" in c.json()["detail"]
    assert not any(r["name"] == "Hall X" for r in resources(client).values())
    ok = client.post("/api/ops/registry/import", headers=D, json={"label": "list.csv", "format": "csv",
                                                                   "text": text.replace("72.99", "72.847"), "commit": True})
    assert ok.status_code == 200 and len(ok.json()["ids"]) == 2
    assert resources(client)[ok.json()["ids"][0]]["source"] == "import: list.csv"
    assert client.post("/api/ops/registry/import", headers=D, json={"label": "x", "format": "json", "text": "{"}).status_code == 422


# ------------------------------------------------------------------ responders (existing path, end to end)
def test_shelter_staff_update_through_the_existing_endpoint(client):
    rid, tok = responder(client, "shelter_staff", ["SH1"])
    r = client.post("/api/ops/resources/SH1/updates", headers=H(tok), json={"fields": {"occupancy": 33}})
    assert r.status_code == 200, r.text
    assert r.json()["resource"]["occupancy"] == 33 and r.json()["resource"]["last_update"]["by"] == f"responder:{rid}"
    assert r.json()["resource"]["freshness"] == "CURRENT"
    assert client.post("/api/ops/resources/SH2/updates", headers=H(tok), json={"fields": {"occupancy": 1}}).status_code == 403
    assert client.post("/api/ops/resources/SH1/updates", headers=H(tok), json={"fields": {"total_capacity": 9, "colour": 1}}).status_code == 403
    assert client.get("/api/ops/resources", headers=H(tok)).status_code == 401           # not a dispatcher


def test_responder_only_endpoints_and_revocation(client):
    rid, tok = responder(client, "field_team", ["T1", "V1"], name="Fire team 1 lead")
    me = client.get("/api/ops/registry/responder/me", headers=H(tok))
    assert me.status_code == 200
    assert me.json()["responder"]["role"] == "field_team" and {r["id"] for r in me.json()["resources"]} == {"T1", "V1"}
    assert "token_hash" not in str(me.json()) and me.json()["pilot_bbox"] == [72.835, 19.010, 72.855, 19.030]
    up = client.post("/api/ops/registry/responder/resources/V1/updates", headers=H(tok),
                     json={"fields": {"status": "OUT_OF_SERVICE", "out_of_service_reason": "MECHANICAL"}})
    assert up.status_code == 200 and up.json()["update"]["actor"] == f"responder:{rid}"
    assert client.post("/api/ops/registry/responder/resources/SH1/updates", headers=H(tok),
                       json={"fields": {"occupancy": 1}}).status_code == 403
    assert client.get("/api/ops/registry/responder/me", headers=D).status_code == 401      # the dispatcher token is no responder
    assert client.get("/api/ops/registry/responder/me").status_code == 401
    assert client.post(f"/api/ops/registry/responders/{rid}/revoke", headers=H(tok)).status_code == 401
    assert client.post(f"/api/ops/registry/responders/{rid}/revoke", headers=D).status_code == 200
    assert client.get("/api/ops/registry/responder/me", headers=H(tok)).status_code == 401
    assert client.post("/api/ops/resources/V1/updates", headers=H(tok), json={"fields": {"status": "AVAILABLE"}}).status_code == 401
    assert client.post("/api/ops/registry/responders/RP99/revoke", headers=D).status_code == 404


# ------------------------------------------------------------------ volunteers and candidates
CAND = {"name": "Temple hall", "lonlat": IN_PILOT, "address": "Lane 4", "description": "large hall", "capacity_estimate": 50}


def test_volunteer_token_can_only_submit_candidates(client):
    vid, vtok = responder(client, "volunteer", [], name="Volunteer Asha")
    r = client.post("/api/ops/registry/candidates", headers=H(vtok), json=CAND)
    assert r.status_code == 200, r.text
    c = r.json()["candidate"]
    assert c["state"] == "SUBMITTED" and c["submitted_by"] == f"volunteer Volunteer Asha ({vid})"
    mine = client.get("/api/ops/registry/responder/me", headers=H(vtok)).json()
    assert mine["resources"] == [] and [x["candidate_id"] for x in mine["candidates"]] == [c["candidate_id"]]
    assert client.get("/api/ops/registry/candidates", headers=H(vtok)).status_code == 401
    assert client.post("/api/ops/resources/SH1/updates", headers=H(vtok), json={"fields": {"occupancy": 1}}).status_code == 403
    assert client.post(f"/api/ops/registry/candidates/{c['candidate_id']}/reject", headers=H(vtok),
                       json={"reason": "x"}).status_code == 401
    _, ftok = responder(client, "field_team", ["T1"])
    assert client.post("/api/ops/registry/candidates", headers=H(ftok), json=CAND).status_code == 403
    assert client.post("/api/ops/registry/candidates", json=CAND).status_code == 401           # no anonymous submissions
    assert client.post("/api/ops/registry/candidates", headers=H(vtok), json={**CAND, "lonlat": [73.5, 19.5]}).status_code == 422


def test_dispatcher_submits_on_behalf_of_a_volunteer(client):
    assert client.post("/api/ops/registry/candidates", headers=D, json=CAND).status_code == 422          # on whose behalf?
    r = client.post("/api/ops/registry/candidates", headers=D, json={**CAND, "on_behalf_of": "volunteer Ravi (phoned in)"})
    assert r.status_code == 200
    assert r.json()["candidate"]["submitted_by"] == "dispatcher on behalf of volunteer Ravi (phoned in)"


def _publish(c, **depths):
    streets = {sid: depths.get(sid, [0.0] * len(T)) for sid in SEG_IDS}
    r = c.post("/api/ops/forecast", headers=D, json={"run_id": "test", "data_mode": "SYNTHETIC TEST",
                                                     "series_t_min": list(T), "streets_cm": streets})
    assert r.status_code == 200, r.text


def test_candidate_workflow_through_the_api_with_the_published_forecast(client):
    _, vtok = responder(client, "volunteer", [])
    cid = client.post("/api/ops/registry/candidates", headers=H(vtok), json=CAND).json()["candidate"]["candidate_id"]
    pre = client.get(f"/api/ops/registry/candidates/{cid}/site-check", headers=D).json()
    assert pre["status"] == "UNKNOWN"                                   # nothing published yet
    _publish(client, s01=[20.0] * len(T))
    pre = client.get(f"/api/ops/registry/candidates/{cid}/site-check", headers=D).json()
    assert pre["status"] == "MODELLED" and pre["max_depth_cm"] == 20.0 and pre["seg_id"] == "s01"
    early = client.post(f"/api/ops/registry/candidates/{cid}/confirm", headers=D, json={})
    assert early.status_code == 422
    p = client.post(f"/api/ops/registry/candidates/{cid}/permission", headers=D, json={
        "granted_by_role": "temple trustee", "method": "PHONE", "granted_at": "2026-10-11T00:00:00Z"})
    assert p.status_code == 200, p.text
    s = client.post(f"/api/ops/registry/candidates/{cid}/suitability", headers=D, json={
        "access": "YES", "water_sanitation": "NO", "accessibility": "YES", "capacity_estimate": 45})
    assert s.status_code == 200, s.text
    assert s.json()["candidate"]["suitability"]["modelled_flood_depth"]["max_depth_cm"] == 20.0     # computed by the server
    before = set(resources(client))
    assert not any(r["name"] == "Temple hall" for r in resources(client).values())
    done = client.post(f"/api/ops/registry/candidates/{cid}/confirm", headers=D, json={"total_capacity": 40})
    assert done.status_code == 200, done.text
    sid = done.json()["candidate"]["shelter_id"]
    assert sid not in before and resources(client)[sid]["source"].startswith("volunteer-submitted, verified by dispatcher")
    lst = client.get("/api/ops/registry/candidates", headers=D).json()["candidates"]
    assert [x["state"] for x in lst] == ["CONFIRMED"]
    audit = client.get("/api/ops/audit", headers=D, params={"collection": "shelter_candidates", "id": cid}).json()["audit"]
    assert [a["action"] for a in audit] == ["candidate_submit", "candidate_permission", "candidate_suitability", "candidate_confirm"]
    assert client.post("/api/ops/registry/candidates/CS99/reject", headers=D, json={"reason": "x"}).status_code == 404
