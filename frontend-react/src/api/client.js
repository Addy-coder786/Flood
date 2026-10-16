// FloodNet API client -- the ONLY place fetch() is called from. Every backend contract used by the UI is
// listed here so a schema change only needs updating in one place. Endpoints and response shapes were read
// directly from backend/floodnet/api/main.py and schemas.py before writing this file (project rule: don't
// guess response schemas).
//
// Units returned by the backend: rainfall mm/h, depth cm (already converted from internal metres at the API
// boundary), volumes m3, time minutes (t_min), coordinates [lon, lat].

// Production API origin, e.g. https://floodnet-api.example.com (no trailing slash). Empty/unset means the API
// is served from the SAME origin as the page (local FastAPI at /static, or the Vite dev proxy). This is the
// only deployment setting the frontend has; it is a public URL, never a secret.
export const API_BASE_URL = String(import.meta.env?.VITE_API_BASE_URL || '').replace(/\/+$/, '')
export const apiUrl = (path) => `${API_BASE_URL}${path}`

export class ApiError extends Error {
  constructor(message, { status = null, detail = null, path = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.path = path
  }
}

// Dispatcher token for live operations (DECISIONS D-39). Typed by the operator, kept for this browser tab only
// (sessionStorage), sent in the X-FloodNet-Token header. Never a VITE_ variable and never in the bundle. When the backend has no
// FLOODNET_DISPATCHER_TOKEN, requests from this machine need none.
export const DISPATCHER_TOKEN_KEY = 'floodnet.dispatcherToken'
export function getDispatcherToken() {
  try { return window.sessionStorage.getItem(DISPATCHER_TOKEN_KEY) || '' } catch { return '' }
}
export function setDispatcherToken(token) {
  try {
    if (token) window.sessionStorage.setItem(DISPATCHER_TOKEN_KEY, token)
    else window.sessionStorage.removeItem(DISPATCHER_TOKEN_KEY)
  } catch { /* storage blocked: the token stays unset */ }
}

export async function request(path, { method = 'GET', body, token } = {}) {
  let res
  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'
  const bearer = token ?? getDispatcherToken()
  if (bearer) headers['X-FloodNet-Token'] = bearer       // own header: the frontend never touches IMD auth headers (sources.test.mjs)
  try {
    res = await fetch(apiUrl(path), {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(`Backend unreachable (${path})`, { path })
  }
  let data = null
  try {
    data = await res.json()
  } catch {
    // non-JSON body (e.g. empty 204) -- leave data null
  }
  if (res.status === 504) {
    // the hosting platform stopped the request at its time limit (Vercel: FUNCTION_INVOCATION_TIMEOUT)
    throw new ApiError('The server did not finish within its time limit (300 s on the current hosting plan). '
      + 'Try a shorter forecast horizon.', { status: 504, detail: data, path })
  }
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || res.statusText
    throw new ApiError(
      typeof detail === 'string' ? detail : JSON.stringify(detail),
      { status: res.status, detail: data, path },
    )
  }
  return data
}

// ---------------------------------------------------------------- pilot / meta
export const getMeta = () => request('/api/meta')
export const getStatus = () => request('/api/status')
export const getProvenance = () => request('/api/provenance')
export const getScenarios = () => request('/api/scenarios')
export const getRoads = () => request('/api/roads')
export const getTopology = () => request('/api/topology')
export const getHotspots = () => request('/api/hotspots')
export const getTerrain = () => request('/api/terrain')
// The simulator's own DEM, lossless (raw float32) -- the single source for the 3D terrain view.
export const getDem = () => request('/api/terrain/dem')
export const getDataStatus = () => request('/api/data-status')

// ---------------------------------------------------------------- simulation
// Each of these returns the COMPLETE run (backend/floodnet/api/bundle.py; decoded by lib/runBundle.js). There
// is deliberately no "fetch a run by id" call: on serverless hosting the next request may reach a different
// server instance, which does not hold the run.
export const simulate = ({ scenarioId, blockage, horizonMin = 180 }) =>
  request('/api/simulate', {
    method: 'POST',
    body: { scenario_id: scenarioId, blockage: blockage ?? { mode: 'none' }, horizon_min: horizonMin },
  })

export const compare = ({ scenarioId, blockage, horizonMin = 180 }) =>
  request('/api/compare', {
    method: 'POST',
    body: { scenario_id: scenarioId, blockage, horizon_min: horizonMin },
  })

export const stormReplay = ({ blockage, horizonMin = 180 } = {}) =>
  request('/api/storm/replay', {
    method: 'POST',
    body: { blockage: blockage ?? { mode: 'none' }, horizon_min: horizonMin },
  })

// ---------------------------------------------------------------- routing
// The route is computed on the depths of the frame on screen, sent WITH the request (only wet segments; a missing
// segment is dry), so routing never depends on the server holding the run.
const routeBody = ({ origin, dest, tMin, vehicle, runId, streetDepthM, seriesTMin, streetsCm, dataMode }) => ({
  origin, dest, t_min: tMin, vehicle, run_id: runId,
  street_depth_m: streetDepthM ?? null, series_t_min: seriesTMin ?? null, streets_cm: streetsCm ?? null, data_mode: dataMode ?? null,
})

export const findRoute = ({ origin, dest, tMin = 0, vehicle = 'car', runId = null, streetDepthM = null, dataMode = null }) =>
  request('/api/route', { method: 'POST', body: routeBody({ origin, dest, tMin, vehicle, runId, streetDepthM, dataMode }) })

// Multiple candidate routes scored under three objectives (see backend/floodnet/routing/router.py
// safe_routes_multi's docstring): "safest" (flood-depth-penalised), "fastest" (DISTANCE only -- there is no
// travel-time/speed model anywhere in the graph, so this must stay labelled "by distance" wherever it is
// shown), and "balanced" (a blended objective between the two). Each candidate also carries `time_safety`
// (safe-through-T+X vs. unsafe-by-T+X) when the run's per-segment series is sent with the request.
export const findRouteAlternatives = ({
  origin, dest, tMin = 0, vehicle = 'car', runId = null, nCandidates = 3, streetDepthM = null, seriesTMin = null,
  streetsCm = null, dataMode = null,
}) =>
  request('/api/route/alternatives', {
    method: 'POST',
    body: { ...routeBody({ origin, dest, tMin, vehicle, runId, streetDepthM, seriesTMin, streetsCm, dataMode }), n_candidates: nCandidates },
  })

// ---------------------------------------------------------------- arrival-time road access (MISC-04 Phase 1)
// Arrival-aware route vs the current-status-only baseline (backend/floodnet/access/). Like the other route calls,
// the run's per-segment series and the operator's timestamped road events travel WITH the request.
export const findArrivalAccess = ({
  origin, dest, vehicle = 'car', dispatchTMin = 0, runId = null, seriesTMin = null, streetsCm = null, dataMode = null,
  events = [],
}) =>
  request('/api/access/route', {
    method: 'POST',
    body: {
      origin, dest, vehicle, dispatch_t_min: dispatchTMin, run_id: runId, series_t_min: seriesTMin,
      streets_cm: streetsCm, data_mode: dataMode, events,
    },
  })

// ---------------------------------------------------------------- response planner (MISC-04 Phase 2)
export const getResponseResources = () => request('/api/response/resources')

// The ledger and both event logs are held by the client and sent every time (backend/floodnet/response/planner.py).
export const planResponse = ({
  nowTMin, runId = null, seriesTMin = null, streetsCm = null, dataMode = null, roadEvents = [], responseEvents = [],
  ledger = [], planVersion = 0, mode = 'arrival', resourcesSource = null,
}) =>
  request('/api/response/plan', {
    method: 'POST',
    body: {
      now_t_min: nowTMin, run_id: runId, series_t_min: seriesTMin, streets_cm: streetsCm, data_mode: dataMode,
      road_events: roadEvents, response_events: responseEvents, ledger, plan_version: planVersion, mode,
      // 'registry' (dispatcher only, D-43/D-50): plan with the live registry instead of the demonstration fixtures
      ...(resourcesSource ? { resources_source: resourcesSource } : {}),
    },
  })

// Scripted replay with both routing methods, judged against a separate outcome run (MISC-04 Phase 3). Both runs are
// sent as series so the call works on any server instance; the outcome run is simulated by the client first.
export const evaluateResponse = ({ forecast, outcome, dataMode = null, seeds = null }) =>
  request('/api/response/evaluate', {
    method: 'POST',
    body: {
      series_t_min: forecast.t_min, streets_cm: forecast.streets, data_mode: dataMode,
      outcome_series_t_min: outcome.t_min, outcome_streets_cm: outcome.streets,
      ...(seeds?.length ? { seeds } : {}),
    },
  })

// ---------------------------------------------------------------- WhatsApp TEST alerts (Twilio; docs/ALERTS_WHATSAPP.md)
// Recipients and credentials live only in the backend .env; the client only supplies the summary lines.
export const whatsappStatus = () => request('/api/alerts/whatsapp/status')
export const sendWhatsapp = ({ kind = 'test', lines = [] }) =>
  request('/api/alerts/whatsapp/send', { method: 'POST', body: { kind, lines } })
export const whatsappMessage = (sid) => request(`/api/alerts/whatsapp/message/${encodeURIComponent(sid)}`)
// One click, every configured phone channel (WhatsApp via Twilio, Telegram).
export const phoneStatus = () => request('/api/alerts/phone/status')
export const sendPhone = ({ kind = 'test', lines = [] }) =>
  request('/api/alerts/phone/send', { method: 'POST', body: { kind, lines } })

// ---------------------------------------------------------------- live operations (D-38..D-43)
const post = (path, body = {}) => request(path, { method: 'POST', body })
export const opsStatus = () => request('/api/ops/status')
export const opsSetClock = (tMin, mode = 'REPLAY') => post('/api/ops/clock', { mode, t_min: tMin })
export const opsPublishForecast = ({ runId, dataMode, seriesTMin, streetsCm }) =>
  post('/api/ops/forecast', { run_id: runId, data_mode: dataMode || 'UNKNOWN', series_t_min: seriesTMin, streets_cm: streetsCm })
export const opsSyncRoadEvents = (events) => post('/api/ops/road-events/sync', { events })
export const opsRoadEvents = () => request('/api/ops/road-events')
export const opsTrips = () => request('/api/ops/trips')
export const opsTrip = (id) => request(`/api/ops/trips/${encodeURIComponent(id)}`)
export const opsCreateTrip = (assignment, names, incidentId = null) => post('/api/ops/trips', { assignment, names, incident_id: incidentId })
// Roads page rescue trips (D-49): the server routes, checks and times everything; the browser sends two points.
export const opsPreviewTrip = ({ origin, dest, vehicleId = null, vehicleType = null, names = {} }, { refreshTraffic = false } = {}) =>
  post('/api/ops/trips/preview', { origin, dest, vehicle_id: vehicleId, vehicle_type: vehicleType, names, refresh_traffic: refreshTraffic })
export const opsStartDirectTrip = ({ origin, dest, vehicleId = null, vehicleType = null, names = {}, clientRef, incidentId = null }) =>
  post('/api/ops/trips/direct', { origin, dest, vehicle_id: vehicleId, vehicle_type: vehicleType, names, client_ref: clientRef, incident_id: incidentId })
export const opsTripStage = (id, stage, reason = '') => post(`/api/ops/trips/${encodeURIComponent(id)}/stage`, { stage, reason })
export const opsDriverLink = (id) => post(`/api/ops/trips/${encodeURIComponent(id)}/driver-link`)
export const opsSimulatePosition = (id) => post(`/api/ops/trips/${encodeURIComponent(id)}/simulate-position`)
export const opsReassess = (id) => post(`/api/ops/trips/${encodeURIComponent(id)}/reassess`)
export const opsDecide = (id, pid, decision, reason = '') =>
  post(`/api/ops/trips/${encodeURIComponent(id)}/proposals/${encodeURIComponent(pid)}/decide`, { decision, reason })
export const opsResources = () => request('/api/ops/resources')
export const opsResourceUpdate = (id, fields) => post(`/api/ops/resources/${encodeURIComponent(id)}/updates`, { fields })

// ---------------------------------------------------------------- subscriber advisories (D-44..D-46)
const del = (path) => request(path, { method: 'DELETE' })
export const messagingStatus = () => request('/api/ops/messaging/status')
export const listSubscribers = () => request('/api/ops/subscribers')
export const addSubscriber = (body) => post('/api/ops/subscribers', body)
export const seedSimulatedSubscribers = () => post('/api/ops/subscribers/seed-simulated')
export const optOutSubscriber = (id) => post(`/api/ops/subscribers/${encodeURIComponent(id)}/opt-out`)
export const eraseSubscriber = (id) => del(`/api/ops/subscribers/${encodeURIComponent(id)}`)
export const listAlerts = () => request('/api/ops/alerts')
export const getAlert = (id) => request(`/api/ops/alerts/${encodeURIComponent(id)}`)
export const draftAlert = ({ windowMin = 60, publicBase }) => post('/api/ops/alerts/draft', { window_min: windowMin, public_base: publicBase })
export const alertAction = (id, action, body = {}) => post(`/api/ops/alerts/${encodeURIComponent(id)}/${action}`, body)
export const officialAlerts = () => request('/api/public/official-alerts')
export const refreshOfficialAlerts = () => post('/api/ops/official-alerts/refresh')
