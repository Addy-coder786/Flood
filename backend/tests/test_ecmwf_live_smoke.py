"""Opt-in LIVE integration smoke test for ECMWFForecastProvider -- NOT run by a normal `pytest` invocation.

Unlike IMDObservationProvider, this provider needs no API key (Open-Meteo's ECMWF endpoint is free for
non-commercial use), so it cannot be gated on a credential being configured. It is gated on an explicit
opt-in environment variable instead, for the same reason IMD's live test is opt-in: "the normal test suite
must NOT depend on the live [external] API" (see docs/ECMWF_OPENMETEO_AUDIT.md).

Run explicitly:
    cd backend && RUN_LIVE_ECMWF_TEST=1 .venv/Scripts/python.exe -m pytest tests/test_ecmwf_live_smoke.py -v -m live
"""
from __future__ import annotations

import os

import pytest

from floodnet.rainfall.provider import ECMWF_ID, ECMWFForecastProvider, ProviderUnavailable

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("RUN_LIVE_ECMWF_TEST"), reason="opt-in only; set RUN_LIVE_ECMWF_TEST=1 to run"),
]


def test_live_fetch_returns_a_plausible_mumbai_forecast():
    prov = ECMWFForecastProvider()
    try:
        scen, meta = prov.get()
    except ProviderUnavailable as e:
        pytest.fail(f"RUN_LIVE_ECMWF_TEST was set but the live call still failed -- report this, don't silence it: {e}")
    assert scen.id == ECMWF_ID
    assert meta.source_type == "ecmwf_forecast"
    assert meta.data_mode == "NWP"
    # sanity bounds on real ECMWF precipitation output, not a guess at today's actual weather
    assert all(0.0 <= float(v) <= 300.0 for v in scen.intensity_mm_h), "hourly mm/h outside a physically plausible range"
    print(f"\nLIVE ECMWF fetch OK: {meta.source_name} @ {meta.timestamp}")
    print(f"  coordinates: {meta.detail['location_lonlat']}")
    print(f"  forecast timestamps: {meta.detail['forecast_timestamps']}")
    print(f"  precipitation (mm): {meta.detail['precipitation_mm']}")


def test_live_scenario_drives_a_real_floodnet_run():
    """End-to-end: the real ECMWF forecast all the way through the unmodified simulation engine, on the real
    pilot. Verifies FloodNet can actually consume the normalized forecast, not just that the provider parses."""
    from floodnet.data.load import load_pilot
    from floodnet.drainage.hydraulics import GraphDrainage
    from floodnet.simulation.engine import run_simulation
    from floodnet.streets.aggregate import make_street_fn
    from floodnet.terrain.runoff import runoff_fn
    from floodnet.terrain.surface import StorageCellSurface

    try:
        pilot = load_pilot()
    except Exception:
        pytest.skip("real pilot data not built")
    scen, meta = ECMWFForecastProvider().get()
    street_fn = make_street_fn(pilot["roads"], pilot["terrain"].grid)
    res = run_simulation(pilot["terrain"], pilot["net"], scen,
                         StorageCellSurface(pilot["terrain"], open_boundary=True), GraphDrainage(pilot["net"]),
                         runoff_fn, street_fn=street_fn, horizon_s=1800, frame_dt_s=300)
    assert len(res.frames) >= 2
    assert abs(res.mass_balance.error_pct) < 0.1
    assert res.provenance["rainfall"]["tag"] == "NWP"
    print(f"\nLIVE-ECMWF-driven FloodNet run OK: run_id={res.run_id}, mass-balance error={res.mass_balance.error_pct:.2e}%")
