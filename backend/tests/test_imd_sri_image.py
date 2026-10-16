"""B. Local real-image INTEGRATION tests: IMD Mumbai-Veravali DWR SRI decoder + provider on the genuine product.

Decoder logic is covered on every machine by test_imd_sri_decoder_logic.py (A). The tests here prove the same
logic holds on the real IMD image and skip, with a stated reason, when it is absent.

The primary tests run on the REAL official SRI image (observed 2026-09-18 03:51:28 UTC). IMD's reuse terms
for that image are unconfirmed, so it is kept locally only (tests/fixtures/local/, gitignored) and those
tests skip when it is absent. The committed, deterministic fixture is the legend table extracted from it
(floodnet/rainfall/imd_vrv_sri_legend.json), which the no-image tests below check.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from floodnet import config
from floodnet.rainfall import imd_sri_image as sri
from floodnet.rainfall.provider import (IMD_SRI_ID, IMDVeravaliSRIImageProvider, ProviderUnavailable,
                                        list_providers)

FIXTURE = Path(__file__).parent / "fixtures" / "local" / "imd_sri_vrv_20260918T035128Z.gif"
OBSERVED = datetime(2026, 9, 18, 3, 51, 28, tzinfo=timezone.utc)
needs_image = pytest.mark.skipif(not FIXTURE.exists(), reason="official IMD SRI image kept locally only")


@pytest.fixture(scope="module")
def raw() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def decoded(raw):
    return sri.decode_sri(raw)


@pytest.fixture(scope="module")
def grid():
    from floodnet.api.state import get_pilot
    try:
        return get_pilot()["terrain"].grid
    except Exception as ex:  # noqa: BLE001
        pytest.skip(f"pilot grid unavailable: {ex}")


# ------------------------------------------------------------------ no image needed
def test_timestamp_comment_is_ist_and_converted_to_utc():
    assert sri.parse_timestamp(b"2026-09-18T09:21:28") == OBSERVED
    for bad in (None, b"", b"yesterday"):
        with pytest.raises(sri.SRIDecodeError):
            sri.parse_timestamp(bad)


def test_legend_scale_passes_through_the_labelled_ticks():
    for y, v in zip(sri.LEGEND_TICKS_Y, sri.LEGEND_TICK_VALUES):
        assert sri.value_at_legend_y(y) == pytest.approx(v, abs=0.05)   # labels are printed to 1 dp


def test_reference_legend_bins_are_ordered_bounded_and_capped():
    bins = json.loads(sri._REFERENCE_LEGEND.read_text(encoding="utf-8"))["bins"]
    assert len(bins) == 47
    assert bins[0]["hi_mm_h"] is None                       # open-ended arrow bin
    assert bins[-1]["lo_mm_h"] == pytest.approx(0.1)        # lowest labelled value
    closed = bins[1:]
    for upper, lower in zip(closed, closed[1:]):
        assert upper["lo_mm_h"] == pytest.approx(lower["hi_mm_h"], abs=1e-3)   # contiguous, top -> bottom
    assert max(b["hi_mm_h"] - b["lo_mm_h"] for b in closed) < 2.6          # no finer precision claimed
    assert len({b["rgb"] for b in bins}) == len(bins)


# ------------------------------------------------------------------ real official image
@needs_image
def test_legend_extracted_from_the_image_itself(raw):
    from PIL import Image
    legend = sri.extract_legend(sri._rgb_keys(Image.open(io.BytesIO(raw))))
    assert len(legend) == 47 and legend[0].hi is None
    assert legend[0].value == legend[0].lo                   # capped bin uses its lower bound


@needs_image
def test_timestamp_and_georeference(decoded):
    assert decoded.observed_at_utc == OBSERVED
    g = decoded.georef
    assert g["tick_rms_px"] < 0.5
    assert g["site_vs_crosshair_km"] < 0.5                  # product geometry agrees with its stated site
    assert decoded.lon_of_x[1230] == pytest.approx(72.876, abs=0.003)
    assert decoded.lat_of_y[1229] == pytest.approx(19.134, abs=0.003)
    assert 0.19 < g["pixel_km"][0] < 0.23 and 0.19 < g["pixel_km"][1] < 0.23


@needs_image
def test_mask_never_marks_basemap_text_or_borders_as_rain(decoded):
    s = decoded.stats
    assert s["legend_colours_in_basemap_zone"] == 0         # legend palette absent from the pure basemap
    assert set(s["excluded_legend_colours"]) == {"fefefe", "d9e2e6"}
    c = decoded.confidence
    assert (c[:, 2440:] == sri.UNKNOWN).all()               # legend / annotation panel
    assert (c[:30, :] == sri.UNKNOWN).all() and (c[2435:, :] == sri.UNKNOWN).all()   # outside the map frame
    assert (c[:200, :200] == sri.UNKNOWN).all()             # corner beyond the 250 km range
    assert 0 < s["echo_pct"] < 5 and s["unknown_pct"] < 10


@needs_image
def test_values_are_legend_bins_non_negative_and_capped(decoded):
    r = decoded.rain_mm_h
    finite = r[np.isfinite(r)]
    assert finite.min() >= 0.0 and finite.max() <= decoded.legend[0].lo + 1e-6
    allowed = np.array([b.value for b in decoded.legend] + [0.0])
    for v in np.unique(finite):                             # stored as float32
        assert np.isclose(allowed, v, atol=1e-4).any(), v
    assert np.isnan(r[decoded.confidence == sri.UNKNOWN]).all()   # no-data stays no-data
    assert (r[decoded.confidence == sri.NO_ECHO] == 0).all()


@needs_image
def test_pixel_colour_maps_to_its_legend_bin(raw, decoded):
    from PIL import Image
    keys = sri._rgb_keys(Image.open(io.BytesIO(raw)))
    ys, xs = np.nonzero(decoded.confidence == sri.ECHO)
    by_rgb = {b.rgb: b for b in decoded.legend}
    for y, x in list(zip(ys, xs))[::max(1, len(ys) // 50)]:
        assert decoded.rain_mm_h[y, x] == pytest.approx(by_rgb[int(keys[y, x])].value)


@needs_image
def test_model_grid_output_for_pilot_and_for_a_real_echo_region(decoded, grid):
    f, den, win = sri.to_model_grid(decoded, grid, config.PILOT_BBOX_LONLAT)
    assert f.shape == (grid.ny, grid.nx) and np.isfinite(f).all() and f.min() >= 0
    assert win["window_valid_pct"] > 50
    # Same real image, a box over the echo cell near TAN (~19.2 N, 72.97 E) -- proves non-zero bins survive.
    ys, xs = np.nonzero(decoded.confidence == sri.ECHO)
    k = int(np.argmin(np.hypot(ys - 1180, xs - 1255)))
    lon, lat = decoded.lon_of_x[xs[k]], decoded.lat_of_y[ys[k]]
    rs, cs = sri.pilot_window(decoded, (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01), margin_px=0)
    assert np.nanmax(decoded.rain_mm_h[rs, cs]) > 0


@needs_image
def test_provider_builds_T_ny_nx_field_with_honest_provenance(raw, grid):
    scen, meta = IMDVeravaliSRIImageProvider().from_image(raw, grid, 3 * 3600, retrieved_at="x",
                                                          now=OBSERVED + timedelta(minutes=40))
    T = 3 * 3600 // config.RAIN_DT_S + 1
    assert scen.is_spatial and scen.intensity_field_mm_h.shape == (T, grid.ny, grid.nx)
    assert (scen.intensity_field_mm_h >= 0).all()
    assert scen.provenance.tag.value == "ESTIMATED"
    assert meta.source_type == "radar_image_derived" and meta.data_mode == "ESTIMATED"
    d = meta.detail
    assert d["mode"] == "RADAR_IMAGE_DERIVED_ESTIMATE"
    assert d["image_observed_at_utc"] == OBSERVED.isoformat()
    assert d["is_official_qpe"] is False and d["is_nowcast"] is False
    assert d["forecast_extension_label"] == "3-HOUR PERSISTENCE ESTIMATE"
    text = (scen.provenance.source + scen.provenance.note + json.dumps(d)).lower()
    assert "not imd numerical qpe" in text
    for claim in ("official imd qpe", "validated radar", "imd 0-3h radar nowcast", "gridded radar data"):
        assert claim not in text


# ------------------------------------------------------------------ failures are explicit, never substituted
@needs_image
def test_provider_refuses_stale_frame(raw, grid):
    with pytest.raises(ProviderUnavailable, match="old"):
        IMDVeravaliSRIImageProvider().from_image(raw, grid, 3600, "x", now=OBSERVED + timedelta(hours=3))


@needs_image
def test_provider_refuses_a_changed_colour_scale(raw, grid):
    from PIL import Image
    img = Image.open(io.BytesIO(raw))
    top = sri._rgb_keys(img)[730, sri.LEGEND_X]
    pal = img.getpalette()
    k = next(i for i in range(256) if (pal[3 * i] << 16 | pal[3 * i + 1] << 8 | pal[3 * i + 2]) == top)
    pal[3 * k:3 * k + 3] = [40, 200, 40]                    # recolour the top legend band
    img.putpalette(pal)
    buf = io.BytesIO(); img.save(buf, format="GIF", comment=img.info["comment"])
    with pytest.raises(ProviderUnavailable, match="colour scale changed"):
        IMDVeravaliSRIImageProvider().from_image(buf.getvalue(), grid, 3600, "x",
                                                 now=OBSERVED + timedelta(minutes=40))


def test_provider_refuses_non_product_images(grid):
    from PIL import Image
    buf = io.BytesIO(); Image.new("P", (400, 300)).save(buf, format="GIF", comment=b"2026-09-18T09:21:28")
    for data in (b"<html>not an image</html>", buf.getvalue()):
        with pytest.raises(ProviderUnavailable, match="not decodable"):
            IMDVeravaliSRIImageProvider().from_image(data, grid, 3600, "x", now=OBSERVED)


def test_provider_fetch_failure_and_missing_grid_are_unavailable(monkeypatch, grid):
    import httpx

    class Boom:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "Client", Boom)
    p = IMDVeravaliSRIImageProvider()
    with pytest.raises(ProviderUnavailable, match="could not be fetched"):
        p.get(IMD_SRI_ID, grid=grid)
    with pytest.raises(ProviderUnavailable, match="grid"):
        p.get(IMD_SRI_ID)
    assert list_providers()[IMD_SRI_ID] is list_providers()[IMD_SRI_ID]


@needs_image
def test_simulate_endpoint_runs_the_radar_derived_field(monkeypatch, raw):
    from fastapi.testclient import TestClient
    from floodnet.api.main import app
    prov = list_providers()[IMD_SRI_ID]
    monkeypatch.setattr(prov, "_fetch", lambda: (raw, "2026-09-18T04:32:28+00:00"))
    # offline + isolated: no Open-Meteo call, and the test frame never enters the real on-disk frame history
    from floodnet.rainfall.radar_nowcast import FrameStore
    monkeypatch.setattr(prov, "_ecmwf_tail", lambda: None)
    monkeypatch.setattr(prov, "_store", FrameStore())
    monkeypatch.setattr(IMDVeravaliSRIImageProvider, "MAX_AGE_MIN", 1e9)   # fixture is days old by now
    r = TestClient(app).post("/api/simulate", json={"scenario_id": IMD_SRI_ID, "horizon_min": 15})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["data_mode"] == "MIXED"
    assert b["provenance"]["rainfall_source"]["source_type"] == "radar_image_derived"
    assert b["provenance"]["rainfall_source"]["detail"]["mode"] == "RADAR_IMAGE_DERIVED_ESTIMATE"
