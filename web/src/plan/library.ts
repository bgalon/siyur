/**
 * Phase A — **"Your plans"**: the surface that gives the app a memory
 * (`docs/design/usable-m1-plan.md` § Phase A, journey 2 "completable, then lost").
 *
 * A tap on a row hands the plan id to the caller, which reads it back with `fetchPlan`
 * and applies it to the **existing** review panel. There is deliberately no second plan
 * renderer in this package: `./render::renderPlanPanel` renders a plan by id already, and
 * a list that drew its own version of a day would be a second thing to keep true.
 *
 * ────────────────────────────────────────────────────────────────────────────────
 * ## Why nothing here carries an attribution chip (ADR-0019)
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * Every value on this surface — the tour date, the state, the stop count, the area's
 * name — is the **user's own composition**, not commons data. `api/areas.py`'s own field
 * note says it outright: `name` is *"the free-text name the user asked for"*, `None` when
 * they drew a ring or sent a bbox. None of these fields carries a `SourceRef` on the wire,
 * because there is nothing to source them to.
 *
 * So this module renders no `SourcedValue` at all, and therefore calls `renderSourcedValue`
 * nowhere — **not** because it takes a shortcut past the funnel, but because it holds
 * nothing the funnel applies to. {@link createLibraryCredit} says so on screen, for the
 * same reason `./render::createPlanStructureCredit` does: an absent chip must be
 * *explained*, or it is indistinguishable from a missing one. If a commons-derived value
 * is ever added to a row (a stop's name, a site's category), it goes through
 * `../map/attribution-chip::renderSourcedValue` like everything else.
 *
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * **The state message can never contradict the state attribute.** The audit's emblematic
 * defect was an element carrying `data-plan-state="proposing"` while rendering *"No day
 * has been proposed yet."* — the attribute a test asserts being right while the sentence a
 * human reads is the opposite. Here the sentence is *derived* from the same value the
 * attribute is written from ({@link stateLabel}, {@link libraryHeadline}), and
 * {@link renderPlanLibrary} re-derives `ready`/`empty` from the row count it is about to
 * draw — so a model claiming `empty` while holding seven plans renders as seven plans, not
 * as "you have not planned a day yet".
 *
 * **A failed load is never an empty list.** `./list` throws rather than folding an
 * unreadable body into `[]`; {@link describeListFailure} turns that into a sentence that
 * says what happened and never says "you have none".
 *
 * ────────────────────────────────────────────────────────────────────────────────
 * ## Two clocks, and only one of them is rendered
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * A plan row carries values from two different clocks, and they are not interchangeable:
 *
 * - **`date`** is the **area-local wall-clock day the plan is for** — "my Tuesday in
 *   Rhodes". It is what a person recognises their own day by, it is what
 *   `opening-hours-py` evaluated the day against, and it is rendered **verbatim**: a
 *   plain `YYYY-MM-DD` string, never parsed and never re-formatted.
 * - **`created_at` / `approved_at`** are **tz-aware UTC audit instants**. They are kept on
 *   {@link PlanSummary} because they are part of the contract, and **rendered nowhere**.
 *
 * That second decision is the load-bearing one. Showing a UTC instant *legibly* means
 * converting it to some local zone, which means `new Date` or `Intl.` — and a phone that
 * has just landed is still on its departure timezone, so the conversion would be wrong in
 * precisely the situation this product exists for. `test/plan.test.ts`'s source scan bans
 * those calls across `src/plan/` and recurses into subdirectories; the right response to
 * that ban is to not need a clock, not to carve out an exception for "only the created-at
 * line". **Recency is carried by the server's newest-first order instead**, which costs no
 * clock at all.
 *
 * Nor does anything here pattern-match a timestamp's shape. The API serialises
 * `…+00:00` (pydantic's rendering, and what `GET /plans/{id}` already returns for
 * `approved_at`); a client that sniffed for a trailing `Z` would be encoding one
 * endpoint's formatting as a fact about a column.
 */

import { PlanRequestError } from './client'
import { ListResponseError, type AreaSummary, type PlanSummary } from './list'
import type { PlanState } from './types'

/* ----------------------------------------------------------------- states --- */

