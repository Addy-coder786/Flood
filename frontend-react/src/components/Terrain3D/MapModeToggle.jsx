// 2D | 3D. View state only: the run, timeline, source, layers and selected street are untouched.
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import styles from './MapModeToggle.module.css'

export default function MapModeToggle() {
  const { mapMode, setMapMode } = useFloodNet()
  return (
    <div className={`${styles.toggle} glass-panel`} role="group" aria-label="Map view" data-tour="map-mode">
      {[['2d', '2D', 'Map'], ['3d', '3D', 'Terrain (MCGM DTM)']].map(([mode, label, title]) => (
        <button
          key={mode}
          type="button"
          className={`${styles.btn} ${mapMode === mode ? styles.btnOn : ''}`}
          onClick={() => setMapMode(mode)}
          aria-pressed={mapMode === mode}
          title={title}
        >
          {label}
        </button>
      ))}
    </div>
  )
}
