// Landing-page scenario: the short story played on the landing map.
//
//   roads -> terrain + weather -> flood-risk areas -> a road turns at-risk -> candidate rescue route
//   -> a closure forces a reroute -> assignment card + alert preview -> settled landing page
//
// HONESTY (CLAUDE.md rules 1 and 3): the road geometry is REAL when it comes from /api/roads (OpenStreetMap); the
// terrain image and the recorded flooding spots are REAL when the backend serves them. EVERYTHING scripted here — the
// rain band, the risk areas, the at-risk road, the route endpoints, the closure, the assignment and the alert — is a
// DEMO scenario and is tagged 'DEMO'. It is not a forecast and nothing in it is sent anywhere. When the backend is
// unreachable the bundled DEMO_ROADS lattice is used instead, also tagged 'DEMO'.
//
// The routes are computed, not drawn by hand: a shortest path (length weighted by road class) on the road graph built
// from the segments' shared endpoints. One-way rules are not applied in this demo.
//
// Pure data + pure functions only (no React, no DOM) so it runs under `node --test` (landingScenario.test.mjs).

export const DEMO_TAG = 'DEMO'

// ---------------------------------------------------------------- timeline (ms)

const STAGE_DEFS = [
  { id: 'roads', ms: 1800, label: 'Roads', title: 'The street network', caption: 'Every road segment of the pilot area.' },
  { id: 'layers', ms: 2000, label: 'Terrain & rain', title: 'Terrain and weather', caption: 'Ground height from the city contour survey, with a scripted rain band passing over.' },
  { id: 'risk', ms: 1800, label: 'Risk areas', title: 'Flood-risk areas develop', caption: 'Water collects in risk areas; the hatched edge marks where the extent is uncertain.' },
  { id: 'atRisk', ms: 1500, label: 'At-risk road', title: 'A road becomes at risk', caption: 'A street inside the risk area is flagged before water reaches it.' },
  { id: 'route', ms: 2200, label: 'Route', title: 'A rescue route is planned', caption: 'Shortest route on the road graph that avoids the at-risk roads.' },
  { id: 'reroute', ms: 3000, label: 'Closure', title: 'A closure forces a reroute', caption: 'A closure is reported on the route; the plan is recomputed around it.' },
  { id: 'response', ms: 1900, label: 'Dispatch', title: 'Team assigned, alert drafted', caption: 'The team gets the new route; a public alert waits for human review.' },
  { id: 'settled', ms: 900, label: 'Explore', title: 'Your turn', caption: 'Open the control centre to run a real forecast on the pilot area.' },
]

export const STAGES = (() => {
  let t = 0
  return STAGE_DEFS.map(({ ms, ...s }) => {
    const out = { ...s, start: t, end: t + ms }
    t += ms
    return Object.freeze(out)
  })
})()

export const TOTAL_MS = STAGES[STAGES.length - 1].end

export const clamp01 = (v) => (v <= 0 ? 0 : v >= 1 ? 1 : v)
const ease = (v) => { const x = clamp01(v); return x * x * (3 - 2 * x) }  // smoothstep; 0 -> 0, 1 -> 1

export function stageIndexAt(t) {
  if (!(t > 0)) return 0
  for (let i = STAGES.length - 1; i >= 0; i--) if (t >= STAGES[i].start) return i
  return 0
}

const S = Object.fromEntries(STAGES.map((s) => [s.id, s]))
/** Progress through stage `id` between fractions a..b of its duration, eased. */
function span(t, id, a = 0, b = 1) {
  const s = S[id], d = s.end - s.start
  return ease((t - (s.start + a * d)) / ((b - a) * d))
}

/** Per-layer progress 0..1 at time t. Every channel is 0 at t=0 and 1 at TOTAL_MS, and never decreases. */
export function layerProgress(t) {
  return {
    roads: span(t, 'roads'),
    terrain: span(t, 'layers', 0, 0.6),
    spots: span(t, 'layers', 0.3, 0.8),
    rain: span(t, 'layers', 0, 1),
    risk: span(t, 'risk'),
    atRisk: span(t, 'atRisk'),
    route: span(t, 'route', 0, 0.85),
    closure: span(t, 'reroute', 0, 0.2),
    oldRoute: span(t, 'reroute', 0.2, 0.35),
    reroute: span(t, 'reroute', 0.35, 1),
    assignment: span(t, 'response', 0, 0.45),
    alert: span(t, 'response', 0.4, 0.9),
  }
}

