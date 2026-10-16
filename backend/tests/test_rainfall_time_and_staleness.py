"""Weather input honesty (decision D-36; acceptance W1-W7 in the Agent D test matrix): parsed observation times,
declared units and validity windows, staleness against an injectable clock, the IMD 24-h caveat, the ECMWF IFS
HRES model request and grid-point provenance, status endpoints exposing staleness, and credential hygiene.

Offline only: httpx.Client is replaced by the fakes from tests.test_rainfall_provider; conftest supplies a FAKE IMD
token. No test here calls api.imd.gov.in or api.open-meteo.com. W8 (ASSUMED stress scenarios as providers) is not
tested because D-36 explicitly rejects adding them as providers.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from floodnet.api import state
from floodnet.api.main import app
from floodnet.config import DATA_PROCESSED
from floodnet.data.load import REQUIRED
from floodnet.rainfall.provider import (
    ECMWF_ID,
    LIVE_ID,
    ECMWFForecastProvider,
    IMDObservationProvider,
    ProviderUnavailable,
    list_providers,
    provider_status,
)
from floodnet.rainfall.source_manager import DEMO, SourceManager
from tests.test_rainfall_provider import _FakeClient, _FakeResponse, _future_hourly, _imd_sample_row, _patch_httpx_client

STALENESS = {"FRESH", "AGING", "STALE", "UNKNOWN"}
TIME_BASES = {"UTC documented", "UTC inferred"}
TIMING_KEYS = {"valid_from_utc", "valid_until_utc", "received_at_utc", "time_basis", "units", "accumulation_interval",
               "age_min", "staleness"}
KEY, TOKEN = "k" * 64, "t" * 64
OBS = datetime(2026, 9, 9, 5, 30, tzinfo=timezone.utc)          # _imd_sample_row's documented observation time
GRID_LAT, GRID_LON = 19.0158, 72.8698                            # IFS HRES cell nearest the pilot (lead-verified)
REAL_PILOT_BUILT = all((DATA_PROCESSED / f).exists() for f in REQUIRED)
client = TestClient(app)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    assert dt.tzinfo is not None and dt.utcoffset() == timedelta(0), f"not an aware UTC timestamp: {s!r}"
    return dt


def _imd_row_with_time(field: str, value: str) -> dict:
    row = _imd_sample_row("8.4")
    del row["Time of Observation"]
    row[field] = value
    return row


def _imd(monkeypatch, row, **kw) -> tuple:
    _patch_httpx_client(monkeypatch, _FakeResponse(200, [row]))
    return IMDObservationProvider(api_key=KEY, api_token=TOKEN, **kw).get()


def _ecmwf_payload(n=4, mm=(4.0, 6.0, 2.0, 1.0), with_grid=True) -> dict:
    times, vals = _future_hourly(n, mm=mm)
    body = {"hourly": {"time": times, "precipitation": vals}}
    if with_grid:
        body.update(latitude=GRID_LAT, longitude=GRID_LON)      # Open-Meteo echoes the grid cell it used
    return body


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------------------------- W1
@pytest.mark.parametrize("field, value, raw, parsed", [
    ("Time", "3", "2026-09-09 3 UTC", "2026-09-09T03:00:00+00:00"),
    ("Time of Observation", "05:30:00", "2026-09-09 05:30:00 UTC", "2026-09-09T05:30:00+00:00"),
    ("Time", "", "2026-09-09", None),
    ("Time", "noon", "2026-09-09 noon UTC", None),
])
def test_imd_time_field_is_parsed_to_iso_utc_and_raw_string_is_kept(monkeypatch, field, value, raw, parsed):
    _, meta = _imd(monkeypatch, _imd_row_with_time(field, value))
    d = meta.detail
    assert d["observed_at"] == raw, "the raw string is pinned by the existing tests and must not change"
    timing = d["timing"]
    assert timing["observed_at_utc"] == parsed
    if parsed is not None:
        assert _parse(timing["observed_at_utc"]) == _parse(parsed)


def test_imd_unparseable_time_gives_none_not_a_guess(monkeypatch):
    _, meta = _imd(monkeypatch, _imd_row_with_time("Time", "noon"))
    timing = meta.detail["timing"]
    assert timing["observed_at_utc"] is None and timing["valid_from_utc"] is None and timing["valid_until_utc"] is None
    assert timing["age_min"] is None and timing["staleness"] == "UNKNOWN"
    assert "noon" in meta.detail["observed_at"]            # kept verbatim, not reformatted


# ---------------------------------------------------------------------------------------------- W2 / W3
def test_imd_timing_block_units_validity_and_time_basis(monkeypatch):
    _, meta = _imd(monkeypatch, _imd_sample_row("8.4"))
    timing = meta.detail["timing"]
    assert TIMING_KEYS | {"observed_at_utc"} <= set(timing), set(timing)
    assert "mm" in json.dumps(timing["units"])
    assert "24" in str(timing["accumulation_interval"])
    assert timing["time_basis"] in TIME_BASES
    assert _parse(timing["observed_at_utc"]) == OBS
    assert _parse(timing["valid_from_utc"]) == OBS
    assert _parse(timing["valid_until_utc"]) > OBS
    _parse(timing["received_at_utc"])
    assert meta.detail["retrieved_at"] == timing["received_at_utc"]
    assert json.loads(json.dumps(meta.to_dict()))["detail"]["timing"] == timing   # JSON-serialisable as is


def test_ecmwf_timing_block_validity_window_and_units(monkeypatch):
    payload = _ecmwf_payload()
    _patch_httpx_client(monkeypatch, _FakeResponse(200, payload))
    _, meta = ECMWFForecastProvider(lat=19.02, lon=72.845).get()
    timing = meta.detail["timing"]
    assert TIMING_KEYS | {"issued_at_utc"} <= set(timing), set(timing)
    assert timing["issued_at_utc"] is None or _parse(timing["issued_at_utc"])    # Open-Meteo gives no issue time
    selected = meta.detail["forecast_timestamps"]
    assert len(selected) == 3
    first = datetime.strptime(selected[0], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    last = datetime.strptime(selected[-1], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    # Open-Meteo documents `precipitation` as the "sum of the preceding hour": each stamp is the END of the hour it
    # covers. The selected values therefore cover [first - 1 h, last]. (Spec correction 2026-10-11: D-36 first said
    # [first, last + 1 h], which would label every hour one hour late; the implementer flagged it, the lead agreed.)
    assert _parse(timing["valid_from_utc"]) == first - timedelta(hours=1)
    assert _parse(timing["valid_until_utc"]) == last
    assert _parse(timing["received_at_utc"]) == _parse(meta.detail["retrieved_at"])
    assert timing["time_basis"] in TIME_BASES
    assert "mm" in json.dumps(timing["units"])
    assert "1" in str(timing["accumulation_interval"]) and "h" in str(timing["accumulation_interval"]).lower()


# ---------------------------------------------------------------------------------------------- W4
def test_imd_staleness_thresholds_are_named_constants():
    assert IMDObservationProvider.FRESH_MAX_MIN == 180
    assert IMDObservationProvider.AGING_MAX_MIN == 360
    assert ECMWFForecastProvider.FRESH_MAX_MIN == 60


@pytest.mark.parametrize("offset_min, expected", [
    (0, "FRESH"),
    (IMDObservationProvider.__dict__.get("FRESH_MAX_MIN", 180) - 1, "FRESH"),
    (IMDObservationProvider.__dict__.get("FRESH_MAX_MIN", 180) + 1, "AGING"),
    (IMDObservationProvider.__dict__.get("AGING_MAX_MIN", 360) - 1, "AGING"),
    (IMDObservationProvider.__dict__.get("AGING_MAX_MIN", 360) + 1, "STALE"),
    (3 * 24 * 60, "STALE"),
])
def test_imd_staleness_label_against_injected_clock(monkeypatch, offset_min, expected):
    now = OBS + timedelta(minutes=offset_min)
    _, meta = _imd(monkeypatch, _imd_sample_row("8.4"), clock=lambda: now)
    timing = meta.detail["timing"]
    assert timing["age_min"] == pytest.approx(offset_min, abs=0.1)
    assert timing["staleness"] == expected


def test_imd_staleness_unknown_when_observation_time_missing(monkeypatch):
    row = _imd_sample_row("8.4")
    del row["Time of Observation"]
    del row["Date of Observation"]
    _, meta = _imd(monkeypatch, row, clock=lambda: OBS)
    timing = meta.detail["timing"]
    assert timing["observed_at_utc"] is None and timing["age_min"] is None and timing["staleness"] == "UNKNOWN"


@pytest.mark.parametrize("offset_min, fresh", [(0, True), (30, True), (61, False), (6 * 60, False)])
def test_ecmwf_staleness_is_age_since_retrieval_against_injected_clock(offset_min, fresh):
    retrieved = datetime(2026, 9, 9, 6, 7, tzinfo=timezone.utc)
    base = retrieved.replace(minute=0) + timedelta(hours=1)
    times = [(base + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(4)]
    payload = {"latitude": GRID_LAT, "longitude": GRID_LON, "hourly": {"time": times, "precipitation": [1.0, 2.0, 3.0, 4.0]}}
    prov = ECMWFForecastProvider(lat=19.02, lon=72.845, clock=lambda: retrieved + timedelta(minutes=offset_min))
    _, meta = prov._normalize(payload, retrieved)
    timing = meta.detail["timing"]
    assert _parse(timing["received_at_utc"]) == retrieved
    assert timing["age_min"] == pytest.approx(offset_min, abs=0.1)
    assert timing["staleness"] in STALENESS
    assert (timing["staleness"] == "FRESH") is fresh


# ---------------------------------------------------------------------------------------------- D-36 P0-2 caveat
def test_imd_caveat_says_not_a_nowcast_and_pinned_label_is_unchanged(monkeypatch):
    scen, meta = _imd(monkeypatch, _imd_sample_row("8.4"))
    d = meta.detail
    assert d["forecast_extension_label"] == "3-HOUR PERSISTENCE ESTIMATE"
    caveat = d["caveat"]
    assert "not a nowcast" in caveat.lower() and "24-h" in caveat
    assert "NOT" in caveat                                             # the emphasis is deliberate
    assert "radar" not in caveat.lower() or "not" in caveat.lower()
    assert "persistence" in scen.provenance.note.lower()
    assert scen.intensity_mm_h[0] == pytest.approx(8.4 / 24.0)         # maths unchanged (W9)


# ---------------------------------------------------------------------------------------------- D-36 P0-1 ECMWF
def test_ecmwf_requests_ifs_hres_and_records_grid_point_and_distance(monkeypatch):
    _patch_httpx_client(monkeypatch, _FakeResponse(200, _ecmwf_payload()))
    prov = ECMWFForecastProvider(lat=19.02, lon=72.845)
    scen, meta = prov.get()
    assert _FakeClient.last_call["params"]["models"] == "ecmwf_ifs"
    assert _FakeClient.last_call["params"]["hourly"] == "precipitation"
    d = meta.detail
    assert d["grid_point_lat"] == pytest.approx(GRID_LAT) and d["grid_point_lon"] == pytest.approx(GRID_LON)
    expected_km = _haversine_km(prov.lat, prov.lon, GRID_LAT, GRID_LON)
    assert d["distance_km_to_pilot"] == pytest.approx(expected_km, abs=0.3)
    assert "ecmwf_ifs" in json.dumps(d), "the requested model id must be recorded in the provenance detail"
    note = scen.provenance.note
    assert "HRES" in note and "not a nowcast" in note.lower()
    assert "imd" not in note.lower().replace("not an imd", "")           # never claims to be an IMD product


# ---------------------------------------------------------------------------------------------- W5 (pin)
@pytest.mark.parametrize("raw", ["", "NA", "-", None])
def test_blank_or_non_numeric_rainfall_is_unavailable_not_zero(monkeypatch, raw):
    row = _imd_sample_row("8.4")
    row["Last 24 hrs Rainfall"] = raw
    _patch_httpx_client(monkeypatch, _FakeResponse(200, [row]))
    with pytest.raises(ProviderUnavailable, match="Last 24 hrs Rainfall"):
        IMDObservationProvider(api_key=KEY, api_token=TOKEN).get()


def test_zero_rainfall_is_a_valid_observation(monkeypatch):
    scen, meta = _imd(monkeypatch, _imd_sample_row("0"))
    assert list(scen.intensity_mm_h) == [0.0] * len(scen.intensity_mm_h)
    assert meta.detail["observed_rainfall_mm"] == 0.0


# ---------------------------------------------------------------------------------------------- W6
def _failure_clients():
    import httpx

    class _Raising(_FakeClient):
        def get(self, url, params=None):
            raise RuntimeError(f"Client error for url '{url}?id={params.get('id')}'")

    class _Timeout(_FakeClient):
        def get(self, url, params=None):
            raise httpx.TimeoutException("timed out")

    return {"200-not-json": (_FakeClient, _FakeResponse(200, "maintenance")),
            "401": (_FakeClient, _FakeResponse(401, {"error": "Invalid or expired JWT token"})),
            "403": (_FakeClient, _FakeResponse(403, {"error": "Invalid API key"})),
            "500": (_FakeClient, _FakeResponse(500, {"error": "upstream"})),
            "raising": (_Raising, None), "timeout": (_Timeout, None)}


@pytest.mark.parametrize("case", ["200-not-json", "401", "403", "500", "raising", "timeout"])
def test_no_secret_in_any_failure_path(monkeypatch, caplog, case):
    import httpx
    cls, response = _failure_clients()[case]
    _FakeClient.response = response
    monkeypatch.setattr(httpx, "Client", cls)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ProviderUnavailable) as exc:
        IMDObservationProvider(api_key=KEY, api_token=TOKEN).get()
    text = str(exc.value) + repr(exc.value.args)
    assert KEY not in text and TOKEN not in text
    assert KEY not in caplog.text and TOKEN not in caplog.text
    for rec in caplog.records:
        assert KEY not in rec.getMessage() and TOKEN not in rec.getMessage()


def test_no_secret_in_metadata_or_provider_status_after_a_successful_fetch(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("IMD_API_KEY", KEY)
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN)
    _patch_httpx_client(monkeypatch, _FakeResponse(200, [_imd_sample_row("8.4")]))
    prov = list_providers()[LIVE_ID]
    prov.clear_cache()
    try:
        _, meta = prov.get()
        meta_json = json.dumps(meta.to_dict())
        status_json = json.dumps(provider_status())
    finally:
        prov.clear_cache()
    for blob in (meta_json, status_json, caplog.text):
        assert KEY not in blob and TOKEN not in blob
    assert "Bearer" not in meta_json


# ---------------------------------------------------------------------------------------------- W7
def test_provider_status_live_row_exposes_staleness_and_age_after_a_fetch(monkeypatch):
    monkeypatch.setenv("IMD_API_KEY", KEY)
    _patch_httpx_client(monkeypatch, _FakeResponse(200, [_imd_sample_row("8.4")]))
    prov = list_providers()[LIVE_ID]
    prov.clear_cache()
    try:
        prov.get()
        rows = {r["id"]: r for r in provider_status()}
    finally:
        prov.clear_cache()
    live = rows[LIVE_ID]
    assert live["available"] is True
    assert live["staleness"] in STALENESS
    # the sample observation (2026-09-09 05:30 UTC) is older than every threshold for any real clock from now on
    assert live["staleness"] == "STALE" and live["age_min"] > IMDObservationProvider.AGING_MAX_MIN
    assert KEY not in json.dumps(rows)


def test_provider_status_ecmwf_row_exposes_staleness_after_a_fetch(monkeypatch):
    _patch_httpx_client(monkeypatch, _FakeResponse(200, _ecmwf_payload()))
    prov = list_providers()[ECMWF_ID]
    prov.clear_cache()
    try:
        prov.get()
        rows = {r["id"]: r for r in provider_status()}
    finally:
        prov.clear_cache()
    row = rows[ECMWF_ID]
    assert row["staleness"] == "FRESH" and row["age_min"] is not None and row["age_min"] < 1.0


@pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
def test_data_status_and_status_expose_staleness_without_secrets(monkeypatch):
    monkeypatch.setenv("IMD_API_KEY", KEY)
    monkeypatch.setenv("IMD_API_TOKEN", TOKEN)
    _patch_httpx_client(monkeypatch, _FakeResponse(200, [_imd_sample_row("8.4")]))
    prov = list_providers()[LIVE_ID]
    prov.clear_cache()
    try:
        r = client.post("/api/simulate", json={"scenario_id": LIVE_ID, "blockage": {"mode": "none"}, "horizon_min": 30})
        assert r.status_code == 200, r.text
        ds = client.get("/api/data-status")
        st = client.get("/api/status")
    finally:
        prov.clear_cache()
    assert ds.status_code == 200 and st.status_code == 200
    active = ds.json()["active_source"]
    assert active is not None and active["staleness"] in STALENESS and "age_min" in active
    live = {row["id"]: row for row in st.json()["rainfall_providers"]}[LIVE_ID]
    assert live["staleness"] in STALENESS and "age_min" in live
    for body in (ds.text, st.text, r.text):
        assert KEY not in body and TOKEN not in body


def test_auto_resolution_falls_back_to_demo_with_fell_back_flag():
    """W7 pin: every live source failing -> DEMO, labelled as a fallback, never relabelled live."""
    from floodnet.rainfall.provider import ScenarioProvider

    class _Down:
        def get(self, *a, **k):
            raise ProviderUnavailable("down (test)")

    demo, _ = ScenarioProvider().get("cloudburst")
    sm = SourceManager(cache_dir=None)
    scen, meta, status = sm.resolve({"live": _Down(), "ecmwf": _Down()}, [("live", "LIVE"), ("ecmwf", "FORECAST")],
                                    grid=None, horizon_s=3 * 3600, demo_scenario=demo)
    assert status["label"] == DEMO and status["fell_back"] is True and status["is_live"] is False
    assert meta.data_mode == "SYNTHETIC" and scen.id == "auto"
    assert all(not a["ok"] for a in status["attempts"] if a["source"] in ("live", "ecmwf"))
