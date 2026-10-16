# Messaging and public alerts (implementation, 2026-10-11)

Implements DECISIONS D-44 (subscriber SMS/WhatsApp advisories), D-45 (public alert page) and the feed reader of D-46.
Design: `docs/LIVE_OPERATIONS.md` §6-§8. Evidence: `docs/LIVE_OPERATIONS_RESEARCH.md` §6-§8.
**[PROPOSED]**: MISC-04 does not ask for subscriber messaging or a public page.

## What is implemented

| Module (`backend/floodnet/`) | Does |
|---|---|
| `messaging/subscribers.py` | Subscribers: E.164 check (+91 must be a 6-9 mobile), Fernet-encrypted number, keyed HMAC for duplicates and STOP lookup, consent per channel `{granted_at, method, notice_version}`, area (point + 100-2000 m radius, or a pilot locality from OpenStreetMap), language, verified, opt-out, DPDP erasure, SIMULATED seed |
| `messaging/targeting.py` | Impact zone from the published forecast and closures; one decision with a reason per subscriber |
| `messaging/alerts.py` | Lifecycle, versions, texts, cooldown, idempotent deliveries, callbacks, STOP |
| `messaging/providers.py` | Provider interface, SIMULATED provider, Twilio and MSG91 adapters, status mapping, webhook signature checks, live gate |
| `messaging/public.py` | Whitelisted public snapshot for `/alerts/<public_id>` |
| `messaging/sachet.py` | Read-only SACHET CAP RSS reader (ETag, DOCTYPE/ENTITY rejected) |
| `messaging/devtest.py` | Designated development test recipients (server configuration only) and the "Send test SMS" workflow (2026-10-11) |
| `api/messaging_api.py` | `APIRouter`; must be included by `api/main.py` (not done by this module) |

Tests: `backend/tests/test_messaging_{subscribers,targeting,providers,alerts,api,devtest,devtest_api,webhooks}.py`,
`test_public_alerts.py`; frontend `src/api/messaging.test.mjs`, `src/lib/publicAlert.test.mjs`.

## Development test recipients and "Send test SMS" (2026-10-11) [PROPOSED]

- **Who:** only the numbers in the server variable `FLOODNET_DEV_TEST_RECIPIENTS` (comma-separated; each entry a
  number with an optional label after `:`). No number is in code, tests, frontend or docs; the API returns counts and
  `•••• 1234` only. Channels: `FLOODNET_DEV_TEST_CHANNELS` (default `sms`).
- **Seeding** (dispatcher button / `POST /api/ops/subscribers/seed-dev-test {locality_id}`): each configured number
  becomes an encrypted subscriber with consent method "designated development test recipient", verified, `dev_test`,
  label kept in the PII vault. Refused (403) in production (`VERCEL=1` or `FLOODNET_ENV=production`), where the list is
  also ignored. An opted-out number is never re-activated by seeding.
- **Test send** (`/api/ops/test-sends/preview`, `POST /api/ops/test-sends`): only to a `dev_test` subscriber still
  listed in the variable, ACTIVE, with consent for the channel. Fixed text; the preview shows it with characters,
  GSM-7/UCS-2 and SMS segments and the mode; the send must quote the preview's `text_sha256`. Provider choice is the
  normal live gate with `purpose="test"` (dev recipients count as allow-listed for test sends only, never for
  advisories). Without `FLOODNET_LIVE_MESSAGING=1`, credentials and a public base URL it is SIMULATED and labelled so.
- **Idempotency:** one browser key per confirmation; delivery id `TEST:<key>`; a double click returns the first record.
  A record stuck in SUBMITTED becomes UNKNOWN after 120 s and is never re-sent. **Retry** only for FAILED with no
  provider message id (the provider never took it), at most 3 attempts, each with its own provider key.
- **States** (`providers.STATUS_MEANING`): ACCEPTED/SENT are not delivery; DELIVERED/READ come from provider reports;
  FAILED, REJECTED, UNKNOWN (never retried), SUPPRESSED. Records live in `deliveries` (kind `test`), so signed provider
  callbacks update them; every step is in the audit with the dispatcher as actor.

## Subscriber editing and number input (2026-10-11)

- `PATCH /api/ops/subscribers/{id}`: language, area (point or pilot locality), verified, permitted channels. A kept
  channel keeps its consent record; a new channel needs `method`; a removed channel is recorded in
  `consent_withdrawn`. The number cannot be edited (erase and add again). Only ACTIVE subscribers (409 otherwise).
- `subscribers.normalize_input` accepts Indian forms (10 digits starting 6-9, a leading 0, `91…`, `0091…`,
  `+91 (0) …`, spaces/dashes/brackets, Devanagari digits) and returns E.164; `normalize_e164` stays strict (ASCII).
  Known limit: a person who opted out cannot be re-subscribed by the dispatcher except by erasure and a fresh add.

## MSG91 and WhatsApp details (from `docs/RESEARCH_ALERTING_AND_INTERNATIONAL.md`, 2026-10-11)

- MSG91 DLR webhook: with the correct shared secret it always answers 200 (MSG91 pauses a webhook that answers
  4xx/5xx), including unparseable bodies (recorded in `provider_webhook_events` with outcome and size only) and a store
  outage. Both report shapes are read (flat Webhook-New events and the older `data[].report[]`). `telNum` is dropped by
  the parser and never stored. Requests carry `CRQID` = our delivery id, used to match a report when `requestId` is
  unknown (only for a delivery of the same provider); its placement in the Flow request is [UNVERIFIED] live.
