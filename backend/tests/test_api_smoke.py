"""API smoke tests. Endpoints depending on modules from other agents may answer 503; we then assert the message is informative."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from floodnet.api.main import app

client = TestClient(app)


def _skip_if_503(r):
    if r.status_code == 503:
        body = r.json()
        assert body.get("error") == "module_missing"
        assert "floodnet." in body.get("detail", ""), body
        pytest.skip(f"module missing: {body['detail'][:120]}")


def test_meta():
    r = client.get("/api/meta"); _skip_if_503(r)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["data_mode"] in ("REAL", "DEMONSTRATION")
    assert m["pilot"]["grid"]["nx"] > 0 and m["pilot"]["crs"] == "EPSG:32643"
    assert "provenance" in m and any("OpenStreetMap" in a for a in m["attribution"])


def test_static_and_root():
    assert client.get("/").status_code == 200
    assert client.get("/api/health").json()["ok"] is True


def test_topology_terrain_roads_hotspots():
    for path in ("/api/topology", "/api/terrain", "/api/roads", "/api/hotspots"):
        r = client.get(path); _skip_if_503(r)
        assert r.status_code == 200, (path, r.text)
        assert "provenance" in r.json(), path
    t = client.get("/api/terrain").json()
    assert t["png_base64"].startswith("iVBORw0KGgo")  # PNG magic in base64
    assert t["z_max"] >= t["z_min"]
    assert client.get("/api/roads").json()["type"] == "FeatureCollection"


def test_scenarios_nonempty():
    r = client.get("/api/scenarios"); _skip_if_503(r)
    assert r.status_code == 200
    sc = r.json()
    assert len(sc) > 0
    assert {"id", "name", "t_min", "intensity_mm_h", "total_mm", "provenance"} <= set(sc[0])


def _first_scenario_id():
    r = client.get("/api/scenarios"); _skip_if_503(r)
    return r.json()[0]["id"]


def test_simulate_frame_series_nowcast_route():
    sid = _first_scenario_id()
    r = client.post("/api/simulate", json={"scenario_id": sid, "blockage": {"mode": "none"}, "horizon_min": 30})
    _skip_if_503(r)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["scenario_id"] == sid and len(s["frames_t_min"]) >= 2 and s["frames_t_min"][-1] == 30.0
    assert {"max_depth_cm", "peak_surcharging_nodes", "peak_flooded_segments", "total_surcharge_m3"} <= set(s["summary"])
    assert "mass_balance" in s and "provenance" in s
    run_id = s["run_id"]

    f = client.get(f"/api/simulation/{run_id}/frame/30")
    assert f.status_code == 200, f.text
    fj = f.json()
    assert fj["streets"]["type"] == "FeatureCollection"
    if fj["streets"]["features"]:
        props = fj["streets"]["features"][0]["properties"]
        assert {"seg_id", "depth_cm", "severity", "passable_car", "passable_ambulance"} <= set(props)
    assert fj["depth_grid"]["png_base64"].startswith("iVBORw0KGgo")
    assert len(fj["nodes"]) > 0 and "hgl_m" in fj["nodes"][0]

    se = client.get(f"/api/simulation/{run_id}/series").json()
    assert len(se["t_min"]) == len(s["frames_t_min"]) == len(se["max_depth_cm"])

    n = client.get("/api/nowcast")
    assert n.status_code == 200 and n.json()["run_id"] == run_id and n.json()["current_frame_index"] == 0

    assert client.get("/api/simulation/nope/frame/0").status_code == 404

    roads = client.get("/api/roads").json()["features"]
    if roads:
        a = roads[0]["geometry"]["coordinates"][0]; b = roads[-1]["geometry"]["coordinates"][-1]
        rr = client.post("/api/route", json={"origin": a, "dest": b, "t_min": 30, "vehicle": "car", "run_id": run_id})
        _skip_if_503(rr)
        assert rr.status_code == 200, rr.text
        rj = rr.json()
        assert isinstance(rj, dict) and rj["run_id"] == run_id and rj["t_min"] == 30


def test_blockage_and_compare():
    sid = _first_scenario_id()
    r = client.post("/api/simulate", json={"scenario_id": sid, "blockage": {"mode": "fraction", "fraction": 0.8}, "horizon_min": 15})
    _skip_if_503(r)
    assert r.status_code == 200, r.text
    assert r.json()["blockage"]["mode"] == "fraction"
    c = client.post("/api/compare", json={"scenario_id": sid, "blockage": {"mode": "random", "share": 0.5, "fraction": 0.9}, "horizon_min": 15})
    _skip_if_503(c)
    assert c.status_code == 200, c.text
    cj = c.json()
    assert len(cj["normal"]["max_depth_cm"]) == len(cj["blocked"]["max_depth_cm"]) == len(cj["frames_t_min"])


def test_unknown_scenario_404():
    r = client.post("/api/simulate", json={"scenario_id": "does-not-exist"}); _skip_if_503(r)
    assert r.status_code == 404
