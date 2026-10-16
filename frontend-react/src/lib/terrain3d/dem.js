// FloodNet DEM helpers for the 3D terrain view — pure functions (no React, no three.js, no DOM) so the rules
// below are unit-tested under `node --test` (dem.test.mjs).
//
// THE RULES
//   1. The elevations are the simulator's own: GET /api/terrain/dem sends `Terrain.z` as raw float32 bytes and
//      `decodeDem` reads them back bit-for-bit. Nothing here smooths, resamples, fills or rescales them.
//   2. Visual relief (exaggeration) multiplies the DRAWN height only, about the DEM's own minimum. `dem.z` is
//      never written to; the elevation inspector always reports `dem.z`, never a mesh coordinate.
//   3. The mesh lives in the model's frame: metres east/north of the grid's south-west corner, one vertex per
//      model cell centre. lon/lat overlays are placed with the backend's fitted affine (error reported by it).

/** base64 -> Uint8Array (works in browsers and in Node). */
export function bytesFromBase64(b64) {
  if (typeof atob === 'function') {
    const bin = atob(b64)
    const out = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i)
    return out
  }
  return new Uint8Array(Buffer.from(b64, 'base64'))
}

/** Decode the /api/terrain/dem payload. Throws rather than guess if the contract is not the one documented. */
export function decodeDem(payload) {
  const [ny, nx] = payload.shape
  if (payload.dtype !== 'float32' || payload.byte_order !== 'little' || payload.row0 !== 'south') {
    throw new Error('unexpected DEM encoding')
  }
  const bytes = bytesFromBase64(payload.z_base64)
  if (bytes.byteLength !== nx * ny * 4) throw new Error('DEM size does not match its declared shape')
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const z = new Float32Array(nx * ny)
  for (let k = 0; k < z.length; k += 1) z[k] = view.getFloat32(k * 4, true)

  let zMin = Infinity
  let zMax = -Infinity
  let iMin = -1
  let iMax = -1
  let nodata = 0
  for (let k = 0; k < z.length; k += 1) {
    const v = z[k]
    if (!Number.isFinite(v)) { nodata += 1; continue }
    if (v < zMin) { zMin = v; iMin = k }
    if (v > zMax) { zMax = v; iMax = k }
  }
  const building = payload.building_base64 ? bytesFromBase64(payload.building_base64) : null
  return {
    nx, ny, res: payload.grid.res, z, zMin, zMax, nodata,
    lowest: cellInfo({ nx, res: payload.grid.res, z }, iMin), highest: cellInfo({ nx, res: payload.grid.res, z }, iMax),
    p01: payload.z_p01 ?? zMin, p99: payload.z_p99 ?? zMax,
    building: building && building.length === nx * ny ? building : null,
    affine: payload.lonlat_to_grid, bbox: payload.bbox_lonlat, datum: payload.datum, sha256: payload.sha256,
    widthM: nx * payload.grid.res, heightM: ny * payload.grid.res,
  }
}

function cellInfo(dem, k) {
  if (k < 0) return null
  const i = k % dem.nx
  const j = Math.floor(k / dem.nx)
  return { i, j, x: (i + 0.5) * dem.res, y: (j + 0.5) * dem.res, elevation: dem.z[k] }
}

/** lon/lat -> metres east/north of the grid's SW corner (the backend's fitted affine). */
export function lonLatToGrid(dem, lon, lat) {
  const { x, y } = dem.affine
  return [x[0] * lon + x[1] * lat + x[2], y[0] * lon + y[1] * lat + y[2]]
}

/** Model cell containing a grid-frame point, or null outside the DEM. The value is the DEM's own. */
export function cellAt(dem, x, y) {
  const i = Math.floor(x / dem.res)
  const j = Math.floor(y / dem.res)
  if (i < 0 || j < 0 || i >= dem.nx || j >= dem.ny) return null
  const elevation = dem.z[j * dem.nx + i]
  return Number.isFinite(elevation) ? { i, j, elevation } : null
}

