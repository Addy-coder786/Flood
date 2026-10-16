import { useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { downloadJson } from '../../lib/exports.js'
import styles from './ResponsePanel.module.css'

// MISC-04 Phase 3: scripted replay with both routing methods, planned on the run on screen and judged against a
// second, independent run (the "outcome"). SIMULATED evidence; the numbers come from running it, nothing is typed in.

const OUTCOME_CHOICES = ['cloudburst', 'heavy', 'moderate', 'july2005']
// Fixed demo script, or reproducible seeded sequences (development seeds; held-out seeds are reserved for the
// offline report, scripts/response_eval.py --held-out, so nothing is tuned on them here).
const SCRIPT_CHOICES = [
  { key: 'demo', label: 'Fixed demo script', seeds: null },
  { key: 'seeds3', label: '3 seeded sequences (dev seeds 0–2)', seeds: [0, 1, 2] },
  { key: 'seeds5', label: '5 seeded sequences (dev seeds 0–4)', seeds: [0, 1, 2, 3, 4] },
]
const AGG_ROWS = [
  ['people_served_by_horizon', 'People served by horizon', 0],
  ['people_unmet_at_end', 'People unmet at end', 0],
  ['legs_invalid_by_outcome', 'Legs invalid by outcome', 0],
  ['missions_invalid_by_outcome', 'Missions invalid by outcome', 0],
  ['known_closure_violations', 'Known-closure violations (target 0)', 0],
  ['invariant_violations_total', 'Double-booking + overflow (target 0)', 0],
]
const fmtAgg = (a, digits = 1) => (a ? `${a.mean.toFixed(digits)} (${a.min}–${a.max})` : '—')

function SeedsResult({ r }) {
  const agg = r.aggregate || {}
  const pod = (m) => agg[`prediction.${m}.probability_of_detection`]
  const far = (m) => agg[`prediction.${m}.false_alarm_ratio`]
  const p = (a) => (a ? `${(a.mean * 100).toFixed(1)}% (${(a.min * 100).toFixed(0)}–${(a.max * 100).toFixed(0)}%)` : '—')
  return (
    <>
      <div className={styles.muted}>
        {r.per_seed.length} sequences, seeds {r.per_seed.map((s) => s.seed).join(', ')}. Values: mean (min–max) over sequences.
        {' '}Information check: {r.information_check?.ok ? 'no input was used before it was received' : 'VIOLATIONS FOUND, see JSON'}.
      </div>
      <table className={styles.table}>
        <thead><tr><th>Metric (SIMULATED)</th><th>Arrival-aware</th><th>Current status</th></tr></thead>
        <tbody>
          {AGG_ROWS.map(([k, label]) => (
            <tr key={k}><td>{label}</td><td>{fmtAgg(agg[`${k}.arrival_aware`])}</td><td>{fmtAgg(agg[`${k}.current_status`])}</td></tr>
          ))}
          <tr><td>Blocked roads detected (POD)</td><td>{p(pod('arrival_aware'))}</td><td>{p(pod('current_status'))}</td></tr>
          <tr><td>False alarm ratio</td><td>{p(far('arrival_aware'))}</td><td>{p(far('current_status'))}</td></tr>
        </tbody>
      </table>
    </>
  )
}
const pct = (x) => (x == null ? '—' : `${(x * 100).toFixed(1)}%`)

function Row({ label, a, c, better }) {
  const cls = better && a != null && c != null && a !== c ? (better(a, c) ? styles.good : styles.bad) : ''
  return (
    <tr><td>{label}</td><td className={cls}>{a ?? '—'}</td><td>{c ?? '—'}</td></tr>
  )
}

export default function EvaluationSection() {
  const { run, scenarioId, response, evaluateResponse } = useFloodNet()
  const ev = response.evaluation
  const [outcome, setOutcome] = useState(() => OUTCOME_CHOICES.find((s) => s !== scenarioId) || 'cloudburst')
  const [scriptKey, setScriptKey] = useState('demo')
  const seeds = SCRIPT_CHOICES.find((c) => c.key === scriptKey)?.seeds ?? null
  const r = ev.result
  const cmp = r?.comparison
  const pq = r?.prediction

  return (
    <>
      <div className={styles.subhead}>Evaluation: arrival-aware vs current-status routing</div>
      <p className={styles.lead}>
        {seeds
          ? `Seeded sequences ${seeds.join(', ')}: reproducible closures, vehicle losses and shelter updates.`
          : 'Fixed demo script: a closure at T+10, a vehicle lost and a shelter full at T+30.'}
      </p>
      <div className={styles.evalForm}>
        <label>
          <span className="field-label">Outcome run (what &ldquo;really&rdquo; happens, simulated)</span>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome scenario">
            {OUTCOME_CHOICES.map((s) => <option key={s} value={s}>{s === scenarioId ? `${s} (same run: sanity check only)` : s}</option>)}
          </select>
        </label>
        <label>
          <span className="field-label">Incident script</span>
          <select value={scriptKey} onChange={(e) => setScriptKey(e.target.value)} aria-label="Incident script">
            {SCRIPT_CHOICES.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
        </label>
      </div>
      <div className={styles.row}>
        <button type="button" className="btn btn-primary" disabled={!run || ev.loading} onClick={() => evaluateResponse(outcome, seeds)}>
          {ev.loading ? 'Simulating outcome and replaying…' : 'Run evaluation'}
        </button>
      </div>
      {ev.loading && <div className={styles.hint}>Runs a second simulation for the outcome scenario (about a minute), then two replays.</div>}
      {ev.error && <div className={styles.bad}>{ev.error}</div>}
      {r?.per_seed && <SeedsResult r={r} />}
      {r?.per_seed && (
        <div className={styles.row}>
          <button type="button" className={`btn ${styles.small}`} onClick={() => downloadJson('floodnet_evaluation_seeds.json', r)}>Evaluation (JSON)</button>
        </div>
      )}
      {r && !r.per_seed && (
        <>
          {r.same_run && <div className={styles.bad}>Forecast and outcome are the same run: arrival-aware routing is right by construction here. Choose a different outcome scenario for a fair comparison.</div>}
          <table className={styles.table}>
            <thead><tr><th>Metric (SIMULATED)</th><th>Arrival-aware</th><th>Current status</th></tr></thead>
            <tbody>
              <Row label="People served by horizon" a={cmp.people_served_by_horizon.arrival_aware} c={cmp.people_served_by_horizon.current_status} better={(a, c) => a > c} />
              <Row label="People unmet at end" a={cmp.people_unmet_at_end.arrival_aware} c={cmp.people_unmet_at_end.current_status} better={(a, c) => a < c} />
              <Row label="Missions dispatched" a={cmp.missions_dispatched.arrival_aware} c={cmp.missions_dispatched.current_status} />
              <Row label="Legs invalid by outcome" a={cmp.legs_invalid_by_outcome.arrival_aware} c={cmp.legs_invalid_by_outcome.current_status} better={(a, c) => a < c} />
              <Row label="Missions invalid by outcome" a={cmp.missions_invalid_by_outcome.arrival_aware} c={cmp.missions_invalid_by_outcome.current_status} better={(a, c) => a < c} />
              <Row label="Known-closure violations (target 0)" a={cmp.known_closure_violations.arrival_aware} c={cmp.known_closure_violations.current_status} />
              <Row label="Closures met that were reported later" a={cmp.closures_encountered.arrival_aware} c={cmp.closures_encountered.current_status} />
              <Row label="Last completion (T+min)" a={cmp.last_completion_t_min.arrival_aware} c={cmp.last_completion_t_min.current_status} />
              <Row label="Replan time, mean (ms)" a={cmp.replan_ms_mean.arrival_aware} c={cmp.replan_ms_mean.current_status} />
              <Row label="Double-booking + overflow (target 0)" a={cmp.invariant_violations_total.arrival_aware} c={cmp.invariant_violations_total.current_status} />
            </tbody>
          </table>
          {Object.keys(cmp.response_delay_min).length > 0 && (
            <div className={styles.muted}>First service delay, current-status minus arrival-aware (min): {Object.entries(cmp.response_delay_min).map(([k, v]) => `${k} ${v > 0 ? '+' : ''}${v}`).join(' · ')}</div>
          )}
          <div className={styles.subhead}>Road-access prediction on all {pq.segments} roads ({pq.vehicle}, leads {pq.lead_min.join('/')} min)</div>
          <table className={styles.table}>
            <thead><tr><th></th><th>Arrival-aware</th><th>Current status</th></tr></thead>
            <tbody>
              <Row label="Accuracy" a={pct(pq.arrival_aware.accuracy)} c={pct(pq.current_status.accuracy)} />
              <Row label="Blocked roads detected (POD)" a={pct(pq.arrival_aware.probability_of_detection)} c={pct(pq.current_status.probability_of_detection)} />
              <Row label="False alarm ratio" a={pct(pq.arrival_aware.false_alarm_ratio)} c={pct(pq.current_status.false_alarm_ratio)} />
              <Row label="Blocked roads flagged incl. uncertain" a={pct(pq.share_of_blocking_flagged_incl_uncertain.arrival_aware)} c={pct(pq.share_of_blocking_flagged_incl_uncertain.current_status)} />
              <Row label="Cases (outcome-blocking)" a={pq.arrival_aware.cases} c={pq.outcome_blocking_cases} />
            </tbody>
          </table>
          <details className={styles.details}>
            <summary>Replay steps (arrival-aware)</summary>
            <ul className={styles.log}>
              {r.modes.arrival.steps.map((s) => (
                <li key={s.t_min}>T+{s.t_min}: v{s.plan_version}, {s.dispatched} dispatched, {s.served} served, {s.unmet} unmet{s.events.length ? ` · events: ${s.events.join('; ')}` : ''}{s.changes.length ? ` · ${s.changes.map((c) => `${c.assignment_id} ${c.change}`).join(', ')}` : ''}</li>
              ))}
            </ul>
          </details>
          <div className={styles.row}>
            <button type="button" className={`btn ${styles.small}`} onClick={() => downloadJson('floodnet_evaluation.json', r)}>Evaluation (JSON)</button>
          </div>
          <p className={styles.caveat}>{r.disclaimer} Forecast: {r.forecast}. Outcome: {r.outcome}.</p>
        </>
      )}
    </>
  )
}
