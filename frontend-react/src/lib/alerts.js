// FloodNet Alert & Decision-Support layer: pure, deterministic functions over data ALREADY fetched into
// Context (`series`, `route.result`) -- no new API calls, no new backend state, no persistence. Every alert
// is a system-generated advisory derived from the current simulation, never a live/official warning.
//
// Depth-tier thresholds are read directly from `lib/severity.js`'s DEFAULT_BANDS_CM (the same "WORKING
// THRESHOLDS, NOT CITED GUIDANCE" bands used for the map/KPI/route severity everywhere else in the app) --
// nothing here invents a new depth threshold. The drainage-node count thresholds have no existing analogue
// in the codebase, so they are new, and are explicitly labelled "FloodNet prototype threshold" wherever
// shown -- a simple, round, order-of-magnitude signal for this ~1,200-node pilot network, not a derived or
// official standard.
import { DEFAULT_BANDS_CM } from './severity.js'

export const ALERT_LABEL = 'FloodNet Forecast Alert'
export const ADVISORY_LABEL = 'System Advisory — not an official government warning'

export const DRAINAGE_ADVISORY_NODES = 10   // FloodNet prototype threshold
export const DRAINAGE_SEVERE_NODES = 50     // FloodNet prototype threshold

export const TIER_COLOR = {
  WATCH: '#ffd23f',
  ADVISORY: '#ff8c1a',
  SEVERE: '#ff3b3b',
  CRITICAL: '#c81e5c',
  DRAINAGE: '#a78bfa',
  ROUTE: '#c58a3d',
}
// Same hues darkened for TEXT on the ivory canvas (WCAG 4.5:1, contrast.test.mjs). TIER_COLOR stays for strokes,
// borders and tints; any tier label rendered as text uses these.
export const TIER_TEXT_COLOR = {
  WATCH: '#866700',
  ADVISORY: '#A95400',
  SEVERE: '#D90000',
  CRITICAL: '#C81E5C',
  DRAINAGE: '#7246F7',
  ROUTE: '#8D622A',
}
export const TIER_ICON = {
  WATCH: '⚠',
  ADVISORY: '⚠',
  SEVERE: '🚨',
  CRITICAL: '🚨',
  DRAINAGE: '⚠',
  ROUTE: '🚧',
}

function nearestIndex(tArr, t) {
  let bi = 0
  let bd = Infinity
  for (let i = 0; i < tArr.length; i++) {
    const d = Math.abs(tArr[i] - t)
    if (d < bd) {
      bd = d
      bi = i
    }
  }
  return bi
}

function peakOf(tArr, vArr) {
  let best = -Infinity
  let bestT = tArr.length ? tArr[0] : 0
  for (let i = 0; i < vArr.length; i++) {
    if (vArr[i] > best) {
      best = vArr[i]
      bestT = tArr[i]
    }
  }
  return { value: best, tMin: bestT }
}

function firstCrossing(tArr, vArr, threshold) {
  for (let i = 0; i < vArr.length; i++) {
    if (vArr[i] >= threshold) return tArr[i]
  }
  return null
}

/** Deterministic alert lifecycle, driven only by the timeline position (`currentT`) against the series --
 * no clock, no persistence. ACTIVE: condition holds at the current frame. NEW: hasn't happened yet at the
 * current frame but the series shows it will. RESOLVED: it already peaked before the current frame and the
 * current value is back under threshold. */
function buildAlert({ id, type, tier, title, subject, series_t, series_v, threshold, currentT, unit, extra }) {
  const firstT = firstCrossing(series_t, series_v, threshold)
  if (firstT == null) return null
  const peak = peakOf(series_t, series_v)
  const idxNow = nearestIndex(series_t, currentT)
  const activeNow = series_v[idxNow] >= threshold
  const state = activeNow ? 'ACTIVE' : currentT >= peak.tMin ? 'RESOLVED' : 'NEW'
  const etaMin = state === 'NEW' ? Math.max(0, Math.round(firstT - currentT)) : null
  return {
    id, type, tier, title, subject, state, unit, threshold,
    currentValue: series_v[idxNow],
    peakValue: peak.value,
    peakTMin: peak.tMin,
    etaMin,
    icon: TIER_ICON[tier],
    color: TIER_COLOR[tier],
    ...extra,
  }
}

