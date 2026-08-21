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

import { renderSourcedValue } from './attribution-chip'
import { isSourceRef, sanitiseAreaResolution } from './guards'
import type { AreaResolution, GeoPolygon, SourceRef } from './types'

/** Default same-origin endpoint; override for a split API host. */
export const DEFAULT_AREAS_ENDPOINT = '/areas'

/** Thrown when `POST /areas` answers with a non-2xx status (`401`, `422`, `404`). */
export class AreaRequestError extends Error {
  constructor(readonly status: number, message?: string) {
    super(message ?? `POST /areas failed with status ${status}`)
    this.name = 'AreaRequestError'
  }
}

/**
 * The `404` that **is** an answer: the name matched several places and the server sent
 * them all (`contracts/areas.md` — *"404 name not resolvable (with disambiguation
 * candidates when Nominatim returns several)"*).
 *
 * Until Phase A this status was kept and the body thrown away, so a search that took 61 s
 * to return twenty usable answers reported "not found" while the answers sat in the
 * response. That is what this class exists to stop: the candidates travel with the error.
 */
export class AreaNotResolvedError extends AreaRequestError {
  constructor(
    /** The server's own sentence about why the name did not resolve. */
    readonly detail: string | null,
    readonly candidates: readonly AreaCandidate[],
  ) {
    super(404, detail ?? 'POST /areas could not resolve that name')
    this.name = 'AreaNotResolvedError'
  }
}

/** Thrown when the body parsed but carried no `area_id` to act on. */
export class AreaResponseError extends Error {
  constructor(message = 'POST /areas returned no usable area_id') {
    super(message)
    this.name = 'AreaResponseError'
  }
}

/* --------------------------------------------------- disambiguation (404) --- */

/**
 * One place the searched name could have meant (`api/areas.py::_candidate`).
 *
 * The server sends **bounds, not the ring** — a division polygon runs to tens of thousands
 * of vertices and the client re-delimits by bbox anyway. Every field is nullable here
 * because the client narrows before it renders: an option it cannot read is reported as
 * unreadable, never repaired into a plausible one.
 */
export interface AreaCandidate {
  /** The division's own name. Commons-derived, so it renders **only** with its stamp. */
  readonly name: string | null
  /** The resolver's own match score. Not sourced data — see {@link buildDisambiguation}. */
  readonly confidence: number | null
  /** `[minLon, minLat, maxLon, maxLat]`, EPSG:4326 — what picking this option re-sends. */
  readonly bbox: readonly [number, number, number, number] | null
  readonly source: SourceRef | null
}

/** An EPSG:4326 extent with actual area, else `null`. A degenerate bbox is a `422`. */
function candidateBbox(raw: unknown): readonly [number, number, number, number] | null {
  if (!Array.isArray(raw) || raw.length !== 4) return null
  const values = raw as unknown[]
  if (!values.every((n): n is number => typeof n === 'number' && Number.isFinite(n))) return null
  const [minLon, minLat, maxLon, maxLat] = values as [number, number, number, number]
  if (minLon < -180 || maxLon > 180 || minLat < -90 || maxLat > 90) return null
  return maxLon > minLon && maxLat > minLat ? [minLon, minLat, maxLon, maxLat] : null
}

/**
 * Narrow the candidate list from a `404` body.
 *
 * **Order is the server's**, and is never re-sorted by confidence. The server returned
 * twenty options *because it could not decide*; re-ranking them would be this client
 * quietly making the decision it declined to make.
 *
 * Nothing is dropped either. A candidate whose name or extent could not be read is kept
 * and rendered as unpickable with the reason stated — silently shortening a list of
 * twenty to a list of eleven is the same class of bug as discarding it entirely.
 */
