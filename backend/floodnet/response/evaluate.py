"""Deterministic replay and evaluation of the response planner (MISC-04 Phase 3; docs/RESPONSE_EVALUATION.md).

Two runs are kept apart on purpose:
  forecast  the run the planner is allowed to see (what it believes will happen);
  outcome   a different run that stands in for what actually happened. Routes are judged against it.
Judging a plan with the run that produced it would make arrival-aware routing look right by construction. With an
imperfect forecast the comparison means something: SIMULATED evidence still, never real-world accuracy.

The replay is scripted and deterministic (DEMO_SCRIPT): at each replan time the simulated world advances (missions
past their planned completion are COMPLETED), the script's events arrive, the planner replans, and a simulated
operator accepts and dispatches every proposal. The same script is run with arrival-aware routing and with the
current-status-only baseline; every number comes from running it, nothing is estimated by hand.

Road-access prediction quality is scored on every road segment, not only on the chosen routes: at each replan time
t and lead L, the arrival-aware predictor classifies the window [t+L, t+L+5] from the forecast, the current-status
predictor classifies the road as it is at t (the "it stays as now" assumption), and both are compared with the
outcome run's status for that window. Blocking = AT_RISK or CLOSED.

Seeded scripts (D-35): generate_script(seed, ...) draws a DEMO_SCRIPT-shaped script from random.Random(seed), so a
scenario is reproducible from its seed alone; evaluate_many() replays several seeds and aggregates; DEV_SEEDS are for
development and tests, HELD_OUT_SEEDS only for the final report (scripts/response_eval.py --held-out).
check_information_availability() re-checks, from the recorded inputs of every replan step, that no decision used a
report received after it was taken. export_rows()/export_csv() turn one evaluation into one row per road x decision
x lead (UNKNOWN truth kept with excluded=True), every row tagged SIMULATED.
"""
from __future__ import annotations

import copy
import csv
import io
import math
import random
import statistics
import time
from collections import Counter

from .. import config
from ..access.events import ClosureBook, RoadEvent
from ..access.road_status import BLOCKING, Forecast, SegmentContext, classify
from ..routing.router import _cached_graph
from .fixtures import demo_resources
from .ledger import UNDERWAY, parse_ledger, parse_response_events
from .planner import Router, plan

DEMO_SCRIPT = {
    "replan_t_min": [0, 10, 30, 60, 90, 120, 150],
    "events": [
        {"t": 10, "kind": "closure_on_underway", "which": 1,
         "note": "confirmed closure of the next road the second dispatched mission would enter after T+10"},
        {"t": 30, "kind": "vehicle_unavailable", "vehicle_id": "V2", "reason": "breakdown (SIMULATED)"},
        {"t": 30, "kind": "shelter_update", "shelter_id": "SH1", "occupancy": 80, "reason": "reported full (SIMULATED)"},
    ],
    "operator": "accepts and dispatches every proposal at each replan; a mission is COMPLETED when its planned "
                "completion time has passed (simulated world)",
    "prediction_lead_min": [5, 15, 30],
    "prediction_vehicle": "truck",
}

GENERATOR_VERSION = "seeded-script-v1"
DEV_SEEDS = range(0, 20)             # development and tests
HELD_OUT_SEEDS = range(100, 120)     # final report only (scripts/response_eval.py --held-out); disjoint from DEV_SEEDS
SOURCE_TIERS = ("OPERATOR", "FIELD_TEAM", "PUBLIC")     # who reported a closure_random (exercises D-32 source tiers)
CLOSURE_KINDS = ("closure_on_underway", "closure_random")
RESOURCE_KINDS = ("vehicle_unavailable", "shelter_update", "settlement_update")
# compact per-case encoding kept in evaluate()["prediction_cases"] (one char per segment and window)
_STATUS_CODE = {"USABLE": "S", "AT_RISK": "A", "UNCERTAIN": "U", "CLOSED": "C", "UNKNOWN": "K"}
_NOT_SCORED = "-"
EXPORT_COLUMNS = ("tag", "seg_id", "sim_t_min", "lead_min", "window_t0_min", "window_t1_min", "t0_utc",
                  "label", "arrival_aware", "current_status", "excluded", "seed", "forecast", "outcome")


