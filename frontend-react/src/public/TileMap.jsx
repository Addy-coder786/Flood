// Optional street map for the public alert page (2026-10-11). Loaded only when the reader presses "Show street map"
// (a dynamic import in AlertMap.jsx, so Leaflet is never part of the page's first download). Same tile source and
// attribution as the dashboard map (components/MapView/MapView.jsx), via TILE_URL / TILE_ATTRIBUTION. Draws only what
// the public snapshot holds: the advisory area and the affected roads; tapping a road selects it in the list.
import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { ROAD_CLASSES, TILE_ATTRIBUTION, TILE_MAX_ZOOM, TILE_URL } from '../lib/publicAlert.js'
import s from './PublicAlertApp.module.css'

function cssVar(name, fallback) {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
  } catch {
    return fallback
  }
}

const latLng = ([lon, lat]) => [lat, lon]

export default function TileMap({ map, selected, onSelect }) {
  const el = useRef(null)
  const lines = useRef([])
  const onSelectRef = useRef(onSelect)
  useEffect(() => { onSelectRef.current = onSelect }, [onSelect])

  useEffect(() => {
    const m = L.map(el.current, { scrollWheelZoom: false })        // the page keeps scrolling over the map
    L.tileLayer(TILE_URL, { maxZoom: TILE_MAX_ZOOM, attribution: TILE_ATTRIBUTION }).addTo(m)
    const accent = cssVar('--accent', '#7A2231')
    if (map?.area_outline?.coordinates?.[0]) {
      L.polygon(map.area_outline.coordinates[0].map(latLng), { color: accent, weight: 1.5, dashArray: '4 3', fillOpacity: 0.08 })
        .addTo(m)
    }
    lines.current = (map?.affected_roads || []).map((r, i) => {
      const c = ROAD_CLASSES[r.class] || ROAD_CLASSES.MODEL_ESTIMATE
      const line = L.polyline(r.coordinates.map(latLng), {
        color: accent, weight: 6, opacity: 0.95, dashArray: c.dash || null, lineCap: r.class === 'REPORTED' ? 'round' : 'butt',
      }).addTo(m)
      line.on('click', () => onSelectRef.current?.(i))
      return line
    })
    const [w, south, e, n] = map?.bbox || []
    if ([w, south, e, n].every(Number.isFinite)) m.fitBounds([[south, w], [n, e]])
    return () => { m.remove(); lines.current = [] }
  }, [map])

  useEffect(() => {
    const on = new Set(String(selected || '').split(',').filter(Boolean).map(Number))
    lines.current.forEach((l, i) => l.setStyle({ weight: on.has(i) ? 10 : 6 }))
  }, [selected, map])

  return <div ref={el} className={s.tileMap} role="region" aria-label="Street map of the affected roads (OpenStreetMap)" />
}
