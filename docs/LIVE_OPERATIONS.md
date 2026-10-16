# Live operations: rescue trips, live decisions, subscriber alerts, public page (design, 2026-10-11)

Design and acceptance criteria for DECISIONS D-38 to D-46. Research and sources: `docs/LIVE_OPERATIONS_RESEARCH.md`.
Everything here is a bounded pilot for the Hindmata / Dadar area. **[PROPOSED]**: MISC-04 does not ask for SMS/WhatsApp
subscriber alerts, a public page or GPS tracking; they extend its "warning and response coordination" goal.

## 1. Decision matrix

| Area | Implement now | Not now (why) | Status target |
|---|---|---|---|
| Shared incident store (D-38) | One SQLite file (Python stdlib) in `FLOODNET_STATE_DIR`, append-only audit table | Managed database for Vercel (no account, cost undecided); on Vercel the ops endpoints answer 503 instead of using per-instance `/tmp` | Local: IMPLEMENTED AND TESTED. Vercel: BLOCKED |
| Access control (D-39) | Roles dispatcher / responder (field team, shelter staff) / driver (one trip) / public; tokens stored as SHA-256 hashes; no dispatcher token configured => loopback clients only, never on Vercel | SSO, user accounts | IMPLEMENTED AND TESTED |
| Navigation (D-40) | FloodNet's arrival-time router stays the only routing authority; turn instructions generated from our own path geometry; mobile-web driver page; GPS by `watchPosition` while the page is open, Screen Wake Lock, 15 s polling | Google Navigation SDK and Mapbox Navigation SDK (no web SDK, cannot take our closures/depths, display terms require their map); self-hosted Valhalla (needs a container host); background GPS (impossible in a browser: W3C spec, WebKit 193946) | IMPLEMENTED; real-device GPS UNVERIFIED |
| Rescue trips (D-41) | A trip is a dispatched assignment (`assignment_id`, `plan_version`); the server holds execution facts (stage, positions, driver reports, reroutes); the dispatcher's ledger holds allocation; ledger status follows the trip stage through one pure function | A second, independent trip state | IMPLEMENTED AND TESTED |
| Route impact and rerouting (D-42) | Event -> validate -> store -> find affected trips -> reassess remaining route from the latest credible position -> required / optional / escalate proposal with reason -> operator acceptance -> driver acknowledgement -> audit | Automatic dispatch of a new route without a person | IMPLEMENTED AND TESTED |
| Vehicle restrictions (D-42) | Depth limit per vehicle type (existing, ASSUMED); reported road restrictions that bind only listed vehicle types | Height/width/weight (no data for the pilot roads) | IMPLEMENTED AND TESTED |
| Resource registry (D-43) | Teams, vehicles, shelters with AVAILABLE / ASSIGNED / OUT_OF_SERVICE (reason), capability lists, capacity; updates from registered responders with observation and receipt time, validation, staleness; planner can use it (`resources_source: "registry"`) | Government databases (no access); NIMS typing conformance | IMPLEMENTED AND TESTED; data SIMULATED |
| Subscriber alerts (D-44) | Consent records, encrypted numbers, geographic targeting, material-change and cooldown rules, review/approval, per-delivery lifecycle, idempotent queueing, signed provider callbacks, STOP handling | Real Indian SMS (DLT registration needed), WhatsApp production templates (business verification needed) | SIMULATED provider IMPLEMENTED AND TESTED; Twilio/MSG91/WhatsApp adapters IMPLEMENTED BUT UNVERIFIED live; live sends BLOCKED |
| Public alert page (D-45) | `/alerts/<public id>`: mobile-first, read-only, whitelisted snapshot, versioned, resolved/expired states, static SVG map, verified contacts only | Per-recipient links (tracking); interactive tiles by default | IMPLEMENTED AND TESTED |
| Official warnings (D-46) | Read the public SACHET CAP feed (ETag), show official alerts separately; CAP drafts stay `status=Draft` | Originating warnings, SACHET contribution, cell broadcast (FloodNet is not an alerting authority) | Feed reader IMPLEMENTED; live feed UNVERIFIED in tests |
| UI (D-47) | Pages Situation, Forecast, Roads, Response, Trips, Reports, Public alerts, Evidence; collapsible animated sidebar and work panel; mobile drawer | A new animation library (CSS transitions suffice) | IMPLEMENTED AND TESTED |

