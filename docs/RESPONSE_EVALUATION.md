# Replay and evaluation (MISC-04 Phase 3)

**Status (2026-10-10): implemented; evidence below comes from running it.** SIMULATED throughout: the "outcome" is
another FloodNet run, not observed conditions. These numbers show how the two routing methods differ under an
imperfect forecast; they are not a real-world accuracy claim (see `VALIDATION.md`). Decision: `DECISIONS.md` D-30.

## Why two runs

Judging a plan with the same run that produced its forecast makes arrival-aware routing right by construction. So the
planner **plans on one run** (the forecast it is allowed to see) and **is judged against a different run** (the
outcome). The Phase 1 scan (`ROAD_ACCESS.md`) is the same-run sanity check; this is the fair comparison.

## The scripted replay (`floodnet/response/evaluate.py::DEMO_SCRIPT`)

Deterministic; identical inputs give identical output (tested). Replans at T+0, 10, 30, 60, 90, 120, 150.

| When | What happens |
|---|---|
| each replan | the simulated operator accepts every proposal and dispatches those due to leave; missions reach the settlement and complete at their planned times |
| T+10 | a confirmed closure of the next road the second dispatched mission would enter |
| T+30 | vehicle V2 reported unavailable (breakdown); shelter SH1 reported full |

The same script runs twice: with **arrival-aware** routing and with the **current-status-only** baseline.

## What is measured

- **Route validity by outcome:** every dispatched mission's roads re-checked against the outcome run at the planned
  minutes; a leg is invalid if any road is AT_RISK or CLOSED then. Known-closure violations (a closure already
  reported at decision time, still used) are counted separately — target 0.
- **Response:** people served by the horizon, unmet at the end, first-service time per settlement, last completion.
- **Planner soundness:** double-booking + shelter overflow across all replans (target 0), replan time.
- **Road-access prediction quality on all 2,978 roads**, not only on chosen routes: at each replan time t and lead
  L ∈ {5, 15, 30} min, the arrival-aware predictor classifies the window [t+L, t+L+5] from the forecast, the
  current-status predictor classifies the road as it is at t ("it stays as now"), both against the outcome run's
  status for that window (truck, 60 cm). Blocking = AT_RISK or CLOSED; UNKNOWN roads are excluded and counted.

## Results: forecast = heavy, outcome = cloudburst (`docs/validation/response_eval_heavy_vs_cloudburst.json`)

| Mission-level (SIMULATED) | Arrival-aware | Current status |
|---|---|---|
| People served by T+180 / unmet at end | 170 / 20 | 170 / 20 |
| Missions dispatched; legs invalid by outcome | 10; 0 | 10; 0 |
| Known-closure violations; double-booking + overflow | 0; 0 | 0; 0 |
| Last completion | T+131 | T+131 |
| Mean replan time | 85 ms | 86 ms |

**The two methods tie at mission level here.** The demo vehicles are trucks (60 cm clearance) and the mission roads
(bases → flooding spots → shelters) never reach 60 cm in the cloudburst outcome at the planned minutes, so both
methods choose the same valid routes. This is reported as found, not adjusted.

| Road-access prediction, 58,900 road-windows (660 UNKNOWN excluded) | Arrival-aware | Current status |
|---|---|---|
| Accuracy | 99.61 % | 99.53 % |
| Probability of detection (blocked road-windows found) | **92.4 %** (427 of 462) | **70.6 %** (326 of 462) |
| False-alarm ratio | 31.1 % | 29.9 % |
| Blocked road-windows flagged incl. UNCERTAIN | 99.4 % | 95.2 % |

This is where the methods differ: assuming a road "stays as now" misses 3.9× more of the road-windows that the
outcome run blocks within the next 5–30 minutes, at the same false-alarm level. For cars and ambulances (30 / 40 cm)
the Phase 1 same-run scan already showed route-level differences (`ROAD_ACCESS.md`).

## Results: forecast = heavy, outcome = 26 July 2005 replay (`docs/validation/response_eval_heavy_vs_july2005.json`)

The forecast ("heavy") badly underestimates this outcome (the 26 July 2005 replay). **Both methods fail in the same
way**, which is the most important finding of the evaluation:

