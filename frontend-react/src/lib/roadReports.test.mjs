// Run with:  npm run test:unit
//
// The map and the Alerts & reports page show which closures are in force BEFORE any backend request, so
// lib/roadAccess.js mirrors backend/floodnet/access/events.py (DECISIONS D-32). These are the same cases as
// backend/tests/test_report_reconciliation.py (Agent C T1-T9): if the two disagree, the map and the router disagree.
// Written test-first: before the mirror exists every case below fails (annotateRoadEvents is not exported, and
// activeClosures still implements "latest applies_from wins").
import test from 'node:test'
import assert from 'node:assert/strict'
import * as ra from './roadAccess.js'

const ev = (event_id, kind, r, a = r, source_tier) => ({
  event_id, seg_id: 'n1', kind, received_t_min: r, applies_from_t_min: a, source: 'operator report (SIMULATED)',
  ...(source_tier ? { source_tier } : {}),
})
const closedAt = (events, t, decision = t) => ra.activeClosures(events, decision, t).get('n1')?.event_id ?? null
const byId = (events, decision) => Object.fromEntries(ra.annotateRoadEvents(events, decision).map((e) => [e.event_id, e]))

test('T1: a reopen without references lifts an earlier closure from its own time', () => {
  const E = [ev('C1', 'closure', 10), ev('R2', 'reopen', 60)]
  assert.equal(closedAt(E, 59, 61), 'C1')
  assert.equal(closedAt(E, 61), null)
  const a = byId(E, 61)
  assert.equal(a.C1.status, 'LIFTED')
  assert.deepEqual(a.R2.lifts, ['C1'])
  assert.equal(a.R2.status, 'APPLIED')
})

test('T2: a reopen recorded before the closure has no effect, and says why', () => {
  const E = [ev('C1', 'closure', 10), ev('R2', 'reopen', 8)]
  assert.equal(closedAt(E, 30), 'C1')
  const a = byId(E, 30)
  assert.equal(a.R2.status, 'NO_EFFECT')
  assert.match(a.R2.explanation, /recorded before closure C1/)
})

test('T3: a late, back-dated closure is not cancelled by an unrelated earlier reopen', () => {
  const E = [ev('C0', 'closure', 0), ev('R1', 'reopen', 20), ev('C1', 'closure', 30, 5)]
  assert.equal(closedAt(E, 35), 'C1')
  const a = byId(E, 35)
  assert.deepEqual(a.R1.lifts, ['C0'])
})

test('T4: a full tie goes to the closure, whatever the array order', () => {
  for (const E of [[ev('C1', 'closure', 10), ev('R2', 'reopen', 10)], [ev('R2', 'reopen', 10), ev('C1', 'closure', 10)]]) {
    assert.equal(closedAt(E, 30), 'C1')
    assert.equal(byId(E, 30).R2.status, 'NO_EFFECT')
  }
})

test('T5: a PUBLIC reopen cannot lift an OPERATOR closure; it is shown as contested', () => {
  const E = [ev('C1', 'closure', 10, 10, 'OPERATOR'), ev('R2', 'reopen', 12, 12, 'PUBLIC')]
  assert.equal(closedAt(E, 15), 'C1')
  const a = byId(E, 15)
  assert.equal(a.R2.status, 'REJECTED_LOWER_TIER')
  assert.deepEqual(a.C1.contested_by, ['R2'])
})

test('T6: a PUBLIC closure is unconfirmed: never in force, listed as UNCONFIRMED', () => {
  const E = [ev('C1', 'closure', 0, 0, 'PUBLIC')]
  assert.equal(closedAt(E, 10), null)
  assert.equal(byId(E, 10).C1.status, 'UNCONFIRMED')
  assert.deepEqual([...ra.unconfirmedClosures(E, 10).keys()], ['n1'])
})

test('T7: a scheduled reopen takes effect at its own time and is labelled scheduled', () => {
  const E = [ev('C1', 'closure', 10), ev('R2', 'reopen', 20, 40)]
  assert.equal(closedAt(E, 25), 'C1')
  assert.equal(closedAt(E, 45), null)
  assert.equal(byId(E, 25).R2.scheduled, true)
  assert.match(byId(E, 25).R2.explanation, /scheduled/)
})

test('T8: staleness is a flag only: an old FIELD_TEAM closure still holds and says how old it is', () => {
  const E = [ev('C1', 'closure', 0, 0, 'FIELD_TEAM')]
  assert.equal(closedAt(E, 100), 'C1')
  const a = byId(E, 100)
  assert.equal(a.C1.stale, true)
  assert.equal(a.C1.age_min, 100)
  assert.match(a.C1.explanation, /stale 100 min/)
})