## 2. Incident clock

Reports, trips and plans use **incident minutes** (T+min from the forecast start), like the rest of FloodNet. The ops
store keeps one clock: `{mode, t_min, set_at}`. In `REPLAY` mode (scenario demonstrations) the time is the dispatcher's
decision time; in `LIVE` mode it advances with the wall clock from `set_at`. Every ops record also keeps wall-clock UTC
times (`observed_at`, `received_at`). GPS freshness is measured in wall-clock seconds.

## 3. Event and record model

Existing structures are reused: road events (`access/events.py`, D-32) and response events (`response/ledger.py`).
Additions:

| Field | Where | Meaning |
|---|---|---|
| `vehicle_types` | road closure | empty = every vehicle; otherwise the closure binds only those vehicle types (a restriction) |
| `origin` | road event | `dispatcher`, `driver:<trip>`, `responder:<id>`; set by the server, never by the client |
| `observed_at`, `received_at` | ops records | device or reporter time; server receipt time (UTC) |
| `superseded_by` / `retracted` | registry updates, alerts | correction links; nothing is edited in place |

Duplicate submissions: a client-supplied `client_ref` is unique per origin; a repeat returns the stored record.

## 4. Rescue-trip lifecycle (D-41)

| Trip stage (server) | Set by | Ledger status |
|---|---|---|
| `ASSIGNED` (trip created from an ACCEPTED or DISPATCHED assignment) | dispatcher | DISPATCHED |
| `EN_ROUTE` (driver started) | driver | DISPATCHED |
| `AT_PICKUP` / `AT_SETTLEMENT` | driver | DISPATCHED |
| `LOADED` (people on board) | driver | IN_PROGRESS |
| `AT_SHELTER` | driver | IN_PROGRESS |
| `COMPLETED` | driver or dispatcher | COMPLETED |
| `ABORTED` | dispatcher (with reason) | CANCELLED |

Blockage, delay, diversion and other incidents are trip events with a reason; a blockage also creates a FIELD_TEAM road
closure on that road (binding, D-32), which triggers reassessment of every active trip.

## 5. Route-impact assessment (D-42)

Credible position, in order: a GPS fix that is FRESH (age <= 30 s, ASSUMED) and within 60 m + reported accuracy of the
planned route (ON_ROUTE) or within 150 m of a road (OFF_ROUTE: reroute from the nearest road node); otherwise the
planned position estimate, labelled with the reason (no GPS, STALE <= 120 s, LOST, off the pilot network). The
remaining route (segments not yet left) is re-checked at its arrival times with the closures and forecast known now.

| Finding | Outcome |
|---|---|
| A remaining segment is CLOSED for this vehicle or AT_RISK | **REQUIRED** reroute proposal if a feasible route exists, else **ESCALATE** ("no feasible route; operator decision") |
| Destination shelter closed or full | ESCALATE (the planner chooses another shelter at the next replan) |
| No blocking issue, alternative saves >= 3 min, or removes >= 2 UNCERTAIN/UNKNOWN segments for <= 2 min extra | **OPTIONAL** proposal (ASSUMED thresholds) |
| Otherwise | NO_CHANGE |

Loop protection: a proposal is not repeated while an identical one (same route signature) is open or was rejected; an
optional proposal is suppressed for 5 incident minutes after the previous one; a route already rejected is never
re-proposed unless the current route becomes blocking. Proposals need dispatcher acceptance; the driver then
acknowledges. Every proposal records the event that triggered it, old and new route, decision time and reason.
Segments already driven are never re-checked, so a forecast change behind the vehicle changes nothing.

## 6. Subscriber alerts (D-44)

Subscriber: encrypted number (Fernet, key `FLOODNET_PII_KEY`), keyed HMAC for de-duplication, last four digits for
display, channels with consent `{granted_at, method, notice_version}`, area `{lonlat, radius_m}` or a pilot locality,
language, `verified`, `opted_out_at`. Numbers never appear in logs, URLs or API responses.

Targeting for an alert version: eligible when consent for the channel, verified, not opted out, and either (a) an
affected road or closure lies within the subscriber's radius, or (b) the network distance from the subscriber's
nearest road node to an affected road is <= 600 m (ASSUMED "nearby" buffer). Every decision is recorded with its reason.

