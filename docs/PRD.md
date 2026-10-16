# PRD — FloodNet Urban Flood Nowcasting System

**Status:** DRAFT v0.1 — planning stage only. No technology chosen. No implementation started.
**Document owner:** Team Bitflare
**Last updated:** 2026-09-08

---

## 0. How to read this document

Every capability in this PRD carries one of three labels. This separation is deliberate and must be preserved in all future edits:

| Label | Meaning |
|---|---|
| **[REQUIRED]** | Explicitly stated or directly implied by the official problem statement. Non-negotiable. |
| **[PROPOSED]** | Our own product decision. Adds value, but the problem statement did **not** ask for it. Droppable under time pressure. |
| **[STRETCH]** | Out of scope for the 48-hour prototype. Recorded so it is not forgotten. |

Additionally, any statement that depends on data, models or facts we have **not yet verified** is marked **`[UNVERIFIED]`**. Nothing marked `[UNVERIFIED]` may be presented to a judge as fact until it is confirmed and the marker removed.

The authoritative wording of the problem statement lives in `docs/REQUIREMENTS.md`. Where this PRD and that document appear to disagree, the problem statement wins.

---

## 1. Product vision

A city control room should be able to look at a map and see, **before the water arrives**, which specific streets and intersections will be under water in the next 0–3 hours, how deep, and why — and then act on it: reroute an ambulance, hold a bus, close an underpass, pre-position a pump.

We are building a **coupled nowcasting system**, not a weather model. The distinguishing idea, taken directly from the problem statement, is that rainfall volume alone does not predict flooding. Flooding is produced by the interaction of four things:

1. how much rain falls, and where, in the next few hours,
2. how much of it runs off instead of soaking in (imperviousness),
3. where gravity takes that runoff across the terrain (micro-topography),
4. whether the underground drainage network can swallow it — and what happens at the nodes where it cannot.

Our product's job is to fuse those four into a single, street-level, forward-looking picture, and to expose that picture through a map and through an API that navigation and dispatch systems can consume.

---

## 2. Problem definition

From the problem statement, restated as a definition of the gap we are closing:

- Indian metros (Mumbai, Delhi, Chennai are named in the PS) experience urban flooding annually.
- Traditional NWP tells cities **how much rain**, not **which streets flood**. Knowing rainfall does not translate into knowing inundation.
- Urban flooding is **hyper-local**, driven by micro-topography, concrete imperviousness, and strained and largely invisible drainage networks.
- Municipal bodies currently **lack real-time, street-level predictive systems**.
- The consequence is that cities are surprised by rapid water accumulation, producing traffic gridlock, economic disruption, and loss of life.

The gap, precisely: **there is no operational translation layer between a rainfall nowcast and a street-level inundation forecast with a 0–3 hour lead time.** That translation layer is the product.

---

## 3. Target users

The PS names emergency services, public transit, and commuters as consumers of the routing API, and municipal bodies as the parties currently lacking the capability.

| User | Context of use | What they need from us |
|---|---|---|
| **Municipal disaster-management / control room** (e.g. city disaster-management cell, ward control room) | Sitting in front of a wall display during a downpour; deciding where to send pumps, barricades, and warnings | City-wide map, ranked list of streets/junctions about to flood, lead time, confidence, cause |
| **Emergency services** (ambulance, fire, police, rescue) | En route to an incident during heavy rain | Is my route about to be cut? Give me a flood-safe alternative, via API |
| **Public transport operators** (bus depots, transit control) | Deciding whether to hold, divert, or terminate services | Which corridors and depots are at risk in the next 0–3 h; diversion suggestions |
| **Commuters / citizens** | Deciding whether to leave now, wait, or take another road | Simple map, plain-language depth, "avoid this stretch", safe route |

**Primary user for the prototype and the demo: the municipal control-room operator.** The other three are served through the same forecast, exposed via the routing API and a simplified map view. We optimise the UI for the control room because that is the user the PS identifies as currently unserved.

---

## 4. Core user problems

1. **"I know it is raining hard. I do not know where it will collect."** Rainfall data is available; inundation is not.
2. **"I find out when the calls start coming in."** Response is reactive; by the time water is reported, the road is already gridlocked.
3. **"The drainage network is invisible to me in real time."** Operators cannot see which drains are at or over capacity, or which manhole is about to surcharge onto the street.
4. **"I cannot tell my ambulances which way to go."** No flood-aware routing exists; a navigation app does not know that a road is currently impassable because of standing water.
5. **"When I do get a warning, I do not know why or whether to trust it."** Without a cause and a confidence, an operator cannot justify closing a road.
6. **"Every heavy rain event is treated as new."** No ability to ask a what-if question — a given rainfall total over a given duration on a given ward — before the season starts.

