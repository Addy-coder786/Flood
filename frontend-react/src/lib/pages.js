// Page registry and hash routing (DECISIONS D-37, D-47, D-50). Nine task pages, one primary task each; the URL hash (#/access)
// selects the page so Back/Forward, refresh and deep links work without a router library or any server change.
// Pure module: App.jsx renders from it, pages.test.mjs pins it.

export const PAGES = [
  { key: 'overview', label: 'Situation', hash: '#/overview', title: 'Situation overview',
    purpose: 'Current situation and next step.' },
  { key: 'forecast', label: 'Forecast', hash: '#/forecast', title: 'Flood forecast',
    purpose: 'Run the 0–180 min street-depth forecast.' },
  { key: 'access', label: 'Roads', hash: '#/access', title: 'Road access at arrival time',
    purpose: 'Plan a rescue trip and check roads at arrival time.' },
  { key: 'response', label: 'Response', hash: '#/response', title: 'Response plan',
    purpose: 'Assign teams, vehicles and shelter places.' },
  { key: 'trips', label: 'Trips', hash: '#/trips', title: 'Rescue trips',
    purpose: 'Start, follow and reroute rescue trips.' },
  { key: 'resources', label: 'Resources', hash: '#/resources', title: 'Resources and shelters',
    purpose: 'Teams, vehicles, shelters and responder links.' },
  { key: 'reports', label: 'Reports', hash: '#/reports', title: 'Field reports',
    purpose: 'Log and resolve road and resource reports.' },
  { key: 'alerts', label: 'Public alerts', hash: '#/alerts', title: 'Public alerts',
    purpose: 'Reviewed advisories for subscribers; official feed.' },
  { key: 'replay', label: 'Evidence', hash: '#/replay', title: 'Replay and evaluation',
    purpose: 'Replay an incident and compare routing methods.' },
]

export const DEFAULT_PAGE = 'overview'

// Right-rail tab keys used before D-37 (tours, briefings, story stages) and the page each now opens.
// 'alerts' is a page of its own since D-47 (it hosts the advisory panel the old tab showed).
export const LEGACY_ALIASES = { why: 'forecast', routing: 'access', provenance: 'overview' }

const BY_KEY = Object.fromEntries(PAGES.map((p) => [p.key, p]))

export function resolvePageKey(key) {
  if (typeof key !== 'string' || !key) return DEFAULT_PAGE
  if (BY_KEY[key]) return key
  return LEGACY_ALIASES[key] || DEFAULT_PAGE
}

/** '#/access', '#/access/', '#/access?x=1' -> 'access'; anything else -> the default page. Never throws. */
export function pageFromHash(hash) {
  if (typeof hash !== 'string') return DEFAULT_PAGE
  const path = hash.replace(/^#/, '').split('?')[0]
  const parts = path.split('/').filter(Boolean)
  if (parts.length !== 1) return DEFAULT_PAGE
  return resolvePageKey(parts[0])
}

export function hashForPage(key) {
  return `#/${resolvePageKey(key)}`
}

export function pageTitle(key) {
  return BY_KEY[resolvePageKey(key)].title
}

export function pageInfo(key) {
  return BY_KEY[resolvePageKey(key)]
}

/** The neighbouring page in sidebar order, clamped at both ends. */
export function nextPage(key, dir) {
  const i = PAGES.findIndex((p) => p.key === resolvePageKey(key))
  const j = Math.min(PAGES.length - 1, Math.max(0, i + Math.sign(Number(dir) || 0)))
  return PAGES[j].key
}

/** empty | loading | stale | error | ready for a page, from the shared state (no DOM). */
export function pageStatus(key, state = {}) {
  const k = resolvePageKey(key)
  const s = state || {}
  if (s.simulating) return 'loading'
  if (s.simError) return 'error'
  if (!s.run) return ['overview', 'reports', 'alerts', 'resources'].includes(k) ? 'ready' : 'empty'
  if (s.isStale) return 'stale'
  if (k === 'response') {
    const res = s.response?.result
    if (res) {
      if (s.currentT != null && res.sim_t_min != null && res.sim_t_min !== s.currentT) return 'stale'
      const planRun = res.forecast?.run_id
      if (planRun && s.run.run_id && planRun !== s.run.run_id) return 'stale'
    }
    if (s.response?.staleReason) return 'stale'
  }
  return 'ready'
}
