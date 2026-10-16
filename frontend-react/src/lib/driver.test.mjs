// Run with:  npm run test:unit
//
// Driver trip page (DECISIONS D-40, D-41, D-42; docs/LIVE_OPERATIONS.md §4-§5; research §5): the pure helpers
// behind src/driver/DriverApp.jsx. Written test-first.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  DRIVER_TOKEN_KEY, tokenFromHash, STAGE_LABELS, stageButtons, stageStatus, completionQuestion, formatDistance, freshnessText,
  shouldPost, positionPayload, nextInstruction, isNewInstruction, geoErrorMessage, geoSupport, REPORT_KINDS,
  upcomingRoads, reportPayload, routeLines, offlineText, hazardRows, upcomingManeuvers,
} from './driver.js'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => readFileSync(join(SRC, p), 'utf8')
const TOKEN = 'q1W2e3R4t5Y6u7I8o9P0a_S-d1F2g3H4'          // secrets.token_urlsafe(24) shape: 32 url-safe chars

const VIEW = {
  trip_id: 'T1', stage: 'EN_ROUTE', route_version: 2, vehicle_type: 'truck', people: 12, team: 'Team A',
  vehicle: 'Truck 1', settlement: 'Settlement S', shelter: 'Shelter H',
  remaining: [
    { kind: 'to_settlement', to: 'Settlement S', arrive_t_min: 14, dwell_min: 5,
      route: { type: 'LineString', coordinates: [[72.84, 19.0], [72.841, 19.001]] },
      segments: [{ seg_id: 's1', name: 'Tilak Road', status: 'USABLE', depth_cm: 2, enter_t_min: 10 },
        { seg_id: 's2', name: 'Dr Ambedkar Road', status: 'UNCERTAIN', depth_cm: 18, enter_t_min: 12 }],
      maneuvers: [
        { type: 'depart', modifier: null, instruction: 'Head north on Tilak Road', street: 'Tilak Road', distance_m: 420.4, at_lonlat: [72.84, 19.0], seg_index: 0 },
        { type: 'turn', modifier: 'left', instruction: 'Turn left onto Dr Ambedkar Road', street: 'Dr Ambedkar Road', distance_m: 180, at_lonlat: [72.841, 19.0], seg_index: 1 },
        { type: 'arrive', modifier: null, instruction: 'Arrive at Settlement S', street: null, distance_m: 0, at_lonlat: [72.841, 19.001], seg_index: 2 },
      ] },
    { kind: 'to_shelter', to: 'Shelter H', arrive_t_min: 30, dwell_min: 0,
      route: { type: 'Feature', geometry: { type: 'LineString', coordinates: [[72.841, 19.001], [72.843, 19.004]] } },
      segments: [{ seg_id: 's2', name: 'Dr Ambedkar Road', status: 'UNCERTAIN', depth_cm: 18, enter_t_min: 20 },
        { seg_id: 's3', name: '', status: 'AT_RISK', depth_cm: 40, enter_t_min: 25 }],
      maneuvers: [] },
  ],
  hazards_ahead: [{ seg_id: 's3', name: '', status: 'AT_RISK', depth_cm: 40, enter_t_min: 25, reason: 'forecast depth reaches the vehicle limit' }],
  position: null, route_change: null, escalation: null,
  next_stages: ['AT_SETTLEMENT', 'LOADED', 'AT_SHELTER', 'COMPLETED'],
  location_sharing: { interval_s: 15, fresh_s: 30, note: 'n' }, disclaimer: 'd',
}

// ───────────────────────────────────────────── token
test('tokenFromHash reads #t=<token> and rejects anything that is not a url-safe token', () => {
  assert.equal(DRIVER_TOKEN_KEY, 'floodnet.driverToken')
  assert.equal(tokenFromHash(`#t=${TOKEN}`), TOKEN)
  assert.equal(tokenFromHash(`t=${TOKEN}`), TOKEN)
  assert.equal(tokenFromHash(`#x=1&t=${TOKEN}`), TOKEN)
  for (const bad of ['', '#', '#t=', '#t=short', `#t=${TOKEN}<b>`, `#t=${'a'.repeat(300)}`, '#/trips', null, undefined]) {
    assert.equal(tokenFromHash(bad), null, String(bad))
  }
})

// ───────────────────────────────────────────── stages
test('stage labels are plain words; buttons follow next_stages only, COMPLETED needs confirmation', () => {
  assert.deepEqual(STAGE_LABELS, {
    EN_ROUTE: 'Start trip', AT_PICKUP: 'Arrived at pickup point', AT_SETTLEMENT: 'Arrived at settlement',
    LOADED: 'People on board', AT_SHELTER: 'Arrived at shelter', COMPLETED: 'Trip completed',
    ACCEPTED: 'Accept trip', ARRIVED: 'Arrived at destination',
  })
  const b = stageButtons(VIEW)
  assert.deepEqual(b.map((x) => x.stage), ['AT_SETTLEMENT', 'LOADED', 'AT_SHELTER', 'COMPLETED'])
  assert.deepEqual(b.map((x) => x.primary), [true, false, false, false])
  assert.deepEqual(b.map((x) => x.confirm), [false, false, false, true])
  assert.equal(b[0].label, 'Arrived at settlement')
  assert.deepEqual(stageButtons({ next_stages: ['ABORTED', 'BOGUS'] }), [], 'unknown or dispatcher-only stages are hidden')
  assert.deepEqual(stageButtons(null), [])
  assert.equal(stageStatus('ASSIGNED'), 'Assigned – not started')
  assert.equal(stageStatus('LOADED'), 'People on board')
  assert.equal(stageStatus('ABORTED'), 'Trip stopped by the dispatcher')
  assert.equal(stageStatus('ZZZ'), 'ZZZ')
})

