import { useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { PRIORITY_COLOR, PRIORITY_TEXT_COLOR, STATUS_COLOR, STATUS_GLYPH, STATUS_LABEL, STATUS_TEXT_COLOR, TRANSITIONS, dispatchLog } from '../../lib/response.js'
import { downloadJson, downloadText, ledgerForExport, toCsv } from '../../lib/exports.js'
import InfoTip from '../ui/InfoTip.jsx'
import shell from '../Shell/Shell.module.css'
import styles from './ResponsePanel.module.css'

// MISC-04 Phase 2: assign limited teams, vehicles and shelter places to prioritised settlements over arrival-aware
// missions; replan when roads, vehicles or shelters change; keep every decision. All numbers are SIMULATED demo
// data on REAL locations; the plan is decision support, never an order.

const NEXT_LABEL = { ACCEPTED: 'Accept', DISPATCHED: 'Dispatch', IN_PROGRESS: 'Arrived', COMPLETED: 'Complete', CANCELLED: 'Cancel' }
const fmtT = (t) => (t == null ? '—' : `T+${Number(t).toFixed(0)}`)

// Tinted chip: darkened text + border in the status hue on a light tint, with a glyph, so the state is readable
// without colour and every chip meets 4.5:1 (contrast.test.mjs).
function Chip({ color, text, glyph, children, title }) {
  return (
    <span className={styles.chip} style={{ color: text, borderColor: color, background: `${color}14` }} title={title}>
      {glyph && <span aria-hidden="true">{glyph}</span>}{children}
    </span>
  )
}

function Assignment({ a, onStatus, names, busy }) {
  const nexts = TRANSITIONS[a.status] || []
  return (
    <li className={styles.assignment} style={{ borderLeftColor: STATUS_COLOR[a.status] }}>
      <div className={styles.aHead}>
        <b>{a.assignment_id}</b>
        <Chip color={STATUS_COLOR[a.status]} text={STATUS_TEXT_COLOR[a.status]} glyph={STATUS_GLYPH[a.status]}>{STATUS_LABEL[a.status]}</Chip>
        <span className={styles.muted}>{a.people} people · {names.settlement[a.settlement_id]} → {names.shelter[a.shelter_id]}</span>
      </div>
      <div className={styles.muted}>{names.team[a.team_id]} + {names.vehicle[a.vehicle_id]}</div>
      <div className={styles.legs}>
        {(a.legs || []).map((l) => (
          <span key={l.kind}>{l.kind.replace('_', ' ')} {fmtT(l.arrive_t_min)}</span>
        ))}
        <span>done {fmtT(a.legs?.[a.legs.length - 1]?.done_t_min)}</span>
      </div>
      {a.attention && <div className={styles.attention}>⚠ {a.attention}</div>}
      {a.explanation && a.status === 'PROPOSED' && <div className={styles.why}>{a.explanation}</div>}
      {nexts.length > 0 && (
        <div className={styles.row}>
          {nexts.map((n) => (
            <button key={n} type="button" disabled={busy} title={busy ? 'A plan is being computed' : undefined} className={`btn ${n === nexts[0] && n !== 'CANCELLED' ? 'btn-primary' : ''} ${styles.small}`} onClick={() => onStatus(a.assignment_id, n)}>
              {NEXT_LABEL[n]}
            </button>
          ))}
        </div>
      )}
      <details className={styles.details}>
        <summary>History ({a.history?.length || 0})</summary>
        <ul className={styles.log}>
          {(a.history || []).map((h, i) => <li key={i}>{fmtT(h.t_min)} {h.status} · {h.by || 'operator'}{h.reason ? ` · ${h.reason}` : ''}</li>)}
        </ul>
      </details>
    </li>
  )
}

export default function ResponsePanel() {
  const { run, currentT, response, responseResources, runResponsePlan, setAssignment, addResponseEvent, roadEvents } = useFloodNet()
  const [reasonText, setReasonText] = useState('')
  const res = responseResources
  const r = response.result
  const st = r?.state
  const stale = Boolean(r && (r.sim_t_min !== currentT || (r.forecast?.run_id && run?.run_id && r.forecast.run_id !== run.run_id)))
  const names = res ? {
    settlement: Object.fromEntries(res.settlements.map((s) => [s.id, s.name])),
    shelter: Object.fromEntries(res.shelters.map((s) => [s.id, s.name])),
    team: Object.fromEntries(res.teams.map((s) => [s.id, s.name])),
    vehicle: Object.fromEntries(res.vehicles.map((s) => [s.id, s.name])),
  } : null
  const live = (response.ledger || []).filter((a) => a.status !== 'CANCELLED')
  const cancelled = (response.ledger || []).filter((a) => a.status === 'CANCELLED')
  const vehicleState = Object.fromEntries((st?.vehicles || []).map((v) => [v.id, v]))

  return (
    <section className={styles.section} data-tour="panel-response">
      <p className={styles.lead}>
        <span className="tag-badge tag-SYNTHETIC">SIMULATED</span> numbers on real MCGM / OSM places · decision support, not an order
        <InfoTip label="How the plan is built">
          Teams, vehicles and shelter places go to settlements by declared priority, only to teams with the required
          capabilities, with every leg routed for its expected arrival time.
        </InfoTip>
      </p>
      <div className={styles.row}>
        <button type="button" className="btn btn-primary" disabled={!run || !res || response.loading} onClick={runResponsePlan}>
          {response.loading ? 'Planning…' : r ? `Replan at ${fmtT(currentT)} min` : `Build plan at ${fmtT(currentT)} min`}
        </button>
      </div>
      {!res && run && <div className={styles.hint}>Demo resources not loaded (backend unavailable?).</div>}
      {response.error && <div className={styles.bad}>{response.error}</div>}
      {stale && <div className={styles.staleBand} role="status"><span aria-hidden="true">↻</span> Plan v{r.plan_version} was made at {fmtT(r.sim_t_min)}{r.forecast?.run_id && run?.run_id && r.forecast.run_id !== run.run_id ? ' on an earlier forecast' : ''}; the decision time is {fmtT(currentT)}. Replan to update.</div>}

      {r && (
        <>
          <div className={styles.tiles}>
            <div><b>{r.summary.people_needing_help}</b><span>need help</span></div>
            <div><b>{r.summary.served}</b><span>served</span></div>
            <div><b>{r.summary.covered_committed}</b><span>committed</span></div>
            <div><b>{r.summary.covered_proposed}</b><span>proposed</span></div>
            <div className={r.summary.unmet ? styles.tileBad : ''}><b>{r.summary.unmet}</b><span>unmet</span></div>
          </div>
          <div className={styles.muted}>Plan v{r.plan_version} at {fmtT(r.sim_t_min)} · replanned in {r.summary.replan_ms} ms · {r.mode === 'arrival' ? 'arrival-aware routing' : 'current-status routing (baseline)'}</div>
          {r.invariant_violations.length > 0 && (
            <div className={styles.bad}>Invariant violations: {r.invariant_violations.join('; ')}</div>
          )}
          {r.changes.length > 0 && (
            <>
              <div className={styles.subhead}>What changed in this plan</div>
              <ul className={styles.list}>
                {r.changes.map((c, i) => <li key={i}><b>{c.assignment_id}</b> {c.change}: <span className={styles.muted}>{c.reason}</span></li>)}
              </ul>
            </>
          )}
        </>
      )}

      {res && (
        // Demo resources stay folded away until a plan uses them (then they open, with their report buttons).
        <details className={shell.advanced} open={Boolean(r)}>
          <summary className={shell.advancedSummary}>
            <span>Settlements, shelters, teams and vehicles</span>
            <span className="tag-badge tag-SYNTHETIC">SIMULATED</span>
          </summary>
          <div className={shell.advancedBody}>
          <div className={styles.subhead}>Settlements</div>
          <ul className={styles.list}>
            {(st?.settlements || res.settlements).map((s) => (
              <li key={s.id}>
                <div className={styles.aHead}>
                  <Chip color={PRIORITY_COLOR[s.priority]} text={PRIORITY_TEXT_COLOR[s.priority]} title={s.priority_reason}>{s.priority}</Chip>
                  <b>{s.id} {s.name}</b>
                </div>
                <div className={styles.muted}>
                  {s.people_needing_help} people need {s.need}
                  {s.required_capabilities?.length ? ` · requires ${s.required_capabilities.join(', ')}` : ''}
                  {st ? ` · ${s.served} served · ${s.covered} covered · ${s.remaining} unmet` : ''}
                  {' '}· numbers {s.numbers_provenance.tag}
                </div>
                {r?.unmet?.[s.id] && (
                  <div className={styles.unmet}>
                    {r.unmet[s.id].people} people unserved: {r.unmet[s.id].reasons.map((x) => x.reason).join('; ')}
                    {r.unmet[s.id].reasons[0]?.detail && <span className={styles.muted}> ({r.unmet[s.id].reasons[0].detail})</span>}
                  </div>
                )}
              </li>
            ))}
          </ul>

          <div className={styles.subhead}>Shelters</div>
          <ul className={styles.list}>
            {(st?.shelters || res.shelters).map((sh) => {
              const free = sh.free ?? sh.total_capacity - sh.occupancy
              return (
                <li key={sh.id}>
                  <div className={styles.aHead}><b>{sh.id} {sh.name}</b></div>
                  <div className={styles.muted}>{sh.total_capacity} places · {sh.occupancy} occupied{st ? ` · ${sh.reserved} reserved` : ''} · <b>{free} free</b> · {sh.status}</div>
                  {r && free > 0 && (
                    <button type="button" className={`btn ${styles.small}`} onClick={() => addResponseEvent({ kind: 'shelter_update', shelter_id: sh.id, occupancy: sh.total_capacity, reason: 'reported full' })}>
                      Report full at {fmtT(currentT)}
                    </button>
                  )}
                </li>
              )
            })}
          </ul>

          <div className={styles.subhead}>Teams and vehicles</div>
          <ul className={styles.list}>
            {res.teams.map((t) => (
              <li key={t.id}><b>{t.id} {t.name}</b> <span className={styles.muted}>· {t.base} · {t.members} members · {t.capabilities.join(', ')}</span></li>
            ))}
            {res.vehicles.map((v) => {
              const vs = vehicleState[v.id]
              const available = vs ? vs.available : true
              return (
                <li key={v.id}>
                  <b>{v.id} {v.name}</b> <span className={styles.muted}>· {v.type} · {v.passenger_capacity} seats{vs ? ` · ${available ? `free from ${fmtT(vs.free_at_t_min)}` : `unavailable: ${vs.unavailable_reason}`}` : ''}</span>
                  {r && (
                    <button type="button" className={`btn ${styles.small}`}
                      onClick={() => addResponseEvent(available
                        ? { kind: 'vehicle_unavailable', vehicle_id: v.id, reason: reasonText || 'reported unavailable' }
                        : { kind: 'vehicle_available', vehicle_id: v.id })}>
                      {available ? 'Report unavailable' : 'Report available'}
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
          {r && (
            <input className={styles.input} value={reasonText} onChange={(e) => setReasonText(e.target.value)} placeholder="Reason for the next report (optional)" />
          )}
          {response.events.length > 0 && (
            <div className={styles.hint}>{response.events.length} resource report(s) logged; replan to apply those received by {fmtT(currentT)}.</div>
          )}
          </div>
        </details>
      )}

      {names && live.length > 0 && (
        <>
          <div className={styles.subhead}>Assignments</div>
          <ul className={styles.alist}>
            {live.map((a) => <Assignment key={a.assignment_id} a={a} onStatus={setAssignment} names={names} busy={response.loading} />)}
          </ul>
          {cancelled.length > 0 && (
            <details className={styles.details}>
              <summary>Cancelled ({cancelled.length})</summary>
              <ul className={styles.alist}>{cancelled.map((a) => <Assignment key={a.assignment_id} a={a} onStatus={setAssignment} names={names} busy={response.loading} />)}</ul>
            </details>
          )}
        </>
      )}

      {(response.ledger.length > 0 || response.planLogs.length > 0) && (
        <details className={styles.details}>
          <summary>Dispatch log</summary>
          <ul className={styles.log}>
            {dispatchLog(response.ledger, response.planLogs).map((row, i) => <li key={i}>{fmtT(row.t)} · {row.by} · {row.text}</li>)}
          </ul>
        </details>
      )}

      {r && (
        <>
          <div className={styles.subhead}>Export evidence</div>
          <div className={styles.row}>
            <button type="button" className={`btn ${styles.small}`} onClick={() => downloadJson('floodnet_ledger.json', { tag: 'SIMULATED demo', plan_version: response.version, sim_t_min: currentT, assignments: ledgerForExport(response.ledger) })}>Ledger (JSON)</button>
            <button type="button" className={`btn ${styles.small}`} onClick={() => downloadText('floodnet_dispatch_log.csv', toCsv(dispatchLog(response.ledger, response.planLogs).map((x) => ({ sim_t_min: x.t, by: x.by, entry: x.text })), ['sim_t_min', 'by', 'entry']), 'text/csv')}>Dispatch log (CSV)</button>
            <button type="button" className={`btn ${styles.small}`} onClick={() => downloadJson('floodnet_plan_versions.json', { tag: 'SIMULATED demo', versions: response.planHistory })}>Plan versions (JSON)</button>
            <button type="button" className={`btn ${styles.small}`} onClick={() => downloadJson('floodnet_events.json', { road_events: roadEvents, response_events: response.events })}>Event logs (JSON)</button>
          </div>
        </>
      )}



      {r && (
        <p className={styles.caveat}>
          Sim time {fmtT(r.sim_t_min)} min from the forecast start; processed at {r.processed_at} (clock time, kept separate).
          {' '}{r.disclaimer}
        </p>
      )}
    </section>
  )
}
