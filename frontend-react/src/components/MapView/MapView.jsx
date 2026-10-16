import { useEffect, useMemo, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { bboxToLatLngBounds, lonLatToLatLng } from '../../lib/format.js'
import { DEFAULT_BANDS_CM, SEVERITY_COLOR, SEVERITY_LABEL, severityColor, severityOf } from '../../lib/severity.js'
import styles from './MapView.module.css'
import { ACCESS_STATUS, CURRENT_STATUS_ROUTE_COLOR, activeClosures, unconfirmedClosures } from '../../lib/roadAccess.js'
import { PRIORITY_COLOR, STATUS_COLOR, STATUS_LABEL } from '../../lib/response.js'
import { MISSION_LEGEND, nextSegment, positionText, rescueLayerVisibility, ROUTE_STYLE, TRAFFIC_LEVELS, trafficLines, tripTitle } from '../../lib/rescueTrip.js'
import { resolvePageKey } from '../../lib/pages.js'

const PILOT_CENTER = [19.02, 72.845]

// Colours for POST /api/route/alternatives candidate layers, keyed by the candidate's primary objective label
// (see RoutePlanner.jsx's OBJECTIVE_LABEL for the matching honest UI text -- "fastest" here is DISTANCE-only).
const OBJECTIVE_COLOR = { safest: '#22c55e', fastest: '#f59e0b', balanced: '#8b5cf6' }
const DEFAULT_CANDIDATE_COLOR = '#64748b'

/** The selected route (D-49): white halo, navy outline, blue core, so it reads over flood, status and water colours.
 * `lines` is a GeoJSON geometry or an array of [lat, lng] arrays (one per road). */
function addHighlighted(g, lines, tooltip) {
  const layers = ['halo', 'outline', 'core'].map((k) => {
    const st = { ...ROUTE_STYLE[k], lineCap: 'round', lineJoin: 'round', interactive: k === 'core' }
    return Array.isArray(lines) ? L.polyline(lines, st).addTo(g) : L.geoJSON(lines, { style: st, interactive: k === 'core' }).addTo(g)
  })
  if (tooltip) layers[2].bindTooltip(tooltip, { sticky: true })
  return layers[2]
}

// Route legend rows (map overlay): [key, label, svg content]
const ROUTE_LEGEND = [
  { key: 'sel', label: 'Selected route', svg: <>
    <line x1="2" y1="5" x2="24" y2="5" stroke={ROUTE_STYLE.halo.color} strokeWidth="9" strokeLinecap="round" />
    <line x1="2" y1="5" x2="24" y2="5" stroke={ROUTE_STYLE.outline.color} strokeWidth="6" strokeLinecap="round" />
    <line x1="2" y1="5" x2="24" y2="5" stroke={ROUTE_STYLE.core.color} strokeWidth="3" strokeLinecap="round" /></> },
  { key: 'next', label: 'Next road (active trip)', svg: <>
    <line x1="2" y1="5" x2="24" y2="5" stroke={ROUTE_STYLE.outline.color} strokeWidth="6" />
    <line x1="2" y1="5" x2="24" y2="5" stroke="#FFFFFF" strokeWidth="2" strokeDasharray="1 4" /></> },
  { key: 'alt', label: 'Not chosen', svg: <line x1="2" y1="5" x2="24" y2="5" stroke={ROUTE_STYLE.alternative.color} strokeWidth="2.5" strokeDasharray="5 4" /> },
  { key: 'risk', label: 'At risk on arrival', svg: <line x1="2" y1="5" x2="24" y2="5" stroke={ACCESS_STATUS.AT_RISK.color} strokeWidth="3.5" /> },
  { key: 'unc', label: 'Uncertain / unknown', svg: <line x1="2" y1="5" x2="24" y2="5" stroke={ACCESS_STATUS.UNCERTAIN.color} strokeWidth="3.5" strokeDasharray="4 3" /> },
  { key: 'closed', label: 'Confirmed closure', svg: <>
    <line x1="2" y1="5" x2="24" y2="5" stroke={ACCESS_STATUS.CLOSED.color} strokeWidth="5" />
    <line x1="2" y1="5" x2="24" y2="5" stroke="#FFFFFF" strokeWidth="1.5" strokeDasharray="3 3" /></> },
  { key: 'veh', label: 'Vehicle (hollow: GPS stale)', svg: <circle cx="13" cy="5" r="4" fill="#7A2231" stroke="#7A2231" /> },
]

// ── drainage node state (three tiers, straight from the solver) ────────────────────────────────────────
// Each per-frame node dict carries `state`, exactly one of 'normal' | 'at_capacity' | 'surcharging':
//   surcharging  -- the node spilled water to the street during this frame interval (a real problem).
//   at_capacity  -- fill_frac >= the solver's own internal full threshold: water has reached street level
//                   and is blocking upstream flow, but it did NOT spill during the interval.
//   normal       -- everything else.
// `fill_frac` / `freeboard_m` are INSTANTANEOUS at the frame boundary, while `state` / `surcharging` are
// INTERVAL-aware, so a node that spilled early in the interval and then drained legitimately reports
// state 'surcharging' with a fill_frac well below 1.0. Every tooltip below therefore times its fill
// number explicitly ("fill at frame end") so the two can never read as a contradiction.
function nodeStateOf(f) {
  if (!f) return null
  if (f.state === 'normal' || f.state === 'at_capacity' || f.state === 'surcharging') return f.state
  return f.surcharging ? 'surcharging' : 'normal' // defensive fallback for a pre-`state` payload
}

// Continuous fill encoding on NORMAL nodes only: radius and fill-opacity vary smoothly with fill_frac
// while the HUE IS HELD at the baseline blue. Deliberately NOT banded -- node depths in this network run
// from 0.57 m to 4.47 m, so any fixed fill-fraction cut (0.85, say) would mean a wildly different real
// freeboard from node to node, i.e. the UI would be asserting a threshold the physics does not define.
const NODE_STYLE_STATIC = { radius: 2.5, color: '#0284c7', weight: 1, fillColor: '#0284c7', fillOpacity: 0.6 }

function nodeStyleFor(f) {
  const state = nodeStateOf(f)
  if (state === 'surcharging') {
    return { radius: 6, color: '#dc2626', weight: 2, fillColor: '#ef4444', fillOpacity: 0.8 }
  }
  if (state === 'at_capacity') {
    // Amber/orange: clearly between calm blue and alarm red, and no pulse -- the pulse is reserved for
    // nodes that are actually spilling.
    return { radius: 4.5, color: '#c2410c', weight: 2, fillColor: '#f97316', fillOpacity: 0.85 }
  }
  if (!f) return NODE_STYLE_STATIC
  const fill = Math.max(0, Math.min(1, f.fill_frac ?? 0))
  return { radius: 2.2 + 1.8 * fill, color: '#0284c7', weight: 1, fillColor: '#0284c7', fillOpacity: 0.32 + 0.5 * fill }
}

const NODE_STATE_TEXT = {
  normal: 'NORMAL',
  at_capacity: 'AT CAPACITY &mdash; water at street level, no spill this interval',
  surcharging: 'SURCHARGING &mdash; spilled to street during this interval',
}

// `n` is the static topology node (ground/invert), `f` the per-frame state (undefined before the first frame).
function nodeTooltip(n, f) {
  const head = `<b>${n.is_outfall ? 'Outfall' : 'Node'} ${n.id}</b>`
  const geom = `ground ${(n.ground_m ?? 0).toFixed(2)} m &middot; invert ${(n.invert_m ?? 0).toFixed(2)} m`
  const state = nodeStateOf(f)
  if (!state) return `${head}<br>${geom}`

  const rows = [NODE_STATE_TEXT[state]]
  if (f.freeboard_m != null) {
    rows.push(
      f.freeboard_m <= 0
        ? 'water at street level (0.00 m freeboard)'
        : `water ${f.freeboard_m.toFixed(2)} m below street level`,
    )
  }
  if (f.fill_frac != null) rows.push(`fill at frame end ${Math.round(Math.max(0, Math.min(1, f.fill_frac)) * 100)}%`)
  if (f.surcharge_m3 > 0) rows.push(`spilled ${f.surcharge_m3.toFixed(1)} m&sup3; this interval`)
  if (f.cause) rows.push(`cause: ${f.cause}`)
  if (f.hgl_m != null) rows.push(`HGL ${f.hgl_m.toFixed(2)} m`)
  return `${head}<br>${geom}<br>${rows.join('<br>')}`
}

// Applied both from the per-frame effect and once right after the static geometry is (re)built, so the
// network is never left showing state-less geometry when topology happens to resolve after the first frame.
function applyNodeFrameStyles(index, frame) {
  const states = new Map((frame?.nodes || []).map((n) => [String(n.id), n]))
  index.forEach((entry, id) => {
    const f = states.get(id)
    entry.layer.setTooltipContent(nodeTooltip(entry.node, f))
    // Outfalls keep their distinct green-rectangle treatment -- they are the network's boundary condition,
    // not a manhole that can surcharge -- so only their tooltip is refreshed.
    if (entry.isOutfall) return
    entry.layer.setStyle(nodeStyleFor(f))
  })
}

// Recolour the drainage edges by utilisation.
// `edge_util` is structurally bounded at 1.0 by the solver (Q = cap_eff * min(1, sqrt(head/drop)), only ever
// reduced afterwards) -- measured max across every frame of two full scenarios was exactly 1.0000 -- so
// there is no >1 case to render and no clamp above 1 to apply. Crucially, util == 1.0 means the conduit is
// flowing at its FULL DESIGN CAPACITY, which is the normal design condition for a storm drain under load,
// NOT a failure. The ramp therefore runs calm teal (idle) -> saturated indigo (full bore): still an obvious
// gradient, but it never reads as alarm and never competes with the genuinely red surcharging nodes, which
// are the actual problem on this map.
function applyEdgeFrameStyles(index, frame) {
  if (!frame?.edges?.length) return
  const util = new Map(frame.edges.map((e) => [e.id, e.util || 0]))
  index.forEach((pl, id) => {
    const u = Math.max(0, Math.min(1, util.get(id) ?? 0))
    pl.setStyle({ color: `hsl(${178 + 54 * u},${45 + 35 * u}%,${62 - 24 * u}%)`, opacity: 0.55 + 0.35 * u })
  })
}

// Internal Leaflet layer groups.
const GROUP_KEYS = ['roads', 'drainageEdges', 'drainageNodes', 'drainageSurcharge', 'hotspots', 'terrain', 'depth', 'streets', 'route', 'access', 'response', 'trips']
const VISIBILITY_FOR = (layers, page) => ({
  roads: layers.roads,
  drainageEdges: layers.drainage,
  drainageNodes: layers.drainage,
  drainageSurcharge: layers.drainage,
  hotspots: layers.hotspots,
  terrain: layers.terrain,
  depth: layers.depth,
  streets: layers.streets,
  route: layers.route,
  access: layers.route,
  // rescue missions and trips: hidden until a rescue view is chosen (rescueTrip.js rescueLayerVisibility)
  response: rescueLayerVisibility(page, layers).missions,
  trips: rescueLayerVisibility(page, layers).trips,
})

export default function MapView() {
  const elRef = useRef(null)
  const mapRef = useRef(null)
  const groupsRef = useRef({})
  const svgRendererRef = useRef(null)
  const roadIndexRef = useRef(new Map())
  const edgeIndexRef = useRef(new Map())
  // id -> { layer, node, isOutfall }. Mirrors edgeIndexRef: markers are created ONCE with the static
  // topology (creating 1200+ of them is expensive) and then re-styled in place every frame.
  const nodeIndexRef = useRef(new Map())
  const segIndexRef = useRef(new Map())
  const routeMarkersRef = useRef([])
  const boundsFittedRef = useRef(false)
  const fittedRouteKeyRef = useRef(null)

  const {
    meta, roads, topology, hotspots, terrain,
    frame, selectedSegId, selectSegment,
    route, pickPoint, alternatives, alternativesStale, selectAlternative,
    access, roadEvents, closeRoadMode, currentT,
    response, responseResources, ops, tripPreview, selectedTripId, activeRightTab, navigation, navFix,
    layers, terrainOpacity, depthOpacity,
    mapCommand, setMapView,
  } = useFloodNet()

  const page = resolvePageKey(activeRightTab)
  const rescueVis = rescueLayerVisibility(page, layers)
  // legend rows only for mission statuses actually drawn (CANCELLED and COMPLETED are not drawn)
  const missionRows = rescueVis.missions
    ? MISSION_LEGEND.filter((r) => (response.ledger || []).some((a) => a.status === r.status && a.legs?.some((l) => l.route)))
    : []
  const tripsShown = rescueVis.trips && (ops?.trips || []).some((t) => !['COMPLETED', 'ABORTED'].includes(t.stage))
  const pickPointRef = useRef(pickPoint)
  const selectSegmentRef = useRef(selectSegment)
  const selectedSegIdRef = useRef(selectedSegId)
  // Declared before the static-geometry effects, so on any commit this is already the current frame by the
  // time the topology effect below rebuilds the drainage network.
  const frameRef = useRef(frame)
  useEffect(() => {
    pickPointRef.current = pickPoint
    selectSegmentRef.current = selectSegment
    selectedSegIdRef.current = selectedSegId
    frameRef.current = frame
  })

  // ---------------------------------------------------------------- init (once)
  useEffect(() => {
    const map = L.map(elRef.current, { zoomControl: false, preferCanvas: true }).setView(PILOT_CENTER, 15)
    // shared with the 3D terrain view so it opens on the place the operator was looking at
    const publishView = () => { const c = map.getCenter(); setMapView({ lat: c.lat, lng: c.lng, zoom: map.getZoom() }) }
    map.on('moveend', publishView)
    publishView()
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map)

    svgRendererRef.current = L.svg()

    const groups = Object.fromEntries(GROUP_KEYS.map((k) => [k, L.layerGroup().addTo(map)]))
    groupsRef.current = groups
    mapRef.current = map

    map.on('click', (e) => pickPointRef.current([e.latlng.lng, e.latlng.lat]))

    // Panels slide in and out (D-47) without a window resize, so Leaflet is told when its container changes size,
    // at most once per animation frame: tiles fill the new space and nothing jumps.
    let frameReq = 0
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
      if (frameReq) return
      frameReq = window.requestAnimationFrame(() => { frameReq = 0; map.invalidateSize({ pan: false, animate: false }) })
    }) : null
    ro?.observe(elRef.current)

    return () => {
      ro?.disconnect()
      if (frameReq) window.cancelAnimationFrame(frameReq)
      map.remove()
      mapRef.current = null
    }
  }, [setMapView])

  // ---------------------------------------------------------------- layer visibility
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const vis = VISIBILITY_FOR(layers, page)
    for (const [key, on] of Object.entries(vis)) {
      const g = groupsRef.current[key]
      if (!g) continue
      const has = map.hasLayer(g)
      if (on && !has) g.addTo(map)
      if (!on && has) map.removeLayer(g)
    }
  }, [layers, page])

  // ---------------------------------------------------------------- pilot bbox + fit bounds (once)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !meta?.pilot?.bbox_lonlat || boundsFittedRef.current) return
    const bounds = bboxToLatLngBounds(meta.pilot.bbox_lonlat)
    map.fitBounds(bounds)
    L.rectangle(bounds, { color: '#94a3b8', weight: 1.5, dashArray: '4 5', fill: false, interactive: false }).addTo(map)
    if (Array.isArray(meta.attribution)) meta.attribution.forEach((a) => map.attributionControl.addAttribution(a))
    boundsFittedRef.current = true
  }, [meta])

  // ---------------------------------------------------------------- roads (static, neutral)
  useEffect(() => {
    const g = groupsRef.current.roads
    if (!g || !roads) return
    g.clearLayers()
    roadIndexRef.current.clear()
    for (const f of roads.features || []) {
      const pl = L.polyline((f.geometry.coordinates || []).map(lonLatToLatLng), {
        color: '#94a3b8',
        weight: 1.2,
        opacity: 0.55,
        interactive: false,
      })
      roadIndexRef.current.set(f.properties.seg_id, pl)
      pl.addTo(g)
    }
  }, [roads])

  // ---------------------------------------------------------------- drainage network (static geometry, restyled per-frame)
  useEffect(() => {
    const edgesG = groupsRef.current.drainageEdges
    const nodesG = groupsRef.current.drainageNodes
    if (!edgesG || !nodesG || !topology) return
    edgesG.clearLayers()
    nodesG.clearLayers()
    edgeIndexRef.current.clear()
    nodeIndexRef.current.clear()
    const caps = (topology.edges || []).map((e) => e.capacity_m3s || 0)
    const cmax = Math.max(1e-6, ...caps)
    for (const e of topology.edges || []) {
      const w = 1.0 + 3 * Math.sqrt((e.capacity_m3s || 0) / cmax)
      const pl = L.polyline((e.geom || []).map(lonLatToLatLng), { color: '#0d9488', weight: w, opacity: 0.75 })
      pl.bindTooltip(
        `<b>Drain ${e.id}</b><br>${e.shape || ''} ${(e.width_m ?? 0).toFixed(2)}&times;${(e.height_m ?? 0).toFixed(2)} m` +
          `<br>capacity ${(e.capacity_m3s ?? 0).toFixed(2)} m&sup3;/s &middot; status ${e.status || ''}` +
          (e.blockage ? `<br>blockage ${Math.round(e.blockage * 100)}%` : ''),
      )
      edgeIndexRef.current.set(e.id, pl)
      pl.addTo(edgesG)
    }
    for (const n of topology.nodes || []) {
      const m = n.is_outfall
        ? L.rectangle([[n.lat - 3e-5, n.lon - 3e-5], [n.lat + 3e-5, n.lon + 3e-5]], { color: '#059669', weight: 2, fillOpacity: 0.6 })
        : L.circleMarker([n.lat, n.lon], NODE_STYLE_STATIC)
      m.bindTooltip(nodeTooltip(n, null))
      // Outfalls keep their distinct green-rectangle treatment and are never re-styled by state (they are
      // the network's boundary condition, not a manhole that can surcharge); they are still indexed so the
      // per-frame effect can refresh their tooltip.
      nodeIndexRef.current.set(String(n.id), { layer: m, node: n, isOutfall: Boolean(n.is_outfall) })
      m.addTo(nodesG)
    }
    // If topology resolved after the first frame did, style it straight away rather than leaving the whole
    // network state-less until the user next scrubs the timeline.
    applyNodeFrameStyles(nodeIndexRef.current, frameRef.current)
    applyEdgeFrameStyles(edgeIndexRef.current, frameRef.current)
  }, [topology])

  // ---------------------------------------------------------------- hotspots (static)
  useEffect(() => {
    const g = groupsRef.current.hotspots
    if (!g || !hotspots) return
    g.clearLayers()
    for (const f of hotspots.features || []) {
      if (f.geometry?.type !== 'Point') continue
      const [lon, lat] = f.geometry.coordinates
      const p = f.properties || {}
      const active = p.active !== false
      const m = L.circleMarker([lat, lon], {
        radius: 6,
        color: '#d97706',
        weight: 2,
        fillColor: '#f59e0b',
        fillOpacity: active ? 0.8 : 0.2,
      })
      m.bindTooltip(
        `<b>${p.name || 'Flooding spot'}</b><br>ward ${p.ward || ''} &middot; ${p.affect_road || ''}` +
          `<br>${p.depth_attr || ''}${active ? '' : '<br><i>inactive</i>'}`,
      )
      m.addTo(g)
    }
  }, [hotspots])

  // ---------------------------------------------------------------- terrain DEM overlay (static)
  const terrainOverlayRef = useRef(null)
  useEffect(() => {
    const g = groupsRef.current.terrain
    if (!g || !terrain?.png_base64) return
    g.clearLayers()
    const overlay = L.imageOverlay('data:image/png;base64,' + terrain.png_base64, bboxToLatLngBounds(terrain.bbox_lonlat), {
      opacity: terrainOpacity ?? 0.35,
      interactive: false,
    }).addTo(g)
    terrainOverlayRef.current = overlay
  }, [terrain, terrainOpacity])

  useEffect(() => {
    if (terrainOverlayRef.current) {
      terrainOverlayRef.current.setOpacity(terrainOpacity ?? 0.35)
    }
  }, [terrainOpacity])

  // ---------------------------------------------------------------- per-frame: street flooding, depth grid, surcharge, edge util
  const depthOverlayRef = useRef(null)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !frame) return

    // streets: reuse existing polylines via diffing to eliminate 900+ polyline recreation churn
    const streetsG = groupsRef.current.streets
    const currentMap = segIndexRef.current
    const activeSegIds = new Set()
    const feats = frame.streets?.features || []

    for (const f of feats) {
      const p = f.properties || {}
      const d = p.depth_cm || 0
      if (d < 1) continue
      const segIdStr = String(p.seg_id)
      activeSegIds.add(segIdStr)

      const sev = p.severity || severityOf(d, DEFAULT_BANDS_CM)
      const isSelected = segIdStr === String(selectedSegIdRef.current)
      const color = isSelected ? '#1d1d1f' : severityColor(sev)
      const weight = isSelected ? 7 : 2.8 + Math.min(6, d / 10)
      const opacity = isSelected ? 1 : 0.95
      const tooltipContent = `<b>${p.name || p.seg_id}</b><br>${Math.round(d)} cm &middot; ${SEVERITY_LABEL[sev] || sev}` +
        `<br>car ${p.passable_car ? 'passable' : 'BLOCKED'} &middot; ambulance ${p.passable_ambulance ? 'passable' : 'BLOCKED'}`

      const existingPl = currentMap.get(segIdStr)
      if (existingPl) {
        existingPl.setStyle({ color, weight, opacity })
        existingPl.setTooltipContent(tooltipContent)
      } else {
        const pl = L.polyline((f.geometry.coordinates || []).map(lonLatToLatLng), {
          color,
          weight,
          opacity,
        })
        pl.bindTooltip(tooltipContent)
        pl.on('click', () => selectSegmentRef.current(p.seg_id))
        pl.on('mouseover', () => pl.setStyle({ weight: (isSelected ? 7 : 2.8 + Math.min(6, d / 10)) + 2 }))
        pl.on('mouseout', () => pl.setStyle({ weight: isSelected ? 7 : 2.8 + Math.min(6, d / 10) }))
        currentMap.set(segIdStr, pl)
        pl.addTo(streetsG)
      }
    }

    // Prune polylines that have receded to 0 depth
    for (const [segIdStr, pl] of currentMap.entries()) {
      if (!activeSegIds.has(segIdStr)) {
        streetsG.removeLayer(pl)
        currentMap.delete(segIdStr)
      }
    }

    // depth grid
    const depthG = groupsRef.current.depth
    depthG.clearLayers()
    depthOverlayRef.current = null
    if (frame.depth_grid?.png_base64) {
      const bbox = frame.depth_grid.bbox_lonlat || meta?.pilot?.bbox_lonlat
      if (bbox) {
        const overlay = L.imageOverlay('data:image/png;base64,' + frame.depth_grid.png_base64, bboxToLatLngBounds(bbox), {
          opacity: depthOpacity ?? 0.5,
          interactive: false,
        }).addTo(depthG)
        depthOverlayRef.current = overlay
      }
    }

    // Drainage nodes: re-style the persistent markers into the three solver tiers, so every node carries
    // its hydraulic state instead of only the surcharging minority (previously 1117 of 1233 nodes were
    // inert blue decoration that never changed across the whole run).
    applyNodeFrameStyles(nodeIndexRef.current, frame)

    // Pulse halo for spilling nodes only. Kept as a separate SVG-rendered, non-interactive ring because the
    // node markers themselves live on the canvas renderer (map is preferCanvas) where a CSS animation class
    // cannot apply; `fill:false` + `interactive:false` means it is purely the pulse, and hover still falls
    // through to the node marker below, which owns the single unified tooltip.
    const surchG = groupsRef.current.drainageSurcharge
    surchG.clearLayers()
    for (const n of frame.nodes || []) {
      if (nodeStateOf(n) !== 'surcharging') continue
      L.circleMarker([n.lat, n.lon], {
        radius: 6,
        color: '#dc2626',
        weight: 2,
        fill: false,
        className: 'node-pulse',
        interactive: false,
        renderer: svgRendererRef.current,
      }).addTo(surchG)
    }

    applyEdgeFrameStyles(edgeIndexRef.current, frame)
  }, [frame, meta, depthOpacity])

  useEffect(() => {
    if (depthOverlayRef.current) {
      depthOverlayRef.current.setOpacity(depthOpacity ?? 0.5)
    }
  }, [depthOpacity])

  // Re-highlight selected segment
  useEffect(() => {
    const map = mapRef.current
    let selectedLayer = null
    segIndexRef.current.forEach((pl, segId) => {
      const isSelected = String(segId) === String(selectedSegId)
      pl.setStyle({ color: isSelected ? '#1d1d1f' : pl.options.color, weight: isSelected ? 7 : pl.options.weight })
      if (isSelected) {
        pl.bringToFront()
        selectedLayer = pl
      }
    })
    if (map && selectedLayer) {
      try {
        map.fitBounds(selectedLayer.getBounds().pad(0.6), { maxZoom: 17 })
      } catch {
        /* ignore */
      }
    }
  }, [selectedSegId])

  // seg_id -> [[lat, lng], ...] from the loaded roads, to draw single roads of a route (next road, hazards)
  const segGeom = useMemo(() => {
    const m = new Map()
    for (const f of roads?.features || []) {
      const c = f.geometry?.type === 'LineString' ? f.geometry.coordinates : null
      if (c && f.properties?.seg_id != null) m.set(String(f.properties.seg_id), c.map(([x, y]) => [y, x]))
    }
    return m
  }, [roads])

  // ---------------------------------------------------------------- navigation mode: the device's own GPS fix
  // A blue dot with its accuracy circle, and the map follows it. The fix is the browser's; nothing is simulated.
  const navGroupRef = useRef(null)
  const navFittedRef = useRef(null)
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!navGroupRef.current) navGroupRef.current = L.layerGroup().addTo(map)
    const g = navGroupRef.current
    g.clearLayers()
    if (!navigation || !navFix) return
    const ll = [navFix.lat, navFix.lon]
    if (Number.isFinite(navFix.accuracy) && navFix.accuracy > 5) {
      L.circle(ll, { radius: navFix.accuracy, color: '#2F6BFF', weight: 1, fillColor: '#2F6BFF', fillOpacity: 0.12, interactive: false }).addTo(g)
    }
    L.circleMarker(ll, { radius: 9, color: '#FFFFFF', weight: 3, fillColor: '#2F6BFF', fillOpacity: 1 })
      .bindTooltip(`Your position (device GPS, ±${Math.round(navFix.accuracy || 0)} m)`).addTo(g)
    map.setView(ll, Math.max(map.getZoom(), 16), { animate: true, duration: 0.6 })
  }, [navigation, navFix])
  // entering navigation with no fix yet: show the whole remaining route once
  useEffect(() => {
    const map = mapRef.current
    if (!map || !navigation) { navFittedRef.current = null; return }
    if (navFittedRef.current === navigation.tripId) return
    const t = (ops?.trips || []).find((x) => x.trip_id === navigation.tripId)
    const pts = (t?.remaining || []).flatMap((leg) => leg.segments.map((sg) => segGeom.get(String(sg.seg_id))).filter(Boolean)).flat()
    if (!pts.length) return
    navFittedRef.current = navigation.tripId
    try { map.fitBounds(L.latLngBounds(pts).pad(0.25), { maxZoom: 17, animate: true }) } catch { /* ignore */ }
  }, [navigation, ops?.trips, segGeom])

  // ---------------------------------------------------------------- live rescue trips (D-41)
  // The remaining route of each active trip (dashed burgundy) and the last known vehicle position with its age. A
  // stale or lost GPS signal is drawn hollow and says so in the tooltip; SIMULATED positions are labelled.
  useEffect(() => {
    const g = groupsRef.current.trips
    if (!g) return
    g.clearLayers()
    const segLine = (id) => segGeom.get(String(id))
    for (const t of ops?.trips || []) {
      if (['COMPLETED', 'ABORTED'].includes(t.stage)) continue
      const selected = t.trip_id === selectedTripId
      // the selected trip: only what is still ahead from the last assessed position, highlighted (D-49)
      const ahead = selected ? (t.remaining || []).flatMap((leg) => leg.segments.map((sg) => segLine(sg.seg_id)).filter(Boolean)) : []
      if (selected && ahead.length) {
        // QA 2026-10-11 M5: an escalated trip has no feasible route, so its old one is not drawn as if it were good;
        // roads ahead that are not plainly usable keep their status colour on top
        if (t.escalation?.open) {
          L.polyline(ahead, { ...ROUTE_STYLE.alternative, color: ACCESS_STATUS.AT_RISK.color, opacity: 0.9 })
            .bindTooltip(`${tripTitle(t)} · ESCALATED: no feasible route (${t.escalation.reason})`, { sticky: true }).addTo(g)
        } else {
          addHighlighted(g, ahead, `${tripTitle(t)} · remaining route (v${t.route_version})`)
        }
        // current statuses from the last assessment (the legs keep the statuses from when they were planned)
        for (const sg of t.last_assessment?.hazards_ahead || (t.remaining || []).flatMap((leg) => leg.segments)) {
          const st = ACCESS_STATUS[sg.status]
          const ln = sg.status && sg.status !== 'USABLE' && segLine(sg.seg_id)
          if (st && ln) L.polyline(ln, { color: st.color, weight: 4, opacity: 1, dashArray: ['AT_RISK', 'CLOSED'].includes(sg.status) ? null : '4 5' })
            .bindTooltip(`${sg.name || sg.seg_id}: ${st.label}`, { sticky: true }).addTo(g)
        }
        const nx = nextSegment(t)
        const line = nx && segLine(nx.seg_id)
        if (line) L.polyline(line, { ...ROUTE_STYLE.next, interactive: false }).addTo(g)
        const last = ahead[ahead.length - 1]
        L.circleMarker(last[last.length - 1], { radius: 7, color: '#0B1F4D', weight: 3, fillColor: '#FFFFFF', fillOpacity: 1 })
          .bindTooltip(`Destination: ${t.names?.destination || t.names?.shelter || t.shelter_id || ''}${nx ? ` · next road: ${nx.name || nx.seg_id}` : ''}`).addTo(g)
      } else {
        for (const leg of t.legs || []) {
          const coords = leg.route?.coordinates
          if (coords?.length > 1) {
            L.polyline(coords.map(([x, y]) => [y, x]), { color: '#7A2231', weight: 3, opacity: 0.7, dashArray: '8 6', interactive: false }).addTo(g)
          }
        }
      }
      const p = t.position
      if (p) {
        const fresh = p.freshness === 'FRESH'
        L.circleMarker([p.lat, p.lon], { radius: selected ? 10 : 8, color: '#7A2231', weight: 3, fillColor: fresh ? '#7A2231' : '#FFFFFF', fillOpacity: 1 })
          .bindTooltip(`${t.trip_id} ${t.names?.vehicle || t.vehicle_id || t.vehicle_type}: ${positionText(p)}`)
          .addTo(g)
      }
    }
  }, [ops?.trips, selectedTripId, segGeom])

  // ---------------------------------------------------------------- route
  // Two candidate-route layer patterns share the `route` layer group: the original single-route
  // baseline(dashed grey)+route(solid green) pair, and -- additive, only drawn once alternatives have been
  // requested -- N safest/fastest/balanced candidate layers (recommended/selected = strongest line, the rest
  // lighter/thinner), reusing the exact same visual convention.
  useEffect(() => {
    const g = groupsRef.current.route
    const map = mapRef.current
    if (!g || !map) return
    g.clearLayers()
    routeMarkersRef.current = []

    const addMarker = (lonlat, label, color) => {
      const icon = L.divIcon({
        className: 'route-marker',
        html: `<div class="route-marker-inner" style="background:${color}"><span>${label}</span></div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 24],
      })
      const m = L.marker(lonLatToLatLng(lonlat), { icon }).addTo(g)
      routeMarkersRef.current.push(m)
    }

    const candidates = alternatives.result?.candidates || []
    const altUnreachable = alternatives.result?.reachable === false
    const markerColor = candidates.length > 0 ? (altUnreachable ? '#dc2626' : '#2563eb') : (route.result?.reachable === false ? '#dc2626' : '#2563eb')

    if (route.origin) addMarker(route.origin, 'A', '#16a34a')
    if (route.dest) addMarker(route.dest, 'B', tripPreview?.result?.decision === 'ESCALATE' ? '#dc2626' : tripPreview?.result ? '#0B1F4D' : markerColor)

    const routeKey = route.origin && route.dest ? `${route.origin.join(',')}|${route.dest.join(',')}` : null
    const pv = tripPreview?.result
    const pvHere = Boolean(pv && tripPreview.request && route.origin && route.dest
      && tripPreview.request.origin.join() === route.origin.join() && tripPreview.request.dest.join() === route.dest.join())

    // the rescue-trip preview is drawn unless the user asked to compare route objectives (their candidates win)
    if (pvHere && pv.route?.reachable && !candidates.length) {
      // Rescue-trip preview (D-49): the server's arrival-aware route, highlighted; what it did not choose, subtle;
      // roads on it that are not plainly usable, marked in their status colour on top.
      for (const a of pv.alternatives || []) {
        if (a.route) L.geoJSON(a.route, { style: ROUTE_STYLE.alternative }).bindTooltip(`Not chosen: ${a.label} — ${a.why_not}`, { sticky: true }).addTo(g)
      }
      const core = addHighlighted(g, pv.route.route, `Selected route · ${pv.eta.eta_min.toFixed(1)} min · ${(pv.route.length_m / 1000).toFixed(2)} km (not a guarantee of safety)`)
      // live traffic (dotted, on top): only sampled roads; flood status colours stay visible beneath
      for (const tl of trafficLines(pv.traffic)) {
        const line = segGeom.get(String(tl.seg_id))
        const sg = pv.traffic.segments[tl.seg_id]
        if (!line) continue
        L.polyline(line, { color: tl.color, weight: 4, opacity: 1, dashArray: '2 8', lineCap: 'round' })
          .bindTooltip(`Live traffic: ${tl.level} (TomTom, ${Math.round(sg.current_kmh)} of ${Math.round(sg.free_flow_kmh)} km/h free flow)`, { sticky: true }).addTo(g)
      }
      for (const h of pv.route.hazards || []) {
        const line = segGeom.get(String(h.seg_id))
        const st = ACCESS_STATUS[h.status]
        if (!line || !st) continue
        L.polyline(line, { color: st.color, weight: 4, opacity: 1, dashArray: h.status === 'AT_RISK' || h.status === 'CLOSED' ? null : '4 5' })
          .bindTooltip(`${h.name || h.seg_id}: ${st.label} — ${h.reason}`, { sticky: true }).addTo(g)
      }
      if (routeKey && routeKey !== fittedRouteKeyRef.current) {
        try {
          map.fitBounds(core.getBounds().pad(0.35), { maxZoom: 16, minZoom: 13, animate: true, duration: 0.5 })
          fittedRouteKeyRef.current = routeKey
        } catch {
          /* ignore */
        }
      }
    } else if (pvHere && pv.decision === 'ESCALATE' && !candidates.length) {
      // no feasible route: nothing is drawn as if there were one
    } else if (access.result) {
      // Arrival-access mode: the `access` layer below draws both routes, coloured by road status.
    } else if (candidates.length > 0) {
      // Alternatives mode: render every candidate, selected/recommended on top and strongest.
      let selectedLayer = null
      candidates.forEach((c, i) => {
        if (!c.route) return
        const isSelected = alternatives.selected === i
        const color = OBJECTIVE_COLOR[c.objectives?.[0]] || DEFAULT_CANDIDATE_COLOR
        // Alternatives are deliberately NOT auto-recomputed when the dashboard timestep changes (evaluated
        // and rejected as too costly). So once `alternativesStale` is true, this geometry was computed for a
        // different timestep than the street-flood colouring and depth overlay it is drawn over: dash it and
        // drop its opacity so it can never present itself as current. RoutePlanner carries the wording.
        // the selected candidate gets the route highlight unless stale; the others stay subtle (D-49)
        const gj = isSelected && !alternativesStale
          ? addHighlighted(g, c.route)
          : L.geoJSON(c.route, {
            style: alternativesStale
              ? { color, weight: isSelected ? 4.5 : 2.5, opacity: isSelected ? 0.55 : 0.28, dashArray: '5 7' }
              : { ...ROUTE_STYLE.alternative, color },
          }).addTo(g)
        gj.on('click', () => selectAlternative(i))
        if (isSelected) {
          gj.bringToFront()
          selectedLayer = gj
        }
      })
      if (selectedLayer && routeKey && routeKey !== fittedRouteKeyRef.current) {
        try {
          map.fitBounds(selectedLayer.getBounds().pad(0.35), { maxZoom: 16, minZoom: 13, animate: true, duration: 0.5 })
          fittedRouteKeyRef.current = routeKey
        } catch {
          /* ignore */
        }
      }
    } else {
      const r = route.result
      if (r?.baseline_route) {
        L.geoJSON(r.baseline_route, { style: { color: 'rgba(148, 163, 184, 0.45)', weight: 2.5, dashArray: '4 6', opacity: 0.65 } }).addTo(g)
      }

      if (r?.route && r.reachable !== false) {
        const gj = addHighlighted(g, r.route, 'Lower flood-risk route (not a guarantee of safety)')
        if (routeKey && routeKey !== fittedRouteKeyRef.current) {
          try {
            map.fitBounds(gj.getBounds().pad(0.35), { maxZoom: 16, minZoom: 13, animate: true, duration: 0.5 })
            fittedRouteKeyRef.current = routeKey
          } catch {
            /* ignore */
          }
        }
      } else if (route.origin && route.dest && routeKey && routeKey !== fittedRouteKeyRef.current) {
        try {
          const bounds = L.latLngBounds([lonLatToLatLng(route.origin), lonLatToLatLng(route.dest)])
          map.fitBounds(bounds.pad(0.4), { maxZoom: 16, minZoom: 13, animate: true, duration: 0.5 })
          fittedRouteKeyRef.current = routeKey
        } catch {
          /* ignore */
        }
      }
    }

    if (!route.origin && !route.dest) {
      fittedRouteKeyRef.current = null
    }
  }, [route, alternatives, alternativesStale, selectAlternative, access.result, tripPreview, segGeom])

  // ---------------------------------------------------------------- arrival-time road access (MISC-04 Phase 1)
  // Closed roads (in force at the timeline time), the current-status-only route (dashed purple) and the
  // arrival-aware route, each road coloured by its status at the minute the vehicle is expected to reach it.
  const segCoords = useMemo(() => {
    const m = new Map()
    for (const f of roads?.features || []) m.set(String(f.properties.seg_id), (f.geometry.coordinates || []).map(lonLatToLatLng))
    return m
  }, [roads])
  useEffect(() => {
    const g = groupsRef.current.access
    if (!g) return
    g.clearLayers()
    for (const [segId, ev] of activeClosures(roadEvents, currentT)) {
      const ll = segCoords.get(segId)
      if (!ll) continue
      L.polyline(ll, { color: ACCESS_STATUS.CLOSED.color, weight: 9, opacity: 0.9 }).addTo(g)
      L.polyline(ll, { color: '#ffffff', weight: 3, dashArray: '6 6', opacity: 0.95 })
        .bindTooltip(`CLOSED: ${ev.name || segId} (report ${ev.event_id}, ${ev.source_tier || 'OPERATOR'}, T+${ev.received_t_min} min)`, { sticky: true })
        .addTo(g)
    }
    // Unconfirmed PUBLIC closure reports: routed as UNCERTAIN, never as closed, so they are drawn differently.
    for (const [segId, ev] of unconfirmedClosures(roadEvents, currentT)) {
      const ll = segCoords.get(segId)
      if (!ll) continue
      L.polyline(ll, { color: ACCESS_STATUS.UNCERTAIN.text, weight: 7, dashArray: '4 6', opacity: 0.95 })
        .bindTooltip(`UNCONFIRMED public report of closure: ${ev.name || segId} (${ev.event_id}, T+${ev.received_t_min} min). Treated as uncertain until an operator confirms.`, { sticky: true })
        .addTo(g)
    }
    const r = access.result
    if (!r) return
    if (r.current_status?.route) {
      L.geoJSON(r.current_status.route, { style: { color: CURRENT_STATUS_ROUTE_COLOR, weight: 4, dashArray: '8 7', opacity: 0.85 } })
        .bindTooltip('Current-status-only route (baseline)', { sticky: true })
        .addTo(g)
    }
    for (const s of r.arrival_aware?.segments || []) {
      const ll = segCoords.get(String(s.seg_id))
      if (!ll) continue
      const st = ACCESS_STATUS[s.status] || ACCESS_STATUS.UNKNOWN
      const depth = s.depth_cm != null ? `, ${s.depth_cm} cm` : ''
      L.polyline(ll, { color: st.color, weight: 7, opacity: 0.95 })
        .bindTooltip(`${s.name || s.seg_id}: ${st.label}${depth} at T+${s.enter_t_min.toFixed(1)} min`, { sticky: true })
        .addTo(g)
    }
  }, [access.result, roadEvents, currentT, segCoords])

  useEffect(() => {
    const el = mapRef.current?.getContainer()
    if (el) el.style.cursor = closeRoadMode ? 'crosshair' : ''
  }, [closeRoadMode])

  // ---------------------------------------------------------------- response planner (MISC-04 Phase 2)
  // Settlements (circle, colour = declared priority), shelters (square), team/vehicle bases (triangle) and the
  // legs of every live assignment, coloured by assignment status. Numbers are SIMULATED demo data.
  useEffect(() => {
    const g = groupsRef.current.response
    if (!g) return
    g.clearLayers()
    const res = responseResources
    if (!res) return
    const st = response.result?.state
    const icon = (shape, color, label) => L.divIcon({
      className: 'resp-marker',
      html: `<div class="resp-marker-${shape}" style="--c:${color}"></div><span class="resp-marker-label">${label}</span>`,
      iconSize: [18, 18], iconAnchor: [9, 9],
    })
    for (const s of st?.settlements || res.settlements) {
      const remaining = s.remaining ?? s.people_needing_help
      L.circleMarker(lonLatToLatLng(s.lonlat), {
        radius: 7 + Math.min(10, Math.sqrt(s.people_needing_help)), color: PRIORITY_COLOR[s.priority] || '#64748b',
        weight: 2, fillColor: PRIORITY_COLOR[s.priority] || '#64748b', fillOpacity: remaining > 0 ? 0.45 : 0.12,
      }).bindTooltip(`${s.id} ${s.name}: ${s.priority} priority, ${s.people_needing_help} people (SIMULATED)` +
        (st ? `, ${s.served} served, ${s.covered} covered, ${s.remaining} unmet` : ''), { sticky: true }).addTo(g)
    }
    for (const sh of st?.shelters || res.shelters) {
      const free = sh.free ?? sh.total_capacity - sh.occupancy
      L.marker(lonLatToLatLng(sh.lonlat), { icon: icon('square', free > 0 ? '#2563eb' : '#9ca3af', sh.id), interactive: true })
        .bindTooltip(`${sh.name}: ${free} of ${sh.total_capacity} places free (SIMULATED)`, { sticky: true }).addTo(g)
    }
    for (const t of res.teams) {
      L.marker(lonLatToLatLng(t.lonlat), { icon: icon('tri', '#0f766e', t.id) })
        .bindTooltip(`${t.name} base: ${t.base}`, { sticky: true }).addTo(g)
    }
    for (const a of response.ledger || []) {
      if (a.status === 'CANCELLED' || a.status === 'COMPLETED') continue
      const color = STATUS_COLOR[a.status] || '#64748b'
      for (const leg of a.legs || []) {
        if (!leg.route) continue
        L.geoJSON(leg.route, { style: { color, weight: a.status === 'PROPOSED' ? 3 : 5, opacity: 0.85, dashArray: a.status === 'PROPOSED' ? '6 6' : null } })
          .bindTooltip(`${a.assignment_id} ${STATUS_LABEL[a.status]}: ${leg.kind.replace('_', ' ')} → ${leg.to}, arrives T+${leg.arrive_t_min.toFixed(0)} min`, { sticky: true })
          .addTo(g)
      }
    }
    for (const v of st?.vehicles || []) {
      L.marker(lonLatToLatLng(v.position_lonlat), { icon: icon('tri', v.available ? '#7c3aed' : '#9ca3af', v.id) })
        .bindTooltip(`${v.name}: ${v.available ? `free from T+${v.free_at_t_min} min` : `unavailable (${v.unavailable_reason})`}`, { sticky: true }).addTo(g)
    }
  }, [response.result, response.ledger, responseResources])

  // ---------------------------------------------------------------- guided-tour command bus
  // ADDITIVE. The guided briefing needs to fly the map and pulse specific features, but Leaflet objects live
  // in this component's refs by design. Rather than exporting those refs (or letting narration text poke the
  // DOM), callers push a declarative command onto Context and this effect executes it here, where the refs
  // already are. Unknown command types are ignored. Highlights are purely visual: they add a CSS class to
  // the existing layer's element and never mutate `selectedSegId`, layer visibility, or any model state, so
  // a briefing can never change what the dashboard is actually showing.
  const highlightedRef = useRef([])
  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapCommand) return

    const clearHighlights = () => {
      for (const el of highlightedRef.current) el?.classList?.remove('tour-highlight')
      highlightedRef.current = []
    }

    const pulse = (index, ids) => {
      clearHighlights()
      const layers = []
      for (const id of ids || []) {
        const entry = index.get(String(id))
        const layer = entry?.layer ?? entry
        const el = layer?.getElement?.()
        if (el) {
          el.classList.add('tour-highlight')
          highlightedRef.current.push(el)
        }
        if (layer) layers.push(layer)
      }
      return layers
    }

    try {
      switch (mapCommand.type) {
        case 'MAP_FLY_TO': {
          const { lat, lng, zoom } = mapCommand
          if (Number.isFinite(lat) && Number.isFinite(lng)) {
            map.flyTo([lat, lng], Number.isFinite(zoom) ? zoom : map.getZoom(), { duration: 1.1 })
          }
          break
        }
        case 'HIGHLIGHT_SEGMENTS': {
          const layers = pulse(segIndexRef.current, mapCommand.ids)
          if (mapCommand.fit !== false && layers.length) {
            const group = L.featureGroup(layers)
            map.flyToBounds(group.getBounds().pad(0.55), { maxZoom: 17, duration: 1.1 })
          }
          break
        }
        case 'HIGHLIGHT_NODES': {
          const layers = pulse(nodeIndexRef.current, mapCommand.ids)
          if (mapCommand.fit !== false && layers.length) {
            const group = L.featureGroup(layers)
            map.flyToBounds(group.getBounds().pad(0.8), { maxZoom: 17, duration: 1.1 })
          }
          break
        }
        case 'CLEAR_HIGHLIGHT':
          clearHighlights()
          break
        default:
          break
      }
    } catch {
      /* a command must never break the map; worst case it does nothing */
    }
  }, [mapCommand])

  // drop highlight classes if this component unmounts mid-briefing
  useEffect(() => () => {
    for (const el of highlightedRef.current) el?.classList?.remove('tour-highlight')
    highlightedRef.current = []
  }, [])

  const legendRows = DEFAULT_BANDS_CM.map(([limit, label], i) => {
    const lo = i === 0 ? 0 : DEFAULT_BANDS_CM[i - 1][0]
    return { sev: label, text: `${lo}–${limit} cm` }
  }).concat([{ sev: 'critical', text: `≥${DEFAULT_BANDS_CM[DEFAULT_BANDS_CM.length - 1][0]} cm` }])

  // A run is on screen but no street reaches 1 cm at this time: say so, rather than show an empty map.
  const noFlooding = Boolean(frame?.streets?.features) && layers.streets
    && !frame.streets.features.some((f) => (f.properties?.depth_cm || 0) >= 1)

  return (
    <>
      <div ref={elRef} className={styles.mapEl} />
      {/* the legends give way to the turn cards while navigating */}
      <div className={`${styles.legend} glass-panel`} hidden={Boolean(navigation)}>
        <div className={styles.legendTitle}>Street flood depth</div>
        {legendRows.map(({ sev, text }) => (
          <div className={styles.legendRow} key={sev}>
            <span className={styles.legendSwatch} style={{ background: SEVERITY_COLOR[sev] }} />
            {SEVERITY_LABEL[sev]} <span style={{ color: 'var(--text-faint)' }}>&nbsp;{text}</span>
          </div>
        ))}
        {noFlooding && <div className={styles.legendEmpty}>No flooded streets at this time</div>}
        {(missionRows.length > 0 || tripsShown) && (
          <div className={styles.routeLegend}>
            <div className={styles.legendTitle}>Rescue missions</div>
            {missionRows.map((r) => (
              <div className={styles.legendRow} key={r.status}>
                <svg width="26" height="10" aria-hidden="true"><line x1="2" y1="5" x2="24" y2="5" stroke={STATUS_COLOR[r.status]} strokeWidth={r.dashed ? 2.5 : 4} strokeDasharray={r.dashed ? '5 4' : undefined} /></svg>{r.label}
              </div>
            ))}
            {tripsShown && (
              <div className={styles.legendRow}>
                <svg width="26" height="10" aria-hidden="true"><line x1="2" y1="5" x2="24" y2="5" stroke="#7A2231" strokeWidth="3" strokeDasharray="6 4" /></svg>Active trip
              </div>
            )}
          </div>
        )}
        {tripPreview?.result?.traffic?.mode === 'LIVE' && (
          <div className={styles.routeLegend}>
            <div className={styles.legendTitle}>Live traffic (dotted)</div>
            {TRAFFIC_LEVELS.map((l) => (
              <div className={styles.legendRow} key={l.level}>
                <svg width="26" height="10" aria-hidden="true"><line x1="2" y1="5" x2="24" y2="5" stroke={l.color} strokeWidth="4" strokeDasharray="2 6" strokeLinecap="round" /></svg>{l.label}
              </div>
            ))}
          </div>
        )}
        {(tripPreview?.result?.route?.reachable || selectedTripId) && (
          <div className={styles.routeLegend}>
            <div className={styles.legendTitle}>Route</div>
            {ROUTE_LEGEND.map(({ key, label, svg }) => (
              <div className={styles.legendRow} key={key}>
                <svg width="26" height="10" aria-hidden="true">{svg}</svg>{label}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}
