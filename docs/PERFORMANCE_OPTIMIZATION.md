# Performance optimization attempt (2026-09-09)

**Status: investigated, one optimization attempted and validated, then reverted after validation caught a
real discrepancy. Solver code is unchanged from the last verified checkpoint.**

## 1. Profile (representative case: `heavy` scenario, 70% uniform blockage, full 180-min horizon)

| | Self time | Calls |
|---|---|---|
| `terrain/surface.py::_substep` | 88.3s (68%) | 3,905 |
| `drainage/hydraulics.py::step` | 25.5s (20%) | 2,160 |
| everything else (runoff, street aggregation, serialization, snapshotting) | <2s (under 2%) | — |

Baseline runtime: cold run 148.4s, subsequent fresh runs 114-129s.

## 1a. Re-measured 2026-09-09 (later same day, code unchanged -- confirmed via `git diff`)

Four more fresh-process runs of the identical scenario: **37.15s, 40.60s, 89.23s** (plus one earlier
114-148s range). Mass-balance error identical to 12 significant figures across every run
(`-3.771e-12`), confirming the *computation* is fully deterministic — the wall-clock **variance itself is
real and environmental** (machine load/thermal/OS scheduling on this dev machine), not a property of the
code changing. Reporting the full observed range honestly rather than the single most flattering number:
**runtime for this scenario has been observed between ~37s and ~148s on this machine, code unchanged
throughout.** No claim of a specific fixed runtime should be made without controlling for machine load.

## 2. What was investigated in `hydraulics.py::step()`

The drainage network has 48 topological levels for 1,134 edges; 26 of 48 levels (54%) touch ≤5 edges. Each
level iteration called `np.bincount(..., minlength=self.N)` (N=1,233 nodes) four times and recomputed HGL
over the full N-node array, regardless of how few nodes the level actually touched — real, measurable waste
for the many small levels. This is an *outer-loop* inefficiency, not a change to the underlying Manning/
capacity-limiting equations.

## 3. What was attempted

Rewrote the per-level block to compute HGL/free-volume only at the level's own `us`/`ds` node indices, and
replaced the four `np.bincount(..., minlength=N)` calls with `np.add.at`/`np.subtract.at` scatter updates
directly on the state array `V` (cost ∝ level size, not N). Same formulas, same evaluation point relative to
V's mutations, same per-level ordering (topological levels, still processed one at a time).

**Measured: 3.30x speedup (152.24s → 46.20s) for the identical scenario.**

## 4. Validation (why it was reverted)

Built a rigorous frame-by-frame comparison harness (old implementation vs new, same pilot/rainfall/blockage/
horizon) checking every frame's depth grid, node HGL, node surcharging, edge flow, edge utilization, street
depths, and node cause — not just the top-line summary metrics.

**Top-line summary metrics looked deceptively close:**

| metric | OLD | NEW |
|---|---|---|
| peak_depth_cm | 268.72318 | 268.72318 |
| peak_surcharging_nodes | 424 | 424 |
| peak_flooded_streets | 1197 | 1197 |
| total_surcharge_m3 | 124980.698 | 124980.613 |
| mass_balance_error_pct | -3.77e-12 | -3.75e-12 |

**But the detailed, frame-by-frame comparison found real discrepancies these summary numbers masked:**
- max |node_hgl| diff: **2.009 m** (not a rounding error — a genuinely different hydraulic grade line at
  some node)
- max |edge_util| diff: **1.000** (100% — a completely different utilization value on at least one edge)
- `node_cause` differed at 1-4 nodes in 20 of the 37 frames
- max node_surcharging boolean mismatch: 4 nodes in a single frame

This means the rewrite is **not** mathematically equivalent to the original in all cases, despite passing
every summary-statistic check. The exact mechanism was not fully isolated within this pass (candidate
suspects: an edge case in how `np.add.at`/`np.subtract.at` interact with edges inside the same
strongly-connected-component level, or an ordering assumption in the original that the rewrite silently
broke) — root-causing it properly needs dedicated debugging time, not a rushed fix.

