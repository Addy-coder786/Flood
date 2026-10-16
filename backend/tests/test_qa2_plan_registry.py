"""Independent QA (2026-10-11): planning from the registry hands out registry data, so it needs the dispatcher."""
import pytest
from fastapi.testclient import TestClient

from floodnet.api.main import app
from floodnet.ops import registry as rg
from floodnet.ops import store as ops_store

TOKEN = "test-dispatcher-token"
D = {"X-FloodNet-Token": TOKEN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", TOKEN)
    monkeypatch.delenv("VERCEL", raising=False)
    ops_store.reset_store_for_tests()
    yield TestClient(app, raise_server_exceptions=False)
    ops_store.reset_store_for_tests()


def test_plan_with_registry_needs_dispatcher(client):
    r = client.post("/api/ops/registry/records", headers=D, json={"kind": "shelter", "record": {
        "name": "Private hall", "lonlat": [72.8425, 19.0102], "total_capacity": 40,
        "address": "Flat 4, Example Bldg, Dadar"}})
    assert r.status_code == 200, r.text
    s = ops_store.get_store()
    s.put("trips", "TR9", {"trip_id": "TR9", "kind": "direct", "stage": "EN_ROUTE", "vehicle_id": "V1"},
          actor="test", action="seed")
    body = {"now_t_min": 0, "series_t_min": [0, 60], "streets_cm": {}, "resources_source": "registry"}
    p = client.post("/api/response/plan", json=body)                      # NO token
    print("unauthenticated plan ->", p.status_code)
    if p.status_code == 200:
        st = p.json()["state"]
        print("shelter rows:", [(x.get("name"), x.get("address"), x.get("source")) for x in st["shelters"]][-2:])
        print("V1:", {k: v for k, v in next(x for x in st["vehicles"] if x["id"] == "V1").items()
                      if k in ("available", "unavailable_reason", "lonlat", "source")})
    assert p.status_code in (401, 403)
