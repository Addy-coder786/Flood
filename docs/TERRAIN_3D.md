# 3D terrain view (2026-09-21)

The 3D view draws **the same DEM the flood solver runs on**. It is a visualisation; it predicts nothing and changes
no model value.

## The DEM (traced from source to solver)
MCGM `Contour_20CM` (layer 301, 2,678 polylines densified to 334,852 points) + 1,233 manhole `GROUND_LEV` points
→ `scipy.griddata` linear, nearest-fill outside the hull → `data/processed/pilot/terrain.npz` key `z` →
`Terrain.z` → `run_simulation()` (surface routing, runoff). Provenance tag REAL (`terrain.json`).

| Property | Value |
|---|---|
| Array | `[ny, nx] = [255, 244]` float32, row 0 = south, one value per cell centre, no no-data cells |
| Grid | 10 m, EPSG:32643 (UTM 43N), origin x0 = 271954.58, y0 = 2103161.09 → 2.44 × 2.55 km |
| Bounds (lon/lat) | 72.83330 – 72.85676 E, 19.00840 – 19.03170 N (same as every map overlay; contains the pilot box) |
| Elevation | 16.14 – 40.37 m (1st–99th percentile 26.62 – 36.15 m) |
| Datum | mTHD (Town Hall Datum); offset to mean sea level **unverified** |

## Transport and encoding — `GET /api/terrain/dem`
`z_base64` = base64 of the raw little-endian IEEE-754 float32 bytes of `Terrain.z`, row-major. **Lossless**: decoding
reproduces the solver's values bit-for-bit (round-trip tolerance 0; `sha256` of the bytes is included and tested).
No scale/offset, no 8-bit quantisation, no resampling. No-data would be sent as NaN, counted, and left as a hole in
the mesh. The payload also carries the building mask and `lonlat_to_grid`, a fitted affine (max error 0.09 m over
the pilot) used only to place lon/lat overlays in the model frame. ~406 KB, cacheable, served by the normal backend
through `VITE_API_BASE_URL` — no extra asset, host or service.

## Renderer — three.js, one vertex per model cell
deck.gl `TerrainLayer` and MapLibre `raster-dem` were evaluated and **not** used: both need an 8-bit RGB height image
on an axis-aligned lon/lat / Web-Mercator raster, then re-tessellate it. The model grid is UTM (≈0.7° from that
frame), so either would mean resampling the DEM and rebuilding a different surface — i.e. not the solver's terrain.
three.js (MIT, pinned 0.170.0) builds the mesh directly: 62,220 vertices at the model's cell centres, 123,444
triangles, in metres east/north of the grid origin. It is lazy-loaded (its own ~129 KB gzip chunk) only when 3D is
opened; the Leaflet map is untouched and stays mounted underneath.

- **Relief:** drawn height = (z − z_min) × relief, default **10×** (3× / 10× / 20× selectable, shown as "10× visual
  relief"). Real relief is 24 m over 2.4 km, which is invisible at 1–3×. Display only — `dem.z` is never written.
- **Surface:** muted hypsometric ramp stretched between the DEM's own 1st/99th percentiles, low north-west sun,
  building footprints slightly darker (not extruded — no heights are known). No aerial texture.
- **Flood:** the forecast frame's own depth grid is draped as a texture (1 pixel = 1 model cell, so it aligns exactly),
  plus flooded street segments as severity-coloured ribbons. Both follow the timeline; the terrain never moves.
- **Inspector:** ray → mesh → model cell → `dem.z[cell]`. Always the DEM value, at any relief.
- **Markers:** lowest and highest DEM cell (16.14 m and 40.37 m).
- **Controls:** 2D | 3D toggle (view state only), drag/rotate/zoom, "Reset terrain view". 3D opens on the place the 2D
  map was centred on; returning to 2D restores the exact 2D view.
- **Tours:** the "2D surface flow" stage, the Guided Briefing terrain step and Demo Story open 3D, aim at the run's
  worst-affected real location, speak the fixed sentence (no elevation is spoken), and return to 2D.

## Limits
Contour-interpolated DTM (effective vertical resolution ≈ 0.2 m; artefacts between contours); relief is exaggerated
for display; no basemap is draped in 3D; needs WebGL (falls back to a message + "Back to 2D map").
**3D visual click-through NOT_VERIFIABLE in the development environment** — browser automation could not reach
localhost, so appearance, lighting and camera framing are unreviewed; the data path, geometry and state handling are
tested (`backend/tests/test_terrain_dem_endpoint.py`, `frontend-react/src/lib/terrain3d/dem.test.mjs`).
