# Research: navigation, live traffic and flood slowdown (2026-10-11)

Scope: whether FloodNet can use a live-traffic source for the edges of **its own** route, whether anything changes the
navigation choice (D-40), the exact Pregnolato et al. (2017) depth–speed function, a short literature check on
flood-aware routing and facility allocation, and Zhengzhou/China lessons. Research only: no code, keys or
dependencies were added. All sources accessed **2026-10-11**. Labels: **[V]** read in the primary source,
**[S]** secondary source or an abstract only, **[UNVERIFIED]** could not be checked. Nothing here is a decision; any
adoption belongs in `DECISIONS.md`.

## Summary and recommendations

1. **Pregnolato et al. (2017), Eq. 4 [V]:** `v(w) = 0.0009·w² − 0.5529·w + 86.9448`, *w* = standing-water depth in
   **mm**, *v* = **maximum safe vehicle speed in km/h**, R² = 0.95, fitted to 10 literature/video/guidance points from
   0 mm (88 km/h) to 300 mm (0 km/h). **The quadratic never reaches zero** (discriminant < 0; minimum ≈ 2.0 km/h at
   ≈ 307 mm; v(300 mm) ≈ 2.07 km/h): "speed = 0 at 300 mm" is the paper's stated threshold ("a road is assumed to be
   impassable only when the limit of 30cm is reached"), not a root of the equation. It is for 2WD passenger cars, UK
   data, no flow velocity. Implementations must clamp: `v = 0 for w ≥ 300 mm`.
2. **Effect for FloodNet's ASSUMED urban speeds (10–30 km/h, D-24):** the curve only bites above ≈131 mm (30 km/h),
   ≈166 mm (20 km/h), ≈213 mm (10 km/h). Proposed use: `speed = min(assumed_class_speed, v(depth))` for cars, labelled
   ASSUMED/literature-derived, not calibrated for Mumbai. No published curve for ambulances/trucks was found.
3. **Live traffic: TomTom is the only provider whose terms, as far as we could read them, do not forbid showing
   results on our Leaflet/OSM map**, has India in its Traffic Flow coverage, a free tier with **no credit card**
   (2,500 non-tile requests/day) and an endpoint that returns live speed for a point (`currentSpeed`,
   `freeFlowSpeed`, `confidence`, `roadClosure`) plus a routing call that can **reconstruct our own route**
   (`supportingPoints`) and return its live-traffic travel time. Credential: a **TomTom API key** (server-side only).
   Caveat: the TomTom API terms page is JavaScript-rendered and could not be read; the permission to display results
   on third-party map data comes from the TomTom Maps SDK for JavaScript licence §3.4 **[UNVERIFIED for the REST API
   terms]**. Confirm before any public demo.
4. **Google Routes: not usable legitimately for us.** The Service Specific Terms say "Customer must not use Google Maps
   Content from the Routes API in conjunction with a non-Google map" and the general terms prohibit use "for High Risk
   Activities" (use or failure could reasonably be expected to cause death or serious injury), which describes rescue
   dispatch. Cost is not the blocker (India free caps are generous).
5. **HERE: not usable on the free plan** for a rescue fleet: the Base Plan excludes "Asset Management" (locating,
   tracking, route calculations for actively managed vehicles) and needs a payment method; India coverage is listed as
   Central/North/South with no West region named (Mumbai coverage **[UNVERIFIED]**).
6. **Mapbox: usable only in a limited way.** India is listed for real-time + typical traffic; Directions API has no
   "Mapbox map only" statement, but Navigation API results may not be cached or stored, Map Matching (the only call
   that could follow *our* geometry) must be displayed on a Mapbox map, and raw Mapbox Traffic Data is enterprise-only.
7. **Navigation (D-40) stands.** No web navigation SDK from Google or Mapbox was found; Valhalla `exclude_polygons`
   and GraphHopper custom-model `areas` exist but do not know our arrival-time flood depths. Nothing found changes
   "our router stays the authority".
8. **Allocation:** none of the papers checked benchmarks optimisation against a transparent priority allocator; nothing
   found justifies replacing it in a 48-hour prototype.
