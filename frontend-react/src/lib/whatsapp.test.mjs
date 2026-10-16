import test from 'node:test'
import assert from 'node:assert/strict'
import { forecastLines, responseLines, testLines } from './whatsapp.js'

const short = (lines) => lines.every((l) => l.length <= 240) && lines.length <= 15

test('forecast lines name the worst streets and stay within limits', () => {
  const lines = forecastLines({
    scenarioName: 'Cloudburst', currentT: 45,
    hotspots: [{ name: 'Hindmata Junction', peakDepthCm: 52.4, peakTMin: 60, category: 'SEVERE' }, { name: 'x'.repeat(400), peakDepthCm: 31, peakTMin: 70, category: 'SEVERE' }],
    alerts: [{ title: 'FLOODNET ALERT', subject: 'Dr Ambedkar Rd', etaMin: 12, state: 'UPCOMING' }],
  })
  assert.ok(lines.some((l) => l.includes('Hindmata Junction: 52 cm at T+60 min')))
  assert.ok(lines.some((l) => l.includes('in 12 min')))
  assert.ok(short(lines))
})

test('response lines carry unmet reasons and attention flags', () => {
  const result = {
    plan_version: 3, sim_t_min: 30,
    summary: { people_needing_help: 215, served: 45, covered_committed: 0, covered_proposed: 150, unmet: 20 },
    unmet: { S4: { people: 5, reasons: [{ reason: 'shelter capacity exhausted' }] } },
    assignments: [{ assignment_id: 'A2', attention: 'destination SH1 has no room' }],
    changes: [{ assignment_id: 'A3', change: 'proposed', reason: 'x' }, { assignment_id: 'A1', change: 're-routed', reason: 'closure' }],
  }
  const lines = responseLines({ result, names: { settlement: { S4: 'Dadar station' } } })
  assert.match(lines[0], /215 need help, 45 served, 150 covered, 20 unmet/)
  assert.ok(lines.some((l) => l.includes('Unmet at Dadar station: 5 people (shelter capacity exhausted)')))
  assert.ok(lines.some((l) => l.includes('A2 needs a decision')))
  assert.ok(lines.some((l) => l.includes('A1 re-routed')) && !lines.some((l) => l.includes('A3 proposed')))
  assert.ok(short(lines))
  assert.deepEqual(responseLines({ result: null }), ['No response plan has been built yet.'])
  assert.equal(testLines({}).length, 2)
})
