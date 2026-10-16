"""Independent QA (2026-10-11) reproductions for the registry (D-50), copied from the reviewer's scratch tests."""
import json

import pytest
from fastapi.testclient import TestClient

from floodnet.api import ops_api
from floodnet.api.main import app
from floodnet.ops import registry as rg
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
    yield TestClient(app, raise_server_exceptions=False)
    ops_store.reset_store_for_tests()


def H(t):
    return {"X-FloodNet-Token": t}


def responder(c, role, scope, name="Desk"):
    r = c.post("/api/ops/responders", headers=D, json={"name": name, "role": role, "scope": scope})
    assert r.status_code == 200, r.text
    return r.json()["responder"]["responder_id"], r.json()["token"]


def test_responder_update_with_huge_number_is_422(client):
    sh = client.get("/api/ops/resources", headers=D).json()["resources"]
    sid = next(r["id"] for r in sh if r["kind"] == "shelter")
    _, tok = responder(client, "shelter_staff", [sid])
    r = client.post(f"/api/ops/registry/responder/resources/{sid}/updates",
                    headers={**H(tok), "content-type": "application/json"}, content='{"fields": {"occupancy": 1e400}}')
    print("shelter_staff occupancy=1e400 ->", r.status_code, r.text[:200])
    assert r.status_code == 422


def test_import_with_huge_number_is_a_row_error(client):
    text = json.dumps([{"kind": "shelter", "name": "Big hall", "lon": IN_PILOT[0], "lat": IN_PILOT[1]}])
    text = text.replace('"lat"', '"total_capacity": 1e400, "lat"')
    r = client.post("/api/ops/registry/import", headers=D, json={"label": "x", "format": "json", "text": text})
    print("import dry run with 1e400 ->", r.status_code, r.text[:200])
    assert r.status_code in (200, 422)


@pytest.mark.parametrize("name", ["Hall (982) 001-2345", "Hall 98200.12345", "Hall +91 98200 12345"])
def test_candidate_phone_filter(client, name):
    _, tok = responder(client, "volunteer", [])
    r = client.post("/api/ops/registry/candidates", headers=H(tok), json={"name": name, "lonlat": IN_PILOT})
    print(repr(name), "->", r.status_code)
    assert r.status_code == 422


def test_registered_organisation_can_carry_a_phone_number(client):
    r = client.post("/api/ops/registry/records", headers=D, json={"kind": "team", "record": {
        "name": "Ward team", "capabilities": ["evacuation"], "members": 4, "base": "Dadar",
        "lonlat": IN_PILOT, "organisation": "call 98200 12345"}})
    print("organisation with a number ->", r.status_code)
    assert r.status_code == 422


def test_volunteer_cannot_update_resources_or_read_dispatcher_data(client):
    _, tok = responder(client, "volunteer", [])
    a = client.post("/api/ops/resources/V1/updates", headers=H(tok), json={"fields": {"status": "AVAILABLE"}})
    b = client.post("/api/ops/registry/responder/resources/V1/updates", headers=H(tok), json={"fields": {"status": "AVAILABLE"}})
    c = client.get("/api/ops/registry/candidates", headers=H(tok))
    d = client.get("/api/ops/trips", headers=H(tok))
    print("volunteer:", a.status_code, b.status_code, c.status_code, d.status_code)
    assert (a.status_code, b.status_code, c.status_code, d.status_code) == (403, 403, 401, 401)


def test_field_team_cannot_touch_unscoped_vehicle(client):
    _, tok = responder(client, "field_team", ["T1"])
    a = client.post("/api/ops/resources/V1/updates", headers=H(tok), json={"fields": {"status": "AVAILABLE"}})
    print("field_team unscoped V1 ->", a.status_code)
    assert a.status_code == 403


def test_candidate_never_in_planner_before_confirm(client, tmp_path):
    _, tok = responder(client, "volunteer", [])
    r = client.post("/api/ops/registry/candidates", headers=H(tok), json={"name": "Temple hall", "lonlat": IN_PILOT})
    assert r.status_code == 200, r.text
    s = ops_store.get_store()
    pr = rg.planner_resources(s)
    assert not any(x.get("name") == "Temple hall" for x in pr["shelters"])
    assert not any(x.get("name") == "Temple hall" for x in rg.list_resources(s))


def test_import_commit_is_all_or_nothing_on_midway_failure(client, monkeypatch):
    rows = [{"kind": "shelter", "name": f"Hall {i}", "lon": IN_PILOT[0], "lat": IN_PILOT[1], "total_capacity": 10}
            for i in range(3)]
    real = rg._insert
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full (simulated)")
        return real(*a, **k)
    monkeypatch.setattr(rg, "_insert", boom)
    r = client.post("/api/ops/registry/import", headers=D,
                    json={"label": "x", "format": "json", "text": json.dumps(rows), "commit": True})
    names = [x["name"] for x in client.get("/api/ops/resources", headers=D).json()["resources"]]
    print("midway failure ->", r.status_code, "| Hall rows persisted:", [n for n in names if n.startswith("Hall ")])
    assert not [n for n in names if n.startswith("Hall ")]
