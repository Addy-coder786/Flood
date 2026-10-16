"""The live-operations store (DECISIONS D-38): one SQLite file, versioned documents, an append-only audit trail,
idempotent inserts, and an incident clock. Written before floodnet/ops/store.py existed."""
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.ops import clock as ck
from floodnet.ops.store import OpsStore, OpsUnavailable, open_store


@pytest.fixture
def store(tmp_path):
    return OpsStore(tmp_path / "ops.sqlite3")


def test_put_get_versions_and_audit(store):
    a = store.put("trips", "TR1", {"stage": "ASSIGNED"}, actor="dispatcher", action="create")
    assert a["_version"] == 1 and a["stage"] == "ASSIGNED"
    b = store.put("trips", "TR1", {**a, "stage": "EN_ROUTE"}, actor="driver:TR1", action="stage")
    assert b["_version"] == 2
    assert store.get("trips", "TR1")["stage"] == "EN_ROUTE"
    trail = store.audit("trips", "TR1")
    assert [t["action"] for t in trail] == ["create", "stage"]
    assert trail[1]["actor"] == "driver:TR1" and trail[1]["body"]["stage"] == "EN_ROUTE"


def test_audit_is_append_only_and_survives_reopen(tmp_path):
    p = tmp_path / "ops.sqlite3"
    s = OpsStore(p)
    s.put("alerts", "AL1", {"status": "DRAFT"}, actor="dispatcher", action="create")
    s.put("alerts", "AL1", {"status": "APPROVED"}, actor="dispatcher", action="approve")
    s2 = OpsStore(p)
    assert s2.get("alerts", "AL1")["status"] == "APPROVED"
    assert len(s2.audit("alerts", "AL1")) == 2


def test_expected_version_guards_lost_updates(store):
    store.put("trips", "TR1", {"stage": "ASSIGNED"}, actor="d", action="create")
    with pytest.raises(ValueError, match="version"):
        store.put("trips", "TR1", {"stage": "X"}, actor="d", action="x", expected_version=5)


def test_insert_unique_is_idempotent(store):
    r1, created1 = store.insert_unique("deliveries", "K1", {"status": "QUEUED"}, actor="system", action="queue")
    r2, created2 = store.insert_unique("deliveries", "K1", {"status": "SOMETHING ELSE"}, actor="system", action="queue")
    assert created1 is True and created2 is False
    assert r2["status"] == "QUEUED"
    assert len(store.list("deliveries")) == 1


def test_list_filters_and_sequences(store):
    for i in range(3):
        store.put("trips", f"TR{i}", {"n": i}, actor="d", action="create")
    assert [d["n"] for d in store.list("trips", lambda d: d["n"] >= 1)] == [1, 2]
    assert store.next_seq("road_event") == 1
    assert store.next_seq("road_event") == 2


def test_open_store_refuses_on_vercel_without_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("FLOODNET_OPS_ALLOW_EPHEMERAL", raising=False)
    with pytest.raises(OpsUnavailable, match="persistent"):
        open_store(tmp_path / "x.sqlite3")


def test_clock_replay_is_frozen_and_live_advances(store):
    t0 = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    ck.set_clock(store, "REPLAY", 40.0, now=t0)
    assert ck.now_t_min(store, now=t0 + timedelta(minutes=10)) == 40.0
    ck.set_clock(store, "LIVE", 40.0, now=t0)
    assert ck.now_t_min(store, now=t0 + timedelta(minutes=10)) == pytest.approx(50.0)
    with pytest.raises(ValueError):
        ck.set_clock(store, "SOMETIMES", 1.0, now=t0)


def test_clock_defaults_to_replay_at_zero(store):
    c = ck.get_clock(store)
    assert c["mode"] == "REPLAY" and c["t_min"] == 0.0