Problems 1–4 are directly derived from the PS. Problems 5–6 are our reading of what makes the output actually usable; they motivate **[PROPOSED]** features and are labelled as ours, not as problem-statement requirements.

---

## 5. Product goals

| # | Goal | Label |
|---|---|---|
| G1 | Predict street-level inundation, expressed as **water depth in centimetres**, over a **0–3 hour** forward window | [REQUIRED] |
| G2 | Drive that prediction from a **high-resolution rainfall nowcast** (Doppler weather radar as the PS-named source) rather than from rain-gauge totals alone | [REQUIRED] |
| G3 | Route rainfall volume across a **2D surface terrain model** derived from a high-resolution DEM | [REQUIRED] |
| G4 | Represent the stormwater drainage network as a **directed graph** (nodes = manholes/inlets, edges = pipes/canals) and compute **hydraulic capacity** on it | [REQUIRED] |
| G5 | Predict where **overcapacity or blockage causes surcharge / backflow onto the street** | [REQUIRED] |
| G6 | Deliver a **dynamic web-based GIS dashboard** with street-by-street projections over the 0–3 h window | [REQUIRED] |
| G7 | Deliver an **API that can interface with navigation maps** to suggest **flood-safe alternative routes** | [REQUIRED] |
| G8 | Make every prediction **traceable to a cause** (which rainfall cell, which terrain sink, which drainage node) | [PROPOSED] |
| G9 | Be **scientifically defensible**: every physical assumption stated, every parameter sourced or explicitly declared as an assumption | [PROPOSED] |
| G10 | Be **buildable and demonstrable within 48 hours** on a bounded pilot area | [PROPOSED] |

---

## 6. Non-goals

Explicitly out of scope. Stating these protects the 48-hour budget and prevents scope creep.

- **We are not building a weather model.** We consume a rainfall nowcast; we do not forecast the atmosphere from first principles.
- **We are not producing a certified engineering design tool.** Output is decision support for the next 0–3 hours, not a basis for drainage capital works.
- **We are not modelling river/coastal flooding, storm surge, or dam release.** Scope is pluvial (rain-driven) urban flooding, as the PS describes.
- **We are not modelling water quality, sediment, or pollutant transport.**
- **We are not claiming city-wide production coverage** in the prototype. We will demonstrate on a bounded pilot area and describe the scaling path honestly.
- **We are not replacing the municipal control room's existing SOP.** We feed it.
- **We are not building a consumer mobile app** in the prototype. The citizen view, if built, is a responsive web view.
- **We are not issuing official public warnings.** The system is advisory; authority stays with the municipal body.

---

## 7. Core system capabilities

The end-to-end chain the PS asks for, as a pipeline. Each stage is expanded in sections 8–18.

```
[8]  Rainfall nowcast (0-3 h, gridded)
        |
[10] Runoff generation (imperviousness / infiltration)
        |
[9]  Terrain preprocessing (DEM -> conditioned surface, flow directions, sinks)
        |
[11] 2D surface-water routing  <--------------------+
        |                                           |  exchange
[12] Drainage network as a directed graph           |  (inlet capture /
[13] Hydraulic capacity per edge and node           |   surcharge back to surface)
[14] Surcharge / overflow / backflow  --------------+
        |
[15] Street-level flood prediction (depth in cm, per street segment / junction)
        |
[16] 0-3 h timeline (time-stepped frames)
        |
        +---> [17] GIS dashboard
        +---> [18] Flood-safe routing API
```

**Design constraint we impose on ourselves:** every arrow above must be a real computation on real inputs for the pilot area. Where a real input is unavailable, the substitute must be labelled in the UI and in the report as synthetic or assumed — never silently. `[UNVERIFIED]` inputs are tracked in §21 and §28.

---

## 8. Rainfall-nowcast input

**[REQUIRED]** — the PS states the pipeline "takes high-resolution rainfall nowcasts (from Doppler Weather Radars)".

**What the stage must produce:** a time series of gridded rainfall intensity (mm/h) or accumulation (mm per timestep) over the pilot area, covering **now through +3 hours**, at a spatial and temporal resolution fine enough to drive street-level routing.

**Functional requirements:**
- R8.1 Accept a gridded rainfall nowcast as the primary driver. **[REQUIRED]**
- R8.2 Produce a sequence of timesteps spanning 0–3 h ahead. **[REQUIRED]**
- R8.3 Reproject / resample the rainfall grid onto the model's working grid or catchment units. **[PROPOSED]** (implementation necessity)
- R8.4 Accept a **user-specified synthetic rainfall scenario** in place of the live nowcast, for demo and stress testing. **[PROPOSED]** — see §19.
- R8.5 Ingest historical radar/rainfall for a past flood event, to enable validation replay. **[PROPOSED]** — see §23.
- R8.6 Carry a per-timestep uncertainty or confidence indicator forward into the output. **[PROPOSED]**

