// Navigation mode on the Roads page (2026-10-11). Pure helpers over the server's driver view
// (backend/floodnet/api/ops_api.py _driver_view): the dashboard user follows a rescue trip they started, with the
// device's own GPS. Turn instructions come from FloodNet's router (ops/navigation.py), never from an outside provider.
// Nothing here moves a position on its own: a fix is the browser's, or there is none.
import { formatDistance, nextInstruction } from './driver.js'

export const NAV_GPS_INTERVAL_S = 15          // matches the server's location_sharing.interval_s
export const NAV_FIX_OLD_S = 60               // a fix older than this is said to be old
export const RISK_CHECK_OLD_MIN = 30          // ASSUMED: a route not re-checked for 30 model minutes may be outdated

/** The token inside a driver link ("driver#t=..."), or null. */
export function tokenFromLink(link) {
  const m = /[#&]t=([A-Za-z0-9_-]+)/.exec(String(link || ''))
  return m ? m[1] : null
}

/** Next turn, distance to it, the instruction after it, and what is left of the trip. */
export function navSummary(view) {
  const empty = { next: null, remainingDistance: '–', remainingMinutes: null, destination: null, arrived: false }
  if (!view) return empty
  const legs = view.remaining || []
  const arrived = view.stage === 'ARRIVED' || view.stage === 'COMPLETED' || !legs.length
  const destination = view.destination || view.shelter || legs[legs.length - 1]?.to || null
  if (arrived) return { ...empty, destination, arrived: true }
  const metres = legs.flatMap((l) => l.maneuvers || []).reduce((a, m) => a + (Number(m.distance_m) || 0), 0)
  const first = legs[0]
  const enter = Number(first.segments?.[0]?.enter_t_min)
  const arrive = Number(legs[legs.length - 1].arrive_t_min)
  const minutes = Number.isFinite(enter) && Number.isFinite(arrive) ? Math.max(0, Math.ceil(arrive - enter)) : null
  return { next: nextInstruction(view), remainingDistance: formatDistance(metres), remainingMinutes: minutes, destination, arrived: false }
}

const NAME = (h) => (h.name || '').trim() || 'unnamed road'

/** One warning line for the top of the screen, most serious first. */
export function warningFor(view) {
  if (!view) return { level: 'clear', text: '' }
  if (view.escalation) {
    return { level: 'escalated', text: `No safer route found: ${view.escalation}. Stop somewhere safe and decide with the dispatcher.` }
  }
  if (view.route_change) {
    return { level: 'reroute', text: `New route: ${view.route_change.reason}. The guidance below already follows it.`, proposal: view.route_change }
  }
  if (view.open_proposal) {
    const p = view.open_proposal
    return { level: 'reroute', text: `${p.kind === 'REQUIRED' ? 'Road ahead blocked or flooded' : 'A better route is available'} – safer route found: ${p.reason}`, proposal: p }
  }
  const h = view.hazards_ahead || []
  if (h.length) {
    const names = h.slice(0, 2).map(NAME).join(', ')
    return { level: 'hazard', text: `${h.length} road${h.length > 1 ? 's' : ''} ahead ${h.length > 1 ? 'are' : 'is'} not plainly usable: ${names}${h.length > 2 ? ` +${h.length - 2}` : ''}. Slow down and look before entering water.` }
  }
  return { level: 'clear', text: 'No flood hazard known on the remaining route. Not a guarantee: watch the road.' }
}

/** What the flood-risk information rests on, and whether it may be outdated. */
export function riskFreshness(view, nowT) {
  const f = view?.eta?.forecast_input
  const checked = Number(view?.assessed_t_min)
  const age = Number.isFinite(checked) && Number.isFinite(Number(nowT)) ? Number(nowT) - checked : null
  const oldCheck = age != null && age > RISK_CHECK_OLD_MIN
  if (!f || !f.label || f.label === 'UNKNOWN') {
    return { label: 'UNKNOWN', outdated: true, text: 'Flood-risk information unavailable or unknown: it may be outdated. Treat every flooded road as impassable.' }
  }
  if (f.label === 'STALE') {
    return { label: 'STALE', outdated: true, text: 'Flood-risk information is from a STALE forecast and may be outdated.' }
  }
  if (oldCheck) {
    return { label: f.label, outdated: true, text: `Route last checked ${Math.round(age)} min ago against a ${f.label} forecast: risk information may be outdated.` }
  }
  return { label: f.label, outdated: false, text: `Risk from a ${f.label} flood forecast${Number.isFinite(checked) ? `, route checked at T+${Math.round(checked)} min` : ''}.` }
}

/** The GPS chip: words and a tone, never colour alone. */
export function gpsStatus({ support, error, fix, nowMs = Date.now(), offline = false }) {
  if (support && !support.ok) return { tone: 'error', text: support.reason }
  if (error) return { tone: 'error', text: error }
  if (!fix) return { tone: 'warn', text: 'GPS: waiting for a fix…' }
  const age = Math.max(0, Math.round((nowMs - fix.at) / 1000))
  const acc = Number.isFinite(fix.accuracy) ? ` ±${Math.round(fix.accuracy)} m` : ''
  const off = offline ? ' · offline, position not shared' : ''
  if (age > NAV_FIX_OLD_S) return { tone: 'warn', text: `GPS fix ${Math.round(age / 60)} min old${acc}${off}` }
  return { tone: 'ok', text: `GPS ${age} s ago${acc}${off}` }
}

const STAGE_BUTTON = { EN_ROUTE: 'Start driving', ARRIVED: 'I have arrived', COMPLETED: 'Finish trip' }

/** The one stage button navigation shows: start driving (accepting on the way), arrived, finish. */
export function navStageAction(view) {
  const next = view?.next_stages || []
  for (const stage of ['EN_ROUTE', 'ARRIVED', 'COMPLETED']) {
    if (next.includes(stage)) {
      const steps = next.slice(0, next.indexOf(stage) + 1)
      return { stage, label: STAGE_BUTTON[stage], steps }
    }
  }
  return null
}
