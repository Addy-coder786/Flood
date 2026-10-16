"""Final-pass regressions: radar edge cases + radar -> FloodNet end to end, hotspot intelligence, the street
inspector's timing, timeline-dependent routing, API status codes and a small concurrency check."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("PIL", reason="Pillow is the optional [radar] dependency")

from floodnet import config  # noqa: E402
from floodnet.analysis.hotspots import FLOOD_THRESHOLD_CM, flood_intelligence, segment_timing  # noqa: E402
from floodnet.api.main import app  # noqa: E402
from floodnet.data.fixtures import synthetic_pilot  # noqa: E402
from floodnet.rainfall import imd_sri_image as sri  # noqa: E402
from floodnet.rainfall.provider import IMD_SRI_ID, IMDVeravaliSRIImageProvider, ProviderUnavailable, list_providers  # noqa: E402
from floodnet.routing.router import safe_route  # noqa: E402
from .sri_fixture import legend_rgbs, lonlat_to_px, make_sri_gif  # noqa: E402

REAL_PILOT_BUILT = all((config.DATA_PROCESSED / f).exists() for f in ("network.json", "terrain.npz", "scenarios.json"))
needs_pilot = pytest.mark.skipif(not REAL_PILOT_BUILT, reason="real pilot data not built")
W, S, E, N = config.PILOT_BBOX_LONLAT
LON, LAT = (W + E) / 2, (S + N) / 2
OBSERVED = datetime(2026, 9, 18, 3, 51, 28, tzinfo=timezone.utc)
client = TestClient(app)


# ================================================================== radar edge cases
def test_no_rain_sri_decodes_to_zero_everywhere_it_is_valid():
    dec = sri.decode_sri(make_sri_gif())
    assert dec.stats["echo_pct"] == 0 and dec.stats["max_rain_mm_h"] == 0
    finite = dec.rain_mm_h[np.isfinite(dec.rain_mm_h)]
    assert finite.size > 0 and float(finite.max()) == 0.0


def test_a_colour_that_is_not_on_the_legend_is_never_read_as_rain():
    """A saturated patch (e.g. a new annotation IMD might add) is neither a legend band nor a known basemap
    colour: it must come out UNKNOWN, not as some nearest-looking rain rate."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(make_sri_gif()))
    pal = img.getpalette()
    free = 5 + len(legend_rgbs())                                  # first unused palette slot in the fixture
    pal[3 * free:3 * free + 3] = [255, 0, 255]                      # magenta: not on the SRI legend
    px = np.array(img)
    x, y = lonlat_to_px(LON, LAT)
    px[y - 6:y + 6, x - 6:x + 6] = free
    out = Image.fromarray(px, mode="P"); out.putpalette(pal)
    buf = io.BytesIO(); out.save(buf, format="GIF", comment=img.info["comment"])
    dec = sri.decode_sri(buf.getvalue())
    assert dec.confidence[y, x] == sri.UNKNOWN and np.isnan(dec.rain_mm_h[y, x])
    assert dec.stats["echo_pct"] == 0


@needs_pilot
def test_radar_image_to_floodnet_end_to_end(monkeypatch):
    """SRI image -> legend bins -> mask -> georeference -> [T, ny, nx] -> engine -> street depths, over the API."""
    prov = list_providers()[IMD_SRI_ID]
    from floodnet.rainfall.radar_nowcast import FrameStore
    gif = make_sri_gif(echoes=[(LON, LAT, 3, 60)])                  # a high-intensity band over the whole pilot
    monkeypatch.setattr(prov, "_fetch", lambda: (gif, "2026-09-18T04:30:00+00:00"))
    monkeypatch.setattr(prov, "_ecmwf_tail", lambda: None)
    monkeypatch.setattr(prov, "_store", FrameStore())
    monkeypatch.setattr(IMDVeravaliSRIImageProvider, "MAX_AGE_MIN", 1e9)
    r = client.post("/api/simulate", json={"scenario_id": IMD_SRI_ID, "horizon_min": 20})
    assert r.status_code == 200, r.text
    b = r.json()
    d = b["provenance"]["rainfall_source"]["detail"]
    band = sri.decode_sri(gif).legend[3]
    assert d["mode"] == "RADAR_IMAGE_DERIVED_ESTIMATE" and b["data_mode"] == "MIXED"
    assert band.lo <= d["pilot_max_mm_h"] <= band.hi and d["pilot_mean_mm_h"] > 0.9 * band.lo
    assert b["summary"]["peak_rain_mm_h"] == pytest.approx(d["pilot_mean_mm_h"], rel=0.02)
    assert b["mass_balance"]["rain_in_m3"] > 0 and abs(b["mass_balance"]["error_pct"]) < 0.5
    assert b["n_frames"] == 5


