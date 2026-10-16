// Flood exposure of a route for one vehicle class, from its REAL max forecast depth vs that vehicle's limit
// (both straight from /api/route). Deliberately coarse and relative: "lower", never "safe" or "best".
export function exposureOf(maxDepthCm, limitCm) {
  if (maxDepthCm == null || limitCm == null || !(limitCm > 0)) return null
  if (maxDepthCm >= limitCm) return { label: 'High', tone: 'high' }
  if (maxDepthCm >= limitCm / 2) return { label: 'Moderate', tone: 'mod' }
  if (maxDepthCm >= 1) return { label: 'Lower', tone: 'low' }
  return { label: 'Dry', tone: 'low' }
}
