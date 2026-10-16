import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import {
  STAGES, TOTAL_MS, VIEW_CENTER, buildScenario, fitView, initialPlayback, layerProgress, makeProjector, pathD,
  playbackReducer, roadClass, sceneViewBox, shouldAnimate, stageIndexAt,
} from '../../lib/landingScenario.js'
import { ROUTE_STYLE } from '../../lib/rescueTrip.js'
import styles from './LandingStory.module.css'

// ============================================================================================
// LandingStory -- the interactive map sequence on the landing page (lib/landingScenario.js holds the logic).
//
// Data: road geometry, the terrain image and the recorded flooding spots come from the FloodNet context, which
// already loads /api/roads, /api/terrain and /api/hotspots at start-up (no second download). They keep their own
// provenance tags. Everything scripted -- rain band, risk areas, at-risk road, route endpoints, closure,
// assignment, alert -- is a DEMO scenario and the map says so. No backend -> bundled DEMO geometry, also labelled.
//
// Rendering: four stacked layers share one viewBox. Terrain (an <svg> image) and rain (a CSS gradient) animate
// only opacity/transform, which the compositor handles without repainting; the road network (2,978 paths) is
// painted once and only re-clipped during the first stage; the small overlay SVG carries everything else.
//
// Motion: one requestAnimationFrame loop writes those attributes directly (React re-renders only when the stage
// changes). It runs only while playing, on screen (IntersectionObserver) and with the tab visible
// (visibilitychange). With prefers-reduced-motion the settled state is shown and the steps jump without motion.
// ============================================================================================

const FALLBACK_AFTER_MS = 6000   // stop waiting for the backend and show the bundled DEMO geometry
// The dashboard's route style (lib/rescueTrip.js ROUTE_STYLE: blue core, navy outline, white halo; slate dashes for
// an earlier route), handed to the stylesheet as custom properties so the landing map draws routes the same way.
const ROUTE_VARS = {
  '--route-halo': ROUTE_STYLE.halo.color,
  '--route-outline': ROUTE_STYLE.outline.color,
  '--route-core': ROUTE_STYLE.core.color,
  '--route-old': ROUTE_STYLE.previous.color,
}
const STAGE_INDEX = Object.fromEntries(STAGES.map((s, i) => [s.id, i]))

function mediaReduced() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

const fmtKm = (m) => `${(m / 1000).toFixed(1)} km`

function useGeometry(scenario, terrain, hotspots) {
  return useMemo(() => {
    if (!scenario) return null
    const project = makeProjector(VIEW_CENTER)
    const vb = sceneViewBox(scenario, project, { padM: 200, minM: 1300 })
    const cx = vb[0] + vb[2] / 2, cy = vb[1] + vb[3] / 2
    const half = Math.max(vb[2], vb[3]) * 1.6     // wide frames show more of the network around the scene
    const inside = ([x, y]) => Math.abs(x - cx) < half && Math.abs(y - cy) < half
    const lines = { major: [], mid: [], minor: [] }
    for (const s of scenario.graph.segs.values()) {
      if (s.coords.some((c) => inside(project(c)))) lines[roadClass(s.highway)].push(s.coords)
    }
    const at = (lonlat) => project(lonlat).map((v) => Math.round(v * 10) / 10)
    const seg = (id) => scenario.graph.segs.get(id).coords
    const mid = (coords) => at(coords[Math.floor(coords.length / 2)])

    let terrainImg = null
    if (terrain?.png_base64 && Array.isArray(terrain.bbox_lonlat)) {
      const [w, s, e, n] = terrain.bbox_lonlat
      const [x0, y0] = project([w, n]), [x1, y1] = project([e, s])
      terrainImg = { href: `data:image/png;base64,${terrain.png_base64}`, x: x0, y: y0, w: x1 - x0, h: y1 - y0,
        tag: terrain.provenance?.tag || 'UNKNOWN' }
    }
    const spots = (hotspots?.features || [])
      .filter((f) => f.properties?.kind === 'centroid' && f.geometry?.type === 'Point')
      .map((f) => at(f.geometry.coordinates))
      .filter(inside)

    return {
      vb, half,
      revealR: half * 2,
      roads: { major: pathD(lines.major, project), mid: pathD(lines.mid, project), minor: pathD(lines.minor, project) },
      atRisk: pathD(scenario.atRisk.segIds.map(seg), project),
      atRiskAnchor: mid(seg(scenario.atRisk.segIds[0])),
      route: pathD([scenario.route.coords], project),
      routeAnchor: mid(scenario.route.coords),
      reroute: pathD([scenario.reroute.coords], project),
      rerouteAnchor: mid(scenario.reroute.coords),
      start: at(scenario.start.lonlat),
      end: at(scenario.end.lonlat),
      closure: at(scenario.closure.lonlat),
      risks: scenario.riskAreas.map((r) => ({ ...r, p: at(r.center) })),
      terrain: terrainImg,
      spots,
    }
  }, [scenario, terrain, hotspots])
}