**Action taken: reverted immediately.** `backend/floodnet/drainage/hydraulics.py` is byte-identical to the
pre-attempt version. No physics code differs from the last verified checkpoint.

## 5. Why this validation approach matters

This is the exact scenario the project's "no optimization that changes the result materially" rule exists
for: a 3.3x speedup that *looks* safe by every summary metric, but demonstrably is not once checked at the
frame/node/edge level. Shipping it on summary-metric evidence alone would have been a real, hidden
correctness regression.

## 6. `terrain/surface.py::_substep` (68% of runtime) — not attempted

`_substep` allocates ~15-20 new arrays per call (H, dHx, dHy, hx, hy, vx, vy, Vx, Vy, out, scale, hb, q, dh)
across 3,905 calls. A scratch-buffer-reuse pattern (pre-allocate once, use `out=` on every numpy op) is a
plausible, real optimization, but was not attempted this pass — after the hydraulics.py result, the priority
shifted to not shipping a second unvalidated change to frozen physics without a dedicated, unhurried pass.

## 7. Recommendation for a future pass

- Re-attempt the hydraulics.py optimization with a smaller, isolated repro (a tiny synthetic network with a
  known cycle/SCC) to pin down exactly where the scatter-based rewrite diverges, before re-attempting on the
  full pilot.
- The `_substep` scratch-buffer-reuse idea in `surface.py` remains untried and is lower-risk (no change to
  reduction/aggregation semantics, just allocation reuse) — a reasonable next target.
- Any future attempt MUST use frame-by-frame (not just summary-metric) validation, per §4-5 above.

# 2026-10-11 Run Forecast profiling

**Scope:** the "Run Forecast" request chain. The React UI sends one `POST /api/simulate`
(`frontend-react/src/state/FloodNetContext.jsx` `runSimulation` -> `api/client.js` `simulate`), decodes the
self-contained response in the browser (`lib/runBundle.js` `decodeRun`, which already unpacks all arrays in
parallel) and then only refreshes `/api/data-status`. No series/frames/hotspots/alert request follows it, so there is
nothing sequential to parallelise on the client: all the waiting is inside that one POST.

**Method.** In-process FastAPI `TestClient`, one fresh Python process per sample (request 1 = cold: imports, pilot
load, street index, physics; requests 2-3 in the same process = warm), real pilot (255 x 244 cells, 1,233 nodes,
1,134 edges, 2,978 road segments), `horizon_min = 180`, blockage none. Per-stage times come from `perf_counter`
wrappers around the named functions. `FLOODNET_IMD_AUTO_RENEW=0` and a scratch `FLOODNET_STATE_DIR` were used so the
benchmark neither starts the token keeper nor writes the real last-good rainfall cache. IMD was called 4 times in
total (one cold `auto` process per measurement round); Open-Meteo once per cold `ecmwf` process. The scripts were kept
outside the repo (session scratchpad: `perf/bench.py`, `run_ab.sh`, `equiv_full.py`).

**Machine caveat.** One Windows 11 laptop (16 logical CPUs) shared with other agents' test runs. Physics wall time
for the identical, deterministic `heavy` run ranged from 17.3 s to 104.6 s across this session with the physics code
untouched, so cold totals are only comparable within one time window. "After" was therefore measured as an
**interleaved A/B**: each repetition ran the old behaviour (reproduced in-process: zlib level 9, uncached segment
context, per-street series built, physics reuse disabled) and the new code back to back. Stage times of the
post-processing steps are far less load-sensitive than the physics and are the most reliable comparison.

## Where the time goes (BEFORE, quiet window, median)

