// WhatsApp TEST alert text (docs/ALERTS_WHATSAPP.md). Builds the summary lines from data already on screen; the
// backend prepends the "FloodNet TEST ALERT (simulation, not an official warning)" banner and picks the recipients.
// Keep every line under 240 characters (the API rejects longer ones).

const t = (m) => `T+${Math.round(Number(m) || 0)} min`
const clip = (s, n = 230) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)

export function testLines({ scenarioName } = {}) {
  return [
    'Test message from the FloodNet dashboard.',
    scenarioName ? `Scenario on screen: ${scenarioName}.` : 'No scenario has been run yet.',
  ]
}

/** Forecast summary: the worst streets over the run (from lib/alerts.js rankHotspots) and the headline alerts. */
export function forecastLines({ scenarioName, dataMode, currentT, hotspots = [], alerts = [] }) {
  const out = [clip(`Forecast: ${scenarioName || 'scenario'}${dataMode ? ` (${dataMode})` : ''}, timeline at ${t(currentT)}.`)]
  for (const a of alerts.slice(0, 3)) {
    out.push(clip(`${a.title}: ${a.subject}${a.etaMin != null ? `, ${a.state === 'ACTIVE' ? 'now' : `in ${Math.round(a.etaMin)} min`}` : ''}.`))
  }
  if (hotspots.length) {
    out.push('Worst streets (peak forecast depth):')
    for (const h of hotspots.slice(0, 5)) out.push(clip(`• ${h.name}: ${Math.round(h.peakDepthCm)} cm at ${t(h.peakTMin)} (${h.category})`))
  } else {
    out.push('No street reaches the minor flooding level in this forecast.')
  }
  out.push('Simulated depths from the FloodNet model, not observed water levels.')
  return out
}

/** Response plan summary: totals, unmet needs with reasons, missions needing the operator, latest changes. */
export function responseLines({ result, names }) {
  if (!result) return ['No response plan has been built yet.']
  const s = result.summary
  const out = [clip(`Response plan v${result.plan_version} at ${t(result.sim_t_min)}: ${s.people_needing_help} need help, ${s.served} served, ${s.covered_committed + s.covered_proposed} covered, ${s.unmet} unmet.`)]
  for (const [sid, u] of Object.entries(result.unmet || {}).slice(0, 4)) {
    out.push(clip(`• Unmet at ${names?.settlement?.[sid] || sid}: ${u.people} people (${u.reasons.map((r) => r.reason).join('; ')})`))
  }
  for (const a of (result.assignments || []).filter((x) => x.attention).slice(0, 3)) {
    out.push(clip(`• ${a.assignment_id} needs a decision: ${a.attention}`))
  }
  for (const c of (result.changes || []).filter((x) => x.change !== 'proposed').slice(0, 4)) {
    out.push(clip(`• ${c.assignment_id} ${c.change}: ${c.reason}`))
  }
  out.push('All headcounts and capacities are SIMULATED demo data.')
  return out.slice(0, 15)
}