- WhatsApp via Twilio: free text is delivered only within 24 h of the recipient's last message (Sandbox: after
  `join <word>`, session ends 3 days after joining); outside it an approved template is needed. Templates are not
  implemented, so such a send is expected to fail; the UI and status say so.

## Public page additions (2026-10-11)

- Plain-language summary built only from the snapshot (modelled depth, closures, reports) with the time horizon;
  the four evidence types shown separately (model estimate, reported, operator-confirmed closure, authorised official
  warning, which FloodNet never claims); confirmed closures; latest change.
- Official warnings: FloodNet's cached SACHET items naming Maharashtra or Mumbai, quoted as received with their link;
  otherwise an honest "no copy / none matching" text and the SACHET link.
- Interactive SVG map: zoom, drag when zoomed, tap a road (or the road list) for its kind and modelled depth
  (`map.affected_roads[].depth_cm`, whole cm, model estimate). Optional street map (Leaflet + the dashboard's OSM tile
  source and attribution) is a separate chunk loaded only when the reader asks.
- No sign-up on the page: it explains that messages go only to people whose consent an operator recorded, and that
  replying STOP stops them. Privacy whitelist and identical 404s unchanged.

## Provider status

| Provider | Status | Reason |
|---|---|---|
| SIMULATED | IMPLEMENTED AND TESTED (default) | Records what would be sent; outbox keeps only `•••• 1234` |
| Twilio SMS / WhatsApp | IMPLEMENTED BUT UNVERIFIED | Tested only with an injected fake HTTP function. No live send was made |
| MSG91 SMS (Flow API) | IMPLEMENTED BUT UNVERIFIED | Request and DLR shapes follow the provider docs as summarised in the research; not checked against the live API |
| WhatsApp Cloud API | Webhook only (status + STOP), UNVERIFIED | Sending through the Cloud API is not implemented |
| Real SMS | BLOCKED | No SMS sender configured (`TWILIO_SMS_FROM` or the MSG91 keys). MSG91 needs DLT registration (legal entity, header, templates, URL whitelisting; TRAI TCCCPR 2025); Twilio's international route to India (random numeric sender, no DLT) needs an upgraded, not trial, account |
| WhatsApp production | BLOCKED | Business verification and approved templates are needed |

## Lifecycle

`DRAFT -> REVIEW_REQUIRED -> APPROVED -> SENDING -> SENT | PARTIAL | FAILED`; `CANCELLED` only before anything was
published; `RESOLVED` publishes "No longer active" and messages only subscribers whose earlier message was accepted.
Model-derived content always needs a person's approval, and every new version goes back to `REVIEW_REQUIRED`.
Deliveries per (alert, version, subscriber, channel): `QUEUED -> SUBMITTED -> ACCEPTED -> SENT -> DELIVERED (-> READ)`,
or `REJECTED`, `FAILED`, `UNKNOWN` (timeout: never retried blindly), `SUPPRESSED` (opted out, cooldown, erased).
Statuses only move forward; callbacks are de-duplicated on (provider, message id, status).

## Targeting rule (thresholds ASSUMED)

Affected road: modelled max depth in `[now, now + window]` >= 15 cm (watch), >= 30 cm or a confirmed closure (advisory;
Pregnolato et al. 2017 report about 30 cm as impassable for 2WD cars, context only); unconfirmed public reports are
shown as REPORTED. Eligible subscriber: consent for an alert channel, verified, not opted out, and an affected road
within the area radius, or within 600 m by road from the subscriber's nearest road node. New version only for a
material change (level, localities, road set, confirmed closures); at most one message per alert per 30 min unless
the level rises or the alert is resolved.

## Privacy measures

- Number, its HMAC, last four digits and the area are kept in a `pii_vault` table outside the append-only audit, so
  erasure really deletes them; the audited record holds only consent, flags and timestamps.
- No endpoint, log line, URL or message body contains a subscriber's number; dispatcher views show `•••• 1234`.
- One public link per alert (128 bits, the same for every recipient: no per-person tracking); the public JSON is
  built field by field and has no subscriber, delivery, trip, vehicle, approver or operator-event data.
- Public headers: `Cache-Control: public, max-age=30`, `X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`;
  unknown, unpublished and cancelled ids give the same 404 body.
- Retention after opt-out: 30 days, then erasure (`purge_expired`). ASSUMED, not a legal determination.

## Environment variables (names only)

`FLOODNET_PII_KEY`, `FLOODNET_LIVE_MESSAGING`, `FLOODNET_MESSAGING_ALLOWLIST`, `FLOODNET_PUBLIC_BASE_URL`,
`FLOODNET_DEV_TEST_RECIPIENTS`, `FLOODNET_DEV_TEST_CHANNELS`, `FLOODNET_ENV`,
`FLOODNET_SMS_PROVIDER` (`twilio` or `msg91`), `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_API_KEY_SID`,
`TWILIO_API_KEY_SECRET`, `TWILIO_SMS_FROM`, `TWILIO_WHATSAPP_FROM`, `MSG91_AUTHKEY`, `MSG91_TEMPLATE_ID`,
`MSG91_WEBHOOK_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `SACHET_RSS_URL`.

## Before any live send

1. DLT registration of a legal entity, header and every template (Indian SMS); URL whitelisting of the public base URL.
2. Approved WhatsApp templates and business verification.
3. A configured `FLOODNET_PUBLIC_BASE_URL`, provider credentials, and `FLOODNET_LIVE_MESSAGING=1`.
4. Only allow-listed, consenting test recipients (`FLOODNET_MESSAGING_ALLOWLIST`).
5. Human-reviewed Hindi and Marathi texts before they are used (English only today).
6. **Explicit approval from the project owner for the first live send.** Nothing in this repository has sent a real
   message.