def test_stale_or_undecodable_radar_is_a_clean_503_not_a_substitution(monkeypatch):
    prov = list_providers()[IMD_SRI_ID]
    monkeypatch.setattr(prov, "_fetch", lambda: (b"<html>maintenance</html>", "x"))
    r = client.post("/api/simulate", json={"scenario_id": IMD_SRI_ID, "horizon_min": 15})
    if r.status_code == 503 and r.json().get("error") == "module_missing":
        pytest.skip("pilot not available")
    assert r.status_code == 503 and "not decodable" in r.json()["detail"]


# ================================================================== hotspot intelligence + street inspector
@pytest.fixture(scope="module")
def synthetic_run():
    import copy
    from floodnet.drainage.hydraulics import GraphDrainage
    from floodnet.simulation.engine import run_simulation
    from floodnet.streets.aggregate import make_street_fn
    from floodnet.terrain.runoff import runoff_fn
    from floodnet.terrain.surface import StorageCellSurface
    p = synthetic_pilot()
    net = copy.deepcopy(p["net"])
    scen = next(s for s in p["scenarios"] if s.id == "cloudburst")
    res = run_simulation(p["terrain"], net, scen, StorageCellSurface(p["terrain"]), GraphDrainage(net), runoff_fn,
                         street_fn=make_street_fn(p["roads"], p["terrain"].grid), horizon_s=3600)
    return p, res


def test_hotspot_metrics_agree_with_the_frames_they_summarise(synthetic_run):
    p, res = synthetic_run
    h = flood_intelligence(res, p["roads"], p["terrain"], top_n=5)
    peak_by_seg = {}
    for f in res.frames:
        for sid, d in f.street_depth_m.items():
            peak_by_seg[sid] = max(peak_by_seg.get(sid, 0.0), d * 100.0)
    assert h["max_depth_cm"] == pytest.approx(max(peak_by_seg.values()), abs=0.06)
    affected = {s for s, v in peak_by_seg.items() if v >= FLOOD_THRESHOLD_CM}
    assert h["affected_segments"] == len(affected)
    length = sum(s.length_m for s in p["roads"].segments if s.seg_id in affected)
    assert h["affected_road_length_m"] == pytest.approx(length, abs=0.1)
    assert h["earliest_onset_min"] is None or 0 <= h["earliest_onset_min"] <= h["peak_t_min"] <= h["horizon_min"]
    assert h["flooded_area_m2"] >= 0 and h["flooded_area_m2"] % (p["terrain"].grid.res ** 2) == pytest.approx(0, abs=1e-6)
    assert 0 <= h["affected_intersections"] <= len(p["roads"].node_xy)
    assert "not validated" in h["basis"]


def test_hotspot_ranking_is_deterministic_and_deepest_first(synthetic_run):
    p, res = synthetic_run
    a = flood_intelligence(res, p["roads"], p["terrain"])["hotspots"]
    b = flood_intelligence(res, p["roads"], p["terrain"])["hotspots"]
    assert a == b
    depths = [x["peak_depth_cm"] for x in a]
    assert depths == sorted(depths, reverse=True) and [x["rank"] for x in a] == list(range(1, len(a) + 1))
    names = [x["name"] for x in a if x["name"]]
    assert len(names) == len(set(names))                           # one entry per named street