**Open, must be verified before implementation (`[UNVERIFIED]`):**
- Whether Indian Doppler Weather Radar (DWR) data for the chosen city is accessible to us in a usable, programmatic form, at what latency, resolution, licence and cost.
- Whether an already-nowcast product is available, or whether we must generate the nowcast ourselves from radar reflectivity sequences (e.g. by extrapolation/advection or an ML approach) — these are very different work packages.
- The radar-to-rainfall conversion in use (reflectivity–rainrate relationship) and whether we must apply it ourselves.
- Fallback options if radar is inaccessible (gauge networks, satellite precipitation, open reanalysis). **No source is assumed available until checked.**

Decision deferred to `docs/DECISIONS.md` → *rainfall source*.

---

## 9. DEM / terrain processing

**[REQUIRED]** — the PS requires fusing rainfall with "high-resolution Digital Elevation Models (DEM)" and calls out micro-topography as a governing factor.

**What the stage must produce:** a hydrologically usable terrain surface for the pilot area — elevations, flow directions, slopes, and the locations of depressions where water accumulates.

**Functional requirements:**
- R9.1 Ingest a DEM for the pilot area and clip it to the study boundary. **[REQUIRED]**
- R9.2 Derive flow direction and accumulation, and identify topographic depressions / sinks. **[REQUIRED]** (implied by "routes that volume across a 2D surface terrain model")
- R9.3 Condition the DEM for urban hydrology — handle bridges/flyovers, culverts and artefacts that would otherwise create false dams. **[PROPOSED]** (a known necessity in urban DEM work; the specific method is undecided)
- R9.4 Optionally burn road centrelines/kerb lines into the surface so streets act as flow paths. **[PROPOSED]**
- R9.5 Record the DEM's source, native resolution, vertical datum and acquisition date as provenance shown in the UI. **[PROPOSED]**

**Open, must be verified (`[UNVERIFIED]`):**
- What DEM resolution is actually obtainable for the chosen city, free and legally usable, at hackathon speed. "High-resolution" in the PS is not quantified; global open DEMs and city LiDAR differ by orders of magnitude in fitness for street-level work.
- Whether a resolution adequate for *street-level* discrimination exists for our pilot area at all. **If it does not, the honest response is to narrow the pilot area and state the limitation — not to overstate resolution.**
- Vertical accuracy, and whether it is sufficient relative to the depth thresholds we report (§15).

Decision deferred → *available DEM*, *target city*, *target geographic area*.

---

## 10. Imperviousness / runoff

**[REQUIRED]** — the PS names "concrete imperviousness" as a governing factor of urban flooding.

**What the stage must produce:** for each unit of the model, the fraction of incoming rainfall that becomes surface runoff rather than infiltrating or being retained.

**Functional requirements:**
- R10.1 Assign an imperviousness / runoff characteristic spatially across the pilot area. **[REQUIRED]**
- R10.2 Convert rainfall to runoff per timestep using a documented, defensible method. **[REQUIRED]**
- R10.3 State the chosen method and every parameter value openly, with its source. **[PROPOSED — G9]**
- R10.4 Allow imperviousness to be edited per zone for scenario work ("what if this ward is fully paved"). **[STRETCH]**

**Open, must be verified (`[UNVERIFIED]`):**
- Source of land-cover / imperviousness data for the pilot area. No candidate source has been checked; availability, resolution and licence are all unknown. Candidates will be enumerated and evaluated during the data-acquisition stage, not assumed here.
- Which runoff-generation method we adopt. Multiple standard approaches exist in urban hydrology with different data requirements; **no method is selected yet** and none will be described as "standard practice" in the demo without a citation.
- Whether antecedent conditions (soil already saturated from earlier rain) can be represented with data we have.

---

## 11. 2D surface-water routing

**[REQUIRED]** — "instantly routes that volume across a 2D surface terrain model" and "mapping how water flows, accumulates".

**What the stage must produce:** for each timestep of the 0–3 h horizon, the distribution and depth of water on the surface, as it moves downhill and pools.

**Functional requirements:**
- R11.1 Route runoff over the conditioned terrain to produce accumulation over time. **[REQUIRED]**
- R11.2 Produce time-varying depth, not just a single end-state. **[REQUIRED]** (the PS asks for a 0–3 h forward window)
- R11.3 Exchange water with the drainage network: surface water captured by inlets; surcharged water returned to the surface. **[REQUIRED]** (this is the "coupled framework" the PS demands)
- R11.4 Run fast enough to be described as "nowcasting" — a 3-hour horizon must be computable in a small fraction of that horizon. **[REQUIRED]** ("real-time", "instantly")
- R11.5 Expose the runtime/skill trade-off explicitly, so the choice of method is defensible. **[PROPOSED — G9]**

