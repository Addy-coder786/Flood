// Self-contained simulation results (backend/floodnet/api/bundle.py).
//
// POST /api/simulate returns the WHOLE run: timeline, every frame's depth image data, street depths, drainage
// node/edge state, aggregate series, hotspots and the CAP draft. This module decodes it once and rebuilds any
// frame locally in exactly the shape GET /api/simulation/{run_id}/frame/{t} returns, so the map, the 3D view and
// every panel render straight from the response. Nothing afterwards asks a server for the run -- on Vercel the
// next request may reach a different instance, whose memory does not hold it.

const TYPED = { f4: Float32Array, u1: Uint8Array, u2: Uint16Array }

function b64ToBytes(b64) {
  if (typeof Buffer !== 'undefined' && typeof window === 'undefined') return new Uint8Array(Buffer.from(b64, 'base64'))
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i)
  return out
}

/** base64(zlib(little-endian typed array)) -> { data: TypedArray, shape } (zlib = the 'deflate' format). */
export async function unpack(p) {
  const stream = new Blob([b64ToBytes(p.data)]).stream().pipeThrough(new DecompressionStream('deflate'))
  const buf = await new Response(stream).arrayBuffer()
  return { data: new TYPED[p.dtype](buf), shape: p.shape }
}

/** Decode a self-contained response into a run object (the response's fields + a decoded `bundle`). */
export async function decodeRun(res) {
  const b = res?.bundle
  if (!b || b.version !== 1) throw new Error('The server returned an incomplete simulation result.')
  const [levels, hgl, s3, raw, cause, util, flow, streets] = await Promise.all([
    unpack(b.depth.levels_delta), unpack(b.nodes.hgl_m), unpack(b.nodes.surcharge_m3),
    unpack(b.nodes.surcharging_raw), unpack(b.nodes.cause), unpack(b.edges.util), unpack(b.edges.flow_m3s),
    unpack(b.streets.depth_m),
  ])
  // frame-delta (mod 256) -> absolute levels, in place
  const [F, ny, nx] = levels.shape
  const cells = ny * nx
  for (let k = 1; k < F; k += 1) {
    for (let c = 0; c < cells; c += 1) levels.data[k * cells + c] = (levels.data[k * cells + c] + levels.data[(k - 1) * cells + c]) & 255
  }
  const { bundle: _drop, ...rest } = res
  return {
    ...rest,
    bundle: {
      t_min: b.t_min, rain_mm_h: b.rain_mm_h, max_depth_cm: b.max_depth_cm, thresholds: b.thresholds,
      depth: { ...b.depth, levels: levels.data, nx, ny },
      nodes: { ...b.nodes, hgl_m: hgl.data, surcharge_m3: s3.data, surcharging_raw: raw.data, cause: cause.data },
      edges: { ...b.edges, util: util.data, flow_m3s: flow.data },
      streets: { seg_ids: b.streets.seg_ids, depth_m: streets.data },
      segments: b.segments,
      segIndex: new Map(b.streets.seg_ids.map((s, i) => [s, i])),
    },
  }
}

// ---------------------------------------------------------------- the backend's own rules, reproduced exactly
const finite = (x) => (Number.isFinite(x) ? x : 0)                               // api/main.py::_f
const round1 = (x) => Math.round(x * 10) / 10

export function severity(cm, bands) {                                            // streets/aggregate.py::severity
  for (const [limit, label] of bands) if (cm < limit) return label
  return 'critical'
}
export const passable = (cm, vehicle, limits) => cm < limits[vehicle]            // streets/aggregate.py::passable

/** Index of the frame closest to t_min (api/main.py::_frame_index). */
export function frameIndex(run, tMin) {
  const ts = run.bundle.t_min
  let best = 0
  for (let k = 1; k < ts.length; k += 1) if (Math.abs(ts[k] - tMin) < Math.abs(ts[best] - tMin)) best = k
  return best
}

const streetDepthM = (b, k, s) => b.streets.depth_m[k * b.streets.seg_ids.length + s]