/**
 * What the surface currently knows.
 *
 * `idle` is a real state, not a placeholder: the list loads on first open rather than on
 * mount, so the label makes **no claim about how many plans exist** until it has been
 * told. Loading on mount would also put a `401` banner in front of every signed-out
 * visitor before they had asked the app for anything.
 */
export type LibraryState = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

/** Human wording for each of ADR-0023's seven states. Total over the closed enum. */
const STATE_LABEL: Record<PlanState, string> = {
  proposing: 'Being produced',
  proposed: 'Waiting for your approval',
  approved: 'Approved',
  superseded: 'Replaced by a newer plan',
  compiling: 'Offline bundle compiling',
  compiled: 'Offline bundle ready',
  failed: 'Failed',
}

/**
 * The label for a row's state — and the **only** source of that sentence.
 *
 * An unrecognised state is named as unrecognised. Mapping an eighth state onto whichever
 * of the seven looks closest is how a list tells a user their day is approved when the
 * server said something else entirely (`./parse`, same rule at the wire boundary).
 */
export function stateLabel(state: PlanState | null): string {
  return state === null ? 'State not recognised' : STATE_LABEL[state]
}

/**
 * Turn a rejected load into something the surface can say.
 *
 * Every branch ends on the same fact — **nothing was listed** — because the failure this
 * exists to prevent is a user reading "you have not planned a day yet" over a database row
 * that is right there. A status this client has no wording for is reported with its
 * number: the number is what a support conversation can act on.
 */
export function describeListFailure(error: unknown): string {
  if (error instanceof ListResponseError) {
    return (
      'The server answered, but not with a list this app could read, so nothing is ' +
      'listed. Your plans have not been lost.'
    )
  }
  if (error instanceof PlanRequestError) {
    if (error.status === 401) {
      return 'Your session has ended, so nothing is listed. Sign in and try again.'
    }
    if (error.status === 404) {
      return 'This server has no list of plans to give, so nothing is listed.'
    }
    return `The server refused the request (status ${error.status}), so nothing is listed.`
  }
  return 'The request did not complete, so nothing is listed. Your plans have not been lost.'
}

/* ------------------------------------------------------------------ model --- */

export interface PlanLibraryModel {
  readonly state: LibraryState
  /** Server order, preserved verbatim — newest first, never re-sorted (see `./list`). */
  readonly plans: readonly PlanSummary[]
  /** `null` ⇒ the area list was not loaded, so no row claims anything about its area. */
  readonly areas: ReadonlyMap<string, AreaSummary> | null
  /** The sentence for a failed load. Set only in `error`. */
  readonly error: string | null
  readonly open: boolean
  /** The row whose plan is currently on the review panel below. */
  readonly selectedPlanId: string | null
}

export const EMPTY_LIBRARY_MODEL: PlanLibraryModel = {
  state: 'idle',
  plans: [],
  areas: null,
  error: null,
  open: false,
  selectedPlanId: null,
}

/**
 * The toggle's own sentence, derived from the state it is about to be stamped with.
 *
 * Exhaustive over {@link LibraryState}: a new state cannot be added without giving it
 * words, which is the mechanical half of "no message contradicts its attribute".
 */
export function libraryHeadline(state: LibraryState, planCount: number): string {
  switch (state) {
    case 'idle':
      return 'Your plans'
    case 'loading':
      return 'Your plans — loading…'
    case 'error':
      return 'Your plans — could not be loaded'
    case 'empty':
      return 'Your plans — none yet'
    case 'ready':
      return `Your plans — ${planCount}`
  }
}

/* ----------------------------------------------------------------- pieces --- */

function line(className: string, text: string): HTMLElement {
  const element = document.createElement('p')
  element.className = className
  element.textContent = text
  return element
}

function span(className: string, text: string): HTMLElement {
  const element = document.createElement('span')
  element.className = className
  element.textContent = text
  return element
}

