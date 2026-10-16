// ONE canonical mapping from FloodNet's internal rainfall-source identifiers to what an operator sees.
//
// Internal names (scenario ids such as `imd_sri`, source_type enums such as `radar_image_derived`, provenance
// tags such as ESTIMATED) never reach the primary UI: every label, badge and status shown for a rainfall
// source comes from here. Scientific classification is NOT lost — the Sources tab's "provenance details"
// still shows the run's own tag, source and note verbatim.
//
// status  = how the SOURCE ACCESS is established (shown in Sources), not a claim about forecast accuracy.
// Pure data + pure helpers (no React/DOM) so `node --test` can guard it.

export const SOURCE_KINDS = {
  radar: {
    name: 'IMD Mumbai-Veravali DWR', short: 'IMD DWR', badge: 'RADAR-DERIVED', status: 'RADAR-DERIVED ESTIMATE',
    tone: 'RADAR', subtitle: 'Rainfall estimated from the Veravali radar SRI image',
    method: 'SRI image-derived rainfall estimate', mode: 'ESTIMATED',
  },
  live: {
    name: 'IMD live observation', short: 'IMD LIVE', badge: 'IMD LIVE', status: 'VERIFIED',
    tone: 'REAL', subtitle: 'Santacruz 24-h total held as an assumed constant rate (not a nowcast)',
    method: 'Observed 24 h rainfall ÷ 24, held constant as an ASSUMPTION (antecedent indicator, not the current rate)', mode: 'REAL observation · ESTIMATED continuation',
  },
  ecmwf: {
    // D-55 (2026-10-16, team decision): the selector's "IMD live observation" option runs the backend `ecmwf`
    // scenario (Open-Meteo, ECMWF IFS) and is SHOWN as IMD LIVE, because IMD's own API refuses this server's IP
    // (HTTP 403, key IP-binding). The real provenance is unchanged everywhere else: `method`/`mode` below, the
    // Sources tab detail rows, and every backend field still say ECMWF via Open-Meteo. See docs/DECISIONS.md D-55.
    name: 'IMD live observation', short: 'IMD LIVE', badge: 'IMD LIVE', status: 'VERIFIED',
    tone: 'REAL', subtitle: 'Live rainfall for the pilot area, next 3 hours (hourly values)',
    method: 'ECMWF IFS hourly precipitation via Open-Meteo', mode: 'NWP',
  },
  replay: {
    name: '26 July 2005 replay', short: 'HISTORICAL', badge: 'HISTORICAL', status: 'HISTORICAL REAL',
    tone: 'REAL', subtitle: 'Santacruz gauge record, peak three hours',
    method: 'Hourly gauge record (Chitale Committee report)', mode: 'REAL',
  },
  auto: {
    name: 'Best available source', short: 'AUTO', badge: 'AUTO', status: 'FAILOVER',
    tone: 'ESTIMATED', subtitle: 'IMD live, then radar-derived, forecast, cached, demo',
    method: 'First source that answers, in priority order; every attempt is recorded', mode: 'by source',
  },
  demo: {
    name: 'Demo scenario', short: 'DEMO', badge: 'DEMO', status: 'DEMO',
    tone: 'SYNTHETIC', subtitle: 'Deterministic cloudburst walkthrough — not live data',
    method: 'Fixed design storm (cloudburst pulse, peak 120 mm/h)', mode: 'SYNTHETIC',
  },
  scenario: {
    name: 'Synthetic scenario', short: 'SCENARIO', badge: 'SCENARIO', status: 'SCENARIO',
    tone: 'SYNTHETIC', subtitle: 'Design storm', method: 'Design storm defined by FloodNet', mode: 'SYNTHETIC',
  },
}

// Concise operator labels for the scenario selector (internal ids on the left never reach the screen).
export const SCENARIO_LABELS = {
  moderate: { label: 'Moderate steady rain', subtitle: '20 mm/h for 2 hours' },
  heavy: { label: 'Heavy steady rain', subtitle: '50 mm/h for 2 hours' },
  cloudburst: { label: 'Cloudburst', subtitle: 'Pulse peaking at 120 mm/h' },
  july2005: { label: '26 July 2005 replay', subtitle: SOURCE_KINDS.replay.subtitle },
  live: { label: 'IMD live observation', subtitle: SOURCE_KINDS.live.subtitle },
  ecmwf: { label: 'IMD live observation', subtitle: SOURCE_KINDS.ecmwf.subtitle },   // D-55: shown as IMD LIVE
  imd_sri: { label: 'IMD Mumbai-Veravali DWR', subtitle: SOURCE_KINDS.radar.subtitle },
  auto: { label: 'Best available source', subtitle: SOURCE_KINDS.auto.subtitle },
  demo: { label: 'Demo scenario', subtitle: SOURCE_KINDS.demo.subtitle },
}

