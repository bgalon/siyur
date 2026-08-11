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
 * **3. Feasibility violations carry no chip, and carry a caption of their OWN**
 * (**ADR-0030 (proposed), amendment A1** — the ruling, not a judgement call left open
 * here). A violation is a **server-computed assertion about the user's composition**
 * ("`walking_m 4200 > budget 3000`"), returned by the contract with no `SourceRef` and
 * stored on `user_plan`, not in the itinerary — so there is no stamp to render, and
 * fabricating an ODbL chip for a sentence the server composed would be a provenance lie
 * in the direction the funnel exists to stop.
 *
 * A1 rules that it may **not** shelter under the plan-structure credit, for two reasons.
 * *Mechanically*, that credit is appended only in the `itinerary` branch while
 * {@link renderFeasibility} renders in every branch — an unreadable itinerary plus a
 * verdict carrying violations left uncaptioned unsourced sentences on screen.
 * *Semantically*, "your own plan … your data, not sourced data" is a claim about **user
 * composition**, and filing the server's verdict *about* that composition under it tells
 * the reader the verdict is their own data. So {@link createVerdictCaption} is emitted
 * **inside** `renderFeasibility` (and inside the approval-failure block, which carries
 * the same kind of sentence), and therefore travels with the section in every branch.
 * FR-005 requires the violations be *named*, so they are rendered verbatim, never
 * collapsed — and never parsed: an opening-window breach names the window, and the
 * expression itself belongs to the stop's chipped `opening_hours`.
 *
 * Point 2 is the same ADR's reading of "value" and is likewise recorded, not decided
 * here — `../travel/render.ts` flagged it first.
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
 *
 * **A refused approval is a first-class outcome of this surface, not a caught-and-logged
 * one.** Approve is the one irreversible action in the product, and the request can be
 * refused for a reason the panel could not have known: another tab re-planned the same
 * area, so this proposal is superseded (`409 plan_superseded`), or the session expired.
 * The model therefore carries {@link PlanReviewModel.failure} and
 * {@link PlanReviewModel.approving}: a rejection lands on the model and is rendered —
 * with the `superseded_by` id, which is the one thing that resolves the situation — and
 * the button is unbound while a request is in flight, so a click that appears to do
 * nothing cannot be answered by clicking again.
 */

import { renderSourcedValue } from '../map/attribution-chip'
import type { SiteRecordV1, SourceRef, SourcedValue } from '../map/types'
import { PlanApprovalConflictError, PlanRequestError } from './client'
import type {
  ExcludedSite,
  Feasibility,
  ItineraryFrame,
  ItineraryV1,
  PlanApproval,
  PlanDetail,
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
  /** The last approve attempt that was refused. Cleared by any fresh read-back. */
  readonly failure: PlanApprovalFailure | null
  /** An approve request is in flight: the button is disabled *and* unbound. */
  readonly approving: boolean
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
  failure: null,
  approving: false,
}

/* ---------------------------------------------------- a refused approval --- */

/** A refused approve attempt, in the shape the panel can render. */
export interface PlanApprovalFailure {
  /** The server's own machine-readable reason (`infeasible` | `plan_superseded`), else `null`. */
  readonly reason: string | null
  /** What to tell the user. **Never claims the plan was approved.** */
  readonly message: string
  /** The server's named violations, when it refused as infeasible (FR-005). */
  readonly violations: readonly string[]
  /** The successor plan id — the actionable part of a `plan_superseded` refusal. */
  readonly supersededBy: string | null
}

/**
 * Turn whatever `onApprove` rejected with into something the panel can say.
 *
 * Every message ends on the same fact — **nothing was approved** — because the failure
 * mode this exists to prevent is a user who cannot tell a refused request from a click
 * that did not register. A status this client has no wording for is reported with its
 * number rather than as a generic "something went wrong": the number is what a support
 * conversation can act on, and inventing a cause would be worse than naming none.
 */