/**
 * How to name the area a plan was made for.
 *
 * Three answers, and the difference between them matters: a name the user typed, an area
 * they drew (which has no name to show, and inventing one would be a fabrication), and
 * *"we did not load the area list"* — which must not be reported as either of the first
 * two. The last case returns `null` and the row simply says nothing about its area.
 *
 * **Nothing here says whether the area has been researched, and nothing here may.**
 * `AreaSummary.researched_at` is `null` for *reused* areas that are fully researched —
 * `api/areas.py:474` short-circuits a covered area to a reuse hint, which never stamps the
 * field (measured live: 14 areas, 3 stamped). So a null is not a negative, and turning
 * this function's empty case into a confident *"not researched yet"* would be the same
 * defect the audit already caught twice elsewhere: **a truthful field rendered as the
 * answer to a question it was not asked.** Show a date when there is one; otherwise show
 * nothing. The list endpoint carries no coverage signal on purpose — `known_site_count` is
 * a PostGIS count per row — and it must not be reconstructed from the client.
 */
export function areaLabel(
  plan: PlanSummary,
  areas: ReadonlyMap<string, AreaSummary> | null,
): string | null {
  if (!areas || !plan.area_id) return null
  const area = areas.get(plan.area_id)
  if (!area) return 'For an area that is no longer listed'
  return area.name ? `For ${area.name}` : 'For an area you delimited on the map'
}

export interface PlanRowOptions {
  /** Called **only** from the row button's click listener. */
  readonly onSelect?: (planId: string) => void | Promise<void>
  readonly selectedPlanId?: string | null
}

/**
 * One plan in the list.
 *
 * The whole row is one button, so the tap target is the row rather than a link inside it —
 * the spec floor is 44 px and a phone thumb does not aim at a word.
 *
 * `data-plan-state`, `data-feasible` and `data-superseded-by` carry the server's own
 * values so a caller (or a test) reads the decision rather than the prose, and the prose
 * beside them is derived from the very same values.
 */
export function renderPlanRow(
  plan: PlanSummary,
  areas: ReadonlyMap<string, AreaSummary> | null,
  options: PlanRowOptions = {},
): HTMLElement {
  const row = document.createElement('li')
  row.className = 'siyur-library__row'
  row.dataset.planId = plan.plan_id
  row.dataset.planState = plan.state ?? 'unknown'
  // Three values, not two — `./render` keeps the same distinction on the panel: `false`
  // alone conflates "checked, and it does not fit" with "never reported".
  row.dataset.feasible = plan.feasible === null ? 'unknown' : String(plan.feasible)
  if (plan.superseded_by) row.dataset.supersededBy = plan.superseded_by
  const selected = options.selectedPlanId === plan.plan_id
  row.dataset.selected = String(selected)

  const open = document.createElement('button')
  open.type = 'button'
  open.className = 'siyur-library__open'
  open.dataset.planId = plan.plan_id
  if (selected) open.setAttribute('aria-current', 'true')

  // Verbatim area-local day. A plan whose date the server did not send says so rather
  // than rendering a blank line that reads as a loading glitch.
  open.append(span('siyur-library__date', plan.date ?? 'Date not recorded'))
  open.append(span('siyur-library__state', stateLabel(plan.state)))
  if (plan.stop_count !== null) {
    open.append(
      span('siyur-library__stops', `${plan.stop_count} stop${plan.stop_count === 1 ? '' : 's'}`),
    )
  }
  const area = areaLabel(plan, areas)
  if (area) open.append(span('siyur-library__area', area))
  row.append(open)

  // Only the two answers that are news. `feasible: true` is already implied by an
  // approved state and adding a badge for it would bury the two that are not.
  if (plan.feasible === false) {
    row.append(line('siyur-library__flag', 'This day did not fit its budgets.'))
  } else if (plan.feasible === null) {
    row.append(line('siyur-library__flag', 'Feasibility was not reported for this day.'))
  }

  if (plan.superseded_by) {
    // The successor id, addressable and rendered verbatim — the one actionable fact about
    // a superseded plan, and the same thing `./render::renderApprovalFailure` surfaces on
    // a `409 plan_superseded`. Throwing it away is what makes a superseded plan a dead end.
    const successor = line(
      'siyur-library__superseded',
      `Replaced by plan ${plan.superseded_by}.`,
    )
    successor.dataset.supersededBy = plan.superseded_by
    row.append(successor)
  }

  // **`created_at` and `approved_at` are deliberately not rendered.** See the two-clocks
  // note in the module header: they are tz-aware UTC audit instants, and the only way to
  // show one legibly is to convert it to a local zone — which needs `new Date` or `Intl.`,
  // both banned under `src/plan/` for exactly the reason that would bite here. Recency is
  // carried by the server's newest-first **order**, which costs no clock at all.

  const { onSelect } = options
  if (onSelect) {
    // ⬇︎ THE ONLY call site. A plan opens on a tap and on nothing else — in particular,
    // rendering the list opens nothing.
    open.addEventListener('click', () => {
      void onSelect(plan.plan_id)
    })
  }
  return row
}

