// Self-contained runs: the browser renders a forecast from the simulate response alone. These tests build a tiny
// bundle exactly as backend/floodnet/api/bundle.py encodes it (base64(zlib(little-endian typed array))) and check
// that frames, series, the street inspector and route inputs come out of it with no further request.
import test from 'node:test'
import assert from 'node:assert/strict'
import { deflateSync } from 'node:zlib'
import { readFileSync } from 'node:fs'
import { buildFrame, buildSeries, decodeRun, depthRgba, explainSegment, frameDepths, frameIndex, wetSeries } from './runBundle.js'

const pk = (Typed, shape, values) => ({
  dtype: { Float32Array: 'f4', Uint8Array: 'u1', Uint16Array: 'u2' }[Typed.name], shape,
  data: deflateSync(Buffer.from(new Typed(values).buffer)).toString('base64'),
})

// 2 frames, 2 nodes, 1 edge, 2 street segments, a 2x2 depth grid
function response(runId, depthsM = [[0.0, 0.0], [0.07, 0.35]]) {
  const levels = [[0, 0, 0, 0], [0, 128, 255, 1]]
  const delta = [...levels[0], ...levels[1].map((v, i) => (v - levels[0][i] + 256) & 255)]
  return {
    run_id: runId, scenario_id: 'cloudburst', frames_t_min: [0, 5], provenance: { rainfall_source: { tag: 'SYNTHETIC' } }, data_mode: 'REAL',
    summary: { max_depth_cm: 35 },
    series: { t_min: [0, 5], rain_mm_h: [0, 100], flooded_segments: [0, 1] },
    hotspots: { max_depth_cm: 35 }, alert: { cap_xml: '<alert/>' }, alert_error: null, explain_provenance: { roads: 'OSM' },
    bundle: {
      version: 1, t_min: [0, 5], rain_mm_h: [0, 100], max_depth_cm: [0, 35],
      depth: { grid: { nx: 2, ny: 2 }, bbox_lonlat: [72.8, 19.0, 72.9, 19.1], scale_max_cm: 35, levels_delta: pk(Uint8Array, [2, 2, 2], delta) },
      nodes: {
        ids: ['N1', 'N2'], lon: [72.81, 72.82], lat: [19.01, 19.02], invert_m: [10, 10], ground_floor_m: [12, 12],
        hgl_m: pk(Float32Array, [2, 2], [10, 10, 12, 11]), surcharge_m3: pk(Float32Array, [2, 2], [0, 0, 3.5, 0]),
        surcharging_raw: pk(Uint8Array, [2, 2], [0, 0, 0, 0]), cause: pk(Uint8Array, [2, 2], [0, 0, 1, 0]),
        cause_codes: ['', 'overcapacity', 'downstream', 'blockage'],
      },
      edges: { ids: ['E1'], us: [0], ds: [1], capacity_m3s: [2], blockage: [0], util: pk(Float32Array, [2, 1], [0.1, 1.2]), flow_m3s: pk(Float32Array, [2, 1], [0.2, 2.4]) },
      streets: { seg_ids: ['S1', 'S2'], depth_m: pk(Float32Array, [2, 2], depthsM.flat()) },
      segments: { node_index: [0, 1], node_distance_m: [4.5, 9], ground_elevation_m: [30.5, null], terrain_note_inside: 'in', terrain_note_outside: 'out' },
      thresholds: { severity_bands_cm: [[5, 'clear'], [15, 'minor'], [30, 'moderate'], [60, 'severe']], vehicle_limit_cm: { car: 30, ambulance: 45 }, flood_threshold_cm: 5 },
    },
  }
}
const roads = { features: [
  { type: 'Feature', geometry: { type: 'LineString', coordinates: [[72.81, 19.01], [72.82, 19.01]] }, properties: { seg_id: 'S1', name: 'Tilak Road', highway: 'primary' } },
  { type: 'Feature', geometry: { type: 'LineString', coordinates: [[72.82, 19.01], [72.83, 19.02]] }, properties: { seg_id: 'S2', name: 'Gokhale Road', highway: 'secondary' } },
] }