**Explicit tension to resolve, not to hide:** full 2D shallow-water hydrodynamics is the most physically faithful option and is typically far too slow for a real-time city-scale nowcast without significant compute or a surrogate model; simplified routing is fast but approximate. The PS demands both physical coupling **and** real-time operation. **We have not chosen a point on this trade-off.** Decision deferred → *surface-routing approach*, *hydraulic engine*, *ML/GNN model*.

---

## 12. Drainage-network representation

**[REQUIRED]** — "Represent the city's stormwater drain network as a directed graph (nodes as manholes/inlets, edges as pipes/canals)."

**What the stage must produce:** a directed graph of the stormwater system for the pilot area, with the attributes needed to compute capacity.

**Functional requirements:**
- R12.1 Nodes represent manholes / inlets / junctions / outfalls. **[REQUIRED]**
- R12.2 Edges represent pipes / drains / canals and are **directed** (they carry flow one way under gravity). **[REQUIRED]**
- R12.3 Nodes carry at minimum: location, ground/rim elevation, invert elevation. Edges carry at minimum: geometry/shape, size, length, slope or end inverts, and a roughness characteristic. **[REQUIRED]** (implied by "must calculate hydraulic capacity")
- R12.4 Each node maps to the surface area that drains into it, so surface and network are coupled. **[REQUIRED]**
- R12.5 Each edge/node carries a **provenance and confidence flag**: measured / digitised from a municipal source / inferred / synthetic. **[PROPOSED — G9]**
- R12.6 Support a per-edge or per-node **blockage factor** (0–1) to represent silted or choked drains. **[REQUIRED]** (the PS asks where "blockages or overcapacity will cause backflow")
- R12.7 Import a network from a standard stormwater model file format. **[PROPOSED]**

**Open, must be verified (`[UNVERIFIED]`):**
- **This is the single highest-risk input in the project.** Municipal stormwater network data in Indian cities is frequently not public, incomplete, or non-digital. The PS itself calls the network "invisible".
- We must determine, per candidate city: does an open or obtainable drainage dataset exist, in what form, with which of the attributes in R12.3?
- If real network geometry is unavailable, the fallback is a **synthetic but topologically plausible** network derived from road centrelines and terrain — this is scientifically defensible **only if** it is labelled as synthetic everywhere it appears, and its assumptions are published. Under no circumstances is a synthesised network to be presented as the real municipal network.

Decision deferred → *available drainage dataset*.

---

## 13. Hydraulic capacity

**[REQUIRED]** — "The model must calculate hydraulic capacity".

**What the stage must produce:** for each edge, the flow it can carry; for each node, whether inflow exceeds what the downstream network can accept.

**Functional requirements:**
- R13.1 Compute a capacity for every edge from its geometry, slope and roughness, using a stated, citable hydraulic formulation. **[REQUIRED]**
- R13.2 Apply the blockage factor (R12.6) as a reduction on effective capacity. **[REQUIRED]**
- R13.3 Compute, per timestep, the flow actually routed through each edge and the resulting utilisation (flow ÷ capacity). **[REQUIRED]**
- R13.4 Propagate flow through the graph respecting direction, conservation of volume, and downstream constraints. **[REQUIRED]**
- R13.5 Expose per-edge utilisation as a first-class output for the dashboard. **[PROPOSED]**

**Open (`[UNVERIFIED]`):** which hydraulic formulation — a steady, capacity-based approximation versus a full unsteady flow solution — is both defensible and fast enough. Decision deferred → *hydraulic engine*. Roughness and other coefficients must come from published tables with a citation, never from memory presented as fact.

---

## 14. Surcharge / overflow / backflow

**[REQUIRED]** — "predict where blockages or overcapacity will cause backflow onto the streets". This is the mechanism that connects the invisible network to the visible street, and it is the conceptual heart of the PS.

**What the stage must produce:** identification, in time and space, of nodes where the network can no longer accept water and water emerges onto the surface — with volume.

**Functional requirements:**
- R14.1 Detect, per node and per timestep, when the water level in the network reaches the node's ground elevation. **[REQUIRED]**
- R14.2 Compute the **volume** that surcharges out of each such node per timestep. **[REQUIRED]**
- R14.3 Return that volume to the 2D surface model at the node's location, where it then re-routes and pools (§11). **[REQUIRED]** — the coupling loop.
- R14.4 Distinguish the two causes the PS names — **overcapacity** (too much water for a sound pipe) and **blockage** (reduced effective capacity) — and report which applies. **[REQUIRED]**
- R14.5 Represent an inlet's limited **capture capacity**: a working drain still cannot swallow water faster than its inlet allows. **[PROPOSED]**
- R14.6 Represent tailwater/outfall constraints (a downstream water body that is high can block discharge and force backflow). **[PROPOSED]**, may be **[STRETCH]** depending on data.