/**
 * Why no row carries an attribution chip, said on screen (see the module header).
 *
 * Not a chip: there is no `SourceRef` on a tour date or a stop count to build one from,
 * and minting one would be exactly the invention `attribution-chip.ts` exists to prevent.
 */
export function createLibraryCredit(): HTMLElement {
  const credit = line(
    'siyur-library__credit',
    'The date, area and state of each day are your own plans — your data, not sourced ' +
      'data, so they carry no source stamp. The places inside a day carry theirs when you ' +
      'open it.',
  )
  credit.dataset.planSource = 'user-owned'
  return credit
}

/* ---------------------------------------------------------------- surface --- */

export interface PlanLibraryOptions extends PlanRowOptions {
  /** Called when the user opens or closes the list. */
  readonly onToggle?: (open: boolean) => void
}

/**
 * The whole "Your plans" block: a toggle, and — when open — the list or the reason there
 * is not one.
 *
 * `ready`/`empty` are **re-derived from the rows actually being drawn**. A model that says
 * `empty` while carrying plans is a bug somewhere upstream, and the one thing this
 * renderer must not do is repeat that bug as a sentence: it draws what it has and labels
 * it accordingly.
 */
export function renderPlanLibrary(
  model: PlanLibraryModel,
  options: PlanLibraryOptions = {},
): HTMLElement {
  const state: LibraryState =
    model.state === 'ready' || model.state === 'empty'
      ? model.plans.length > 0
        ? 'ready'
        : 'empty'
      : model.state

  const root = document.createElement('section')
  root.className = 'siyur-library'
  root.dataset.state = state
  root.dataset.open = String(model.open)
  root.dataset.planCount = String(model.plans.length)

  const toggle = document.createElement('button')
  toggle.type = 'button'
  toggle.className = 'siyur-library__toggle'
  toggle.textContent = libraryHeadline(state, model.plans.length)
  toggle.setAttribute('aria-expanded', String(model.open))
  toggle.dataset.action = model.open ? 'close' : 'open'
  const { onToggle } = options
  if (onToggle) {
    toggle.addEventListener('click', () => {
      onToggle(!model.open)
    })
  }
  root.append(toggle)

  const body = document.createElement('div')
  body.className = 'siyur-library__body'
  body.hidden = !model.open
  root.append(body)

  if (state === 'error') {
    // `role="alert"`: the surface re-renders around this, and a refusal has to reach the
    // person who just tapped the toggle rather than wait to be scrolled to.
    const alert = document.createElement('section')
    alert.className = 'siyur-library__failure'
    alert.setAttribute('role', 'alert')
    alert.append(
      line(
        'siyur-library__failure-message',
        model.error ?? 'The list of your plans could not be loaded, so nothing is listed.',
      ),
    )
    body.append(alert)
    return root
  }
  if (state === 'loading') {
    body.append(line('siyur-library__status', 'Looking for the days you have planned…'))
    return root
  }
  if (state === 'idle') {
    body.append(line('siyur-library__status', 'Open this to see the days you have planned.'))
    return root
  }
  if (state === 'empty') {
    body.append(
      line(
        'siyur-library__status',
        'You have not planned a day yet. Delimit an area and plan one — it will be here ' +
          'when you come back.',
      ),
    )
    body.append(createLibraryCredit())
    return root
  }

  const list = document.createElement('ol')
  list.className = 'siyur-library__list'
  // The **model** decides which row is open, not the caller's options: the surface holds
  // one selection and re-renders around it, and an options value that could disagree with
  // `model.selectedPlanId` would be a second answer to a one-answer question.
  const rowOptions: PlanRowOptions = { ...options, selectedPlanId: model.selectedPlanId }
  for (const plan of model.plans) {
    list.append(renderPlanRow(plan, model.areas, rowOptions))
  }
  body.append(list, createLibraryCredit())
  return root
}

