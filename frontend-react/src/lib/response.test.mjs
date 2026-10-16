import test from 'node:test'
import assert from 'node:assert/strict'
import { dispatchLog, makeResponseEvent, setAssignmentStatus } from './response.js'

const ledger = [{ assignment_id: 'A1', status: 'PROPOSED', history: [{ status: 'PROPOSED', t_min: 0, by: 'planner' }] }]

test('status changes append to history and refuse illegal moves', () => {
  const l1 = setAssignmentStatus(ledger, 'A1', 'ACCEPTED', 3)
  assert.equal(l1[0].status, 'ACCEPTED')
  assert.equal(l1[0].history.length, 2)
  assert.equal(ledger[0].history.length, 1)                       // original untouched
  assert.throws(() => setAssignmentStatus(l1, 'A1', 'COMPLETED', 4), /cannot go from ACCEPTED to COMPLETED/)
})

test('events and dispatch log', () => {
  const ev = [makeResponseEvent([], { kind: 'vehicle_unavailable', vehicle_id: 'V2', t: 30, reason: 'breakdown' })]
  assert.equal(ev[0].event_id, 'E1')
  assert.equal(ev[0].applies_from_t_min, 30)
  const rows = dispatchLog(setAssignmentStatus(ledger, 'A1', 'ACCEPTED', 3), [{ t: 1, lines: ['A1 proposed'] }])
  assert.deepEqual(rows.map((r) => r.t), [0, 1, 3])
  assert.equal(rows[1].by, 'planner')
})