| Mission-level (SIMULATED) | Arrival-aware | Current status |
|---|---|---|
| People served by T+180 / unmet at end | 170 / 20 | 170 / 20 |
| Missions dispatched; legs invalid by outcome; missions invalid | 10; **9**; **5** | 10; **9**; **5** |
| Known-closure violations; double-booking + overflow | 0; 0 | 0; 0 |
| Mean replan time | 102 ms | 100 ms |

| Road-access prediction, 58,900 road-windows | Arrival-aware | Current status |
|---|---|---|
| Accuracy | 91.0 % | 90.8 % |
| Probability of detection | **10.5 %** (620 of 5,900) | **7.9 %** (465 of 5,900) |
| False-alarm ratio | 0 % | 0 % |
| Blocked road-windows flagged incl. UNCERTAIN | 41.8 % | 33.8 % |

Example: mission A7's shelter leg enters L J Marg at T+77.6 with a forecast depth of 42.6 cm (UNCERTAIN, under the
60 cm limit); in the outcome the road is at 65.5 cm (AT_RISK). The planner warned, but had no better road to offer
from what it believed. **Arrival-aware routing is only as good as its forecast**: checking the right minute does not
help when the forecast is wrong about the storm. What it does add, even here, is the warning — 42 % of the roads that
turned out blocked carried an UNCERTAIN or AT_RISK flag at planning time, against 34 % for "stays as now".

## Results: 20 seeded sequences, development and held-out (2026-10-11, D-35)

`generate_script(seed)` draws a reproducible incident per seed (closures on the current routes or on random roads,
reported by an operator, a field team or the public; vehicle losses; shelter and settlement updates). Development
seeds 0-19 and held-out seeds 100-119 are disjoint; nothing was tuned on the held-out seeds. Forecast = heavy,
outcome = cloudburst, both FloodNet simulations. Every replan step records the inputs it used;
`check_information_availability` found no input used before it was received in any sequence. Values are mean
(min-max) over the 20 sequences; files `response_eval_heavy_vs_cloudburst_seeds.json` (development) and
`response_eval_heavy_vs_cloudburst_heldout_seeds.json` (held-out), regenerated by `scripts/response_eval.py`.

| SIMULATED, 20 sequences each | Dev: arrival-aware | Dev: current status | Held-out: arrival-aware | Held-out: current status |
|---|---|---|---|---|
| People served by T+180 | 199 (130-210) | 199 (130-210) | 195 (61-210) | 195 (61-210) |
| People unmet at end | 19.8 (5-85) | 19.8 (5-85) | 21.0 (5-154) | 21.0 (5-154) |
| Missions invalid by outcome | 0.00 (0-0) | 0.05 (0-1) | 0.05 (0-1) | 0.05 (0-1) |
| Known-closure violations; double-booking + overflow | 0; 0 | 0; 0 | 0; 0 | 0; 0 |
| Road-window POD | 92.5 % (82.7-99.0) | 71.9 % (40.1-88.2) | 92.1 % (83.0-98.9) | 71.0 % (42.3-90.4) |
| Road-window false-alarm ratio | 32.0 % (21.0-38.4) | 30.6 % (19.4-41.3) | 30.6 % (13.3-39.8) | 28.2 % (6.4-40.6) |

Reading: with trucks (60 cm clearance) the two routing methods again tie at mission level; the difference is in
road-access prediction, where checking each road at its arrival minute finds about 21 percentage points more of the
road-windows that the outcome run blocks, at a false-alarm ratio about 2 points higher. The held-out sequences
reproduce the development result. Limits: outcome labels are a second simulation, not observed road conditions;
POD and false-alarm ratio depend on how often roads block in the outcome; 20 sequences on one forecast/outcome pair
is a robustness check of the method comparison, not a measure of real-world flood-prediction accuracy.

## Exports (dashboard, Response tab)

Ledger (JSON, without route geometry), dispatch log (CSV), plan versions (JSON), event logs (JSON), evaluation
(JSON). All built from what the browser holds; nothing is stored server-side.

## Limits

- Outcome = another simulation of the same simplified physics; agreement between two runs is not validation.
- One scripted sequence, one vehicle class for the prediction score; speeds and thresholds are assumptions.
- Prediction quality is scored per road-window with equal weight; a 5-minute false alarm on a service lane counts
  as much as a miss on a trunk road.
- Replay operator behaviour is idealised (accepts everything, no delays).