/**
 * How the mounted surface fetches. Injected rather than imported so a test can drive the
 * whole lifecycle without a network, exactly as `./client`'s `fetchImpl` seam does.
 */
export interface PlanLibrarySources {
  readonly loadPlans: () => Promise<readonly PlanSummary[]>
  /**
   * Optional and **best-effort**: area names only make the rows friendlier. A failure here
   * leaves `areas` at `null`, so rows say nothing about their area rather than guessing —
   * it must never turn a perfectly good list of plans into an error.
   */
  readonly loadAreas?: () => Promise<readonly AreaSummary[]>
}

/** The mounted "Your plans" surface: holds the model and re-renders in place. */
export class PlanLibrarySurface {
  private model: PlanLibraryModel
  private element_: HTMLElement
  private readonly renderOptions: PlanLibraryOptions

  constructor(
    private readonly container: HTMLElement,
    private readonly sources: PlanLibrarySources,
    private readonly options: PlanLibraryOptions = {},
  ) {
    this.renderOptions = { ...options, onToggle: (open) => this.setOpen(open) }
    this.model = EMPTY_LIBRARY_MODEL
    this.element_ = renderPlanLibrary(this.model, this.renderOptions)
    this.container.append(this.element_)
  }

  get element(): HTMLElement {
    return this.element_
  }

  get current(): PlanLibraryModel {
    return this.model
  }

  /** Merge a partial model and re-render in place. */
  update(patch: Partial<PlanLibraryModel>): void {
    this.model = { ...this.model, ...patch }
    const next = renderPlanLibrary(this.model, this.renderOptions)
    this.element_.replaceWith(next)
    this.element_ = next
  }

  /**
   * Open or close the list, loading it the first time it is opened.
   *
   * Lazy on purpose (see {@link LibraryState}). Re-opening does not refetch — the user
   * asked to see the list, not to pay for it twice — while {@link refresh} exists for the
   * caller that has just changed what the list should say.
   */
  setOpen(open: boolean): void {
    this.update({ open })
    this.options.onToggle?.(open)
    if (open && (this.model.state === 'idle' || this.model.state === 'error')) void this.refresh()
  }

  /**
   * (Re)load the list.
   *
   * Plans are the request that can fail the surface; areas are a courtesy. They are
   * awaited separately so a `GET /areas` that 404s cannot hide a `GET /plans` that
   * worked — the failure mode where one broken read makes the app look empty.
   */
  async refresh(): Promise<void> {
    this.update({ state: 'loading', error: null })
    let plans: readonly PlanSummary[]
    try {
      plans = await this.sources.loadPlans()
    } catch (error) {
      this.update({ state: 'error', error: describeListFailure(error) })
      return
    }
    this.update({ state: plans.length > 0 ? 'ready' : 'empty', plans, error: null })

    const { loadAreas } = this.sources
    if (!loadAreas) return
    try {
      const areas = await loadAreas()
      this.update({ areas: new Map(areas.map((area) => [area.area_id, area])) })
    } catch {
      // Deliberately swallowed *into a null*, not into a lie: `areas` stays `null`, so
      // `areaLabel` says nothing at all rather than "an area you delimited".
      this.update({ areas: null })
    }
  }

  /** Mark which row is currently open on the panel below. */
  select(planId: string | null): void {
    this.update({ selectedPlanId: planId })
  }

  destroy(): void {
    this.element_.remove()
  }
}

/** Create and mount a {@link PlanLibrarySurface}. */
export function mountPlanLibrary(
  container: HTMLElement,
  sources: PlanLibrarySources,
  options: PlanLibraryOptions = {},
): PlanLibrarySurface {
  return new PlanLibrarySurface(container, sources, options)
}
