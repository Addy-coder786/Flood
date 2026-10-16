import { Component, useEffect, useState } from 'react'
import GatewayFlow from '../ui/gateway-flow.jsx'
import KineticGrid from '../ui/kinetic-grid.jsx'
import styles from './HeroVisual.module.css'

// ============================================================================================
// HeroVisual -- an ISOLATED background layer for the landing hero, integrating the two supplied
// 21st.dev components (GatewayFlow: canvas particle-flow converging to centre; KineticGrid:
// cursor-reactive grid warp + click ripples). Takes no props, imports nothing from
// Context/API/routing/simulation -- it cannot touch app state because it never imports it.
//
// Layering: KineticGrid sits behind, at low opacity, as the "secondary environmental layer" the
// integration spec asked for (subtle grid texture, cursor warp). GatewayFlow sits above it as the
// primary hero visual (the converging particle flow). Both run in `mode="light"`/FloodNet colours
// (see gateway-flow.jsx's `patch()` and kinetic-grid.jsx's `theme.default`).
//
// Safety: wrapped in a real React error boundary (the only mechanism that can catch a render-time
// error in a child component) -- if either supplied component throws, a static CSS-only fallback
// renders instead, and the exception never reaches LandingPage/App. `prefers-reduced-motion` is
// honoured by not mounting either animated component at all when active, showing the same static
// fallback (both supplied components run continuous rAF loops with no built-in reduced-motion
// handling of their own -- gating at this wrapper level is the correct place to add it without
// hand-editing their preserved internals).
// ============================================================================================

class HeroVisualBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch() {
    // Intentionally silent: this is decorative-only. The static fallback below is the recovery.
  }
  render() {
    if (this.state.failed) return <div className={styles.staticFallback} aria-hidden="true" />
    return this.props.children
  }
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false,
  )
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

function AnimatedLayers() {
  return (
    <div className={styles.layer} aria-hidden="true">
      <div className={styles.gridLayer}>
        <KineticGrid globalColor="default" />
      </div>
      <div className={styles.flowLayer}>
        <GatewayFlow mode="light" speed={0.9} density={0.85} opacity={0.6} />
      </div>
    </div>
  )
}

export default function HeroVisual() {
  const reducedMotion = useReducedMotion()

  if (reducedMotion) {
    return <div className={styles.staticFallback} aria-hidden="true" />
  }

  return (
    <HeroVisualBoundary>
      <AnimatedLayers />
    </HeroVisualBoundary>
  )
}
