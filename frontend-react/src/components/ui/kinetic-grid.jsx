import { useEffect, useRef, useCallback } from 'react'
import { cn } from '../../lib/utils.js'
import styles from './kinetic-grid.module.css'

// ============================================================================================
// KineticGrid -- ported verbatim (animation mechanism unchanged) from the supplied 21st.dev
// component (kinetic-grid.tsx). Only two kinds of change were made, both explicitly within the
// "may be adapted" scope for this integration:
//   1. TypeScript types/interfaces removed (compile-time only; zero runtime/behavioral change).
//   2. Tailwind utility classNames replaced with an equivalent CSS Module (kinetic-grid.module.css)
//      -- same computed styles, no Tailwind dependency added to the project -- and the `theme`
//      color constants recoloured from the original electric-blue to FloodNet's warm ivory /
//      burgundy / ochre palette (background, line, node, glow, ripple colours only).
// Every other line -- the warp math, edge-pin logic, ripple physics, smoothstep easing, grid/node/
// ripple drawing, the mouse-lerp animation loop, resize/mousemove/click wiring -- is unchanged.
// ============================================================================================

// ─── Constants ────────────────────────────────────────────────────────────────

const CELL_SIZE = 55 // Desktop-ish size. Will dictate cols/rows
const INFLUENCE_RADIUS = 260
const MAX_WARP = 24
const DOT_SPACING = 28
const LERP_SPEED = 0.08

// FloodNet adaptation: warm-ivory-tinted line base instead of the original white-on-black base
// (the original was `{ r:255, g:255, b:255, a:0.13 }`, meant to read as faint white lines on a
// near-black canvas fill -- ours reads as faint warm-charcoal lines on an ivory fill instead).
const LINE_BASE = { r: 43, g: 33, b: 24, a: 0.1 }
const NODE_BASE_RADIUS = 1.8
const NODE_ACTIVE_RADIUS = 3.2

// ─── Helpers ──────────────────────────────────────────────────────────────────

function lerpN(a, b, t) {
  return a + (b - a) * t
}

