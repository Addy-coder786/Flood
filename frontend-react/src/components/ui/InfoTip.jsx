import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import styles from './InfoTip.module.css'

// Compact "i" disclosure for detail that is useful but not needed at first glance. A real <button> with
// aria-expanded / aria-controls; Enter or Space toggles, Escape closes and returns focus, a click outside closes.
// The note is positioned inside the nearest scrolling container so it never causes horizontal scroll.

function clampLeft(btn, width) {
  let box = { left: 0, right: document.documentElement.clientWidth }
  for (let el = btn.parentElement; el && el !== document.body; el = el.parentElement) {
    const o = getComputedStyle(el).overflowY
    if (o === 'auto' || o === 'scroll') { box = el.getBoundingClientRect(); break }
  }
  const b = btn.getBoundingClientRect()
  const w = Math.min(width, box.right - box.left - 16)
  const want = b.left + b.width / 2 - w / 2
  const left = Math.max(box.left + 8, Math.min(want, box.right - 8 - w))
  return { left: left - b.left, width: w }
}

export default function InfoTip({ label = 'More information', children, width = 280 }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState(null)
  const id = useId()
  const btn = useRef(null)
  const wrap = useRef(null)

  useLayoutEffect(() => {
    if (open && btn.current) setPos(clampLeft(btn.current, width))
  }, [open, width])

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (!wrap.current?.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus() } }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])

  return (
    <span ref={wrap} className={styles.wrap}>
      <button ref={btn} type="button" className={styles.btn} aria-label={label} title={label}
        aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        <span aria-hidden="true">i</span>
      </button>
      <span id={id} role="note" className={styles.pop} hidden={!open}
        style={pos ? { left: pos.left, width: pos.width } : undefined}>
        {children}
      </span>
    </span>
  )
}
