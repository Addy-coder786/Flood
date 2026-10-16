// Run with:  npm run test:unit
//
// Resource registry page helpers (2026-10-11): freshness and source chips, occupancy, client-side validation that
// mirrors the server (backend/floodnet/ops/registry.py), CSV parsing for the import preview, commitments from trips
// and the ledger, responder links, and the candidate-shelter steps. Written test-first: before lib/registry.js
// exists every test fails.
import test from 'node:test'
import assert from 'node:assert/strict'

const load = () => import('./registry.js')

const VOCAB = {
  capabilities: ['evacuation', 'water rescue'],
  vehicle_types: [{ type: 'motorcycle', depth_limit_cm: 30 }, { type: 'car', depth_limit_cm: 30 }, { type: 'truck', depth_limit_cm: 60 }],
  pilot_bbox: [72.835, 19.010, 72.855, 19.030],
  shelter_statuses: ['open', 'closed'],
}

test('freshness chip: UNKNOWN for never-confirmed seed values, STALE past the threshold, CURRENT otherwise', async () => {
  const { freshnessChip } = await load()
  assert.deepEqual(freshnessChip({ freshness: 'UNKNOWN', age_min: null }).text, 'UNKNOWN')
  assert.equal(freshnessChip({ freshness: 'UNKNOWN' }).tone, 'muted')
  const stale = freshnessChip({ freshness: 'STALE', age_min: 75 })
  assert.equal(stale.text, 'STALE')
  assert.equal(stale.tone, 'warn')
  assert.match(stale.title, /1 h 15 min/)
  assert.equal(freshnessChip({ freshness: 'CURRENT', age_min: 3 }).tone, 'ok')
  assert.equal(freshnessChip({}).text, 'UNKNOWN', 'a record without freshness is never shown as current')
})

test('age text is honest about missing times', async () => {
  const { ageText } = await load()
  assert.equal(ageText(null), 'never confirmed')
  assert.equal(ageText(0.4), 'just now')
  assert.equal(ageText(12.2), '12 min ago')
  assert.equal(ageText(185), '3 h 5 min ago')
})

test('source chip separates SIMULATED seed, dispatcher entry, import and volunteer-verified records', async () => {
  const { sourceChip } = await load()
  assert.deepEqual([sourceChip({ source: 'SIMULATED seed (fixtures)', verified: false }).text,
    sourceChip({ source: 'SIMULATED seed (fixtures)', verified: false }).tone], ['SIMULATED', 'sim'])
  assert.equal(sourceChip({ registered_source: 'dispatcher entry', source: 'dispatcher entry' }).text, 'DISPATCHER ENTRY')
  assert.equal(sourceChip({ registered_source: 'import: ward.csv', source: 'update RU3 by dispatcher' }).text, 'IMPORTED')
  assert.equal(sourceChip({ registered_source: 'volunteer-submitted, verified by dispatcher (candidate CS1)' }).text, 'VOLUNTEER · VERIFIED')
  // a seed record that has since been updated by a person is no longer only a simulation
  assert.equal(sourceChip({ source: 'update RU1 by responder:RP1', verified: true }).text, 'UPDATED')
})

test('occupancy: free places, percentage, and a tone that does not rely on colour alone', async () => {
  const { occupancy } = await load()
  assert.deepEqual(occupancy({ occupancy: 20, total_capacity: 80 }), { used: 20, total: 80, free: 60, pct: 25, level: 'ok', text: '20 / 80 occupied · 60 free' })
  assert.equal(occupancy({ occupancy: 70, total_capacity: 80 }).level, 'warn')
  const full = occupancy({ occupancy: 90, total_capacity: 80 })
  assert.equal(full.level, 'full')
  assert.equal(full.pct, 100)
  assert.equal(full.free, 0)
  assert.match(full.text, /over capacity/)
  assert.equal(occupancy({}).text, 'capacity not known')
})

