import 'maplibre-gl/dist/maplibre-gl.css'
import './style.css'
import { registerSW } from 'virtual:pwa-register'
import { createMap } from './map'

// Precache the app shell via Workbox (vite-plugin-pwa). This is what makes the
// empty-map skeleton load offline — the DU-00 "empty map renders offline" gate.
registerSW({ immediate: true })

const container = document.getElementById('map')
if (container) {
  createMap(container)
}