---

## 15. Street-level flood prediction

**[REQUIRED]** — "pinpoint exactly which streets or intersections will flood" and "water depth estimations in centimetres".

**What the stage must produce:** per street segment and per intersection, a predicted water depth in centimetres for each timestep in the 0–3 h horizon.

**Functional requirements:**
- R15.1 Aggregate gridded/cell-level surface depth onto **named street segments and intersections**. **[REQUIRED]**
- R15.2 Report depth **in centimetres**. **[REQUIRED]** (explicit in the PS)
- R15.3 Report, per segment: peak depth in the window, and the time at which flooding begins. **[PROPOSED]** — needed for the lead-time value proposition.
- R15.4 Classify segments into operational severity bands tied to depth thresholds. **[PROPOSED]** — bands must be defined from a citable source (e.g. published vehicle- or pedestrian-safety guidance) or, failing that, declared openly as our own working thresholds. **We will not invent depth-to-impact thresholds and present them as established.**
- R15.5 Attach a confidence indicator to each prediction. **[PROPOSED]**
- R15.6 Provide a ranked "top N streets/junctions at risk" list for the control room. **[PROPOSED]**

---

## 16. 0–3 hour forecast timeline

**[REQUIRED]** — "0–3 hour lead time", "0–3 hour forward-looking window".

**Functional requirements:**
- R16.1 Produce output at discrete timesteps covering now to +3 hours. **[REQUIRED]**
- R16.2 The dashboard must let the user move through that window (time slider / animation). **[REQUIRED]** ("dynamic", "forward-looking window")
- R16.3 Re-run automatically as each new rainfall nowcast arrives, so the forecast is rolling. **[REQUIRED]** ("real-time")
- R16.4 Timestep length to be chosen so that flood onset can be resolved usefully. **[PROPOSED]** — the PS does not specify it; our choice must be justified against runtime and against the rainfall product's own timestep.
- R16.5 Show the age of the driving nowcast and the time of the last model run. **[PROPOSED]** — an operator must never mistake a stale forecast for a live one.

---

## 17. GIS dashboard

**[REQUIRED]** — "A dynamic, web-based GIS dashboard showing real-time, street-by-street flooding projections (e.g. water depth estimations in centimetres) with a 0–3 hour forward-looking window."

**Functional requirements:**
- R17.1 Web-based, map-centric interface. **[REQUIRED]**
- R17.2 Street-by-street flood depth rendered on the map, in centimetres. **[REQUIRED]**
- R17.3 Time control across the 0–3 h window. **[REQUIRED]**
- R17.4 Updates as new model runs complete. **[REQUIRED]**
- R17.5 Rainfall nowcast layer, viewable alongside the flood layer. **[PROPOSED]**
- R17.6 Drainage-network layer showing node surcharge state and edge utilisation. **[PROPOSED]** — this is what makes the "invisible network" visible, and it is our strongest differentiator against a generic flood map.
- R17.7 Ranked at-risk list, click-to-zoom to the segment. **[PROPOSED]**
- R17.8 Per-segment detail panel: depth over time, onset time, cause, confidence, data provenance. **[PROPOSED]**
- R17.9 Clear, permanent labelling of any layer driven by synthetic or assumed data. **[PROPOSED — non-negotiable for honesty]**
- R17.10 Alerting/notification to subscribed users. **[STRETCH]**

---

## 18. Flood-safe routing API

**[REQUIRED]** — "An API utility that can interface with navigation maps to suggest flood-safe alternative routes for emergency services, public transit, and commuters during heavy downpours."

**Functional requirements:**
- R18.1 Expose a documented HTTP API taking an origin and destination and returning a route that avoids predicted flooding. **[REQUIRED]**
- R18.2 Apply the flood forecast as a cost/penalty or an exclusion on the road network. **[REQUIRED]**
- R18.3 Be consumable by an external navigation system — standard formats, documented schema. **[REQUIRED]** ("interface with navigation maps")
- R18.4 Serve the three named audiences: emergency services, public transit, commuters. **[REQUIRED]**
- R18.5 Support **time-aware** routing — avoid a road that will be flooded when the vehicle actually reaches it, not only what is flooded now. **[PROPOSED]** — this is the natural payoff of having a 0–3 h forecast, but it is our idea, not the problem statement's wording.
- R18.6 Vehicle-class-aware thresholds (a fire tender tolerates more depth than a hatchback). **[PROPOSED]**
- R18.7 Return the reason a route was avoided, so a dispatcher can override with knowledge. **[PROPOSED]**
- R18.8 Expose the flood forecast itself as an API (segments + depth + time) so third parties can build their own routing. **[PROPOSED]**
- R18.9 Live integration with a commercial navigation provider. **[STRETCH]**