// ---------------------------------------------------------------- playback

/** Time that shows stage i fully drawn: 1 ms before its end (the end itself belongs to the next stage). */
export function seekTime(i) {
  const k = Math.max(0, Math.min(STAGES.length - 1, Math.trunc(i) || 0))
  return k === STAGES.length - 1 ? TOTAL_MS : STAGES[k].end - 1
}

export function initialPlayback(reducedMotion) {
  return reducedMotion ? { t: TOTAL_MS, playing: false } : { t: 0, playing: true }
}

/** Playback state machine. Actions: tick{dt}, play, pause, toggle, replay, seek{stage}; any may carry reducedMotion. */
export function playbackReducer(state, action) {
  const rm = Boolean(action.reducedMotion)
  switch (action.type) {
    case 'tick': {
      if (!state.playing) return state
      const t = state.t + Math.max(0, action.dt || 0)
      return t >= TOTAL_MS ? { t: TOTAL_MS, playing: false } : { t, playing: true }
    }
    case 'play':
      if (rm) return { t: TOTAL_MS, playing: false }
      return state.t >= TOTAL_MS ? { t: 0, playing: true } : { t: state.t, playing: true }
    case 'pause':
      return { t: state.t, playing: false }
    case 'toggle':
      return state.playing ? playbackReducer(state, { type: 'pause' }) : playbackReducer(state, { type: 'play', reducedMotion: rm })
    case 'replay':
      return rm ? { t: TOTAL_MS, playing: false } : { t: 0, playing: true }
    case 'seek':
      return { t: seekTime(action.stage), playing: false }
    default:
      return state
  }
}

export function shouldAnimate({ playing, inView, pageVisible, reducedMotion }) {
  return Boolean(playing && inView && pageVisible && !reducedMotion)
}

// ---------------------------------------------------------------- projection (local equirectangular, metres)

const M_PER_DEG_LAT = 110574
const M_PER_DEG_LON_EQ = 111320

/** [lon, lat] -> [x, y] metres from `center`, x east, y DOWN (SVG), so north is up. Accurate to <0.1% over ~5 km. */
export function makeProjector([lon0, lat0]) {
  const kx = M_PER_DEG_LON_EQ * Math.cos((lat0 * Math.PI) / 180)
  return ([lon, lat]) => [(lon - lon0) * kx, (lat0 - lat) * M_PER_DEG_LAT]
}

const r1 = (v) => Math.round(v * 10) / 10

/** SVG path data for a list of lines (each a list of [lon, lat]). */
export function pathD(lines, project) {
  let d = ''
  for (const line of lines) {
    for (let i = 0; i < line.length; i++) {
      const [x, y] = project(line[i])
      d += `${i ? 'L' : 'M'}${r1(x)} ${r1(y)}`
    }
  }
  return d
}

export function haversineM([lon1, lat1], [lon2, lat2]) {
  const rad = Math.PI / 180, dLat = (lat2 - lat1) * rad, dLon = (lon2 - lon1) * rad
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2
  return 2 * 6371008.8 * Math.asin(Math.sqrt(a))
}

const lineLength = (c) => { let s = 0; for (let i = 1; i < c.length; i++) s += haversineM(c[i - 1], c[i]); return s }

// ---------------------------------------------------------------- road graph

// Lower factor = preferred. Keeps the demo route on arterials, the way a dispatcher would drive.
const CLASS_FACTOR = { motorway: 0.8, trunk: 0.85, primary: 0.85, secondary: 0.9, tertiary: 1, unclassified: 1.2, residential: 1.25, living_street: 1.5, service: 1.6 }
const nodeKey = ([lon, lat]) => `${lon.toFixed(6)},${lat.toFixed(6)}`

/** Undirected graph from LineString features; segments connect where their endpoints coincide (to ~0.1 m). */
export function buildGraph(features) {
  const nodes = [], index = new Map(), adj = [], segs = new Map()
  const node = (c) => {
    const k = nodeKey(c)
    let i = index.get(k)
    if (i === undefined) { i = nodes.length; index.set(k, i); nodes.push([c[0], c[1]]); adj.push([]) }
    return i
  }
  for (const f of features || []) {
    const c = f?.geometry?.type === 'LineString' ? f.geometry.coordinates : null
    if (!c || c.length < 2) continue
    const p = f.properties || {}
    const id = String(p.seg_id ?? `seg${segs.size}`)
    const a = node(c[0]), b = node(c[c.length - 1])
    const lengthM = Number.isFinite(p.length_m) ? p.length_m : lineLength(c)
    const cost = lengthM * (CLASS_FACTOR[p.highway] ?? 1.3)
    segs.set(id, { id, a, b, coords: c, lengthM, cost, name: p.name || '', highway: p.highway || '' })
    if (a !== b) { adj[a].push(id); adj[b].push(id) }
  }
  return { nodes, adj, segs }
}