test('direct trips (Roads page, D-49): accept, drive, arrive, complete', () => {
  const b = stageButtons({ kind: 'direct', next_stages: ['ACCEPTED', 'EN_ROUTE', 'ARRIVED', 'COMPLETED'] })
  assert.deepEqual(b.map((x) => x.label), ['Accept trip', 'Start trip', 'Arrived at destination', 'Trip completed'])
  assert.equal(b[0].primary, true)
  assert.equal(stageStatus('PLANNED'), 'New trip – accept it to begin')
  assert.equal(stageStatus('ACCEPTED'), 'Accepted – not started')
  assert.equal(stageStatus('ARRIVED'), 'At the destination')
  assert.match(completionQuestion({ kind: 'direct' }), /destination/)
  assert.match(completionQuestion({ kind: 'mission' }), /shelter/)
})

// ───────────────────────────────────────────── formatting
test('formatDistance: metres rounded to 10 m below 1 km, km with one decimal above', () => {
  assert.equal(formatDistance(420.4), '420 m')
  assert.equal(formatDistance(4), '0 m')
  assert.equal(formatDistance(995), '1.0 km')
  assert.equal(formatDistance(1234), '1.2 km')
  assert.equal(formatDistance(12500), '13 km')
  assert.equal(formatDistance(null), '–')
  assert.equal(formatDistance(NaN), '–')
  assert.equal(formatDistance(-5), '–')
})

test('freshnessText: words and a glyph for FRESH / STALE / LOST / none', () => {
  assert.deepEqual(freshnessText(null), { glyph: '○', tone: 'none', text: 'No location received by the dispatcher yet' })
  assert.deepEqual(freshnessText({ freshness: 'FRESH', age_s: 8.4 }), { glyph: '●', tone: 'fresh', text: 'Location current (8 s old)' })
  assert.deepEqual(freshnessText({ freshness: 'STALE', age_s: 75 }), { glyph: '◐', tone: 'stale', text: 'Location getting old (1 min 15 s old)' })
  const lost = freshnessText({ freshness: 'LOST', age_s: 600 })
  assert.equal(lost.tone, 'lost')
  assert.match(lost.text, /^Location lost \(last fix 10 min ago\)/)
  assert.match(lost.text, /estimated position/)
})

test('offlineText names the last good update time in IST', () => {
  assert.equal(offlineText(Date.parse('2026-10-11T14:10:00Z')),
    'Offline – last update at 7:40 pm IST; instructions may be out of date')
  assert.equal(offlineText(null), 'Offline – no update received yet; instructions may be out of date')
})

// ───────────────────────────────────────────── location sharing
test('shouldPost throttles to at most one post per interval', () => {
  assert.equal(shouldPost(null, 1000, 15), true)
  assert.equal(shouldPost(1000, 15999, 15), false)
  assert.equal(shouldPost(1000, 16000, 15), true)
  assert.equal(shouldPost(1000, 5000, 0), false, 'a bad interval falls back to 15 s')
  assert.equal(shouldPost(1000, 16000, undefined), true)
})

test('positionPayload maps a GeolocationPosition, epoch ms for recorded_at', () => {
  const pos = { coords: { longitude: 72.8412, latitude: 19.0031, accuracy: 12.6 }, timestamp: 1791900000000 }
  assert.deepEqual(positionPayload(pos), { lon: 72.8412, lat: 19.0031, accuracy_m: 13, recorded_at: 1791900000000 })
  assert.deepEqual(positionPayload({ coords: { longitude: 1, latitude: 2, accuracy: null }, timestamp: 5 }),
    { lon: 1, lat: 2, accuracy_m: null, recorded_at: 5 })
  assert.equal(positionPayload({ coords: { longitude: NaN, latitude: 2 } }), null)
  assert.equal(positionPayload(null), null)
})

test('geoErrorMessage maps codes 1/2/3; geoSupport reports insecure / unsupported', () => {
  assert.match(geoErrorMessage({ code: 1 }), /permission/i)
  assert.match(geoErrorMessage({ code: 2 }), /not available/i)
  assert.match(geoErrorMessage({ code: 3 }), /too long/i)
  assert.match(geoErrorMessage({}), /could not/i)
  assert.equal(geoSupport({ isSecureContext: false, hasGeolocation: true }).ok, false)
  assert.match(geoSupport({ isSecureContext: false, hasGeolocation: true }).reason, /https/i)
  assert.equal(geoSupport({ isSecureContext: true, hasGeolocation: false }).ok, false)
  assert.deepEqual(geoSupport({ isSecureContext: true, hasGeolocation: true }), { ok: true, reason: null })
})