/** Up to two depth alerts (SEVERE, CRITICAL crossings of the pilot-wide max depth) -- WATCH/ADVISORY tiers
 * are used for hotspot categorisation (see rankHotspots) but not surfaced as headline feed items, to keep
 * the feed to the escalations an operator actually needs to act on. `subjectLabel` should name the real
 * street segment where the pilot-wide peak actually occurs (never an invented neighbourhood name). */
export function computeDepthAlerts(series, currentT, subjectLabel) {
  if (!series?.t_min?.length) return []
  const severeThreshold = DEFAULT_BANDS_CM[2][0] // 30 (severe starts here)
  const criticalThreshold = DEFAULT_BANDS_CM[3][0] // 60
  const t = series.t_min
  const v = series.max_depth_cm
  const out = []
  const severe = buildAlert({
    id: 'depth-severe', type: 'DEPTH', tier: 'SEVERE', title: 'FLOODNET ALERT',
    subject: subjectLabel || 'the pilot area', series_t: t, series_v: v, threshold: severeThreshold, currentT, unit: 'cm',
  })
  if (severe) out.push(severe)
  const critical = buildAlert({
    id: 'depth-critical', type: 'DEPTH', tier: 'CRITICAL', title: 'CRITICAL FLOOD ALERT',
    subject: subjectLabel || 'the pilot area', series_t: t, series_v: v, threshold: criticalThreshold, currentT, unit: 'cm',
  })
  if (critical) out.push(critical)
  return out
}

/** One drainage-capacity alert, tiered by the (prototype, labelled) node-count thresholds above. */
export function computeDrainageAlert(series, currentT) {
  if (!series?.t_min?.length) return null
  const t = series.t_min
  const v = series.surcharging_count
  const peak = peakOf(t, v)
  if (peak.value >= DRAINAGE_SEVERE_NODES) {
    return buildAlert({
      id: 'drainage-severe', type: 'DRAINAGE', tier: 'DRAINAGE', title: 'DRAINAGE CAPACITY ALERT',
      subject: 'drainage network', series_t: t, series_v: v, threshold: DRAINAGE_SEVERE_NODES, currentT, unit: 'nodes',
    })
  }
  if (peak.value >= DRAINAGE_ADVISORY_NODES) {
    return buildAlert({
      id: 'drainage-advisory', type: 'DRAINAGE', tier: 'DRAINAGE', title: 'DRAINAGE CAPACITY ALERT',
      subject: 'drainage network', series_t: t, series_v: v, threshold: DRAINAGE_ADVISORY_NODES, currentT, unit: 'nodes',
    })
  }
  return null
}

/** Route alert: does the CURRENTLY PLANNED route's own set of segments cross the selected vehicle's depth
 * limit at any future frame? Reuses series.streets (already fetched) and `route.result.vehicle_limit_cm` --
 * the exact same limit `/api/route` itself already enforced when it built this route -- never a second,
 * independently-invented limit. */
export function computeRouteAlert(series, route, currentT) {
  if (!series?.t_min?.length || !route?.result?.reachable || !route.result.route_segments?.length) return null
  const limit = route.result.vehicle_limit_cm
  if (limit == null) return null
  const t = series.t_min
  const segs = route.result.route_segments
  // worst-case depth across every segment on the route, per frame
  const worst = t.map((_, i) => {
    let m = 0
    for (const sid of segs) {
      const arr = series.streets?.[sid]
      if (arr && arr[i] > m) m = arr[i]
    }
    return m
  })
  const alert = buildAlert({
    id: 'route-unsafe', type: 'ROUTE', tier: 'ROUTE', title: 'ROUTE ALERT',
    subject: `current ${route.vehicle} route`, series_t: t, series_v: worst, threshold: limit, currentT, unit: 'cm',
  })
  if (!alert) return null
  return { ...alert, title: alert.state === 'ACTIVE' ? 'ROUTE ALERT — ROUTE UNSAFE NOW' : 'ROUTE ALERT' }
}

export function computeAllAlerts({ series, route, currentT, subjectLabel }) {
  const out = [...computeDepthAlerts(series, currentT, subjectLabel)]
  const drainage = computeDrainageAlert(series, currentT)
  if (drainage) out.push(drainage)
  const routeAlert = computeRouteAlert(series, route, currentT)
  if (routeAlert) out.push(routeAlert)
  // Most severe / most imminent first.
  const rank = { CRITICAL: 0, SEVERE: 1, DRAINAGE: 2, ROUTE: 3, ADVISORY: 4, WATCH: 5 }
  out.sort((a, b) => (rank[a.tier] ?? 9) - (rank[b.tier] ?? 9) || (a.etaMin ?? 0) - (b.etaMin ?? 0))
  return out
}

