"""TomTom Flow Segment Data fetcher (floodnet/traffic/tomtom.py). Every HTTP call is FAKED: no test here reaches the
network, and no test reads the developer's .env (the key, when needed, is a fake set via monkeypatch)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.traffic import tomtom

FAKE_KEY = "fake-tomtom-key-ABC123"
T0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
PTS = [("s01", 72.8425, 19.010), ("s12", 72.8475, 19.010), ("n34", 72.8425, 19.015)]


def flow(current, free, confidence=0.9, closure=False):
    """A TomTom-shaped body (field names from the Flow Segment Data v4 doc)."""
    d = {"frc": "FRC5", "confidence": confidence, "roadClosure": closure,
         "coordinates": {"coordinate": [{"latitude": 19.01, "longitude": 72.84}]}}
    if current is not None:
        d["currentSpeed"] = current
        d["currentTravelTime"] = 100
    if free is not None:
        d["freeFlowSpeed"] = free
        d["freeFlowTravelTime"] = 60
    return json.dumps({"flowSegmentData": d})


class FakeHTTP:
    """Returns (status, body) per call; records what it was asked (so we can assert the key is never in the URL)."""

    def __init__(self, by_point=None, default=(200, flow(30, 40))):
        self.by_point, self.default, self.calls = by_point or {}, default, []

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params), timeout))
        r = self.by_point.get(params["point"], self.default)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    tomtom.clear_cache()
    yield
    tomtom.clear_cache()


def test_missing_key_is_unavailable_with_a_plain_note_and_no_http_call():
    http = FakeHTTP()
    out = tomtom.sample(PTS, http_get=http, now=T0)
    assert out["mode"] == "UNAVAILABLE" and out["provider"] == "tomtom"
    assert out["note"] == "Live traffic unavailable — TOMTOM_API_KEY not configured; using estimated travel time"
    assert out["segments"] == {} and http.calls == []
    st = tomtom.status()
    assert st["configured"] is False and st["mode"] == "UNAVAILABLE" and st["cache_ttl_s"] == 60


def test_live_levels_and_ratios_from_fake_responses(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    http = FakeHTTP(by_point={"19.01,72.8425": (200, flow(36, 40)),            # ratio 0.9 -> normal
                              "19.01,72.8475": (200, flow(24, 40, 0.7)),       # 0.6 -> moderate
                              "19.015,72.8425": (200, flow(10, 40, closure=True))})  # 0.25 -> heavy
    out = tomtom.sample(PTS, http_get=http, now=T0)
    assert out["mode"] == "LIVE" and out["failures"] == 0 and out["ttl_s"] == 60
    assert out["fetched_at"] == T0.isoformat()
    s = out["segments"]
    assert s["s01"]["level"] == "normal" and s["s01"]["ratio"] == 0.9 and s["s01"]["current_kmh"] == 36
    assert s["s12"]["level"] == "moderate" and s["s12"]["confidence"] == 0.7
    assert s["n34"]["level"] == "heavy" and s["n34"]["road_closure"] is True
    assert all(v["source"] == "tomtom" and v["from_cache"] is False and v["sampled_at"] == T0.isoformat()
               for v in s.values())
    assert len(http.calls) == 3 and all(t == 4.0 for _, _, t in http.calls)
    assert all(p["unit"] == "kmph" for _, p, _ in http.calls)      # doc value is lower-case; params case-sensitive
    assert tomtom.status()["mode"] == "LIVE" and tomtom.status()["cached_segments"] == 3


def test_missing_or_zero_speeds_give_level_unknown_never_a_guess(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    http = FakeHTTP(by_point={"19.01,72.8425": (200, flow(None, None)), "19.01,72.8475": (200, flow(20, 0))})
    out = tomtom.sample(PTS[:2], http_get=http, now=T0)
    assert out["mode"] == "LIVE"
    assert out["segments"]["s01"]["level"] == "unknown" and out["segments"]["s01"]["ratio"] is None
    assert out["segments"]["s01"]["current_kmh"] is None
    assert out["segments"]["s12"]["level"] == "unknown" and out["segments"]["s12"]["ratio"] is None


def test_cache_hit_within_60s_then_expiry_and_refresh_bypass(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    http = FakeHTTP()
    tomtom.sample(PTS[:1], http_get=http, now=T0)
    again = tomtom.sample(PTS[:1], http_get=http, now=T0 + timedelta(seconds=59))
    assert len(http.calls) == 1 and again["segments"]["s01"]["from_cache"] is True
    assert again["segments"]["s01"]["sampled_at"] == T0.isoformat() and again["mode"] == "LIVE"
    tomtom.sample(PTS[:1], http_get=http, now=T0 + timedelta(seconds=61))
    assert len(http.calls) == 2
    tomtom.sample(PTS[:1], http_get=http, now=T0 + timedelta(seconds=62), refresh=True)
    assert len(http.calls) == 3


def test_timeout_invalid_json_and_http_500_are_skipped_and_counted(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    http = FakeHTTP(by_point={"19.01,72.8425": TimeoutError("slow"), "19.01,72.8475": (200, "<html>not json"),
                              "19.015,72.8425": (500, "boom")},
                    default=(200, flow(30, 40)))
    pts = PTS + [("n45", 72.8475, 19.015)]
    out = tomtom.sample(pts, http_get=http, now=T0)
    assert out["mode"] == "LIVE" and out["failures"] == 3           # partial failure keeps mode LIVE
    assert set(out["segments"]) == {"n45"}
    assert "3 of 4" in out["note"]


def test_every_call_failing_is_unavailable(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    out = tomtom.sample(PTS, http_get=FakeHTTP(default=(403, "forbidden")), now=T0)
    assert out["mode"] == "UNAVAILABLE" and out["failures"] == 3 and out["segments"] == {}
    assert "estimated travel time" in out["note"]


def test_more_than_40_points_refused(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    http = FakeHTTP()
    with pytest.raises(ValueError):
        tomtom.sample([(f"s{i}", 72.84, 19.01) for i in range(41)], http_get=http, now=T0)
    assert http.calls == []


def test_key_never_appears_in_results_or_the_request_url(monkeypatch):
    monkeypatch.setenv("TOMTOM_API_KEY", FAKE_KEY)
    http = FakeHTTP(default=(403, "forbidden " + FAKE_KEY))
    out = tomtom.sample(PTS, http_get=http, now=T0)
    assert FAKE_KEY not in json.dumps(out) and FAKE_KEY not in json.dumps(tomtom.status())
    assert all(FAKE_KEY not in url for url, _, _ in http.calls)   # the key travels as a query param, not in the URL
    assert all(p["key"] == FAKE_KEY for _, p, _ in http.calls)