/** Bilinear height between cell centres — used ONLY to drape overlays on the drawn surface. */
export function surfaceAt(dem, x, y) {
  const fx = Math.min(Math.max(x / dem.res - 0.5, 0), dem.nx - 1)
  const fy = Math.min(Math.max(y / dem.res - 0.5, 0), dem.ny - 1)
  const i0 = Math.floor(fx); const j0 = Math.floor(fy)
  const i1 = Math.min(i0 + 1, dem.nx - 1); const j1 = Math.min(j0 + 1, dem.ny - 1)
  const tx = fx - i0; const ty = fy - j0
  const at = (i, j) => { const v = dem.z[j * dem.nx + i]; return Number.isFinite(v) ? v : dem.zMin }
  return (at(i0, j0) * (1 - tx) + at(i1, j0) * tx) * (1 - ty) + (at(i0, j1) * (1 - tx) + at(i1, j1) * tx) * ty
}

/** Drawn height for a real elevation. Relief is exaggerated about the DEM minimum; the DEM is untouched. */
export function drawnHeight(dem, elevation, exaggeration) {
  return (elevation - dem.zMin) * exaggeration
}

/** Vertex positions [x, y, h] per model cell centre, plus which vertices are no-data (left out of the mesh). */
export function buildPositions(dem, exaggeration) {
  const { nx, ny, res, z } = dem
  const positions = new Float32Array(nx * ny * 3)
  for (let j = 0; j < ny; j += 1) {
    for (let i = 0; i < nx; i += 1) {
      const k = j * nx + i
      const v = z[k]
      positions[k * 3] = (i + 0.5) * res
      positions[k * 3 + 1] = (j + 0.5) * res
      positions[k * 3 + 2] = Number.isFinite(v) ? drawnHeight(dem, v, exaggeration) : 0
    }
  }
  return positions
}

/** Two triangles per cell quad; a quad touching a no-data cell is omitted (a hole, never an invented value). */
export function buildIndices(dem) {
  const { nx, ny, z } = dem
  const out = []
  for (let j = 0; j < ny - 1; j += 1) {
    for (let i = 0; i < nx - 1; i += 1) {
      const a = j * nx + i; const b = a + 1; const c = a + nx; const d = c + 1
      if (!(Number.isFinite(z[a]) && Number.isFinite(z[b]) && Number.isFinite(z[c]) && Number.isFinite(z[d]))) continue
      out.push(a, b, d, a, d, c)
    }
  }
  return new Uint32Array(out)
}

// Muted hypsometric ramp in FloodNet's palette: low ground cool slate-teal, high ground warm sand/terracotta.
const RAMP = [
  [0.00, [0x4f, 0x7d, 0x8c]], [0.25, [0x8f, 0xb3, 0xa6]], [0.50, [0xe4, 0xdc, 0xc3]],
  [0.75, [0xd2, 0xa6, 0x79]], [1.00, [0xa8, 0x5f, 0x45]],
]

/** Colour for an elevation, stretched between the DEM's own 1st and 99th percentiles (display only). */
export function rampColor(dem, elevation) {
  const span = Math.max(dem.p99 - dem.p01, 1e-6)
  const t = Math.min(Math.max((elevation - dem.p01) / span, 0), 1)
  let k = 1
  while (k < RAMP.length - 1 && t > RAMP[k][0]) k += 1
  const [t0, c0] = RAMP[k - 1]; const [t1, c1] = RAMP[k]
  const u = (t - t0) / (t1 - t0)
  return [0, 1, 2].map((n) => (c0[n] + (c1[n] - c0[n]) * u) / 255)
}

export function buildColors(dem) {
  const colors = new Float32Array(dem.nx * dem.ny * 3)
  for (let k = 0; k < dem.z.length; k += 1) {
    const v = dem.z[k]
    const c = Number.isFinite(v) ? rampColor(dem, v) : [0.6, 0.6, 0.6]
    const shade = dem.building?.[k] ? 0.8 : 1 // building footprints read slightly darker; they are NOT extruded
    colors[k * 3] = c[0] * shade; colors[k * 3 + 1] = c[1] * shade; colors[k * 3 + 2] = c[2] * shade
  }
  return colors
}

export const RELIEF_OPTIONS = [3, 5, 10, 20]
export const DEFAULT_RELIEF = 5    // visual only: the DEM the simulation uses never changes

export function legendStops(dem) {
  return [dem.p01, (dem.p01 + dem.p99) / 2, dem.p99].map((v) => ({ elevation: v, color: rampColor(dem, v) }))
}
