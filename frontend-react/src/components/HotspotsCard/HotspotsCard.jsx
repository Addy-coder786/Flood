// Flood Hotspots — operational summary of the run on screen (backend/floodnet/analysis/hotspots.py), delivered
// inside the simulation response itself. Model output only; clicking a hotspot selects that street.
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { fmt, shortId } from '../../lib/format.js'
import { SEVERITY_COLOR } from '../../lib/severity.js'
import styles from './HotspotsCard.module.css'

const km = (m) => (m >= 1000 ? `${fmt(m / 1000, 1)} km` : `${fmt(m, 0)} m`)
const ha = (m2) => `${fmt(m2 / 10000, 1)} ha`
const tPlus = (t) => (t == null ? '—' : `T+${Math.round(t)}`)

export default function HotspotsCard() {
  const { run, isStale, selectSegment, setCurrentT, issueMapCommand } = useFloodNet()
  const data = run && !isStale ? run.hotspots : null
  if (!data) return null

  const open = (h) => {
    setCurrentT(h.peak_t_min)
    selectSegment(h.seg_id)
    issueMapCommand({ type: 'HIGHLIGHT_SEGMENTS', ids: [h.seg_id] })
  }

  return (
    <section className={styles.section} data-tour="hotspots">
      <div className="panel-heading">Flood Hotspots</div>
      <div className={styles.grid}>
        <div className={styles.cell}><span>Max depth</span><strong>{fmt(data.max_depth_cm, 0)} cm</strong></div>
        <div className={styles.cell}><span>First flooding</span><strong>{tPlus(data.earliest_onset_min)}</strong></div>
        <div className={styles.cell}><span>Peak</span><strong>{tPlus(data.peak_t_min)}</strong></div>
        <div className={styles.cell}><span>Flooded area</span><strong>{ha(data.flooded_area_m2)}</strong></div>
        <div className={styles.cell}><span>Road affected</span><strong>{km(data.affected_road_length_m)}</strong></div>
        <div className={styles.cell}><span>Intersections</span><strong>{data.affected_intersections}</strong></div>
      </div>
      {data.hotspots.length === 0 ? (
        <div className={styles.empty}>No street reaches {data.threshold_cm} cm in this forecast.</div>
      ) : (
        <ol className={styles.list}>
          {data.hotspots.map((h) => (
            <li key={h.seg_id}>
              <button type="button" className={styles.item} onClick={() => open(h)} title="Show on map at its peak time">
                <span className={styles.rank} style={{ background: SEVERITY_COLOR[h.severity] }}>{h.rank}</span>
                <span className={styles.name}>{h.name || `Road ${shortId(h.seg_id)}`}</span>
                <span className={styles.meta}>{fmt(h.peak_depth_cm, 0)} cm · {tPlus(h.peak_t_min)}</span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
