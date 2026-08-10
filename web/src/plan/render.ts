/**
 * T027 — the plan review surface: the proposed day, its named feasibility violations,
 * and the HITL approve gate.
 *
 * ────────────────────────────────────────────────────────────────────────────────
 * ## Provenance on this surface, stated (ADR-0019, ratified)
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * **1. Every commons-derived value renders through `renderSourcedValue`** — the single
 * funnel in `../map/attribution-chip.ts`. Place names, addresses, opening hours and a
 * leg's distance and duration all go through it, inline, in the same element, with no
 * interaction required. There is no second path from a sourced value to the DOM in this
 * package, so **a value with no stamp renders nothing at all** — not a default string,
 * not a placeholder. `../travel/render.ts` discharges co-presence the same way; this is
 * that idiom, not a second one.
 *
 * **2. Plan structure — times, order, dwell, budgets — carries no chip.** `ItineraryV1`
 * is the user's own private composition and has no `SourceRef` on any of those fields
 * (`docs/data/itinerary.md`): there is nothing to render, and minting a stamp for it
 * would be exactly the invention `attribution-chip.ts` exists to prevent.
 * {@link createPlanStructureCredit} therefore says so in words, so an absent chip is
 * *explained* rather than indistinguishable from a missing one — the precedent
 * `../travel/render.ts::createPlanCredit` set for the offline surface.
 *
 * **3. Feasibility violations carry no chip either, and sit under the same credit.**
 * A violation is a **server-computed verdict about the user's own plan**
 * ("`walking_m 4200 > budget 3000`"), returned by the contract with no `SourceRef` and
 * stored on `user_plan`, not in the itinerary. Some violation strings quote an OSM
 * opening-hours expression, which is the one place this reading is arguable — but the
 * value being displayed is the verdict, the server stamps it with nothing, and
 * fabricating an ODbL chip for a sentence the server composed would be a provenance lie
 * in the direction the funnel exists to stop. FR-005 requires the violations be *named*,
 * so they are rendered verbatim rather than dropped.
 *
 * Points 2 and 3 are judgement calls about the scope of "value" and they owe an ADR —
 * the same one `../travel/render.ts` flagged. Recorded here so a reviewer can disagree
 * with a decision rather than discover a gap. Neither is resolved unilaterally.
 *
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * **Times are area-local wall clock, rendered verbatim.** `10:00` means ten in the
 * morning where the traveller will be standing. Nothing here constructs a `Date` from a
 * plan time, or from anything else — a browser still on the departure timezone (or
 * simply elsewhere in the world from the area) would shift or re-date the whole day,
 * silently and plausibly.
 *
 * **The approve affordance is unattached, not merely disabled, whenever the plan cannot
 * be approved.** The server answers `409` while a plan is infeasible or superseded, so
 * a button that fires a doomed request is a button that lies; the click listener is
 * bound in exactly one branch, under {@link approvability}. There is no override.
 */

import { renderSourcedValue } from '../map/attribution-chip'
import type { SiteRecordV1, SourceRef, SourcedValue } from '../map/types'
import type {
  ExcludedSite,
  Feasibility,
  ItineraryFrame,
  ItineraryV1,
  PlanApproval,
  PlanState,
  RouteLegV1,
  Stop,
  TimelineEntry,
} from './types'

/** Everything the panel renders from. */
export interface PlanReviewModel {
  readonly planId: string | null
  readonly itinerary: ItineraryFrame
  readonly feasibility: Feasibility
  readonly approval: PlanApproval
  /** Commons records for the stops, from `GET /sites` — the source of every chip. */
  readonly sites: ReadonlyMap<string, SiteRecordV1>
  readonly excluded: readonly ExcludedSite[]
  readonly attribution: readonly string[]
  /** `load_sites.candidates`; `0` is the honest "not enough here" (never padded). */
  readonly candidates: number | null
}

export const EMPTY_PLAN_MODEL: PlanReviewModel = {
  planId: null,
  itinerary: { kind: 'absent' },
  feasibility: { ok: false, violations: [], checked_at: null, readable: false },
  approval: { state: null, approved_at: null, superseded_by: null },
  sites: new Map(),
  excluded: [],
  attribution: [],
  candidates: null,
}

/* --------------------------------------------------------- approvability --- */

export interface Approvability {
  readonly approvable: boolean
  /** Why not — always present when `approvable` is false, so the UI never just greys out. */
  readonly reason: string | null
}