// ───────────────────────────────────────────── instructions and voice
test('nextInstruction: first maneuver of the first remaining leg, with its distance and the next step', () => {
  const n = nextInstruction(VIEW)
  assert.equal(n.instruction, 'Head north on Tilak Road')
  assert.equal(n.distance, '420 m')
  assert.equal(n.then, 'Turn left onto Dr Ambedkar Road')
  assert.equal(n.to, 'Settlement S')
  assert.equal(n.kind, 'to_settlement')
  assert.equal(nextInstruction({ ...VIEW, remaining: [] }), null)
  assert.equal(nextInstruction(null), null)
  const noMan = nextInstruction({ remaining: [{ to: 'X', kind: 'to_shelter', maneuvers: [] }] })
  assert.equal(noMan.instruction, 'Go to X')
  assert.equal(noMan.distance, null)
  assert.deepEqual(upcomingManeuvers(VIEW).map((m) => m.instruction),
    ['Turn left onto Dr Ambedkar Road', 'Arrive at Settlement S'])
})

test('isNewInstruction: speak only when the first instruction or its destination changes', () => {
  const a = nextInstruction(VIEW)
  assert.equal(isNewInstruction(null, a), true)
  assert.equal(isNewInstruction(a, { ...a }), false)
  assert.equal(isNewInstruction(a, { ...a, distance: '300 m' }), false, 'distance alone is not a new instruction')
  assert.equal(isNewInstruction(a, { ...a, instruction: 'Turn right onto X' }), true)
  assert.equal(isNewInstruction(a, null), false)
})

// ───────────────────────────────────────────── reports, hazards, route
test('report kinds and payloads: road required for blockage / high water, a client_ref always', () => {
  assert.deepEqual(REPORT_KINDS.map((k) => [k.kind, k.label, k.needsRoad]), [
    ['blockage', 'Road blocked', true], ['high_water', 'Water too deep', true], ['delay', 'Delay', false], ['other', 'Other', false],
  ])
  assert.deepEqual(upcomingRoads(VIEW), [
    { seg_id: 's1', label: 'Tilak Road' }, { seg_id: 's2', label: 'Dr Ambedkar Road' }, { seg_id: 's3', label: 'unnamed road (3rd ahead)' },
  ])
  assert.deepEqual(reportPayload({ kind: 'blockage', segId: '', note: '', clientRef: 'r1' }),
    { ok: false, error: 'Choose the road this is about.' })
  assert.deepEqual(reportPayload({ kind: 'blockage', segId: 's2', note: '  tree down  ', clientRef: 'r1' }),
    { ok: true, body: { kind: 'blockage', seg_id: 's2', note: 'tree down', client_ref: 'r1' } })
  assert.deepEqual(reportPayload({ kind: 'delay', segId: 's2', note: 'x'.repeat(400), clientRef: 'r2' }).body,
    { kind: 'delay', seg_id: 's2', note: 'x'.repeat(300), client_ref: 'r2' })
  assert.deepEqual(reportPayload({ kind: 'other', segId: '', note: '', clientRef: 'r3' }).body,
    { kind: 'other', seg_id: null, note: '', client_ref: 'r3' })
  assert.equal(reportPayload({ kind: 'nope', clientRef: 'r' }).ok, false)
})

test('hazardRows give each hazard a status word and glyph from the shared road-access table', () => {
  const [h] = hazardRows(VIEW)
  assert.deepEqual([h.name, h.label, h.glyph], ['unnamed road', 'At risk', '▲'])
  assert.match(h.detail, /40 cm/)
  assert.match(h.detail, /forecast depth reaches the vehicle limit/)
  assert.deepEqual(hazardRows({ hazards_ahead: [] }), [])
})

test('routeLines accepts LineString or Feature geometry per remaining leg', () => {
  assert.deepEqual(routeLines(VIEW), [[[72.84, 19.0], [72.841, 19.001]], [[72.841, 19.001], [72.843, 19.004]]])
  assert.deepEqual(routeLines({ remaining: [{ route: null }] }), [])
})

// ───────────────────────────────────────────── source pins
test('driver page: own header for the token, never in the address bar, no dashboard imports', () => {
  const src = read('driver/DriverApp.jsx')
  assert.match(src, /X-FloodNet-Token/)
  assert.match(src, /history\.replaceState/)
  assert.match(src, /sessionStorage/)
  assert.match(src, /watchPosition/)
  assert.match(src, /wakeLock/)
  assert.match(src, /visibilitychange/)
  assert.match(src, /never a guarantee of safety/)
  for (const re of [/FloodNetContext/, /from ['"]leaflet/, /from ['"]three/, /from ['"]\.\.\/App/]) assert.doesNotMatch(src, re)
  assert.doesNotMatch(src, /\bsafe route\b|\bsafest\b/i)
})