def generate_script(seed: int, roads, resources: dict, horizon_t_min: float = 180, *,
                    n_closures: int = 1, n_resource_events: int = 1) -> dict:
    """A DEMO_SCRIPT-shaped script drawn from random.Random(seed): the same seed, roads and resources give an
    identical script. Never runs the planner. Event times are replan times, so replay() processes every event
    (the test suite may shorten `replan_t_min` afterwards; events at dropped times then simply never arrive)."""
    rng = random.Random(int(seed))
    horizon = int(horizon_t_min)
    candidates = list(range(10, horizon + 1, 10))
    n_replans = min(len(candidates), max(2, horizon // 30))
    replan = [0] + sorted(rng.sample(candidates, n_replans))
    later = replan[1:] or replan
    seg_ids = sorted(s.seg_id for s in roads.segments)
    vehicles = sorted(resources["vehicles"], key=lambda r: r["id"])
    shelters = sorted(resources["shelters"], key=lambda r: r["id"])
    settlements = sorted(resources["settlements"], key=lambda r: r["id"])
    events: list[dict] = []
    for _ in range(int(n_closures)):
        t = rng.choice(later)
        if rng.random() < 0.5:
            events.append({"t": t, "kind": "closure_on_underway", "which": rng.randrange(0, 2),
                           "note": "confirmed closure of the next road an underway mission would enter (SIMULATED)"})
        else:
            tier = rng.choice(SOURCE_TIERS)
            events.append({"t": t, "kind": "closure_random", "seg_id": rng.choice(seg_ids), "source_tier": tier,
                           "note": f"closure reported by {tier} (SIMULATED)"})
    for _ in range(int(n_resource_events)):
        t = rng.choice(replan)
        kind = rng.choice(RESOURCE_KINDS)
        if kind == "vehicle_unavailable":
            events.append({"t": t, "kind": kind, "vehicle_id": rng.choice(vehicles)["id"], "reason": "breakdown (SIMULATED)"})
        elif kind == "shelter_update":
            sh = rng.choice(shelters)
            cap = int(sh.get("total_capacity") or 0)
            events.append({"t": t, "kind": kind, "shelter_id": sh["id"], "occupancy": rng.randint(cap // 2, cap) if cap else 0,
                           "reason": "occupancy reported (SIMULATED)"})
        else:
            s = rng.choice(settlements)
            need = int(s.get("people_needing_help") or 0) + rng.choice((-20, -10, 10, 20, 40))
            events.append({"t": t, "kind": kind, "settlement_id": s["id"], "people_needing_help": max(0, need),
                           "reason": "need revised by field report (SIMULATED)"})
    events.sort(key=lambda e: e["t"])                                   # stable: generation order breaks ties
    vehicle_types = sorted({v["type"] for v in vehicles if v["type"] in config.VEHICLE_LIMIT_CM}) or ["truck"]
    return {"replan_t_min": replan, "events": events, "operator": DEMO_SCRIPT["operator"],
            "prediction_lead_min": [L for L in DEMO_SCRIPT["prediction_lead_min"] if L < horizon] or [5],
            "prediction_vehicle": rng.choice(vehicle_types),
            "seed": int(seed), "generator_version": GENERATOR_VERSION, "horizon_t_min": horizon,
            "tag": "SIMULATED scripted scenario drawn from random.Random(seed); events are injected, never observed"}


def _known(resources: dict) -> dict:
    known = {k: {r["id"] for r in resources[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
    known.update({"vehicle_id": known["vehicles"], "shelter_id": known["shelters"], "settlement_id": known["settlements"]})
    return known


def _road_event(i: int, seg_id: str, t: float, note: str, source: str = "scripted replay (SIMULATED)",
                source_tier: str | None = None) -> RoadEvent:
    # `source_tier` is only passed on when RoadEvent knows the field (D-32 adds it); otherwise it lives in `source`
    extra = {"source_tier": source_tier} if source_tier is not None and "source_tier" in RoadEvent.__dataclass_fields__ else {}
    return RoadEvent(event_id=f"C{i}", seg_id=seg_id, kind="closure", received_t_min=t, applies_from_t_min=t,
                     source=source, note=note, **extra)


def replay(roads, seg_ctx: SegmentContext, forecast: Forecast, outcome: Forecast, mode: str,
           script: dict = DEMO_SCRIPT) -> dict:
    """One scripted replay with one routing method. Returns the steps, the final ledger and its metrics."""
    resources = demo_resources()
    known = _known(resources)
    G = _cached_graph(roads)
    horizon = min(forecast.horizon_t_min, outcome.horizon_t_min)
    ledger: list[dict] = []
    road_events: list[RoadEvent] = []
    resp_events_raw: list[dict] = []
    version, steps, replan_ms = 0, [], []
    for t in [x for x in script["replan_t_min"] if x <= horizon]:
        # 1. the simulated world advances
        # (the simulated operator dispatches a mission when its planned departure is due; it reaches the settlement
        # and completes at its planned times -- the outcome run judges afterwards whether that was really possible)
        for a in ledger:
            depart = a["legs"][0]["depart_t_min"] if a.get("legs") else None
            at_site = next((l["arrive_t_min"] for l in a.get("legs", []) if l["kind"] == "to_settlement"), depart)
            done = a["legs"][-1]["done_t_min"] if a.get("legs") else None
            for status, when in (("DISPATCHED", depart), ("IN_PROGRESS", at_site), ("COMPLETED", done)):
                prev = {"DISPATCHED": "ACCEPTED", "IN_PROGRESS": "DISPATCHED", "COMPLETED": "IN_PROGRESS"}[status]
                if a["status"] == prev and when is not None and when <= t:
                    a["status"] = status
                    a["history"] = a["history"] + [{"status": status, "t_min": when, "reason": "replay: planned time passed", "by": "replay"}]
        # 2. scripted events arriving now
        arrived = []
        for e in script["events"]:
            if e["t"] != t:
                continue
            if e["kind"] == "closure_on_underway":
                underway = [a for a in ledger if a["status"] in UNDERWAY]
                target = underway[e["which"]] if len(underway) > e["which"] else None
                seg = next((s for a in ([target] if target else []) for leg in a["legs"] for s in leg["segments"] if s["enter_t_min"] > t), None)
                if seg is not None:
                    road_events.append(_road_event(len(road_events) + 1, seg["seg_id"], t, e["note"]))
                    arrived.append(f"closure {road_events[-1].event_id} on {seg['name'] or seg['seg_id']} ({target['assignment_id']} would enter it at T+{seg['enter_t_min']:.1f})")
                else:
                    arrived.append("closure_on_underway: no underway mission with a road still ahead; skipped")
            elif e["kind"] == "closure_random":
                tier = e.get("source_tier")
                road_events.append(_road_event(len(road_events) + 1, str(e["seg_id"]), t, e.get("note", ""),
                                               source=f"{tier or 'scripted'} report (SIMULATED)", source_tier=tier))
                arrived.append(f"closure {road_events[-1].event_id} on {e['seg_id']} reported by {tier or 'script'}")
            else:
                resp_events_raw.append({**{k: v for k, v in e.items() if k != "t"}, "received_t_min": t, "event_id": f"E{len(resp_events_raw) + 1}"})
                arrived.append(f"{e['kind']} {e.get('vehicle_id') or e.get('shelter_id') or e.get('settlement_id')}: {e.get('reason', '')}")
        # 3. replan
        book = ClosureBook(road_events, t)
        rt = Router(roads, G, forecast, seg_ctx, book, mode)
        t0 = time.perf_counter()
        out = plan(resources, parse_response_events(resp_events_raw, known), parse_ledger(copy.deepcopy(ledger), known), rt, t, version)
        replan_ms.append(round((time.perf_counter() - t0) * 1000, 1))
        version = out["plan_version"]
        ledger = out["assignments"]
        # 4. the simulated operator accepts every proposal and dispatches the ones due to leave now
        n_disp = 0
        for a in ledger:
            if a["status"] == "PROPOSED":
                a["status"] = "ACCEPTED"
                a["history"] = a["history"] + [{"status": "ACCEPTED", "t_min": t, "by": "replay"}]
                if a["legs"][0]["depart_t_min"] <= t + 1e-9:
                    a["status"] = "DISPATCHED"
                    a["history"] = a["history"] + [{"status": "DISPATCHED", "t_min": t, "by": "replay"}]
                n_disp += 1
        s = out["summary"]
        # what this decision was actually given (checked afterwards by check_information_availability)
        received = [e.received_t_min for e in book.used] + [e["received_t_min"] for e in resp_events_raw]
        steps.append({"t_min": t, "plan_version": version, "events": arrived, "dispatched": n_disp,
                      "served": s["served"], "committed": s["covered_committed"] + s["covered_proposed"], "unmet": s["unmet"],
                      "changes": [c for c in out["changes"] if c["change"] != "proposed"],
                      "invariant_violations": out["invariant_violations"],
                      "inputs_road_event_ids": [e.event_id for e in book.used],
                      "inputs_response_event_ids": [e["event_id"] for e in resp_events_raw],
                      "inputs_max_received_t_min": max(received) if received else None,
                      "replan_ms": replan_ms[-1]})
    return {"mode": mode, "steps": steps, "ledger": ledger, "road_events": [e.to_dict() for e in road_events],
            "response_events": resp_events_raw, "replan_ms": replan_ms,
            "metrics": _mission_metrics(ledger, road_events, outcome, seg_ctx, horizon, replan_ms, steps)}


def _mission_metrics(ledger, road_events, outcome: Forecast, ctx: SegmentContext, horizon: float, replan_ms, steps) -> dict:
    """Judge every dispatched mission's legs against the OUTCOME run at the planned times."""
    all_closures = ClosureBook(road_events, horizon + 1e9)           # everything that was ever reported
    vehicles = {v["id"]: v for v in demo_resources()["vehicles"]}
    legs_total = legs_invalid = missions_invalid = 0
    closure_hits = closure_known_violations = 0
    blocked_examples = []
    first_service: dict[str, float] = {}
    last_done = 0.0
    served = Counter()
    for a in ledger:
        if a["status"] == "CANCELLED":
            continue
        limit = float(config.VEHICLE_LIMIT_CM[vehicles[a["vehicle_id"]]["type"]])
        decision_t = next((h["t_min"] for h in a["history"] if h["status"] == "DISPATCHED"), a["legs"][0]["depart_t_min"])
        known_then = ClosureBook([e for e in all_closures.used], decision_t)
        bad_mission = False
        for leg in a["legs"]:
            legs_total += 1
            bad_leg = False
            for s in leg["segments"]:
                st = classify(s["seg_id"], s["enter_t_min"], s["exit_t_min"], forecast=outcome, ctx=ctx, closures=all_closures, limit_cm=limit)
                if st["status"] in BLOCKING:
                    bad_leg = True
                    if st["status"] == "CLOSED":
                        closure_hits += 1
                        if known_then.closed_during(s["seg_id"], s["enter_t_min"], s["exit_t_min"]):
                            closure_known_violations += 1
                    if len(blocked_examples) < 8:
                        blocked_examples.append({"assignment_id": a["assignment_id"], "leg": leg["kind"], "road": s["name"] or s["seg_id"],
                                                 "enter_t_min": s["enter_t_min"], "outcome_status": st["status"], "outcome_depth_cm": st["depth_cm"],
                                                 "planned_status": s["status"], "planned_depth_cm": s["depth_cm"]})
            legs_invalid += bad_leg
            bad_mission = bad_mission or bad_leg
            if leg["kind"] == "to_settlement":
                first_service[a["settlement_id"]] = min(first_service.get(a["settlement_id"], 1e9), leg["arrive_t_min"])
        missions_invalid += bad_mission
        if a["status"] == "COMPLETED":
            served[a["settlement_id"]] += a["people"]
            last_done = max(last_done, a["legs"][-1]["done_t_min"])
    final = steps[-1] if steps else {}
    return {"missions_dispatched": sum(1 for a in ledger if a["status"] != "CANCELLED"),
            "missions_completed_by_horizon": sum(1 for a in ledger if a["status"] == "COMPLETED"),
            "missions_cancelled_by_planner": sum(1 for a in ledger if a["status"] == "CANCELLED" and a["history"][-1].get("by") == "planner"),
            "people_served_by_horizon": sum(served.values()), "people_unmet_at_end": final.get("unmet"),
            "legs_total": legs_total, "legs_invalid_by_outcome": legs_invalid, "missions_invalid_by_outcome": missions_invalid,
            "closures_encountered": closure_hits, "known_closure_violations": closure_known_violations,
            "first_service_t_min": {k: round(v, 1) for k, v in sorted(first_service.items())},
            "last_completion_t_min": round(last_done, 1),
            "replans": len(replan_ms), "replan_ms_mean": round(sum(replan_ms) / max(len(replan_ms), 1), 1), "replan_ms_max": max(replan_ms, default=0),
            "invariant_violations_total": sum(len(s["invariant_violations"]) for s in steps),
            "blocked_examples": blocked_examples}


def prediction_quality(roads, ctx: SegmentContext, forecast: Forecast, outcome: Forecast, road_events: list[dict],
                       replan_t_min, leads, vehicle: str) -> dict:
    """Confusion of blocking / passable on every segment, arrival-aware vs current-status predictor vs outcome."""
    limit = float(config.VEHICLE_LIMIT_CM[vehicle])
    events = [RoadEvent(**e) for e in road_events]
    horizon = min(forecast.horizon_t_min, outcome.horizon_t_min)
    res = {"arrival_aware": Counter(), "current_status": Counter()}
    warned = Counter()
    n_unknown = 0
    seg_ids = [s.seg_id for s in roads.segments]
    windows = []
    for t in replan_t_min:
        book = ClosureBook(events, t)
        truth_book = ClosureBook(events, horizon + 1e9)
        for L in leads:
            t0, t1 = t + L, t + L + 5
            if t1 > horizon:
                continue
            codes = {"truth": [], "arrival_aware": [], "current_status": []}
            windows.append({"sim_t_min": t, "lead_min": L, "window_t0_min": t0, "window_t1_min": t1, **codes})
            for sid in seg_ids:
                truth = classify(sid, t0, t1, forecast=outcome, ctx=ctx, closures=truth_book, limit_cm=limit)
                if truth["status"] == "UNKNOWN":
                    n_unknown += 1
                    codes["truth"].append(_STATUS_CODE["UNKNOWN"])
                    codes["arrival_aware"].append(_NOT_SCORED)
                    codes["current_status"].append(_NOT_SCORED)
                    continue
                truth_block = truth["status"] in BLOCKING
                p_arr = classify(sid, t0, t1, forecast=forecast, ctx=ctx, closures=book, limit_cm=limit)
                p_cur = classify(sid, t, t, forecast=forecast, ctx=ctx, closures=book, limit_cm=limit)
                codes["truth"].append(_STATUS_CODE.get(truth["status"], "?"))
                codes["arrival_aware"].append(_STATUS_CODE.get(p_arr["status"], "?"))
                codes["current_status"].append(_STATUS_CODE.get(p_cur["status"], "?"))
                for name, p in (("arrival_aware", p_arr), ("current_status", p_cur)):
                    pb = p["status"] in BLOCKING
                    res[name]["hits" if pb and truth_block else "false_alarms" if pb else "misses" if truth_block else "correct_passable"] += 1
                if truth_block:
                    warned["outcome_blocking"] += 1
                    if p_arr["status"] in ("AT_RISK", "CLOSED", "UNCERTAIN"):
                        warned["flagged_by_arrival_aware"] += 1
                    if p_cur["status"] in ("AT_RISK", "CLOSED", "UNCERTAIN"):
                        warned["flagged_by_current_status"] += 1
            for k in codes:
                windows[-1][k] = "".join(codes[k])

    def score(c: Counter) -> dict:
        n = sum(c.values())
        h, m, fa = c["hits"], c["misses"], c["false_alarms"]
        return {**{k: c[k] for k in ("hits", "misses", "false_alarms", "correct_passable")}, "cases": n,
                "accuracy": round((h + c["correct_passable"]) / n, 4) if n else None,
                "probability_of_detection": round(h / (h + m), 4) if h + m else None,
                "false_alarm_ratio": round(fa / (h + fa), 4) if h + fa else None}
    return {"vehicle": vehicle, "vehicle_limit_cm": limit, "replan_t_min": list(replan_t_min), "lead_min": list(leads),
            "segments": len(seg_ids), "unknown_cases_excluded": n_unknown,
            "arrival_aware": score(res["arrival_aware"]), "current_status": score(res["current_status"]),
            "outcome_blocking_cases": warned["outcome_blocking"],
            "share_of_blocking_flagged_incl_uncertain": {
                "arrival_aware": round(warned["flagged_by_arrival_aware"] / warned["outcome_blocking"], 4) if warned["outcome_blocking"] else None,
                "current_status": round(warned["flagged_by_current_status"] / warned["outcome_blocking"], 4) if warned["outcome_blocking"] else None},
            # every case, one character per segment and window (export_rows expands it); evaluate() moves it to
            # the top-level key "prediction_cases" so the summary above stays small
            "cases": {"segment_ids": seg_ids, "legend": {**{v: k for k, v in _STATUS_CODE.items()}, _NOT_SCORED: "not scored (truth UNKNOWN)"},
                      "windows": windows}}


def evaluate(roads, seg_ctx: SegmentContext, forecast: Forecast, outcome: Forecast, script: dict = DEMO_SCRIPT) -> dict:
    runs = {mode: replay(roads, seg_ctx, forecast, outcome, mode, script) for mode in ("arrival", "current")}
    pred = prediction_quality(roads, seg_ctx, forecast, outcome, runs["arrival"]["road_events"],
                              [t for t in script["replan_t_min"] if t <= min(forecast.horizon_t_min, outcome.horizon_t_min)],
                              script["prediction_lead_min"], script["prediction_vehicle"])
    cases = pred.pop("cases")
    a, c = runs["arrival"]["metrics"], runs["current"]["metrics"]
    comparison = {k: {"arrival_aware": a[k], "current_status": c[k]} for k in
                  ("people_served_by_horizon", "people_unmet_at_end", "missions_dispatched", "legs_invalid_by_outcome",
                   "missions_invalid_by_outcome", "closures_encountered", "known_closure_violations", "last_completion_t_min",
                   "replan_ms_mean", "invariant_violations_total")}
    comparison["response_delay_min"] = {s: round(c["first_service_t_min"].get(s, 0) - a["first_service_t_min"].get(s, 0), 1)
                                        for s in sorted(set(a["first_service_t_min"]) | set(c["first_service_t_min"]))
                                        if s in a["first_service_t_min"] and s in c["first_service_t_min"]}
    return {"tag": "SIMULATED replay; forecast and outcome are two FloodNet runs, not observations",
            "forecast": forecast.label, "outcome": outcome.label, "script": script,
            "modes": {"arrival": {k: v for k, v in runs["arrival"].items() if k != "ledger"},
                      "current": {k: v for k, v in runs["current"].items() if k != "ledger"}},
            "ledgers": {m: r["ledger"] for m, r in runs.items()},
            "prediction": pred, "prediction_cases": cases, "comparison": comparison,
            "same_run": forecast.streets_cm is outcome.streets_cm or forecast.label == outcome.label}


# ----------------------------------------------------------------------------- D-35: seeds, checks and exports
def strip_timing(result):
    """A copy without wall-clock fields (replan_ms*, processed_at, generated_at), recursively, so two evaluations of
    the same seed can be compared byte for byte."""
    if isinstance(result, dict):
        return {k: strip_timing(v) for k, v in result.items()
                if not (str(k).startswith("replan_ms") or k in ("processed_at", "generated_at"))}
    if isinstance(result, list):
        return [strip_timing(v) for v in result]
    return result


def check_information_availability(result: dict) -> dict:
    """Did any replan use a report received after the decision time? Checked from what each step RECORDED as its
    inputs (ids and max received_t_min), against the events as stored in the result, from both sides: an input
    received after the step, a recorded maximum later than the step, a recorded maximum that disagrees with the
    listed inputs, or an event received by the step that the planner was not given. A step without an input record
    is itself a violation (the check must never pass vacuously)."""
    violations: list[dict] = []
    eps = 1e-9
    if not isinstance(result.get("forecast"), str) or not result.get("forecast"):
        violations.append({"where": "forecast", "problem": "no single issued forecast label recorded for the replay"})
    for mode, run in (result.get("modes") or {}).items():
        road = {e["event_id"]: e for e in run.get("road_events", [])}
        resp = {e["event_id"]: e for e in run.get("response_events", [])}
        prev_t = None
        for i, step in enumerate(run.get("steps", [])):
            t = float(step["t_min"])
            where = {"mode": mode, "step": i, "t_min": t}
            if prev_t is not None and t < prev_t:
                violations.append({**where, "problem": f"replan steps out of order (previous T+{prev_t})"})
            prev_t = t
            if any(k not in step for k in ("inputs_road_event_ids", "inputs_response_event_ids", "inputs_max_received_t_min")):
                violations.append({**where, "problem": "step carries no record of the inputs it was given"})
                continue
            used_received = []
            for label, ids, book in (("road", step["inputs_road_event_ids"], road), ("response", step["inputs_response_event_ids"], resp)):
                for eid in ids:
                    e = book.get(eid)
                    if e is None:
                        violations.append({**where, "problem": f"{label} event {eid} used by the step is not in the result"})
                        continue
                    used_received.append(float(e["received_t_min"]))
                    if float(e["received_t_min"]) > t + eps:
                        violations.append({**where, "event_id": eid, "received_t_min": e["received_t_min"],
                                           "problem": f"{label} event {eid} received at T+{e['received_t_min']} was used by the decision at T+{t}"})
                for eid, e in book.items():
                    if float(e["received_t_min"]) <= t + eps and eid not in ids:
                        violations.append({**where, "event_id": eid, "problem": f"{label} event {eid} received by T+{t} was withheld from the planner"})
            recorded = step["inputs_max_received_t_min"]
            if recorded is not None and float(recorded) > t + eps:
                violations.append({**where, "inputs_max_received_t_min": recorded,
                                   "problem": f"inputs received up to T+{recorded} were used by the decision at T+{t}"})
            actual = max(used_received) if used_received else None
            if (recorded is None) != (actual is None) or (actual is not None and abs(float(recorded) - actual) > eps):
                violations.append({**where, "inputs_max_received_t_min": recorded, "recomputed": actual,
                                   "problem": "recorded max received_t_min of the inputs disagrees with the listed inputs"})
    return {"ok": not violations, "violations": violations}


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _flat_metrics(out: dict) -> dict:
    """{metric name: number} for one evaluation; timing (replan_ms*) and non-numeric entries are left out."""
    flat: dict = {}
    for k, v in out["comparison"].items():
        if k.startswith("replan_ms") or not isinstance(v, dict):
            continue
        for sub, x in v.items():
            if _is_number(x):
                flat[f"{k}.{sub}"] = x
    pq = out["prediction"]
    for k in ("unknown_cases_excluded", "outcome_blocking_cases"):
        if _is_number(pq.get(k)):
            flat[f"prediction.{k}"] = pq[k]
    for name in ("arrival_aware", "current_status"):
        for k, x in pq[name].items():
            if _is_number(x):
                flat[f"prediction.{name}.{k}"] = x
        x = pq["share_of_blocking_flagged_incl_uncertain"].get(name)
        if _is_number(x):
            flat[f"prediction.share_of_blocking_flagged_incl_uncertain.{name}"] = x
    return flat


def evaluate_many(roads, seg_ctx: SegmentContext, forecast: Forecast, outcome: Forecast, seeds, **script_kwargs) -> dict:
    """evaluate() once per seed with generate_script(seed, ..., **script_kwargs); the aggregate covers every metric
    present for all seeds (std = population standard deviation over the seeds). Nothing is cached or estimated."""
    script_kwargs.setdefault("horizon_t_min", int(min(forecast.horizon_t_min, outcome.horizon_t_min)))
    resources = demo_resources()
    per_seed, violations = [], []
    for seed in seeds:
        script = generate_script(seed, roads, resources, **script_kwargs)
        out = evaluate(roads, seg_ctx, forecast, outcome, script)
        chk = check_information_availability(out)
        violations += [{"seed": int(seed), **v} for v in chk["violations"]]
        per_seed.append({"seed": int(seed), "script": script, "comparison": out["comparison"], "prediction": out["prediction"],
                         "metrics": _flat_metrics(out), "information_check": chk, "modes": out["modes"]})
    names = [n for n in (per_seed[0]["metrics"] if per_seed else {}) if all(n in p["metrics"] for p in per_seed)]
    aggregate = {}
    for n in names:
        vals = [float(p["metrics"][n]) for p in per_seed]
        aggregate[n] = {"n": len(vals), "mean": statistics.fmean(vals), "min": min(vals), "max": max(vals),
                        "std": statistics.pstdev(vals)}
    return {"tag": "SIMULATED multi-seed replay; forecast and outcome are two FloodNet runs, not observations",
            "forecast": forecast.label, "outcome": outcome.label, "seeds": [int(s) for s in seeds],
            "generator_version": GENERATOR_VERSION, "script_kwargs": script_kwargs,
            "per_seed": per_seed, "aggregate": aggregate, "std_definition": "population standard deviation over seeds",
            "information_check": {"ok": not violations, "violations": violations},
            "same_run": forecast.streets_cm is outcome.streets_cm or forecast.label == outcome.label}


def export_rows(evaluation: dict) -> list[dict]:
    """One row per road x decision time x lead from evaluate()["prediction_cases"]. Rows whose outcome status is
    UNKNOWN keep label None and excluded=True (they are counted in prediction.unknown_cases_excluded, never scored).
    `t0_utc` is filled only when the evaluation carries one (none of today's forecasts does)."""
    cases = evaluation.get("prediction_cases")
    if not cases:
        raise ValueError("evaluation carries no prediction_cases (API responses drop them unless include_ledgers is set); "
                         "call evaluate() directly")
    decode = {v: k for k, v in _STATUS_CODE.items()}
    script = evaluation.get("script") or {}
    common = {"tag": "SIMULATED", "t0_utc": evaluation.get("t0_utc"), "seed": script.get("seed"),
              "forecast": evaluation.get("forecast"), "outcome": evaluation.get("outcome")}
    rows = []
    for w in cases["windows"]:
        for i, sid in enumerate(cases["segment_ids"]):
            truth = w["truth"][i]
            excluded = truth == _STATUS_CODE["UNKNOWN"]
            rows.append({**common, "seg_id": sid, "sim_t_min": w["sim_t_min"], "lead_min": w["lead_min"],
                         "window_t0_min": w["window_t0_min"], "window_t1_min": w["window_t1_min"],
                         "label": None if excluded else decode.get(truth),
                         "arrival_aware": None if excluded else decode.get(w["arrival_aware"][i]),
                         "current_status": None if excluded else decode.get(w["current_status"][i]),
                         "excluded": excluded})
    return rows


def export_csv(rows: list[dict]) -> str:
    """CSV with the fixed EXPORT_COLUMNS header (tag first, always SIMULATED); None becomes an empty field."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: "" if r.get(k) is None else r.get(k) for k in EXPORT_COLUMNS})
    return buf.getvalue()
