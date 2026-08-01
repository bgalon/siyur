/**
 * T052 — the reuse surface for an already-researched area (FR-006 / US2 / SC-003).
 *
 * Contract: `specs/001-research-cited-sites/contracts/areas.md` (`POST /areas`)
 * and `contracts/research.md` (`POST /areas/{id}/research`, `force_refresh`).
 *
 * The single behaviour this module exists to guarantee:
 *
 * > **A covered area is never auto-researched.** When `coverage.covered`, the
 * > client shows the *existing* cited data (via `GET /sites`) and offers an
 * > explicit refresh. Research is a user action.
 *
 * That is structural, not a review discipline: `requestResearch` is referenced
 * in exactly one place in this file — inside the button's `click` listener.
 * There is no other path from a coverage response to a research pass.
 *
 * The client also invents no data here. The count, the staleness date and the
 * refresh flag are all rendered from the response; a coverage block the guard
 * could not read renders as "not covered", never as a guess.
 */

import { sanitiseAreaResolution } from './guards'
import type { AreaResolution, GeoPolygon } from './types'

/** Default same-origin endpoint; override for a split API host. */
export const DEFAULT_AREAS_ENDPOINT = '/areas'

/** Thrown when `POST /areas` answers with a non-2xx status (`401`, `422`, `404`). */
export class AreaRequestError extends Error {
  constructor(readonly status: number, message?: string) {
    super(message ?? `POST /areas failed with status ${status}`)
    this.name = 'AreaRequestError'
  }
}

/** Thrown when the body parsed but carried no `area_id` to act on. */
export class AreaResponseError extends Error {
  constructor(message = 'POST /areas returned no usable area_id') {
    super(message)
    this.name = 'AreaResponseError'
  }
}

/* ----------------------------------------------------------------- fetch --- */

/** `POST /areas` request body — one of `name`, `bbox` or `polygon` (FR-001). */
export interface AreaRequest {
  readonly name?: string
  /** `[minLon, minLat, maxLon, maxLat]`, EPSG:4326. */
  readonly bbox?: readonly [number, number, number, number]
  readonly polygon?: GeoPolygon
}

