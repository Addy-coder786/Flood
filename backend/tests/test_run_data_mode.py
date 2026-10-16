"""Run-level data-mode semantics for the IMD live path.

A live run is driven by a REAL IMD station observation that FloodNet extends over 3 h with a persistence
ESTIMATE. The run must therefore not report data_mode=REAL (which reads as "the whole forecast input was
observed"): it is MIXED, while the observation and the forecast driver each keep their own tag. Replay and
synthetic runs are unaffected. IMD HTTP is faked with the live response shape; no credentials are needed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from floodnet.api import state
from floodnet.api.main import app
from floodnet.config import DATA_PROCESSED
from floodnet.data.load import REQUIRED
from floodnet.rainfall.provider import LIVE_ID, list_providers

REAL_PILOT_BUILT = all((DATA_PROCESSED / f).exists() for f in REQUIRED)
pytestmark = pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")

client = TestClient(app)

# Shape as returned live by api.imd.gov.in current_wx on 2026-09-18 (values illustrative, not a record).
LIVE_ROW = {"Station Id": "43003", "Station": "Mumbai-Santacruz", "Date of Observation": "2026-09-18",
            "Time": "4", "Last 24 hrs Rainfall": "8.4"}


class _Resp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return [LIVE_ROW]


class _Client:
    def __init__(self, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, url, params=None): return _Resp()


@pytest.fixture
def live_body(monkeypatch):
    monkeypatch.setenv("IMD_API_KEY", "test-key")
    monkeypatch.setenv("IMD_API_TOKEN", "test-token-" + "x" * 40)   # realistic length (a fake value)
    monkeypatch.setattr("httpx.Client", _Client)
    list_providers()[LIVE_ID].clear_cache()
    r = client.post("/api/simulate", json={"scenario_id": LIVE_ID, "blockage": {"mode": "none"}, "horizon_min": 30})
    assert r.status_code == 200, r.text
    yield r.json()
    list_providers()[LIVE_ID].clear_cache()


def test_live_observation_stays_real_and_driver_is_estimated(live_body):
    d = live_body["provenance"]["rainfall_source"]["detail"]
    assert d["observation_tag"] == "REAL"
    assert d["forecast_driver_tag"] == "ESTIMATED"
    assert d["source_label"] == "Live IMD observation + 3-hour persistence estimate"
    assert live_body["provenance"]["rainfall"]["tag"] == "ESTIMATED"          # what the engine consumed
    assert live_body["provenance"]["rainfall_source"]["data_mode"] == "ESTIMATED"


def test_live_run_level_mode_is_mixed_not_real(live_body):
    assert state.data_mode() == "REAL"                     # pilot GIS inputs are real...
    assert live_body["data_mode"] == "MIXED"               # ...but the run as a whole is not
    assert live_body["provenance"]["data_mode"] == "MIXED"
    assert live_body["provenance"]["pilot_data_mode"] == "REAL"
    run_id = live_body["run_id"]
    for path in (f"/api/simulation/{run_id}/series", f"/api/simulation/{run_id}/frame/0",
                 f"/api/simulation/{run_id}/alert"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.text)
        assert r.json()["data_mode"] == "MIXED", path


def test_live_run_carries_no_mock_or_synthetic_or_nowcast_label(live_body):
    rs = live_body["provenance"]["rainfall_source"]
    text = str(rs).lower()
    assert "mock" not in text and "synthetic" not in text
    assert rs["detail"]["forecast_extension_label"] == "3-HOUR PERSISTENCE ESTIMATE"
    assert "nowcast" not in rs["detail"]["source_label"].lower()
    assert "radar" not in rs["detail"]["source_label"].lower()
    assert live_body["data_mode"] != "REAL"


def _scenario_with_tag(tag: str) -> str:
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    for s in r.json():
        if str(s.get("provenance", {}).get("tag", "")).upper() == tag:
            return s["id"]
    pytest.skip(f"no {tag} scenario in this build")


@pytest.mark.parametrize("tag", ["SYNTHETIC", "REAL"])
def test_replay_and_synthetic_runs_keep_the_pilot_mode(tag):
    sid = _scenario_with_tag(tag)
    r = client.post("/api/simulate", json={"scenario_id": sid, "blockage": {"mode": "none"}, "horizon_min": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provenance"]["rainfall"]["tag"] == tag     # classification of the rainfall itself unchanged
    assert body["data_mode"] == state.data_mode()            # run-level mode unchanged for these paths
    assert body["data_mode"] != "MIXED"


def test_july2005_replay_is_classified_real():
    r = client.post("/api/simulate", json={"scenario_id": "july2005", "blockage": {"mode": "none"}, "horizon_min": 30})
    if r.status_code == 404 or r.status_code == 422:
        pytest.skip("july2005 replay not present in this build")
    assert r.status_code == 200, r.text
    assert r.json()["provenance"]["rainfall"]["tag"] == "REAL"
    assert r.json()["data_mode"] == state.data_mode()
