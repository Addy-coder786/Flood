import { useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { driverUrl, STAGE_LABEL, tripSummary } from '../../lib/ops.js'
import { isDirect, tripTitle } from '../../lib/rescueTrip.js'
import PageHeader, { Section, StateBand } from '../Shell/PageHeader.jsx'
import InfoTip from '../ui/InfoTip.jsx'
import styles from './Pages.module.css'
import trips from './Trips.module.css'

// Rescue trips (DECISIONS D-40..D-42). A trip is a dispatched assignment: start it here, give the driver link to the
// driver, follow the vehicle, and decide on route changes FloodNet proposes when roads close or flood risk changes.

const fmt = (t) => (t == null ? '–' : `T+${Math.round(t)}`)

export function OpsConnect({ ops, setToken }) {
  const [token, setTok] = useState('')
  if (ops.available) return null
  if (ops.available === null) return <StateBand kind="loading">Checking the shared incident store…</StateBand>
  return (
    <div className={trips.connect}>
      <StateBand kind="error">{ops.reason}</StateBand>
      <form className={trips.tokenRow} onSubmit={(e) => { e.preventDefault(); setToken(token); setTok('') }}>
        <span className={trips.tokenLabel}>
          <label className="field-label" htmlFor="dispatcher-token">Dispatcher token</label>
          <InfoTip label="About the dispatcher token">
            Set on the server as FLOODNET_DISPATCHER_TOKEN, or open FloodNet on the server machine. Kept for this browser
            tab only. Forecasts, roads and plans work without it.
          </InfoTip>
        </span>
        <input id="dispatcher-token" className="input" type="password" autoComplete="off" value={token}
          onChange={(e) => setTok(e.target.value)} />
        <button type="submit" className="btn" disabled={!token.trim()}>Connect</button>
      </form>
    </div>
  )
}

function Comparison({ c }) {
  if (!c?.candidate) return null
  const rows = [['Arrival (assumed road speeds)', fmt(c.current.eta_t_min), fmt(c.candidate.eta_t_min)],
    ['Arrival with MODELLED flood delay', fmt(c.current.eta_with_flood_delay_min), fmt(c.candidate.eta_with_flood_delay_min)],
    ['Roads closed or over the limit', c.current.blocking, c.candidate.blocking],
    ['Uncertain or unknown roads', c.current.uncertain, c.candidate.uncertain]]
  return (
    <table className={styles.table}>
      <thead><tr><th scope="col">Remaining route</th><th scope="col">Current</th><th scope="col">Proposed</th></tr></thead>
      <tbody>{rows.map(([k, a, b]) => <tr key={k}><th scope="row">{k}</th><td>{a}</td><td>{b}</td></tr>)}</tbody>
    </table>
  )
}

export function Proposal({ trip, p, onDecide, busy }) {
  const [why, setWhy] = useState('')
  return (
    <div className={`${trips.proposal} ${p.kind === 'REQUIRED' ? trips.required : ''}`} role="group" aria-label={`Route change ${p.proposal_id}`}>
      <div className={trips.proposalHead}>
        <strong>{p.kind === 'REQUIRED' ? '⚠ Route change required' : '◇ Route change suggested'}</strong>
        <span className={styles.small}>{p.proposal_id} · {fmt(p.created_t_min)} · trigger: {p.trigger?.kind || 'update'}</span>
      </div>
      <p className={styles.explain}>{p.reason}</p>
      <Comparison c={p.comparison} />
      <p className={styles.note}>Candidate route with lower modelled exposure — never a guarantee of safety.</p>
      <div className={trips.actions}>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onDecide(trip.trip_id, p.proposal_id, 'ACCEPT')}>Accept and send to driver</button>
        <input className="input" aria-label="Reason for rejecting" placeholder="Reason (if rejecting)" value={why} onChange={(e) => setWhy(e.target.value)} />
        <button type="button" className="btn" disabled={busy || !why.trim()} onClick={() => onDecide(trip.trip_id, p.proposal_id, 'REJECT', why)}>Reject</button>
      </div>
    </div>
  )
}

