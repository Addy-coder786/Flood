# Live operations: research findings (2026-10-11)

Research for rescue trips, live decision handling, subscriber alerts and the public alert page (DECISIONS D-38 to
D-45). Five independent research workstreams, primary sources first. Labels: **[V]** read in the primary source,
**[S]** secondary or a search summary of a primary page that could not be opened, **[U]** unverified. Nothing here is
an integration with, or compliance with, any of the systems described.

## 1. United States: FEMA NIMS

- NIMS Third Edition (October 2017) is the current doctrine; FEMA's components page (updated 12 Dec 2025) still links
  it [V] https://www.fema.gov/sites/default/files/2020-07/fema_nims_doctrine-2017.pdf ,
  https://www.fema.gov/emergency-managers/nims/components
- Resources are described by **capability, category, kind and type**; a type is a *minimum* capability level (Type 1
  above Type 2) [V, doctrine pp. 6-7]. Inventory fields include status, typing definition, home and present location,
  owner, point of contact, mutual-aid readiness [V, p. 9].
- Six-step resource management: identify requirements, order and acquire, mobilize, track and report, demobilize,
  reimburse and restock [V, pp. 12-16]. A request names quantity, kind and type **or the required capability and
  intended use**, acceptable substitutes, required arrival time and reporting location; the provider reports any
  discrepancy [V, p. 13]. Unfilled requests go to mutual aid or the next level of government [V, p. 13].
- Resource status is **Assigned / Available / Out of Service** (mechanical, rest or personnel reasons); changes are
  documented (ICS 210) [V, pp. 91-92].
- FEMA Swiftwater/Flood Search and Rescue Team typing (RTLT 8-508-1020 v1.8, March 2021): minimum 16 / 12 / 6 people for
  Type 1 / 2 / 3; "nationally typed resources represent the minimum criteria" [V]
  https://rtlt.preptoolkit.fema.gov/Public/Resource/ViewFile/8-508-1020?type=Pdf

**Adopted (as FloodNet design, not NIMS compliance):** settlement needs state the required *capability*, never a unit;
resource status AVAILABLE / ASSIGNED / OUT_OF_SERVICE with a reason; every candidate rejected for an assignment gets a
named reason; a need nobody can fill becomes an explicit escalation request with the same fields.

## 2. Japan: SIP4D, SOBO-WEB, Emergency Fire Response Teams

- SIP4D (NIED) is a hub between information providers and users; its two core functions are automatic format
  conversion and "logical integration" of the same kind of data from several agencies; written into the Basic Disaster
  Management Plan in FY2021 [V] https://www.bosai.go.jp/information/kenkyudogaposter/pdf/2025_79.pdf ; Usuda et al.
  2017, J. Disaster Res. 12(5):1002, doi:10.20965/jdr.2017.p1002 [V, abstract].
- SIP4D-ZIP (JSA-S1016:2023): metadata JSON (author, licence, information-type code, a time-track id), GeoJSON
  features, attribute schema, legend; a draft revision adds retraction/correction and usage scope [V, NIED posters].
- SOBO-WEB (Cabinet Office) has operated since April 2024 for about 1,900 organisations and shares disaster information
  as geospatial layers based on the EEI list (road closures, isolated settlements, shelters, water levels ...) [V]
  https://www.bousai.go.jp/taisaku/soboweb/index.html
- Emergency Fire Response Teams (Fire Organization Act Art. 44) are organised in typed companies, including water-borne
  and special-equipment units used for flooded-area rescue [V, FDMA English brochure; white-paper detail S].

**Adopted:** one shared, timestamped incident picture used by every page (dispatcher, driver, public), each record
with source, observation time, receipt time and a correction/retraction link. **Not found:** how SIP4D resolves
conflicts or freshness; FloodNet's tier and staleness rules (D-32) are our own policy.

## 3. China: flood control, digital twins, resource dispatch

- **Deployed (official):** the State Council investigation report on the Zhengzhou 20 July 2021 flood names an unsound
  warning-response linkage ("预警与响应联动机制不健全") and asks for integrated warning and response management
  ("强化预警和响应一体化管理") [V] https://www.mem.gov.cn/xw/bndt/202201/t20220121_407106.shtml . Detailed timelines
  quoted in media are [S] and not used.