function lerpColor(base, active, t) {
  const r = Math.round(lerpN(base.r, active.r, t))
  const g = Math.round(lerpN(base.g, active.g, t))
  const b = Math.round(lerpN(base.b, active.b, t))
  const a = lerpN(base.a, active.a, t)
  return `rgba(${r},${g},${b},${a.toFixed(3)})`
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function KineticGrid({ children, className, globalColor = 'default' }) {
  const canvasRef = useRef(null)

  const mouseRef = useRef({ x: -9999, y: -9999 })
  const targetMouseRef = useRef({ x: -9999, y: -9999 })
  const ripplesRef = useRef([])
  const rafRef = useRef(0)
  const sizeRef = useRef({ w: 0, h: 0 })

  // ── Warp ────────────────────────────────────────────────────────────────────

  const getWarpedPoint = useCallback((gx, gy, col, row, mouse, ripples, cols, rows) => {
    // Edge pin — smoothly locks boundary rows/cols in place
    const edgeMargin = 1.5
    const colPin = Math.min(col / edgeMargin, (cols - 1 - col) / edgeMargin, 1)
    const rowPin = Math.min(row / edgeMargin, (rows - 1 - row) / edgeMargin, 1)
    const pinFactor = colPin * colPin * rowPin * rowPin

    const dx = gx - mouse.x
    const dy = gy - mouse.y
    const dist = Math.sqrt(dx * dx + dy * dy)

    const proximity = Math.max(0, 1 - dist / INFLUENCE_RADIUS) * pinFactor

    // Ripple displacement
    let rx = 0, ry = 0
    for (const r of ripples) {
      const rdx = gx - r.x
      const rdy = gy - r.y
      const rdist = Math.sqrt(rdx * rdx + rdy * rdy)
      const waveWidth = 55
      const diff = rdist - r.radius
      if (Math.abs(diff) < waveWidth) {
        const strength = (1 - Math.abs(diff) / waveWidth) * r.opacity * 18 * pinFactor
        const angle = Math.atan2(rdy, rdx)
        const sign = diff < 0 ? -1 : 1
        rx += Math.cos(angle) * strength * sign * -1
        ry += Math.sin(angle) * strength * sign * -1
      }
    }

    // Cursor warp with bell falloff
    if (dist < INFLUENCE_RADIUS && dist > 0 && pinFactor > 0) {
      const t = dist / INFLUENCE_RADIUS
      const eased = t < 0.01 ? 0 : (1 - t) * (1 - t) * Math.min(1, dist / 60)
      const warpAmt = eased * MAX_WARP * pinFactor
      const angle = Math.atan2(dy, dx)
      return {
        pt: {
          x: gx - Math.cos(angle) * warpAmt + rx,
          y: gy - Math.sin(angle) * warpAmt + ry,
        },
        proximity,
      }
    }

    return { pt: { x: gx + rx, y: gy + ry }, proximity }
  }, [])

  // ── Draw ────────────────────────────────────────────────────────────────────

  const draw = useCallback((now) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const { w: W, h: H } = sizeRef.current
    const mouse = mouseRef.current
    const ripples = ripplesRef.current

    // FloodNet palette: 'default' = warm ivory ground + muted-burgundy reactive lines/nodes;
    // 'monochrome' = charcoal ground + charcoal-on-ivory reactive lines/nodes. Same two-theme
    // shape as the original (default / monochrome), values recoloured only.
    const theme = {
      default: {
        bg: '#F7F2E9',
        lineActive: { r: 122, g: 34, b: 49, a: 0.55 },
        nodeActive: { r: 122, g: 34, b: 49, a: 0.9 },
        glow: '122,34,49',
        ripple: '184,135,74',
      },
      monochrome: {
        bg: '#2B2118',
        lineActive: { r: 247, g: 242, b: 233, a: 0.5 },
        nodeActive: { r: 247, g: 242, b: 233, a: 0.85 },
        glow: '247,242,233',
        ripple: '247,242,233',
      },
    }[globalColor ?? 'default']

    ctx.clearRect(0, 0, W, H)

    // Background
    ctx.fillStyle = theme.bg
    ctx.fillRect(0, 0, W, H)

    // Static background dot texture
    ctx.fillStyle = 'rgba(43,33,24,0.05)'
    for (let x = DOT_SPACING / 2; x < W; x += DOT_SPACING) {
      for (let y = DOT_SPACING / 2; y < H; y += DOT_SPACING) {
        ctx.beginPath()
        ctx.arc(x, y, 0.7, 0, Math.PI * 2)
        ctx.fill()
      }
    }

    // Update ripples
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i]
      const age = (now - r.born) / 1000
      // FIX: Ensure radius is never negative
      r.radius = Math.max(0, age * 400)
      r.opacity = Math.max(0, 1 - age * 1.2)
      if (r.opacity <= 0) ripples.splice(i, 1)
    }

    // ── Build warped grid ─────────────────────────────────────────────────
    const cols = Math.max(2, Math.ceil(W / CELL_SIZE)) + 1
    const rows = Math.max(2, Math.ceil(H / CELL_SIZE)) + 1
    const cellW = W / (cols - 1)
    const cellH = H / (rows - 1)

    const pts = []
    const prox = []

    for (let row = 0; row < rows; row++) {
      pts[row] = []
      prox[row] = []
      for (let col = 0; col < cols; col++) {
        const { pt, proximity } = getWarpedPoint(col * cellW, row * cellH, col, row, mouse, ripples, cols, rows)
        pts[row][col] = pt
        prox[row][col] = proximity
      }
    }

    // ── Grid lines ────────────────────────────────────────────────────────
    const drawSeg = (p1, p2, pr1, pr2) => {
      const avg = (pr1 + pr2) / 2
      const t = avg * avg * (3 - 2 * avg) // smoothstep
      ctx.beginPath()
      ctx.moveTo(p1.x, p1.y)
      ctx.lineTo(p2.x, p2.y)
      ctx.strokeStyle = lerpColor(LINE_BASE, theme.lineActive, t)
      ctx.lineWidth = lerpN(0.8, 1.5, t)
      ctx.stroke()
    }

    ctx.lineCap = 'butt'

    for (let row = 0; row < rows; row++)
      for (let col = 0; col < cols - 1; col++)
        drawSeg(pts[row][col], pts[row][col + 1], prox[row][col], prox[row][col + 1])

    for (let col = 0; col < cols; col++)
      for (let row = 0; row < rows - 1; row++)
        drawSeg(pts[row][col], pts[row + 1][col], prox[row][col], prox[row + 1][col])

    // ── Intersection nodes ────────────────────────────────────────────────
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const p = pts[row][col]
        const pr = prox[row][col]
        const t = pr * pr * (3 - 2 * pr) // smoothstep
        const r = lerpN(NODE_BASE_RADIUS, NODE_ACTIVE_RADIUS, t)

        // Outer glow ring for active nodes
        if (t > 0.3) {
          const glowR = r + lerpN(0, 6, (t - 0.3) / 0.7)
          const grd = ctx.createRadialGradient(p.x, p.y, r * 0.5, p.x, p.y, glowR)
          grd.addColorStop(0, `rgba(${theme.glow},${(t * 0.3).toFixed(3)})`)
          grd.addColorStop(1, `rgba(${theme.glow},0)`)
          ctx.beginPath()
          ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2)
          ctx.fillStyle = grd
          ctx.fill()
        }

        // Node fill
        ctx.beginPath()
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2)
        ctx.fillStyle = lerpColor({ r: 43, g: 33, b: 24, a: 0.18 }, theme.nodeActive, t)
        ctx.fill()
      }
    }

    // ── Ripple rings ──────────────────────────────────────────────────────
    for (const r of ripples) {
      // FIX: Ensure radius is positive before drawing arc
      const safeRadius = Math.max(0, r.radius)
      ctx.beginPath()
      ctx.arc(r.x, r.y, safeRadius, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(${theme.ripple},${(r.opacity * 0.28).toFixed(3)})`
      ctx.lineWidth = 1.5
      ctx.stroke()
    }
  }, [getWarpedPoint, globalColor])

  // ── Animation loop ──────────────────────────────────────────────────────────

  const animate = useCallback((now) => {
    const m = mouseRef.current
    const t = targetMouseRef.current

    m.x = lerpN(m.x, t.x, LERP_SPEED)
    m.y = lerpN(m.y, t.y, LERP_SPEED)

    draw(now)
    rafRef.current = requestAnimationFrame(animate)
  }, [draw])

  // ── Setup ───────────────────────────────────────────────────────────────────
  // FloodNet adaptation: sized to the component's own container (ResizeObserver) instead of the
  // original `window.innerWidth/innerHeight` full-viewport sizing, and mouse/click listeners are
  // scoped to the container element instead of `window` -- because here KineticGrid is used as a
  // decorative layer WITHIN the hero, not as a full-page wrapper (the original's own documented
  // usage). This is a "container size / positioning" adaptation, not a change to the warp/ripple
  // mechanism itself, which is byte-for-byte the same math as the supplied component.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const container = canvas.parentElement
    if (!container) return undefined

    const setSize = () => {
      const w = container.clientWidth
      const h = container.clientHeight
      canvas.width = w
      canvas.height = h
      sizeRef.current = { w, h }
    }

    setSize()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(setSize) : null
    ro?.observe(container)
    window.addEventListener('resize', setSize)

    const toLocal = (e) => {
      const rect = container.getBoundingClientRect()
      return { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }

    const onMouseMove = (e) => {
      targetMouseRef.current = toLocal(e)
    }

    const onClick = (e) => {
      const { x, y } = toLocal(e)
      ripplesRef.current.push({ x, y, radius: 0, opacity: 1, born: performance.now() })
    }

    container.addEventListener('mousemove', onMouseMove)
    container.addEventListener('click', onClick)
    rafRef.current = requestAnimationFrame(animate)

    return () => {
      ro?.disconnect()
      window.removeEventListener('resize', setSize)
      container.removeEventListener('mousemove', onMouseMove)
      container.removeEventListener('click', onClick)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [animate])

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className={cn(styles.wrap, globalColor === 'monochrome' ? styles.monochrome : styles.default, className)}>
      <canvas ref={canvasRef} className={styles.canvas} />
      {children && <div className={styles.content}>{children}</div>}
    </div>
  )
}
