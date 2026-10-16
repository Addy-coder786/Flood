import test from 'node:test'
import assert from 'node:assert/strict'
import { activeClosures, makeRoadEvent, nearestSegment } from './roadAccess.js'

const roads = {
  features: [
    { properties: { seg_id: 'a', name: 'A Road' }, geometry: { coordinates: [[72.84, 19.01], [72.841, 19.01]] } },
    { properties: { seg_id: 'b', name: 'B Road' }, geometry: { coordinates: [[72.84, 19.0105], [72.841, 19.0105]] } },
  ],
}

test('nearestSegment picks the closest road and ignores far clicks', () => {
  assert.equal(nearestSegment(roads, [72.8405, 19.0101]).segId, 'a')
  assert.equal(nearestSegment(roads, [72.8405, 19.0104]).segId, 'b')
  assert.equal(nearestSegment(roads, [72.85, 19.02]), null)
})

test('activeClosures: closure, reopen, and events not yet received', () => {
  const ev = []
  ev.push(makeRoadEvent(ev, { segId: 'a', name: 'A', kind: 'closure', t: 10 }))
  ev.push(makeRoadEvent(ev, { segId: 'a', name: 'A', kind: 'reopen', t: 30 }))
  ev.push(makeRoadEvent(ev, { segId: 'b', name: 'B', kind: 'closure', t: 50 }))
  assert.deepEqual([...activeClosures(ev, 5).keys()], [])
  assert.deepEqual([...activeClosures(ev, 20).keys()], ['a'])
  assert.deepEqual([...activeClosures(ev, 40).keys()], [])
  assert.deepEqual([...activeClosures(ev, 60).keys()], ['b'])
  assert.equal(ev[0].event_id, 'C1')
  assert.equal(ev[1].event_id, 'R2')
})
