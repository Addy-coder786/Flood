"""Experimental radar-image nowcast: frame history, motion from the DECODED rain field only, advection, and the
source-aware forecast horizon. Runs everywhere on the generated stand-in image (tests/sri_fixture.py); it has
NOT been exercised on two genuinely consecutive IMD frames -- see docs/RADAR_NOWCAST_STATUS.md.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

pytest.importorskip("PIL", reason="Pillow is the optional [radar] dependency")

from floodnet import config  # noqa: E402
from floodnet.contracts import Grid  # noqa: E402
from floodnet.rainfall import imd_sri_image as sri  # noqa: E402
from floodnet.rainfall import radar_nowcast as rn  # noqa: E402
from floodnet.rainfall.provider import IMDVeravaliSRIImageProvider  # noqa: E402
from .sri_fixture import make_sri_gif  # noqa: E402

W, S, E, N = config.PILOT_BBOX_LONLAT
LON, LAT = (W + E) / 2, (S + N) / 2
PX_PER_DEG_LON = 497.25
BAND = 25
T0 = datetime(2026, 9, 18, 3, 51, 28, tzinfo=timezone.utc)      # GIF comment 09:21:28 IST
IST = "2026-09-18T{:02d}:{:02d}:28"


def _gif(px_west: float, minute: int, radius: int = 45, **kw) -> bytes:
    """A rain disc `px_west` image pixels west of the pilot centre, observed at 09:<minute> IST."""
    return make_sri_gif(echoes=[(LON - px_west / PX_PER_DEG_LON, LAT, BAND, radius)],
                        comment=IST.format(9, minute), **kw)


def _frame(data: bytes) -> rn.Frame:
    return rn.frame_from_decoded(sri.decode_sri(data), config.PILOT_BBOX_LONLAT)


@pytest.fixture(autouse=True)
def _nowcast_enabled(monkeypatch):
    """The experimental nowcast is opt-in (FLOODNET_RADAR_NOWCAST=1); these tests exercise it switched on."""
    monkeypatch.setenv("FLOODNET_RADAR_NOWCAST", "1")


@pytest.fixture(scope="module")
def grid() -> Grid:
    from pyproj import Transformer
    fwd = Transformer.from_crs(config.CRS_GEO, config.CRS_COMPUTE, always_xy=True)
    x0, y0 = fwd.transform(W, S)
    x1, y1 = fwd.transform(E, N)
    return Grid(x0=x0, y0=y0, res=100.0, nx=int((x1 - x0) // 100), ny=int((y1 - y0) // 100))


@pytest.fixture(scope="module")
def pair():
    """Two frames 20 min apart; the disc moves 40 px east (140 -> 100 px west of the pilot)."""
    a, b = _gif(140, 21), _gif(100, 41)
    return a, b, _frame(a), _frame(b)


# ------------------------------------------------------------------ frame history
def test_store_dedupes_republished_scan_and_orders_by_time(pair, tmp_path):
    _, _, fa, fb = pair
    store = rn.FrameStore(tmp_path)
    assert store.add(fb) is True and store.add(fa) is True          # added out of order
    assert store.add(_frame(_gif(100, 41))) is False                 # same observation time re-published
    assert [f.observed_at for f in store.frames] == [T0, T0 + timedelta(minutes=20)]
    assert store.latest_pair()[1].observed_at == T0 + timedelta(minutes=20)
    reloaded = rn.FrameStore(tmp_path)                                # survives a restart
    assert [f.observed_at for f in reloaded.frames] == [f.observed_at for f in store.frames]
    assert np.array_equal(np.isnan(reloaded.frames[0].rain), np.isnan(fa.rain))


def test_single_frame_gives_no_pair():
    store = rn.FrameStore()
    store.add(_frame(_gif(100, 41)))
    assert store.latest_pair() is None


def test_store_keeps_only_the_most_recent_frames(pair):
    _, _, fa, _ = pair
    store = rn.FrameStore(max_frames=3)
    for k in range(5):
        store.add(rn.Frame(T0 + timedelta(minutes=10 * k), fa.rain, fa.rows, fa.cols, fa.lons, fa.lats, fa.pixel_km))
    assert [f.observed_at for f in store.frames] == [T0 + timedelta(minutes=10 * k) for k in (2, 3, 4)]


# ------------------------------------------------------------------ motion: decoded field only
def test_motion_recovered_from_decoded_fields(pair):
    _, _, fa, fb = pair
    m = rn.estimate_motion(fa, fb)
    assert m is not None
    assert m.dx_px_per_min == pytest.approx(40 / 20, abs=0.1) and abs(m.dy_px_per_min) < 0.1
    assert m.toward_deg == pytest.approx(90, abs=5)                   # moving east
    assert m.speed_kmh == pytest.approx(40 * fb.pixel_km[0] * 3, rel=0.1)
    assert m.correlation > 0.8 and m.gap_min == pytest.approx(20)


def test_basemap_text_and_white_never_drive_motion():
    """Only labels / white patches move between frames; there is no decoded rain, so there is no motion."""
    occ = lambda px: [(LON - px / PX_PER_DEG_LON, LAT, "text", 30), (LON - px / PX_PER_DEG_LON, LAT + 0.1, "white", 30)]  # noqa: E731
    fa = _frame(make_sri_gif(occluders=occ(140), comment=IST.format(9, 21)))
    fb = _frame(make_sri_gif(occluders=occ(100), comment=IST.format(9, 41)))
    assert fa.echo_px == 0 and fb.echo_px == 0
    assert rn.estimate_motion(fa, fb) is None


def test_motion_refused_without_enough_evidence(pair):
    _, _, fa, fb = pair
    late = rn.Frame(fa.observed_at + timedelta(minutes=70), fb.rain, fb.rows, fb.cols, fb.lons, fb.lats, fb.pixel_km)
    assert rn.estimate_motion(fa, late) is None                        # frames too far apart
    soon = rn.Frame(fa.observed_at + timedelta(minutes=2), fb.rain, fb.rows, fb.cols, fb.lons, fb.lats, fb.pixel_km)
    assert rn.estimate_motion(fa, soon) is None                        # too close together
    tiny_a, tiny_b = _frame(_gif(140, 21, radius=3)), _frame(_gif(100, 41, radius=3))
    assert rn.estimate_motion(tiny_a, tiny_b) is None                  # too few echo pixels to track
    other = rn.Frame(fb.observed_at, fb.rain[:-4], (fb.rows[0], fb.rows[1] - 4), fb.cols, fb.lons, fb.lats[:-4], fb.pixel_km)
    assert rn.estimate_motion(fa, other) is None                       # window geometry differs
    noise = rn.Frame(fb.observed_at, np.random.default_rng(1).permutation(fb.rain.ravel()).reshape(fb.rain.shape),
                     fb.rows, fb.cols, fb.lons, fb.lats, fb.pixel_km)
    assert rn.estimate_motion(fa, noise) is None                       # no coherent pattern -> weak correlation


def test_advection_moves_rain_keeps_unknown_unknown_and_caps_the_lead(pair):
    _, _, fa, fb = pair
    m = rn.estimate_motion(fa, fb)
    out = rn.advect(fb, m, 600)                                        # +10 min -> 20 px east
    cy, cx0 = np.argwhere(np.nan_to_num(fb.rain) > 0).mean(axis=0)
    _, cx1 = np.argwhere(np.nan_to_num(out) > 0).mean(axis=0)
    assert cx1 - cx0 == pytest.approx(20, abs=2)
    assert np.isnan(out[:, :15]).all()                                 # inflow from outside the window is unknown
    assert np.nanmax(out) <= np.nanmax(fb.rain) + 1e-4 and np.nanmin(out) >= 0
    assert np.array_equal(np.isnan(rn.advect(fb, m, 1800)), np.isnan(rn.advect(fb, m, 7200)))   # capped at 30 min
    assert np.array_equal(np.isnan(rn.advect(fb, m, 0)), np.isnan(fb.rain))


# ------------------------------------------------------------------ source transitions + provenance
def test_horizon_is_observed_then_nowcast_then_ecmwf(pair, grid):
    a, b, _, _ = pair
    p, store = IMDVeravaliSRIImageProvider(), rn.FrameStore()
    now = T0 + timedelta(minutes=50)
    p.from_image(a, grid, 3 * 3600, "x", now=T0 + timedelta(minutes=30), store=store)
    t_tail = np.arange(0, 10801, 300.0)
    tail = (t_tail, np.where(t_tail < 3600, 2.0, 7.0), T0 + timedelta(minutes=50))     # retrieved 30 min after frame b
    scen, meta = p.from_image(b, grid, 3 * 3600, "x", now=now, store=store, tail=tail)
    f, t = scen.intensity_field_mm_h, scen.t_s
    d = meta.detail

    assert float(f[0].max()) == 0.0                                    # observed frame: disc still west of the pilot
    i30 = int(np.flatnonzero(t == 1800)[0])
    assert float(f[i30].mean()) > 5.0                                  # advected disc has reached the pilot
    assert float(f[i30 - 3].mean()) <= float(f[i30].mean())            # arriving, not invented at once
    i35, i120 = i30 + 1, int(np.flatnonzero(t == 7200)[0])
    assert np.allclose(f[i35], 2.0) and np.allclose(f[i120], 7.0)      # ECMWF tail, uniform
    # wall-clock alignment: model t = 90 min is 60 min after the ECMWF retrieval -> second-hour value
    assert np.allclose(f[int(np.flatnonzero(t == 5400)[0])], 7.0)

    labels = [(s["from_min"], s["to_min"], s["label"], s["tag"]) for s in d["forecast_segments"]]
    assert labels == [(0, 0, "Radar-derived estimate", "RADAR_IMAGE_DERIVED_ESTIMATE"),
                      (5, 30, "Experimental radar-image nowcast", "EXPERIMENTAL_NOWCAST"),
                      (30, 180, "ECMWF forecast", "NWP")]
    assert d["is_experimental_image_nowcast"] is True and d["is_imd_nowcast"] is False and d["is_official_qpe"] is False
    assert d["nowcast"]["toward_deg"] == pytest.approx(90, abs=5) and d["frames_in_history"] == 2
    note = scen.provenance.note
    assert "not an IMD nowcast" in note and "Experimental radar-image nowcast" in note and "ECMWF forecast" in note
    assert scen.provenance.tag.value == "ESTIMATED" and meta.data_mode == "ESTIMATED"
    assert "imd nowcast" not in d["forecast_extension_label"].lower()


def test_without_a_second_frame_no_nowcast_is_claimed(pair, grid):
    _, b, _, _ = pair
    scen, meta = IMDVeravaliSRIImageProvider().from_image(b, grid, 3 * 3600, "x", now=T0 + timedelta(minutes=50),
                                                          store=rn.FrameStore())
    d = meta.detail
    assert d["nowcast"] is None and d["is_experimental_image_nowcast"] is False
    assert d["forecast_extension_label"] == "3-HOUR PERSISTENCE ESTIMATE"
    assert [s["label"] for s in d["forecast_segments"]] == ["Radar-derived estimate",
                                                            "Persistence of the observed frame", "Persistence estimate"]
    assert np.array_equal(scen.intensity_field_mm_h[0], scen.intensity_field_mm_h[-1])
    assert "no radar nowcast is made" in scen.provenance.note


def test_republished_stale_scan_does_not_fake_a_pair(pair, grid):
    """IMD re-publishes the same scan while the radar is down: the second fetch must not become 'frame 2'."""
    _, b, _, _ = pair
    p, store = IMDVeravaliSRIImageProvider(), rn.FrameStore()
    for _ in range(2):
        _, meta = p.from_image(b, grid, 3600, "x", now=T0 + timedelta(minutes=50), store=store)
    assert meta.detail["frames_in_history"] == 1 and meta.detail["nowcast"] is None


def test_nowcast_without_ecmwf_holds_the_last_advected_field(pair, grid):
    a, b, _, _ = pair
    p, store = IMDVeravaliSRIImageProvider(), rn.FrameStore()
    p.from_image(a, grid, 3 * 3600, "x", now=T0 + timedelta(minutes=30), store=store)
    scen, meta = p.from_image(b, grid, 3 * 3600, "x", now=T0 + timedelta(minutes=50), store=store)
    f, t = scen.intensity_field_mm_h, scen.t_s
    i30 = int(np.flatnonzero(t == 1800)[0])
    assert np.array_equal(f[i30], f[-1])
    assert meta.detail["forecast_segments"][-1]["label"] == "Persistence estimate"


def test_nowcast_is_disabled_by_default_even_with_two_good_frames(pair, grid, monkeypatch):
    monkeypatch.delenv("FLOODNET_RADAR_NOWCAST", raising=False)
    a, b, _, _ = pair
    p, store = IMDVeravaliSRIImageProvider(), rn.FrameStore()
    p.from_image(a, grid, 3 * 3600, "x", now=T0 + timedelta(minutes=30), store=store)
    scen, meta = p.from_image(b, grid, 3 * 3600, "x", now=T0 + timedelta(minutes=50), store=store)
    assert meta.detail["frames_in_history"] == 2                       # history is still kept ...
    assert meta.detail["nowcast"] is None and meta.detail["is_experimental_image_nowcast"] is False
    assert meta.detail["forecast_extension_label"] == "3-HOUR PERSISTENCE ESTIMATE"   # ... SRI + persistence path
    assert np.array_equal(scen.intensity_field_mm_h[0], scen.intensity_field_mm_h[-1])