---

## 19. Scenario simulation

**[PROPOSED]** — the PS does not ask for this. We include it for two practical reasons: a live demo cannot depend on it raining during judging, and planners need "what-if" capability.

- R19.1 Run the full pipeline on a user-defined synthetic rainfall event (intensity, duration, spatial pattern). **[PROPOSED]**
- R19.2 Replay a historical rainfall event. **[PROPOSED]** — also serves validation (§23).
- R19.3 Toggle blockage on chosen drainage nodes/edges and compare outcomes — "what if these three drains are silted". **[PROPOSED]** — directly demonstrates R12.6/R14.4.
- R19.4 Save and compare scenarios side by side. **[STRETCH]**

**Demo-critical:** at least R19.1 and R19.3 should exist, because they make the system demonstrable on demand regardless of live weather. This must not be confused with the real-time capability; the UI must state clearly when it is in scenario mode.

---

## 20. Explainability / root-cause information

**[PROPOSED]** — not required by the PS. Included because an unexplained warning is an unactionable warning, and because it is what makes the coupled model visibly different from a black box.

- R20.1 For any flooded segment, show the contributing chain: rainfall over its catchment → runoff → the drainage node(s) that surcharged → the terrain depression where it pooled. **[PROPOSED]**
- R20.2 State the dominant cause: overcapacity, blockage, terrain sink with no drainage, or downstream constraint. **[PROPOSED]**
- R20.3 Show input provenance and confidence for that segment. **[PROPOSED]**
- R20.4 Counterfactual: "if node X were cleared, predicted depth here falls by Y cm". **[STRETCH]**

---

## 21. Data requirements

Nothing in this table is confirmed. Each row must be resolved during the data-acquisition stage, and the outcome recorded in `docs/DECISIONS.md`.

| # | Data | Needed for | Criticality | Status |
|---|---|---|---|---|
| D1 | Rainfall nowcast, gridded, 0–3 h (Doppler radar per PS) | §8 | **Blocking** | `[UNVERIFIED]` — access, latency, resolution, licence all unknown |
| D2 | High-resolution DEM for pilot area | §9 | **Blocking** | `[UNVERIFIED]` — best obtainable resolution unknown |
| D3 | Stormwater drainage network with node/edge attributes | §12, §13, §14 | **Blocking** (highest risk) | `[UNVERIFIED]` — may not be publicly available in any candidate city |
| D4 | Road network (segments, junctions, names, connectivity) | §15, §18 | **Blocking** | `[UNVERIFIED]` — open sources are likely but unconfirmed for our area |
| D5 | Land cover / imperviousness | §10 | High | `[UNVERIFIED]` |
| D6 | Historical flood observations (reported waterlogging points, past event records) | §23 | High — without it there is no external validation | `[UNVERIFIED]` |
| D7 | Historical rainfall matching the events in D6 | §23 | High | `[UNVERIFIED]` |
| D8 | Administrative / ward boundaries for the pilot area | §9, UI | Medium | `[UNVERIFIED]` |
| D9 | Outfall / receiving water levels | R14.6 | Low (stretch) | `[UNVERIFIED]` |
| D10 | Soil / infiltration characteristics | §10 | Low–Medium | `[UNVERIFIED]` |

**Rules for all data:**
1. Record source, licence, resolution, date and access method for every dataset before use.
2. Never present synthetic or assumed data as observed. Label it in the code, the API response, the UI and the presentation.
3. If a dataset is unavailable, that is a finding to report, not a gap to fill with invention.

---

## 22. Outputs

| Output | Content | Consumer | Label |
|---|---|---|---|
| O1 | Per-street-segment / per-intersection predicted water depth (cm) per timestep, 0–3 h | Dashboard, API | [REQUIRED] |
| O2 | Map layers: flood depth over time | Dashboard | [REQUIRED] |
| O3 | Flood-safe route between two points | Routing API | [REQUIRED] |
| O4 | Drainage node state: surcharging or not, volume, cause | Dashboard, analysis | [REQUIRED] (needed to satisfy §14) |
| O5 | Drainage edge utilisation (flow ÷ capacity) | Dashboard | [PROPOSED] |
| O6 | Ranked at-risk street/junction list with onset time | Control room | [PROPOSED] |
| O7 | Per-segment explanation and provenance | Dashboard, API | [PROPOSED] |
| O8 | Scenario comparison result | Planning | [PROPOSED] |
| O9 | Machine-readable forecast feed for third parties | External systems | [PROPOSED] |
| O10 | Validation report against historical events | Judges, credibility | [PROPOSED] — see §23 |