- **Pilot:** the Ministry of Water Resources "digital twin river basin" pilots (2022-2023) and the "four pre-"
  (forecast, warning, rehearsal, plan) concept [S]; scope is river basins and water works, not urban drainage [U].
- **Research:** emergency-vehicle routing in dynamic urban floods (Zhang et al. 2024, China Safety Science Journal
  34(9):217-224) [V, abstract]; Shanghai inundation-road impact (Yin et al. 2016, J. Hydrology 537:138) [S].
- Cross-region relief allocation is request-based from provincial headquarters [S].

**Adopted:** every forecast change, report and closure is linked to its effect on the response plan and active trips
(the Zhengzhou lesson); unmet demand is escalated explicitly. **Not claimed:** a digital twin, "four pre-" compliance,
algorithmic cross-region dispatch.

## 4. Flood-aware routing and facility allocation

| Study | Use in FloodNet |
|---|---|
| Alabbad, Mount, Campbell, Demir (2024), *Urban Informatics* 3:10, doi:10.1007/s44212-024-00040-0 (the Iowa web decision-support framework for road accessibility and emergency-facility allocation) [V record] | Benchmark of scope (road status, routing, facility allocation for non-experts); FloodNet's arrival-time evaluation is time-dependent, theirs uses design floods |
| Green et al. (2017), *NHESS* 17:1-16, doi:10.5194/nhess-17-1-2017 [V] | 25 cm as an emergency-vehicle restriction (from one UK fire service); an uninformed responder took +200% longer on one route, so closures must reach drivers |
| Pregnolato et al. (2017), *Transp. Res. D* 55:67-81, doi:10.1016/j.trd.2017.06.020 [V] | Depth-disruption curve for cars; 30 cm impassable for 2WD cars, 45 cm cited for 4WD. Recorded as literature context for the ASSUMED vehicle limits; not calibrated for Mumbai, not changed in this round |
| Gama, Santos, Scaparra (2016), *EURO J. Comput. Optim.* 4(3):299, doi:10.1007/s13675-015-0058-3 [V abstract] | Multi-period shelter MIP: rejected at our scale (greedy with capacity constraints kept, D-28) |

## 5. Navigation providers and the browser

| Criterion | Google | Mapbox | OSRM / Valhalla / GraphHopper / ORS | FloodNet router (chosen) |
|---|---|---|---|---|
| Web turn-by-turn SDK | None: Navigation SDK is Android/iOS/Flutter/React Native [V] | None: Navigation SDK is Android/iOS [V] | Engines return steps; UI is ours | Instructions from our own path geometry |
| Our closures and flood depth | Routes API avoid options are tolls/highways/ferries/indoor only, best-effort [V] | `exclude=point()` up to 50, beta, best-effort [V] | Valhalla `exclude_polygons`, ORS `avoid_polygons`, GraphHopper areas; OSRM batch only [V] | Native: depth at arrival time and binding closures |
| Terms with our Leaflet/OSM map | Routes results on a map must be on a Google Map [V] | Map Matching results on a Mapbox map [V] | Open licences | No third-party terms |
| Vehicle restrictions | `vehicleInfo` not a dimension filter [U] | height/width/weight on driving profiles [V] | Valhalla/ORS truck dimensions [V] | Depth limit per vehicle type + reported road restrictions per vehicle type |
| India live traffic | Traffic layer covers India [V] | [U] | None | None (stated) |

Browser limits [V]: Geolocation needs HTTPS and a permission prompt, and the W3C spec delivers no position updates while
the page is hidden (https://www.w3.org/TR/geolocation/); iOS does not run web pages in the background (WebKit bug
193946, RESOLVED LATER); Screen Wake Lock works on HTTPS and is released when the page is hidden; Background Sync is
limited availability; `speechSynthesis` is widely available. **Consequence:** a mobile web driver page works only while
it is open and in the foreground; background tracking needs a native app.

## 6. Messaging in India

