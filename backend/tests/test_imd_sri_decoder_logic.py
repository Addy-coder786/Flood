"""A. Deterministic decoder-LOGIC tests for the IMD Mumbai-Veravali SRI image path -- run on every machine.

They use `sri_fixture.make_sri_gif`, a format-faithful stand-in generated from the committed legend table and
the layout measured on the official product (no IMD imagery). Behaviour on the genuine IMD image is covered by
the local integration tests in test_imd_sri_image.py (B), which skip when that image is absent.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

pytest.importorskip("PIL", reason="Pillow is the optional [radar] dependency")

from floodnet import config  # noqa: E402
from floodnet.contracts import Grid  # noqa: E402
from floodnet.rainfall import imd_sri_image as sri  # noqa: E402
from floodnet.rainfall.provider import IMDVeravaliSRIImageProvider, ProviderUnavailable  # noqa: E402
from .sri_fixture import legend_rgbs, lonlat_to_px, make_sri_gif  # noqa: E402

OBSERVED = datetime(2026, 9, 18, 3, 51, 28, tzinfo=timezone.utc)
W, S, E, N = config.PILOT_BBOX_LONLAT
PILOT_LON, PILOT_LAT = (W + E) / 2, (S + N) / 2
BAND_20 = 25                     # a closed legend band in the ~20 mm/h range (see the legend JSON)


@pytest.fixture(scope="module")
def grid() -> Grid:
    """A 100 m model grid over the pilot box, built directly (no pilot data files needed)."""
    from pyproj import Transformer
    fwd = Transformer.from_crs(config.CRS_GEO, config.CRS_COMPUTE, always_xy=True)
    x0, y0 = fwd.transform(W, S)
    x1, y1 = fwd.transform(E, N)
    return Grid(x0=x0, y0=y0, res=100.0, nx=int((x1 - x0) // 100), ny=int((y1 - y0) // 100))


@pytest.fixture(scope="module")
def rain_over_pilot() -> bytes:
    return make_sri_gif(echoes=[(PILOT_LON, PILOT_LAT, BAND_20, 40)])


@pytest.fixture(scope="module")
def decoded(rain_over_pilot):
    return sri.decode_sri(rain_over_pilot)


def test_timestamp_from_ist_comment(decoded):
    assert decoded.observed_at_utc == OBSERVED


def test_legend_extracted_band_by_band(decoded):
    assert [b.rgb for b in decoded.legend] == legend_rgbs()
    top, bottom = decoded.legend[0], decoded.legend[-1]
    assert top.hi is None and top.value == top.lo                     # open-ended arrow band, capped
    assert bottom.lo == pytest.approx(0.1)
    for upper, lower in zip(decoded.legend[1:], decoded.legend[2:]):
        assert upper.lo == pytest.approx(lower.hi, abs=1e-3)           # contiguous bins


def test_band_maps_to_its_midpoint_value(decoded):
    b = decoded.legend[BAND_20]
    x, y = lonlat_to_px(PILOT_LON, PILOT_LAT)
    assert decoded.confidence[y, x] == sri.ECHO
    assert decoded.rain_mm_h[y, x] == pytest.approx(b.value)
    assert b.lo < decoded.rain_mm_h[y, x] < b.hi


def test_georeference_recovers_tick_geometry_and_site(decoded):
    g = decoded.georef
    assert g["tick_rms_px"] < 0.5 and g["site_vs_crosshair_km"] < 0.3
    x, y = lonlat_to_px(73.0, 19.0)
    assert decoded.lon_of_x[x] == pytest.approx(73.0, abs=0.003)
    assert decoded.lat_of_y[y] == pytest.approx(19.0, abs=0.003)


def test_mask_classes_and_no_data(decoded):
    c, r = decoded.confidence, decoded.rain_mm_h
    assert np.isnan(r[c == sri.UNKNOWN]).all()
    assert (r[c == sri.NO_ECHO] == 0).all()
    assert (c[:, 2440:] == sri.UNKNOWN).all()                          # legend panel is never rain
    assert (c[:150, :150] == sri.UNKNOWN).all()                        # beyond the 250 km range
    cx, cy = decoded.georef["crosshair_px"]
    assert c[int(cy), int(cx) + 300] == sri.UNKNOWN                    # dark crosshair line occludes
    assert decoded.stats["legend_colours_in_basemap_zone"] == 0


def test_white_band_and_text_over_rain_are_unknown_not_rain():
    lon2, lat2 = PILOT_LON + 0.1, PILOT_LAT + 0.1
    dec = sri.decode_sri(make_sri_gif(
        echoes=[(PILOT_LON, PILOT_LAT, BAND_20, 30)],
        occluders=[(PILOT_LON, PILOT_LAT, "text", 6), (lon2, lat2, "white", 8)]))
    x, y = lonlat_to_px(PILOT_LON, PILOT_LAT)
    assert dec.confidence[y, x] == sri.UNKNOWN and np.isnan(dec.rain_mm_h[y, x])
    x2, y2 = lonlat_to_px(lon2, lat2)
    assert dec.confidence[y2, x2] == sri.UNKNOWN                       # white = ambiguous legend band
    assert "fefefe" in dec.stats["excluded_legend_colours"]


def test_values_non_negative_and_within_legend(decoded):
    finite = decoded.rain_mm_h[np.isfinite(decoded.rain_mm_h)]
    assert finite.min() >= 0 and finite.max() <= decoded.legend[0].lo


def test_model_grid_and_T_ny_nx(rain_over_pilot, grid):
    scen, meta = IMDVeravaliSRIImageProvider().from_image(rain_over_pilot, grid, 3 * 3600, "x",
                                                          now=OBSERVED + timedelta(minutes=40))
    T = 3 * 3600 // config.RAIN_DT_S + 1
    f = scen.intensity_field_mm_h
    assert scen.is_spatial and f.shape == (T, grid.ny, grid.nx)
    band = sri.decode_sri(rain_over_pilot).legend[BAND_20]
    assert np.allclose(f, band.value, atol=1e-3)                       # uniform disc covers the whole pilot
    assert np.array_equal(f[0], f[-1])                                 # persistence: t>0 holds frame t=0
    assert meta.detail["mode"] == "RADAR_IMAGE_DERIVED_ESTIMATE" and meta.detail["is_official_qpe"] is False


def test_no_echo_over_pilot_gives_zero_not_substituted_rain(grid):
    scen, _ = IMDVeravaliSRIImageProvider().from_image(make_sri_gif(), grid, 3600, "x",
                                                       now=OBSERVED + timedelta(minutes=40))
    assert float(scen.intensity_field_mm_h.max()) == 0.0


def test_refusals(rain_over_pilot, grid):
    p = IMDVeravaliSRIImageProvider()
    later = OBSERVED + timedelta(minutes=40)
    with pytest.raises(ProviderUnavailable, match="old"):
        p.from_image(rain_over_pilot, grid, 3600, "x", now=OBSERVED + timedelta(hours=3))
    with pytest.raises(ProviderUnavailable, match="future"):
        p.from_image(rain_over_pilot, grid, 3600, "x", now=OBSERVED - timedelta(hours=1))
    bad = legend_rgbs(); bad[0] = 0x28C828                              # colour scale changed
    with pytest.raises(ProviderUnavailable, match="colour scale changed"):
        p.from_image(make_sri_gif(legend_override=bad), grid, 3600, "x", now=later)
    with pytest.raises(ProviderUnavailable, match="timestamp"):
        p.from_image(make_sri_gif(comment="no time here"), grid, 3600, "x", now=later)
    from PIL import Image
    small = io.BytesIO(); Image.new("P", (400, 300)).save(small, format="GIF", comment=b"2026-09-18T09:21:28")
    for data in (b"<html>not an image</html>", small.getvalue()):
        with pytest.raises(ProviderUnavailable, match="not decodable"):
            p.from_image(data, grid, 3600, "x", now=later)


def test_mostly_undecodable_pilot_is_refused(grid):
    covered = make_sri_gif(occluders=[(PILOT_LON, PILOT_LAT, "text", 25)])
    with pytest.raises(ProviderUnavailable, match="decodable"):
        IMDVeravaliSRIImageProvider().from_image(covered, grid, 3600, "x", now=OBSERVED + timedelta(minutes=40))