export function nearestNode(graph, lonlat) {
  let best = -1, bestD = Infinity
  for (let i = 0; i < graph.nodes.length; i++) {
    if (!graph.adj[i].length) continue
    const d = haversineM(graph.nodes[i], lonlat)
    if (d < bestD) { bestD = d; best = i }
  }
  return best
}

/** Dijkstra by cost. Returns { from, to, segIds, nodePath, lengthM, coords } or null when unreachable. */
export function shortestPath(graph, from, to, { blocked } = {}) {
  if (from < 0 || to < 0) return null
  const n = graph.nodes.length
  const dist = new Float64Array(n).fill(Infinity), via = new Array(n).fill(null)
  dist[from] = 0
  const heap = [[0, from]]
  const push = (e) => { heap.push(e); let i = heap.length - 1; while (i) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p } }
  const pop = () => {
    const top = heap[0], last = heap.pop()
    if (heap.length) {
      heap[0] = last
      let i = 0
      for (;;) {
        const l = 2 * i + 1, r = l + 1
        let m = i
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r
        if (m === i) break
        ;[heap[m], heap[i]] = [heap[i], heap[m]]; i = m
      }
    }
    return top
  }
  while (heap.length) {
    const [d, u] = pop()
    if (d > dist[u]) continue
    if (u === to) break
    for (const id of graph.adj[u]) {
      if (blocked?.has(id)) continue
      const s = graph.segs.get(id)
      const v = s.a === u ? s.b : s.a
      const nd = d + s.cost
      // tie-break on segment id so equal-cost graphs give the same answer every time
      if (nd < dist[v] - 1e-9 || (Math.abs(nd - dist[v]) <= 1e-9 && via[v] && id < via[v])) { dist[v] = nd; via[v] = id; push([nd, v]) }
    }
  }
  if (from !== to && !via[to]) return null
  const segIds = [], nodePath = [to]
  for (let v = to; v !== from;) {
    const s = graph.segs.get(via[v])
    segIds.push(s.id)
    v = s.a === v ? s.b : s.a
    nodePath.push(v)
  }
  segIds.reverse(); nodePath.reverse()
  // one continuous line in travel order
  const coords = []
  segIds.forEach((id, k) => {
    const s = graph.segs.get(id)
    const c = s.a === nodePath[k] ? s.coords : [...s.coords].reverse()
    coords.push(...(k ? c.slice(1) : c))
  })
  const lengthM = segIds.reduce((acc, id) => acc + graph.segs.get(id).lengthM, 0)
  return { from, to, segIds, nodePath, lengthM, coords }
}

/** Point at fraction f (0..1) of a polyline's length. */
export function pointAlong(coords, f) {
  const total = lineLength(coords)
  let goal = clamp01(f) * total
  for (let i = 1; i < coords.length; i++) {
    const d = haversineM(coords[i - 1], coords[i])
    if (goal <= d && d > 0) {
      const k = goal / d
      return [coords[i - 1][0] + k * (coords[i][0] - coords[i - 1][0]), coords[i - 1][1] + k * (coords[i][1] - coords[i - 1][1])]
    }
    goal -= d
  }
  return coords[coords.length - 1]
}

// ---------------------------------------------------------------- DEMO scenario

// Pilot view (lon/lat): the model domain of the Hindmata / Dadar pilot. The fallback lattice sits inside it.
export const VIEW_CENTER = [72.8450, 19.0190]
export const VIEW_HALF_M = [1250, 1250]        // half width / half height of the framed area, metres

// DEMO route endpoints — scripted, not real units or a real incident.
export const SCENARIO = Object.freeze({
  base: { lonlat: [72.8402, 19.0108], label: 'Response base (DEMO)' },
  incident: { lonlat: [72.8512, 19.0268], label: 'Incident (DEMO)' },
  riskAt: 0.42,          // the main DEMO risk area sits at this fraction along the unconstrained route
  riskRadiusM: 150,
  uncertainRadiusM: 230,
  closureAt: 0.6,        // the DEMO closure is reported near this fraction along the candidate route
  team: 'Rescue team R-2',
})

