// Small static SVG of the remaining route (no tiles): current leg solid, later legs dashed, the last shared position
// as a ring. Projection helpers are shared with the public alert map (src/lib/publicAlert.js).
import { makeProjector, pathD } from '../lib/publicAlert.js'
import s from './DriverApp.module.css'

const W = 320
const PAD = 14

function bboxOf(lines, extra) {
  const pts = [...lines.flat(), ...(extra ? [extra] : [])].filter((p) => Array.isArray(p) && p.every(Number.isFinite))
  if (!pts.length) return null
  let [w, sth, e, n] = [Infinity, Infinity, -Infinity, -Infinity]
  for (const [x, y] of pts) { w = Math.min(w, x); e = Math.max(e, x); sth = Math.min(sth, y); n = Math.max(n, y) }
  const m = 0.0004                                         // ~40 m margin so a straight leg is not degenerate
  return [w - m, sth - m, e + m, n + m]
}

export default function RouteSketch({ legs, position }) {
  const lines = legs.map((l) => l.coords)
  const pos = position && Number.isFinite(position.lon) && Number.isFinite(position.lat) ? [position.lon, position.lat] : null
  const proj = makeProjector(bboxOf(lines, pos), W, PAD)
  if (!proj) return null
  const h = Math.round(proj.height * 10) / 10
  const end = legs[0]?.coords.at(-1)
  const [ex, ey] = end ? proj.project(end) : [null, null]
  const [px, py] = pos ? proj.project(pos) : [null, null]
  const label = `Sketch of the remaining route, north up: ${legs.length} leg${legs.length === 1 ? '' : 's'}, `
    + `current leg to ${legs[0]?.to || 'the next stop'}.${pos ? ' The ring is the last location you shared.' : ''}`
  return (
    <figure className={s.sketchFigure}>
      <svg className={s.sketch} viewBox={`0 0 ${W} ${h}`} width="100%" role="img" aria-label={label} preserveAspectRatio="xMidYMid meet">
        <rect className={s.sketchBg} x="0" y="0" width={W} height={h} />
        {legs.slice(1).map((l, i) => <path key={`l${i}`} className={s.legLater} d={pathD(l.coords, proj)} />)}
        {legs[0] && <path className={s.legNow} d={pathD(legs[0].coords, proj)} />}
        {end && <circle className={s.stop} cx={ex} cy={ey} r="6" />}
        {pos && <circle className={s.here} cx={px} cy={py} r="8" />}
      </svg>
      <figcaption className={s.small}>
        Solid line: current leg to {legs[0]?.to || 'the next stop'}. Dashed line: later legs. Filled dot: next stop.
        {pos ? ' Ring: last location you shared.' : ''} North is up. Map data © OpenStreetMap contributors.
      </figcaption>
    </figure>
  )
}
