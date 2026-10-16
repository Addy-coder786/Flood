// 3D geographic labels: real OSM data only, anchored to lon/lat, ranked, and de-cluttered on screen.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { buildLabels, layoutLabels, occluded, roadLabels } from './labels.js'

const places = JSON.parse(readFileSync(new URL('./place_labels.json', import.meta.url), 'utf8'))

// a flat 1 km x 1 km DEM whose affine maps lon/lat degrees 1:1000 to metres (enough for geometry checks)
const flat = (z = 10) => ({
  nx: 100, ny: 100, res: 10, widthM: 1000, heightM: 1000, zMin: 0, z: new Float32Array(100 * 100).fill(z),
  affine: { x: [1000, 0, 0], y: [0, 1000, 0] },
})
const road = (name, highway, coords) => ({ type: 'Feature', properties: { name, highway }, geometry: { type: 'LineString', coordinates: coords } })

test('place labels are real OSM features inside the DEM, with provenance', () => {
  const { provenance, labels } = places
  assert.equal(provenance.licence, 'ODbL 1.0')
  assert.match(provenance.source, /OpenStreetMap/)
  const [w, s, e, n] = provenance.clipped_to_dem_bbox
  for (const l of labels) {
    assert.ok(l.lon > w && l.lon < e && l.lat > s && l.lat < n, `${l.name} outside the DEM`)
    assert.ok(l.osm.length >= 1 && l.osm.every((id) => /^(node|way|relation)\/\d+$/.test(id)), `${l.name} has no OSM id`)
    assert.ok(['primary', 'locality', 'landmark'].includes(l.tier))
  }
  const names = labels.map((l) => l.name)
  for (const expected of ['Dadar West', 'Matunga East', 'Shivaji Park', 'Wadala Village', 'Dadar']) {
    assert.ok(names.includes(expected), `${expected} missing`)
  }
  assert.equal(new Set(names).size, names.length)                                   // one label per place
})

test('road labels: one per trunk/primary name at its longest segment; circles are junctions', () => {
  const fc = { features: [
    road('Tilak Road', 'primary', [[0.1, 0.1], [0.2, 0.1]]),
    road('Tilak Road', 'primary', [[0.1, 0.3], [0.5, 0.3]]),                      // longest -> label here
    road("King's Circle", 'trunk', [[0.6, 0.6], [0.62, 0.6]]),
    road('Quiet Lane', 'residential', [[0, 0], [0.9, 0.9]]),                         // too minor
  ] }
  const out = roadLabels(fc)
  assert.equal(out.length, 2)
  const tilak = out.find((r) => r.name === 'Tilak Road')
  assert.ok(Math.abs(tilak.lon - 0.3) < 1e-9 && Math.abs(tilak.lat - 0.3) < 1e-9)
  assert.equal(tilak.tier, 'road')
  assert.equal(out.find((r) => r.name === "King's Circle").tier, 'junction')
})

test('buildLabels ranks localities first and keeps labels on the terrain', () => {
  const pl = [
    { name: 'Inside Place', tier: 'primary', kind: 'suburb', rank: 100, lon: 0.5, lat: 0.5 },
    { name: 'Far Away', tier: 'locality', kind: 'neighbourhood', rank: 80, lon: 3, lat: 3 },
  ]
  const out = buildLabels(flat(), { features: [road('Some Marg', 'trunk', [[0.2, 0.2], [0.4, 0.2]])] }, pl)
  assert.deepEqual(out.map((l) => l.text), ['Inside Place', 'Some Marg'])
  assert.deepEqual([out[0].x, out[0].y], [500, 500])                                 // lon/lat -> DEM metres
})

test('layout drops the lower-ranked of two overlapping labels and never covers a flood-spot pin', () => {
  const items = [
    { visible: true, sx: 100, sy: 100, w: 80, h: 14 },
    { visible: true, sx: 110, sy: 104, w: 60, h: 14 },                              // overlaps the first
    { visible: true, sx: 300, sy: 100, w: 60, h: 14 },
    { visible: false, sx: 500, sy: 100, w: 60, h: 14 },
  ]
  layoutLabels(items)
  assert.deepEqual(items.map((i) => Boolean(i.show)), [true, false, true, false])
  const again = [{ visible: true, sx: 300, sy: 100, w: 60, h: 14 }]
  layoutLabels(again, 4, [[295, 95, 305, 105]])                                      // a pin sits there
  assert.equal(Boolean(again[0].show), false)
})

test('a label behind a ridge is occluded; one in the open is not', () => {
  const dem = flat(0)
  for (let j = 0; j < 100; j += 1) dem.z[j * 100 + 50] = 50                         // a 50 m wall at x = 505 m
  const cam = { x: 100, y: 500, z: 20 }
  assert.equal(occluded(dem, 1, cam, { x: 900, y: 500, z: 4 }), true)
  assert.equal(occluded(dem, 1, cam, { x: 300, y: 500, z: 4 }), false)
})
