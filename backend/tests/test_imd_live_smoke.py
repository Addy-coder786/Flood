"""Opt-in LIVE integration smoke tests for the IMD API -- NOT run by a normal `pytest` invocation.

Real IMD access needs a short-lived JWT (IMD_API_TOKEN) plus an IP-bound key (IMD_API_KEY), so these tests
depend on credentials that expire. They are therefore explicitly opt-in: a plain `pytest -q` skips them with
a stated reason and never needs a live credential. When opted in, a missing or rejected credential is a
FAILURE, never a skip -- the point of opting in is to prove real access.

Run explicitly (from backend/):
    RUN_LIVE_IMD=1 .venv/Scripts/python.exe -m pytest tests/test_imd_live_smoke.py -v -m live
"""
from __future__ import annotations

import os

import pytest

from floodnet.rainfall.provider import LIVE_ID, IMDObservationProvider, ProviderUnavailable

# IMD's platform requires BOTH credentials (X-API-Key AND Authorization: Bearer <JWT>) -- they are different
# values, and a key-only configuration is rejected with HTTP 401 "Authorization header missing or invalid".
# Once opted in, both must be present (checked by the autouse fixture below) -- missing is a failure.
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_IMD") != "1",
                       reason="live IMD smoke tests are opt-in; set RUN_LIVE_IMD=1 to run them"),
]


@pytest.fixture(autouse=True)
def _credentials_required_once_opted_in():
    """IMD's platform needs BOTH credentials (X-API-Key AND Authorization: Bearer <JWT>, different values).
    Opting in without them is a failed check, not a silent skip."""
    import floodnet.config  # noqa: F401 -- loads the root .env
    missing = [n for n in ("IMD_API_KEY", "IMD_API_TOKEN") if not os.environ.get(n)]
    if missing:
        pytest.fail(f"RUN_LIVE_IMD=1 but {', '.join(missing)} not configured (see docs/LIVE_RAINFALL_AUDIT.md)")


def test_live_fetch_returns_a_plausible_mumbai_observation():
    prov = IMDObservationProvider()
    try:
        scen, meta = prov.get()
    except ProviderUnavailable as e:
        pytest.fail(f"IMD credentials were set but the live call still failed -- report this, don't silence "
                    f"it (the message names which credential IMD rejected, if that was the cause): {e}")
    assert scen.id == LIVE_ID
    assert meta.source_type == "live_observation"
    # sanity bounds on the observed 24h total this normalises from, not a guess at today's actual weather
    assert 0.0 <= float(scen.intensity_mm_h[0]) * 24.0 <= 500.0, "24h total outside a physically plausible range for Mumbai"
    print(f"\nLIVE IMD fetch OK: {meta.source_name} @ {meta.timestamp} -> {scen.provenance.note}")


def test_live_scenario_drives_a_real_floodnet_run():
    """End-to-end: LIVE input all the way through the unmodified simulation engine, on the real pilot."""
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
    scen, meta = IMDObservationProvider().get()
    street_fn = make_street_fn(pilot["roads"], pilot["terrain"].grid)
    res = run_simulation(pilot["terrain"], pilot["net"], scen,
                         StorageCellSurface(pilot["terrain"], open_boundary=True), GraphDrainage(pilot["net"]),
                         runoff_fn, street_fn=street_fn, horizon_s=1800, frame_dt_s=300)
    assert len(res.frames) >= 2
    assert abs(res.mass_balance.error_pct) < 0.1
    assert res.provenance["rainfall"]["tag"] == "ESTIMATED"
    print(f"\nLIVE-driven FloodNet run OK: run_id={res.run_id}, mass-balance error={res.mass_balance.error_pct:.2e}%")


@pytest.mark.parametrize("endpoint, name_key, mumbai", [
    ("stationnowcast", "Station", "MUMBAI"),
    ("districtnowcast", "State_District", "MUMBAI"),
    ("districtrainfall", "District", "MUMBAI"),
])
def test_live_nowcast_and_rainfall_endpoints_return_mumbai_rows(endpoint, name_key, mumbai):
    """The other documented endpoints FloodNet relies on for context. Credentials go in headers only and are
    never printed; only the HTTP status and row counts are reported."""
    import httpx
    headers = {"X-API-Key": os.environ["IMD_API_KEY"], "Authorization": f"Bearer {os.environ['IMD_API_TOKEN']}"}
    r = httpx.get(f"{IMDObservationProvider.BASE_URL}/{endpoint}", headers=headers, timeout=60)
    assert r.status_code == 200, f"{endpoint}: HTTP {r.status_code} {r.text[:120]}"
    rows = r.json()
    assert isinstance(rows, list) and rows, f"{endpoint}: empty or non-list response"
    hits = [row for row in rows if mumbai in str(row.get(name_key, "")).upper()]
    assert hits, f"{endpoint}: no row whose {name_key} contains {mumbai!r} among {len(rows)} rows"
    print(f"\nLIVE IMD {endpoint}: {len(rows)} rows, {len(hits)} Mumbai row(s)")