export function sanitiseAreaCandidates(raw: unknown): readonly AreaCandidate[] {
  if (!Array.isArray(raw)) return []
  const out: AreaCandidate[] = []
  for (const row of raw) {
    if (typeof row !== 'object' || row === null || Array.isArray(row)) continue
    const record = row as Record<string, unknown>
    const name = typeof record.name === 'string' && record.name.trim() !== '' ? record.name : null
    const confidence =
      typeof record.confidence === 'number' && Number.isFinite(record.confidence)
        ? record.confidence
        : null
    out.push({
      name,
      confidence,
      bbox: candidateBbox(record.bbox),
      source: isSourceRef(record.source) ? record.source : null,
    })
  }
  return out
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
  if (!response.ok) {
    // **The body of a `404` is the answer, not noise.** It carries `{message, candidates}`
    // — up to twenty places the name could have meant — **nested under `detail`**. Keeping
    // only `response.status` here was the original defect; reading `candidates` from the
    // *root* of the body was the same defect wearing the fix's clothes (FAIL-017). Either
    // way the user was told "not found" while the answer sat in the response they had
    // already waited a minute for.
    if (response.status === 404) {
      const body = await readJsonBody(response)
      const envelope =
        typeof body === 'object' && body !== null && !Array.isArray(body)
          ? (body as Record<string, unknown>)
          : {}
      // **The payload is nested under `detail`, and reading the root is FAIL-017.**
      // FastAPI serialises `HTTPException(detail=X)` as `{"detail": X}`, so the message and
      // candidates arrive one level down. Reading `envelope.candidates` found nothing on
      // every real response, and the client fell through to the status-only error that
      // FAIL-013 exists to prevent — a 65-second name search that then said nothing at all.
      //
      // The unit test agreed with the bug because its fixture also sent the body un-nested:
      // both halves shared one assumption and neither was ever compared to the server. The
      // root is NOT read as a fallback — a fallback would let the two shapes drift apart
      // again silently, and this failure is only visible when they are forced to agree.
      const detail =
        typeof envelope.detail === 'object' &&
        envelope.detail !== null &&
        !Array.isArray(envelope.detail)
          ? (envelope.detail as Record<string, unknown>)
          : {}
      const candidates = sanitiseAreaCandidates(detail.candidates)
      if (candidates.length > 0) {
        throw new AreaNotResolvedError(
          typeof detail.message === 'string' && detail.message.trim() !== ''
            ? detail.message
            : null,
          candidates,
        )
      }
    }
    throw new AreaRequestError(response.status)
  }

  const resolution = sanitiseAreaResolution(await readJsonBody(response))
  if (!resolution) throw new AreaResponseError()
  return resolution
}

/** `JSON.parse` answering `undefined` instead of throwing on a body that is not JSON. */
async function readJsonBody(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown
  } catch {
    return undefined
  }
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
  /**
   * **The count is read in BOTH branches (UX-09).**
   *
   * The uncovered branch used to be the hardcoded string "No cited places here yet",
   * and the audit caught it saying exactly that about an area the server had just
   * reported `known_site_count: 958` for. The product's core asset, denied to the user
   * by a literal — and `covered` is *not* "we hold sites here": it is "this area has
   * been researched" (`commons/repository.py::coverage`, ADR-0018), so the two
   * genuinely differ and the honest answer needs both facts, not one of them.
   *
   * Three states, then, not two — and the zero case keeps its own sentence, because
   * "0 cited places" is a number where "none yet" is an answer.
   */
  const count = coverage.known_site_count
  const places = `${count} cited place${count === 1 ? '' : 's'}`
  appendLine(
    root,
    'siyur-coverage__count',
    covered
      ? `${places} already researched here`
      : count > 0
        ? `${places} already in the commons here, from earlier research nearby — but this ` +
          `area itself has not been researched yet.`
        : 'No cited places here yet',
  )
  // The raw number, so a caller (or a test) reads the value rather than the sentence.
  root.dataset.knownSiteCount = String(count)

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
  action.dataset.pending = 'false'
  action.textContent = covered ? 'Refresh this area →' : 'Start researching →'
  action.disabled = covered && !coverage.refresh_available

  const { requestResearch } = options
  if (requestResearch) {
    /**
     * **In flight is a state, and the button carries it (UX-08).**
     *
     * A pass runs for tens of seconds to minutes and the control stayed enabled for all
     * of it, so a user who saw nothing happen tapped again and queued a second pass
     * behind a single-worker API. The `409` guard in `api/areas.py` is the last line of
     * defence against that, not the first — and it is process-local, so it is not even a
     * reliable one.
     *
     * `restore` remembers the button's *original* disabled state rather than assuming
     * `false`: a covered area with `refresh_available: false` mounts already disabled,
     * and re-enabling it on the way out would offer a refresh the server just refused.
     */
    const label = action.textContent
    const restore = action.disabled
    action.addEventListener('click', () => {
      if (action.dataset.pending === 'true') return
      action.dataset.pending = 'true'
      action.disabled = true
      action.setAttribute('aria-busy', 'true')
      action.textContent = covered ? 'Refreshing…' : 'Researching…'
      // ⬇︎ THE ONLY call site. Research runs on a user gesture and nowhere else.
      void Promise.resolve(
        requestResearch({ area_id: resolution.area_id, force_refresh: covered }),
      ).finally(() => {
        action.dataset.pending = 'false'
        action.disabled = restore
        action.setAttribute('aria-busy', 'false')
        action.textContent = label
      })
    })
  }
  root.append(action)

  return root
}