test('a forecast renders from the simulate response alone: flood lines, timeline, depth image, drainage', async () => {
  const run = await decodeRun(response('run-A'))
  assert.deepEqual(run.frames_t_min, [0, 5])                                         // timeline
  const f = buildFrame(run, frameIndex(run, 5), roads)
  const props = Object.fromEntries(f.streets.features.map((x) => [x.properties.seg_id, x.properties]))
  assert.equal(props.S1.depth_cm, 7); assert.equal(props.S1.severity, 'minor')        // flood lines, coloured
  assert.equal(props.S2.depth_cm, 35); assert.equal(props.S2.severity, 'severe')
  assert.equal(props.S2.passable_car, false); assert.equal(props.S2.passable_ambulance, true)
  assert.deepEqual(f.streets.features[0].geometry, roads.features[0].geometry)
  const [n1, n2] = f.nodes
  assert.equal(n1.surcharging, true); assert.equal(n1.state, 'surcharging'); assert.equal(n1.cause, 'overcapacity')
  assert.equal(n2.fill_frac, 0.5); assert.equal(n2.freeboard_m, 1); assert.equal(n2.state, 'normal')
  assert.ok(Math.abs(f.edges[0].util - 1.2) < 1e-6)
  assert.equal(f.depth_grid.max_depth_cm, 35)
  const rgba = depthRgba(run.bundle.depth.levels, 1, 2, 2)     // image top row = grid row 1 (north): levels [255, 1]
  assert.deepEqual([...rgba.slice(0, 4)], [0, 40, 160, 255])                         // level 255: deepest colour
  assert.deepEqual([...rgba.slice(4, 8)], [120, 180, 255, 60])                       // level 1: shallowest visible
  assert.deepEqual([...rgba.slice(8, 12)], [0, 0, 0, 0])                             // grid row 0 (south), level 0
  assert.equal(run.hotspots.max_depth_cm, 35); assert.equal(run.alert.cap_xml, '<alert/>')
})

test('nothing is fetched after the forecast: a second "instance" is never needed', async () => {
  const calls = []
  globalThis.fetch = async (url) => { calls.push(url); throw new Error('no network') }
  const run = await decodeRun(response('run-A'))
  buildFrame(run, 1, roads); buildSeries(run); explainSegment(run, 'S2', 5, roads); frameDepths(run, 1); wetSeries(run)
  assert.deepEqual(calls, [])
  const ctx = readFileSync(new URL('../state/FloodNetContext.jsx', import.meta.url), 'utf8')
  const client = readFileSync(new URL('../api/client.js', import.meta.url), 'utf8')
  for (const gone of ['getFrame', 'getSeries', 'explainSegment(run.run_id', 'getRun', 'getFloodIntelligence', 'getAlert']) {
    assert.ok(!ctx.includes(`api.${gone}`), `FloodNetContext still calls api.${gone}`)
  }
  assert.ok(!/\/api\/simulation\/\$\{/.test(client), 'client still fetches a run by id')
  assert.ok(!/regenerat|alias/i.test(client), 'no retry / re-run layer may remain')
})

test('series, street inspector and route inputs come from the same run', async () => {
  const run = await decodeRun(response('run-A'))
  const s = buildSeries(run)
  assert.deepEqual(s.streets, { S1: [0, 7], S2: [0, 35] })
  assert.deepEqual(s.flooded_segments, [0, 1])
  const e = explainSegment(run, 'S2', null, roads)                                   // default: the peak frame
  assert.equal(e.t_min, 5); assert.equal(e.depth_cm, 35); assert.equal(e.seg_name, 'Gokhale Road')
  assert.equal(e.nearest_drainage_node.id, 'N2'); assert.ok(Math.abs(e.nearest_drainage_node.utilization - 1.2) < 1e-6)
  assert.equal(e.dominant_cause, 'surface_ponding_only'); assert.equal(e.terrain_context.ground_elevation_m, null)
  assert.deepEqual(e.timing, { peak_depth_cm: 35, peak_t_min: 5, onset_t_min: 5, threshold_cm: 5 })
  assert.equal(explainSegment(run, 'S1', 5, roads).dominant_cause, 'drainage_overcapacity')
  assert.deepEqual(Object.keys(frameDepths(run, 1)).sort(), ['S1', 'S2'])
  assert.deepEqual(frameDepths(run, 0), {})                                          // dry frame: nothing sent
  assert.deepEqual(wetSeries(run).streets, { S1: [0, 7], S2: [0, 35] })
})

test('a new scenario replaces the previous result completely', async () => {
  const a = await decodeRun(response('run-A'))
  const b = await decodeRun(response('run-B', [[0.0, 0.0], [0.5, 0.0]]))
  const fb = buildFrame(b, 1, roads)
  assert.equal(fb.run_id, 'run-B')
  assert.equal(fb.streets.features.find((x) => x.properties.seg_id === 'S2').properties.depth_cm, 0)
  assert.equal(buildFrame(a, 1, roads).streets.features.find((x) => x.properties.seg_id === 'S2').properties.depth_cm, 35)
})

test('an incomplete response is an error, never a "complete" run without data', async () => {
  await assert.rejects(decodeRun({ run_id: 'x', summary: {} }), /incomplete simulation result/)
  const bad = response('run-A'); bad.bundle.version = 99
  await assert.rejects(decodeRun(bad), /incomplete simulation result/)
})

test('frames wait for road geometry instead of drawing an empty map', async () => {
  const run = await decodeRun(response('run-A'))
  assert.equal(buildFrame(run, 1, null).streets.features.length, 0)
  const ctx = readFileSync(new URL('../state/FloodNetContext.jsx', import.meta.url), 'utf8')
  assert.match(ctx, /if \(roads\) framesRef\.current\.set/)                          // not cached until roads exist
  assert.match(ctx, /\}, \[run, currentT, roads\]\)/)                                 // rebuilt when roads arrive
})
