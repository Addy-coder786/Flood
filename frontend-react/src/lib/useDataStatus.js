// One shared poll of GET /api/data-status (cheap: the backend makes no upstream call for it). Every component that
// shows source health reads the same snapshot, so they can never disagree. Holds no credential — the endpoint
// never returns one.
import { useEffect, useState } from 'react'
import { getDataStatus } from '../api/client.js'

const POLL_MS = 60000
let snapshot = null
let timer = null
const listeners = new Set()

function refresh() {
  getDataStatus()
    .then((d) => { snapshot = d })
    .catch(() => { snapshot = null })
    .finally(() => listeners.forEach((fn) => fn(snapshot)))
}

export function refreshDataStatus() { refresh() }

export function useDataStatus() {
  const [value, setValue] = useState(snapshot)
  useEffect(() => {
    listeners.add(setValue)
    if (listeners.size === 1) { refresh(); timer = setInterval(refresh, POLL_MS) }
    return () => {
      listeners.delete(setValue)
      if (listeners.size === 0) { clearInterval(timer); timer = null }
    }
  }, [])
  return value
}

/** Is the IMD live source able to answer right now, per the server's own token state? */
export function imdLiveUsable(d) {
  if (!d || ['VALID', 'EXPIRING_SOON', 'RENEWING'].includes(d.imd_auth_status)) return true
  // with IMD account credentials on the server, an expired token is renewed automatically on the next request
  return d.imd_auth_status === 'EXPIRED' && d.imd_auto_renewal === true && d.imd_refresh_status !== 'failed'
}

/**
 * Badge for the IMD live source, from the server's own state. Never "IMD LIVE" once the latest IMD request has
 * failed: IMD RENEWING while a renewal runs, IMD AUTH EXPIRED when the token has lapsed, IMD UNAVAILABLE when a
 * renewal failed, the key/IP was refused, nothing is configured, or the last request failed for another reason.
 * With automatic renewal (IMD account credentials on the server) an expired token reads IMD RENEWING, not EXPIRED.
 */
export function imdLiveBadge(d) {
  if (!d) return { text: 'IMD', tone: 'UNKNOWN' }
  const auth = d.imd_auth_status
  const refresh = d.imd_refresh_status
  if (refresh === 'refreshing' || auth === 'RENEWING') return { text: 'IMD RENEWING', tone: 'UNKNOWN' }
  if (auth === 'EXPIRED') {
    if (refresh === 'failed') return { text: 'IMD UNAVAILABLE', tone: 'UNKNOWN' }
    return d.imd_auto_renewal ? { text: 'IMD RENEWING', tone: 'UNKNOWN' } : { text: 'IMD AUTH EXPIRED', tone: 'UNKNOWN' }
  }
  if (auth === 'UNAVAILABLE') return { text: 'IMD UNAVAILABLE', tone: 'UNKNOWN' }
  if (d.imd_live && d.imd_live.ok === false) return { text: 'IMD UNAVAILABLE', tone: 'UNKNOWN' }
  return { text: 'IMD LIVE', tone: 'REAL' }
}