/* --------------------------------------------------- the disambiguation UI --- */

export interface DisambiguationOptions {
  /**
   * Called **only** from a candidate's own button. Receives that candidate's bbox
   * verbatim, which the caller re-sends as `POST /areas { bbox }`.
   */
  readonly onPick?: (candidate: AreaCandidate) => void | Promise<void>
  readonly onDismiss?: () => void
}

/**
 * The list of places the searched name could have meant.
 *
 * **Nothing is chosen for the user.** No candidate is auto-picked, not even the one with
 * the highest confidence, and the list is not re-ordered by it: the server returned twenty
 * *because it could not decide*, and deciding on its behalf is how someone ends up
 * delimiting the wrong city and researching it for forty seconds before noticing.
 * `onPick` is referenced in exactly one place in this function — inside a button's click
 * listener — so that is structural rather than a review discipline.
 *
 * **A name is a commons-derived value and renders through `renderSourcedValue` or not at
 * all** (ADR-0019). A candidate whose name carries no source stamp therefore shows no
 * name — and gets **no pick button either**, because a button that says nothing but
 * "somewhere" is the same wrong-city risk in a different shape. The reason is stated, as
 * `../plan/render.ts::renderPlanStop` states it for a stop whose place cannot be shown.
 *
 * **`confidence` is the resolver's own score, not sourced data**, so it carries no chip
 * and gets a caption instead — the same ruling ADR-0030 A1 makes for a server-computed
 * feasibility verdict. It is rendered because it helps a person choose, and rendered
 * verbatim because rounding someone else's score is inventing one.
 */