Alert lifecycle: `DRAFT -> REVIEW_REQUIRED -> APPROVED -> SENDING -> SENT | PARTIAL | FAILED`, `CANCELLED`, and
`RESOLVED` / `EXPIRED` for the public page. Deliveries per (alert version, subscriber, channel):
`QUEUED -> SUBMITTED -> ACCEPTED -> SENT -> DELIVERED (-> READ)`, or `REJECTED`, `FAILED`, `UNKNOWN`, `SUPPRESSED`;
transitions only move forward; the idempotency key is stored before the provider call; a timeout becomes `UNKNOWN`,
never a blind retry. A new version is created only for a material change (status level, affected localities, confirmed
closures); a subscriber gets at most one message per alert per 30 minutes unless the level rises or the alert is
resolved. Opted-out subscribers receive nothing.

Live sending requires `FLOODNET_LIVE_MESSAGING=1`, provider credentials, a configured public base URL and an allow-list
of authorised test recipients; otherwise the SIMULATED provider records what would be sent.

## 7. Public alert page contract (D-45)

`GET /api/public/alerts/{public_id}` returns only: headline, locality text, FloodNet status level (`watch`, `advisory`,
`ended`), evidence blocks (model estimate / reported / confirmed closure) each with time and source, time window,
uncertainty sentence, precautions (BMC wording), map data (affected road lines, closures, area outline, context roads),
updates history (version, time, what changed), `updated_at`, `expires_at`, computed state (`active`, `stale`,
`resolved`, `expired`) and the disclaimer. Never: recipients, trips, team or vehicle positions, internal notes, event
ids from operators. Unknown and withdrawn ids give the same 404. Headers: `Cache-Control: public, max-age=30`,
`X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`.

## 8. Acceptance criteria (each has a test)

1. A route is rejected because of a confirmed closure; a restriction for trucks removes a faster route for a truck only.
2. A road on the remaining route becomes AT_RISK while a vehicle is en route -> REQUIRED proposal; no feasible route ->
   ESCALATE, never an invented route.
3. Two candidate routes are compared for the selected vehicle with ETA, uncertain segments and closures.
4. Team capability or registry status changes alter assignment feasibility.
5. GPS FRESH / STALE / LOST / off-network are classified and the fallback is labelled.
6. Repeating the same update does not create a second proposal; optional proposals respect the cooldown.
7. A forecast change affecting only segments already driven creates no proposal.
8. Conflicting and stale reports follow D-32 (existing suite) and registry updates out of order are not applied.
9. Only geographically eligible, verified, consenting subscribers are targeted; opted-out subscribers get nothing.
10. Processing the same event twice does not queue a second delivery; a provider failure is recorded as FAILED.
11. The public API returns the requested version, `resolved` and `expired` states, and no private field.
12. Message bodies contain the correct public link and STOP text; no phone number is ever returned.
13. The full flow: forecast -> plan -> trip -> closure en route -> reroute accepted -> alert approved -> simulated
    delivery -> public page, in a browser.

## 9. Verification, independent QA and known limits (2026-10-11)

**Independent QA review** (a separate reviewer, adversarial, with reproductions) found 2 HIGH, 8 MEDIUM and 8 LOW
defects. Each was reproduced in `backend/tests/test_qa_live_ops.py` before the fix:

| Finding | Fix |
|---|---|
| H1 any web page open on the dispatcher's machine could act as the dispatcher (loopback rule + `*` CORS) | without a token, the TCP client, the `Host` header and any `Origin` must all be this machine |
| H2 a late vehicle could be rerouted straight to the shelter, skipping the pickup | the position never moves past what the driver reported (stage-aware) |
| M1 a rejected detour came back as the vehicle moved; open proposals were superseded at every node | proposals are about a finding (the blocking roads), not about the exact remaining road list |
| M2 one failed `BEGIN` left the store locked | lock released on failure; 10 s busy timeout |
| M3 stale assessments, lost updates | re-checked inside the transaction; `stale_assessment` |
| M4 a finished trip's link could still report binding closures; duplicate reports | refused after COMPLETED/ABORTED; `client_ref` de-duplicates |
| M5 a synced event id `F2` could be overwritten by a field report | `F<number>` ids reserved for field reports |
| M6 a trucks-only restriction became a public "closure" | only closures for every vehicle reach the public page |
| M7 erasure left digits and distances | simulated outbox keeps no digits of real numbers; no distances in stored targeting; `secure_delete` + WAL checkpoint |
| M8 approval could apply to a version the reviewer had not seen | the API approves only the version sent by the reviewer |
| LOW: 500s on odd input, token hashes in the audit API, escalations never closing, link base from any origin | fixed and tested |