---

## 23. Validation requirements

The PS does not specify a validation protocol. We impose one, because a nowcasting system that has never been checked against reality is not defensible. **[PROPOSED]**

- V1 **Physical sanity / conservation.** Water in = water stored on surface + water in network + water discharged. Any imbalance is reported, not hidden.
- V2 **Behavioural sanity.** Depth increases with rainfall intensity, all else equal. Water pools in terrain depressions, not on ridges. Blocking a drain increases upstream flooding. These are falsifiable checks we can run without external truth data.
- V3 **Historical event replay.** Drive the model with rainfall from a past flood event and compare predicted flooded locations against recorded waterlogging points (D6). Report agreement honestly, including misses and false alarms. **Conditional on D6/D7 existing.**
- V4 **Sensitivity.** Vary the parameters we had to assume (imperviousness, roughness, blockage) and report how much the answer moves. This bounds how strongly we may claim anything.
- V5 **Runtime.** Measure and publish the wall-clock time for a full 0–3 h run over the pilot area, to substantiate the word "nowcasting".
- V6 **Honesty audit before the demo.** Every number shown on screen is traced to either real data, a cited parameter, or a declared assumption. Anything that cannot be traced is removed.

**We will not report any accuracy figure we have not measured.** No metric appears in the pitch deck unless it came out of V1–V5 on our own run.

---

## 24. MVP features (the 48-hour prototype)

Ordered by priority. The MVP must cover every **[REQUIRED]** capability at least in a bounded, honest form.

**Must have — direct problem-statement compliance:**
1. M1 Bounded pilot area, one city, defined and justified. (Enables everything else.)
2. M2 Rainfall nowcast input for that area, 0–3 h, gridded — real if obtainable, otherwise scenario-driven with an explicit label. §8
3. M3 DEM ingestion and terrain preprocessing for the pilot area. §9
4. M4 Rainfall → runoff with a spatial imperviousness characteristic. §10
5. M5 2D surface routing producing time-varying depth. §11
6. M6 Drainage network as a directed graph with capacity attributes. §12
7. M7 Hydraulic capacity and flow routing on the graph, including blockage factor. §13
8. M8 Surcharge detection and return of surcharged volume to the surface — the coupling. §14
9. M9 Street-level depth in centimetres per segment/junction, per timestep. §15
10. M10 Web GIS dashboard: flood layer, time slider across 0–3 h, street-level detail. §17
11. M11 Flood-safe routing API with documented endpoints, plus a visible demo of it in the dashboard. §18

**Must have — credibility:**
12. M12 Scenario mode (synthetic rainfall + drain-blockage toggle) so the system can be demonstrated on demand. §19
13. M13 Drainage-network visualisation with surcharge/utilisation state. R17.6
14. M14 Provenance and synthetic-data labelling throughout. R17.9
15. M15 Validation results V1, V2, V5 at minimum, written up. §23

**Explicitly deferred out of MVP if time is short:** per-segment explanation panel (§20), time-aware routing (R18.5), vehicle-class thresholds (R18.6), historical replay (V3, if D6 does not exist), confidence bands (R15.5).

---

## 25. Stretch features

- S1 Historical event replay and quantitative validation (V3), if data allows.
- S2 Full explainability panel with causal chain (§20).
- S3 Counterfactual "clear this drain" analysis (R20.4).
- S4 Time-aware and vehicle-class-aware routing (R18.5, R18.6).
- S5 Alerting/subscription and a simplified citizen view (R17.10).
- S6 Integration with a live commercial navigation provider (R18.9).
- S7 Machine-learning surrogate to accelerate or improve the physical model — **only** if a physical baseline exists first and the ML model can be trained and evaluated honestly. An untrained or unevaluated model will not ship.
- S8 Scaling from the pilot area to a full city, with measured runtime evidence.
- S9 Outfall/tailwater constraints (R14.6).
- S10 Crowd-sourced flood reports as an assimilation input.

---

## 26. Success criteria

**A. Problem-statement compliance (pass/fail — every one must be demonstrable live):**
- C1 System consumes a rainfall nowcast and produces a 0–3 h forward-looking output.
- C2 A DEM-based 2D surface routing step demonstrably influences the result.
- C3 The drainage network is a directed graph with nodes and edges as the PS specifies.
- C4 Hydraulic capacity is computed, and overcapacity/blockage produces surcharge onto the street.
- C5 Output is street-level water depth in centimetres.
- C6 A dynamic web GIS dashboard shows it across the 0–3 h window.
- C7 An API returns flood-safe alternative routes.

