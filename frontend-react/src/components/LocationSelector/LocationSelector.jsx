import { useState, useRef, useEffect } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
// PILOT_AREAS lives in lib/locations.js (a plain data/util module, not a component) rather than here, so
// RoutePlanner's FROM/TO search can reuse these same verified landmarks without duplicating them AND so this
// component file only exports a component (mixing a component export with a data export here would disable
// Fast Refresh for this file -- react(only-export-components)).
import { PILOT_AREAS } from '../../lib/locations.js'
import styles from './LocationSelector.module.css'

export default function LocationSelector() {
  const { meta, pickPoint } = useFloodNet()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('hindmata')
  const popoverRef = useRef(null)

  useEffect(() => {
    function handleClickOutside(e) {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [open])

  const filtered = PILOT_AREAS.filter((a) =>
    a.name.toLowerCase().includes(query.toLowerCase()) ||
    a.zone.toLowerCase().includes(query.toLowerCase())
  )

  const handleSelect = (area) => {
    setSelectedId(area.id)
    setOpen(false)
    if (area.coords && pickPoint) {
      pickPoint(area.coords)
    }
  }

  const currentArea = PILOT_AREAS.find((a) => a.id === selectedId) || PILOT_AREAS[0]

  return (
    <div className={styles.wrapper} ref={popoverRef}>
      <button
        className={`${styles.triggerBtn} ${open ? styles.triggerBtnOpen : ''}`}
        onClick={() => setOpen(!open)}
        title="Select operational area or landmark"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span className={styles.pinIcon}>📍</span>
        <div className={styles.triggerText}>
          <div className={styles.triggerCity}>Mumbai</div>
          <div className={styles.triggerArea}>{currentArea.name}</div>
        </div>
        <span className={styles.chevron}>{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div className={styles.popover} role="dialog" aria-label="Select catchment area">
          <div className={styles.popoverHead}>
            <div className={styles.popoverTitle}>Operational Area Selection</div>
            <div className={styles.searchWrap}>
              <input
                type="text"
                className={styles.searchInput}
                placeholder="Search Mumbai pilot landmarks..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
            </div>
          </div>

          <div className={styles.areaList}>
            <div className={styles.listLabel}>Pilot Catchment Areas (MCGM F/North)</div>
            {filtered.map((area) => (
              <button
                key={area.id}
                className={`${styles.areaItem} ${area.id === selectedId ? styles.areaItemActive : ''}`}
                onClick={() => handleSelect(area)}
              >
                <div className={styles.areaItemHead}>
                  <span className={styles.areaName}>{area.name}</span>
                  <span className={styles.areaStatus}>{area.status}</span>
                </div>
                <div className={styles.areaZone}>{area.zone}</div>
                <div className={styles.areaDesc}>{area.desc}</div>
              </button>
            ))}
          </div>

          <div className={styles.popoverFooter}>
            <div className={styles.coverageNote}>
              <b>Active Model Coverage:</b> 2D coupled physics is configured for the Hindmata–Dadar catchment ({meta?.pilot?.name || 'Mumbai F/North'}) only. The model is <b>not calibrated</b> against observed flood depths — none exist publicly for this area.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
