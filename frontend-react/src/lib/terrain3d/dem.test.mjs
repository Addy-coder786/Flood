// 3D terrain rules (see dem.js): same DEM as the solver, lossless decode, relief is display-only, no-data is a
// hole, and the view mode never touches forecast state.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  decodeDem, buildPositions, buildIndices, buildColors, lonLatToGrid, cellAt, surfaceAt, drawnHeight,
  RELIEF_OPTIONS, DEFAULT_RELIEF,
} from './dem.js'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const read = (rel) => readFileSync(join(SRC, rel), 'utf8')

/** A payload shaped exactly like GET /api/terrain/dem, from known float32 values (row 0 = south). */
function payload(values, nx, ny, extra = {}) {
  const buf = Buffer.alloc(values.length * 4)
  values.forEach((v, k) => buf.writeFloatLE(v, k * 4))
  return {
    shape: [ny, nx], dtype: 'float32', byte_order: 'little', row0: 'south', grid: { res: 10, nx, ny },
    z_base64: buf.toString('base64'), bbox_lonlat: [72.83, 19.0, 72.86, 19.03],
    lonlat_to_grid: { x: [100000, 0, -7283000], y: [0, 110000, -2090000], max_residual_m: 0.09 },
    ...extra,
  }
}
const Z = [29.4, 31.25, 16.1413021, 28.0, 40.3720055, 30.5] // 3 x 2, includes the real pilot min / max values

test('decode is lossless: the float32 values come back bit-for-bit, row 0 = south', () => {
  const dem = decodeDem(payload(Z, 3, 2))
  assert.deepEqual(Array.from(dem.z), Z.map((v) => Math.fround(v)))
  assert.equal(dem.zMin, Math.fround(16.1413021))
  assert.equal(dem.zMax, Math.fround(40.3720055))
  assert.deepEqual([dem.lowest.i, dem.lowest.j], [2, 0])
  assert.deepEqual([dem.highest.i, dem.highest.j], [1, 1])
  assert.equal(dem.widthM, 30)
})

test('an unexpected encoding or size is refused, never guessed', () => {
  assert.throws(() => decodeDem(payload(Z, 3, 2, { dtype: 'uint8' })))
  assert.throws(() => decodeDem(payload(Z, 3, 2, { row0: 'north' })))
  assert.throws(() => decodeDem(payload(Z.slice(0, 5), 3, 2)))
})

test('visual relief changes only the drawn height; the DEM and the inspector value never change', () => {
  const dem = decodeDem(payload(Z, 3, 2))
  const before = Array.from(dem.z)
  const p3 = buildPositions(dem, 3)
  const p20 = buildPositions(dem, 20)
  assert.deepEqual(Array.from(dem.z), before)                       // untouched by rendering
  assert.equal(cellAt(dem, 15, 15).elevation, Math.fround(40.3720055)) // inspector reads the DEM, at any relief
  for (let k = 0; k < Z.length; k += 1) {
    assert.equal(p3[k * 3], p20[k * 3]); assert.equal(p3[k * 3 + 1], p20[k * 3 + 1])       // x, y fixed
    assert.ok(Math.abs(p20[k * 3 + 2] - (dem.z[k] - dem.zMin) * 20) < 1e-3)                 // h = (z - zmin) * relief
  }
  assert.equal(drawnHeight(dem, dem.zMin, 10), 0)
  assert.ok(RELIEF_OPTIONS.includes(DEFAULT_RELIEF) && RELIEF_OPTIONS.every((r) => r >= 1))
})

test('vertices sit on model cell centres in the grid frame', () => {
  const dem = decodeDem(payload(Z, 3, 2))
  const p = buildPositions(dem, 1)
  assert.deepEqual([p[0], p[1]], [5, 5])
  assert.deepEqual([p[15], p[16]], [25, 15])
  assert.equal(cellAt(dem, 29.9, 0.1).elevation, Math.fround(16.1413021))
  assert.equal(cellAt(dem, -1, 5), null)
  assert.equal(cellAt(dem, 5, 20), null)
  assert.equal(surfaceAt(dem, 5, 5), Math.fround(29.4))              // exact at a cell centre
  assert.deepEqual(lonLatToGrid(dem, 72.8301, 19.0001).map((v) => Math.round(v)), [10, 11])
})

test('no-data is a hole in the mesh, never an invented elevation', () => {
  const dem = decodeDem(payload([1, 2, 3, NaN, 5, 6, 7, 8, 9], 3, 3))
  assert.equal(dem.nodata, 1)
  assert.equal(cellAt(dem, 5, 15), null)                             // the NaN cell reports nothing
  const idx = Array.from(buildIndices(dem))
  assert.ok(!idx.includes(3))                                         // no triangle uses the no-data vertex
  assert.equal(idx.length, 2 * 2 * 3)                                 // only the two quads that avoid it
  assert.equal(buildIndices(decodeDem(payload(Z, 3, 2))).length, 2 * 2 * 3)
  assert.equal(buildColors(dem).length, 27)
})

test('the 3D view gets elevations only from /api/terrain/dem — no terrain service, no generated relief', () => {
  const view = read('components/Terrain3D/Terrain3D.jsx')
  assert.match(view, /getDem\(\)\.then\(decodeDem\)/)
  assert.match(read('api/client.js'), /getDem = \(\) => request\('\/api\/terrain\/dem'\)/)
  const code = view + read('lib/terrain3d/dem.js')
  for (const re of [/mapbox/i, /cesium/i, /maptiler/i, /terrarium/i, /srtm/i, /Math\.random/, /noise\(/i, /https?:\/\//]) {
    assert.doesNotMatch(code, re)
  }
})

test('the timeline drives overlays only; terrain geometry depends on the DEM and the relief alone', () => {
  const view = read('components/Terrain3D/Terrain3D.jsx')
  assert.match(view, /attr\.array\.set\(buildPositions\(dem, relief\)\)[\s\S]*?\}, \[dem, relief\]\)/)
  assert.match(view, /\}, \[dem, live, layers\.depth\]\)/)            // water follows the forecast frame
  assert.match(view, /\}, \[dem, live, layers\.streets, relief, selectedSegId\]\)/)
  assert.doesNotMatch(view, /buildPositions\([^)]*(frame|live|currentT)/)
})

test('2D | 3D is view state only: switching cannot reset the run, the time or the source', () => {
  const ctx = read('state/FloodNetContext.jsx')
  assert.match(ctx, /const \[mapMode, setMapMode\] = useState\('2d'\)/)
  const toggle = read('components/Terrain3D/MapModeToggle.jsx')
  assert.match(toggle, /onClick=\{\(\) => setMapMode\(mode\)\}/)
  assert.doesNotMatch(toggle, /setCurrentT|runSimulation|setScenarioId|toggleLayer|selectSegment/)
  const app = read('App.jsx')
  assert.match(app, /visibility: 'hidden'/)                           // Leaflet stays mounted under 3D
  assert.match(app, /lazy\(\(\) => import\('\.\/components\/Terrain3D\/Terrain3D\.jsx'\)\)/)
})
