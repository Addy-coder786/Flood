import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { useDataStatus } from '../../lib/useDataStatus.js'
import { provenanceTag } from '../../lib/format.js'
import { activeSource, utcClock } from '../../lib/sources.js'
import styles from './ProvenancePanel.module.css'

// Keys at the top of /api/provenance that are NOT nested tag/source/note groups.
const SKIP_KEYS = new Set(['data_mode', 'attribution', 'pilot_data_mode'])

/** Recursively flattens the /api/provenance tree into {label, tag, source, note} rows. A node is a "leaf"
 * once it has a `tag` key; anything else is a namespace to recurse into, building a dotted breadcrumb label
 * (e.g. "network.roughness") so nested per-field provenance is never collapsed away or hidden -- this is the
 * app's scientific-honesty surface, shown in full under "View provenance details". */
function flattenProvenance(node, prefix = '') {
  if (!node || typeof node !== 'object') return []
  if ('tag' in node) {
    return [{ label: prefix || '(root)', tag: node.tag, source: node.source, note: node.note }]
  }
  const rows = []
  for (const [key, value] of Object.entries(node)) {
    if (SKIP_KEYS.has(key)) continue
    rows.push(...flattenProvenance(value, prefix ? `${prefix}.${key}` : key))
  }
  return rows
}

/** MAIN VIEW: what feeds the forecast on screen, one card per input. Only real, current inputs. */
function buildCards({ source, rainfallSource, provenance }) {
  const d = rainfallSource?.detail || {}
  const rainMeta = source.kind === 'radar' ? utcClock(d.image_observed_at_utc)
    : source.kind === 'live' ? d.station
      : source.kind === 'ecmwf' ? utcClock(d.retrieved_at) : null
  const cards = [{
    key: 'rain', title: 'Rain', name: source.name, badge: source.badge, tone: source.tone,
    meta: [rainMeta, source.ran ? null : 'not run yet'].filter(Boolean).join(' · '),
  }]
  if (provenance?.terrain) cards.push({ key: 'terrain', title: 'Terrain', name: 'MCGM DTM', badge: 'REAL', tone: 'REAL', meta: '10 m model grid · 2D and 3D views' })
  if (provenance?.network) cards.push({ key: 'drainage', title: 'Drainage', name: 'MCGM stormwater network', badge: 'REAL', tone: 'REAL', meta: 'Capacity estimated' })
  if (provenance?.roads) cards.push({ key: 'roads', title: 'Roads', name: 'OpenStreetMap', badge: 'REAL', tone: 'REAL', meta: null })
  if (provenance?.buildings) cards.push({ key: 'buildings', title: 'Buildings', name: 'OpenStreetMap', badge: 'REAL', tone: 'REAL', meta: 'Runoff surfaces estimated' })
  return cards
}

// IMD token / radar / forecast / cache at a glance. Polls the cheap /api/data-status (it makes no upstream call).
const DOT = { ok: '#1f8a4c', warn: '#b45309', bad: '#C4273D', idle: '#9ca3af' }
function healthItems(d) {
  if (!d) return []
  const auth = d.imd_auth_status
  const tried = (s) => (s?.ok == null ? 'idle' : s.ok ? 'ok' : 'bad')
  return [
    { key: 'imd', label: 'IMD', tone: auth === 'VALID' ? 'ok' : auth === 'EXPIRING_SOON' || auth === 'RENEWING' ? 'warn' : auth === 'EXPIRED' ? 'bad' : 'idle',
      title: `IMD sign-in: ${String(auth || 'unknown').replace('_', ' ').toLowerCase()}${d.imd_auth?.minutes_left != null && auth !== 'EXPIRED' ? ` · ~${Math.round(d.imd_auth.minutes_left)} min left` : ''}` },
    { key: 'radar', label: 'Radar', tone: tried(d.radar), title: d.radar?.reason || 'IMD Mumbai-Veravali DWR image' },
    { key: 'ecmwf', label: 'Forecast', tone: tried(d.ecmwf), title: d.ecmwf?.reason || 'ECMWF NWP' },
    { key: 'cache', label: 'Cache', tone: d.cache?.available ? 'ok' : 'idle', title: d.cache?.available ? `Last good field: ${d.cache.original_label}, ${d.cache.age_min} min ago` : 'No cached field yet' },
  ]
}

export default function ProvenancePanel() {
  const health = useDataStatus()
  const { provenance, run, isStale, scenarioId, currentScenario } = useFloodNet()

  const source = activeSource({ run, isStale, scenarioId, currentScenario })
  const runRain = source.ran ? run.provenance?.rainfall : null
  const rainfallSource = source.ran ? run.provenance?.rainfall_source : null
  const cards = buildCards({ source, rainfallSource, provenance })

  const detailRows = [
    ...(runRain ? [{ label: 'rainfall (current run)', tag: runRain.tag, source: runRain.source, note: runRain.note }] : []),
    ...(provenance ? flattenProvenance(provenance) : []),
  ]

  return (
    <section className={styles.section} data-tour="panel-sources">
      <div className="panel-heading">Data Sources</div>

      <ul className={styles.cards}>
        {cards.map((c) => (
          <li key={c.key} className={styles.card}>
            <span className={styles.cardTitle}>{c.title}</span>
            <span className={styles.cardName}>{c.name}</span>
            <span className={styles.cardFoot}>
              <span className={`tag-badge tag-${c.tone}`}>{c.badge}</span>
              {c.meta && <span className={styles.cardMeta}>{c.meta}</span>}
            </span>
          </li>
        ))}
      </ul>

      {health && (
        <div className={styles.health} aria-label="Source health">
          {healthItems(health).map((h) => (
            <span key={h.key} className={styles.healthItem} title={h.title}>
              <span className={styles.healthDot} style={{ background: DOT[h.tone] }} />{h.label}
            </span>
          ))}
        </div>
      )}

      <details className={styles.details}>
        <summary className={styles.summary}>
          <span>View provenance details</span>
          <span className={styles.chevron} aria-hidden="true">▾</span>
        </summary>

        <div className={styles.body}>
          {source.ran && source.kind !== 'scenario' && (
            <div className={styles.method}>
              <div><span className={styles.methodLabel}>Mode</span>{source.mode}</div>
              <div><span className={styles.methodLabel}>Method</span>{source.method}</div>
              <div><span className={styles.methodLabel}>Status</span>{source.status}</div>
            </div>
          )}

          {detailRows.length === 0 ? (
            <div className={styles.empty}>Provenance not loaded yet.</div>
          ) : (
            <table className={styles.table}>
              <tbody>
                {detailRows.map((r) => (
                  <tr key={r.label}>
                    <td className={styles.labelCell}>
                      <div className={styles.labelText}>{r.label}</div>
                      <span className={`tag-badge tag-${provenanceTag(r)}`}>{provenanceTag(r)}</span>
                    </td>
                    <td className={styles.sourceCell}>
                      <div>{r.source || '—'}</div>
                      {r.note && <div className={styles.note}>{r.note}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {Array.isArray(provenance?.attribution) && provenance.attribution.length > 0 && (
            <div className={styles.attribution}>{provenance.attribution.join(' · ')}</div>
          )}
          <div className={styles.footer}>
            Decision-support prototype. Simplified 2D surface physics and a capacity-limited drainage
            solver; depths carry terrain and parameter uncertainty.
          </div>
        </div>
      </details>
    </section>
  )
}