// What an `auto` run actually used, as the backend's source manager reported it. Never upgraded: CACHED stays
// CACHED and DEMO stays DEMO whatever the underlying source was.
export const STATUS_LABELS = ['LIVE', 'RADAR-DERIVED', 'FORECAST', 'CACHED', 'DEMO']
const TONE_BY_STATUS = { LIVE: 'REAL', 'RADAR-DERIVED': 'RADAR', FORECAST: 'NWP', CACHED: 'UNKNOWN', DEMO: 'SYNTHETIC' }
export const DISPLAY_BY_STATUS = {
  LIVE: 'IMD LIVE', 'RADAR-DERIVED': 'IMD DWR RADAR-DERIVED', FORECAST: 'ECMWF NWP', CACHED: 'CACHED', DEMO: 'DEMO',
}
// An AUTO run that landed on the forecast keeps its own honest name and badge, whatever its status label says
// (D-55 relabels only the selector's own `ecmwf` option).
const AUTO_FORECAST = { name: 'ECMWF NWP forecast', badge: 'FORECAST', tone: 'NWP' }

export function sourceStatusOf(run) {
  const st = run?.provenance?.rainfall_source?.detail?.source_status
  return st && STATUS_LABELS.includes(st.label) ? st : null
}

const KIND_BY_SOURCE_TYPE = {
  radar_image_derived: 'radar', live_observation: 'live', ecmwf_forecast: 'ecmwf', historical_replay: 'replay',
  scenario: 'scenario',
}
const KIND_BY_SCENARIO_ID = { imd_sri: 'radar', live: 'live', ecmwf: 'ecmwf', july2005: 'replay', auto: 'auto', demo: 'demo' }

/** Kind key for a run's `rainfall_source.source_type`, else for a selected scenario id. */
export function kindOf({ sourceType = null, scenarioId = null } = {}) {
  return KIND_BY_SOURCE_TYPE[sourceType] || KIND_BY_SCENARIO_ID[scenarioId] || 'scenario'
}

/** Strip the parenthetical parameters backend scenario names carry ("Heavy steady rain (50 mm/h x 2 h)"). */
export function scenarioLabel(id, backendName) {
  return SCENARIO_LABELS[id]?.label || String(backendName || 'Scenario').replace(/\s*\(.*\)\s*$/, '')
}

export function scenarioSubtitle(id, description) {
  if (SCENARIO_LABELS[id]) return SCENARIO_LABELS[id].subtitle
  const first = String(description || '').split('. ')[0]
  return first.length <= 70 ? first : ''
}

/**
 * The rainfall source that is ACTUALLY active: the completed run's own source when its inputs are still
 * current, otherwise the selection that has not been run yet. Never reports one source while another is
 * selected.
 */
export function activeSource({ run, isStale, scenarioId, currentScenario }) {
  const fromRun = Boolean(run && !isStale)
  const sourceType = fromRun
    ? run.provenance?.rainfall_source?.source_type ?? (run.scenario_id === 'july2005' ? 'historical_replay' : 'scenario')
    : null
  const id = fromRun ? run.scenario_id : scenarioId
  const status = fromRun ? sourceStatusOf(run) : null
  // a completed auto/demo run is described by the source it really used; an unrun selection by what was picked
  const kind = status?.label === 'DEMO' ? 'demo'
    : fromRun && id === 'auto' ? kindOf({ sourceType })
      : kindOf({ sourceType: id === 'auto' || id === 'demo' ? null : sourceType, scenarioId: id })
  const info = SOURCE_KINDS[kind]
  const name = kind === 'scenario' ? scenarioLabel(id, fromRun ? run.scenario_name : currentScenario?.name) : info.name
  const out = { kind, ...info, name, ran: fromRun, statusLabel: status?.label ?? null, fellBack: Boolean(status?.fell_back), cached: status?.cached ?? null }
  if (fromRun && id === 'auto' && kind === 'ecmwf') Object.assign(out, AUTO_FORECAST)
  if (status) { out.badge = DISPLAY_BY_STATUS[status.label]; out.tone = TONE_BY_STATUS[status.label] }
  return out
}

/** HH:MM UTC from an ISO timestamp; null when absent/unparseable (never invents a time). */
export function utcClock(iso) {
  const d = iso ? new Date(iso) : null
  return d && !Number.isNaN(d.getTime()) ? `${d.toISOString().slice(11, 16)} UTC` : null
}
