// Client-side exports (MISC-04 Phase 3): the ledger, dispatch log, plan versions, event logs and evaluation results
// are downloaded from what the browser already holds. No server round-trip, nothing is stored anywhere.

export function toCsv(rows, columns) {
  const cols = columns || Array.from(rows.reduce((set, r) => { Object.keys(r).forEach((k) => set.add(k)); return set }, new Set()))
  const cell = (v) => {
    if (v == null) return ''
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n')
}

export function downloadText(filename, text, type = 'text/plain') {
  if (typeof document === 'undefined') return
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const downloadJson = (filename, obj) => downloadText(filename, JSON.stringify(obj, null, 1), 'application/json')

/** The ledger without route geometry: what a reviewer needs, a tenth of the size. */
export function ledgerForExport(ledger) {
  return (ledger || []).map((a) => ({
    ...a,
    legs: (a.legs || []).map(({ route: _route, segments, ...leg }) => ({ ...leg, roads: (segments || []).map((s) => s.name || s.seg_id) })),
  }))
}