**B. Quality:**
- C8 A full run over the pilot area completes fast enough to be credibly called nowcasting (target measured and reported, V5).
- C9 Conservation and behavioural checks (V1, V2) pass.
- C10 Every data source and assumption is documented; no unlabelled synthetic data anywhere in the demo.
- C11 A judge can follow the causal chain from a rainfall cell to a flooded street in under two minutes of explanation.

**C. Judging:**
- C12 The demo runs end to end without manual intervention.
- C13 We can answer "where did this number come from?" for anything on screen.
- C14 We can state our limitations without being asked — and they are the limitations of the *data*, not of unexamined engineering.

---

## 27. Major risks

| # | Risk | Impact | Direction of mitigation (not yet decided) |
|---|---|---|---|
| K1 | **Real drainage network data does not exist / is not obtainable.** The PS's central innovation depends on it. | Severe | Synthetic-but-plausible network derived from roads + terrain, labelled unambiguously as synthetic, with the methodology published. Treat "this data is not open" as a finding worth stating to judges. |
| K2 | **Doppler radar nowcast data is inaccessible or too slow to obtain.** | Severe | Fall back to another rainfall source or to scenario-driven input; keep the interface identical so a real feed can be plugged in later. |
| K3 | **DEM resolution too coarse for street-level claims.** | High | Narrow the pilot area; state the effective resolution honestly; do not claim sub-street precision the DEM cannot support. |
| K4 | **Physics vs. real-time conflict** (§11): a defensible hydrodynamic model may not run fast enough. | High | Decide the trade-off deliberately in the architecture stage; measure and publish runtime; never claim real-time performance we have not timed. |
| K5 | **No ground truth → unvalidatable model.** | High | Prioritise V1/V2/V4 which need no external truth; pursue V3 only if D6 exists. Never publish an accuracy number we did not measure. |
| K6 | **48-hour budget consumed by data wrangling.** | High | Time-box data acquisition; define the synthetic fallback for every blocking dataset *before* starting, so the pipeline is never blocked. |
| K7 | **Scope creep into ML for its own sake.** | Medium | ML only after a working physical baseline, and only if it can be trained and evaluated honestly (S7). |
| K8 | **Over-claiming in the pitch.** Judges probe exactly this. | Medium–High | The honesty audit (V6) is a mandatory pre-demo gate. |
| K9 | **Coupling bugs** — surface and network double-counting or losing water. | Medium | Conservation check (V1) as a continuous test, not an afterthought. |
| K10 | **Licence / usage restrictions** on a dataset discovered late. | Medium | Record licence at acquisition time, before building on it. |

---

## 28. Assumptions that still require verification

Every item here is currently **unproven**. None may be stated as fact in any document, demo or pitch until verified, and each must be either confirmed or replaced during the data-acquisition stage.

**Data availability**
- A1 That usable Doppler radar rainfall nowcast data can be obtained for an Indian metro within our time budget.
- A2 That a DEM of sufficient resolution for street-level discrimination exists for our pilot area.
- A3 That any real stormwater drainage network data is obtainable for any candidate city.
- A4 That an open road network with adequate coverage and attributes exists for the pilot area.
- A5 That land-cover/imperviousness data at useful resolution exists for the pilot area.
- A6 That historical flood/waterlogging records suitable for validation exist and are obtainable.
- A7 That the licences on all of the above permit hackathon use and public demonstration.

**Scientific / modelling**
- A8 That a surface-routing method exists which is both defensible and fast enough for a 0–3 h nowcast at our chosen resolution.
- A9 That a graph-based hydraulic treatment of the drainage network can meaningfully reproduce surcharge behaviour at the fidelity the PS implies.
- A10 That coupling surface and network at the timestep we choose is numerically stable.
- A11 That depth-based severity thresholds can be sourced from published guidance rather than invented.
- A12 That radar-derived rainfall is accurate enough at the scale of a city ward to drive street-level prediction. Radar-to-rainfall conversion carries uncertainty; the magnitude for our case is unmeasured.

**Product / operational**
- A13 That "street-level" for judging purposes means street *segments and intersections*, not individual addresses. (The PS says "streets or intersections", which supports this reading, but it is our interpretation.)
- A14 That a control-room operator would act on a 0–3 h advisory forecast — we have not spoken to one.
- A15 That the timestep and update cadence we pick match what the rainfall product actually delivers.

**Existing code / references**
- A16 That any of the six externally referenced repositories are usable, correctly licensed, and fit for purpose. **None has been inspected.** They are candidates for a later audit only, and nothing in this PRD depends on them.
- A17 That the YouTube reference video reflects a real, working system. It is treated as UI inspiration only and carries no authority over requirements.

---

## Related documents

- `docs/REQUIREMENTS.md` — the problem statement decomposed into testable engineering requirements, with traceability.
- `docs/DECISIONS.md` — decisions still open. **Currently: all major technical decisions are UNDECIDED.**
