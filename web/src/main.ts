import 'maplibre-gl/dist/maplibre-gl.css'
import './style.css'
import { registerSW } from 'virtual:pwa-register'
import { createMapWithAttribution, mountSitesLayer } from './map'

// Precache the app shell via Workbox (vite-plugin-pwa). This is what makes the
// empty-map skeleton load offline — the DU-00 "empty map renders offline" gate.
registerSW({ immediate: true })

const container = document.getElementById('map')
if (container) {
  const { map, attribution } = createMapWithAttribution(container)
  // Cited commons for the current viewport (Spec 001 T042/T044). `GET /sites`
  // requires a bearer token; auth wiring lands with the api/auth task, so until
  // then the layer reports the 401 rather than rendering anything unsourced.
  mountSitesLayer(map, attribution, {
    onError: (error) => console.warn('[siyur] /sites unavailable', error),
  })
}
