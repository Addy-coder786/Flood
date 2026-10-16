import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { routeFor } from './lib/publicAlert.js'

// Three screens share one build (DECISIONS D-45, D-47): <base>alerts/<public_id> is the public alert page a resident
// opens from an SMS/WhatsApp link, <base>driver is the rescue driver's trip page, anything else is the command
// centre. Each is a dynamic import so the public and driver pages never download the dashboard (Leaflet, three.js).
const route = routeFor(window.location.pathname, import.meta.env.BASE_URL)
const root = createRoot(document.getElementById('root'))

let screen
if (route.kind === 'public') screen = import('./public/PublicAlertApp.jsx')
else if (route.kind === 'driver') screen = import('./driver/DriverApp.jsx')
else screen = import('./App.jsx')

if (route.kind !== 'app') root.render(<p style={{ padding: 16, fontSize: 16 }}>Loading FloodNet…</p>)
const props = route.kind === 'public' ? { publicId: route.id } : {}

screen.then(
  (mod) => {
    const Screen = mod.default
    root.render(
      <StrictMode>
        <Screen {...props} />
      </StrictMode>,
    )
  },
  () => root.render(<p style={{ padding: 16, fontSize: 16 }}>FloodNet could not load. Check your connection and reload the page.</p>),
)