/** Why each non-`proposed` state blocks approval. Total over the closed enum. */
const STATE_BLOCK: Record<PlanState, string | null> = {
  proposing: 'This day is still being produced.',
  proposed: null,
  approved: 'This day is already approved.',
  superseded: 'A newer proposal has replaced this one — approve that plan instead.',
  compiling: 'This day is approved; its offline bundle is compiling.',
  compiled: 'This day is approved; its offline bundle is ready.',
  failed: 'This proposal failed. Plan the day again.',
}

const FRAME_BLOCK: Record<Exclude<ItineraryFrame['kind'], 'itinerary'>, string> = {
  empty: 'There is not enough in the commons here to fill this day, so nothing was planned.',
  unreadable: 'The proposed day could not be read, so there is nothing to approve.',
  absent: 'No day has been proposed yet.',
}

/**
 * May this plan be approved, and if not, why?
 *
 * The order of the checks is the order of the honest answer: nothing to approve, then
 * no verdict, then a failed verdict, then the row's own state. **Approval is refused
 * whenever the verdict is unreadable** — the gate fails shut, because `ok` absent is
 * not `ok` true.
 */
export function approvability(model: PlanReviewModel): Approvability {
  if (!model.planId) return { approvable: false, reason: 'This day has not been saved yet.' }
  if (model.itinerary.kind !== 'itinerary') {
    return { approvable: false, reason: FRAME_BLOCK[model.itinerary.kind] }
  }
  if (!model.feasibility.readable) {
    return {
      approvable: false,
      reason: 'Feasibility was not reported for this day, so it cannot be approved.',
    }
  }
  if (!model.feasibility.ok) {
    const n = model.feasibility.violations.length
    return {
      approvable: false,
      reason:
        n > 0
          ? `Approval is blocked until the ${n} conflict${n === 1 ? '' : 's'} below ` +
            `${n === 1 ? 'is' : 'are'} resolved.`
          : 'Approval is blocked: this day is not feasible.',
    }
  }
  const state = model.approval.state
  if (state === null) {
    return { approvable: false, reason: 'This plan is in a state this app does not recognise.' }
  }
  const blocked = STATE_BLOCK[state]
  return blocked === null
    ? { approvable: true, reason: null }
    : { approvable: false, reason: blocked }
}

/* ---------------------------------------------------------------- pieces --- */

function line(className: string, text: string): HTMLElement {
  const element = document.createElement('p')
  element.className = className
  element.textContent = text
  return element
}

/**
 * Adapt a leg's **bare** `SourceRef` into the shape the chip funnel takes.
 *
 * `bundleable` is deliberately left unset. `RouteLegV1` has no such field
 * (`docs/data/route-leg.md`) and, unlike the travel surface, nothing here can observe
 * the answer: bundleability is derived by the compile quarantine filter, which has not
 * run on a plan still under review. Asserting `true` would be this module inventing a
 * fact about a bundle that does not exist yet.
 */
function legValue<T>(value: T, source: SourceRef): SourcedValue<T> {
  return { value, source }
}

/** The site's display name in `lang`, falling back to any name it does carry. */
function nameValue(site: SiteRecordV1, lang: string): SourcedValue<string> | null {
  return site.names[lang] ?? Object.values(site.names)[0] ?? null
}

/** A walking leg's distance and time, each carrying the leg's own ODbL stamp. */
export function renderPlanLeg(leg: RouteLegV1): HTMLElement {
  const row = document.createElement('li')
  row.className = 'siyur-leg siyur-plan-leg'
  row.dataset.legId = leg.id
  const distance = renderSourcedValue(legValue(leg.distance_m, leg.source), {
    label: 'WALK',
    format: (metres) => `${Math.round(metres)} m`,
  })
  const duration = renderSourcedValue(legValue(Math.round(leg.duration_s / 60), leg.source), {
    format: (minutes) => `${minutes} min`,
  })
  if (distance) row.append(distance)
  if (duration) row.append(duration)
  return row
}

/**
 * One stop: its planned window (user-owned, no chip) and its place's stamped values.
 *
 * A stop whose place cannot be shown is still listed, with the reason stated. Dropping
 * it silently would make the day look shorter than it is — and the day's length is
 * precisely what the reviewer is being asked to judge. The stated reason is a fact
 * about an absence, never a stand-in for the value.
 */
