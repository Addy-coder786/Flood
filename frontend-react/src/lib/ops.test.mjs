// Run with:  npm run test:unit
//
// The dispatcher browser and the shared incident store (DECISIONS D-38, D-41): field reports merged into the road
// log, trip stages applied to the ledger through one mapping, and driver links built from the app's own base path.
// Written test-first: before lib/ops.js exists every test fails.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const load = () => import('./ops.js')

const A = (id, status, extra = {}) => ({ assignment_id: id, status, history: [{ status, t_min: 0, by: 'planner' }], legs: [{ kind: 'to_settlement' }], ...extra })
const trip = (aid, stage, extra = {}) => ({ trip_id: 'TR1', assignment_id: aid, stage, route_version: 1, legs: [{ kind: 'x' }], ...extra })

test('field reports from drivers are merged once; dispatcher events from the server are not duplicated', async () => {
  const { mergeFieldEvents } = await load()
  const local = [{ event_id: 'C1', seg_id: 's', kind: 'closure', received_t_min: 1 }]
  const server = [{ event_id: 'C1', seg_id: 's', kind: 'closure', received_t_min: 1, origin: 'dispatcher' },
    { event_id: 'F1', seg_id: 't', kind: 'closure', received_t_min: 3, origin: 'driver:TR1', source_tier: 'FIELD_TEAM' }]
  const merged = mergeFieldEvents(local, server)
  assert.deepEqual(merged.map((e) => e.event_id), ['C1', 'F1'])
  assert.equal(mergeFieldEvents(merged, server), merged, 'nothing new -> the same array (no re-render)')
})

test('dispatcherEvents: only events made in this browser are synced up', async () => {
  const { dispatcherEvents } = await load()
  const ev = [{ event_id: 'C1' }, { event_id: 'F1', origin: 'driver:TR1' }, { event_id: 'C2', origin: 'dispatcher' }]
  assert.deepEqual(dispatcherEvents(ev).map((e) => e.event_id), ['C1', 'C2'])
})

test('trip stages move the ledger forward through the D-41 mapping, with the trip named in the history', async () => {
  const { applyTripsToLedger, LEDGER_FOR_STAGE } = await load()
  assert.equal(LEDGER_FOR_STAGE.LOADED, 'IN_PROGRESS')
  const { ledger, changes } = applyTripsToLedger([A('A1', 'ACCEPTED')], [trip('A1', 'LOADED')], 12)
  assert.equal(ledger[0].status, 'IN_PROGRESS')
  assert.deepEqual(ledger[0].history.slice(-1)[0], { status: 'IN_PROGRESS', t_min: 12, reason: 'trip TR1: LOADED', by: 'trip' })
  assert.deepEqual(ledger[0].history.map((h) => h.status), ['ACCEPTED', 'DISPATCHED', 'IN_PROGRESS'],
    'no step is skipped: the backend refuses ACCEPTED -> IN_PROGRESS')
  assert.equal(changes.length, 1)
})

test('the ledger never moves backwards and finished assignments are left alone', async () => {
  const { applyTripsToLedger } = await load()
  const l = [A('A1', 'IN_PROGRESS'), A('A2', 'COMPLETED')]
  const out = applyTripsToLedger(l, [trip('A1', 'EN_ROUTE'), { ...trip('A2', 'ABORTED'), trip_id: 'TR2' }], 5)
  assert.equal(out.ledger, l, 'no change -> same array')
  assert.equal(out.changes.length, 0)
})

test('an aborted trip cancels its assignment with the reason', async () => {
  const { applyTripsToLedger } = await load()
  const t = trip('A1', 'ABORTED', { stage_history: [{ stage: 'ABORTED', reason: 'bridge gone' }] })
  const { ledger } = applyTripsToLedger([A('A1', 'DISPATCHED')], [t], 7)
  assert.equal(ledger[0].status, 'CANCELLED')
  assert.match(ledger[0].history.slice(-1)[0].reason, /bridge gone/)
})