9. **Zhengzhou (full report read) [V]:** the Jingguang North Tunnel had a written closure trigger (close when road water
   > 40 cm) that was exceeded at about 15:00 but full closure came only at 16:16; CCTV showed the queue at 15:46 but
   nobody reported or acted. Lesson for FloodNet: threshold-based closure **proposals with timestamps and an
   acknowledgement/escalation clock**, which our event log and D-42 escalation already partly provide.

**Prior research (`LIVE_OPERATIONS_RESEARCH.md`) status:** §5 confirmed in substance and refined (Google: the
restriction is "with or near a non-Google map", plus a new High Risk Activities finding); Mapbox India live traffic
moves from [U] to [V] listed; Map Matching "Mapbox map" rule confirmed (API docs, not in the July 2026 Product Terms);
Pregnolato row confirmed but **incomplete** (the equation does not reach 0 at 30 cm); §3 China confirmed and extended
from the full report.

---

## 1. Live traffic providers

### VERIFIED FACTS

**Google Maps Platform**
- Service Specific Terms, Routes API §19.1–19.3 [V]: "Customer may use Google Maps Content from the Routes API in
  Customer Applications without a corresponding Google Map"; "Customer must not use Google Maps Content from the
  Routes API in conjunction with a non-Google map"; lat/lng may be cached up to 30 consecutive days. Same pattern for
  Directions, Distance Matrix, Roads, Route Optimization, Navigation SDK.
  https://cloud.google.com/maps-platform/terms/maps-service-terms
- Platform terms §3.2.3(e) [V]: "Customer will not use the Google Maps Core Services with or near a non-Google Map in a
  Customer Application." §3.2.1(c)(i): customer will not access or use the Services "for High Risk Activities",
  defined as "activities where the use or failure of the Services would reasonably be expected to result in death,
  serious personal injury, or severe environmental damage or property damage". §3.2.3(c)(vi): no converting
  "text-based driving times into synthesized speech results". https://cloud.google.com/maps-platform/terms
- SKUs [V]: Essentials = basic features, ≤10 intermediate waypoints; **Pro = `TRAFFIC_AWARE` or
  `TRAFFIC_AWARE_OPTIMAL`**; Enterprise = e.g. two-wheel or large-vehicle routing (page last updated 2026-10-09).
  https://developers.google.com/maps/documentation/routes/usage-and-billing
- India price list [V] (last updated 2026-10-08): Compute Routes Essentials (India) 70,000 free/month, then $1.50 per
  1,000; Pro 35,000 free, $3.00; Enterprise 7,000 free, $4.50 (lower above 5,000,000).
  https://developers.google.com/maps/billing-and-pricing/pricing-india ; global Pro is 5,000 free, $10.00
  https://developers.google.com/maps/billing-and-pricing/pricing
- `TRAFFIC_ON_POLYLINE` returns `speedReadingIntervals` with categories **NORMAL / SLOW / TRAFFIC_JAM only** (no numeric
  speed); requires `TRAFFIC_AWARE` or `TRAFFIC_AWARE_OPTIMAL`, DRIVE or TWO_WHEELER.
  https://developers.google.com/maps/documentation/routes/traffic_on_polylines
- Route modifiers: `avoidTolls`, `avoidHighways`, `avoidFerries`, `avoidIndoor` (+ `vehicleInfo`, `tollPasses`);
  "Specifying a route modifier does not necessarily eliminate routes that include the restricted feature." No custom
  area or road avoidance found. https://developers.google.com/maps/documentation/routes/route-modifiers

**TomTom**
- Pricing [V]: "No credit card required"; "Free daily API requests: 50k tile, 2.5k non-tile"; Routing €0.75/1K; Traffic
  Flow (non-tile, "Real-time and historical traffic flow data") €0.75/1K; Traffic Incidents (non-tile) €0.75/1K.
  https://docs.tomtom.com/pricing
- Traffic API market coverage [V]: **India (IN/IND) ✔ Traffic Incidents, ✔ Traffic Flow** ("detailed data").
  https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/product-information/market-coverage
- Flow Segment Data v4 [V] (last edit 2025-04-17): for the road fragment closest to a point returns `currentSpeed`,
  `freeFlowSpeed`, `currentTravelTime`, `freeFlowTravelTime`, `confidence` (0–1), `roadClosure`, `frc`, segment
  coordinates, optional OpenLR. https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data