export function renderPlanStop(
  stop: Stop,
  start: string,
  durationMin: number,
  site: SiteRecordV1 | undefined,
  lang: string,
): HTMLElement {
  const item = document.createElement('li')
  item.className = 'siyur-plan-stop'
  item.dataset.stopOrder = String(stop.order)
  item.dataset.siteId = stop.site_id

  // Verbatim wall clock. No `Date`, no locale conversion — see the module header.
  item.append(line('siyur-plan-stop__when', `${start} · ${durationMin} min`))

  if (!site) {
    item.dataset.place = 'unavailable'
    item.append(
      line('siyur-plan-stop__note', 'This place’s commons record is not loaded on this device.'),
    )
    return item
  }

  const name = nameValue(site, lang)
  const rendered = name && renderSourcedValue(name, { className: 'siyur-plan-stop__name' })
  if (!rendered) {
    // FR-008 / SC-004: no stamp, no value. Named as a withheld value, not as an error.
    item.dataset.place = 'unstamped'
    item.append(
      line(
        'siyur-plan-stop__note',
        'This place carries no source stamp, so its details are not shown.',
      ),
    )
    return item
  }

  item.dataset.place = 'stamped'
  item.append(rendered)
  const address = renderSourcedValue(site.address, { label: 'ADDRESS' })
  if (address) item.append(address)
  const hours = renderSourcedValue(site.opening_hours, { label: 'HOURS' })
  if (hours) item.append(hours)
  return item
}

/**
 * The day's credit for plan structure — mechanism point 2 in the module header.
 *
 * Not a chip: there is no `SourceRef` on a stop time to build one from.
 */
export function createPlanStructureCredit(itinerary: ItineraryV1): HTMLElement {
  const credit = line(
    'siyur-plan-credit',
    `Times, stop order, dwell and budgets are your own plan for ${itinerary.date} ` +
      `(area-local time) — your data, not sourced data. Everything else here carries ` +
      `the source and licence of the value it belongs to.`,
  )
  credit.dataset.planSource = 'user-owned'
  return credit
}

/**
 * The named violations (FR-005), one row each, in the server's own words.
 *
 * **Never collapsed to "infeasible".** "walking_m 4200 > budget 3000" tells the user
 * what to change; "this plan is infeasible" tells them only that something is wrong.
 * `textContent`, never `innerHTML`: a server-composed string is data, not a template.
 */
export function renderFeasibility(feasibility: Feasibility): HTMLElement {
  const section = document.createElement('section')
  section.className = 'siyur-plan-feasibility'
  section.dataset.ok = String(feasibility.ok)
  section.dataset.readable = String(feasibility.readable)

  if (!feasibility.readable) {
    section.append(line('siyur-plan-feasibility__title', 'Feasibility was not reported.'))
    return section
  }
  if (feasibility.ok) {
    section.append(
      line('siyur-plan-feasibility__title', 'This day fits your time and walking budgets.'),
    )
    return section
  }

  section.append(
    line(
      'siyur-plan-feasibility__title',
      feasibility.violations.length === 1
        ? '1 conflict to resolve:'
        : `${feasibility.violations.length} conflicts to resolve:`,
    ),
  )
  const list = document.createElement('ul')
  list.className = 'siyur-plan-violations'
  for (const violation of feasibility.violations) {
    const item = document.createElement('li')
    item.className = 'siyur-plan-violation'
    item.textContent = violation
    list.append(item)
  }
  section.append(list)
  return section
}

/**
 * Order the day: timeline entries when the plan carries them, otherwise stops
 * interleaved with the leg joining each consecutive pair.
 *
 * The fallback is not cosmetic. `docs/data/itinerary.md`'s own worked example of an
 * **infeasible** plan carries `timeline.entries: []` and a 4200 m leg — the one thing
 * the reviewer must see to understand the violation. Legs are addressed by
 * `from_stop`/`to_stop`, the plan's one addressing scheme.
 */
function timelineRows(
  itinerary: ItineraryV1,
): readonly { readonly stop?: Stop; readonly leg?: RouteLegV1; readonly entry?: TimelineEntry }[] {
  const stopsByOrder = new Map(itinerary.stops.map((stop) => [stop.order, stop]))
  const legsById = new Map(itinerary.legs.map((leg) => [leg.id, leg]))

  if (itinerary.timeline.length > 0) {
    const rows: { stop?: Stop; leg?: RouteLegV1; entry?: TimelineEntry }[] = []
    for (const entry of itinerary.timeline) {
      if (entry.leg_id !== null) {
        const leg = legsById.get(entry.leg_id)
        if (leg) rows.push({ leg })
        continue
      }
      const stop = entry.stop_order === null ? undefined : stopsByOrder.get(entry.stop_order)
      if (stop) rows.push({ stop, entry })
    }
    return rows
  }

  const legsByPair = new Map(
    itinerary.legs.map((leg) => [`${leg.from_stop}→${leg.to_stop}`, leg]),
  )
  const rows: { stop?: Stop; leg?: RouteLegV1 }[] = []
  for (const [index, stop] of itinerary.stops.entries()) {
    rows.push({ stop })
    const next = itinerary.stops[index + 1]
    const leg = next && legsByPair.get(`${stop.order}→${next.order}`)
    if (leg) rows.push({ leg })
  }
  return rows
}

