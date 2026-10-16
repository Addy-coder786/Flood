import { useMemo } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { ACCESS_STATUS, ACCESS_STATUS_ORDER, CURRENT_STATUS_ROUTE_COLOR, TIER_LABEL, activeClosures, unconfirmedClosures } from '../../lib/roadAccess.js'
import InfoTip from '../ui/InfoTip.jsx'
import styles from './ArrivalAccess.module.css'

// MISC-04 Phase 1: is each road still usable at the minute the vehicle reaches it? Compares the arrival-aware
// route with a current-status-only route (the baseline) for the A/B points and vehicle chosen in the Route
// planner above. Dispatch time = the timeline. Every status is decided by the backend (floodnet/access/).

const fmtMin = (m) => (m == null ? '—' : `${m.toFixed(1)} min`)

function RouteCard({ title, color, dashed, block }) {
  if (!block) return null
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>
        <span className={styles.swatch} style={{ borderColor: color, borderStyle: dashed ? 'dashed' : 'solid' }} />
        {title}
      </div>
      {!block.reachable ? (
        <div className={styles.bad}>No route{block.unreachable_reason ? `: ${block.unreachable_reason}` : ''}</div>
      ) : (
        <>
          <div className={styles.kv}><span>Travel time</span><b>{fmtMin(block.travel_time_min)}</b></div>
          <div className={styles.kv}><span>Arrives</span><b>T+{block.arrival_t_min.toFixed(1)} min</b></div>
          <div className={styles.kv}><span>Length</span><b>{(block.length_m / 1000).toFixed(2)} km</b></div>
          <div className={block.valid_at_arrival ? styles.good : styles.bad}>
            {block.valid_at_arrival ? 'No closed or at-risk road on arrival' : `${block.blocked_at_arrival.length} road(s) closed or at risk on arrival`}
          </div>
          <div className={styles.chips}>
            {ACCESS_STATUS_ORDER.filter((k) => block.status_counts_at_arrival[k]).map((k) => (
              <span key={k} className={styles.chip} style={{ color: ACCESS_STATUS[k].text, borderColor: ACCESS_STATUS[k].color, background: `${ACCESS_STATUS[k].color}14` }} title={ACCESS_STATUS[k].hint}>
                <span aria-hidden="true">{ACCESS_STATUS[k].glyph}</span> {ACCESS_STATUS[k].label} {block.status_counts_at_arrival[k]}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

export default function ArrivalAccess() {
  const {
    route, run, currentT, access, planArrivalAccess, roadEvents, reopenRoad, closeRoadMode, setCloseRoadMode, setActiveRightTab,
  } = useFloodNet()
  const r = access.result
  const closures = useMemo(() => [...activeClosures(roadEvents, currentT).values()], [roadEvents, currentT])
  const unconfirmed = useMemo(() => [...unconfirmedClosures(roadEvents, currentT).values()], [roadEvents, currentT])
  const pending = roadEvents.filter((e) => e.received_t_min > currentT).length
  const canCheck = Boolean(route.origin && route.dest && run)

  return (
    <section className={styles.section} data-tour="panel-access">
      <div className={styles.headRow}>
        <h2 className={styles.h2}>Arrival-time check</h2>
        <InfoTip label="About the arrival-time check">
          Checks each road at the minute the vehicle is expected to reach it and compares that with a route planned on
          dispatch-time conditions (the baseline). Statuses are model estimates, not observations; a confirmed closure
          stays in force until a reopening is reported.
        </InfoTip>
      </div>

      <div className={styles.row}>
        <button type="button" className="btn btn-primary" disabled={!canCheck || access.loading}
          onClick={() => planArrivalAccess({ origin: route.origin, dest: route.dest, vehicle: route.vehicle || 'car' })}>
          {access.loading ? 'Checking…' : `Check access at decision time T+${currentT} min`}
        </button>
      </div>
      {run && !(route.origin && route.dest) && <div className={styles.hint}>Set A and B above first.</div>}
      {access.error && <div className={styles.bad}>{access.error}</div>}

      <div className={styles.subhead}>Confirmed road closures</div>
      <div className={styles.row}>
        <button type="button" className={`btn ${closeRoadMode ? 'btn-primary' : ''}`} aria-pressed={Boolean(closeRoadMode)}
          onClick={() => setCloseRoadMode(closeRoadMode ? false : { kind: 'closure', tier: 'OPERATOR' })}>
          {closeRoadMode ? 'Click a road on the map…' : 'Report a closure (operator)'}
        </button>
        <button type="button" className="btn" onClick={() => setActiveRightTab('reports')}>All reports and tiers</button>
      </div>
      {closures.length === 0 ? (
        <div className={styles.hint}>No closure in force at T+{currentT} min.</div>
      ) : (
        <ul className={styles.list}>
          {closures.map((e) => (
            <li key={e.event_id}>
              <span><b>{e.name || e.seg_id}</b> · {e.event_id}, {TIER_LABEL[e.source_tier] || 'Operator'}, received T+{e.received_t_min} min{e.stale ? ` · stale ${Math.round(e.age_min)} min, confirm` : ''}{e.contested_by?.length ? ` · contested by ${e.contested_by.join(', ')}` : ''}</span>
              <button type="button" className={styles.linkBtn} onClick={() => reopenRoad(e.seg_id, e.name)}>Reopen</button>
            </li>
          ))}
        </ul>
      )}
      {unconfirmed.length > 0 && (
        <div className={styles.hint}>
          {unconfirmed.length} unconfirmed public report(s) of closure ({unconfirmed.map((e) => e.name || e.seg_id).join(', ')}): treated as Uncertain, not closed, until an operator confirms.
        </div>
      )}
      {pending > 0 && <div className={styles.hint}>{pending} report(s) arrive later than T+{currentT} min and are not used yet.</div>}
      {roadEvents.length > 0 && (
        <details className={styles.details}>
          <summary>Event log ({roadEvents.length})</summary>
          <ul className={styles.log}>
            {roadEvents.map((e) => (
              <li key={e.event_id}>{e.event_id} · {e.kind} · {e.name || e.seg_id} · received T+{e.received_t_min} min · {TIER_LABEL[e.source_tier] || 'Operator'}</li>
            ))}
          </ul>
        </details>
      )}

      {r && (
        <>
          <div className={styles.subhead}>Result for dispatch at T+{r.dispatch_t_min} min ({r.vehicle}, {r.vehicle_limit_cm} cm limit)</div>
          <div className={styles.summary}>{r.comparison.summary}</div>
          <div className={styles.cards}>
            <RouteCard title="Arrival-aware" color={ACCESS_STATUS.USABLE.color} block={r.arrival_aware} />
            <RouteCard title="Current status only" color={CURRENT_STATUS_ROUTE_COLOR} dashed block={r.current_status} />
          </div>
          {r.current_status?.blocked_at_arrival?.length > 0 && (
            <>
              <div className={styles.subhead}>Roads the current-status route would meet</div>
              <ul className={styles.list}>
                {r.current_status.blocked_at_arrival.map((s) => (
                  <li key={s.seg_id}>
                    <span><b>{s.name || s.seg_id}</b> · {ACCESS_STATUS[s.status]?.label} at T+{s.enter_t_min.toFixed(1)} min</span>
                    <span className={styles.muted}>{s.reason}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {r.events_ignored_future.length > 0 && (
            <div className={styles.hint}>{r.events_ignored_future.length} later report(s) ignored: not received by the decision time.</div>
          )}
          <div className={styles.legend}>
            {ACCESS_STATUS_ORDER.map((k) => (
              <span key={k} title={ACCESS_STATUS[k].hint}><i style={{ background: ACCESS_STATUS[k].color }} /><span aria-hidden="true">{ACCESS_STATUS[k].glyph}</span> {ACCESS_STATUS[k].label}</span>
            ))}
          </div>
          <p className={styles.caveat}>
            <span className="tag-badge tag-SYNTHETIC">SIMULATED</span> forecast ·{' '}
            <span className="tag-badge tag-UNKNOWN">ASSUMED</span> speeds ({r.assumptions.default_speed_kmh} km/h default, no slowdown in water)
            <br />{r.disclaimer}
          </p>
        </>
      )}
    </section>
  )
}