**Browser walkthrough** (Chrome, real local backend; `docs/validation/live_ops/*.png`): forecast, plan, trip, driver
link (token removed from the address bar), driver reports a blocked road, REQUIRED route change with comparison,
dispatcher accepts, driver acknowledges, advisory drafted, reviewed, approved and sent with the SIMULATED provider,
public page on a 390 px phone, simulated delivery reports, resolved. Found and fixed during the walkthrough: trips and
field reports were not tied to an incident (assignment ids repeat across incidents), a replaced proposal gave an
unclear error, the public page could show a cached version after resolution, the public page said "input data mode:
REAL" for a synthetic storm (now names the rainfall source and shows a demonstration banner).

**Known limits:** the shared store models one live incident per server (trips and field reports carry the browser's
incident id; server-side closures used for rerouting are not split by incident); in REPLAY mode the shared clock
follows the dispatcher's timeline, so scrubbing back hides later field reports (no-future-information rule); real-device
GPS, Screen Wake Lock on iOS/Android, `speechSynthesis` voices and `navigator.share` were not tested on phones; concurrent
sends and callbacks arriving during a send (QA LOW) are documented, not fixed; responders cannot yet be revoked; Hindi
and Marathi message templates are not written (human-reviewed translation required); the Vercel deployment has no
shared store (D-38) and was not redeployed or tested.


## Registry and shelters (2026-10-11)

Extends D-43. **[PROPOSED]**: MISC-04 asks for response coordination; dispatcher registration, imports, responder
links and volunteer-suggested shelters are our design for it, not items the problem statement names.

**Records and sources.** Every team, vehicle and shelter carries `source` / `registered_source` (`SIMULATED seed
(...)`, `dispatcher entry`, `import: <label>`, `volunteer-submitted, verified by dispatcher (candidate CSn)`, or the
latest `update RUn by <actor>`), `observed_at` (when it was true), `received_at` (when the server got it) and
`freshness`: CURRENT, STALE (older than 60 min, ASSUMED) or UNKNOWN (never confirmed, or the import did not say when).
Seed records stay SIMULATED and UNKNOWN until a person confirms them. Locations and numbers entered by a person are
tagged REPORTED (not checked by FloodNet).

**Validation (server is the authority; `lib/registry.js` mirrors it).** Capabilities only from the planner's existing
words (`evacuation`, `water rescue`, taken from the fixtures, D-34); vehicle types only those with a depth limit in
`config.VEHICLE_LIMIT_CM` except `pedestrian` (limits ASSUMED); locations inside the pilot box
`config.PILOT_BBOX_LONLAT`; names unique per kind; occupancy not above capacity; observed time not in the future.
Import (JSON list / `{"records": [...]}` or CSV with a `kind` column, at most 500 rows): a dry run lists every row's
errors; a commit is all-or-nothing in one SQLite transaction.

**Responders.** Roles `field_team`, `shelter_staff` (scoped to listed resources) and `volunteer` (no resource scope;
can only submit candidate shelters). The link `…/#/resources?rt=<token>` opens a responder view; the token is moved to
sessionStorage and removed from the address bar. Responder endpoints accept only a responder token (never the
dispatcher fallback), so updates are attributed to the sender. Links can be revoked.

**Candidate shelters.** `SUBMITTED → PERMISSION_CONFIRMED → SUITABILITY_CHECKED → CONFIRMED`, or `REJECTED` (reason)
from any open state. Permission records who (role, optional name), how, when; free text refuses phone numbers.
Suitability records access, water/sanitation, step-free entry (YES/NO/UNKNOWN), capacity estimate, and the
server-computed modelled depth: the largest depth on the nearest road (within 150 m, ASSUMED) in the forecast
published to live operations, or UNKNOWN (nothing published, no road near, road not linked to the model). It is a
street depth, not a measurement at the building. Candidates are a separate collection: the planner and the shelter
list never see one before CONFIRMED. Volunteers may have at most 20 open submissions (ASSUMED). Every step is audited.