/* ----------------------------------------------------------------- panel --- */

export interface PlanPanelOptions {
  /** Called **only** from the approve button's click listener, which is bound only
   * when {@link approvability} says the request can succeed. */
  readonly onApprove?: (planId: string) => void | Promise<void>
  readonly lang?: string
}

/** The whole review panel: the day, the verdict, and the gate. */
export function renderPlanPanel(
  model: PlanReviewModel,
  options: PlanPanelOptions = {},
): HTMLElement {
  const root = document.createElement('section')
  root.className = 'siyur-plan'
  root.dataset.planState = model.approval.state ?? 'unknown'
  root.dataset.feasible = String(model.feasibility.ok)
  if (model.planId) root.dataset.planId = model.planId

  const verdict = approvability(model)
  root.dataset.approvable = String(verdict.approvable)

  if (model.itinerary.kind === 'itinerary') {
    const itinerary = model.itinerary.itinerary
    const lang = options.lang ?? itinerary.lang
    const date = document.createElement('h2')
    date.className = 'siyur-plan__date'
    date.textContent = itinerary.date // verbatim; never re-formatted through a locale
    root.append(date, createPlanStructureCredit(itinerary))
    root.append(
      line(
        'siyur-plan__budgets',
        `Budget: ${itinerary.budgets.walking_m} m walking · ${itinerary.budgets.hours} h`,
      ),
    )

    const list = document.createElement('ol')
    list.className = 'siyur-plan-timeline'
    for (const row of timelineRows(itinerary)) {
      if (row.leg) list.append(renderPlanLeg(row.leg))
      else if (row.stop) {
        list.append(
          renderPlanStop(
            row.stop,
            row.entry?.start ?? row.stop.planned_start,
            row.entry?.duration_min ?? row.stop.dwell_min,
            model.sites.get(row.stop.site_id),
            lang,
          ),
        )
      }
    }
    root.append(list)
  } else {
    root.append(line('siyur-plan__empty', FRAME_BLOCK[model.itinerary.kind]))
    if (model.candidates === 0) {
      // SC-006's sibling: an area with nothing in it says so. Nothing is ever padded.
      root.append(
        line('siyur-plan__candidates', 'No cited places were available here to plan from.'),
      )
    }
  }

  root.append(renderFeasibility(model.feasibility))

  if (model.excluded.length > 0) {
    // FR-003: a place the walking network cannot reach is dropped and reported —
    // never joined by a straight line pretending to be a route.
    const dropped = document.createElement('ul')
    dropped.className = 'siyur-plan-excluded'
    for (const entry of model.excluded) {
      const item = document.createElement('li')
      item.className = 'siyur-plan-excluded__item'
      item.dataset.siteId = entry.site_id
      item.dataset.reason = entry.reason
      item.textContent = `A place was left out of this day: ${entry.reason}.`
      dropped.append(item)
    }
    root.append(dropped)
  }

  root.append(renderApproveControl(model, verdict, options))

  if (model.attribution.length > 0) {
    // The aggregate credit, mirrored verbatim. Additional to the per-value chips
    // above — never a substitute for them (ADR-0019).
    const credit = line('siyur-plan-attribution', model.attribution.join(' · '))
    credit.dataset.creditScope = 'aggregate'
    root.append(credit)
  }

  return root
}

let blockedReasonSeq = 0

/**
 * The HITL gate's control.
 *
 * Rendered as a **disabled** button plus the stated reason rather than as nothing at
 * all: an absent control is indistinguishable from a broken screen, while a disabled
 * one beside the named violations says what to fix. It is disabled *and* unwired — a
 * DOM tamper that clears `disabled` still fires no request, because there is no
 * listener to fire.
 */