/** Small bundled street lattice, DEMO geometry only (used when /api/roads is unavailable). */
export const DEMO_ROADS = (() => {
  const cols = 9, rows = 9, lon0 = 72.8370, lat0 = 19.0090, dLon = 0.0021, dLat = 0.0024
  // deterministic wobble so the lattice does not look like graph paper
  const P = (i, j) => [
    Math.round((lon0 + i * dLon + 0.00035 * Math.sin(j * 1.7 + i)) * 1e6) / 1e6,
    Math.round((lat0 + j * dLat + 0.0003 * Math.cos(i * 1.3 + j * 0.7)) * 1e6) / 1e6,
  ]
  const feats = []
  const add = (id, a, b, name, highway) => feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: [a, b] }, properties: { seg_id: id, name, highway } })
  for (let j = 0; j < rows; j++) for (let i = 0; i < cols - 1; i++) {
    add(`dx${i}_${j}`, P(i, j), P(i + 1, j), `Demo Street ${j + 1}`, j % 4 === 0 ? 'primary' : 'residential')
  }
  for (let i = 0; i < cols; i++) for (let j = 0; j < rows - 1; j++) {
    add(`dy${i}_${j}`, P(i, j), P(i, j + 1), `Demo Avenue ${String.fromCharCode(65 + i)}`, i % 4 === 1 ? 'secondary' : 'residential')
  }
  return { type: 'FeatureCollection', features: feats, provenance: { tag: DEMO_TAG, source: 'Bundled demonstration lattice (not real streets)' } }
})()

const MAJOR = new Set(['motorway', 'trunk', 'primary', 'secondary', 'tertiary'])
export const roadClass = (highway) => (highway === 'motorway' || highway === 'trunk' || highway === 'primary' ? 'major' : MAJOR.has(highway) ? 'mid' : 'minor')

const ELEVATED = /flyover|bridge|over ?pass|skywalk/i
const mostCommonName = (segs) => {
  const n = new Map()
  for (const s of segs) if (s.name) n.set(s.name, (n.get(s.name) || 0) + s.lengthM)
  return [...n.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0]?.[0] || 'an unnamed road'
}

/**
 * The whole scripted scene, computed from a road FeatureCollection (REAL or DEMO).
 * Returns null-free output: when the given roads cannot produce a route, the bundled DEMO network is used.
 */
export function buildScenario(roads) {
  const usable = roads && Array.isArray(roads.features) && roads.features.length >= 10
  const attempt = usable ? scenarioFrom(roads) : null
  return attempt || scenarioFrom(DEMO_ROADS)
}

