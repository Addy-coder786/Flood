import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { SEVERITY_LABEL } from '../../lib/severity.js'
import SeverityBadge from '../SeverityBadge/SeverityBadge.jsx'
import styles from './FloodedStreets.module.css'

const TOP_N = 8

export default function FloodedStreets() {
  const { run, frame, selectedSegId, selectSegment } = useFloodNet()

  const features = frame?.streets?.features || []
  const flooded = features
    .filter((f) => (f.properties?.depth_cm ?? 0) > 0)
    .sort((a, b) => (b.properties?.depth_cm ?? 0) - (a.properties?.depth_cm ?? 0))
  const ranked = flooded.slice(0, TOP_N)
  const maxDepth = ranked[0]?.properties?.depth_cm || 1

  const hasData = Boolean(run && frame)

  return (
    <section className={styles.section} aria-labelledby="flooded-streets-heading">
      <div className="panel-heading">
        <span id="flooded-streets-heading">Top flooded streets</span>
        {flooded.length > 0 && <span className={styles.count}>({flooded.length})</span>}
      </div>

      {!hasData ? (
        <div className={styles.empty}>Run a forecast to see street-level flooding.</div>
      ) : ranked.length === 0 ? (
        <div className={styles.empty}>No street flooding at this timestep.</div>
      ) : (
        /* role="list" is explicit because `list-style: none` drops list semantics in Safari/VoiceOver. */
        <ul className={styles.list} role="list">
          {ranked.map((f, i) => {
            const p = f.properties || {}
            const isSelected = String(p.seg_id) === String(selectedSegId)
            const depth = p.depth_cm ?? 0
            const pct = Math.min(100, Math.max(8, Math.round((depth / maxDepth) * 100)))
            const sevText = SEVERITY_LABEL[p.severity] || p.severity || 'unclassified'
            const label =
              `Rank ${i + 1}. ${p.name || p.seg_id}. ` +
              `${sevText} flooding, ${Math.round(depth)} centimetres. ` +
              `Select this street segment on the map.`

            return (
              <li key={p.seg_id}>
                <button
                  type="button"
                  className={`${styles.row} ${isSelected ? styles.rowSelected : ''}`}
                  onClick={() => selectSegment(p.seg_id)}
                  aria-label={label}
                  aria-current={isSelected ? 'true' : undefined}
                >
                  <div className={styles.barFill} style={{ width: `${pct}%` }} aria-hidden="true" />
                  <span className={styles.rank}>#{i + 1}</span>
                  <span className={styles.name}>{p.name || p.seg_id}</span>
                  <SeverityBadge severity={p.severity} depthCm={p.depth_cm} />
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
