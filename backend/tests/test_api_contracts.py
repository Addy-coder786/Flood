"""Tests for the newer API surface: /api/status, /api/provenance, GET /api/simulate/{run_id}, the
road-segment /explain endpoint, and error-handling regressions (clean 4xx, no raw tracebacks).

Follows the pattern in test_api_smoke.py: endpoints depending on modules from other agents may answer 503;
we then assert the message is informative and skip.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from floodnet.api.main import app
from floodnet.config import DATA_PROCESSED
from floodnet.data.load import REQUIRED

client = TestClient(app)

REAL_PILOT_BUILT = all((DATA_PROCESSED / f).exists() for f in REQUIRED)


def _skip_if_503(r):
    if r.status_code == 503:
        body = r.json()
        assert body.get("error") == "module_missing"
        assert "floodnet." in body.get("detail", ""), body
        pytest.skip(f"module missing: {body['detail'][:120]}")


def _first_scenario_id():
    r = client.get("/api/scenarios"); _skip_if_503(r)
    return r.json()[0]["id"]


# ----------------------------------------------------------------------------- /api/status, /api/provenance
def test_status():
    # /api/status itself never touches the pilot, so force it to load first (mirroring /api/health, which
    # has the same lazy-load characteristic) -- otherwise data_mode can still read "UNLOADED" if this is
    # the first request the process has served.
    m = client.get("/api/meta"); _skip_if_503(m)
    r = client.get("/api/status"); _skip_if_503(r)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["data_mode"] in ("REAL", "DEMONSTRATION")
    assert isinstance(body["runs"], list)
    providers = body["rainfall_providers"]
    assert len(providers) >= 1
    for entry in providers:
        assert {"id", "source_type", "data_mode"} <= set(entry)
        # "radar_nowcast" = the IMD radar integration boundary, always available=False (see D-14);
        # "external_nowcast" (the deprecated inert stub) is never listed at all.
        assert entry["source_type"] in ("scenario", "historical_replay", "live_observation",
                                        "ecmwf_forecast", "radar_nowcast", "radar_image_derived")
        # the experimental SRI-image source is an ESTIMATE, never a nowcast and never REAL
        if entry["source_type"] == "radar_image_derived":
            assert entry["data_mode"] == "ESTIMATED" and "nowcast" not in entry["source_name"].lower()
        if entry["source_type"] == "radar_nowcast":
            assert entry["available"] is False and entry["reason"].startswith("ACCESS PENDING")


def test_provenance():
    r = client.get("/api/provenance"); _skip_if_503(r)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"terrain", "impervious", "buildings", "network", "roads", "data_mode", "attribution"} <= set(body)
    assert isinstance(body["attribution"], list) and len(body["attribution"]) > 0


# ----------------------------------------------------------------------------- GET /api/simulate/{run_id}
def test_get_simulate_valid_and_404():
    sid = _first_scenario_id()
    r = client.post("/api/simulate", json={"scenario_id": sid, "horizon_min": 15})
    _skip_if_503(r)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    r2 = client.get(f"/api/simulate/{run_id}")
    assert r2.status_code == 200, r2.text
    assert r2.json()["run_id"] == run_id
    assert r2.json()["scenario_id"] == sid

    r3 = client.get("/api/simulate/does-not-exist")
    assert r3.status_code == 404
    assert "Traceback" not in r3.text


# ----------------------------------------------------------------------------- /api/scenarios additive "source"
def test_scenarios_keeps_existing_keys_and_adds_source():
    r = client.get("/api/scenarios"); _skip_if_503(r)
    assert r.status_code == 200
    sc = r.json()
    assert len(sc) > 0
    for s in sc:
        assert {"id", "name", "description", "t_min", "intensity_mm_h", "total_mm", "provenance", "source"} <= set(s)
    ids = {s["id"] for s in sc}
    for s in sc:
        if s["id"] in ("moderate", "heavy", "cloudburst"):
            assert s["source"] is not None
            assert s["source"]["source_type"] == "scenario"
            assert s["source"]["data_mode"] == "SYNTHETIC"
        if s["id"] == "july2005":
            assert s["source"]["source_type"] == "historical_replay"
            assert s["source"]["data_mode"] == "REAL"


# ----------------------------------------------------------------------------- explain endpoint
def _run_scenario(scenario_id: str, blockage=None, horizon_min: int = 60):
    r = client.post("/api/simulate", json={"scenario_id": scenario_id,
                                            "blockage": blockage or {"mode": "none"},
                                            "horizon_min": horizon_min})
    _skip_if_503(r)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def test_explain_valid_segment():
    sid = _first_scenario_id()
    run_id = _run_scenario(sid)
    rr = client.get("/api/roads"); _skip_if_503(rr)
    feats = rr.json()["features"]
    if not feats:
        pytest.skip("no road segments in pilot bundle")
    seg_id = feats[0]["properties"]["seg_id"]

    r = client.get(f"/api/simulation/{run_id}/explain/{seg_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    expected_keys = {"run_id", "seg_id", "seg_name", "t_min", "depth_cm", "severity", "passable_car",
                      "passable_ambulance", "rainfall_mm_h", "nearest_drainage_node", "dominant_cause",
                      "terrain_context", "provenance"}
    assert expected_keys <= set(body)
    assert body["run_id"] == run_id
    assert body["seg_id"] == seg_id
    assert body["seg_name"] is not None
    assert body["t_min"] is not None
    assert body["depth_cm"] is not None
    assert body["severity"] in ("clear", "minor", "moderate", "severe", "critical")
    assert isinstance(body["passable_car"], bool)
    assert isinstance(body["passable_ambulance"], bool)
    assert body["rainfall_mm_h"] is not None
    node = body["nearest_drainage_node"]
    assert {"id", "distance_m", "surcharging", "cause", "utilization"} <= set(node)
    assert node["id"] is not None and node["distance_m"] is not None
    assert body["dominant_cause"] in ("drainage_overcapacity", "drainage_blockage",
                                       "drainage_downstream_backup", "surface_ponding_only", "unknown")
    tc = body["terrain_context"]
    assert "ground_elevation_m" in tc and "note" in tc
    assert isinstance(body["provenance"], dict) and {"roads", "network", "terrain"} <= set(body["provenance"])


def test_explain_unknown_seg_id_404():
    sid = _first_scenario_id()
    run_id = _run_scenario(sid)
    r = client.get(f"/api/simulation/{run_id}/explain/not-a-real-segment-id")
    assert r.status_code == 404
    assert "Traceback" not in r.text


def test_explain_unknown_run_id_404():
    roads = client.get("/api/roads").json()
    feats = roads.get("features") or []
    seg_id = feats[0]["properties"]["seg_id"] if feats else "whatever"
    r = client.get(f"/api/simulation/does-not-exist/explain/{seg_id}")
    assert r.status_code == 404
    assert "Traceback" not in r.text


def test_explain_negative_t_min_422():
    sid = _first_scenario_id()
    run_id = _run_scenario(sid)
    roads = client.get("/api/roads").json()["features"]
    if not roads:
        pytest.skip("no road segments in pilot bundle")
    seg_id = roads[0]["properties"]["seg_id"]
    r = client.get(f"/api/simulation/{run_id}/explain/{seg_id}", params={"t_min": -5})
    assert r.status_code == 422
    assert "Traceback" not in r.text


@pytest.mark.skipif(not REAL_PILOT_BUILT, reason="processed pilot data not built (run floodnet.data.build_pilot)")
def test_explain_dry_vs_flooded_segment_differ_sensibly():
    """Under a real pilot + the 'heavy' scenario with a severe forced blockage, a segment near a
    surcharging node should show a different (or at least not-less-severe) picture than the driest segment."""
    run_id = _run_scenario("heavy", blockage={"mode": "fraction", "fraction": 0.9}, horizon_min=180)
    series = client.get(f"/api/simulation/{run_id}/series").json()
    streets = series["streets"]
    if not streets:
        pytest.skip("no street depth series produced (street aggregation unavailable)")
    wettest_id = max(streets, key=lambda sid: max(streets[sid]))
    driest_id = min(streets, key=lambda sid: max(streets[sid]))

    wet = client.get(f"/api/simulation/{run_id}/explain/{wettest_id}").json()
    dry = client.get(f"/api/simulation/{run_id}/explain/{driest_id}").json()
    assert wet["depth_cm"] >= dry["depth_cm"]
    if max(streets[wettest_id]) > max(streets[driest_id]):
        assert wet["dominant_cause"] != "unknown" or wet["depth_cm"] > 0


# ----------------------------------------------------------------------------- invalid-request regressions
def test_unknown_scenario_id_404_regression():
    r = client.post("/api/simulate", json={"scenario_id": "not-a-real-scenario"})
    _skip_if_503(r)
    assert r.status_code == 404
    assert "Traceback" not in r.text


def test_malformed_simulate_body_422():
    r = client.post("/api/simulate", json={})  # missing required scenario_id
    assert r.status_code == 422
    assert "Traceback" not in r.text


def test_bad_vehicle_route_422():
    rr = client.get("/api/roads"); _skip_if_503(rr)
    feats = rr.json().get("features") or []
    if not feats:
        pytest.skip("no road segments in pilot bundle")
    a = feats[0]["geometry"]["coordinates"][0]
    b = feats[-1]["geometry"]["coordinates"][-1]
    r = client.post("/api/route", json={"origin": a, "dest": b, "vehicle": "not-a-real-vehicle"})
    _skip_if_503(r)
    assert r.status_code == 422, r.text
    assert "Traceback" not in r.text


def test_out_of_range_coordinates_route_422():
    r = client.post("/api/route", json={"origin": [999.0, 999.0], "dest": [72.84, 19.02], "vehicle": "car"})
    assert r.status_code == 422
    assert "Traceback" not in r.text
    r2 = client.post("/api/route", json={"origin": [72.84, 19.02], "dest": [-500.0, 12.0], "vehicle": "car"})
    assert r2.status_code == 422
    assert "Traceback" not in r2.text


def test_negative_t_min_frame_422():
    r = client.get("/api/simulation/does-not-exist/frame/-5")
    # negative t_min is checked before the run-id lookup, so this is 422 not 404
    assert r.status_code == 422
    assert "Traceback" not in r.text


def test_unknown_run_id_series_404():
    r = client.get("/api/simulation/does-not-exist/series")
    assert r.status_code == 404
    assert "Traceback" not in r.text


# ---------------------------------------------------------------------- scenario_id="live" (IMD)
# No real network call in these -- IMDObservationProvider.get is monkeypatched. Real-credential behaviour is
# covered separately by the opt-in tests/test_imd_live_smoke.py (skipped without a real IMD_API_KEY).

def test_simulate_live_without_credentials_is_503_not_fake_data(monkeypatch):
    monkeypatch.delenv("IMD_API_KEY", raising=False)
    from floodnet.rainfall.provider import _imd_live_provider
    _imd_live_provider.clear_cache()
    r = client.post("/api/simulate", json={"scenario_id": "live", "blockage": {"mode": "none"}, "horizon_min": 60})
    assert r.status_code == 503
    assert "Traceback" not in r.text
    assert "IMD_API_KEY" in r.json().get("detail", "")


def test_simulate_live_with_mocked_provider_runs_the_real_engine(monkeypatch):
    if not REAL_PILOT_BUILT:
        pytest.skip("real pilot data not built")
    import numpy as np
    from floodnet.config import HORIZON_S, RAIN_DT_S
    from floodnet.contracts import RainfallScenario
    from floodnet.provenance import Provenance, Tag
    from floodnet.rainfall.provider import IMDObservationProvider, RainfallSourceMeta

    t_s = np.arange(0, HORIZON_S + 1, RAIN_DT_S, dtype=float)
    fake_scen = RainfallScenario(id="live", name="Live (IMD-observed, TEST)", t_s=t_s,
                                 intensity_mm_h=np.full_like(t_s, 3.0),
                                 provenance=Provenance(Tag.ESTIMATED, "test", "72.0 mm/24h persistence, mocked"))
    fake_detail = {"source": "IMD", "station": "Mumbai-Santacruz", "station_id": "43003",
                   "retrieved_at": "2026-09-09T00:00:00+00:00", "observed_at": "2026-09-09 00:00:00",
                   "observation_type": "24h cumulative rainfall (observed)", "observed_rainfall_mm": 72.0,
                   "persistence_intensity_mm_h": 3.0, "forecast_extension_label": "3-HOUR PERSISTENCE ESTIMATE",
                   "forecast_extension_note": "Not an official IMD nowcast or forecast -- a persistence assumption."}
    fake_meta = RainfallSourceMeta(source_type="live_observation", source_name="IMD Mumbai-Santacruz", timestamp="2026-09-09T00:00:00+00:00",
                                   forecast_horizon_min=HORIZON_S // 60, resolution_min=1440, data_mode="ESTIMATED",
                                   provenance=fake_scen.provenance, detail=fake_detail)
    monkeypatch.setattr(IMDObservationProvider, "get", lambda self, scenario_id=None, **kw: (fake_scen, fake_meta))

    r = client.post("/api/simulate", json={"scenario_id": "live", "blockage": {"mode": "none"}, "horizon_min": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario_id"] == "live"
    assert body["provenance"]["rainfall"]["tag"] == "ESTIMATED"
    assert "persistence" in body["provenance"]["rainfall"]["note"] or "mocked" in body["provenance"]["rainfall"]["note"]
    # a real run happened: frames + a genuinely closing mass balance, not a stub response
    assert body["n_frames"] >= 2
    assert abs(body["mass_balance"]["error_pct"]) < 0.1

    # structured live detail (Task 4: SOURCE/MODE/STATION/RETRIEVED/RAINFALL/FORECAST EXTENSION) reaches the
    # actual HTTP response, unmodified, alongside (not replacing) the existing provenance.rainfall key.
    rs = body["provenance"]["rainfall_source"]
    assert rs["source_type"] == "live_observation"
    assert rs["detail"]["station"] == "Mumbai-Santacruz"
    assert rs["detail"]["observed_rainfall_mm"] == 72.0
    assert rs["detail"]["forecast_extension_label"] == "3-HOUR PERSISTENCE ESTIMATE"

    r2 = client.get(f"/api/simulate/{body['run_id']}")
    assert r2.status_code == 200 and r2.json()["run_id"] == body["run_id"]


def test_simulate_live_failure_message_never_contains_a_key(monkeypatch):
    """End-to-end (not just the provider unit test): a failing live call must still reach the HTTP client as
    a clean 503 with no key/secret in the body, exercising the real api/main.py error-handling path."""
    from floodnet.rainfall.provider import IMDObservationProvider, ProviderUnavailable

    def _boom(self, scenario_id=None, **kw):
        raise ProviderUnavailable("IMD current_wx request failed (station 43003): HTTP 401")

    monkeypatch.setattr(IMDObservationProvider, "get", _boom)
    r = client.post("/api/simulate", json={"scenario_id": "live", "blockage": {"mode": "none"}, "horizon_min": 30})
    assert r.status_code == 503
    assert "Traceback" not in r.text
    assert "SECRET" not in r.text and "apikey=" not in r.text.lower()


def test_simulate_static_scenarios_unaffected_by_live_wiring():
    """Regression: adding scenario_id='live' support must not change behaviour for the existing modes."""
    for sid in ("heavy", "july2005"):
        r = client.post("/api/simulate", json={"scenario_id": sid, "blockage": {"mode": "none"}, "horizon_min": 15})
        _skip_if_503(r)
        assert r.status_code == 200, r.text
        assert r.json()["scenario_id"] == sid
        assert "rainfall_source" not in r.json()["provenance"]  # only the live path adds this key


def test_status_reports_live_provider_configured_state(monkeypatch):
    monkeypatch.delenv("IMD_API_KEY", raising=False)
    from floodnet.rainfall.provider import _imd_live_provider
    _imd_live_provider.clear_cache()
    r = client.get("/api/status")
    assert r.status_code == 200
    providers = {p["id"]: p for p in r.json()["rainfall_providers"]}
    assert "live" in providers
    assert providers["live"]["available"] is False
    assert "IMD_API_KEY" in providers["live"].get("reason", "")
    assert "external_nowcast" not in providers


# ----------------------------------------------------------------------------- scenario_id='ecmwf' (Open-Meteo NWP)
def test_simulate_ecmwf_with_mocked_provider_runs_the_real_engine(monkeypatch):
    """Mirrors test_simulate_live_with_mocked_provider_runs_the_real_engine exactly, for the ECMWF path --
    proves the normalized ECMWF forecast reaches the SAME unmodified physics engine as every other source."""
    if not REAL_PILOT_BUILT:
        pytest.skip("real pilot data not built")
    import numpy as np
    from floodnet.config import HORIZON_S, RAIN_DT_S
    from floodnet.contracts import RainfallScenario
    from floodnet.provenance import Provenance, Tag
    from floodnet.rainfall.provider import ECMWFForecastProvider, RainfallSourceMeta

    t_s = np.arange(0, HORIZON_S + 1, RAIN_DT_S, dtype=float)
    fake_scen = RainfallScenario(id="ecmwf", name="ECMWF NWP forecast (Open-Meteo), TEST", t_s=t_s,
                                 intensity_mm_h=np.full_like(t_s, 5.0),
                                 provenance=Provenance(Tag.NWP, "test", "mocked ECMWF forecast, 5 mm/h for 3h"))
    fake_detail = {"source": "Open-Meteo", "model": "ECMWF", "data_type": "Numerical Weather Prediction",
                   "classification": "FORECAST", "retrieved_at": "2026-09-09T00:00:00+00:00",
                   "forecast_timestamps": ["2026-09-09T01:00", "2026-09-09T02:00", "2026-09-09T03:00"],
                   "precipitation_mm": [5.0, 5.0, 5.0], "location_lonlat": [72.845, 19.02],
                   "location_source": "Hindmata/Dadar pilot bbox centroid (config.PILOT_BBOX_LONLAT)",
                   "endpoint": "https://api.open-meteo.com/v1/ecmwf"}
    fake_meta = RainfallSourceMeta(source_type="ecmwf_forecast", source_name="ECMWF NWP (Open-Meteo)",
                                   timestamp="2026-09-09T00:00:00+00:00", forecast_horizon_min=HORIZON_S // 60,
                                   resolution_min=60, data_mode="NWP", provenance=fake_scen.provenance, detail=fake_detail)
    monkeypatch.setattr(ECMWFForecastProvider, "get", lambda self, scenario_id=None, **kw: (fake_scen, fake_meta))

    r = client.post("/api/simulate", json={"scenario_id": "ecmwf", "blockage": {"mode": "none"}, "horizon_min": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario_id"] == "ecmwf"
    assert body["provenance"]["rainfall"]["tag"] == "NWP"
    assert "mocked" in body["provenance"]["rainfall"]["note"] or "forecast" in body["provenance"]["rainfall"]["note"].lower()
    # a real run happened: frames + a genuinely closing mass balance, not a stub response
    assert body["n_frames"] >= 2
    assert abs(body["mass_balance"]["error_pct"]) < 0.1

    rs = body["provenance"]["rainfall_source"]
    assert rs["source_type"] == "ecmwf_forecast"
    assert rs["detail"]["source"] == "Open-Meteo"
    assert rs["detail"]["model"] == "ECMWF"
    assert rs["detail"]["classification"] == "FORECAST"

    r2 = client.get(f"/api/simulate/{body['run_id']}")
    assert r2.status_code == 200 and r2.json()["run_id"] == body["run_id"]


def test_simulate_ecmwf_unavailable_is_503_not_fake_data(monkeypatch):
    """A failed ECMWF fetch must never silently fall back to synthetic rainfall while still labelled ecmwf."""
    from floodnet.rainfall.provider import ECMWFForecastProvider, ProviderUnavailable

    def _boom(self, scenario_id=None, **kw):
        raise ProviderUnavailable("Open-Meteo ECMWF request failed: ConnectError")

    monkeypatch.setattr(ECMWFForecastProvider, "get", _boom)
    r = client.post("/api/simulate", json={"scenario_id": "ecmwf", "blockage": {"mode": "none"}, "horizon_min": 30})
    assert r.status_code == 503
    assert "Traceback" not in r.text
    assert "Open-Meteo" in r.json().get("detail", "")


def test_simulate_static_and_live_scenarios_unaffected_by_ecmwf_wiring():
    """Regression: adding scenario_id='ecmwf' support must not change behaviour for the existing modes."""
    for sid in ("heavy", "july2005"):
        r = client.post("/api/simulate", json={"scenario_id": sid, "blockage": {"mode": "none"}, "horizon_min": 15})
        _skip_if_503(r)
        assert r.status_code == 200, r.text
        assert r.json()["scenario_id"] == sid
        assert "rainfall_source" not in r.json()["provenance"]  # only the live/ecmwf paths add this key


def test_status_reports_ecmwf_provider_needs_no_credentials(monkeypatch):
    from floodnet.rainfall.provider import _ecmwf_provider
    _ecmwf_provider.clear_cache()
    r = client.get("/api/status")
    assert r.status_code == 200
    providers = {p["id"]: p for p in r.json()["rainfall_providers"]}
    assert "ecmwf" in providers
    assert providers["ecmwf"]["available"] is True
    assert "external_nowcast" not in providers
