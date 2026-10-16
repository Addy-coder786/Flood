import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { request } from '../../api/client.js'
import { geoErrorMessage, geoSupport, hazardRows, positionPayload, shouldPost, upcomingManeuvers } from '../../lib/driver.js'
import { gpsStatus, NAV_GPS_INTERVAL_S, navStageAction, navSummary, riskFreshness, warningFor } from '../../lib/navigation.js'
import { ACCESS_STATUS } from '../../lib/roadAccess.js'
import s from './NavigationMode.module.css'

// Navigation mode (2026-10-11): the Roads page user follows the rescue trip they just started, like a phone
// navigation app, over the map. Everything is the server's driver view for that trip (turn instructions from
// FloodNet's own router, hazards and route changes from the route-impact rules), fetched with the trip's driver token.
// The device's own GPS is posted at most every 15 s; the server re-checks the route after each fix. There is no
// simulated movement here: with no fix, the position chip says so and the server's planned estimate is used.

const POLL_MS = 15_000
const GEO_OPTS = { enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000 }
const TERMINAL = new Set(['COMPLETED', 'ABORTED'])

const call = (token, path, body) => request(path, { method: body ? 'POST' : 'GET', body, token })

export default function NavigationMode() {
  const { navigation, exitNavigation, setNavFix, decideProposal, refreshOps, ops, currentT } = useFloodNet()
  const { tripId, token } = navigation
  const [view, setView] = useState(null)
  const [error, setError] = useState(null)         // { kind: 'invalid' | 'offline' | 'other', text }
  const [busy, setBusy] = useState(false)
  const [fix, setFix] = useState(null)
  const [geoErr, setGeoErr] = useState(null)
  const [offline, setOffline] = useState(false)
  const [tick, setTick] = useState(() => Date.now())
  const [showList, setShowList] = useState(false)
  const lastPostRef = useRef(null)
  const inflightRef = useRef(false)
  const lockRef = useRef(null)

  const applyView = useCallback((v) => { setView(v); setOffline(false); setError(null) }, [])
  const onFail = useCallback((e) => {
    if (e?.status === 401) setError({ kind: 'invalid', text: 'This trip link is no longer valid (a new driver link replaced it). Exit and start again.' })
    else if (e?.status === 404) setError({ kind: 'other', text: e.message })
    else setOffline(true)
  }, [])

  // the trip view, polled while the tab is visible
  useEffect(() => {
    let alive = true
    const load = () => {
      if (document.visibilityState !== 'visible') return
      call(token, '/api/ops/driver/trip').then((v) => { if (alive) applyView(v) }, (e) => { if (alive) onFail(e) })
    }
    load()
    const timer = setInterval(load, POLL_MS)
    const onVis = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onVis)
    const clock = setInterval(() => setTick(Date.now()), 5000)
    return () => { alive = false; clearInterval(timer); clearInterval(clock); document.removeEventListener('visibilitychange', onVis) }
  }, [token, applyView, onFail])

  // the open proposal for this trip (dispatcher view, polled by useOps): accepting it here is the dispatcher's act
  const open = useMemo(() => {
    const t = (ops.trips || []).find((x) => x.trip_id === tripId)
    return (t?.proposals || []).find((p) => p.status === 'OPEN') || null
  }, [ops.trips, tripId])
  const viewWithProposal = useMemo(() => (view ? { ...view, open_proposal: open && !view.route_change ? open : null } : null), [view, open])

  // device GPS: watch, post at most every NAV_GPS_INTERVAL_S, keep the screen awake
  const support = useMemo(() => geoSupport({ isSecureContext: window.isSecureContext, hasGeolocation: !!navigator.geolocation }), [])
  const onPosition = useCallback((pos) => {
    const body = positionPayload(pos)
    if (!body) return
    setGeoErr(null)
    const f = { lat: body.lat, lon: body.lon, accuracy: body.accuracy_m, at: Date.now() }
    setFix(f)
    setNavFix(f)
    const now = Date.now()
    if (inflightRef.current || !shouldPost(lastPostRef.current, now, NAV_GPS_INTERVAL_S)) return
    inflightRef.current = true
    lastPostRef.current = now
    call(token, '/api/ops/driver/position', body)
      .then((v) => { applyView(v); refreshOps() }, onFail)
      .finally(() => { inflightRef.current = false })
  }, [token, applyView, onFail, setNavFix, refreshOps])
  useEffect(() => {
    if (!support.ok) return undefined
    const id = navigator.geolocation.watchPosition(onPosition, (e) => setGeoErr(geoErrorMessage(e)), GEO_OPTS)
    const nudge = setInterval(() => {
      if (document.visibilityState === 'visible') navigator.geolocation.getCurrentPosition(onPosition, () => {}, GEO_OPTS)
    }, NAV_GPS_INTERVAL_S * 1000)
    const wake = () => navigator.wakeLock?.request?.('screen').then((l) => { lockRef.current = l }, () => {})
    wake()
    const onVis = () => { if (document.visibilityState === 'visible') wake() }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      navigator.geolocation.clearWatch(id)
      clearInterval(nudge)
      document.removeEventListener('visibilitychange', onVis)
      lockRef.current?.release?.().catch?.(() => {})
      lockRef.current = null
    }
  }, [support.ok, onPosition])

  const act = async (fn) => {
    setBusy(true)
    try { await fn() } catch (e) { onFail(e) } finally { setBusy(false) }
  }
  const stageAction = navStageAction(view)
  const onStage = () => act(async () => {
    let v = view
    for (const st of stageAction.steps) v = await call(token, '/api/ops/driver/stage', { stage: st })
    applyView(v)
    refreshOps()
  })
  const onAcceptRoute = (p) => act(async () => {
    await decideProposal(tripId, p.proposal_id, 'ACCEPT')
    const v = await call(token, '/api/ops/driver/trip')
    if (v.route_change) applyView(await call(token, '/api/ops/driver/ack', { proposal_id: v.route_change.proposal_id }))
    else applyView(v)
  })
  const onAck = (p) => act(async () => applyView(await call(token, '/api/ops/driver/ack', { proposal_id: p.proposal_id })))

  const summary = navSummary(viewWithProposal)
  const warning = warningFor(viewWithProposal)
  const risk = riskFreshness(view, currentT)
  const gps = gpsStatus({ support, error: geoErr, fix, nowMs: tick, offline })
  const hazards = hazardRows(view)
  const upcoming = upcomingManeuvers(view)
  const finished = view && TERMINAL.has(view.stage)

  return (
    <div className={s.nav} role="region" aria-label="Navigation">
      <div className={s.top}>
        {error ? (
          <div className={`${s.banner} ${s.banner_escalated}`} role="alert">{error.text}</div>
        ) : !view ? (
          <div className={s.card}><p className={s.muted} role="status">{offline ? 'Offline – trying to load the trip…' : 'Loading the trip…'}</p></div>
        ) : finished ? (
          <div className={s.card}>
            <p className={s.turn}>{view.stage === 'COMPLETED' ? 'Trip completed' : 'Trip stopped by the dispatcher'}</p>
            <p className={s.muted}>{summary.destination ? `Destination: ${summary.destination}` : ''}</p>
          </div>
        ) : (
          <>
            {warning.level !== 'clear' && (
              <div className={`${s.banner} ${s[`banner_${warning.level}`]}`} role={warning.level === 'hazard' ? 'status' : 'alert'}>
                <span>{warning.text}</span>
                {warning.level === 'reroute' && warning.proposal && !view.route_change && (
                  <div className={s.bannerActions}>
                    <button type="button" className={`btn btn-primary ${s.sm}`} disabled={busy} onClick={() => onAcceptRoute(warning.proposal)}>Take the safer route</button>
                    <button type="button" className={`btn ${s.sm}`} disabled={busy} onClick={() => setShowList(true)}>Keep current route for now</button>
                  </div>
                )}
                {warning.level === 'reroute' && view.route_change && (
                  <div className={s.bannerActions}>
                    <button type="button" className={`btn btn-primary ${s.sm}`} disabled={busy} onClick={() => onAck(view.route_change)}>OK, following the new route</button>
                  </div>
                )}
              </div>
            )}
            <div className={s.card} aria-live="polite">
              {summary.arrived ? (
                <p className={s.turn}>You have reached {summary.destination || 'the destination'}</p>
              ) : summary.next ? (
                <>
                  <p className={s.distance}>{summary.next.distance ? `In ${summary.next.distance}` : 'Now'}</p>
                  <p className={s.turn}>{summary.next.instruction}</p>
                  {summary.next.then && <p className={s.then}>Then: {summary.next.then}</p>}
                </>
              ) : <p className={s.turn}>Go to {summary.destination || 'the destination'}</p>}
            </div>
          </>
        )}
      </div>

      <div className={s.bottom}>
        <div className={s.stats}>
          <div><span className={s.statLabel}>Remaining</span><strong>{summary.remainingMinutes == null ? '–' : `${summary.remainingMinutes} min`}</strong></div>
          <div><span className={s.statLabel}>Distance</span><strong>{summary.remainingDistance}</strong></div>
          <div><span className={s.statLabel}>To</span><strong className={s.dest}>{summary.destination || '–'}</strong></div>
        </div>
        <div className={s.chips}>
          <span className={`${s.chip} ${s[`chip_${gps.tone}`]}`}>{gps.text}</span>
          <span className={`${s.chip} ${risk.outdated ? s.chip_warn : s.chip_info}`}>{risk.text}</span>
          <span className={`${s.chip} ${s.chip_info}`}>{view?.eta?.traffic_delay_min != null ? `Traffic as at start (+${Number(view.eta.traffic_delay_min).toFixed(1)} min)` : 'Traffic not included'}</span>
        </div>
        {view?.position?.source === 'PLANNED_ESTIMATE' && !fix && (
          <p className={s.muted}>No GPS fix yet: the server estimates your position from the plan.</p>
        )}
        <div className={s.actions}>
          {stageAction && !finished && (
            <button type="button" className={`btn ${stageAction.stage === 'COMPLETED' ? 'btn-primary' : ''}`} disabled={busy || !view} onClick={onStage}>{stageAction.label}</button>
          )}
          <button type="button" className="btn" aria-expanded={showList} onClick={() => setShowList((x) => !x)}>
            {showList ? 'Hide details' : `Details${hazards.length ? ` · ${hazards.length} hazard${hazards.length > 1 ? 's' : ''}` : ''}`}
          </button>
          <button type="button" className={`btn btn-danger ${s.exit}`} onClick={exitNavigation}>Exit navigation</button>
        </div>
        {showList && (
          <div className={s.details}>
            {hazards.length > 0 && (
              <>
                <p className={s.detailsHead}>Flood hazards on the remaining route</p>
                <ul className={s.list}>
                  {hazards.map((h) => (
                    <li key={h.seg_id}>
                      <span style={{ color: ACCESS_STATUS[h.status]?.text }} aria-hidden="true">{h.glyph} </span>
                      <b>{h.name}</b> · {h.label}{h.detail ? ` – ${h.detail}` : ''}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {upcoming.length > 0 && (
              <>
                <p className={s.detailsHead}>After that</p>
                <ol className={s.list}>
                  {upcoming.slice(0, 8).map((m, i) => <li key={`${m.instruction}-${i}`}>{m.instruction}{m.type !== 'arrive' && m.distance_m ? ` – ${Math.round(m.distance_m)} m` : ''}</li>)}
                </ol>
              </>
            )}
            <p className={s.muted}>{view?.disclaimer || 'A route is a candidate with lower modelled flood exposure, never a guarantee of safety.'}</p>
          </div>
        )}
      </div>
    </div>
  )
}
