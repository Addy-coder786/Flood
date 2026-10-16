# STATUS — handoff for the next session

**Updated 2026-09-09 after the scientific validation pass (see `docs/VALIDATION.md`).** Original vertical
slice was commit `01fb21f` / `bf92843`; this pass added DEM-reliability flagging, an open domain boundary,
a real Flooding-Spots comparison, a working PySWMM cross-check, and a demo-safety script. Tests: 64/64
passing.

## Runnable now
```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn floodnet.api.main:app --port 8000     # data/processed/pilot already built -> data_mode REAL
```
Open http://localhost:8000 → pick scenario → Run / Compare → scrub 0–180 min → click twice → flood-safe route.
Tests: `.venv\Scripts\python.exe -m pytest tests -q` → 64 passed (≈30–70 s). Rebuild data: `python -m floodnet.data.build_pilot` (needs internet). Demo safety: `python scripts/demo_check.py`.

## Verified numbers (real pilot, heavy storm 50 mm/h × 2 h, 3 h horizon, 10 m grid, ~40 s per run)
Post-validation-pass (open boundary now live; see VALIDATION.md §9 for before/after):
| | NORMAL | BLOCKED 70 % |
|---|---|---|
| peak surcharging nodes | 186 | 424 |
| flooded segments (≥15 cm) at peak | 1,104 | 1,197 |
| total surcharge to street (m³) | 45,751 | 124,981 |
| outfall discharge (m³) | 164,624 | 132,748 |
| mass-balance error | ~1e-12 | ~1e-12 |

## Validation pass — done (see `docs/VALIDATION.md` for full detail)
1. **Extreme-depth cause found and fixed** (not by editing the DEM): 407 cells (0.65% of grid) inconsistent
   with the surveyed manhole network are now flagged, not altered; a closed-grid-boundary artefact at the
   clipped pilot's edge was fixed with a physically-motivated open (free-outfall) boundary. `heavy` max
   depth 2.82→2.69 m all-cells, 2.15 m excluding flagged cells either way. `july2005` reliable-cell max is
   still 5.70 m — **flagged as an open question** (§5/§9 of VALIDATION.md), likely the storage-cell
   scheme's lack of lateral momentum; worth a follow-up if time allows.
2. **Flooding-Spots comparison done**: `heavy` 3/5 active spots detected (misses diagnosed); `july2005`
   5/5 but base rate 60% (flagged as uninformative, not celebrated). Chitale named-location check done.
3. **PySWMM cross-check working**: real DYNWAVE run against the real pilot network (fixed a genuine SWMM
   constraint — 27 of 116 outfalls have >1 real inlet conduit, need a collector-junction export). Edge-flow
   ranking agrees (Spearman 0.74); node-level agreement is weak (Jaccard 0.10) for physically explained
   reasons (capacity-limited vs Saint-Venant) — see `docs/validation/SWMM.md`.
4. **Demo safety**: `backend/scripts/demo_check.py`, 16/16 checks pass across all 4 scenarios + blockage.

## Highest-priority next tasks (in order)
1. **Investigate the `july2005` 5.70 m reliable-cell extreme** (VALIDATION.md §5/§9) — likely needs either
   a genuine 2D shallow-water term for lateral spreading near buildings, or a sanity cap tied to a
   documented physical argument (not an arbitrary clip).
2. **Explainability panel** (P1): per-street cause chain from `node_cause` + upstream rainfall.
3. **Time-aware routing** (use the frame at estimated arrival), ambulance vs car demo.
4. Stretch: IMD radar GIF + pysteps extrapolation nowcast (P2); scale-up to box E (6,012 nodes); a tide/
   tailwater boundary condition at outfalls; calibrate Manning n / inlet capacity against the SWMM cross-check.

## Known limitations to state on stage
Storage-cell diffusive surface scheme (not Saint-Venant, no lateral momentum — see the `july2005` extreme-depth note above); no reverse pipe flow (backflow = blocked downstream + upstream surcharge); open boundary uses local terrain slope as a proxy friction slope, unverified against a measured discharge; no tide at outfalls; Manning n / inlet 0.05 m³/s / storage 1.5 m² / impervious fractions ESTIMATED; rainfall uniform over the pilot; Santacruz gauge ~10 km away; DTM from 20 cm contours (SD 0.28 m vs manholes); MCGM data vintage unknown, mTHD datum (consistent within pilot, offset to MSL unverified); severity bands are working thresholds, not cited guidance.
