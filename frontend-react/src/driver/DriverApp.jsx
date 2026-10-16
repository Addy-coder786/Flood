// Driver trip page (DECISIONS D-40, D-41, D-42; docs/LIVE_OPERATIONS.md §4-§5; research §5).
// Opened on a rescue driver's phone from `<base>driver#t=<token>`. The token moves to sessionStorage and leaves the
// address bar; every request sends it in the X-FloodNet-Token header. Location is shared only after an explicit
// consent press, only while this page is open and on screen (browsers stop GPS for hidden pages), at most once per
// `location_sharing.interval_s`. Polls the trip every 15 s while visible; keeps the last trip when offline.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiUrl } from '../api/client.js'
import {
  DRIVER_TOKEN_KEY, REPORT_KINDS, freshnessText, geoErrorMessage, geoSupport, hazardRows, isNewInstruction,
  completionQuestion, nextInstruction, offlineText, positionPayload, reportPayload, routeLines, shouldPost, stageButtons, stageStatus,
  tokenFromHash, upcomingManeuvers, upcomingRoads, formatDistance,
} from '../lib/driver.js'
import { formatISTTime } from '../lib/publicAlert.js'
import RouteSketch from './RouteSketch.jsx'
import s from './DriverApp.module.css'

const POLL_MS = 15_000
const TERMINAL = new Set(['COMPLETED', 'ABORTED'])
const DISCLAIMER = 'A route is a candidate with lower modelled flood exposure, never a guarantee of safety. '
  + 'Follow what you see and official instructions.'
const LEG_KIND = { pickup: 'Pickup point', to_settlement: 'Settlement', to_shelter: 'Shelter', to_destination: 'Destination' }
const GEO_OPTS = { enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000 }

// ---------------------------------------------------------------- token and API
function claimToken() {
  let token = null
  try {
    token = tokenFromHash(window.location.hash)
    if (window.location.hash) {
      // remove the token from the address bar and the history entry
      window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
    }
    if (token) window.sessionStorage.setItem(DRIVER_TOKEN_KEY, token)
    else token = window.sessionStorage.getItem(DRIVER_TOKEN_KEY) || null
  } catch { /* storage blocked: keep the token in memory for this page only */ }
  return token
}