| Path | cold total | warm total | physics (`run_simulation`) | rainfall provider | post-processing (bundle + series + hotspots + alert) | response |
|---|---|---|---|---|---|---|
| `heavy` (UI default), n=3 cold / 6 warm | 19.72 s (19.44-20.00) | 0.669 s | 18.61 s cold; 0 warm (static-scenario memo, pre-existing) | n/a | 0.63 s | 1,518,042 B |
| `ecmwf`, n=3 / 6 | 10.00 s (9.80-10.07) | 8.79 s (8.35-9.07) | 8.35 s cold, 8.51 s warm (**recomputed although the provider returned its TTL-cached forecast**) | 0.87 s cold (Open-Meteo HTTP); 0 warm (provider 10-min TTL) | 0.17 s | 319,758 B |
| `auto` (IMD live won), n=1 / 2 | 10.11 s | 8.84 s | 8.22 s cold, 8.59 s warm (same recomputation) | IMD `get` 1.25 s, of which HTTP 0.51 s; 0 warm (provider TTL) | 0.19 s | 320,089 B |

Other cold costs: imports 0.50 s, pilot load 0.034 s (already cached per process), street-cell index 0.21 s, model
construction 0.18 s. Inside the physics (cProfile, `heavy`): `terrain/surface.py::_substep` ~67 %,
`drainage/hydraulics.py::step` ~23 %, everything else under 10 %. Inside the warm `heavy` request: `run_bundle`
0.47 s, of which **zlib level 9 on the depth-level array alone took ~0.37 s** (level 6: 36 ms, 258 KB instead of
227 KB); `_series_payload` 0.105 s, mostly building the per-street series (~110,000 float conversions) that
`bundle.self_contained` then **discards**; the per-segment inspector context (static per pilot) 0.03 s, recomputed
on every request.

## What changed (and why the output is identical)

1. **Physics reuse for identical inputs** (`backend/floodnet/api/state.py`: `_physics_key`, `_run_physics`,
   `PHYSICS_MEMO_MAX = 4`). Provider-driven runs (`live`, `ecmwf`, `auto`, `demo`, gridded sources) re-ran the whole
   physics on every click even when the provider returned the same rainfall from its own TTL cache. A run is now
   reused only when every physics input is identical: the same loaded pilot objects, canonical blockage spec,
   horizon, frame step, and a rainfall scenario with the same id, name, description, provenance (tag = data mode,
   source, note), time axis, intensities and spatial field/grid (blake2b of the array bytes). Providers are still
   called on every request, so their freshness rules (IMD / ECMWF 10-min TTL, SRI 5 min, `auto` failover order) are
   unchanged, and any new value is a new key and is simulated. Source metadata (`rainfall_source`, timestamps,
   staleness, `active_source`) is never cached: the caller attaches the current provider metadata to every result.
   The memo is cleared with the other derived caches when the pilot is swapped. The semantics are the same as the
   pre-existing static-scenario memo (`_run_scenario_cached`): `runtime_s` reports the time of the computation that
   produced the frames.
2. **zlib level 9 -> 6 for the packed bundle arrays** (`backend/floodnet/api/bundle.py`, `ZLIB_LEVEL`). Lossless:
   the decoded arrays are byte-identical. The response grows by ~41.6 KB (1,518,042 -> 1,559,673 B, +2.7 %) for
   `heavy`, +0.2 % for `ecmwf`, still far below the 4.5 MB limit the existing test guards.
3. **The discarded per-street series is no longer built** (`backend/floodnet/api/main.py`:
   `_series_payload(..., include_streets=False)`, used only by `_complete`). `GET /api/simulation/{id}/series` still
   returns it, with the key order unchanged.
4. **Per-pilot cache of the static segment context** (`backend/floodnet/api/bundle.py`: `_segment_context` ->
   `_compute_segment_context`). The cache entry holds strong references to the exact net / terrain / roads objects
   and the segment list. It is used only for those same objects (`is`) and is cleared on a pilot swap.

Not changed: physics (`terrain/`, `drainage/`, `simulation/`), grid, time step, horizon, IMD integration and fallback
order, labels, the frontend.

