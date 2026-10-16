export function fmt(x, digits = 1) {
  if (x == null || Number.isNaN(Number(x))) return '–'
  return Number(x).toFixed(digits)
}

export function fmtInt(x) {
  return fmt(x, 0)
}

export function fmtMinutes(tMin) {
  if (tMin == null) return '–'
  const h = Math.floor(tMin / 60)
  const m = Math.round(tMin % 60)
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m} min`
}

export function shortId(id, n = 8) {
  return id ? String(id).slice(0, n) : '–'
}

/** provenance can be a {tag, source, note} object or a bare string/enum value. */
export function provenanceTag(p) {
  if (!p) return 'UNKNOWN'
  const t = typeof p === 'string' ? p : p.tag || p.data_tag
  const key = String(t || 'UNKNOWN').toUpperCase().replace(/^TAG\./, '')
  return ['REAL', 'ESTIMATED', 'SYNTHETIC', 'DEMONSTRATION', 'NWP'].includes(key) ? key : 'UNKNOWN'
}

export function bboxToLatLngBounds(bboxLonLat) {
  // [west, south, east, north] -> Leaflet [[south, west], [north, east]]
  const [w, s, e, n] = bboxLonLat
  return [
    [s, w],
    [n, e],
  ]
}

export function lonLatToLatLng(coord) {
  return [coord[1], coord[0]]
}

export function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v))
}