def test_street_timing_matches_the_segment_series(synthetic_run):
    _, res = synthetic_run
    sid = max(res.frames[-1].street_depth_m, key=res.frames[-1].street_depth_m.get)
    t = segment_timing(res, sid)
    series = [f.street_depth_m.get(sid, 0.0) * 100 for f in res.frames]
    assert t["peak_depth_cm"] == pytest.approx(max(series), abs=0.06)
    assert t["peak_t_min"] == res.frames[int(np.argmax(series))].t_s / 60
    assert t["onset_t_min"] is None or t["onset_t_min"] <= t["peak_t_min"]
    assert segment_timing(res, "no-such-segment") is None


@needs_pilot
def test_hotspot_and_inspector_endpoints():
    run = client.post("/api/simulate", json={"scenario_id": "demo", "horizon_min": 45}).json()
    h = client.get(f"/api/simulation/{run['run_id']}/hotspots?top=3")
    assert h.status_code == 200
    body = h.json()
    assert len(body["hotspots"]) <= 3 and body["run_id"] == run["run_id"]
    assert client.get("/api/simulation/nope/hotspots").status_code == 404
    if body["hotspots"]:
        e = client.get(f"/api/simulation/{run['run_id']}/explain/{body['hotspots'][0]['seg_id']}?t_min=45").json()
        assert e["timing"]["peak_depth_cm"] == body["hotspots"][0]["peak_depth_cm"]
        for key in ("depth_cm", "rainfall_mm_h", "terrain_context", "nearest_drainage_node", "timing"):
            assert key in e
        assert "caused" not in str(e).lower()


# ================================================================== routing follows the forecast timeline
def test_the_same_trip_is_routed_differently_as_the_flood_develops():
    roads = synthetic_pilot()["roads"]
    mid = next(s for s in roads.segments if {s.u, s.v} == {1, 2})
    o, d = np.asarray(roads.node_lonlat[0]).tolist(), np.asarray(roads.node_lonlat[4]).tolist()
    timeline = {0: {}, 30: {mid.seg_id: 0.12}, 60: {mid.seg_id: 0.45}}       # depth on the direct link, metres
    routes = {t: safe_route(roads, depth, o, d, vehicle="car") for t, depth in timeline.items()}
    assert routes[0]["route_segments"] == routes[0]["baseline_segments"]      # dry: direct
    assert routes[60]["length_m"] > routes[0]["length_m"] and mid.seg_id not in routes[60]["route_segments"]
    assert routes[60]["max_depth_on_route_cm"] < routes[60]["baseline_max_depth_cm"] == pytest.approx(45.0)
    assert routes[30]["reachable"] and routes[30]["max_depth_on_route_cm"] <= 12.0 + 1e-6
    for r in routes.values():
        assert "traffic" not in str(r).lower()


# ================================================================== API behaviour
def test_status_codes_and_cors():
    assert client.get("/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/data-status").status_code == 200
    assert client.get("/api/simulation/unknown/series").status_code == 404
    assert client.post("/api/simulate", json={"scenario_id": "definitely-not-a-source"}).status_code in (404, 503)
    assert client.post("/api/simulate", json={}).status_code == 422
    assert client.post("/api/route", json={"origin": "x"}).status_code == 422
    pre = client.options("/api/data-status", headers={"Origin": "https://floodnet.vercel.app",
                                                        "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] in ("*", "https://floodnet.vercel.app")


@needs_pilot
def test_small_concurrent_read_load_is_served_consistently():
    run = client.post("/api/simulate", json={"scenario_id": "demo", "horizon_min": 15}).json()
    paths = [f"/api/simulation/{run['run_id']}/frame/{t}" for t in (0, 5, 10, 15)] + [
        f"/api/simulation/{run['run_id']}/series", f"/api/simulation/{run['run_id']}/hotspots",
        "/api/data-status", "/api/terrain/dem"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda p: TestClient(app).get(p).status_code, paths * 3))
    assert codes == [200] * len(codes)