export function TripCard({ trip, link, fn }) {
  const s = tripSummary(trip)
  const [abortWhy, setAbortWhy] = useState('')
  const open = (trip.proposals || []).filter((p) => p.status === 'OPEN')
  const hazards = trip.last_assessment?.hazards_ahead || []
  const names = trip.names || {}
  const url = link ? driverUrl(window.location.origin, import.meta.env.BASE_URL, link) : null
  return (
    <article className={trips.card} aria-labelledby={`trip-${trip.trip_id}`}>
      <header className={trips.cardHead}>
        <h3 id={`trip-${trip.trip_id}`} className={trips.cardTitle}>{tripTitle(trip)}</h3>
        <span className={trips.stage}>{s.stage}</span>
      </header>
      <dl className={styles.kv}>
        <dt>Team / vehicle</dt><dd>{names.team || trip.team_id} · {names.vehicle || trip.vehicle_id} ({trip.vehicle_type})</dd>
        {isDirect(trip) ? (
          <><dt>Trip</dt><dd>Direct trip from the Roads page (no planner assignment)</dd></>
        ) : (
          <><dt>People</dt><dd>{trip.people}</dd>
            <dt>Assignment</dt><dd>{trip.assignment_id} (plan v{trip.plan_version}) · ledger {trip.ledger_status}</dd></>
        )}
        <dt>Location</dt><dd>{s.gps}</dd>
        <dt>Route</dt><dd>version {trip.route_version}{s.awaitingAck ? ' · waiting for the driver to acknowledge the new route' : ''}</dd>
      </dl>
      {s.attention && <p className={trips.attention} role="status">! {s.attention}</p>}
      {trip.escalation?.open && (
        <p className={styles.note}>No feasible route. Decide with the team: hold, wait, replan to another shelter, or request support.</p>
      )}
      {open.map((p) => <Proposal key={p.proposal_id} trip={trip} p={p} onDecide={fn.decideProposal} busy={fn.ops.busy} />)}
      {hazards.length > 0 && (
        <details className={trips.hazards}>
          <summary>{hazards.length} road(s) ahead that are not plainly usable</summary>
          <ul>{hazards.slice(0, 12).map((h) => <li key={`${h.seg_id}-${h.enter_t_min}`}>{h.name || h.seg_id}: {h.status} at {fmt(h.enter_t_min)} — {h.reason}</li>)}</ul>
        </details>
      )}
      <div className={trips.actions}>
        {url ? (
          <>
            <a className="btn" href={url} target="_blank" rel="noopener noreferrer">Open driver view</a>
            <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(url)}>Copy driver link</button>
          </>
        ) : (
          <button type="button" className="btn" disabled={fn.ops.busy} onClick={() => fn.newDriverLink(trip.trip_id)}>New driver link</button>
        )}
        <button type="button" className="btn" disabled={fn.ops.busy} onClick={() => fn.simulatePosition(trip.trip_id)} title="Demonstration only: records a SIMULATED GPS fix where the plan puts the vehicle now">Simulate GPS (demo)</button>
        <button type="button" className="btn" disabled={fn.ops.busy} onClick={() => fn.reassessTrip(trip.trip_id)}>Re-check route</button>
      </div>
      <details className={trips.hazards}>
        <summary>Finish or abort</summary>
        <div className={trips.actions}>
          <button type="button" className="btn" disabled={fn.ops.busy} onClick={() => fn.setTripStage(trip.trip_id, 'COMPLETED')}>Mark completed</button>
          <input className="input" aria-label="Reason for aborting" placeholder="Reason to abort" value={abortWhy} onChange={(e) => setAbortWhy(e.target.value)} />
          <button type="button" className="btn btn-danger" disabled={fn.ops.busy || !abortWhy.trim()} onClick={() => fn.setTripStage(trip.trip_id, 'ABORTED', abortWhy)}>Abort trip</button>
        </div>
      </details>
    </article>
  )
}

export default function TripsPage({ status, statusText }) {
  const fn = useFloodNet()
  const { ops, setToken, response, startTrip } = fn
  const active = ops.trips.filter((t) => !['COMPLETED', 'ABORTED'].includes(t.stage))
  const done = ops.trips.filter((t) => ['COMPLETED', 'ABORTED'].includes(t.stage))
  const withTrip = new Set(active.filter((t) => t.assignment_id).map((t) => t.assignment_id))
  const ready = (response.ledger || []).filter((a) => ['ACCEPTED', 'DISPATCHED'].includes(a.status) && !withTrip.has(a.assignment_id))
  return (
    <>
      <PageHeader pageKey="trips" status={status} statusText={statusText} />
      <OpsConnect ops={ops} setToken={setToken} />
      {ops.available && ops.error && <StateBand kind="error">{ops.error}</StateBand>}

      <Section title="Start a trip" hint="Accepted or dispatched assignments from the response plan. Starting a trip dispatches it and creates a link for the driver's phone.">
        {!ready.length ? <p className={styles.empty}>No accepted assignments yet.</p> : (
          <ul className={trips.readyList}>
            {ready.map((a) => (
              <li key={a.assignment_id} className={trips.readyRow}>
                <span><strong>{a.assignment_id}</strong> {a.settlement_id} → {a.shelter_id} · {a.team_id} + {a.vehicle_id} · {a.people} people · done {fmt(a.planned_done_t_min ?? a.legs?.slice(-1)[0]?.done_t_min)}</span>
                <button type="button" className="btn btn-primary" disabled={!ops.available || ops.busy} onClick={() => startTrip(a)}>Start trip</button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={`Active trips (${active.length})`} hint="Updated every 10 s. A route change is proposed when a road on the remaining route closes or floods for this vehicle; you decide, then the driver acknowledges. The driver page shares GPS only while it is open and on screen; stale or lost signals are shown as such and the planned position is used instead.">
        {!active.length ? <p className={styles.empty}>No active trip.</p>
          : active.map((t) => <TripCard key={t.trip_id} trip={t} link={ops.links[t.trip_id]} fn={fn} />)}
      </Section>

      {done.length > 0 && (
        <Section title={`Finished trips (${done.length})`} advanced>
          <ul className={trips.readyList}>
            {done.map((t) => <li key={t.trip_id}>{tripTitle(t)} · {STAGE_LABEL[t.stage]} · route v{t.route_version}</li>)}
          </ul>
        </Section>
      )}
    </>
  )
}