async function driverCall(token, path, body) {
  const headers = { 'X-FloodNet-Token': token }
  if (body) headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch(apiUrl(path), { method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' })
  } catch {
    const e = new Error('No connection')
    e.offline = true
    throw e
  }
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (!res.ok) {
    const e = new Error(typeof data?.detail === 'string' ? data.detail : `The server answered ${res.status}`)
    e.status = res.status
    e.offline = res.status >= 500
    throw e
  }
  return data
}

const newClientRef = () => (globalThis.crypto?.randomUUID?.() || `r-${Date.now()}-${Math.random().toString(36).slice(2)}`)

// ---------------------------------------------------------------- location sharing (foreground only)
function useLocationSharing({ token, active, intervalS, onView, onError }) {
  const [sharing, setSharing] = useState(false)
  const [geoMsg, setGeoMsg] = useState(null)
  const [lastSentAt, setLastSentAt] = useState(null)
  const [wake, setWake] = useState('off')        // off | active | released | unsupported | failed
  const watchRef = useRef(null)
  const nudgeRef = useRef(null)
  const lockRef = useRef(null)
  const lastPostRef = useRef(null)
  const inflightRef = useRef(false)
  const sharingRef = useRef(false)

  const acquireWake = useCallback(() => {
    if (!navigator.wakeLock?.request) { setWake('unsupported'); return }
    navigator.wakeLock.request('screen').then((lock) => {
      lockRef.current = lock
      setWake('active')
      lock.addEventListener('release', () => setWake((w) => (w === 'active' ? 'released' : w)))
    }, () => setWake('failed'))
  }, [])

  const teardown = useCallback(() => {
    sharingRef.current = false
    if (watchRef.current != null) navigator.geolocation?.clearWatch(watchRef.current)
    watchRef.current = null
    clearInterval(nudgeRef.current)
    nudgeRef.current = null
    lockRef.current?.release?.().catch?.(() => {})
    lockRef.current = null
  }, [])

  const onPosition = useCallback((pos) => {
    setGeoMsg(null)
    const body = positionPayload(pos)
    const now = Date.now()
    if (!body || !sharingRef.current || inflightRef.current || !shouldPost(lastPostRef.current, now, intervalS)) return
    inflightRef.current = true
    lastPostRef.current = now
    driverCall(token, '/api/ops/driver/position', body)
      .then((v) => { setLastSentAt(Date.now()); onView(v) }, onError)
      .finally(() => { inflightRef.current = false })
  }, [token, intervalS, onView, onError])

  const onGeoError = useCallback((err) => setGeoMsg(geoErrorMessage(err)), [])

  const start = useCallback(() => {
    const support = geoSupport({ isSecureContext: window.isSecureContext, hasGeolocation: !!navigator.geolocation })
    if (!support.ok) { setGeoMsg(support.reason); return }
    sharingRef.current = true
    setSharing(true)
    setGeoMsg('Waiting for the phone to find your location…')
    watchRef.current = navigator.geolocation.watchPosition(onPosition, onGeoError, GEO_OPTS)
    // watchPosition may stay quiet while the vehicle stands still; ask again once per interval (errors ignored here)
    nudgeRef.current = setInterval(() => {
      if (document.visibilityState === 'visible') navigator.geolocation.getCurrentPosition(onPosition, () => {}, GEO_OPTS)
    }, Math.max(5, Number(intervalS) || 15) * 1000)
    acquireWake()
  }, [onPosition, onGeoError, intervalS, acquireWake])

  const stop = useCallback(() => {
    teardown()
    setSharing(false)
    setWake('off')
    setGeoMsg(null)
  }, [teardown])

  // The wake lock is released whenever the page is hidden; take it again when the driver comes back.
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'visible' && sharingRef.current) acquireWake() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [acquireWake])

  // Trip finished or link revoked: stop the GPS watch (the panel then shows sharing as stopped).
  useEffect(() => { if (!active) teardown() }, [active, teardown])
  useEffect(() => teardown, [teardown])

  return { sharing: sharing && active, geoMsg, lastSentAt, wake, start, stop }
}

// ---------------------------------------------------------------- voice (off by default)
function speak(instr) {
  if (!('speechSynthesis' in window)) return
  const dist = instr.distance ? `, then continue for ${instr.distance.replace(/ km$/, ' kilometres').replace(/ m$/, ' metres')}` : ''
  const u = new SpeechSynthesisUtterance(`${instr.instruction}${dist}.`)
  u.lang = 'en-IN'
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(u)
}

// ---------------------------------------------------------------- panels
function LocationPanel({ loc, view }) {
  const fr = freshnessText(view?.position)
  const note = view?.location_sharing?.note
  const wakeText = {
    active: 'Screen kept on while sharing.',
    released: 'The screen may turn off; keep this page open and on screen.',
    unsupported: 'This browser cannot keep the screen on; keep the phone awake yourself.',
    failed: 'The screen could not be kept on; keep the phone awake yourself.',
  }[loc.wake]
  return (
    <section className={s.card} aria-labelledby="loc-h">
      <h2 id="loc-h" className={s.h2}>Location sharing</h2>
      {!loc.sharing ? (
        <>
          <p className={s.small}>{note || 'Sent only while this page is open and on screen.'}</p>
          <button type="button" className={`${s.btn} ${s.btnPrimary} ${s.btnBlock}`} onClick={loc.start}>
            Share my location with the dispatcher during this trip
          </button>
        </>
      ) : (
        <>
          <p className={s.ok}><span aria-hidden="true">● </span>Sharing your location while this page is open.</p>
          <p className={s.small}>{loc.lastSentAt ? `Last sent at ${formatISTTime(loc.lastSentAt)}.` : 'Nothing sent yet.'} {wakeText}</p>
          <button type="button" className={`${s.btn} ${s.btnBlock}`} onClick={loc.stop}>Stop sharing my location</button>
        </>
      )}
      {loc.geoMsg && <p className={s.warn} role="status">{loc.geoMsg}</p>}
      <p className={s.small}><span aria-hidden="true">{fr.glyph} </span>Dispatcher sees: {fr.text}.</p>
    </section>
  )
}

