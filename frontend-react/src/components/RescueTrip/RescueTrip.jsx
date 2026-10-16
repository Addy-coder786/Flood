import { useEffect, useMemo, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { buildLocationIndex, findLocationByCoords, PILOT_AREAS, searchLocations } from '../../lib/locations.js'
import { driverUrl } from '../../lib/ops.js'
import { ACCESS_STATUS } from '../../lib/roadAccess.js'
import {
  DIRECT_STAGES, etaRows, FLOODED_ROAD_VEHICLES, trafficSummary, freshnessChips, isDirect, lifecycle, positionText, ROUTE_STYLE, tripTitle,
  VEHICLE_LIMIT_CM, vehicleOptions,
} from '../../lib/rescueTrip.js'
import { tokenFromLink } from '../../lib/navigation.js'
import { LocationSearchField } from '../RoutePlanner/RoutePlanner.jsx'
import { OpsConnect, Proposal } from '../Pages/TripsPage.jsx'
import styles from './RescueTrip.module.css'

// Roads page rescue trip (DECISIONS D-49): pick a vehicle and two points, read the server's route preview (distance,
// ETA by labelled parts, hazards, data freshness), press Start Trip, then follow the trip: lifecycle, GPS, route
// changes when a road closes. Every number shown here comes from the backend; nothing is drawn or timed locally.

const fmtLonLat = (p) => (p ? `${p[0].toFixed(5)}, ${p[1].toFixed(5)}` : '')
const tt = (t) => (t == null ? '–' : `T+${Math.round(t)}`)

function Chip({ tone, children, title }) {
  return <span className={`${styles.chip} ${styles[`chip_${tone}`] || ''}`} title={title}>{children}</span>
}

function Lifecycle({ trip }) {
  const { steps, exception } = lifecycle(trip)
  return (
    <>
      <ol className={styles.steps} aria-label="Trip lifecycle">
        {steps.map((s) => (
          <li key={s.stage} className={`${styles.step} ${styles[`step_${s.state}`]}`} aria-current={s.state === 'current' ? 'step' : undefined}>
            <span className={styles.stepDot} aria-hidden="true" />{s.label}
            <span className="sr-only">{s.state === 'done' ? ' (done)' : s.state === 'current' ? ' (current)' : ' (not yet)'}</span>
          </li>
        ))}
      </ol>
      {exception && <p className={styles.exception} role="status">{exception}</p>}
    </>
  )
}

function ActiveTrip({ trip, link, fn, selected }) {
  const [abortWhy, setAbortWhy] = useState('')
  const open = (trip.proposals || []).filter((p) => p.status === 'OPEN')
  const url = link ? driverUrl(window.location.origin, import.meta.env.BASE_URL, link) : null
  const eta = trip.eta
  const next = DIRECT_STAGES[DIRECT_STAGES.indexOf(trip.stage) + 1] || null
  return (
    <article className={`${styles.trip} ${selected ? styles.tripSelected : ''}`} aria-labelledby={`rt-${trip.trip_id}`}>
      <header className={styles.tripHead}>
        <h3 id={`rt-${trip.trip_id}`} className={styles.tripTitle}>{tripTitle(trip)}</h3>
        <span className={styles.headBtns}>
          {!['COMPLETED', 'ABORTED'].includes(trip.stage) && (
            <button type="button" className={`btn btn-primary ${styles.sm}`} disabled={fn.ops.busy}
              title={link ? 'Follow this trip on this device with turn-by-turn guidance' : 'Creates a new driver link first (a phone using the old link must reopen it), then navigates here'}
              onClick={() => (link ? fn.startNavigation(trip.trip_id, tokenFromLink(link)) : fn.navigateWithNewLink(trip.trip_id))}>
              {link ? 'Navigate' : 'Navigate (new link)'}
            </button>
          )}
          <button type="button" className={`btn ${styles.sm}`} aria-pressed={selected} onClick={() => fn.selectTrip(selected ? null : trip.trip_id)}>
            {selected ? 'Shown on map' : 'Show on map'}
          </button>
        </span>
      </header>
      <Lifecycle trip={trip} />
      <dl className={styles.kv}>
        <dt>Vehicle</dt><dd>{trip.names?.vehicle || trip.vehicle_id || 'not tracked'} ({trip.vehicle_type})</dd>
        <dt>Location</dt><dd>{positionText(trip.position)}{!trip.position && trip.last_assessment?.position?.source === 'PLANNED_ESTIMATE' ? ' · position estimated from the plan' : ''}</dd>
        {eta && <><dt>ETA at start</dt><dd>{eta.eta_min.toFixed(1)} min (arrive {tt(eta.arrival_t_min)}){eta.flood_delay_min ? `, incl. +${eta.flood_delay_min.toFixed(1)} min MODELLED flood delay` : ''} · traffic UNKNOWN</dd></>}
        <dt>Route</dt><dd>version {trip.route_version}{trip.awaiting_ack ? ' · waiting for the driver to acknowledge the new route' : ''}
          {trip.last_assessment ? ` · checked ${tt(trip.last_assessment.assessed_t_min)}: ${trip.last_assessment.reason}` : ''}</dd>
      </dl>
      {trip.attention && <p className={styles.exception} role="status">! {trip.attention}</p>}
      {open.map((p) => <Proposal key={p.proposal_id} trip={trip} p={p} onDecide={fn.decideProposal} busy={fn.ops.busy} />)}
      {trip.escalation?.open && (
        <p className={styles.note}>No feasible route. Decide with the crew: hold, wait, change destination or vehicle, or request support.</p>
      )}
      <div className={styles.actions}>
        {url ? (
          <>
            <a className={`btn ${styles.sm}`} href={url} target="_blank" rel="noopener noreferrer">Open driver view</a>
            <button type="button" className={`btn ${styles.sm}`} onClick={() => navigator.clipboard?.writeText(url)}>Copy driver link</button>
          </>
        ) : (
          <button type="button" className={`btn ${styles.sm}`} disabled={fn.ops.busy} onClick={() => fn.newDriverLink(trip.trip_id)}>New driver link</button>
        )}
        <button type="button" className={`btn ${styles.sm}`} disabled={fn.ops.busy} onClick={() => fn.reassessTrip(trip.trip_id)}>Re-check route</button>
        <button type="button" className={`btn ${styles.sm}`} disabled={fn.ops.busy} onClick={() => fn.simulatePosition(trip.trip_id)}
          title="Demonstration only: records a SIMULATED GPS fix where the plan puts the vehicle now">Simulate GPS (demo)</button>
      </div>
      <details className={styles.more}>
        <summary>Record a stage for the driver, finish or abort</summary>
        <div className={styles.actions}>
          {next && (
            <button type="button" className={`btn ${styles.sm}`} disabled={fn.ops.busy} onClick={() => fn.setTripStage(trip.trip_id, next)}>
              Record “{next.replace('_', ' ').toLowerCase()}” (by dispatcher)
            </button>
          )}
          <input className="input" aria-label="Reason for aborting" placeholder="Reason to abort" value={abortWhy} onChange={(e) => setAbortWhy(e.target.value)} />
          <button type="button" className={`btn btn-danger ${styles.sm}`} disabled={fn.ops.busy || !abortWhy.trim()} onClick={() => fn.setTripStage(trip.trip_id, 'ABORTED', abortWhy)}>Abort trip</button>
        </div>
      </details>
    </article>
  )
}

function TrafficRow({ p, fn }) {
  const t = trafficSummary(p.traffic, p.eta)
  const live = t.mode === 'LIVE'
  return (
    <div className={`${styles.traffic} ${live ? styles.trafficLive : ''}`} role="group" aria-label="Live traffic">
      <div className={styles.trafficHead}>
        <Chip tone={live ? 'live' : 'warn'}>{live ? 'LIVE TRAFFIC' : 'TRAFFIC UNAVAILABLE'}</Chip>
        <span className={styles.trafficText}>{t.text}</span>
        <button type="button" className={`btn ${styles.sm}`} disabled={fn.tripPreview.loading || !fn.tripPreview.request}
          title="Fetch traffic again (bypasses the 60 s cache)" onClick={() => fn.previewTrip(fn.tripPreview.request, { refreshTraffic: true })}>
          {fn.tripPreview.loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      {live ? (
        <p className={styles.note}>With traffic <b>{t.withTraffic}</b> · without traffic {t.withoutTraffic} · updated {t.updated} · {t.coverage}. Traffic changes the ETA only; the route is chosen by flood risk.</p>
      ) : t.detail ? <p className={styles.note}>{t.detail}</p> : null}
    </div>
  )
}

function Preview({ p, fn, vehicleLabel, onStart, onStartNav }) {
  const chips = freshnessChips(p.freshness)
  const rows = etaRows(p.eta, p.freshness?.forecast)
  const escalate = p.decision === 'ESCALATE'
  const blocked = escalate || (p.vehicle?.id && p.vehicle.status !== 'AVAILABLE')
  return (
    <div className={styles.preview}>
      <div className={`${styles.decision} ${styles[`decision_${p.decision}`]}`} role={escalate ? 'alert' : 'status'}>
        <strong>{escalate ? 'No feasible route – escalate to a person' : p.decision === 'CAUTION' ? 'Candidate route, with cautions' : 'Candidate route'}</strong>
        <span>{p.decision_text}</span>
      </div>
      {!escalate && (
        <>
          <div className={styles.stats}>
            <div><span className={styles.statLabel}>ETA</span><span className={styles.statBig}>{p.eta.eta_min.toFixed(1)}<small> min</small></span></div>
            <div><span className={styles.statLabel}>Distance</span><span className={styles.statBig}>{(p.route.length_m / 1000).toFixed(2)}<small> km</small></span></div>
            <div><span className={styles.statLabel}>Arrives</span><span className={styles.statBig}>{tt(p.eta.arrival_t_min)}</span></div>
          </div>
          <table className={styles.etaTable}>
            <caption className="sr-only">ETA parts</caption>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <th scope="row"><Chip tone={r.tone}>{r.label}</Chip></th>
                  <td className={styles.etaValue}>{r.value}</td>
                  <td className={styles.etaNote}>{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <div className={styles.chips} aria-label="Data freshness">
        {chips.map((c) => <Chip key={c.key} tone={c.tone} title={c.detail}>{c.name}: {c.label}</Chip>)}
      </div>
      {!escalate && <TrafficRow p={p} fn={fn} />}
      {p.warnings?.length > 0 && <ul className={styles.warnings}>{p.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
      {!escalate && p.route.hazards.length > 0 && (
        <details className={styles.more} open={p.route.hazards.length <= 3}>
          <summary>{p.route.hazards.length} road(s) on this route are not plainly usable</summary>
          <ul className={styles.hazards}>
            {p.route.hazards.map((h) => (
              <li key={`${h.seg_id}-${h.enter_t_min}`}>
                <span style={{ color: ACCESS_STATUS[h.status]?.text }} aria-hidden="true">{ACCESS_STATUS[h.status]?.glyph} </span>
                <b>{h.name || h.seg_id}</b> · {ACCESS_STATUS[h.status]?.label || h.status} at {tt(h.enter_t_min)} — {h.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!escalate && p.eta.status_changes_when_delayed?.length > 0 && (
        <p className={styles.note}>If water slows the vehicle as modelled: {p.eta.status_changes_when_delayed.map((c) => `${c.name || c.seg_id} becomes ${c.status_delayed}`).join('; ')}.</p>
      )}
      {p.alternatives?.length > 0 && (
        <div className={styles.alts}>
          <span className={styles.altSwatch} style={{ borderColor: ROUTE_STYLE.alternative.color }} aria-hidden="true" />
          {p.alternatives.map((a) => (
            <span key={a.label}><b>Not chosen:</b> {a.label} ({(a.length_m / 1000).toFixed(2)} km, {a.travel_time_min.toFixed(1)} min) — {a.why_not}.</span>
          ))}
        </div>
      )}
      <button type="button" className={`btn btn-primary ${styles.start}`} disabled={blocked || fn.ops.busy} onClick={onStartNav}>
        {fn.ops.busy ? 'Starting…' : `Start navigation${vehicleLabel ? ` with ${vehicleLabel}` : ''}`}
      </button>
      <button type="button" className={styles.linkBtn} disabled={blocked || fn.ops.busy} onClick={onStart}
        title="Starts the trip and gives you a link for the driver's phone instead of navigating here">
        Dispatch to a driver instead
      </button>
      <p className={styles.disclaimer}>{p.disclaimer}</p>
    </div>
  )
}

export default function RescueTrip() {
  const fn = useFloodNet()
  const { ops, setToken, route, roads, run, currentT, tripPreview, previewTrip, clearTripPreview, setRouteOrigin, setRouteDest,
    setRouteVehicle, clearRoute, closeRoadMode, setCloseRoadMode, selectedTripId } = fn
  const { registry, types } = useMemo(() => vehicleOptions(ops.resources, ops.allTrips || ops.trips, VEHICLE_LIMIT_CM, fn.response?.ledger), [ops.resources, ops.allTrips, ops.trips, fn.response?.ledger])
  const [vehicle, setVehicle] = useState('')
  const firstFree = registry.find((o) => !o.disabled)?.value
  const sel = vehicle || firstFree || 'type:car'
  const chosen = sel.startsWith('type:') ? types.find((t) => t.value === sel) : registry.find((o) => o.value === sel)
  const vtype = chosen?.type || 'car'

  const index = useMemo(() => buildLocationIndex(roads, PILOT_AREAS), [roads])
  const [aText, setAText] = useState('')
  const [bText, setBText] = useState('')
  const aMatch = useMemo(() => findLocationByCoords(index, route.origin), [index, route.origin])
  const bMatch = useMemo(() => findLocationByCoords(index, route.dest), [index, route.dest])
  const aName = aMatch?.name || (chosen?.lonlat && route.origin && route.origin.join() === chosen.lonlat.join() ? `${chosen.value} base` : null)


  // preview whenever both points, the vehicle or the decision time change (debounced; the server clock follows the
  // timeline 400 ms after it moves)
  const ready = Boolean(ops.available && route.origin && route.dest && run)
  const req = useMemo(() => (ready ? {
    origin: route.origin, dest: route.dest,
    vehicleId: sel.startsWith('type:') ? null : sel, vehicleType: sel.startsWith('type:') ? vtype : null,
    names: { origin: aName || fmtLonLat(route.origin), destination: bMatch?.name || fmtLonLat(route.dest) },
  } : null), [ready, route.origin, route.dest, sel, vtype, aName, bMatch])
  useEffect(() => {
    if (!req) return undefined
    const id = setTimeout(() => previewTrip(req), 700)
    return () => clearTimeout(id)
  }, [req, currentT, previewTrip])
  // while live traffic is on, the ETA follows the provider's 60 s cache
  const liveTraffic = tripPreview.result?.traffic?.mode === 'LIVE'
  useEffect(() => {
    if (!req || !liveTraffic) return undefined
    const id = setInterval(() => { if (document.visibilityState === 'visible') previewTrip(req) }, 60_000)
    return () => clearInterval(id)
  }, [req, liveTraffic, previewTrip])

  const mine = ops.trips.filter((t) => isDirect(t) && !['COMPLETED', 'ABORTED'].includes(t.stage))
  // QA 2026-10-11 M3: only a preview made for the points and vehicle on screen now; an older one is never startable
  const current = Boolean(req && tripPreview.request && JSON.stringify(tripPreview.request) === JSON.stringify(req))
  const p = current ? tripPreview.result : null
  // a started trip takes its points with it: the next trip starts from a clean A/B (no second preview of the same
  // points offering the next free vehicle, which looked like an invitation to start a duplicate)
  const onStart = async () => { if (await fn.startDirectTrip()) clearRoute() }
  // Start navigation: the same trip, then this device follows it (the token comes back once with the new trip; a
  // repeated click on an existing trip makes a fresh driver link instead)
  const onStartNav = async () => {
    const out = await fn.startDirectTrip({ navigate: true })
    if (!out?.trip) return
    clearRoute()
    const tok = tokenFromLink(out.driver_link)
    if (tok) fn.startNavigation(out.trip.trip_id, tok)
    else fn.navigateWithNewLink(out.trip.trip_id)
  }
  const report = (spec, label) => setCloseRoadMode(closeRoadMode?.label === label ? false : { ...spec, label })

  return (
    <section className={styles.section} aria-labelledby="rescue-trip-h" data-tour="panel-trip">
      <div className={styles.head}>
        <h2 id="rescue-trip-h" className={styles.h2}>Rescue trip</h2>
        <span className={styles.headNote}>checked at arrival time · {p?.traffic?.mode === 'LIVE'
          ? <span className="tag-badge tag-REAL" title="Live traffic from TomTom on the sampled roads of this route">live traffic</span>
          : <span className="tag-badge tag-UNKNOWN" title="Live traffic unavailable; ETAs use estimated travel time">traffic not included</span>}</span>
      </div>
      <OpsConnect ops={ops} setToken={setToken} />
      {ops.available && ops.error && <p className={styles.error} role="alert">{ops.error}</p>}

      <div className={styles.field}>
        <label className="field-label" htmlFor="rt-vehicle">Vehicle</label>
        <select id="rt-vehicle" value={sel} onChange={(e) => {
          const v = e.target.value
          setVehicle(v)
          clearTripPreview()
          // the arrival check below uses the same vehicle type
          setRouteVehicle(v.startsWith('type:') ? v.slice(5) : registry.find((o) => o.value === v)?.type || 'car')
        }}>
          {registry.length > 0 && (
            <optgroup label="Registered vehicles">
              {registry.map((o) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>)}
            </optgroup>
          )}
          <optgroup label="Vehicle type only (not tracked)">
            {types.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </optgroup>
        </select>
        {chosen?.lonlat && (
          <button type="button" className={styles.linkBtn} onClick={() => setRouteOrigin(chosen.lonlat)}>Start from {chosen.value}&rsquo;s base</button>
        )}
      </div>

      <div className={styles.points}>
        <LocationSearchField id="rt-from" label="From (A)" placeholder="Search a street or landmark, or click the map" value={aText}
          onChange={setAText} results={searchLocations(index, aText)} onSelect={(loc) => { setRouteOrigin(loc.coords); setAText('') }}
          selectedLabel={route.origin ? `A: ${aName || fmtLonLat(route.origin)}` : null} />
        <LocationSearchField id="rt-to" label="To (B)" placeholder="Search a street or landmark, or click the map" value={bText}
          onChange={setBText} results={searchLocations(index, bText)} onSelect={(loc) => { setRouteDest(loc.coords); setBText('') }}
          selectedLabel={route.dest ? `B: ${bMatch?.name || fmtLonLat(route.dest)}` : null} />
      </div>
      <div className={styles.row}>
        <span className={styles.hint}>{!run ? 'Needs a forecast.' : !route.origin ? 'Click the map to set A, then B.' : !route.dest ? 'Now set B.' : tripPreview.loading ? 'Checking the route…' : `Decision time T+${currentT} min.`}</span>
        {(route.origin || route.dest) && <button type="button" className={styles.linkBtn} onClick={() => { clearRoute(); clearTripPreview() }}>Clear points</button>}
      </div>

      {current && tripPreview.error && <p className={styles.error} role="alert">{tripPreview.error}</p>}
      {p && <Preview p={p} fn={fn} onStart={onStart} onStartNav={onStartNav} vehicleLabel={chosen && !sel.startsWith('type:') ? chosen.value : vtype} />}

      <div className={styles.report} role="group" aria-label="Report a road problem on the map">
        <span className={styles.reportLabel}>Report on the map:</span>
        {[
          [{ kind: 'closure', tier: 'OPERATOR' }, 'Closed road'],
          [{ kind: 'closure', tier: 'OPERATOR', vehicleTypes: FLOODED_ROAD_VEHICLES, note: 'flooded road (depth not measured)' }, 'Flooded road'],
          [{ kind: 'closure', tier: 'OPERATOR', note: 'blocked access' }, 'Blocked access'],
        ].map(([spec, label]) => (
          <button key={label} type="button" className={`btn ${styles.sm} ${closeRoadMode?.label === label ? 'btn-primary' : ''}`}
            aria-pressed={closeRoadMode?.label === label} onClick={() => report(spec, label)}
            title={label === 'Flooded road' ? `Closes the road to ${FLOODED_ROAD_VEHICLES.join(' and ')} (30 cm limit, ASSUMED); taller vehicles keep the forecast check` : 'Closes the road to every vehicle'}>
            {closeRoadMode?.label === label ? 'Click a road…' : label}
          </button>
        ))}
      </div>

      {mine.length > 0 && (
        <div className={styles.tripList}>
          <h3 className={styles.h3}>Active trips ({mine.length})</h3>
          {mine.map((t) => <ActiveTrip key={t.trip_id} trip={t} link={ops.links[t.trip_id]} fn={fn} selected={selectedTripId === t.trip_id} />)}
        </div>
      )}
    </section>
  )
}
