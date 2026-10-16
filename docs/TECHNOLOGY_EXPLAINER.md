# Technology explainer — how FloodNet tells its own story (2026-09-21)

One data file, `frontend-react/src/lib/story/stages.js`, drives every surface below, so they cannot drift apart.
It is guarded by `src/lib/story/stages.test.mjs` (`npm run test:unit`).

## The chain (each stage = a real capability)
| # | Stage | One line shown to users | Backed by | Badge |
|---|---|---|---|---|
| 1 | Rain | IMD observations and radar-derived rainfall estimates. | `rainfall/provider.py` providers | REAL |
| 2 | Spatial rainfall | Rainfall is represented on the model's working grid. | `RainfallScenario.intensity_field_mm_h [T,ny,nx]`, `rainfall/gridded.py` | ESTIMATED |
| 3 | Runoff | Urban surfaces convert rainfall into surface runoff. | `terrain/runoff.py` | ESTIMATED |
| 4 | 2D surface flow | The terrain model determines where surface water can move and accumulate. | `terrain/surface.py` (storage-cell), ~10 m pilot grid; opens the 3D view of the same DEM (`TERRAIN_3D.md`) | REAL terrain |
| 5 | Drainage network | Underground capacity is simulated alongside surface flow. | `drainage/hydraulics.py`, MCGM network | REAL geometry |
| 6 | Flood depth | FloodNet produces street-level depth estimates in centimetres. | `streets/aggregate.py`, 0–180 min frames | ESTIMATED |
| 7 | Action | Alerts and flood-aware routing turn predictions into decisions. | `alerts/`, `routing/router.py` | ESTIMATED |

The grid is FloodNet's own ~10 m working grid; it is not coarsened to match other projects' 100 m demos.

## Surfaces
- **Landing → "How FloodNet Works"**: seven cards (glyph, title, line, badge). Clicking a card opens that stage in
  the live dashboard.
- **Dashboard story strip** (`components/StoryStrip`): RAINFALL → FLOOD → DRAINAGE → ACTION + "How it works".
  Clicking opens the real tab / map layer / timeline for that stage and spotlights it; the callout has a
  seven-dot stage switcher. Layers, tab and playback are restored on close. No demo data.
- **Demo Story** (second button beside Guided Briefing; same `useTour` engine, overlay, voice, en/हिन्दी/मराठी):
  Rainfall → Terrain → Runoff → Drainage → Flood development → Why flooded → Alerts → Lower flood-risk route.
  Technology sentences are fixed; numbers come only from the completed run; steps with no data are skipped.
- **Guided Briefing** and **Explore FloodNet** are unchanged in purpose (situation briefing; product tour).

## Wording rules
Allowed: "IMD Mumbai-Veravali DWR", "radar-derived rainfall estimate", "experimental radar-image nowcast" (only
when a run actually used one), "3-hour persistence estimate", "ECMWF forecast", "flood-aware routing", "lower
flood-risk". Never: official IMD radar QPE, IMD radar nowcast, AI prediction, validated accuracy, live traffic,
observed blockage, "this drain caused this street", government alert issuance, "best"/"safest"/"guaranteed".
Normal screens carry no disclaimer paragraphs; methodology and limits live in the Sources tab and `docs/`.

## Tide (not part of the chain)
No tide boundary exists and none is shown. Sources checked 2026-09-21: INCOIS Mumbai gauge (view-only, "no download
facility", datum unstated); Survey of India monthly high/low-water PDFs (reproduction needs written permission);
MCGM high-tide list (not machine-readable); UHSLC/IOC (no Mumbai station); PSMSL (monthly means only); FES2022 /
TPXO tide models (registration; harbour accuracy unvalidated). Next step if pursued: FES2022 via pyTMD, checked
against the Survey of India Mumbai table, labelled "predicted astronomical tide (model)", after written permission.
