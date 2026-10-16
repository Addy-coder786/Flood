"""/api/traffic/* through the real app. Roads come from the small SYNTHETIC test network (tests/ops_net.py); HTTP to
TomTom is faked; the key is a fake set via monkeypatch (never the developer's .env)."""
import json

import pytest
from fastapi.testclient import TestClient

from floodnet.api import traffic_api
from floodnet.api.main import app
from floodnet.traffic import tomtom
from tests.ops_net import ROADS

FAKE_KEY = "fake-tomtom-key-XYZ789"


def flow(current, free):
    return json.dumps({"flowSegmentData": {"currentSpeed": current, "freeFlowSpeed": free, "confidence": 0.8,
                                           "roadClosure": False, "frc": "FRC5"}})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    monkeypatch.setattr(traffic_api, "_roads", lambda: ROADS)
    tomtom.clear_cache()
    yield TestClient(app)
    tomtom.clear_cache()


def test_status_without_key(client):
    r = client.get("/api/traffic/status")
    assert r.status_code == 200
    assert r.json()["configured"] is False and r.json()["mode"] == "UNAVAILABLE"
    assert r.json()["provider"] == "tomtom" and r.json()["cache_ttl_s"] == 60


def test_segments_without_key_is_an_honest_unavailable(client):
    r = client.post("/api/traffic/segments", json={"seg_ids": ["s01", "s12"]})
    assert r.status_code == 200
    assert r.json()["mode"] == "UNAVAILABLE" and "TOMTOM_API_KEY not configured" in r.json()["note"]


def test_unknown_ids_are_a_422_listing_up_to_five(client):
    bad = [f"zz{i}" for i in range(7)]
    r = client.post("/api/traffic/segments", json={"seg_ids": ["s01"] + bad})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert sum(b in detail for b in bad) == 5 and "zz5" not in detail and "zz6" not in detail


def test_more_than_40_ids_or_empty_list_is_422(client):
    assert client.post("/api/traffic/segments", json={"seg_ids": ["s01"] * 41}).status_code == 422
    assert client.post("/api/traffic/segments", json={"seg_ids": []}).status_code == 422


def test_segments_resolve_to_midpoints_and_the_key_never_leaves_the_server(client, monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    calls = []

    def http(url, params, timeout):
        calls.append(dict(params))
        return 200, flow(12, 40)
    monkeypatch.setattr(tomtom, "_httpx_get", http)
    r = client.post("/api/traffic/segments", json={"seg_ids": ["s01", "w03"], "refresh": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "LIVE" and set(body["segments"]) == {"s01", "w03"}
    assert body["segments"]["s01"]["level"] == "heavy" and body["segments"]["s01"]["source"] == "tomtom"
    pts = {c["point"] for c in calls}
    assert pts == {"19.01,72.8425", "19.0125,72.84"}      # midpoint of s01 (0-1) and of w03 (0-3), as lat,lon
    assert FAKE_KEY not in r.text
    # second request within the TTL is served from the cache; refresh=true re-fetches
    client.post("/api/traffic/segments", json={"seg_ids": ["s01"]})
    assert len(calls) == 2
    client.post("/api/traffic/segments", json={"seg_ids": ["s01"], "refresh": True})
    assert len(calls) == 3
