"""ETA breakdown for a route (DECISIONS D-49): ASSUMED road speeds + MODELLED FLOOD DELAY (Pregnolato et al. 2017
depth-disruption curve on the forecast depth at the delayed arrival minute) + traffic, which is UNKNOWN because no live
traffic feed is configured. Written before floodnet/ops/eta.py existed."""
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.access.arrival_router import route_one
from floodnet.access.events import ClosureBook
from floodnet.ops import eta
from tests.ops_net import G, NO_CTX, ROADS, flood_from, forecast

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


def _route(fc, vehicle="truck", o=0, d=2, t=0.0):
    r = route_one(ROADS, G, o, d, vehicle=vehicle, dispatch_t_min=t, forecast=fc, seg_ctx=NO_CTX,
                  closures=ClosureBook([], t))
    assert r["reachable"]
    return r


def test_pregnolato_curve_values_and_shape():
    assert eta.pregnolato_speed_kmh(0) == pytest.approx(86.9448)
    assert eta.pregnolato_speed_kmh(100) == pytest.approx(0.0009 * 100 ** 2 - 0.5529 * 100 + 86.9448)
    speeds = [eta.pregnolato_speed_kmh(d) for d in range(0, 300, 10)]
    assert all(b < a for a, b in zip(speeds, speeds[1:]))           # deeper water, slower, up to 300 mm


def test_speed_in_water_never_exceeds_the_assumed_road_speed_and_has_a_floor():
    assert eta.speed_in_water_kmh(15.0, 0.0) == 15.0                 # dry: the assumed road speed
    assert eta.speed_in_water_kmh(15.0, 5.0) == 15.0                 # 50 mm: curve ~60 km/h, road speed binds
    assert eta.speed_in_water_kmh(15.0, 20.0) < 15.0                 # 200 mm: curve ~12 km/h
    assert eta.speed_in_water_kmh(15.0, 29.0) == eta.MIN_WATER_SPEED_KMH
    assert eta.speed_in_water_kmh(15.0, 45.0) == eta.MIN_WATER_SPEED_KMH   # beyond the curve: ASSUMED floor


def test_dry_route_has_no_flood_delay_and_traffic_is_unknown():
    fc = forecast()
    r = _route(fc)
    b = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0.0))
    assert b["flood_delay_min"] == 0.0
    assert b["eta_min"] == pytest.approx(r["travel_time_min"], abs=0.02)
    labels = {c["key"]: c["label"] for c in b["components"]}
    assert labels == {"base": "ASSUMED ROAD SPEEDS", "flood": "MODELLED FLOOD DELAY", "traffic": "UNKNOWN"}
    traffic = next(c for c in b["components"] if c["key"] == "traffic")
    assert traffic["minutes"] is None and "no live traffic" in traffic["detail"]


def test_shallow_water_adds_a_modelled_delay_and_moves_the_arrival_later():
    fc = forecast(s01=20.0, s12=20.0)                                 # 20 cm: below the truck's 60 cm limit
    r = _route(fc)
    b = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0.0))
    length_km = sum(s.length_m for s in ROADS.segments if s.seg_id in ("s01", "s12")) / 1000.0
    v = eta.pregnolato_speed_kmh(200.0)                                 # ~12.4 km/h, below the ASSUMED 15 km/h
    assert b["flood_delay_min"] == pytest.approx(length_km / v * 60 - length_km / 15.0 * 60, abs=0.02)
    assert b["eta_min"] == pytest.approx(b["free_flow_min"] + b["flood_delay_min"], abs=0.02)
    assert b["arrival_t_min"] == pytest.approx(b["eta_min"], abs=0.02)
    assert b["segments_slowed"] == 2


def test_a_delayed_arrival_is_rechecked_against_the_forecast_at_the_later_minute():
    """Water on s01 slows the truck; s12 floods from T+6, which the free-flow schedule (s12 left by ~T+4.2) misses."""
    fc = forecast(s01=25.0, s12=flood_from(6.0, 80.0))
    r = _route(fc)
    assert [s["seg_id"] for s in r["segments"]] == ["s01", "s12"] and r["valid_at_arrival"]
    b = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0.0))
    changed = {c["seg_id"]: c for c in b["status_changes_when_delayed"]}
    assert changed["s12"]["status_free_flow"] == "USABLE" and changed["s12"]["status_delayed"] == "AT_RISK"


def test_pedestrian_flood_delay_is_not_modelled():
    fc = forecast(s01=20.0)
    r = _route(fc, vehicle="pedestrian")
    b = eta.breakdown(ROADS, G, r["segments"], vehicle="pedestrian", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0.0))
    flood = next(c for c in b["components"] if c["key"] == "flood")
    assert flood["label"] == "NOT MODELLED" and flood["minutes"] is None and b["flood_delay_min"] is None


