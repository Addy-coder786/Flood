import { useEffect, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import styles from './NoticeBanner.module.css'

export default function NoticeBanner() {
  const { notice, bootError } = useFloodNet()
  const [visible, setVisible] = useState(null)

  useEffect(() => {
    if (!notice) return
    setVisible(notice)
    if (notice.kind === 'info') {
      const t = setTimeout(() => setVisible(null), 4500)
      return () => clearTimeout(t)
    }
  }, [notice])

  const shown = visible || (bootError ? { text: `Some pilot data failed to load: ${bootError}`, kind: 'error' } : null)
  if (!shown) return null
  return <div className={`${styles.banner} ${shown.kind === 'info' ? styles.info : styles.error}`}>{shown.text}</div>
}