test('T9: an AUTHORITY closure needs an AUTHORITY reopen', () => {
  const E = [ev('C1', 'closure', 0, 0, 'AUTHORITY'), ev('R2', 'reopen', 5, 5, 'FIELD_TEAM')]
  assert.equal(closedAt(E, 10), 'C1')
  assert.equal(byId(E, 10).R2.status, 'REJECTED_LOWER_TIER')
  const E2 = [...E, ev('R3', 'reopen', 6, 6, 'AUTHORITY')]
  assert.equal(closedAt(E2, 10), null)
  assert.equal(byId(E2, 10).R3.status, 'APPLIED')
})

test('no future information: reports received after the decision time are FUTURE and change nothing', () => {
  const E = [ev('C1', 'closure', 10), ev('R2', 'reopen', 40)]
  assert.equal(closedAt(E, 50, 30), 'C1')        // decision at T+30 does not know the T+40 reopen
  assert.equal(byId(E, 30).R2.status, 'FUTURE')
})

test('legacy events without a tier behave as OPERATOR and the old two-argument call still works', () => {
  const E = [ev('C1', 'closure', 0), ev('R2', 'reopen', 5)]
  assert.equal(ra.activeClosures(E, 3).get('n1')?.event_id, 'C1')
  assert.equal(ra.activeClosures(E, 6).size, 0)
  assert.deepEqual(ra.SOURCE_TIERS, ['AUTHORITY', 'OPERATOR', 'FIELD_TEAM', 'PUBLIC'])
})

test('makeRoadEvent records the tier (default OPERATOR) and keeps the existing fields', () => {
  const e = ra.makeRoadEvent([], { segId: '12', name: 'Tilak Road', kind: 'closure', t: 5 })
  assert.equal(e.source_tier, 'OPERATOR')
  assert.equal(e.event_id, 'C1')
  const p = ra.makeRoadEvent([e], { segId: '12', name: 'Tilak Road', kind: 'reopen', t: 9, tier: 'PUBLIC' })
  assert.equal(p.source_tier, 'PUBLIC')
  assert.match(p.source, /public report/i)
})

// ---------------------------------------------------------------- parity with the backend (QA audit M1, L6, L7)
// reconciliation_parity.json is generated from backend/floodnet/access/events.py by
// backend/scripts/reconciliation_parity.py and pinned to the backend by backend/tests/test_reconciliation_parity.py.
// The browser mirror must give the same answer on every case.
import { readFileSync as _read } from 'node:fs'
const PARITY = JSON.parse(_read(new URL('./reconciliation_parity.json', import.meta.url), 'utf8'))

for (const [name, c] of Object.entries(PARITY.cases)) {
  test(`parity with the backend: ${name}`, () => {
    const t = c.decision_t_min
    const ann = Object.fromEntries(ra.annotateRoadEvents(c.events, t).map((e) => [e.event_id, e]))
    const pick = (f) => Object.fromEntries(Object.keys(c.expected.status).sort().map((k) => [k, f(ann[k])]))
    assert.equal(ra.activeClosures(c.events, t).get('x')?.event_id ?? null, c.expected.closed_by, 'closed_by')
    assert.equal(ra.unconfirmedClosures(c.events, t).get('x')?.event_id ?? null, c.expected.unconfirmed, 'unconfirmed')
    assert.deepEqual(pick((e) => e.status), c.expected.status, 'status')
    assert.deepEqual(pick((e) => e.lifts), c.expected.lifts, 'lifts')
    assert.deepEqual(pick((e) => e.contested_by), c.expected.contested_by, 'contested_by')
    assert.deepEqual(pick((e) => Boolean(e.stale)), c.expected.stale, 'stale')
  })
}

// ---------------------------------------------------------------- vehicle restrictions (D-42)
test('makeRoadEvent records a restriction for some vehicle types; a reopen never carries one', () => {
  const c = ra.makeRoadEvent([], { segId: '12', name: 'Bridge', kind: 'closure', t: 5, vehicleTypes: ['truck'] })
  assert.deepEqual(c.vehicle_types, ['truck'])
  const plain = ra.makeRoadEvent([], { segId: '12', name: 'Bridge', kind: 'closure', t: 5 })
  assert.equal('vehicle_types' in plain, false, 'an ordinary closure stays exactly as before')
  const r = ra.makeRoadEvent([c], { segId: '12', name: 'Bridge', kind: 'reopen', t: 9, vehicleTypes: ['truck'] })
  assert.equal('vehicle_types' in r, false)
})

test('a restriction is shown on the map like a closure (no vehicle chosen) and says which vehicles it binds', () => {
  const E = [{ event_id: 'C1', seg_id: 'n1', kind: 'closure', received_t_min: 0, applies_from_t_min: 0, vehicle_types: ['truck'] }]
  assert.equal(ra.activeClosures(E, 5).get('n1')?.event_id, 'C1')
  assert.match(ra.annotateRoadEvents(E, 5)[0].explanation, /only truck/)
})