**Planner.** `planner_resources()` adds records registered after the seed, and a registry vehicle on an active
*direct* trip (`trips` kind `direct`, Roads page) reaches the planner as unavailable ("on rescue trip TRn (started on
the Roads page)"); mission trips stay with the ledger.

**API** (`floodnet/api/registry_api.py`, under `/api/ops/registry`): `GET vocabulary`, `POST records`, `POST import`,
`POST responders/{id}/revoke`, `GET responder/me`, `POST responder/resources/{id}/updates`, `POST candidates`
(volunteer token, or dispatcher with `on_behalf_of`), `GET candidates`, `GET candidates/{id}/site-check`,
`POST candidates/{id}/permission|suitability|confirm|reject`.

**Tests:** `backend/tests/test_registry_records.py`, `test_registry_candidates.py`, `test_registry_api.py`;
`frontend-react/src/lib/registry.test.mjs`. Browser walkthrough on a scratch backend (2026-10-11): registration, import
preview and commit, responder and volunteer links on a 390 px phone, candidate to confirmed shelter, no console errors.

**Limits.** No government or agency database is connected; registered values are what an authorised person typed.
New capability words need a planning decision. The responder view lives inside the dashboard page (a separate
lightweight screen like the driver page is not built). Free-text phone filtering is a pattern, not a guarantee.
Site depth uses the nearest road only.

## 10. Rescue trips from the Roads page (D-49, 2026-10-11)

- **Flow:** vehicle (a registry record, or a bare vehicle type marked "not tracked") + A and B (search or two map
  clicks) → `POST /api/ops/trips/preview` → Start trip (`POST /api/ops/trips/direct`, idempotent on the preview's
  `client_ref`) → driver link → PLANNED → ACCEPTED (driver) → EN_ROUTE → ARRIVED → COMPLETED. ABORTED needs the
  dispatcher and a reason. The dispatcher can record a stage on the driver's behalf; the history says who did.
- **Server authority:** the route, ETA, hazards and freshness are recomputed at Start trip from the two points; the
  browser's geometry is never stored. A point more than 300 m from a mapped road is refused (ASSUMED). A registered
  vehicle that is not AVAILABLE or already on an active trip cannot start one.
- **No feasible route:** the preview says ESCALATE, nothing is drawn as a route, Start trip is refused (409) and the
  operator decides (other vehicle type, wait, other destination).
- **ETA:** ASSUMED ROAD SPEEDS + MODELLED FLOOD DELAY (Pregnolato et al. 2017, re-checked at the delayed minutes) +
  traffic UNKNOWN. Freshness chips: forecast SIMULATED / MODELLED / STALE / UNKNOWN; closures (operations log); traffic
  UNKNOWN; vehicle status (registry, SIMULATED seed, STALE).
- **Live changes:** the D-42 impact rules apply unchanged. A reroute keeps the road the vehicle is on (fix for a
  rerouting loop found while testing). Route comparisons show the free-flow arrival and the arrival with the modelled
  flood delay.
- **Map:** selected route = blue core, navy outline, white halo; alternatives thin slate dashes; hazards in their status
  colour on top; the next road of the selected trip in white dashes; a route legend while a preview or trip is shown.
- **Not done:** no live traffic feed (TomTom is the candidate: key and REST terms needed); no turn-by-turn voice beyond
  the driver page's existing read-aloud; GPS only while the driver page is open (browser limit).

## 11. Second independent QA review (D-52, 2026-10-11)

Reviewer's reproductions are part of the suite (`backend/tests/test_qa2_trips.py`, `test_qa2_registry.py`,
`test_qa2_plan_registry.py`); all failed against the reviewed code and pass now.