/** "Top Flood Priorities": rank real road segments by PEAK depth over the whole run (not just the current
 * frame -- that's FloodedStreets' job), each with peak time and a LOW/MODERATE/SEVERE/CRITICAL category
 * (merged from the same DEFAULT_BANDS_CM used everywhere else: clear+minor -> LOW). */
export function rankHotspots(series, segNameById, topN = 5) {
  if (!series?.streets) return []
  const t = series.t_min
  const rows = []
  for (const [segId, depths] of Object.entries(series.streets)) {
    const peak = peakOf(t, depths)
    if (peak.value < DEFAULT_BANDS_CM[0][0]) continue // below "minor" -- not a priority
    rows.push({
      segId,
      name: segNameById?.get(segId) || segId,
      peakDepthCm: peak.value,
      peakTMin: peak.tMin,
      category: categoryOf(peak.value),
    })
  }
  rows.sort((a, b) => b.peakDepthCm - a.peakDepthCm)
  return rows.slice(0, topN)
}

const CATEGORY_MERGE = { clear: 'LOW', minor: 'LOW', moderate: 'MODERATE', severe: 'SEVERE', critical: 'CRITICAL' }
export function categoryOf(depthCm) {
  for (const [limit, label] of DEFAULT_BANDS_CM) {
    if (depthCm < limit) return CATEGORY_MERGE[label]
  }
  return 'CRITICAL'
}
export const CATEGORY_COLOR = { LOW: '#8a94a6', MODERATE: '#ff8c1a', SEVERE: '#ff3b3b', CRITICAL: '#c81e5c' }

/** Conservative, generic operator suggestions for a SEVERE/CRITICAL alert -- explicitly NOT instructions. */
export function recommendedActions(alert) {
  const actions = []
  if (alert.type === 'DEPTH') {
    actions.push('🚧 Review/consider traffic restriction near the affected segment')
    actions.push('📍 Monitor the affected hotspot as the forecast progresses')
  }
  if (alert.type === 'DRAINAGE') {
    actions.push('🔧 Inspect the drainage nodes reported as surcharging')
  }
  if (alert.type === 'ROUTE') {
    actions.push('🚑 Check an alternate emergency route before it is needed')
  }
  return actions
}

/** One human-readable body sentence per alert, built ONLY from the numbers already on the alert object --
 * never a template that implies a fact the alert doesn't carry. */
export function describeAlert(alert) {
  const round = (v) => Math.round(v)
  if (alert.type === 'DEPTH') {
    if (alert.state === 'NEW') {
      return `Predicted flooding is approaching ${alert.tier} level near ${alert.subject}. Peak depth: ${round(alert.peakValue)} cm at +${round(alert.peakTMin)} min. Expected in: ${alert.etaMin} min.`
    }
    if (alert.state === 'ACTIVE') {
      return `Depth near ${alert.subject} is at ${alert.tier} level now (${round(alert.currentValue)} cm). Peak: ${round(alert.peakValue)} cm at +${round(alert.peakTMin)} min.`
    }
    return `Peak ${alert.tier} depth near ${alert.subject} has passed (peaked at ${round(alert.peakValue)} cm at +${round(alert.peakTMin)} min).`
  }
  if (alert.type === 'DRAINAGE') {
    const thresholdNote = `(FloodNet prototype threshold: ${alert.threshold} nodes)`
    if (alert.state === 'NEW') {
      return `Up to ${round(alert.peakValue)} drainage nodes are predicted to approach surcharge conditions. Expected in: ${alert.etaMin} min. ${thresholdNote}`
    }
    if (alert.state === 'ACTIVE') {
      return `${round(alert.currentValue)} drainage nodes are surcharging now (peak ${round(alert.peakValue)} at +${round(alert.peakTMin)} min). ${thresholdNote}`
    }
    return `Drainage surcharge has passed its peak (${round(alert.peakValue)} nodes at +${round(alert.peakTMin)} min). ${thresholdNote}`
  }
  if (alert.type === 'ROUTE') {
    if (alert.state === 'NEW') {
      return `${alert.subject} is predicted to exceed the ${round(alert.threshold)} cm vehicle depth limit in ${alert.etaMin} min.`
    }
    if (alert.state === 'ACTIVE') {
      return `${alert.subject} exceeds the ${round(alert.threshold)} cm vehicle depth limit now.`
    }
    return `${alert.subject} was predicted unsafe earlier in the forecast but has since cleared.`
  }
  return ''
}