export interface ResolveAreaOptions {
  readonly area: AreaRequest
  readonly endpoint?: string
  /** Injectable for tests; defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch
  readonly token?: string | null
  readonly signal?: AbortSignal
}

/** Resolve + coverage-check a user-delimited area. Read-only: starts nothing. */
export async function resolveArea(options: ResolveAreaOptions): Promise<AreaResolution> {
  const {
    area,
    endpoint = DEFAULT_AREAS_ENDPOINT,
    fetchImpl = globalThis.fetch,
    token,
    signal,
  } = options

  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetchImpl(endpoint, {
    method: 'POST',
    headers,
    body: JSON.stringify(area),
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) throw new AreaRequestError(response.status)

  const resolution = sanitiseAreaResolution(await response.json())
  if (!resolution) throw new AreaResponseError()
  return resolution
}

/** The research endpoint for a resolved area (`contracts/research.md`). */
export function researchPath(areaId: string, endpoint = DEFAULT_AREAS_ENDPOINT): string {
  return `${endpoint}/${encodeURIComponent(areaId)}/research`
}

/* ------------------------------------------------------------- decision --- */

/**
 * What to do with a resolved area.
 *
 * `'reuse'` — the commons already covers it: show what is there, offer refresh.
 * `'research'` — nothing known yet, so a research pass is *available*. Even then
 * this module does not start one; only the user's click does.
 */
export type CoverageAction = 'reuse' | 'research'

export function coverageAction(resolution: AreaResolution): CoverageAction {
  return resolution.coverage.covered ? 'reuse' : 'research'
}

/* ------------------------------------------------------------ staleness --- */

const DAY_MS = 86_400_000

/**
 * Human-readable age of the stalest covered record, so the user can judge
 * whether a refresh is worth it.
 *
 * The server's own date is always rendered; the relative age is appended only
 * when the date parses. An unparseable value is echoed verbatim rather than
 * dropped or reformatted — the stamp is data, not a template.
 */
export function describeStaleness(observedAt: string | null, now: Date = new Date()): string | null {
  if (!observedAt) return null
  const then = new Date(observedAt)
  if (Number.isNaN(then.getTime())) return observedAt

  const days = Math.floor((now.getTime() - then.getTime()) / DAY_MS)
  if (days < 0) return observedAt
  if (days === 0) return `${observedAt} · today`
  return `${observedAt} · ${days} day${days === 1 ? '' : 's'} ago`
}

/* ------------------------------------------------------------------ card --- */

/** `POST /areas/{area_id}/research` payload the user's click asks for. */
export interface ResearchRequest {
  readonly area_id: string
  readonly force_refresh: boolean
}

export interface CoverageCardOptions {
  /**
   * Called **only** from the button's click handler. Nothing in this module
   * calls it on mount — that is the FR-006 guarantee.
   */
  readonly requestResearch?: (request: ResearchRequest) => void | Promise<void>
  /** Injectable clock, for a deterministic staleness string in tests. */
  readonly now?: () => Date
}

const appendLine = (root: HTMLElement, className: string, text: string): void => {
  const line = document.createElement('p')
  line.className = className
  line.textContent = text
  root.append(line)
}

/**
 * The commons-coverage card (`ux-handoff/README.md` § Phase 1): what is already
 * known about this area, how old it is, and one explicit call to action.
 *
 * `data-covered` / `data-refresh-available` / `data-stalest-observed-at` carry
 * the raw server values so a caller (or a test) reads the decision, not prose.
 */
export function buildCoverageCard(
  resolution: AreaResolution,
  options: CoverageCardOptions = {},
): HTMLElement {
  const { coverage } = resolution
  const covered = coverage.covered

  const root = document.createElement('section')
  root.className = 'siyur-coverage'
  root.dataset.areaId = resolution.area_id
  root.dataset.covered = String(covered)
  root.dataset.refreshAvailable = String(coverage.refresh_available)
  if (coverage.stalest_observed_at) {
    root.dataset.stalestObservedAt = coverage.stalest_observed_at
  }

  appendLine(
    root,
    'siyur-coverage__title',
    covered ? 'Already in the commons' : 'Not researched yet',
  )
  appendLine(
    root,
    'siyur-coverage__count',
    covered
      ? `${coverage.known_site_count} cited place${coverage.known_site_count === 1 ? '' : 's'} already researched here`
      : 'No cited places here yet',
  )

  const staleness = describeStaleness(coverage.stalest_observed_at, options.now?.())
  if (staleness) {
    appendLine(root, 'siyur-coverage__staleness', `Oldest observation: ${staleness}`)
  }

  // The refresh affordance. It exists whenever the server says a refresh is
  // available — which, per the contract, is always the case for a covered area.
  const action = document.createElement('button')
  action.type = 'button'
  action.className = 'siyur-coverage__action'
  action.dataset.action = covered ? 'refresh' : 'research'
  action.textContent = covered ? 'Refresh this area →' : 'Start researching →'
  action.disabled = covered && !coverage.refresh_available

  const { requestResearch } = options
  if (requestResearch) {
    // ⬇︎ THE ONLY call site. Research runs on a user gesture and nowhere else.
    action.addEventListener('click', () => {
      void requestResearch({ area_id: resolution.area_id, force_refresh: covered })
    })
  }
  root.append(action)

  return root
}

/* ------------------------------------------------------------ controller --- */

export interface AreaReuseOptions extends CoverageCardOptions {
  /**
   * Show the cited data already in the commons — in practice
   * `() => sitesLayer.refresh()`, i.e. a plain `GET /sites`. Read-only by
   * construction: `GET /sites` triggers no research (`contracts/sites.md`).
   */
  readonly showExistingSites?: () => void | Promise<void>
  readonly onError?: (error: unknown) => void
}

/**
 * Renders the coverage card for the last resolved area into `container` and,
 * when the area is covered, asks the caller to show the existing sites.
 *
 * Deliberately *not* a research orchestrator: it holds no research code path at
 * all. The card's button is the only way research is ever requested.
 */
export class AreaReuseSurface {
  private card: HTMLElement | null = null

  constructor(
    private readonly container: HTMLElement,
    private readonly options: AreaReuseOptions = {},
  ) {}

  /** The mounted card (`null` before the first {@link apply}). */
  get element(): HTMLElement | null {
    return this.card
  }

  /**
   * Apply a resolved area. Returns the action taken so a caller can assert on
   * it: `'reuse'` means existing data was shown and no research was started.
   */
  async apply(resolution: AreaResolution): Promise<CoverageAction> {
    const action = coverageAction(resolution)

    this.card?.remove()
    this.card = buildCoverageCard(resolution, this.options)
    this.container.append(this.card)

    if (action === 'reuse') {
      try {
        await this.options.showExistingSites?.()
      } catch (error) {
        this.options.onError?.(error)
      }
    }
    return action
  }

  destroy(): void {
    this.card?.remove()
    this.card = null
  }
}

/** Resolve an area and apply it to a surface in one step. */
export async function resolveAndApply(
  surface: AreaReuseSurface,
  options: ResolveAreaOptions,
): Promise<AreaResolution> {
  const resolution = await resolveArea(options)
  await surface.apply(resolution)
  return resolution
}
