"""Alert targeting (DECISIONS D-44, docs/LIVE_OPERATIONS.md §6, acceptance criterion 9).
Written before floodnet/messaging/targeting.py existed.

Road ids are REAL segments of the pilot network (OSM); the depth series are SYNTHETIC TEST FIXTURES, not model output.
Subscriber points are REAL coordinates: S1 Dadar TT (floodnet/response/fixtures.py), a road node of the pilot network,
and Rao Wadi (OSM place node, floodnet/messaging/subscribers.PILOT_LOCALITIES)."""
import pytest

from floodnet.access.events import ClosureBook, parse_events
from floodnet.access.road_status import Forecast
from floodnet.api import state
from floodnet.messaging import targeting as tg

KHODADAD_A = "22848363_0"       # Khodadad Circle (trunk)
KHODADAD_B = "1235236198_0"     # Khodadad Circle (trunk)
TILAK = "22848583_0"            # Tilak Road (primary)
VINCENT = "102178069_0"         # Dr Babasaheb Ambedkar Marg (Vincent Road), dry in the fixture

# SYNTHETIC TEST FIXTURE depths (cm)
FORECAST = Forecast((0.0, 15.0, 30.0, 45.0, 60.0, 90.0), {
    KHODADAD_A: [0, 10, 20, 35, 40, 20],
    KHODADAD_B: [0, 5, 16, 18, 18, 10],
    VINCENT: [0, 0, 0, 0, 0, 0],
}, label="SYNTHETIC TEST FIXTURE")
DRY = Forecast((0.0, 30.0, 60.0), {}, label="SYNTHETIC TEST FIXTURE (dry)")

S1 = [72.848752, 19.017141]             # ~83 m from Khodadad Circle
ROAD_NODE = [72.8509328, 19.0177508]    # ~290 m straight line, ~360 m by road from Khodadad Circle
RAO_WADI = [72.841445, 19.031244]       # ~1.6 km away


@pytest.fixture(scope="module")
def roads():
    r = state.get_pilot()["roads"]
    ids = {s.seg_id for s in r.segments}
    missing = {KHODADAD_A, KHODADAD_B, TILAK, VINCENT} - ids
    if missing:
        pytest.skip(f"pilot road network does not contain the REAL test segments {missing} (DEMONSTRATION pilot?)")
    return r


def book(events=(), now=0.0):
    return ClosureBook(parse_events(list(events)), now)


def rec(sid, lonlat, radius, *, channels=("sms",), verified=True, status="ACTIVE"):
    return {"id": sid, "channels": {c: {"consent": {"granted_at": "2026-10-11T00:00:00Z", "method": "test",
                                                    "notice_version": "v"}} for c in channels},
            "verified": verified, "status": status, "opted_out_at": None if status == "ACTIVE" else "2026-10-11T00:00:00Z",
            "area": {"kind": "point", "lonlat": lonlat, "radius_m": radius}}


def test_thresholds_are_labelled():
    assert tg.WATCH_CM == 15 and tg.ADVISORY_CM == 30 and tg.NEARBY_NETWORK_M == 600
    assert "ASSUMED" in tg.THRESHOLD_NOTE and "Pregnolato" in tg.THRESHOLD_NOTE


def test_zone_levels_from_model_depths(roads):
    z = tg.impact_zone(FORECAST, book(), 0.0, 60, roads=roads)
    segs = {s["seg_id"]: s for s in z["segments"]}
    assert set(segs) == {KHODADAD_A, KHODADAD_B}                 # Vincent Road is dry
    assert segs[KHODADAD_A]["max_depth_cm"] == 40 and segs[KHODADAD_A]["basis"] == "MODEL_ESTIMATE"
    assert segs[KHODADAD_A]["name"] == "Khodadad Circle"
    assert z["level"] == "advisory"
    short = tg.impact_zone(FORECAST, book(), 0.0, 20, roads=roads)       # frames 0..30 -> max 20 cm
    assert short["level"] == "watch"
    assert z["localities"] and all(l in tg.LOCALITY_IDS for l in z["localities"])


def test_zone_confirmed_closure_and_reported(roads):
    ev = [{"event_id": "C1", "seg_id": TILAK, "kind": "closure", "received_t_min": 0, "source_tier": "OPERATOR"},
          {"event_id": "P1", "seg_id": VINCENT, "kind": "closure", "received_t_min": 0, "source_tier": "PUBLIC"}]
    z = tg.impact_zone(DRY, book(ev), 0.0, 30, roads=roads)
    segs = {s["seg_id"]: s for s in z["segments"]}
    assert segs[TILAK]["basis"] == "CONFIRMED_CLOSURE" and segs[VINCENT]["basis"] == "REPORTED"
    assert z["level"] == "advisory" and z["closures"] == ["C1"] and z["reported"] == ["P1"]
    only_report = tg.impact_zone(DRY, book(ev[1:]), 0.0, 30, roads=roads)
    assert only_report["level"] == "watch"


def test_zone_empty_and_beyond_horizon(roads):
    assert tg.impact_zone(DRY, book(), 0.0, 30, roads=roads)["level"] is None
    late = tg.impact_zone(FORECAST, book(), 120.0, 30, roads=roads)
    assert late["window"]["beyond_horizon"] is True and late["segments"] == [] and late["level"] is None


def test_signature_is_material(roads):
    a = tg.impact_zone(FORECAST, book(), 0.0, 60, roads=roads)
    b = tg.impact_zone(FORECAST, book(), 0.0, 60, roads=roads)
    c = tg.impact_zone(FORECAST, book(), 0.0, 20, roads=roads)
    assert a["signature"] == b["signature"] and a["signature"] != c["signature"]


def test_decisions_geography_consent_verification_opt_out(roads):
    z = tg.impact_zone(FORECAST, book(), 0.0, 60, roads=roads)
    subs = [rec("direct", S1, 300), rec("nearby", ROAD_NODE, 100), rec("far", RAO_WADI, 300),
            rec("optout", S1, 300, status="OPTED_OUT"), rec("unverified", S1, 300, verified=False),
            rec("wa_only", S1, 300, channels=("whatsapp",))]
    d = {x["subscriber_id"]: x for x in tg.decide(z, subs, channels=("sms",), roads=roads)}
    assert d["direct"]["eligible"] and d["direct"]["match"] == "direct" and d["direct"]["channels"] == ["sms"]
    assert d["direct"]["distance_m"] < 300
    assert d["nearby"]["eligible"] and d["nearby"]["match"] == "nearby" and d["nearby"]["network_m"] <= 600
    assert not d["far"]["eligible"] and "outside" in d["far"]["reason"]
    assert not d["optout"]["eligible"] and "opted out" in d["optout"]["reason"]
    assert not d["unverified"]["eligible"] and "not verified" in d["unverified"]["reason"]
    assert not d["wa_only"]["eligible"] and "consent" in d["wa_only"]["reason"]
    assert all(x["reason"] for x in d.values())


def test_no_zone_means_no_recipients(roads):
    z = tg.impact_zone(DRY, book(), 0.0, 30, roads=roads)
    d = tg.decide(z, [rec("direct", S1, 300)], channels=("sms",), roads=roads)
    assert not d[0]["eligible"]
