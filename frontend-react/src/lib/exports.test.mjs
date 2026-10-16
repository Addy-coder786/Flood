import test from 'node:test'
import assert from 'node:assert/strict'
import { ledgerForExport, toCsv } from './exports.js'

test('toCsv quotes commas and quotes, keeps column order', () => {
  const csv = toCsv([{ t: 1, text: 'A1 → ACCEPTED (ok, fine)', by: 'operator' }, { t: 2, text: 'say "hi"', by: 'planner' }], ['t', 'by', 'text'])
  assert.equal(csv.split('\n')[0], 't,by,text')
  assert.equal(csv.split('\n')[1], '1,operator,"A1 → ACCEPTED (ok, fine)"')
  assert.equal(csv.split('\n')[2], '2,planner,"say ""hi"""')
})

test('ledgerForExport drops geometry but keeps road names', () => {
  const out = ledgerForExport([{ assignment_id: 'A1', legs: [{ kind: 'to_shelter', route: { type: 'LineString' }, segments: [{ seg_id: 'x', name: 'Tilak Road' }] }] }])
  assert.equal(out[0].legs[0].route, undefined)
  assert.deepEqual(out[0].legs[0].roads, ['Tilak Road'])
})