export function buildDisambiguation(
  candidates: readonly AreaCandidate[],
  detail: string | null,
  options: DisambiguationOptions = {},
): HTMLElement {
  const root = document.createElement('section')
  root.className = 'siyur-disambiguation'
  root.dataset.candidates = String(candidates.length)
  root.setAttribute('role', 'group')

  appendLine(
    root,
    'siyur-disambiguation__title',
    candidates.length === 1
      ? 'One place matches that name. Is this the one?'
      : `${candidates.length} places match that name. Which one did you mean?`,
  )
  // The server's own sentence, verbatim and `textContent`-only — never a template.
  if (detail) appendLine(root, 'siyur-disambiguation__detail', detail)

  const list = document.createElement('ul')
  list.className = 'siyur-disambiguation__list'

  for (const [index, candidate] of candidates.entries()) {
    const item = document.createElement('li')
    item.className = 'siyur-disambiguation__item'
    // The server's own position in its own list, so a test can prove nothing re-ranked it.
    item.dataset.rank = String(index)
    if (candidate.confidence !== null) item.dataset.confidence = String(candidate.confidence)

    const named =
      candidate.name !== null && candidate.source !== null
        ? renderSourcedValue(
            { value: candidate.name, source: candidate.source },
            { className: 'siyur-disambiguation__name' },
          )
        : null

    const pickable = named !== null && candidate.bbox !== null
    item.dataset.candidate = named === null ? 'unstamped' : pickable ? 'pickable' : 'unusable'

    if (pickable) {
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'siyur-disambiguation__pick'
      button.dataset.rank = String(index)
      button.append(named)
      if (candidate.confidence !== null) {
        const score = document.createElement('span')
        score.className = 'siyur-disambiguation__confidence'
        score.textContent = `MATCH ${candidate.confidence}`
        button.append(score)
      }
      const { onPick } = options
      if (onPick) {
        // ⬇︎ THE ONLY call site. An area is re-delimited on a tap and on nothing else.
        button.addEventListener('click', () => {
          void onPick(candidate)
        })
      }
      item.append(button)
    } else if (named === null) {
      // FR-008 / SC-004 again: no stamp, no value — and with no name on screen there is
      // nothing here a person could knowingly choose, so there is no control to choose it.
      appendLine(
        item,
        'siyur-disambiguation__note',
        'This suggestion carries no source stamp, so its name is not shown and it cannot ' +
          'be chosen.',
      )
    } else {
      item.append(named)
      appendLine(
        item,
        'siyur-disambiguation__note',
        'This suggestion came with no usable extent, so it cannot be opened.',
      )
    }
    list.append(item)
  }
  root.append(list)

  // The caption for the match scores — computed by the resolver, about the user's own
  // search. Not a chip: there is no `SourceRef` on a ranking to build one from.
  const caption = document.createElement('p')
  caption.className = 'siyur-disambiguation__caption'
  caption.dataset.scoreSource = 'server-computed'
  caption.textContent =
    'The match scores are the area resolver’s own ranking of your search — a computed ' +
    'result, not sourced data, so they carry no source stamp. Each name carries its own.'
  root.append(caption)

  const { onDismiss } = options
  if (onDismiss) {
    const dismiss = document.createElement('button')
    dismiss.type = 'button'
    dismiss.className = 'siyur-disambiguation__dismiss'
    dismiss.dataset.action = 'dismiss'
    dismiss.textContent = 'None of these — search again'
    dismiss.addEventListener('click', () => {
      onDismiss()
    })
    root.append(dismiss)
  }

  return root
}

/**
 * The mounted picker: one list at a time, cleared as soon as a search succeeds.
 *
 * Stale candidates are worse than none — a list left on screen from the previous search
 * is an invitation to delimit the wrong place — so {@link DisambiguationSurface.clear} is
 * what the caller runs on every successful resolve.
 */
export class DisambiguationSurface {
  private panel: HTMLElement | null = null

  constructor(
    private readonly container: HTMLElement,
    private readonly options: DisambiguationOptions = {},
  ) {}

  /** The mounted panel, or `null` when nothing is being disambiguated. */
  get element(): HTMLElement | null {
    return this.panel
  }

  /** Show the candidates carried by an {@link AreaNotResolvedError}. */
  show(error: AreaNotResolvedError): HTMLElement {
    this.clear()
    const panel = buildDisambiguation(error.candidates, error.detail, this.options)
    this.container.append(panel)
    this.panel = panel
    return panel
  }

  clear(): void {
    this.panel?.remove()
    this.panel = null
  }
}

/** Create a {@link DisambiguationSurface} bound to `container`. */
export function mountDisambiguation(
  container: HTMLElement,
  options: DisambiguationOptions = {},
): DisambiguationSurface {
  return new DisambiguationSurface(container, options)
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
  /**
   * Whether this resolution is still the one the caller wants, checked **after** the
   * network and immediately before the surface is written to.
   *
   * `Use this view` may pre-empt an in-flight name search (R-02), so two resolutions can be
   * racing and the slow one can land last. The apply is the first irreversible step, so it
   * is the one that has to ask. Omitted means "always" — the single-flight case.
   */
  stillWanted: () => boolean = () => true,
): Promise<AreaResolution> {
  const resolution = await resolveArea(options)
  if (stillWanted()) await surface.apply(resolution)
  return resolution
}
