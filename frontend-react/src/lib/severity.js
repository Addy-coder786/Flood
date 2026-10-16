// Single source of truth for severity presentation (colour/label) used everywhere: map, street list, KPI
// cards, route warnings, timeline. The THRESHOLDS themselves come from the backend (`/api/meta` ->
// severity_bands_cm), never hardcoded independently here -- this file only maps a severity string to how it
// looks. DEFAULT_BANDS is a fallback for the brief window before /api/meta has loaded, and mirrors
// backend/floodnet/config.py::SEVERITY_BANDS_CM at the time of writing.
export const DEFAULT_BANDS_CM = [
  [5, 'clear'],
  [15, 'minor'],
  [30, 'moderate'],
  [60, 'severe'],
]

export const SEVERITY_ORDER = ['clear', 'minor', 'moderate', 'severe', 'critical']

// Kept in lockstep with the --sev-* custom properties in index.css. These two palettes had drifted: this
// object still held the saturated values from the pre-light-theme design (#ffd23f / #ff8c1a / #ff3b3b),
// which are used BOTH as map-marker fills AND as text/tint on the near-white panel surfaces -- bright
// yellow text at ~9.5px on #FEFCF7 does not reach a 4.5:1 contrast ratio. Values below are the darkened
// light-theme ones, with `severe` given its own value: index.css had it duplicating `moderate`, which made
// two distinct severity bands render identically on the map and in the street list.
export const SEVERITY_COLOR = {
  clear: '#6B7280',
  minor: '#C99A1E',
  moderate: '#D9731F',
  severe: '#C2410C',
  critical: '#C4273D',
}

export const SEVERITY_LABEL = {
  clear: 'Clear',
  minor: 'Minor',
  moderate: 'Moderate',
  severe: 'Severe',
  critical: 'Critical',
}

/** Mirrors backend floodnet/streets/aggregate.py::severity() -- bands is [[limit_cm, label], ...] ascending;
 * anything at/above the last limit is 'critical'. */
export function severityOf(depthCm, bands = DEFAULT_BANDS_CM) {
  for (const [limit, label] of bands) {
    if (depthCm < limit) return label
  }
  return 'critical'
}

export function severityColor(sev) {
  return SEVERITY_COLOR[sev] || SEVERITY_COLOR.clear
}

const DEFAULT_VEHICLE_LIMIT_CM = { pedestrian: 60, motorcycle: 30, car: 30, ambulance: 40, truck: 60 }

export function passable(depthCm, vehicle, limits = DEFAULT_VEHICLE_LIMIT_CM) {
  const limit = limits[vehicle] ?? 30
  return depthCm < limit
}
