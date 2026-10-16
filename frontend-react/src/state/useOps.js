// Live operations in the dispatcher browser (DECISIONS D-38 to D-43). Connects this browser's incident to the shared
// store on the server so drivers and the public page see the same picture:
//   up    the forecast on screen, the decision time (REPLAY clock) and the road reports made here
//   down  field reports from drivers (merged into the road log once) and trip stages (moved into the ledger)
// Everything here is optional: if the server has no shared store (Vercel, D-38) or the dispatcher token is missing,
// `ops.available` is false with the reason, and the rest of FloodNet works exactly as before.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api/client.js'
import { applyTripsToLedger, dispatcherEvents, forecastInputLabel, mergeFieldEvents, tripsOf } from '../lib/ops.js'
import { tokenFromLink } from '../lib/navigation.js'

const POLL_MS = 10000
const noop = () => {}
const SYNC_KEYS = ['event_id', 'seg_id', 'kind', 'received_t_min', 'applies_from_t_min', 'source', 'note', 'source_tier', 'references', 'vehicle_types', 'name']

export function useOps({ run, accessSeries, currentT, roadEvents, setRoadEvents, setResponse, notify, responseResources, incidentId }) {
  const [ops, setOps] = useState({ available: null, reason: '', trips: [], resources: [], lastSync: null, error: null, links: {}, busy: false })
  // Roads page rescue trip (D-49): the preview the dispatcher is looking at and the trip selected on the map
  const [tripPreview, setTripPreview] = useState({ loading: false, error: null, result: null, request: null, clientRef: null })
  const [selectedTripId, setSelectedTripId] = useState(null)
  // Navigation mode (Roads page): the trip this device is driving, with its driver token, and the device's own last
  // GPS fix for the map. Only ever a real fix from the browser; nothing here is simulated.
  const [navigation, setNavigation] = useState(null)        // { tripId, token } | null
  const [navFix, setNavFix] = useState(null)                // { lat, lon, accuracy, at } | null
  const tRef = useRef(currentT)
  useEffect(() => { tRef.current = currentT }, [currentT])
  const incRef = useRef(incidentId)
  useEffect(() => { incRef.current = incidentId }, [incidentId])

  const probe = useCallback(async () => {
    try {
      await api.opsStatus()
      setOps((o) => ({ ...o, available: true, reason: '', error: null }))
    } catch (e) {
      const reason = e.status === 401 ? 'Dispatcher token required for live operations.'
        : e.status === 503 ? (e.message || 'Live operations are not available on this deployment.')
          : `Live operations unavailable: ${e.message}`
      setOps((o) => ({ ...o, available: false, reason }))
    }
  }, [])
  useEffect(() => {
    const id = setTimeout(probe, 0)          // async probe; state is set when the server answers
    return () => clearTimeout(id)
  }, [probe])

  const setToken = useCallback((token) => { api.setDispatcherToken(token.trim()); probe() }, [probe])

  // up: forecast, clock, dispatcher road reports
  const runId = run?.run_id ?? null
  const dataMode = run ? forecastInputLabel(run) : null      // rainfall source first: a design storm is SYNTHETIC
  useEffect(() => {
    if (!ops.available || !accessSeries?.t_min?.length) return
    api.opsPublishForecast({ runId, dataMode, seriesTMin: accessSeries.t_min, streetsCm: accessSeries.streets })
      .catch((e) => setOps((o) => ({ ...o, error: `Could not share the forecast: ${e.message}` })))
  }, [ops.available, accessSeries, runId, dataMode])
  useEffect(() => {
    if (!ops.available) return undefined
    const id = setTimeout(() => api.opsSetClock(currentT).catch(noop), 400)
    return () => clearTimeout(id)
  }, [ops.available, currentT])
  useEffect(() => {
    if (!ops.available) return undefined
    const mine = dispatcherEvents(roadEvents)
    if (!mine.length) return undefined
    const id = setTimeout(() => {
      const events = mine.map((e) => Object.fromEntries(SYNC_KEYS.filter((k) => e[k] !== undefined).map((k) => [k, e[k]])))
      api.opsSyncRoadEvents(events)
        .then((r) => { if (r.conflicts?.length) setOps((o) => ({ ...o, error: `Road reports ${r.conflicts.join(', ')} differ from the shared copy; the shared copy is kept.` })) })
        .catch((e) => setOps((o) => ({ ...o, error: `Could not share road reports: ${e.message}` })))
    }, 400)
    return () => clearTimeout(id)
  }, [ops.available, roadEvents])

  // down: field reports and trips
  const refresh = useCallback(async () => {
    try {
      const [ev, tr] = await Promise.all([api.opsRoadEvents(), api.opsTrips()])
      setRoadEvents((local) => mergeFieldEvents(local, ev.events, incRef.current))
      setResponse((r) => {
        const { ledger, changes } = applyTripsToLedger(r.ledger, tr.trips, tRef.current, incRef.current)
        if (ledger === r.ledger) return r
        return { ...r, ledger, planLogs: [...r.planLogs, { t: tRef.current, lines: changes.map((c) => `${c.assignment_id} ${c.from} → ${c.to} (trip ${c.trip_id})`) }] }
      })
      const mine = tripsOf(tr.trips, incRef.current)
      const res = await api.opsResources().catch(() => null)          // the registry is optional for trips
      setOps((o) => ({ ...o, trips: mine, otherTrips: tr.trips.length - mine.length, allTrips: tr.trips,
        resources: res?.resources ?? o.resources, lastSync: Date.now(), error: null }))
    } catch (e) {
      setOps((o) => ({ ...o, error: `Live update failed: ${e.message}` }))
    }
  }, [setRoadEvents, setResponse])
  useEffect(() => {
    if (!ops.available) return undefined
    const first = setTimeout(refresh, 0)
    const id = setInterval(refresh, POLL_MS)
    return () => { clearTimeout(first); clearInterval(id) }
  }, [ops.available, refresh])

  const act = useCallback(async (fn, okText) => {
    setOps((o) => ({ ...o, busy: true, error: null }))
    try {
      const out = await fn()
      if (okText) notify?.(okText, 'info')
      await refresh()
      return out
    } catch (e) {
      setOps((o) => ({ ...o, error: e.message }))
      await refresh().catch(() => {})      // show what changed on the server (e.g. a newer proposal) at once
      setOps((o) => ({ ...o, error: e.message }))
      return null
    } finally {
      setOps((o) => ({ ...o, busy: false }))
    }
  }, [refresh, notify])

  const nameOf = useCallback((table, id) => (responseResources?.[table] || []).find((x) => x.id === id)?.name || id, [responseResources])
  const startTrip = useCallback((assignment) => act(async () => {
    const names = { team: nameOf('teams', assignment.team_id), vehicle: nameOf('vehicles', assignment.vehicle_id),
      settlement: nameOf('settlements', assignment.settlement_id), shelter: nameOf('shelters', assignment.shelter_id) }
    const out = await api.opsCreateTrip(assignment, names, incRef.current)
    if (out?.driver_link) setOps((o) => ({ ...o, links: { ...o.links, [out.trip.trip_id]: out.driver_link } }))
    return out
  }, 'Trip started. Give the driver link to the driver.'), [act, nameOf])
  const newDriverLink = useCallback((tripId) => act(async () => {
    const out = await api.opsDriverLink(tripId)
    setOps((o) => ({ ...o, links: { ...o.links, [tripId]: out.driver_link } }))
    return out
  }, 'New driver link created; the old one no longer works.'), [act])
  const decideProposal = useCallback((tripId, pid, decision, reason = '') =>
    act(() => api.opsDecide(tripId, pid, decision, reason), decision === 'ACCEPT' ? 'Route change accepted; waiting for the driver to acknowledge.' : 'Route change rejected.'), [act])
  const simulatePosition = useCallback((tripId) => act(() => api.opsSimulatePosition(tripId)), [act])
  const reassessTrip = useCallback((tripId) => act(() => api.opsReassess(tripId)), [act])
  const setTripStage = useCallback((tripId, stage, reason = '') => act(() => api.opsTripStage(tripId, stage, reason)), [act])

  const previewTrip = useCallback(async (req, opts = {}) => {
    const key = JSON.stringify(req)
    setTripPreview((p) => ({ ...p, loading: true, error: null, request: req }))
    try {
      const result = await api.opsPreviewTrip(req, opts)
      // a new key per preview: Start Trip clicked twice for the same preview is one trip (server dedupes on it)
      setTripPreview((p) => (JSON.stringify(p.request) === key ? { loading: false, error: null, result, request: req,
        clientRef: `start-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}` } : p))
    } catch (e) {
      setTripPreview((p) => (JSON.stringify(p.request) === key ? { ...p, loading: false, error: e.message, result: null } : p))
    }
  }, [])
  const clearTripPreview = useCallback(() => setTripPreview({ loading: false, error: null, result: null, request: null, clientRef: null }), [])
  const startNavigation = useCallback((tripId, token) => {
    if (!tripId || !token) return
    setSelectedTripId(tripId)
    setNavigation({ tripId, token })
  }, [])
  const exitNavigation = useCallback(() => { setNavigation(null); setNavFix(null) }, [])
  /** Navigate a trip this browser holds no token for: a new driver link is made (the old one stops working). */
  const navigateWithNewLink = useCallback((tripId) => act(async () => {
    const out = await api.opsDriverLink(tripId)
    setOps((o) => ({ ...o, links: { ...o.links, [tripId]: out.driver_link } }))
    startNavigation(tripId, tokenFromLink(out.driver_link))
    return out
  }), [act, startNavigation])
  const startDirectTrip = useCallback(({ navigate = false } = {}) => act(async () => {
    const { request, clientRef } = tripPreview
    if (!request) throw new Error('Preview a route first.')
    const out = await api.opsStartDirectTrip({ ...request, clientRef, incidentId: incRef.current })
    if (out?.driver_link) setOps((o) => ({ ...o, links: { ...o.links, [out.trip.trip_id]: out.driver_link } }))
    if (out?.trip) setSelectedTripId(out.trip.trip_id)
    setTripPreview({ loading: false, error: null, result: null, request: null, clientRef: null })
    return out
  }, navigate ? 'Trip started. Navigation on this device begins now.'
    : 'Trip started. Give the driver link to the driver; the trip is PLANNED until the driver accepts.'), [act, tripPreview])

  return useMemo(() => ({ ops, setToken, refreshOps: refresh, startTrip, newDriverLink, decideProposal, simulatePosition, reassessTrip, setTripStage,
    tripPreview, previewTrip, clearTripPreview, startDirectTrip, selectedTripId, selectTrip: setSelectedTripId,
    navigation, navFix, setNavFix, startNavigation, exitNavigation, navigateWithNewLink }),
  [ops, setToken, refresh, startTrip, newDriverLink, decideProposal, simulatePosition, reassessTrip, setTripStage,
    tripPreview, previewTrip, clearTripPreview, startDirectTrip, selectedTripId, navigation, navFix, startNavigation,
    exitNavigation, navigateWithNewLink])
}