/** Node state for one frame (api/main.py::_node_fill_state): interval-aware surcharging, fill, freeboard. */
export function nodeState(b, k, n) {
  const N = b.nodes.ids.length
  const hgl = b.nodes.hgl_m[k * N + n]
  const s3 = b.nodes.surcharge_m3[k * N + n]
  const invert = b.nodes.invert_m[n]; const ground = b.nodes.ground_floor_m[n]
  const fill = Math.min(Math.max((hgl - invert) / Math.max(ground - invert, 1e-9), 0), 1)
  const surcharging = b.nodes.surcharging_raw[k * N + n] === 1 || s3 > 1e-9
  const atCapacity = !surcharging && fill >= 0.999
  return {
    hgl, s3, fill, freeboard: ground - hgl, surcharging,
    cause: b.nodes.cause_codes[b.nodes.cause[k * N + n]],
    state: surcharging ? 'surcharging' : atCapacity ? 'at_capacity' : 'normal',
  }
}

/** RGBA pixels of the depth image, top row first -- api/png.py::depth_png_base64 from the 8-bit level. */
export function depthRgba(levels, k, nx, ny) {
  const out = new Uint8ClampedArray(nx * ny * 4)
  const base = k * nx * ny
  for (let j = 0; j < ny; j += 1) {
    const src = base + (ny - 1 - j) * nx                                           // row 0 of the grid is south
    for (let i = 0; i < nx; i += 1) {
      const lvl = levels[src + i]
      if (!lvl) continue
      const frac = (lvl - 1) / 254
      const o = (j * nx + i) * 4
      out[o] = Math.trunc(120 * (1 - frac))
      out[o + 1] = Math.trunc(180 - 140 * frac)
      out[o + 2] = Math.trunc(255 - 95 * frac)
      out[o + 3] = Math.trunc(60 + 195 * frac)
    }
  }
  return out
}

function pngBase64(rgba, nx, ny) {
  if (typeof document === 'undefined') return null                                // tests: no canvas
  const canvas = document.createElement('canvas')
  canvas.width = nx; canvas.height = ny
  canvas.getContext('2d').putImageData(new ImageData(rgba, nx, ny), 0, 0)
  return canvas.toDataURL('image/png').slice('data:image/png;base64,'.length)
}

/** One frame, same shape as GET /api/simulation/{run_id}/frame/{t}. `roads` = GET /api/roads (geometry). */
export function buildFrame(run, k, roads) {
  const b = run.bundle
  const { thresholds: th } = b
  const features = []
  for (const f of roads?.features || []) {
    const p = f.properties || {}
    const s = b.segIndex.get(p.seg_id)
    const cm = s == null ? 0 : finite(streetDepthM(b, k, s)) * 100
    features.push({
      type: 'Feature', geometry: f.geometry,
      properties: {
        seg_id: p.seg_id, name: p.name, highway: p.highway, depth_cm: round1(cm), severity: severity(cm, th.severity_bands_cm),
        passable_car: passable(cm, 'car', th.vehicle_limit_cm), passable_ambulance: passable(cm, 'ambulance', th.vehicle_limit_cm),
      },
    })
  }
  const nodes = b.nodes.ids.map((id, n) => {
    const st = nodeState(b, k, n)
    return {
      id, lon: b.nodes.lon[n], lat: b.nodes.lat[n], hgl_m: finite(st.hgl), surcharging: st.surcharging,
      surcharge_m3: finite(st.s3), cause: st.cause, fill_frac: finite(st.fill), freeboard_m: finite(st.freeboard), state: st.state,
    }
  })
  const E = b.edges.ids.length
  const edges = b.edges.ids.map((id, e) => ({
    id, util: finite(b.edges.util[k * E + e]), flow_m3s: finite(b.edges.flow_m3s[k * E + e]),
    edge_capacity_m3s: b.edges.capacity_m3s[e], blockage: b.edges.blockage[e],
  }))
  const { nx, ny } = b.depth
  return {
    run_id: run.run_id, t_min: b.t_min[k], rain_mm_h: finite(b.rain_mm_h[k]),
    streets: { type: 'FeatureCollection', features, provenance: { roads: roads?.provenance ?? null, depths: 'derived from simulation frame' } },
    nodes, edges,
    depth_grid: {
      grid: b.depth.grid, bbox_lonlat: b.depth.bbox_lonlat, png_base64: pngBase64(depthRgba(b.depth.levels, k, nx, ny), nx, ny),
      max_depth_cm: b.max_depth_cm[k], scale_max_cm: b.depth.scale_max_cm,
    },
    provenance: run.provenance, data_mode: run.data_mode,
  }
}