test('validateRecord mirrors the server rules for teams, vehicles and shelters', async () => {
  const { validateRecord } = await load()
  const team = validateRecord('team', { name: ' Ward team ', capabilities: ['evacuation'], members: '5', base: 'Ward office', lon: '72.846', lat: '19.014' }, VOCAB)
  assert.deepEqual(team.errors, {})
  assert.deepEqual(team.record, { name: 'Ward team', capabilities: ['evacuation'], members: 5, base: 'Ward office', lonlat: [72.846, 19.014] })
  const bad = validateRecord('team', { name: '', capabilities: [], members: '0', base: '', lon: '73.1', lat: '19.0' }, VOCAB)
  assert.deepEqual(Object.keys(bad.errors).sort(), ['base', 'capabilities', 'location', 'members', 'name'])
  assert.match(bad.errors.location, /pilot area/)
  const veh = validateRecord('vehicle', { name: 'Boat', type: 'boat', passenger_capacity: '6', base: 'Jetty', lon: '72.846', lat: '19.014' }, VOCAB)
  assert.match(veh.errors.type, /truck/)
  const ok = validateRecord('vehicle', { name: 'Truck 3', type: 'truck', passenger_capacity: '20', base: 'Depot', lon: '72.846', lat: '19.014', organisation: 'Ward' }, VOCAB)
  assert.deepEqual(ok.record, { name: 'Truck 3', type: 'truck', passenger_capacity: 20, base: 'Depot', lonlat: [72.846, 19.014], organisation: 'Ward' })
  const sh = validateRecord('shelter', { name: 'Hall', total_capacity: '50', occupancy: '60', status: 'open', lon: '72.846', lat: '19.014' }, VOCAB)
  assert.match(sh.errors.occupancy, /capacity/)
  assert.deepEqual(validateRecord('shelter', { name: 'Hall', total_capacity: '50', lon: '72.846', lat: '19.014' }, VOCAB).record,
    { name: 'Hall', total_capacity: 50, occupancy: 0, status: 'open', lonlat: [72.846, 19.014] })
})

test('validateCandidate refuses phone numbers and places outside the pilot area', async () => {
  const { validateCandidate } = await load()
  const ok = validateCandidate({ name: 'Temple hall', lon: '72.846', lat: '19.014', description: 'large hall', capacity_estimate: '40' }, VOCAB.pilot_bbox)
  assert.deepEqual(ok.errors, {})
  assert.deepEqual(ok.body, { name: 'Temple hall', lonlat: [72.846, 19.014], address: '', description: 'large hall', capacity_estimate: 40 })
  const bad = validateCandidate({ name: 'Hall', lon: '72.846', lat: '19.014', description: 'call 98200 12345' }, VOCAB.pilot_bbox)
  assert.match(bad.errors.description, /phone/)
  assert.match(validateCandidate({ name: 'x', lon: '', lat: '' }, VOCAB.pilot_bbox).errors.location, /lon/)
  assert.ok(!('description' in validateCandidate({ name: 'x', lon: '72.846', lat: '19.014', description: 'checked 2026-10-11' }, VOCAB.pilot_bbox).errors),
    'a date is not a phone number')
})

test('parseCsv handles quotes, commas inside quotes, CRLF and blank lines', async () => {
  const { parseCsv } = await load()
  const rows = parseCsv('kind,name,capabilities\r\nteam,"Team, north","evacuation;water rescue"\r\n\r\nshelter,"Hall ""B""",\r\n')
  assert.deepEqual(rows, {
    header: ['kind', 'name', 'capabilities'],
    rows: [{ kind: 'team', name: 'Team, north', capabilities: 'evacuation;water rescue' }, { kind: 'shelter', name: 'Hall "B"', capabilities: '' }],
  })
  assert.deepEqual(parseCsv('').rows, [])
})

test('detectFormat picks JSON or CSV from the file name, then from the text', async () => {
  const { detectFormat } = await load()
  assert.equal(detectFormat('list.csv', '[]'), 'csv')
  assert.equal(detectFormat('list.JSON', 'kind,name'), 'json')
  assert.equal(detectFormat('', '  [{"kind":"team"}]'), 'json')
  assert.equal(detectFormat('', 'kind,name\nteam,x'), 'csv')
})