| # | Finding | Fix |
|---|---|---|
| H1 | Direct trip without GPS assumed to drive the timetable before the driver accepted | PLANNED/ACCEPTED: not moved, whole route re-checked; schedule runs from the EN_ROUTE report; overdue arrival keeps the last road (`impact._direct_schedule`) |
| H2 | One vehicle on a direct and a mission trip; dashboard planned with fixtures, not the registry | mission trip refuses a vehicle on an active direct trip; the dashboard plans with `resources_source=registry` when live operations are on; the Roads picker also treats plan commitments as busy |
| M1 | Closure on the road being driven ignored | that road is checked; if blocked, the position moves to its start and the trip is rerouted |
| M2 | ARRIVED trips got reroute proposals | ARRIVED: NO_CHANGE |
| M3 | Start trip could use a preview for earlier points | a preview is shown and startable only for the request on screen |
| M4 | Planning from the registry needed no login | `resources_source=registry` requires the dispatcher |
| M5 | Escalated / blocked trip drawn as a good route | escalated: red dashed; hazards ahead keep their status colour |
| M6 | Flood-delay comparison counted the road being driven for the candidate only | excluded from both columns |
| M7 | Unknown or cached data labelled current; "LATEST" matched "TEST" | vehicle chip UNKNOWN/STALE from the registry; cached input STALE; whole-word matching |
| M8 | D-51 wording vs live gate; preview mode not bound to send | D-51 corrected; send needs the preview's mode, LIVE only after a LIVE preview, retries never change mode |
| L1, L2 | NaN/inf coordinates and 1e400 numbers were a 500 | 422 / row error |
| L3 | Start key reused across incidents returned an old trip | a key repeats only the same incident and points |
| L4 | Flood delay silently 0 near the forecast end | counted as unknown depth |
| L5 | Phone numbers with brackets/dots, and in organisation/base/address | wider pattern on all free-text fields (a pattern, not a guarantee) |
| L6 | Roads trip card hid "attention" | shown |
| L7 | Lifecycle by colour only; whole preview live region | state words for screen readers; only the decision line is a live region |
| L8 | Browser could rename the registry vehicle | registry name wins |
| L9 | Pedestrian offered as a vehicle type | **accepted**: a team on foot is a real option; its flood delay is NOT MODELLED and says so |

## 12. Navigation mode and live traffic (D-54, 2026-10-11)

- **Start navigation** (Roads page, after a preview): starts the direct trip and opens navigation over the map on
  this device. The work panel slides away and comes back on Exit. The trip stays active on exit ("Navigate" on its
  card resumes; without a stored token a new driver link is made first, which invalidates the old one).
- **Guidance:** next turn + distance (from `ops/navigation.py` maneuvers), the instruction after it, remaining
  minutes and distance, destination, flood hazards on the remaining route, upcoming turns under Details.
- **Position:** `navigator.geolocation.watchPosition` (high accuracy), posted to `/api/ops/driver/position` with the
  trip token at most every 15 s; the server re-checks the route after each fix. Needs https or localhost; the GPS
  chip says "waiting", the fix age and accuracy, "permission refused", or "offline". No fix → the server's planned
  estimate is used and the screen says so. Navigation never fabricates movement.
- **Changes while driving:** a REQUIRED/OPTIONAL proposal shows "safer route found" with *Take the safer route*
  (dispatcher decision made on this device, then the driver acknowledgement) or *Keep current route for now*;
  escalation shows "No safer route found … stop somewhere safe and decide with the dispatcher".
- **Risk freshness:** "Risk from a SIMULATED/MODELLED forecast, route checked at T+n" or "may be outdated" when the
  forecast input is STALE/UNKNOWN or the last check is more than 30 model minutes old (ASSUMED).
- **Live traffic:** `POST /api/traffic/segments`, `GET /api/traffic/status`; the preview samples the route's roads
  (≤ 40, evenly spread) and shows LIVE TRAFFIC with the fetch time, coverage, ETA with and without traffic, a
  Refresh button (bypasses the 60 s cache) and an automatic refresh every 60 s while live. Dotted
  green/yellow/red lines over the route; flood status colours stay underneath. Without `TOMTOM_API_KEY`: "Live
  traffic unavailable — using estimated travel time". Traffic never changes the route.
- **Verified:** Chrome walkthrough `docs/validation/navigation/` (GPS supplied to the browser by the test harness;
  the app's own geolocation code path ran unchanged). Not verified: a real phone, and TomTom live answers (no key).
