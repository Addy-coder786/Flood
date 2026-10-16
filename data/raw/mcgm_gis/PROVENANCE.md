# MCGM GIS snapshot — PROVENANCE

**Status: REAL data. Official MCGM (BMC) ArcGIS REST service. Snapshotted 2026-09-08 ~13:09 IST.**

Source service (public, anonymous, no token):
`https://prsrvgisapp.mcgm.gov.in/server/rest/services/mcgm/MCGMGIS_Departments_Master_All_Layers/MapServer`

| File | Layer | Content | Records | CRS |
|---|---|---|---|---|
| `c7_{0,8000,16000,24000,32000}.json` | 7 `Storm Water Drains` | conduits: US/DS node id, length, shape, W×H (mm), US/DS invert (mTHD), Existing/Proposal | 34,711 | EPSG:4326 (server-reprojected from 32643) |
| `n6_{0,8000,16000,24000,32000}.json` | 6 `Storm Water Manholes` | nodes: NODE_ID, GROUND_LEV (mTHD), point | 34,431 | EPSG:4326 |
| `mapserver_meta.json` | service root | 121-layer inventory | — | — |
| `all_layers.json` | `/layers?f=json` | full field definitions, all 121 layers | — | — |

Format: Esri JSON (`features[].attributes`, `features[].geometry`), paged at 8,000/page.

Independently re-verified by the lead on 2026-09-08 18:1x IST: 34,711 conduits, 0 null/zero inverts or widths;
34,431 nodes, 34,431 unique IDs, 0 null ground levels; 0 conduits with a dangling endpoint;
live server `returnCountOnly` still returns 34,711 / 937 (layer 344 Flooding Spots).

**Known caveats (must travel with the data):**
- Elevations are **mTHD (Town Hall Datum)**, NOT MSL/EGM96. Offset UNVERIFIED — must be calibrated before mixing with any DEM.
- 11,246 of 34,711 conduits are `USER_TEXT2 = 'Proposal'` (BRIMSTOWAD proposed works). Filter to `Existing` (23,465) for a present-day model.
- No material, no roughness fields → Manning n must be ASSUMED and labelled.
- No outfall/node-type field; outfalls inferred from graph sinks.
- Data vintage unknown. Licence: `copyrightText: "MCGM, Esri India"`; no explicit open licence — credit MCGM, do not claim redistribution rights.
- Not yet snapshotted: layer 344 Flooding Spots (937), layer 301 Contour_20CM (284,403), layer 0 Ward_Boundary (26), layer 345 Flow Level Sensor (5).

Full research report: `research/mumbai/DRAINAGE.md`.
