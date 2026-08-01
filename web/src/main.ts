import 'maplibre-gl/dist/maplibre-gl.css'
import './style.css'
import { registerSW } from 'virtual:pwa-register'
import {
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
 * Auth plumbing (Google SSO → JWT) belongs to the api/auth task. Until it lands
 * this yields `null`, every call goes out unauthenticated, and the endpoints
 * answer `401` — which the surfaces report rather than papering over.
 */
const getToken = (): string | null => null

const onError = (error: unknown): void => {
  console.warn('[siyur]', error)
}

const container = document.getElementById('map')
if (container) {
  const { map, attribution } = createMapWithAttribution(container)

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
    await resolveAndApply(reuse, { area, token: getToken() })
  }

  mountDelimitControl(controls, {
    getBounds: () => map.getBounds(),
    onDelimit: delimit,
    onError,
  })
}