/** The run's series, same shape as GET /api/simulation/{run_id}/series (street series rebuilt locally). */
export function buildSeries(run) {
  const b = run.bundle
  const F = b.t_min.length
  const streets = {}
  b.streets.seg_ids.forEach((sid, s) => {
    const row = new Array(F)
    for (let k = 0; k < F; k += 1) row[k] = round1(finite(streetDepthM(b, k, s)) * 100)
    streets[sid] = row
  })
  return { ...run.series, streets, provenance: run.provenance, data_mode: run.data_mode }
}

const DOMINANT = { overcapacity: 'drainage_overcapacity', blockage: 'drainage_blockage', downstream: 'drainage_downstream_backup' }

/** Street inspector, same result as GET /api/simulation/{run_id}/explain/{seg_id}. */
export function explainSegment(run, segId, tMin, roads) {
  const b = run.bundle
  const s = b.segIndex.get(segId)
  const feat = (roads?.features || []).find((f) => f.properties?.seg_id === segId)
  if (s == null || !feat || !b.segments) throw new Error(`unknown seg_id '${segId}'`)
  const F = b.t_min.length
  const cmAt = (k) => finite(streetDepthM(b, k, s)) * 100
  let k = 0
  if (tMin != null) k = frameIndex(run, tMin)
  else for (let q = 1; q < F; q += 1) if (cmAt(q) > cmAt(k)) k = q
  const depthCm = cmAt(k)
  const node = b.segments.node_index[s]
  const E = b.edges.ids.length
  let util = 0; let incident = false
  for (let e = 0; e < E; e += 1) {
    if (b.edges.us[e] === node || b.edges.ds[e] === node) {
      const u = b.edges.util[k * E + e]
      util = incident ? Math.max(util, u) : u
      incident = true
    }
  }
  const st = nodeState(b, k, node)
  const ground = b.segments.ground_elevation_m[s]
  // segment timing (analysis/hotspots.py::segment_timing)
  const th = b.thresholds
  const floodThresholdCm = th.flood_threshold_cm
  let peakK = 0; let onset = null
  for (let q = 0; q < F; q += 1) {
    if (cmAt(q) > cmAt(peakK)) peakK = q
    if (onset == null && cmAt(q) >= floodThresholdCm) onset = b.t_min[q]
  }
  return {
    run_id: run.run_id, seg_id: segId, seg_name: feat.properties?.name ?? null, t_min: b.t_min[k],
    depth_cm: Math.round(depthCm * 100) / 100, severity: severity(depthCm, th.severity_bands_cm),
    passable_car: passable(depthCm, 'car', th.vehicle_limit_cm), passable_ambulance: passable(depthCm, 'ambulance', th.vehicle_limit_cm),
    rainfall_mm_h: finite(b.rain_mm_h[k]),
    nearest_drainage_node: {
      id: b.nodes.ids[node], distance_m: b.segments.node_distance_m[s], surcharging: st.surcharging,
      cause: st.cause, utilization: incident ? finite(util) : 0,
    },
    dominant_cause: DOMINANT[st.cause] || (depthCm > 0 ? 'surface_ponding_only' : 'unknown'),
    timing: { peak_depth_cm: round1(cmAt(peakK)), peak_t_min: b.t_min[peakK], onset_t_min: onset, threshold_cm: floodThresholdCm },
    terrain_context: {
      ground_elevation_m: ground,
      note: ground == null ? b.segments.terrain_note_outside : b.segments.terrain_note_inside,
    },
    provenance: run.explain_provenance,
  }
}

/** Wet street depths (metres) of one frame, for the route planner (a missing segment is dry). */
export function frameDepths(run, k) {
  const b = run.bundle
  const out = {}
  b.streets.seg_ids.forEach((sid, s) => {
    const d = streetDepthM(b, k, s)
    if (d > 0) out[sid] = d
  })
  return out
}

/** The run's wet street series (cm, 0.1 cm) for time-aware route safety. */
export function wetSeries(run) {
  const b = run.bundle
  const F = b.t_min.length
  const streets = {}
  b.streets.seg_ids.forEach((sid, s) => {
    let wet = false
    const row = new Array(F)
    for (let k = 0; k < F; k += 1) { row[k] = round1(finite(streetDepthM(b, k, s)) * 100); if (row[k] > 0) wet = true }
    if (wet) streets[sid] = row
  })
  return { t_min: b.t_min, streets }
}
