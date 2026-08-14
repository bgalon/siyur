import 'maplibre-gl/dist/maplibre-gl.css'
import './style.css'
import './plan.css'
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
  type SiteRecordV1,
  type SitesResponse,
} from './map'
import {
  EMPTY_PLAN_MODEL,
  approvePlan,
  fetchPlan,
  mountPlanForm,
  mountPlanReview,
  streamPlan,
  type ProposeRequest,
} from './plan'

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

/**
 * Dev-only basemap. Served by `vite.config.ts`'s `siyur-dev-basemap-assets` out of
 * `web/dev-assets/`, which `scripts/fetch-basemap.sh` populates.
 *
 * The path is a demo fixture, not a hardcoded place: any `.pmtiles` over any city
 * renders through the same call (FR-001).
 */
const DEV_BASEMAP_ARCHIVE = '/tiles/rhodes.pmtiles'

const container = document.getElementById('map')
if (container) {
  // Production keeps EMPTY_STYLE; the real tile source arrives inside the compiled
  // bundle at DU-05. The dev basemap is applied afterwards via `setStyle` rather
  // than passed in here, and that shape is deliberate on two counts:
  //
  //   1. `import.meta.env.DEV` is statically replaced at build time, so this whole
  //      branch — and with it the only `import()` of `./map/basemap` — is dropped
  //      from a production build. A *static* import would not have been: measured,
  //      the `@protomaps/basemaps` theme tree-shakes but the `pmtiles` reader does
  //      not, and it shipped ~30 KB into the prod bundle until this became dynamic.
  //   2. Markers are DOM overlays, not style layers, so `setStyle` swaps the
  //      basemap underneath them without disturbing anything mounted below.
  const { map, attribution } = createMapWithAttribution(container)

  if (import.meta.env.DEV) {
    void import('./map/basemap')
      .then(({ basemapStyle }) => map.setStyle(basemapStyle({ archiveUrl: DEV_BASEMAP_ARCHIVE })))
      .catch(onError)
  }

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

  // --- Phase 2 (ux-handoff § Screens 3 "Plan the day"): the request form and the
  // review panel. Its own panel rather than a third row in the sheet — the sheet is
  // capped at 55vh and a reviewed day is the longest thing this app renders, and the
  // reviewer needs the map beside it, not under it.
  const planPanel = document.createElement('aside')
  planPanel.className = 'siyur-plan-panel'
  const planTitle = document.createElement('h2')
  planTitle.className = 'siyur-plan-panel__title'
  planTitle.textContent = 'Plan a day'
  const planHint = document.createElement('p')
  planHint.className = 'siyur-plan-panel__hint'
  planHint.textContent =
    'Delimit an area first — a day is planned only from the cited places researched there.'
  const formHost = document.createElement('div')
  const reviewHost = document.createElement('div')
  planPanel.append(planTitle, planHint, formHost, reviewHost)

  document.body.append(controls, sheet, planPanel)

  /**
   * Every commons record the map has fetched, keyed by id — the lookup
   * `renderPlanStop` needs to put a stamped name, address and opening hours on a stop.
   *
   * Accumulated rather than replaced. `SitesLayer` refetches per viewport, and a stop
   * can easily be off-screen while its day is being reviewed; replacing would make the
   * panel say "not loaded on this device" about a place the user looked at a pan ago.
   */
  const commons = new Map<string, SiteRecordV1>()

  const review = mountPlanReview(reviewHost, {
    onApprove: (planId) => approve(planId),
    onApproveFailure: onError,
  })

  /**
   * Fold a `GET /sites` response into {@link commons}.
   *
   * While a day **is** on screen the panel is re-rendered only when a stop that could
   * not be shown now can be: `PlanReviewSurface.update` replaces the whole subtree, so
   * re-rendering on every `moveend` would move the page under the person reading it —
   * and blur whatever they had focused — to produce identical DOM.
   *
   * That skip must **not** extend to the case with no day on screen, which is every
   * viewport fetched before the plan is proposed. Gating the update on "it unblocks a
   * stop" alone left the model holding the empty map it was constructed with, and the
   * first plan then rendered every stop as "not loaded on this device" — with no chips,
   * which is indistinguishable from the provenance funnel correctly withholding an
   * unstamped value. Caught by `test/plan-wiring.test.ts`.
   */
  const rememberCommons = (response: SitesResponse): void => {
    let added = false
    for (const site of response.sites) {
      if (!commons.has(site.id)) {
        commons.set(site.id, site)
        added = true
      }
    }
    if (!added) return
    const frame = review.current.itinerary
    if (frame.kind === 'itinerary') {
      const known = review.current.sites
      const unblocks = frame.itinerary.stops.some(
        (stop) => !known.has(stop.site_id) && commons.has(stop.site_id),
      )
      if (!unblocks) return
    }
    // A copy, so `review.current.sites` stays the snapshot the panel was rendered from
    // and the check above keeps meaning what it says.
    review.update({ sites: new Map(commons) })
  }

  // Cited commons for the current viewport (Spec 001 T042/T044).
  const layer = mountSitesLayer(map, attribution, {
    getToken,
    onError: (error) => onError(error),
    onSites: rememberCommons,
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

  /**
   * The one propose path: stream `POST /plans` into the review panel, then read the
   * persisted plan back.
   *
   * The read-back is what puts the aggregate credit on screen — `PlanDetail.attribution`
   * is the only place it comes from, and no SSE frame carries it — and it replaces the
   * streamed approval state with the row's own. A read-back that fails is reported and
   * dropped: the proposal on screen came from the stream and is still the truth about it.
   *
   * Rejections are **not** caught here. `PlanRequestForm.submit` states a transport
   * failure on the form itself and releases the button, which is where a user who just
   * pressed "Plan this day" is looking.
   */
  const propose = async (request: ProposeRequest): Promise<void> => {
    review.start()
    let planId: string | undefined
    try {
      const proposal = await streamPlan({
        request,
        token: getToken(),
        handlers: review.handlers(),
      })
      planId = proposal.done?.plan_id
    } catch (error) {
      // `start()` set the row to `proposing`; leaving it there after the request failed
      // asserts that something is still running on the server. The visible reason is
      // unchanged either way (no itinerary arrived, so the panel already says so).
      review.update({ approval: EMPTY_PLAN_MODEL.approval })
      throw error
    }
    if (!planId) return
    try {
      review.applyDetail(planId, await fetchPlan(planId, { token: getToken() }))
    } catch (error) {
      onError(error)
    }
  }

  /**
   * The HITL gate (FR-005). Reached only from the approve button, which
   * `renderApproveControl` binds only in the branch where the server can answer `200`.
   *
   * **A failed read-back must not reject.** Past the `approvePlan` line the approval has
   * happened; `PlanReviewSurface` renders any rejection from here through
   * `describeApprovalFailure`, every branch of which ends on "nothing was approved". For
   * the one irreversible action in the product, that is the wrong direction to be wrong
   * in — so the `GET` is a separate `try` and its failure only costs the fresher body.
   */
  const approve = async (planId: string): Promise<void> => {
    const result = await approvePlan(planId, { token: getToken() })
    try {
      review.applyDetail(result.plan_id, await fetchPlan(result.plan_id, { token: getToken() }))
    } catch (error) {
      onError(error)
      review.update({
        approval: {
          state: result.state,
          approved_at: result.approved_at,
          // Not invented: the approve body carries no successor id, so whatever the
          // panel already knew stands.
          superseded_by: review.current.approval.superseded_by,
        },
      })
    }
  }

  const form = mountPlanForm(formHost, {
    onSubmit: (request) => propose(request),
    onSubmitFailure: onError,
  })

  // --- The delimit step (US1's "delimit the area"). The bbox comes from the
  // map, the name from the user; nothing here is bound to a place.
  const delimit = async (area: AreaRequest): Promise<void> => {
    const resolution = await resolveAndApply(reuse, { area, token: getToken() })
    // Bind the form to what was just resolved. The field is read-only in the form
    // because an area id is a UUID, not something a person types — and the plan must be
    // for the area on screen, not for whatever was typed last.
    form.setArea(resolution.area_id)
    planHint.textContent =
      'Set your budgets and the date you are planning for — the date decides which ' +
      'opening hours and public holidays apply.'
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