@pytest.mark.parametrize("mode,age_min,expected", [
    ("SIMULATED scenario", 5, "SIMULATED"),
    ("SYNTHETIC TEST", 5, "SIMULATED"),
    ("LIVE IMD", 5, "MODELLED"),
    ("LIVE IMD", 90, "STALE"),
    (None, 5, "UNKNOWN"),
])
def test_forecast_input_label(mode, age_min, expected):
    meta = None if mode is None else {"data_mode": mode, "_updated_at": (NOW - timedelta(minutes=age_min)).isoformat()}
    assert eta.forecast_input_label(meta, NOW)["label"] == expected


# ----------------------------------------------------------------------------- live traffic in the ETA (2026-10-11)
def _traffic(**by_seg):
    """Traffic in the shape of floodnet/traffic/tomtom.py sample()['segments']."""
    return {k: {"current_kmh": v[0], "free_flow_kmh": v[1], "ratio": v[0] / v[1], "level": v[2], "source": "tomtom"}
            for k, v in by_seg.items()}


def test_live_traffic_scales_only_the_sampled_roads_and_is_a_separate_component():
    fc = forecast()
    r = _route(fc)                                                    # s01 + s12, each ~0.52 km at 15 km/h
    live = _traffic(s01=(10.0, 40.0, "heavy"))                        # s01 moving at a quarter of free flow
    b = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0.0), traffic=live)
    base = next(c for c in b["components"] if c["key"] == "base")
    traffic = next(c for c in b["components"] if c["key"] == "traffic")
    s01 = next(s for s in b["segments"] if s["seg_id"] == "s01")
    s12 = next(s for s in b["segments"] if s["seg_id"] == "s12")
    assert traffic["label"] == "LIVE TRAFFIC" and traffic["minutes"] == pytest.approx(s01["free_min"] * 3, abs=0.02)
    assert s12["traffic_min"] == 0.0 and s12["traffic_level"] is None    # not sampled: no delay invented
    assert s01["traffic_level"] == "heavy"
    assert b["traffic_delay_min"] == traffic["minutes"]
    assert b["eta_min"] == pytest.approx(base["minutes"] + b["flood_delay_min"] + b["traffic_delay_min"], abs=0.02)
    assert b["traffic_sampled"] == 1 and b["traffic_unsampled"] == 1


def test_traffic_and_flood_delays_do_not_double_count_and_both_are_reported():
    fc = forecast(s01=20.0)                                           # water on s01 AND heavy traffic on s01
    r = _route(fc)
    b = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0.0), traffic=_traffic(s01=(20.0, 40.0, "moderate")))
    s01 = next(s for s in b["segments"] if s["seg_id"] == "s01")
    # the slower of the two governs the crossing: flood delay is on the flood speed, traffic delay on the assumed
    # speed; each is the extra over free flow for its own cause, never added on top of the other's slowed time
    assert s01["flood_min"] > 0 and s01["traffic_min"] > 0
    assert s01["min"] == pytest.approx(s01["free_min"] + max(s01["flood_min"], s01["traffic_min"]), abs=0.02)
    assert b["flood_delay_min"] > 0 and b["traffic_delay_min"] > 0
    assert b["eta_min"] == pytest.approx(b["free_flow_min"] + sum(max(s["flood_min"], s["traffic_min"]) for s in b["segments"]), abs=0.05)


def test_without_traffic_the_eta_is_unchanged_and_traffic_stays_unknown():
    fc = forecast()
    r = _route(fc)
    plain = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                          closures=ClosureBook([], 0.0))
    empty = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                          closures=ClosureBook([], 0.0), traffic={})
    assert plain["eta_min"] == empty["eta_min"] and plain["traffic_delay_min"] is None
    assert next(c for c in plain["components"] if c["key"] == "traffic")["label"] == "UNKNOWN"


def test_faster_traffic_than_assumed_never_shortens_the_eta():
    """Live free-flow speeds (40+ km/h) exceed FloodNet's ASSUMED speeds; traffic only ever adds time."""
    fc = forecast()
    r = _route(fc)
    b = eta.breakdown(ROADS, G, r["segments"], vehicle="truck", dispatch_t_min=0.0, forecast=fc, seg_ctx=NO_CTX,
                      closures=ClosureBook([], 0.0), traffic=_traffic(s01=(45.0, 40.0, "normal")))
    assert b["traffic_delay_min"] == 0.0 and b["eta_min"] == pytest.approx(b["free_flow_min"], abs=0.02)