- Calculate Route [V] (last edit 2026-08-07): POST `supportingPoints` "to be used as input for route reconstruction";
  `avoidAreas` up to ten rectangles (≈160×160 km max); `traffic=true` "Use all available traffic information";
  `departAt`; `computeTravelTimeFor=all` returns `noTrafficTravelTimeInSeconds`, `historicTrafficTravelTimeInSeconds`,
  `liveTrafficIncidentsTravelTimeInSeconds`; `sectionType=traffic`.
  https://docs.tomtom.com/routing-api/documentation/tomtom-maps/calculate-route
- TomTom Maps SDK for JavaScript licence §3.4 [V]: "You shall be permitted to display TomTom APIs results ("Results") on
  third party content or map data provided that nothing shall entitle you to add or incorporate Results into the OSM or
  any other open source database." §1 says API use remains subject to TomTom's standard API terms.
  https://cdn.jsdelivr.net/npm/@tomtom-org/maps-sdk@0.48.3/LICENSE.txt
- TomTom developer terms (https://developer.tomtom.com/terms-and-conditions, redirecting to
  docs.tomtom.com/legal/terms-and-conditions): **could not be fetched** (JavaScript-rendered; WebFetch and Exa returned
  only navigation). Their caching, storage and safety clauses are **[UNVERIFIED]**.

**HERE**
- Traffic API v7 coverage [V]: "India - Central", "India - North", "India - South" (Flow ✔, Incidents ✔, no Deep
  Coverage mark); no "India - West". https://docs.here.com/traffic-api/docs/traffic-here-traffic-api-v7-coverage-information
- Base Plan exclusions [V]: "Asset Management" = locating, tracking or map display, route calculations, analytics,
  alerts for an "Asset" (includes people and vehicles "actively managed using HERE's service").
  https://www.here.com/get-started/pricing/base-plan-restrictions
- Developer billing [S, HERE knowledge base]: a payment method is provided up front; free tiers apply first.
  https://docs.here.com/here-kb/docs/how-is-billing-for-a-developer-here-platform-account . Free amounts per API could
  not be read (pricing page content not in the fetched HTML) **[UNVERIFIED]**.

**Mapbox**
- India is listed among countries where the ETA "is calculated using both real-time and typical traffic data"; real-time
  = speeds observed in the last 15 minutes; otherwise typical traffic is used [V].
  https://docs.mapbox.com/help/dive-deeper/directions/
- Directions API [V]: `driving-traffic` "factors in current and historic traffic conditions"; up to 25 coordinates;
  annotations `duration`, `speed`, `congestion`, `congestion_numeric`; `exclude=point(lon lat)` BETA, up to 50,
  best-effort; 300 requests/min; no statement found requiring a Mapbox map.
  https://docs.mapbox.com/api/navigation/directions/
- Map Matching [V]: supports `driving-traffic`, 2–100 coordinates; "Results must be displayed on a Mapbox map using one
  of the Mapbox libraries or SDKs." https://docs.mapbox.com/api/navigation/map-matching/
- Product Terms (PDF dated July 21, 2026) [V]: §2.10.1 "Customer shall not export, download, cache or store results
  from any request to a Navigation API"; §2.12.2 Mapbox Traffic Data only for "internal evaluation purposes" without an
  Order; §1.4 attribution (Mapbox logo, © Mapbox, © OpenStreetMap); §1.16 safety disclaimer.
  https://www.mapbox.com/legal/product-terms
- Pricing [V]: Directions "Up to 100,000" free monthly requests, then $2.00/1,000.
  https://www.mapbox.com/pricing . Whether account creation needs a card: not stated on that page **[UNVERIFIED]**.

### ASSUMPTIONS
- Our Leaflet map with OSM tiles is a "non-Google map" and a "non-Mapbox map" in the sense of those terms.
- Rescue-vehicle dispatch in a flood is a use whose failure "would reasonably be expected to result in death or serious
  personal injury" (our reading; Google decides).
- Whether Mapbox's Directions API is a "Navigation API" under §2.10 is our inference from the `/api/navigation/` docs
  path; the terms define Navigation APIs only "as described in Mapbox documentation".
- A demo in October may show real live traffic next to a SIMULATED flood; the combination is not a real-world state.