export function renderApproveControl(
  model: PlanReviewModel,
  verdict: Approvability,
  options: PlanPanelOptions = {},
): HTMLElement {
  const wrapper = document.createElement('div')
  wrapper.className = 'siyur-plan-approve'

  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'siyur-plan-approve__button'
  button.textContent = 'Approve this day'
  button.dataset.approvable = String(verdict.approvable)
  button.disabled = !verdict.approvable
  button.setAttribute('aria-disabled', String(!verdict.approvable))
  wrapper.append(button)

  if (verdict.approvable && model.planId) {
    const planId = model.planId
    const { onApprove } = options
    // ⬇︎ THE ONLY call site, in the only branch where the server can answer 200.
    if (onApprove) button.addEventListener('click', () => void onApprove(planId))
  } else if (verdict.reason) {
    const reason = line('siyur-plan-approve__blocked', verdict.reason)
    // A per-instance id: `aria-describedby` resolves to the FIRST matching element in
    // the document, so a fixed id would point every panel's button at the first panel's
    // reason — and the surface re-renders the whole control on each update.
    reason.id = `siyur-plan-approve-blocked-${(blockedReasonSeq += 1)}`
    button.setAttribute('aria-describedby', reason.id)
    wrapper.append(reason)
  }

  return wrapper
}

/* --------------------------------------------------------------- surface --- */

export interface PlanReviewOptions extends PlanPanelOptions {
  /** Commons records for the stops, looked up as the plan streams in. */
  readonly sites?: ReadonlyMap<string, SiteRecordV1>
}

/**
 * The mounted review surface: holds the model, re-renders on every update, and exposes
 * {@link PlanReviewSurface.handlers} for `./client`'s `streamPlan` — the same shape
 * `ResearchProgressSurface` uses.
 */
export class PlanReviewSurface {
  private model: PlanReviewModel
  private panel: HTMLElement

  constructor(
    private readonly container: HTMLElement,
    private readonly options: PlanReviewOptions = {},
  ) {
    this.model = { ...EMPTY_PLAN_MODEL, sites: options.sites ?? new Map() }
    this.panel = renderPlanPanel(this.model, this.options)
    this.container.append(this.panel)
  }

  get element(): HTMLElement {
    return this.panel
  }

  get current(): PlanReviewModel {
    return this.model
  }

  /** Whether the gate is open — the thing tests and callers should assert on. */
  get approvable(): boolean {
    return approvability(this.model).approvable
  }

  /** Merge a partial model and re-render in place. */
  update(patch: Partial<PlanReviewModel>): void {
    this.model = { ...this.model, ...patch }
    const next = renderPlanPanel(this.model, this.options)
    this.panel.replaceWith(next)
    this.panel = next
  }

  /** Clear the previous proposal so nothing stale is left on screen. */
  start(): void {
    this.update({
      ...EMPTY_PLAN_MODEL,
      sites: this.model.sites,
      approval: { state: 'proposing', approved_at: null, superseded_by: null },
    })
  }

  /** Handlers to hand to `streamPlan`. */
  handlers(): {
    onStatus: (status: { candidates: number | null; excluded: readonly ExcludedSite[] }) => void
    onItinerary: (frame: ItineraryFrame) => void
    onFeasibility: (feasibility: Feasibility) => void
    onDone: (done: { plan_id: string; state: PlanState | null } | null) => void
  } {
    return {
      onStatus: (status) => {
        this.update({
          ...(status.candidates !== null ? { candidates: status.candidates } : {}),
          ...(status.excluded.length > 0
            ? { excluded: [...this.model.excluded, ...status.excluded] }
            : {}),
        })
      },
      onItinerary: (itinerary) => this.update({ itinerary }),
      onFeasibility: (feasibility) => this.update({ feasibility }),
      onDone: (done) => {
        if (!done) return
        this.update({
          planId: done.plan_id,
          approval: { ...this.model.approval, state: done.state },
        })
      },
    }
  }

  /** Apply a `GET /plans/{id}` body — the read-back path (and the post-approve one). */
  applyDetail(planId: string, detail: PlanApplyDetail): void {
    this.update({
      planId,
      itinerary: detail.plan
        ? { kind: 'itinerary', itinerary: detail.plan }
        : { kind: 'unreadable' },
      feasibility: detail.feasibility,
      approval: detail.approval,
      attribution: detail.attribution,
    })
  }

  destroy(): void {
    this.panel.remove()
  }
}

/** The `GET /plans/{id}` shape {@link PlanReviewSurface.applyDetail} consumes. */
export interface PlanApplyDetail {
  readonly plan: ItineraryV1 | null
  readonly feasibility: Feasibility
  readonly approval: PlanApproval
  readonly attribution: readonly string[]
}

/** Create and mount a {@link PlanReviewSurface}. */
export function mountPlanReview(
  container: HTMLElement,
  options: PlanReviewOptions = {},
): PlanReviewSurface {
  return new PlanReviewSurface(container, options)
}