function ReportPanel({ view, token, onView, onError }) {
  const [kind, setKind] = useState(null)
  const [segId, setSegId] = useState('')
  const [note, setNote] = useState('')
  const [pending, setPending] = useState(null)       // body of a report that failed to send (same client_ref on retry)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const roads = upcomingRoads(view)
  const k = REPORT_KINDS.find((x) => x.kind === kind)

  const send = async (body) => {
    setBusy(true)
    try {
      const out = await driverCall(token, '/api/ops/driver/report', body)
      if (out?.trip) onView(out.trip)
      setPending(null)
      setKind(null); setSegId(''); setNote('')
      setMsg({ ok: true, text: out?.road_event_id
        ? 'Report sent. The dispatcher sees it, and the road is recorded as closed for route checks.'
        : 'Report sent to the dispatcher.' })
    } catch (e) {
      onError(e)
      setPending(e.status === 401 ? null : body)
      setMsg({ ok: false, text: e.offline ? 'Not sent – no connection. Press "Send again" when you have signal.' : `Not sent: ${e.message}` })
    } finally {
      setBusy(false)
    }
  }

  const submit = (ev) => {
    ev.preventDefault()
    const p = reportPayload({ kind, segId, note, clientRef: newClientRef() })
    if (!p.ok) { setMsg({ ok: false, text: p.error }); return }
    send(p.body)
  }

  return (
    <section className={s.card} aria-labelledby="rep-h">
      <h2 id="rep-h" className={s.h2}>Report a problem</h2>
      <div className={s.reportGrid}>
        {REPORT_KINDS.map((r) => (
          <button key={r.kind} type="button" className={`${s.btn} ${kind === r.kind ? s.btnPressed : ''}`}
            aria-pressed={kind === r.kind} onClick={() => { setKind(kind === r.kind ? null : r.kind); setMsg(null) }}>
            {r.label}
          </button>
        ))}
      </div>
      {k && (
        <form className={s.form} onSubmit={submit}>
          {k.needsRoad && (
            <label className={s.label}>
              Which road?
              <select className={s.input} value={segId} onChange={(e) => setSegId(e.target.value)} required>
                <option value="">Choose a road ahead…</option>
                {roads.map((r) => <option key={r.seg_id} value={r.seg_id}>{r.label}</option>)}
              </select>
            </label>
          )}
          {!k.needsRoad && roads.length > 0 && (
            <label className={s.label}>
              Road (optional)
              <select className={s.input} value={segId} onChange={(e) => setSegId(e.target.value)}>
                <option value="">Not about one road</option>
                {roads.map((r) => <option key={r.seg_id} value={r.seg_id}>{r.label}</option>)}
              </select>
            </label>
          )}
          <label className={s.label}>
            Note (optional)
            <textarea className={s.input} rows={2} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <button type="submit" className={`${s.btn} ${s.btnPrimary} ${s.btnBlock}`} disabled={busy}>
            {busy ? 'Sending…' : `Send "${k.label}" report`}
          </button>
        </form>
      )}
      {msg && <p className={msg.ok ? s.ok : s.warn} role="status">{msg.text}</p>}
      {pending && (
        <button type="button" className={`${s.btn} ${s.btnBlock}`} disabled={busy} onClick={() => send(pending)}>Send again</button>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- page
function Shell({ children }) {
  return (
    <div className={s.page}>
      <p className={s.strip}>{DISCLAIMER}</p>
      <main className={s.main}>
        <p className={s.brand}>FloodNet – rescue trip</p>
        {children}
      </main>
    </div>
  )
}

export default function DriverApp() {
  const [token] = useState(claimToken)
  const [view, setView] = useState(null)
  const [phase, setPhase] = useState(token ? 'loading' : 'notoken')   // loading | ready | invalid | notoken
  const [offline, setOffline] = useState(false)
  const [lastOkAt, setLastOkAt] = useState(null)
  const [actionMsg, setActionMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [voice, setVoice] = useState(false)
  const spokenRef = useRef(null)

  useEffect(() => { document.title = 'FloodNet – rescue trip' }, [])

  const applyView = useCallback((v) => {
    if (!v || !v.trip_id) return
    setView(v)
    setLastOkAt(Date.now())
    setOffline(false)
    setPhase('ready')
  }, [])

  const handleError = useCallback((e) => {
    if (e?.status === 401) { setPhase('invalid'); return }
    if (e?.offline) setOffline(true)
  }, [])

  const invalid = phase === 'invalid'
  useEffect(() => {
    if (!token || invalid) return undefined
    let alive = true
    const tick = () => {
      if (document.visibilityState !== 'visible') return
      driverCall(token, '/api/ops/driver/trip').then((v) => { if (alive) applyView(v) }, (e) => { if (alive) handleError(e) })
    }
    tick()
    const timer = setInterval(tick, POLL_MS)
    const onVis = () => { if (document.visibilityState === 'visible') tick() }
    document.addEventListener('visibilitychange', onVis)
    return () => { alive = false; clearInterval(timer); document.removeEventListener('visibilitychange', onVis) }
  }, [token, invalid, applyView, handleError])

  const active = phase === 'ready' && !!view && !TERMINAL.has(view.stage)
  const loc = useLocationSharing({ token, active, intervalS: view?.location_sharing?.interval_s, onView: applyView, onError: handleError })

  const instr = useMemo(() => nextInstruction(view), [view])
  useEffect(() => {
    if (!voice || !instr || !isNewInstruction(spokenRef.current, instr)) return
    spokenRef.current = instr
    speak(instr)
  }, [voice, instr])

  const act = async (path, body, okText) => {
    setBusy(true)
    setActionMsg(null)
    try {
      applyView(await driverCall(token, path, body))
      if (okText) setActionMsg({ ok: true, text: okText })
    } catch (e) {
      handleError(e)
      if (e.status !== 401) setActionMsg({ ok: false, text: e.offline ? 'Not sent – no connection. Try again.' : `Not accepted: ${e.message}` })
    } finally {
      setBusy(false)
    }
  }

  const onStage = (b) => {
    if (b.confirm && !window.confirm(completionQuestion(view))) return
    act('/api/ops/driver/stage', { stage: b.stage }, `Recorded: ${b.label}.`)
  }

  const toggleVoice = () => {
    if (voice) { window.speechSynthesis?.cancel(); spokenRef.current = null }
    setVoice(!voice)
  }

  if (phase === 'notoken') {
    return <Shell><h1 className={s.h1}>Ask the dispatcher for a new driver link</h1>
      <p>This page needs the driver link sent by the dispatcher for your trip.</p></Shell>
  }
  if (phase === 'invalid') {
    return <Shell><h1 className={s.h1}>This driver link is no longer valid; ask the dispatcher for a new one</h1>
      <p>Location sharing has stopped. Nothing more is sent from this page.</p></Shell>
  }
  if (!view) {
    return (
      <Shell>
        <p role="status">{offline ? offlineText(null) : 'Loading your trip…'}</p>
      </Shell>
    )
  }

  const buttons = stageButtons(view)
  const hazards = hazardRows(view)
  const upcoming = upcomingManeuvers(view)
  const leg = view.remaining?.[0]
  const legs = (view.remaining || []).map((l) => ({ to: l.to, coords: routeLines({ remaining: [l] })[0] })).filter((l) => l.coords)
  const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window
  const done = TERMINAL.has(view.stage)

  return (
    <Shell>
      {offline && <p className={s.offline} role="status"><span aria-hidden="true">⚠ </span>{offlineText(lastOkAt)}</p>}

      <h1 className={s.h1}>{stageStatus(view.stage)}</h1>
      <p className={s.small}>{[view.vehicle, view.team].filter(Boolean).join(' · ')}{view.route_version > 1 ? ` · route version ${view.route_version}` : ''}</p>

      {view.escalation && (
        <div className={`${s.banner} ${s.bannerStop}`} role="alert">
          <p><strong><span aria-hidden="true">⛔ </span>Stop and contact the dispatcher:</strong> {view.escalation}</p>
        </div>
      )}

      {view.route_change && (
        <div className={`${s.banner} ${s.bannerChange}`}>
          <p><strong><span aria-hidden="true">↻ </span>{view.route_change.kind === 'REQUIRED' ? 'Route changed by the dispatcher' : 'New route suggested and approved by the dispatcher'}</strong></p>
          <p>Reason: {view.route_change.reason}. Instructions below follow the new route.</p>
          <button type="button" className={`${s.btn} ${s.btnPrimary} ${s.btnBlock}`} disabled={busy}
            onClick={() => act('/api/ops/driver/ack', { proposal_id: view.route_change.proposal_id }, 'New route acknowledged.')}>
            Acknowledge new route
          </button>
        </div>
      )}

      {!done && instr && (
        <section className={s.next} aria-labelledby="next-h">
          <h2 id="next-h" className={s.nextLabel}>Next</h2>
          <p className={s.nextText}>{instr.instruction}</p>
          {instr.distance && <p className={s.nextDist}>for {instr.distance}</p>}
          {instr.then && <p className={s.nextThen}>Then: {instr.then}</p>}
          {canSpeak && (
            <button type="button" className={`${s.btn} ${s.btnSmall}`} aria-pressed={voice} onClick={toggleVoice}>
              {voice ? 'Stop reading instructions aloud' : 'Read instructions aloud'}
            </button>
          )}
        </section>
      )}

      {leg && (
        <section className={s.card} aria-labelledby="dest-h">
          <h2 id="dest-h" className={s.h2}>Going to</h2>
          <p className={s.dest}>{leg.to || 'Next stop'} <span className={s.small}>({LEG_KIND[leg.kind] || 'Stop'})</span></p>
          {view.kind === 'direct' ? (
            <p>{view.origin ? `From ${view.origin}` : ''}{view.destination ? ` · to ${view.destination}` : ''}
              {view.eta ? ` · ETA at start ${view.eta.eta_min.toFixed(0)} min (modelled; traffic not included)` : ''}</p>
          ) : (
            <p>{view.people != null ? `${view.people} people to move` : 'Number of people not given'}
              {view.settlement ? ` · from ${view.settlement}` : ''}{view.shelter ? ` · to ${view.shelter}` : ''}</p>
          )}
        </section>
      )}

      <section className={s.card} aria-labelledby="haz-h">
        <h2 id="haz-h" className={s.h2}>Hazards ahead</h2>
        {hazards.length ? (
          <ul className={s.list}>
            {hazards.map((h, i) => (
              <li key={`${h.seg_id}-${i}`} className={s.hazard}>
                <span className={s.hazGlyph} aria-hidden="true">{h.glyph}</span>
                <span><strong>{h.label}: {h.name}</strong>{h.detail ? <span className={s.small}> – {h.detail}</span> : null}</span>
              </li>
            ))}
          </ul>
        ) : <p>None flagged at the last check. Watch the road.</p>}
      </section>

      {upcoming.length > 0 && (
        <section className={s.card} aria-labelledby="man-h">
          <h2 id="man-h" className={s.h2}>After that</h2>
          <ol className={s.list}>
            {upcoming.map((m, i) => (
              <li key={`${m.seg_index}-${i}`}>{m.instruction}{m.type !== 'arrive' && m.distance_m ? <span className={s.small}> – {formatDistance(m.distance_m)}</span> : null}</li>
            ))}
          </ol>
        </section>
      )}

      {legs.length > 0 && <section className={s.card} aria-label="Route sketch"><RouteSketch legs={legs} position={view.position} /></section>}

      {buttons.length > 0 && (
        <section className={s.card} aria-labelledby="stage-h">
          <h2 id="stage-h" className={s.h2}>Trip progress</h2>
          <div className={s.stageGrid}>
            {buttons.map((b) => (
              <button key={b.stage} type="button" disabled={busy} onClick={() => onStage(b)}
                className={`${s.btn} ${s.btnBlock} ${b.primary ? s.btnPrimary : ''}`}>
                {b.label}
              </button>
            ))}
          </div>
        </section>
      )}
      {actionMsg && <p className={actionMsg.ok ? s.ok : s.warn} role="status">{actionMsg.text}</p>}

      {!done && <ReportPanel view={view} token={token} onView={applyView} onError={handleError} />}
      {!done && <LocationPanel loc={loc} view={view} />}
      {done && <p className={s.card}>This trip is finished. Location sharing has stopped.</p>}
    </Shell>
  )
}