test('an accepted reroute replaces the legs in the ledger once', async () => {
  const { applyTripsToLedger } = await load()
  const t = trip('A1', 'EN_ROUTE', { route_version: 2, legs: [{ kind: 'to_settlement', new: true }] })
  const first = applyTripsToLedger([A('A1', 'DISPATCHED')], [t], 4)
  assert.equal(first.ledger[0].legs[0].new, true)
  assert.equal(first.ledger[0].trip_route_version, 2)
  assert.equal(applyTripsToLedger(first.ledger, [t], 5).ledger, first.ledger)
})

test('driverUrl builds the link from the app base path and keeps the token in the fragment', async () => {
  const { driverUrl } = await load()
  assert.equal(driverUrl('http://127.0.0.1:5173', '/static/', 'driver#t=abc'), 'http://127.0.0.1:5173/static/driver#t=abc')
  assert.equal(driverUrl('https://example.test', '/', 'driver#t=abc'), 'https://example.test/driver#t=abc')
})

test('tripSummary says what needs a person, in words', async () => {
  const { tripSummary } = await load()
  assert.match(tripSummary({ stage: 'EN_ROUTE', proposals: [{ status: 'OPEN', kind: 'REQUIRED' }] }).attention, /route change needs a decision/)
  assert.match(tripSummary({ stage: 'EN_ROUTE', proposals: [], escalation: { open: true, reason: 'no feasible route' } }).attention, /no feasible route/)
  assert.equal(tripSummary({ stage: 'EN_ROUTE', proposals: [] }).attention, null)
  assert.match(tripSummary({ stage: 'EN_ROUTE', proposals: [], position: { freshness: 'LOST', age_s: 300 } }).gps, /lost/i)
})

test('trips from another incident never touch this incident\'s ledger (assignment ids repeat across incidents)', async () => {
  const { applyTripsToLedger } = await load()
  const mine = { ...trip('A1', 'LOADED'), incident_id: 'INC-2' }
  const other = { ...trip('A1', 'COMPLETED'), trip_id: 'TR9', incident_id: 'INC-1' }
  const { ledger } = applyTripsToLedger([A('A1', 'ACCEPTED')], [other, mine], 3, 'INC-2')
  assert.equal(ledger[0].status, 'IN_PROGRESS')
  const none = applyTripsToLedger([A('A1', 'ACCEPTED')], [other], 3, 'INC-2')
  assert.equal(none.changes.length, 0)
})

test('the forecast shared with live operations names its rainfall source, not only the pilot data mode', async () => {
  const { forecastInputLabel } = await load()
  const run = { scenario_id: 'heavy', scenario_name: 'Heavy design storm', data_mode: 'REAL', provenance: { rainfall_source: { source_type: 'scenario' } } }
  const label = forecastInputLabel(run)
  assert.match(label, /SYNTHETIC/i, label)
  assert.match(label, /REAL pilot/i)
  assert.doesNotMatch(label, /^REAL$/)
})

test('the public page always revalidates (a resolved alert is never shown from the browser cache)', () => {
  const src = readFileSync(new URL('../public/PublicAlertApp.jsx', import.meta.url), 'utf8')
  assert.match(src, /cache:\s*'no-cache'/)
})

test('field reports from another incident are not merged into this one', async () => {
  const { mergeFieldEvents } = await load()
  const server = [{ event_id: 'F1', seg_id: 't', kind: 'closure', received_t_min: 3, origin: 'driver:TR1', incident_id: 'INC-old' },
    { event_id: 'F2', seg_id: 'u', kind: 'closure', received_t_min: 4, origin: 'driver:TR2', incident_id: 'INC-new' }]
  assert.deepEqual(mergeFieldEvents([], server, 'INC-new').map((e) => e.event_id), ['F2'])
})

test('a direct trip (no assignment) never changes the planner ledger', async () => {
  const { applyTripsToLedger } = await load()
  const ledger = [{ assignment_id: 'A1', status: 'ACCEPTED', history: [] }]
  const trips = [{ trip_id: 'TR9', kind: 'direct', assignment_id: null, stage: 'COMPLETED', incident_id: 'INC-a' }]
  const out = applyTripsToLedger(ledger, trips, 5, 'INC-a')
  assert.equal(out.ledger, ledger)
  assert.deepEqual(out.changes, [])
})
