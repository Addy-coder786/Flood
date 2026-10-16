import { useFloodNet } from '../../state/FloodNetContext.jsx'
import styles from './LayerControl.module.css'

const PRIMARY = [
  { key: 'streets',  label: 'Street flooding',              color: '#ea580c' },
  { key: 'depth',    label: 'Flood depth grid',              color: '#c58a3d' },
  { key: 'hotspots', label: 'Known flood spots (MCGM)',      color: '#d97706' },
  { key: 'route',    label: 'Emergency route',               color: '#2F6BFF' },
  // on the Response and Trips pages missions always show; this switch shows them on every page
  { key: 'missions', label: 'Rescue missions (all pages)',   color: '#7c3aed' },
]
const TECHNICAL = [
  { key: 'roads',    label: 'Road network (OSM)',            color: '#817d73' },
  { key: 'drainage', label: 'Drainage network',              color: '#1e5550' },
  { key: 'terrain',  label: 'Terrain elevation (DEM)',       color: '#8a7a99' },
]

export default function LayerControl() {
  const { layers, toggleLayer, terrainOpacity, setTerrainOpacity, depthOpacity, setDepthOpacity } = useFloodNet()

  const Row = ({ item }) => (
    <label className={styles.row}>
      <input
        type="checkbox"
        checked={!!layers[item.key]}
        onChange={() => toggleLayer(item.key)}
      />
      <span className={styles.swatch} style={{ background: item.color }} />
      <span className={styles.rowLabel}>{item.label}</span>
    </label>
  )

  return (
    <section className={styles.section}>
      <div className="panel-heading">Map Layers</div>

      <div className={styles.group}>
        {PRIMARY.map((it) => <Row item={it} key={it.key} />)}
      </div>

      <div className={styles.groupSep}>Technical</div>
      <div className={styles.group}>
        {TECHNICAL.map((it) => (
          <div key={it.key}>
            <Row item={it} />
            {it.key === 'drainage' && (
              <div className={styles.drainageLegend}>
                Nodes: blue = normal · amber = at capacity · red = surcharging.
                Conduits: colour = flow ÷ design capacity (1.0 = full design flow).
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Opacity sliders — restoring parity with the legacy frontend/ dashboard */}
      <div className={styles.sliders}>
        <div className={styles.sliderRow}>
          <span className={styles.sliderLabel}>Terrain opacity</span>
          <input
            type="range"
            className={styles.slider}
            min="0" max="1" step="0.05"
            value={terrainOpacity ?? 0.40}
            onChange={(e) => setTerrainOpacity(Number(e.target.value))}
          />
          <span className={styles.sliderVal}>{Math.round((terrainOpacity ?? 0.40) * 100)}%</span>
        </div>
        <div className={styles.sliderRow}>
          <span className={styles.sliderLabel}>Depth opacity</span>
          <input
            type="range"
            className={styles.slider}
            min="0" max="1" step="0.05"
            value={depthOpacity ?? 0.50}
            onChange={(e) => setDepthOpacity(Number(e.target.value))}
          />
          <span className={styles.sliderVal}>{Math.round((depthOpacity ?? 0.50) * 100)}%</span>
        </div>
      </div>
    </section>
  )
}