function scenarioFrom(roads) {
  const roadsTag = roads === DEMO_ROADS ? DEMO_TAG : String(roads.provenance?.tag || 'UNKNOWN')
  const g = buildGraph(roads.features)
  const from = nearestNode(g, SCENARIO.base.lonlat)
  const to = nearestNode(g, SCENARIO.incident.lonlat)
  const direct = shortestPath(g, from, to)
  if (!direct || direct.segIds.length < 4) return null

  // DEMO risk areas: the main one on the direct route, a second one off to the side for context.
  const center = pointAlong(direct.coords, SCENARIO.riskAt)
  const side = pointAlong(direct.coords, 0.78)
  const riskAreas = [
    { id: 'risk-main', center, radiusM: SCENARIO.riskRadiusM, uncertainM: SCENARIO.uncertainRadiusM, tag: DEMO_TAG },
    { id: 'risk-side', center: [side[0] - 0.0042, side[1] + 0.0006], radiusM: 110, uncertainM: 170, tag: DEMO_TAG },
  ]
  const inRisk = (s) => s.coords.some((c) => haversineM(c, center) <= SCENARIO.riskRadiusM)
  const riskSegs = [...g.segs.values()].filter(inRisk)
  // "the road that turns at-risk": the direct route's segments inside the risk area
  const atRiskSegs = direct.segIds.map((id) => g.segs.get(id)).filter(inRisk)
  if (!atRiskSegs.length) return null
  const blockedRisk = new Set(riskSegs.map((s) => s.id))

  const route = shortestPath(g, from, to, { blocked: blockedRisk })
  if (!route) return null

  // DEMO closure: the first at-grade segment on the candidate route at or after SCENARIO.closureAt of its length,
  // skipping the first and last segment (those would strand the endpoints).
  let acc = 0, closureSeg = null
  for (let k = 1; k < route.segIds.length - 1; k++) {
    const s = g.segs.get(route.segIds[k])
    acc += g.segs.get(route.segIds[k - 1]).lengthM
    if (acc / route.lengthM >= SCENARIO.closureAt && !ELEVATED.test(s.name)) { closureSeg = s; break }
  }
  if (!closureSeg) return null
  const blockedAll = new Set([...blockedRisk, closureSeg.id])
  const reroute = shortestPath(g, from, to, { blocked: blockedAll })
  if (!reroute) return null

  const atRiskName = mostCommonName(atRiskSegs)
  const closureName = closureSeg.name || 'an unnamed road'
  const fmtKm = (m) => `${(m / 1000).toFixed(1)} km`
  const detourM = Math.max(0, reroute.lengthM - route.lengthM)
  const endName = mostCommonName([g.segs.get(reroute.segIds[reroute.segIds.length - 1])])

  return {
    roadsTag,
    roadsSource: roads.provenance?.source || '',
    segmentCount: g.segs.size,
    graph: g,
    start: { lonlat: g.nodes[from], label: SCENARIO.base.label },
    end: { lonlat: g.nodes[to], label: SCENARIO.incident.label },
    riskAreas,
    atRisk: { segIds: atRiskSegs.map((s) => s.id), name: atRiskName, tag: DEMO_TAG },
    direct: { segIds: direct.segIds, lengthM: direct.lengthM },
    route: { ...route, label: `Candidate route · ${fmtKm(route.lengthM)}` },
    closure: {
      segId: closureSeg.id, name: closureName, tag: DEMO_TAG,
      lonlat: pointAlong(closureSeg.coords, 0.5),
      reason: `DEMO closure reported on ${closureName}`,
    },
    reroute: { ...reroute, label: `New route · ${fmtKm(reroute.lengthM)}`, detourM },
    assignment: {
      tag: DEMO_TAG, team: SCENARIO.team, destination: `Incident near ${endName}`,
      routeText: `${fmtKm(reroute.lengthM)} by road${detourM >= 50 ? ` (+${fmtKm(detourM)} detour)` : ''}`,
      avoids: `avoids the closure on ${closureName} and the at-risk stretch of ${atRiskName}`,
      status: 'Proposed · dispatcher approves',
    },
    alert: {
      tag: DEMO_TAG, sent: false, channel: 'SMS',
      text: `FloodNet DEMO: Flooding possible near ${atRiskName}. Avoid the area if you can. Emergency: 112.`,
      link: 'floodnet/alerts/…',
    },
  }
}

/** SVG viewBox [x, y, w, h] (projected metres) around the routes and risk areas, padded, at least minM a side. */
export function sceneViewBox(scenario, project, { padM = 220, minM = 1400 } = {}) {
  const pts = [...scenario.route.coords, ...scenario.reroute.coords].map(project)
  for (const r of scenario.riskAreas) {
    const [cx, cy] = project(r.center)
    pts.push([cx - r.uncertainM, cy - r.uncertainM], [cx + r.uncertainM, cy + r.uncertainM])
  }
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y) }
  const w = Math.max(minM, x1 - x0 + 2 * padM), h = Math.max(minM, y1 - y0 + 2 * padM)
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2
  return [r1(cx - w / 2), r1(cy - h / 2), r1(w), r1(h)]
}

/**
 * Fit a content box (SVG units) into a W x H pixel frame, keeping `reserveRight` pixels on the right free for overlay
 * cards. Returns the frame-shaped viewBox (so every stacked SVG layer lines up with no letterbox) and u, the SVG
 * units per CSS pixel. `top` / `bottom` pixel strips (map labels) are kept clear the same way. A frame with no size
 * yet gets the content box itself.
 */
export function fitView(content, W, H, reserveRight = 0, { top = 0, bottom = 0 } = {}) {
  const [x, y, w, h] = content
  if (!(W > 0 && H > 0)) return { vb: content, u: Math.max(w, h) / 600 }
  const availW = Math.max(W * 0.4, W - Math.max(0, reserveRight))
  const t = Math.max(0, top), b = Math.max(0, bottom)
  const availH = Math.max(H * 0.5, H - t - b)
  const s = Math.min(availW / w, availH / h)     // pixels per unit
  const u = 1 / s
  const vbW = W * u, vbH = H * u
  const vx = x - (availW * u - w) / 2
  const topPx = availH === H - t - b ? t : (H - availH) / 2
  const vy = y - topPx * u - (availH * u - h) / 2
  return { vb: [vx, vy, vbW, vbH], u }
}