### PROPOSED FOR FLOODNET
- **Option T (TomTom, if adopted):** backend-only `TOMTOM_API_KEY` (name proposed). For a route already chosen by our
  router, one `calculateRoute` POST with `supportingPoints` = our geometry, `traffic=true`,
  `computeTravelTimeFor=all` → show "live-traffic travel time for this route (TomTom, fetched hh:mm)" beside our
  ASSUMED-speed ETA. One non-tile request per check fits 2,500/day. Optional per-edge factor
  `currentSpeed / freeFlowSpeed` from Flow Segment Data for a handful of corridor points. Treat TomTom `roadClosure`
  as a PUBLIC-tier signal (UNCERTAIN, never CLOSED) under D-32. Do not persist TomTom results beyond the request until
  the API terms are read (avoid building a stored speed layer on OSM edges).
- Label everything: provider, fetch time, `confidence`, "live traffic ≠ flood forecast".
- Do not integrate Google Routes or HERE.

### UNRESOLVED
- TomTom REST API terms (storage/caching, safety-critical use, use with OSM-based routing): **[UNVERIFIED]**.
- TomTom data density on Hindmata/Dadar local roads (FRC5–6) and `confidence` there: unknown until a test call.
- HERE coverage of Mumbai; HERE free amounts.
- Whether the organisers allow a third-party key in the demo.

## 2. Navigation SDKs vs OSM engines

### VERIFIED FACTS
- Google Navigation products are listed for Android, iOS, Flutter and React Native; no web/JavaScript Navigation SDK was
  found [S, search of developers.google.com navigation pages]. Navigation SDK content is also "No Use with a non-Google
  map" (§12.2) [V, Service Specific Terms above].
- No Mapbox web Navigation SDK found; Mapbox lists Navigation SDK for mobile and automotive [S].
- Valhalla route API [V]: `exclude_polygons` ("Roads intersecting these rings will be avoided"); `exclude_locations`;
  `date_time` types 0–3; `speed_types` `freeflow`, `constrained`, `predicted`, `current` "if available".
  https://valhalla.github.io/valhalla/api/route/api-reference/
- GraphHopper custom models [V]: GeoJSON `areas` usable in conditions; areas can be blocked by multiplying speed with 0;
  `limit_to`/`multiply_by` speed operations. https://raw.githubusercontent.com/graphhopper/graphhopper/master/docs/core/custom-models.md
- OSRM was not re-checked in this round.

### ASSUMPTIONS
- None of these engines evaluates depth **at the minute the vehicle reaches the road**; they would need one request per
  forecast frame or a custom cost, which our router already does.

### PROPOSED FOR FLOODNET
- Keep D-40 unchanged. No new dependency.

### UNRESOLVED
- None that affects the decision.

## 3. Flood depth and vehicle speed

### VERIFIED FACTS (Pregnolato, Ford, Wilkinson, Dawson 2017, *Transp. Res. D* 55:67–81, doi:10.1016/j.trd.2017.06.020, open access)
Source read: https://www.sciencedirect.com/science/article/pii/S1361920916308367
- Eq. (4): `v(w) = 0.0009w² − 0.5529w + 86.9448`, R² = 0.95; "*v*(*w*) is the maximum acceptable velocity that ensures
  safe control of the vehicle given the depth of water". Units from Table 2: depth mm, speed km/h.
- Fitted points (Table 2, "Estimated function"): 0 mm/88 km/h; 10/77 (hydroplaning formula); 87/40 (YouTube video);
  116/37 (Galatioto et al. 2014); 125/26 (video); 189/24 (Galatioto); 200/16 (EVSTF 2015); 230/7 (driver guidance);
  250/3 (Boyce 2012); **300/0** ("the average depth at which a passenger vehicles starts to float"; Kramer et al. 2016,
  AA and others).
- Bounds (not fitted, "insufficient data"): 150 mm/0 (air-intake stall), 450 mm/8 km/h (a BMW wading manual), 600 mm/0
  (Kramer: special vehicles). Applies to 2WD cars; 4WD "safe driving in depths up to 45cm, or even 90cm". Kramer et al.
  (2016) recommended fording thresholds of 0.3 m for passenger cars and 0.6 m for emergency vehicles (as summarised in
  Table 1). Depth is the only intensity measure; flow velocity and debris are future work.