- TRAI TCCCPR (Second Amendment, 12 Feb 2025): commercial communication from a sender not registered with an access
  provider is unsolicited; headers carry -P/-S/-T/-G; templates have fixed-text and variable rules; URLs and call-back
  numbers must be whitelisted [V] https://trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf . A
  subscription alert fits "Service-Implicit"; "-G" is only for government direction [V, our reading of the
  definitions; the operator decides].
- DLT onboarding needs a legal entity's documents, a header, templates and URL whitelisting [S]: not achievable during a
  hackathon. MSG91 Flow API and delivery-report webhook documented (status 0 sent, 1 delivered, 2 failed, 9 NDNC,
  16/25 rejected, 17 blocked); **no webhook signature documented** [V provider docs]. MSG91 short links track clicks per
  mobile number [V]: not used.
- Twilio: India domestic SMS needs DLT and a registered sender; shortened URLs not allowed; status values
  queued/sending/sent/delivered/undelivered/failed/read; status callbacks are signed [V]; trial accounts send only to
  verified numbers and do not support custom bodies [V] https://www.twilio.com/docs/usage/trials .
- WhatsApp Business: opt-in required and opt-outs must be respected [V] https://whatsappbusiness.com/policy/ ;
  templates, status webhooks (sent/delivered/read/failed) and `X-Hub-Signature-256` HMAC-SHA256 over the raw body [S].
- "Accepted by the provider" (request id, `queued`, `wamid`) is not delivery; only a carrier receipt or a WhatsApp
  `delivered` status is.

## 7. Official public warnings in India

- SACHET is NDMA's CAP-based Integrated Alert System, developed and maintained by C-DOT; alerts come from NDMA, the
  State/UT disaster management authorities and alert-generating agencies (IMD, CWC, INCOIS ...) [V]
  https://sachet.ndma.gov.in/ . A public RSS feed exists (e.g. `rss_maharashtra.xml`) declaring public domain; the
  integration guide asks consumers to use ETag / If-None-Match [V per research agent; not re-fetched by the lead].
- No documented route lets a non-government organisation originate or contribute alerts [U]; cell broadcast is
  government-only [S].
- CAP 1.2: `status` Actual/Exercise/System/Test/Draft, `msgType` Alert/Update/Cancel, `references` [V]
  https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html
- DPDP Act 2023 and Rules (notified November 2025): specific consent notice, withdrawal at any time, erasure, breach
  notification [V PIB explainer]; most rules commence about May 2027 [S].

## 8. Public alert page

- Complete warnings name source, hazard and impact, location, protective action and time, plus where to learn more;
  an explicit "no longer active" follow-up raises trust (Sutton, EENA 2025, citing Sutton & Kuligowski 2019,
  doi:10.1061/(ASCE)NH.1527-6996.0000324) [V].
- Text location outperformed a map alone in an earthquake-warning study [V abstract]; GOV.UK flood pages put status,
  "Updated" time and the next update first, and keep ended warnings visible as removed [V]
  https://check-for-flooding.service.gov.uk/ . The Environment Agency's level names ("Flood alert", "Flood warning")
  and Japan's numbered levels belong to those authorities and are **not** reused.
- Verified contacts: **112** (MHA ERSS) [V] https://www.mha.gov.in/en/commoncontent/emergency-response-support-system-erss ;
  **1916** BMC Emergency Operation Centre and BMC precautions ("Don't walk through flowing water", "only half a metre of
  flood water can carry a car away", "Avoid contact with flood water", "Stay away from power lines") [V] BMC CONNECT
  directory, https://dm.mcgm.gov.in/ . NDMA 1078 and ward numbers were not verified and are omitted.
- WCAG 2.2: targets at least 24x24 px, no colour-only meaning, reflow at 320 px, reduced motion [V]. A link preview is
  cached by WhatsApp, so Open Graph text must not depend on time or severity [S].

## Not established

AWS rainfall units; SIP4D conflict handling; whether a `*.vercel.app` URL can be DLT-whitelisted; Indian vehicle wading
depths; full Zhengzhou report text; WhatsApp webhook retry semantics; MSG91 idempotency.
