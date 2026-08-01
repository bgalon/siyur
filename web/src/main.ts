import 'maplibre-gl/dist/maplibre-gl.css'
import './style.css'
import { registerSW } from 'virtual:pwa-register'
import {
  boundsOfPolygon,
  createMapWithAttribution,
  mountAreaReuse,
  mountDelimitControl,
  mountResearchProgress,
  mountSitesLayer,
  resolveAndApply,
  runResearch,
  type AreaRequest,
  type ResearchRequest,
} from './map'

// Precache the app shell via Workbox (vite-plugin-pwa). This is what makes the
// empty-map skeleton load offline — the DU-00 "empty map renders offline" gate.
registerSW({ immediate: true })

/**
 * The API authenticates with a **signed session cookie**, not a bearer token
 * (`api/security.py`). `fetch` defaults to `credentials: 'same-origin'`, so the
 * cookie rides along on its own once the app and the API share an origin — which
 * is what `vite.config.ts`'s dev proxy arranges. This hook stays because the
 * surfaces accept an optional token and a future non-cookie deployment would
 * need one; today it is correctly `null`, and an unauthenticated call still gets
 * a `401` that the surfaces report rather than paper over.
 */
const getToken = (): string | null => null

const onError = (error: unknown): void => {
  console.warn('[siyur]', error)
}

const container = document.getElementById('map')
if (container) {
  const { map, attribution } = createMapWithAttribution(container)

  // Dev-only handle. MapLibre's handlers ignore synthetic wheel/click events, so
  // an automated browser check (and the DU-07 airplane-mode e2e) has no way to
  // position the map without one. `import.meta.env.DEV` is statically replaced at
  // build time, so this whole block is dropped from the production bundle.
  if (import.meta.env.DEV) {
    ;(window as unknown as { __siyurMap?: unknown }).__siyurMap = map
  }

  // --- Phase 1 shell (ux-handoff README § Screens 1 "Define the area"): a
  // full-bleed map, a floating control pill over it, and a bottom sheet holding
  // the commons-coverage card and the research progress.
  const controls = document.createElement('div')
  controls.className = 'siyur-controls'

  const sheet = document.createElement('section')
  sheet.className = 'siyur-sheet'
  const grip = document.createElement('div')
  grip.className = 'siyur-sheet__grip'
  // Coverage card above, research progress below it (mock reading order).
  const coverageHost = document.createElement('div')
  coverageHost.className = 'siyur-sheet__body'
  const progressHost = document.createElement('div')
  progressHost.className = 'siyur-sheet__body'
  sheet.append(grip, coverageHost, progressHost)

  document.body.append(controls, sheet)

  // Cited commons for the current viewport (Spec 001 T042/T044).
  const layer = mountSitesLayer(map, attribution, {
    getToken,
    onError: (error) => onError(error),
  })

  const progress = mountResearchProgress(progressHost)

  /**
   * The one research code path. Reached only from the coverage card's button —
   * `areas.ts` owns the covered-vs-not decision and calls this from a click and
   * nowhere else, so a covered area is still never auto-researched (FR-006).
   */
  const research = async (request: ResearchRequest): Promise<void> => {
    await runResearch(progress, { request, token: getToken() })
    // Read back what the pass persisted. `GET /sites` is read-only.
    await layer.refresh()
  }

  const reuse = mountAreaReuse(layer, coverageHost, {
    requestResearch: research,
    onError,
  })

  // --- The delimit step (US1's "delimit the area"). The bbox comes from the
  // map, the name from the user; nothing here is bound to a place.
  const delimit = async (area: AreaRequest): Promise<void> => {
    const resolution = await resolveAndApply(reuse, { area, token: getToken() })
    // Frame what was just resolved. `createMap` opens on the whole world, so
    // without this an old town resolves to a sub-pixel speck and the map reads
    // as empty even when the commons holds hundreds of cited places for it.
    const bounds = boundsOfPolygon(resolution.polygon)
    if (bounds) {
      // Copied into mutable tuples: `fitBounds` takes a mutable `LngLatBoundsLike`,
      // while `boundsOfPolygon` returns a readonly extent on purpose.
      map.fitBounds(
        [
          [bounds[0][0], bounds[0][1]],
          [bounds[1][0], bounds[1][1]],
        ],
        { padding: 48, maxZoom: 17, duration: 600 },
      )
    }
  }

  mountDelimitControl(controls, {
    getBounds: () => map.getBounds(),
    onDelimit: delimit,
    onError,
  })
}