function routeLengthUnits(d) {
  // length of an "M x y L x y ..." path in SVG units (metres)
  let s = 0, prev = null
  for (const m of d.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)) {
    const q = [Number(m[1]), Number(m[2])]
    if (prev && m[0][0] === 'L') s += Math.hypot(q[0] - prev[0], q[1] - prev[1])
    prev = q
  }
  return s || 1
}

// Caption extras that name the computed things (road names, lengths) -- still DEMO inputs.
function stageDetail(id, sc) {
  if (!sc) return ''
  switch (id) {
    case 'roads': return `${sc.segmentCount.toLocaleString('en-IN')} segments, ${sc.roadsTag === 'REAL' ? 'from OpenStreetMap' : 'bundled demo geometry (backend unreachable)'}.`
    case 'atRisk': return `${sc.atRisk.name} is inside the risk area.`
    case 'route': return `${fmtKm(sc.route.lengthM)} from the response base to the incident.`
    case 'reroute': return `${sc.closure.reason}. New route: ${fmtKm(sc.reroute.lengthM)}.`
    default: return ''
  }
}

function stageTags(id, sc, geo) {
  if (id === 'roads') return [sc?.roadsTag || 'DEMO']
  if (id === 'layers') return [geo?.terrain ? `${geo.terrain.tag} terrain` : null, 'DEMO rain'].filter(Boolean)
  if (id === 'settled') return []
  return ['DEMO']
}

const tagClass = (tag) => (String(tag).startsWith('REAL') ? 'tag-REAL' : String(tag).startsWith('DEMO') ? 'tag-DEMONSTRATION' : 'tag-UNKNOWN')
const round3 = (v) => String(Math.round(v * 1000) / 1000)