test('commitments: active trips and live ledger assignments per team, vehicle and shelter', async () => {
  const { commitments } = await load()
  const trips = [
    { trip_id: 'TR1', stage: 'EN_ROUTE', team_id: 'T1', vehicle_id: 'V1', shelter_id: 'SH2', assignment_id: 'A1' },
    { trip_id: 'TR2', stage: 'COMPLETED', team_id: 'T2', vehicle_id: 'V2', shelter_id: 'SH1', assignment_id: 'A2' },
  ]
  const ledger = [
    { assignment_id: 'A1', status: 'DISPATCHED', team_id: 'T1', vehicle_id: 'V1', shelter_id: 'SH2' },
    { assignment_id: 'A3', status: 'ACCEPTED', team_id: 'T2', vehicle_id: 'V2', shelter_id: 'SH1' },
    { assignment_id: 'A4', status: 'PROPOSED', team_id: 'T2', vehicle_id: 'V2', shelter_id: 'SH1' },
    { assignment_id: 'A5', status: 'CANCELLED', team_id: 'T1', vehicle_id: 'V1', shelter_id: 'SH1' },
  ]
  const c = commitments(trips, ledger)
  assert.deepEqual(c.V1, [{ kind: 'trip', id: 'TR1', status: 'EN_ROUTE', assignment_id: 'A1' }], 'A1 already shown through its trip')
  assert.deepEqual(c.V2, [{ kind: 'assignment', id: 'A3', status: 'ACCEPTED', assignment_id: 'A3' }], 'finished trips and proposals are not commitments')
  assert.deepEqual(c.SH2.map((x) => x.id), ['TR1'])
  assert.deepEqual(commitments(null, undefined), {})
})

test('responder links carry the token in the fragment and are read back once', async () => {
  const { responderUrl, tokenFromHash } = await load()
  assert.equal(responderUrl('https://x.example/', '/app', 'abc_-1'), 'https://x.example/app/#/resources?rt=abc_-1')
  assert.equal(tokenFromHash('#/resources?rt=abc_-1'), 'abc_-1')
  assert.equal(tokenFromHash('#/resources'), null)
  assert.equal(tokenFromHash('#/trips?rt=x'), null, 'only the resources page accepts a responder token')
})

test('candidate steps show where a candidate is and what comes next', async () => {
  const { candidateSteps, nextCandidateAction } = await load()
  const s = candidateSteps({ state: 'PERMISSION_CONFIRMED' })
  assert.deepEqual(s.map((x) => [x.state, x.status]), [['SUBMITTED', 'done'], ['PERMISSION_CONFIRMED', 'done'],
    ['SUITABILITY_CHECKED', 'current'], ['CONFIRMED', 'todo']])
  assert.equal(nextCandidateAction({ state: 'SUBMITTED' }), 'permission')
  assert.equal(nextCandidateAction({ state: 'PERMISSION_CONFIRMED' }), 'suitability')
  assert.equal(nextCandidateAction({ state: 'SUITABILITY_CHECKED' }), 'confirm')
  assert.equal(nextCandidateAction({ state: 'CONFIRMED' }), null)
  const rej = candidateSteps({ state: 'REJECTED', history: [{ state: 'SUBMITTED' }, { state: 'REJECTED' }] })
  assert.equal(rej.at(-1).state, 'REJECTED')
  assert.equal(rej.at(-1).status, 'rejected')
})

test('site depth text never turns UNKNOWN into a number', async () => {
  const { depthText } = await load()
  assert.match(depthText({ status: 'UNKNOWN', reason: 'no forecast has been published to live operations' }), /^UNKNOWN: no forecast/)
  assert.equal(depthText({ status: 'MODELLED', max_depth_cm: 20, peak_t_min: 30, road_name: 'South Rd', distance_m: 22.1 }),
    'MODELLED 20 cm at T+30 on South Rd (22 m away)')
  assert.equal(depthText({ status: 'MODELLED', max_depth_cm: 0, peak_t_min: null, road_name: 'South Rd', distance_m: 22.1 }),
    'MODELLED 0 cm (no water on South Rd in the published forecast, 22 m away)')
  assert.equal(depthText(null), 'UNKNOWN: not checked yet')
})

test('local date-time input helpers round-trip to UTC ISO', async () => {
  const { toLocalInput, fromLocalInput } = await load()
  const d = new Date('2026-10-11T04:30:00Z')
  const local = toLocalInput(d)
  assert.match(local, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
  assert.equal(fromLocalInput(local), '2026-10-11T04:30:00.000Z')
  assert.equal(fromLocalInput(''), null)
  assert.equal(fromLocalInput('nonsense'), null)
})
