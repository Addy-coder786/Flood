# Extreme-depth investigation (Task 1/2)

**Question:** the vertical-slice report showed a maximum modelled cell depth of ~2.8 m under the `heavy`
storm (50 mm/h × 2 h). Is this physically plausible, and is it a DEM/model artefact?

## Method

1. Ran `heavy`, no blockage, full 180 min on the real pilot; found the argmax-depth cell and the top 5.
2. For each, inspected: ground elevation, 5×5 neighbourhood, nearest drainage node + its surveyed
   `GROUND_LEV`, nearest road, nearest MCGM Flooding Spot, the closed depression it sits in (priority-flood
   fill), and MCGM contour points (layer 301) within 30 m.
3. Ran a pilot-wide check: labelled every closed depression in the DTM (268 total), and checked whether
   its bottom elevation is consistent with the 1,233 real MCGM manhole `GROUND_LEV` values nearby.
4. Checked whether the pilot's closed grid boundary (an artefact of clipping a 2.44×2.55 km window out of
   Mumbai) was trapping water that should physically leave the window.

Reproduce: `backend/floodnet/validation/extreme_depth.py` (per-cell analysis) and the pit-handling
tests/report below. Raw output: `docs/validation/extreme_depth.json`, `dem_reliability.json`,
`boundary_before_after.json`.

## Finding 1 — a small number of DTM cells are inconsistent with the surveyed network

Across the whole pilot, the contour-derived DTM agrees closely with independently surveyed manhole ground
levels: **mean residual +0.012 m, SD 0.283 m over 1,205 manholes** (see `docs/STATUS.md`). Every one of the
1,233 real MCGM manholes in the pilot reads **≥ 27.22 mTHD**.

Of the 268 closed depressions in the DTM, **7 (covering 407 cells, 0.65% of the grid)** have a bottom
elevation more than 3 m below every surveyed manhole within 250 m — as much as **11.4 m** below. These are
exactly the cells that produced the reported ~2.8 m extreme (the rank-1 and rank-3 cells in the per-cell
analysis, at z = 16.1–19.1 m against a surrounding manhole floor of 27.4–29.6 m).

**These low elevations are genuinely present in the MCGM contour source layer** — they are not an
interpolation overshoot invented by our `griddata` step; contour vertices as low as 15.4–18.6 m exist
within 30 m of these cells. We could not determine from the available data *why* the contour layer records
elevations 8–11 m below the surrounding street/manhole network at these specific spots — plausible
explanations include a subway/rail underpass, an excavation, a drainage channel bed, or an attribute
inconsistency in the MCGM layer — and we are not in a position to guess. **We have not altered the DEM.**
Per the task instruction, the honest response is to flag these cells rather than silently trust or silently
discard them.

`floodnet/terrain/pits.py::dem_reliability_mask()` implements this flag (conservative: interior depressions
only, >3 m deficit vs. the nearest surveyed manholes, >0.25 m depression depth). It is computed at build
time and stored in `data/processed/pilot/dem_reliability_mask.npz` + `terrain.json["pit_handling"]`; it
does not touch `z`. **Excluding these 407 flagged cells, the maximum modelled depth anywhere in the pilot
drops from 2.82 m to 2.15 m** — i.e. the flagged cells, not general model behaviour, produced the reported
extreme.

## Finding 2 — the closed grid boundary was an artefact of the clip window

The pilot is a 2.44×2.55 km window clipped out of Mumbai. The original surface model had a **closed**
boundary (no water leaves the grid). 83% of the low-elevation corridor in the DTM sits within 200 m of the
grid edge, and one component of 1,719 cells (z 26.4–27.0 m) runs directly off the south-west edge — terrain
that is genuinely low *and* continues downhill outside the clipped window. With a closed boundary that
water has nowhere to go but pond against the clip line, which is a modelling artefact of where we drew the
pilot box, not a real flood mechanism.

**Fix (model, not data):** `floodnet/terrain/pits.py::outward_open_boundary()` opens edge cells whose
terrain slopes outward (402 of 994 edge cells); `StorageCellSurface` gained an optional free-outfall
boundary condition (Manning-type outflow at the edge, `q = (1/n)·h^(5/3)·√S`), and its volume is tracked in
`MassBalance.boundary_out_m3` so mass balance still closes exactly. This is now the default in the live API
(`floodnet/api/state.py::build_models`, `open_boundary=True`).

## Before / after (`heavy`, 180 min, real pilot)

| | closed boundary (original) | **open boundary (now live)** | open + 70% blockage |
|---|---|---|---|
| max cell depth | 2.82 m | 2.69 m | 2.69 m |
| max cell depth, DEM-reliable cells only | 2.15 m | 2.15 m | 2.15 m |
| max street depth | 282.1 cm | 264.0 cm | 265.5 cm |
| flooded segments ≥ 15 cm (peak) | 1,163 | 1,104 | 1,197 |
| peak surcharging nodes | 190 | 186 | 424 |
| surcharge to streets (m³) | 47,498 | 45,751 | 124,981 |
| outfall discharge (m³) | 168,424 | 164,624 | 132,748 |
| **boundary outflow (m³)** | 0 (closed) | **40,271** | 42,168 |
| mass-balance error | 2.3e-12 % | 2.4e-12 % | 3.8e-12 % |
| blocked > normal still holds? | — | — | yes (surcharge 45,751→124,981; nodes 186→424; segments 1,104→1,197) |

Full test: `backend/tests/test_pits.py` (5 tests — DEM never mutated by flagging, open boundary is a subset
of outward-sloping edge cells only, mass balance closes with the new boundary term, a large genuine
synthetic depression is neither flagged nor artificially drained, depression reporting works).

## Verdict

The 2.8 m extreme was **not** a DEM interpolation artefact in the ordinary sense (the low elevations really
are in MCGM's contour data) and it was **not** fixed by altering the DEM. It was driven by two separate,
identified, and now-handled effects: (1) a small set of contour cells (0.65% of the grid) whose elevation
is inconsistent with the surveyed drainage network nearby, now flagged for exclusion from headline
street-depth claims; and (2) an artificial closed boundary at the edge of the clipped pilot window, now
open where terrain genuinely slopes outward. Excluding the flagged cells, the model's plausible depth range
for this storm is **up to ~2.15 m**, still a severe flood by any standard for a 100 mm two-hour storm in a
low-lying, densely built catchment — consistent with Mumbai's own 2005 record of >1 m depths at multiple
locations (see `docs/validation/FLOODING_SPOTS.md`) — not a sign the model is broken.