export function describeApprovalFailure(error: unknown): PlanApprovalFailure {
  if (error instanceof PlanApprovalConflictError) {
    if (error.reason === 'plan_superseded') {
      return {
        reason: error.reason,
        message:
          'A newer proposal replaced this day before it could be approved, so nothing ' +
          'was approved. Approve the newer plan instead.',
        violations: error.violations,
        supersededBy: error.supersededBy,
      }
    }
    return {
      reason: error.reason,
      message:
        error.reason === 'infeasible'
          ? 'The server refused this approval because the day is not feasible. ' +
            'Nothing was approved.'
          : 'The server refused this approval. Nothing was approved.',
      violations: error.violations,
      supersededBy: error.supersededBy,
    }
  }
  if (error instanceof PlanRequestError) {
    const message =
      error.status === 401
        ? 'Your session has ended, so nothing was approved. Sign in and try again.'
        : error.status === 404
          ? 'This plan is no longer on the server, so nothing was approved.'
          : `The server refused this approval (status ${error.status}). Nothing was approved.`
    return { reason: null, message, violations: [], supersededBy: null }
  }
  return {
    reason: null,
    message: 'The approval request did not complete, so nothing was approved.',
    violations: [],
    supersededBy: null,
  }
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
 * Did the day pass — the single reading of a verdict this module uses.
 *
 * `ok` alone is not the answer. A verdict that claims `ok:true` while naming violations
 * contradicts itself, and the gate resolves the contradiction shut.
 * `./parse::sanitiseFeasibility` already applies this rule at the wire boundary; this is
 * the same rule at the point of use, so a `Feasibility` assembled anywhere else — a
 * caller's fixture, a future non-HTTP source — cannot open the one irreversible gate in
 * the product on a self-contradicting verdict. Both {@link approvability} and
 * {@link renderFeasibility} go through here, so the gate and the sentence beside it can
 * never disagree.
 */
export function feasibilityPasses(feasibility: Feasibility): boolean {
  return feasibility.readable && feasibility.ok && feasibility.violations.length === 0
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
  if (!feasibilityPasses(model.feasibility)) {
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
 * The caption for a **server-computed verdict** — ADR-0030 (proposed) A1, module header
 * point 3.
 *
 * Its own line, not the plan-structure credit's: this text says "we checked your plan",
 * where {@link createPlanStructureCredit} says "this is your plan". Emitted by every
 * function that renders verdict prose, so the sentences are never uncaptioned in any
 * branch — including the branches where no itinerary rendered at all.
 */
export function createVerdictCaption(): HTMLElement {
  const caption = line(
    'siyur-plan-verdict-credit',
    'This verdict is Siyur’s own check of your plan — a computed result, not sourced ' +
      'data, so it carries no source stamp. Any place details it refers to carry their ' +
      'own stamps above.',
  )
  caption.dataset.verdictSource = 'server-computed'
  return caption
}

/**
 * The named violations (FR-005), one row each, in the server's own words.
 *
 * **Never collapsed to "infeasible".** "walking_m 4200 > budget 3000" tells the user
 * what to change; "this plan is infeasible" tells them only that something is wrong.
 * `textContent`, never `innerHTML`: a server-composed string is data, not a template.
 *
 * The reassurance line is spoken only when the verdict actually passes
 * ({@link feasibilityPasses}). A body claiming `ok:true` while naming breaches would
 * otherwise print "this day fits your budgets" *and* drop the one string saying what to
 * fix — the reassurance and the omission being the same mistake twice.
 */
export function renderFeasibility(feasibility: Feasibility): HTMLElement {
  const section = document.createElement('section')
  section.className = 'siyur-plan-feasibility'
  const passes = feasibilityPasses(feasibility)
  section.dataset.ok = String(passes)
  section.dataset.readable = String(feasibility.readable)

  if (!feasibility.readable) {
    section.append(line('siyur-plan-feasibility__title', 'Feasibility was not reported.'))
    section.append(createVerdictCaption())
    return section
  }
  if (passes) {
    section.append(
      line('siyur-plan-feasibility__title', 'This day fits your time and walking budgets.'),
    )
    section.append(createVerdictCaption())
    return section
  }

  section.append(
    line(
      'siyur-plan-feasibility__title',
      // A refusal with nothing named is stated as such: "0 conflicts to resolve" reads
      // as a bug, and pretending the day passed would be the opposite of failing shut.
      feasibility.violations.length === 0
        ? 'This day was refused, but the server named no conflict.'
        : feasibility.violations.length === 1
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
  section.append(createVerdictCaption())
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
  /**
   * Called when {@link PlanPanelOptions.onApprove} rejects.
   *
   * The rejection is never discarded. {@link PlanReviewSurface} renders it onto its own
   * model; a caller rendering a bare panel gets it here, and is expected to re-render
   * with {@link PlanReviewModel.failure} set — {@link describeApprovalFailure} builds it.
   */
  readonly onApproveFailure?: (error: unknown) => void
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
  // Three values, not two: `false` alone conflated "checked, and it does not fit" with
  // "never reported", which are different things to tell a user and to assert on.
  root.dataset.feasible = model.feasibility.readable
    ? String(feasibilityPasses(model.feasibility))
    : 'unknown'
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
  // Directly under the control that failed, so the refusal is where the click was.
  if (model.failure) root.append(renderApprovalFailure(model.failure))

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
 * A refused approval, stated where the click happened.
 *
 * `role="alert"` because the panel re-renders around it: the refusal has to reach a user
 * who is looking at the button, not at the bottom of the page. The `supersededBy` id is
 * rendered as its own addressable line — it is the actionable part of the one refusal a
 * user cannot otherwise diagnose, since the plan on their screen still looks fine.
 */
export function renderApprovalFailure(failure: PlanApprovalFailure): HTMLElement {
  const block = document.createElement('section')
  block.className = 'siyur-plan-approve-failure'
  block.setAttribute('role', 'alert')
  block.dataset.reason = failure.reason ?? 'unknown'
  block.append(line('siyur-plan-approve-failure__message', failure.message))

  if (failure.supersededBy) {
    const successor = line(
      'siyur-plan-approve-failure__superseded',
      `The plan that replaced it is ${failure.supersededBy}.`,
    )
    successor.dataset.supersededBy = failure.supersededBy
    block.append(successor)
  }

  if (failure.violations.length > 0) {
    // The server's own words again (FR-005), verbatim and `textContent`-only. Their own
    // class, so a violation the *refusal* named is never mistaken in a query for one the
    // feasibility section reported — they can disagree, and that difference is the news.
    const list = document.createElement('ul')
    list.className = 'siyur-plan-approve-failure__violations'
    for (const violation of failure.violations) {
      const item = document.createElement('li')
      item.className = 'siyur-plan-approve-failure__violation'
      item.textContent = violation
      list.append(item)
    }
    block.append(list)
  }

  // Server-computed prose about the user's own plan — captioned here too (A1).
  block.append(createVerdictCaption())
  return block
}

/**
 * The HITL gate's control.
 *
 * Rendered as a **disabled** button plus the stated reason rather than as nothing at
 * all: an absent control is indistinguishable from a broken screen, while a disabled
 * one beside the named violations says what to fix. It is disabled *and* unwired — a
 * DOM tamper that clears `disabled` still fires no request, because there is no
 * listener to fire.
 *
 * **In flight is a third state.** While an approve request is outstanding the button is
 * disabled, unbound and says so: approval is irreversible and the server's idempotency
 * is the last line of defence, not the first. The listener additionally disarms itself
 * the instant it fires, so a double-click cannot POST twice in the window before the
 * surface re-renders.
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
  button.textContent = model.approving ? 'Approving this day…' : 'Approve this day'
  button.dataset.approvable = String(verdict.approvable)
  button.dataset.pending = String(model.approving)
  button.disabled = !verdict.approvable || model.approving
  button.setAttribute('aria-disabled', String(button.disabled))
  if (model.approving) button.setAttribute('aria-busy', 'true')
  wrapper.append(button)

  if (verdict.approvable && model.planId && !model.approving) {
    const planId = model.planId
    const { onApprove, onApproveFailure } = options
    // ⬇︎ THE ONLY call site, in the only branch where the server can answer 200.
    if (onApprove) {
      let fired = false
      button.addEventListener('click', () => {
        if (fired) return
        fired = true
        button.disabled = true
        button.dataset.pending = 'true'
        // The rejection is routed, never `void`ed away: a refused approval that changes
        // nothing on screen is a click the user will simply make again.
        void Promise.resolve(onApprove(planId)).catch((error: unknown) => {
          fired = false
          button.disabled = false
          button.dataset.pending = 'false'
          onApproveFailure?.(error)
        })
      })
    }
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
  /**
   * The caller's options with `onApprove` **wrapped**, so the surface owns the request's
   * lifecycle: pending on the way in, {@link PlanApprovalFailure} on the way out. The
   * caller keeps writing a plain `approvePlan(...)` call and does not have to remember
   * that a rejection has to be routed back onto a model it cannot see.
   */
  private readonly panelOptions: PlanReviewOptions

  constructor(
    private readonly container: HTMLElement,
    private readonly options: PlanReviewOptions = {},
  ) {
    const { onApprove } = options
    this.panelOptions = onApprove
      ? { ...options, onApprove: (planId: string) => this.runApprove(planId, onApprove) }
      : options
    this.model = { ...EMPTY_PLAN_MODEL, sites: options.sites ?? new Map() }
    this.panel = renderPlanPanel(this.model, this.panelOptions)
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
    const next = renderPlanPanel(this.model, this.panelOptions)
    this.panel.replaceWith(next)
    this.panel = next
  }

  /**
   * Run the caller's approve, with the two states the panel needs to tell the truth.
   *
   * Does **not** re-throw: the rejection has already been rendered by the time this
   * returns, and re-throwing would leave an unhandled rejection for a failure that is a
   * handled outcome. `options.onApproveFailure`, if the caller supplied one, still gets
   * the original error — the surface handles the *display*, not the caller's business.
   */
  private async runApprove(
    planId: string,
    onApprove: NonNullable<PlanPanelOptions['onApprove']>,
  ): Promise<void> {
    this.update({ approving: true, failure: null })
    try {
      await onApprove(planId)
      this.update({ approving: false })
    } catch (error: unknown) {
      this.update({ approving: false, failure: describeApprovalFailure(error) })
      this.options.onApproveFailure?.(error)
    }
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

  /**
   * Apply a `GET /plans/{id}` body — the read-back path (and the post-approve one).
   *
   * `detail.plan` arrives as a **frame** and is applied unchanged. Re-deriving it here
   * (`plan ? itinerary : unreadable`) is what turned a reloaded honest empty day into
   * "could not be read" — the panel then contradicted, on the same row, what the stream
   * had said a moment earlier. A fresh read-back also clears a stale refusal: it is an
   * answer about the *current* server state, which is exactly what the refusal was
   * about.
   */
  applyDetail(planId: string, detail: PlanApplyDetail): void {
    this.update({
      planId,
      itinerary: detail.plan,
      feasibility: detail.feasibility,
      approval: detail.approval,
      attribution: detail.attribution,
      failure: null,
      approving: false,
    })
  }

  destroy(): void {
    this.panel.remove()
  }
}

/**
 * The `GET /plans/{id}` shape {@link PlanReviewSurface.applyDetail} consumes — the wire
 * type itself, deliberately.
 *
 * It was a separate declaration while it differed (`plan: ItineraryV1 | null`); now that
 * `PlanDetail` carries the frame, a second copy would only be a thing that can drift
 * away from the contract's own shape.
 */
export type PlanApplyDetail = PlanDetail

/** Create and mount a {@link PlanReviewSurface}. */
export function mountPlanReview(
  container: HTMLElement,
  options: PlanReviewOptions = {},
): PlanReviewSurface {
  return new PlanReviewSurface(container, options)
}