export default function LandingStory() {
  const { roads, terrain, hotspots, bootLoading } = useFloodNet()
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const id = (name) => `${name}-${uid}`

  // ---------------------------------------------------------------- data readiness
  const [waitedOut, setWaitedOut] = useState(false)
  useEffect(() => {
    if (roads || !bootLoading) return undefined
    const t = setTimeout(() => setWaitedOut(true), FALLBACK_AFTER_MS)
    return () => clearTimeout(t)
  }, [roads, bootLoading])
  const ready = Boolean(roads) || !bootLoading || waitedOut
  const scenario = useMemo(() => (ready ? buildScenario(roads) : null), [ready, roads])
  const geo = useGeometry(scenario, terrain, hotspots)
  const routeLen = useMemo(() => (geo ? routeLengthUnits(geo.route) : 1), [geo])

  // ---------------------------------------------------------------- playback state (ref; React sees stage changes)
  const [reduced, setReduced] = useState(mediaReduced)
  const [initial] = useState(() => initialPlayback(mediaReduced()))
  const pbRef = useRef(initial)
  const [ui, setUi] = useState(() => ({ stage: stageIndexAt(initial.t), playing: initial.playing }))
  const [inView, setInView] = useState(true)
  const [pageVisible, setPageVisible] = useState(() => typeof document === 'undefined' || !document.hidden)
  const [frame, setFrame] = useState({ w: 0, h: 0, reserve: 0, top: 0, bottom: 0 })
  const [tip, setTip] = useState(null)

  // frame-shaped viewBox shared by every layer; u = SVG units per CSS pixel (keeps strokes a fixed pixel width)
  const view = useMemo(() => (geo ? fitView(geo.vb, frame.w, frame.h, frame.reserve, { top: frame.top, bottom: frame.bottom }) : null), [geo, frame])
  const u = view?.u ?? 4

  const rootRef = useRef(null)
  const canvasRef = useRef(null)
  const cardsRef = useRef(null)
  const chipsRef = useRef(null)
  const sourceRef = useRef(null)
  const svgRef = useRef(null)
  const el = useRef({})
  const live = useRef({ geo, u, routeLen })
  const reducedRef = useRef(reduced)
  const bind = (key) => (node) => { el.current[key] = node }

  // Write the visual state for time t straight to the DOM. Only values that changed are written: re-setting an
  // SVG attribute to the same value still invalidates its layer, and the road layer must not repaint every frame.
  const apply = useCallback((t) => {
    const { geo: g, u: k, routeLen: len } = live.current
    const q = el.current
    if (!g) return
    const p = layerProgress(t)
    const put = (node, kind, name, value) => {
      if (!node) return
      const memo = node.__landingMemo || (node.__landingMemo = {})
      if (memo[kind + name] === value) return
      memo[kind + name] = value
      if (kind === 'attr') node.setAttribute(name, value)
      else node.style.setProperty(name, value)
    }
    const op = (node, v) => put(node, 'style', 'opacity', round3(v))

    put(q.clip, 'attr', 'r', String(Math.max(0.01, Math.round(p.roads * g.revealR))))
    op(q.terrain, 0.42 * p.terrain)
    op(q.spots, p.spots)
    op(q.rain, 0.7 * Math.sin(Math.PI * p.rain))
    put(q.rain, 'style', 'transform', `translate3d(${Math.round(-100 + 290 * p.rain)}%, 0, 0)`)
    op(q.risk, p.risk)
    if (q.risk) {
      const grow = 0.35 + 0.65 * p.risk
      for (const c of q.risk.querySelectorAll('circle[data-r]')) put(c, 'attr', 'r', String(Math.round(Number(c.dataset.r) * grow)))
    }
    op(q.atRisk, p.atRisk > 0 ? 1 : 0)
    put(q.atRisk, 'style', '--dash', round3(1 - p.atRisk))

    // candidate route: drawn, then dimmed and dashed once the closure forces a reroute
    const old = p.oldRoute > 0
    put(q.route, 'attr', 'data-state', old ? 'old' : 'draw')
    op(q.route, p.route > 0 ? 1 - 0.15 * p.oldRoute : 0)
    put(q.route, 'style', '--dasharray', old ? `${round3((7 * k) / len)} ${round3((6 * k) / len)}` : '1 1')
    put(q.route, 'style', '--dash', old ? '0' : round3(1 - p.route))
    op(q.markers, Math.min(1, p.route * 4))
    op(q.closure, p.closure)
    put(q.closureScale, 'attr', 'transform', `scale(${round3((0.6 + 0.4 * p.closure) * k)})`)
    op(q.reroute, p.reroute > 0 ? 1 : 0)
    put(q.reroute, 'style', '--dash', round3(1 - p.reroute))
    for (const [key, v] of [['cardA', p.assignment], ['cardB', p.alert]]) {
      op(q[key], v)
      put(q[key], 'style', 'transform', `translateY(${Math.round((1 - v) * 14)}px)`)
      put(q[key], 'style', 'visibility', v > 0 ? 'visible' : 'hidden')
    }
    put(q.bar, 'style', 'transform', `scaleX(${round3(t / TOTAL_MS)})`)
  }, [])

  const dispatch = useCallback((action) => {
    const next = playbackReducer(pbRef.current, { ...action, reducedMotion: reducedRef.current })
    pbRef.current = next
    apply(next.t)
    const stage = stageIndexAt(next.t)
    setUi((prev) => (prev.stage === stage && prev.playing === next.playing ? prev : { stage, playing: next.playing }))
  }, [apply])

  // keep the loop's inputs current and repaint when the geometry or the scale changes
  useLayoutEffect(() => {
    live.current = { geo, u, routeLen }
    apply(pbRef.current.t)
  }, [geo, u, routeLen, apply])

  // decode the terrain image off the main thread before it fades in (avoids a one-frame hitch)
  const terrainHref = geo?.terrain?.href
  useEffect(() => {
    if (!terrainHref || typeof Image !== 'function') return
    const img = new Image()
    img.src = terrainHref
    img.decode?.().catch(() => { /* decoded on first paint instead */ })
  }, [terrainHref])

  // reduced-motion preference (it can change while the page is open)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => {
      reducedRef.current = mq.matches
      setReduced(mq.matches)
      if (mq.matches) dispatch({ type: 'replay' })
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [dispatch])

  // pause off screen and in a hidden tab
  useEffect(() => {
    const onVis = () => setPageVisible(!document.hidden)
    document.addEventListener('visibilitychange', onVis)
    let io = null
    if (typeof IntersectionObserver === 'function' && rootRef.current) {
      io = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.15 })
      io.observe(rootRef.current)
    }
    return () => { document.removeEventListener('visibilitychange', onVis); io?.disconnect() }
  }, [])

  // frame size, and the strip the cards cover when they float over the map
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || typeof ResizeObserver !== 'function') return undefined
    const measure = () => {
      const { width, height } = canvas.getBoundingClientRect()
      const cards = cardsRef.current
      const floating = cards && getComputedStyle(cards).position === 'absolute'
      const reserve = floating ? cards.getBoundingClientRect().width + 24 : 0
      // keep the DEMO chip (top) and the source line (bottom) off the scene
      const top = chipsRef.current ? chipsRef.current.getBoundingClientRect().height + 20 : 0
      const bottom = sourceRef.current ? sourceRef.current.getBoundingClientRect().height + 20 : 0
      setFrame((f) => (f.w === width && f.h === height && f.reserve === reserve && f.top === top && f.bottom === bottom
        ? f : { w: width, h: height, reserve, top, bottom }))
    }
    const ro = new ResizeObserver(measure)
    ro.observe(canvas)
    if (sourceRef.current) ro.observe(sourceRef.current)   // the source line grows when the terrain tag arrives
    return () => ro.disconnect()
  }, [geo])

  // the animation loop
  const animate = shouldAnimate({ playing: ui.playing, inView: inView && Boolean(geo), pageVisible, reducedMotion: reduced })
  useEffect(() => {
    if (!animate) return undefined
    let raf = 0, last = performance.now()
    const tick = (now) => {
      const dt = Math.min(64, now - last)   // a long pause (tab switch, jank) never skips a stage
      last = now
      dispatch({ type: 'tick', dt })
      if (pbRef.current.playing) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [animate, dispatch])

  // ---------------------------------------------------------------- hover / focus labels
  const showTip = (target, clientX, clientY) => {
    const node = target?.closest?.('[data-tip]')
    const canvas = canvasRef.current, svg = svgRef.current
    if (!node || !canvas || !svg) { setTip(null); return }
    const box = canvas.getBoundingClientRect()
    let x = clientX, y = clientY
    if (x == null) {
      const m = svg.getScreenCTM()
      if (!m) return
      const pt = svg.createSVGPoint()
      pt.x = Number(node.dataset.ax); pt.y = Number(node.dataset.ay)
      const s = pt.matrixTransform(m)
      x = s.x; y = s.y
    }
    const text = node.dataset.tip
    setTip((prev) => (prev && prev.text === text && Math.abs(prev.x - (x - box.left)) < 2 && Math.abs(prev.y - (y - box.top)) < 2
      ? prev : { text, x: x - box.left, y: y - box.top, flip: x - box.left > box.width * 0.55 }))
  }

  const stage = STAGES[ui.stage]
  const reached = (stageId) => ui.stage >= STAGE_INDEX[stageId]
  const sc = scenario
  const focusable = (stageId) => ({ tabIndex: reached(stageId) && geo ? 0 : -1, 'aria-hidden': reached(stageId) ? undefined : true })

  const tips = sc ? {
    route: reached('reroute') ? `Earlier route · blocked by the DEMO closure on ${sc.closure.name}` : `Candidate rescue route · ${fmtKm(sc.route.lengthM)} · avoids at-risk roads (DEMO)`,
    reroute: `New route · ${fmtKm(sc.reroute.lengthM)} · avoids the closure (DEMO)`,
    atRisk: `At risk: ${sc.atRisk.name} (DEMO)`,
    closure: sc.closure.reason,
    start: sc.start.label,
    end: sc.end.label,
    risk: 'Flood-risk area (DEMO) · hatched edge = uncertain extent',
  } : {}

  const vbAttr = view ? view.vb.map((v) => Math.round(v * 10) / 10).join(' ') : undefined
  const layerSvg = { viewBox: vbAttr, preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': true, focusable: 'false' }

  return (
    <figure ref={rootRef} className={styles.story} aria-labelledby={id('cap')} style={ROUTE_VARS}>
      <div className={styles.frame} data-ready={geo ? 'yes' : 'no'}>
        <div ref={canvasRef} className={styles.canvas} style={{ '--u': u }}>
          {!geo && <div className={styles.loading} role="status">Loading the pilot road network…</div>}

          {geo?.terrain && (
            <div ref={bind('terrain')} className={styles.layer}>
              <svg className={styles.svgLayer} {...layerSvg}>
                <defs>
                  {/* feathered edge so the terrain grid does not end in a hard rectangle (no blur filter: cheap) */}
                  <radialGradient id={id('feather')}>
                    <stop offset="0.6" stopColor="#fff" stopOpacity="1" />
                    <stop offset="1" stopColor="#fff" stopOpacity="0" />
                  </radialGradient>
                  <mask id={id('terrainMask')} maskUnits="userSpaceOnUse" x={geo.terrain.x} y={geo.terrain.y} width={geo.terrain.w} height={geo.terrain.h}>
                    <rect x={geo.terrain.x} y={geo.terrain.y} width={geo.terrain.w} height={geo.terrain.h} fill={`url(#${id('feather')})`} />
                  </mask>
                </defs>
                <image href={geo.terrain.href} x={geo.terrain.x} y={geo.terrain.y} width={geo.terrain.w} height={geo.terrain.h}
                  preserveAspectRatio="none" mask={`url(#${id('terrainMask')})`} />
              </svg>
            </div>
          )}

          {geo && (
            <div className={styles.layerStatic}>
              <svg className={`${styles.svgLayer} ${styles.roads}`} {...layerSvg}>
                <defs>
                  <clipPath id={id('reveal')}>
                    <circle ref={bind('clip')} cx={geo.start[0]} cy={geo.start[1]} r="0.01" />
                  </clipPath>
                </defs>
                <g clipPath={`url(#${id('reveal')})`}>
                  <path className={styles.roadMinor} d={geo.roads.minor} />
                  <path className={styles.roadMid} d={geo.roads.mid} />
                  <path className={styles.roadMajor} d={geo.roads.major} />
                </g>
              </svg>
            </div>
          )}

          {geo && <div ref={bind('rain')} className={styles.rainLayer} aria-hidden="true" />}

          {geo && (
            <svg
              ref={svgRef}
              className={`${styles.svgLayer} ${styles.overlay}`}
              viewBox={vbAttr}
              preserveAspectRatio="xMidYMid meet"
              role="group"
              aria-label="Map of the Hindmata / Dadar pilot area playing a DEMO response scenario"
              onPointerOver={(e) => showTip(e.target, e.clientX, e.clientY)}
              onPointerMove={(e) => { if (tip) showTip(e.target, e.clientX, e.clientY) }}
              onPointerLeave={() => setTip(null)}
              onFocus={(e) => showTip(e.target)}
              onBlur={() => setTip(null)}
            >
              <defs>
                <pattern id={id('hatch')} width={Math.round(8 * u)} height={Math.round(8 * u)} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                  <line x1="0" y1="0" x2="0" y2={Math.round(8 * u)} className={styles.hatchLine} />
                </pattern>
                <radialGradient id={id('water')}>
                  <stop offset="0%" className={styles.waterStop0} />
                  <stop offset="70%" className={styles.waterStop1} />
                  <stop offset="100%" className={styles.waterStop2} />
                </radialGradient>
              </defs>

              <g ref={bind('spots')} className={styles.spots}>
                {geo.spots.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={Math.round(5 * u)} className={styles.spot} />)}
              </g>

              <g ref={bind('risk')} className={styles.risk}>
                {geo.risks.map((r, i) => (
                  <g key={r.id} {...(i === 0 ? { 'data-tip': tips.risk, 'data-ax': r.p[0], 'data-ay': r.p[1] } : {})}>
                    <circle cx={r.p[0]} cy={r.p[1]} r={r.uncertainM} data-r={r.uncertainM} fill={`url(#${id('hatch')})`} className={styles.uncertain} />
                    <circle cx={r.p[0]} cy={r.p[1]} r={r.radiusM} data-r={r.radiusM} fill={`url(#${id('water')})`} className={styles.water} />
                  </g>
                ))}
              </g>

              <g ref={bind('atRisk')} className={styles.atRisk} data-tip={tips.atRisk} data-ax={geo.atRiskAnchor[0]} data-ay={geo.atRiskAnchor[1]}
                role="img" aria-label={tips.atRisk} {...focusable('atRisk')}>
                <path d={geo.atRisk} pathLength="1" className={styles.atRiskCasing} />
                <path d={geo.atRisk} pathLength="1" className={styles.atRiskCore} />
                <path d={geo.atRisk} className={styles.hit} />
              </g>

              <g ref={bind('route')} className={styles.route} data-state="draw" data-tip={tips.route}
                data-ax={geo.routeAnchor[0]} data-ay={geo.routeAnchor[1]} role="img" aria-label={tips.route} {...focusable('route')}>
                <path d={geo.route} pathLength="1" className={styles.halo} />
                <path d={geo.route} pathLength="1" className={styles.casing} />
                <path d={geo.route} pathLength="1" className={styles.core} />
                <path d={geo.route} className={styles.hit} />
              </g>

              <g ref={bind('reroute')} className={styles.route} data-state="draw" data-tip={tips.reroute}
                data-ax={geo.rerouteAnchor[0]} data-ay={geo.rerouteAnchor[1]} role="img" aria-label={tips.reroute} {...focusable('reroute')}>
                <path d={geo.reroute} pathLength="1" className={styles.halo} />
                <path d={geo.reroute} pathLength="1" className={styles.casing} />
                <path d={geo.reroute} pathLength="1" className={styles.core} />
                <path d={geo.reroute} className={styles.hit} />
              </g>

              <g ref={bind('closure')} className={styles.closure} transform={`translate(${geo.closure[0]} ${geo.closure[1]})`}
                data-tip={tips.closure} data-ax={geo.closure[0]} data-ay={geo.closure[1]} role="img" aria-label={tips.closure} {...focusable('reroute')}>
                <g ref={bind('closureScale')}>
                  <circle r="10" className={styles.closureDisc} />
                  <path d="M-4.5 -4.5L4.5 4.5M4.5 -4.5L-4.5 4.5" className={styles.closureX} />
                </g>
              </g>

              <g ref={bind('markers')} className={styles.markers}>
                <g transform={`translate(${geo.start[0]} ${geo.start[1]})`} data-tip={tips.start} data-ax={geo.start[0]} data-ay={geo.start[1]}
                  role="img" aria-label={tips.start} {...focusable('route')}>
                  <g transform={`scale(${round3(u)})`}>
                    <circle r="9" className={styles.startDot} />
                    <circle r="3" className={styles.startCore} />
                  </g>
                </g>
                <g transform={`translate(${geo.end[0]} ${geo.end[1]})`} data-tip={tips.end} data-ax={geo.end[0]} data-ay={geo.end[1]}
                  role="img" aria-label={tips.end} {...focusable('route')}>
                  <g transform={`scale(${round3(u)})`}>
                    <path d="M0 -11L11 0L0 11L-11 0Z" className={styles.endMark} />
                    <path d="M0 -4.5V1.5M0 4.2V4.6" className={styles.endBang} />
                  </g>
                </g>
              </g>
            </svg>
          )}

          {/* labels that must always be visible */}
          <div ref={chipsRef} className={styles.chips}>
            <span className={`tag-badge tag-DEMONSTRATION ${styles.demoChip}`}>DEMO scenario — not live data</span>
          </div>
          {sc && (
            <p ref={sourceRef} className={styles.sourceLine}>
              Roads: {sc.roadsTag === 'REAL' ? 'OpenStreetMap' : 'bundled demo geometry'}{' '}
              <span className={`tag-badge ${tagClass(sc.roadsTag)}`}>{sc.roadsTag}</span>
              {geo?.terrain && <> · Terrain: MCGM contours <span className={`tag-badge ${tagClass(geo.terrain.tag)}`}>{geo.terrain.tag}</span></>}
            </p>
          )}

          {tip && (
            <div className={`${styles.tip} ${tip.flip ? styles.tipFlip : ''}`} style={{ left: tip.x, top: tip.y }} role="tooltip">{tip.text}</div>
          )}
        </div>

        {sc && (
          <div ref={cardsRef} className={styles.cards}>
            <article ref={bind('cardA')} className={styles.card} aria-label="Response assignment (DEMO)">
              <header className={styles.cardHead}>
                <span className={styles.cardKicker}>Assignment</span>
                <span className="tag-badge tag-DEMONSTRATION">DEMO</span>
              </header>
              <p className={styles.cardTitle}>{sc.assignment.team} → {sc.assignment.destination}</p>
              <p className={styles.cardBody}>New route {sc.assignment.routeText}; {sc.assignment.avoids}.</p>
              <p className={styles.cardStatus}><span className={styles.statusDot} aria-hidden="true" />{sc.assignment.status}</p>
            </article>
            <article ref={bind('cardB')} className={`${styles.card} ${styles.sms}`} aria-label="Public alert preview (DEMO, not sent)">
              <header className={styles.cardHead}>
                <span className={styles.cardKicker}>{sc.alert.channel} preview · not sent</span>
                <span className="tag-badge tag-DEMONSTRATION">DEMO</span>
              </header>
              <p className={styles.bubble}>{sc.alert.text}</p>
              <p className={styles.smsLink}>
                <span className={styles.linkGlyph} aria-hidden="true">↗</span>
                <span>{sc.alert.link}</span>
                <span className={styles.linkNote}>public alert page (concept) · sent only after human review</span>
              </p>
            </article>
          </div>
        )}
      </div>

      {/* controls + steps */}
      <div className={styles.controls}>
        <div className={styles.transport}>
          {!reduced ? (
            <>
              <button type="button" className={styles.ctl} onClick={() => dispatch({ type: 'toggle' })} disabled={!geo}
                aria-label={ui.playing ? 'Pause the scenario' : 'Play the scenario'}>
                {ui.playing
                  ? <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M4 3h3v10H4zM9 3h3v10H9z" fill="currentColor" /></svg>
                  : <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M4 2.5v11l9-5.5z" fill="currentColor" /></svg>}
                <span>{ui.playing ? 'Pause' : 'Play'}</span>
              </button>
              <button type="button" className={styles.ctl} onClick={() => dispatch({ type: 'replay' })} disabled={!geo} aria-label="Replay the scenario from the start">
                <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M3.5 8a4.5 4.5 0 1 0 1.4-3.3M3.5 2.5v2.8h2.8" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
                <span>Replay</span>
              </button>
            </>
          ) : (
            <span className={styles.reducedNote}>Reduced motion: showing the final state. Pick a step to see it.</span>
          )}
          {!reduced && <div className={styles.track} aria-hidden="true"><div ref={bind('bar')} className={styles.trackFill} /></div>}
        </div>

        <ol className={styles.steps} aria-label="Scenario steps">
          {STAGES.map((s, i) => (
            <li key={s.id}>
              <button
                type="button"
                className={`${styles.step} ${i === ui.stage ? styles.stepActive : ''} ${i < ui.stage ? styles.stepDone : ''}`}
                aria-current={i === ui.stage ? 'step' : undefined}
                onClick={() => dispatch({ type: 'seek', stage: i })}
                disabled={!geo}
              >
                <span className={styles.stepNum}>{i + 1}</span>
                {s.label}
              </button>
            </li>
          ))}
        </ol>

        <figcaption id={id('cap')} className={styles.caption} aria-live={ui.playing ? 'off' : 'polite'}>
          <span className={styles.captionTitle}>
            {stage.title}
            {stageTags(stage.id, sc, geo).map((t) => <span key={t} className={`tag-badge ${tagClass(t)}`}>{t}</span>)}
          </span>
          <span className={styles.captionText}>{stage.caption} {stageDetail(stage.id, sc)}</span>
        </figcaption>

        <ul className={styles.legend} aria-label="Map legend">
          <li><svg viewBox="0 0 24 10" aria-hidden="true"><path d="M1 5H23" className={styles.lgRoad} /></svg>Road <span className={`tag-badge ${tagClass(sc?.roadsTag || 'DEMO')}`}>{sc?.roadsTag || '…'}</span></li>
          {geo?.terrain && <li><span className={styles.lgTerrain} aria-hidden="true" />Terrain, low → high <span className={`tag-badge ${tagClass(geo.terrain.tag)}`}>{geo.terrain.tag}</span></li>}
          {geo?.spots.length > 0 && <li><svg viewBox="0 0 24 10" aria-hidden="true"><circle cx="12" cy="5" r="4" className={styles.lgSpot} /></svg>Recorded flooding spot (MCGM, ~2017)</li>}
          <li><span className={styles.lgWater} aria-hidden="true" />Flood-risk area</li>
          <li><span className={styles.lgHatch} aria-hidden="true" />Uncertain extent</li>
          <li><svg viewBox="0 0 24 10" aria-hidden="true"><path d="M1 5H23" className={styles.lgAtRisk} /></svg>At-risk road</li>
          <li><svg viewBox="0 0 24 12" aria-hidden="true"><circle cx="12" cy="6" r="5" className={styles.lgClosureDisc} /><path d="M9.8 3.8L14.2 8.2M14.2 3.8L9.8 8.2" className={styles.lgClosureX} /></svg>Closure</li>
          <li><svg viewBox="0 0 24 10" aria-hidden="true"><path d="M1 5H23" className={styles.lgHalo} /><path d="M1 5H23" className={styles.lgCasing} /><path d="M1 5H23" className={styles.lgCore} /></svg>Rescue route</li>
          <li><svg viewBox="0 0 24 10" aria-hidden="true"><path d="M1 5H23" className={styles.lgOld} /></svg>Earlier route</li>
          <li className={styles.lgNote}><span className="tag-badge tag-DEMONSTRATION">DEMO</span> Scripted, not a forecast.</li>
        </ul>
      </div>
    </figure>
  )
}