**Equivalence evidence.** `backend/tests/test_perf_run_forecast.py` (18 tests), plus a full-horizon check
(`equiv_full.py`: 180 min, real pilot, `heavy` with no blockage and `cloudburst` with 70 % blockage). Every non-bundle
response field, every non-packed bundle field and all 8 packed arrays after decoding are identical between the old
and new code paths.

## AFTER (interleaved A/B, second window, median of 3 cold / 6 warm, same machine)

| Path | cold BEFORE | cold AFTER | warm BEFORE | warm AFTER | stages, warm BEFORE -> AFTER |
|---|---|---|---|---|---|
| `heavy` (UI default) | 31.28 s (17.92-48.65) | 19.01 s (17.34-55.57) | 0.681 s (0.618-0.706) | **0.178 s** (0.172-0.207) | `run_bundle` 0.470 -> 0.087 s; `_series_payload` 0.113 -> 0.007 s |
| `ecmwf` (repeat click within the provider TTL) | 10.09 s (9.39-37.07) | 31.53 s (9.65-40.65) | 28.38 s (8.15-41.64) | **0.323 s** (0.094-0.363) | physics 27.48 s -> 0 (reused) |
| `auto`, IMD live (first A/B window, n=1 cold / 2 warm) | 13.80 s | 24.61 s | 38.90 s (38.41-39.39) | **0.105 s** (0.104-0.107) | physics 37.71 s -> 0 (reused) |

Treat the cold columns as noise: they are dominated by the unchanged physics, which took 17-55 s for the same
deterministic run within this window. The deterministic cold saving is the post-processing, about 0.5 s on `heavy`
(cold medians: `run_bundle` 0.471 -> 0.116 s, `_series_payload` 0.104 -> 0.007 s). The large wins are on the warm and
repeat paths. A repeat of the default scenario is ~3.8x faster. A repeat live or forecast run within the provider TTL
no longer re-runs the physics, so it drops from seconds to ~0.1-0.3 s.

## Tried and NOT shipped (measured, bit-identical, gain within noise)

- `GraphDrainage.step` restructured per level (precomputed per-level static arrays, local `np.bincount`, outfall sum
  over a full-length outfall vector to keep the summation order, `V += 0.0` to keep the sign-of-zero behaviour). It
  was bit-identical on every frame of a 60-min real-pilot run (`tobytes()` equality of all frame arrays, equal mass
  balance), but only 5.59 s -> 5.43 s (~3 %). The step is dominated by per-call numpy overhead across 48 levels, not
  by array size. This also suggests a mechanism for the 2026-09-09 divergence (section 4): `np.subtract.at` applies
  contributions one at a time, while `np.bincount` sums them first and subtracts once. The rounding differs, and
  the thresholds amplify it. (This was not re-tested on that rewrite.)
- Surface open-boundary block computed on the ~1,000 boundary cells instead of the full grid, plus a precomputed
  closed-face mask. Bit-identical over a 60-min run; timings 5.09 -> 5.09 s and 5.43 -> 4.79 s in two pairs, which is
  not distinguishable from load noise on this machine.

## Remaining bottlenecks

1. **The first run of any new input is still the physics:** ~8-19 s on a quiet window here for 180 min, ~67 % of it
   in `_substep`, where `np.cbrt` on three full-grid arrays is about 2 ms of a ~5.5 ms sub-step. Options, none
   implemented: compute the x- and y-face `cbrt` in two threads (numpy releases the GIL; bit-identical per element;
   about 10 % expected); pre-warm the UI default (`heavy`, none, 180) at startup so the first click after a restart is
   warm (about 20 s of CPU at boot; not possible on Vercel); or a background job with real progress (a larger change).
2. IMD `get` spends ~0.7 s outside the HTTP call (token handling in `imd_auth`). Left alone because IMD integration
   behaviour is out of scope for this change.
3. Cold imports (0.5 s) and the street-cell index (0.2 s) are paid once per process, which is negligible next to the
   physics.
