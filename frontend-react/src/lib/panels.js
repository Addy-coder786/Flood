// Collapsible panels (DECISIONS D-47). Two panels slide in and out: the navigation sidebar ('nav': expanded shows
// labels, collapsed shows icons; on narrow screens it is an off-canvas drawer) and the work panel ('work': the page
// beside the map; on narrow screens a bottom sheet). Pure functions: App.jsx holds the state, tests pin the rules
// (panels.test.mjs). Panels never unmount pages, so forecasts, routes, incidents and assignments are untouched.

export const PANELS_STORAGE_KEY = 'floodnet.panels.v1'
export const TRANSITION_MS = 240

const NAV = ['expanded', 'collapsed']
const WORK = ['open', 'closed']
const VALUES = { nav: NAV, work: WORK }

export function layoutFor(width) {
  const w = Number(width) || 0
  if (w > 1280) return 'wide'
  if (w > 960) return 'laptop'
  return 'narrow'
}

const DEFAULTS = {
  wide: { nav: 'expanded', work: 'open' },
  laptop: { nav: 'collapsed', work: 'open' },
  narrow: { nav: 'collapsed', work: 'open' },
}

export function initialPanels(width, stored) {
  const layout = layoutFor(width)
  const base = { ...DEFAULTS[layout] }
  if (stored && VALUES.nav.includes(stored.nav) && VALUES.work.includes(stored.work)) {
    base.work = stored.work
    if (layout !== 'narrow') base.nav = stored.nav
  }
  return base
}

function set(state, panel, value) {
  if (!VALUES[panel] || !VALUES[panel].includes(value) || state[panel] === value) return state
  return { ...state, [panel]: value }
}

export function panelsReducer(state, action) {
  switch (action?.type) {
    case 'toggle': {
      const vals = VALUES[action.panel]
      if (!vals) return state
      return set(state, action.panel, state[action.panel] === vals[0] ? vals[1] : vals[0])
    }
    case 'set':
      return set(state, action.panel, action.value)
    case 'navigate': {
      // a page was chosen: show it; on a phone the navigation drawer gets out of the way
      let s = set(state, 'work', 'open')
      if (action.layout === 'narrow') s = set(s, 'nav', 'collapsed')
      return s
    }
    case 'layout':
      return action.layout === 'narrow' ? set(state, 'nav', 'collapsed') : state
    default:
      return state
  }
}

export function serializePanels(state) {
  return JSON.stringify({ nav: state.nav, work: state.work })
}

export function deserializePanels(text) {
  if (typeof text !== 'string' || !text) return null
  try {
    const d = JSON.parse(text)
    if (!d || typeof d !== 'object' || Array.isArray(d)) return null
    return NAV.includes(d.nav) && WORK.includes(d.work) ? { nav: d.nav, work: d.work } : null
  } catch {
    return null
  }
}

/** What a toggle button says and announces (aria-expanded + label + tooltip). */
export function panelControl(state, panel) {
  if (panel === 'work') {
    const open = state.work === 'open'
    return open
      ? { expanded: true, label: 'Hide the page panel', title: 'Hide the page panel (more map)' }
      : { expanded: false, label: 'Show the page panel', title: 'Show the page panel' }
  }
  const open = state.nav === 'expanded'
  return open
    ? { expanded: true, label: 'Collapse navigation', title: 'Collapse navigation to icons' }
    : { expanded: false, label: 'Expand navigation', title: 'Expand navigation' }
}