- Conclusions: "a road is assumed to be impassable only when the limit of 30cm is reached."
- Data: ODbL, doi:10.17634/121736-4 (not fetched).
- Our arithmetic on Eq. 4 (reproducible): v(0)=86.94, v(100)=40.65, v(150)=24.26, v(200)=12.36, v(250)=4.97,
  v(300)=2.07 km/h; discriminant −0.0073 → no real root; vertex at 307 mm, 2.03 km/h. Depth at which v equals
  30 / 20 / 10 / 5 km/h: 131 / 166 / 213 / 250 mm.

**Newer or regional work**
- Choo et al. (2020), *Water* 12(4):926 (MDPI page https://www.mdpi.com/2073-4441/12/4/926), Seoul: rainfall–depth–speed
  curve built on Pregnolato [S abstract]; a conference version reports a linear refit "y = 85 × −9" whose text is
  garbled in the PDF extraction **[UNVERIFIED]** (https://www.witpress.com/Secure/elibrary/papers/FRIAR20/FRIAR20012FU1.pdf).
- Singh, Sinha, Vijhani, Pahuja (2018), *IJDRR* 28:237–250, doi:10.1016/j.ijdrr.2018.03.017: India, "safety speed
  function" relating depth to speed; >40 % of road length "immovable" for a 1-in-100-year event [S abstract]. City not
  confirmed **[UNVERIFIED]**.
- Li et al. (2026), *Int. J. Disaster Risk Sci.* 17(1):182–196, doi:10.1007/s13753-026-00697-y: IHUM flood model + SUMO,
  Guangzhou; 100-year storm: average speed −54 %, police response 4.83 → 14.52 min [S abstract].
- He et al. (2026), *Natural Hazards*, Bristol: probability of stability loss per vehicle type, isochrones from hospitals
  [S abstract]; article DOI not confirmed (abstract cites supplementary material at 10.1007/s11069-026-08005-z).

**Mumbai evidence (credible but limited)**
- Soni & Chandel (2020), *Transp. Res. Procedia* 48:2096–2107, doi:10.1016/j.trpro.2020.08.269: two Greater Mumbai
  roads, survey; rainfall increased travel time by 8–140 % (rainfall intensity, **not** flood depth) [S abstract].
- Lalwani et al. (2025), *Int. J. Disaster Resilience in the Built Environment* 17:39–54, doi:10.1108/ijdrbe-12-2024-0130:
  **Dadar** microsimulation (5,159 links, 18 zones) calibrated on crowdsourced travel times; disconnection scenario:
  ~65 % of OD pairs exceed the base model's maximum travel time [S abstract]. A related conference deck assumes a 50 %
  speed reduction on inundated roads (an assumption, not a measurement) [S]
  https://www.urbanmobilityindia.in/Upload/Conference/f1f9f1d3-891a-4b2c-a565-9ef0732750da.pdf
- No Mumbai-measured depth–speed relationship was found.

### ASSUMPTIONS
- Using Eq. 4 as a cap (`min(class_speed, v(w))`) rather than a ratio `v(w)/v(0)` is our modelling choice; the paper
  defines *v* as a maximum safe speed, which supports the cap.
- Indian vehicles, driver behaviour and drainage differ from the UK sources.

### PROPOSED FOR FLOODNET
- Cars: `speed = min(ASSUMED class speed, v(depth_mm))`, `0` at ≥ 300 mm (already AT_RISK at the 30 cm car limit).
- Ambulance (40 cm) and truck (60 cm) limits: no fitted curve exists. Option: apply the car curve up to 250 mm and a
  fixed ASSUMED crawl speed (e.g. 5 km/h, in line with the 230–250 mm guidance points) from 250 mm to the vehicle
  limit, labelled ASSUMED. To be decided in `DECISIONS.md`, not here.
- Return the function, its source and its limits under `assumptions` in the API, as D-24 does.

### UNRESOLVED
- Any validation for Mumbai; speeds of emergency vehicles in water; effect of flow velocity.

## 4. Flood-aware routing and facility allocation

### VERIFIED FACTS
| Paper | What it shows |
|---|---|
| Alabbad, Mount, Campbell, Demir (2024), *Urban Informatics* 3:10, doi:10.1007/s44212-024-00040-0 [V abstract + intro] | Web DSS (FastAPI + PostGIS) for road status, shortest paths to amenities, location-allocation and service areas in two Iowa towns under flood scenarios; demonstrates feasibility, no comparison with a simpler allocator |
| Green et al. (2017), *NHESS* 17:1–16, doi:10.5194/nhess-17-1-2017 [V metadata; content per prior research] | City-scale emergency-responder accessibility in floods; 25 cm restriction from one UK fire service |
| Coles, Yu, Wilby, Green, Herring (2017), *J. Hydrology* 546:419–436, doi:10.1016/j.jhydrol.2016.12.013 [V metadata only] | Emergency-service accessibility during flooding, York |
| Yin et al. (2016), *J. Hydrology* 537:138–145, doi:10.1016/j.jhydrol.2016.03.037 [V metadata] | Shanghai pluvial flood impact on roads; 30 cm binary closure (as described by Pregnolato) |
| Miller (2022), *Natural Hazards Review* 23, doi:10.1061/(ASCE)NH.1527-6996.0000586 [S abstract] | Time-varying accessibility during a 500-year pluvial event (Lafayette); timing matters |
| Gama, Santos, Scaparra (2016), doi:10.1007/s13675-015-0058-3 [prior research, not re-checked] | Multi-period shelter location-allocation MIP |

### ASSUMPTIONS
- Only abstracts/introductions were read for most; a full-text benchmark could exist and was not found.

### PROPOSED FOR FLOODNET
- Keep the transparent priority allocator (D-28). If anything, feed it the same arrival-time travel times (with the
  Section 3 slowdown) the router uses; no MIP.

### UNRESOLVED
- No evidence either way on optimisation vs priority rules at our scale.

## 5. China

### VERIFIED FACTS
- Zhengzhou "7·20" investigation report, full PDF (46 pages), Ministry of Emergency Management, January 2022 [V]
  https://www.mem.gov.cn/gk/sgcc/tbzdsgdcbg/202201/P020220121639049697767.pdf (summary:
  https://www.mem.gov.cn/xw/bndt/202201/t20220121_407106.shtml):
  - Jingguang Expressway North Tunnel: 6 dead, 247 vehicles flooded (18 in tunnel, 87 on the ramp, 142 on the exit
    road). The tunnel centre's 2021 plan required closure when tunnel water exceeds 30 cm or ordinary-road water exceeds
    40 cm; at about 15:00 the west-exit road water exceeded 40 cm; only some ramps were closed "to keep the tunnel
    open"; forced full closure at 16:16, "too late"; tunnel submerged 17:30. At 15:46 the centre saw the ramp queue on
    CCTV but did not report or act; traffic police did not direct traffic at the exit as the plan required, although
    two cameras were working (printed pp. 14–15).
  - Metro Line 5: 14 dead; red warnings issued 19–20 July but no emergency response was started; network stop order
    only at 18:04 after the train had lost power; the dispatcher ordered the train to reverse ~30 m without knowing
    the situation, into a section ~75 cm lower (printed pp. 12–13).
  - Findings include an unsound warning-response linkage ("预警与响应联动机制不健全"); recommendations include
    integrated warning and response management ("强化预警和响应一体化管理"). Cited regulations call for traffic
    control so that command, rescue and relief vehicles pass first (footnotes in the accountability section).
- Digital-twin river basins: MWR completed top-level design 2021–2022, ran digital-twin basin pilots 2022–2023 and
  digital-twin water-network pilots from July 2023, organised around the "four pre" (预报、预警、预演、预案: forecast,
  warning, rehearsal, plan) [S, *Water Resources Informatization* 2025 abstract,
  http://slxxh.ijournals.cn/slxxh/article/abstract/20250101 ]. Scope in the sources found: river basins, reservoirs,
  water works; no urban-drainage or road-routing component found.

### ASSUMPTIONS
- The tunnel failure maps to FloodNet's underpasses (Hindmata area) by analogy only.

### PROPOSED FOR FLOODNET
- When forecast or reported depth on an underpass/low segment crosses a configured trigger, raise a **closure proposal**
  with the trigger, time and an acknowledgement clock; unacknowledged proposals escalate (fits D-42). Proposals are
  never closures until a human records one (D-32 policy unchanged).
- Do not claim a digital twin or "four pre" compliance.

### UNRESOLVED
- Official MWR page for the pilot list (not fetched); Indian (BMC) underpass closure triggers, if any, not researched.
