/**
 * Wire types for `POST /plans`, `GET /plans/{plan_id}` and
 * `POST /plans/{plan_id}/approve` — `specs/002-plan-compile-offline/contracts/plans.md`.
 *
 * `ItineraryV1` and its parts are **imported from `../travel/types`, not restated**.
 * The plan under review and the plan frozen into a bundle are the same object read over
 * two transports; ADR-0002's point is that the read model does not change when the
 * transport does, and a second declaration of it here would be the first crack in that.
 */

import type { ItineraryV1, RouteLegV1, Stop, TimelineEntry } from '../travel/types'

export type { ItineraryV1, RouteLegV1, Stop, TimelineEntry }

/**
 * **ADR-0023's seven states, exposed verbatim** — no DB→API mapping layer (T007b).
 *
 * An earlier draft of the contract listed three, which would have rendered a
 * `compiling` plan as an unknown state; and collapsing `compiling` into `approved`
 * leaves the surface unable to tell "approved, idle" from "approved, compile running".
 */
export type PlanState =
  | 'proposing'
  | 'proposed'
  | 'approved'
  | 'superseded'
  | 'compiling'
  | 'compiled'
  | 'failed'

/** Every state, for exhaustive narrowing. Order is the contract's. */
export const PLAN_STATES: readonly PlanState[] = [
  'proposing',
  'proposed',
  'approved',
  'superseded',
  'compiling',
  'compiled',
  'failed',
]

/**
 * `POST /plans` request body.
 *
 * `date` is a plain `YYYY-MM-DD` in the **area's** local frame and is **required**:
 * it is what `opening-hours-py` evaluates weekday rules, `PH` and seasonal ranges
 * against. Nothing in this package defaults it — see `./form`.
 */
export interface ProposeRequest {
  readonly area_id: string
  readonly budgets: { readonly walking_m: number; readonly hours: number }
  readonly interests: string
  readonly date: string
  /** Area-local wall clock the day begins at. Request-only: not a field of `ItineraryV1`. */
  readonly day_start: string
  readonly lang: string
}

/** A site the walking network could not reach — dropped, never straight-lined (FR-003). */
export interface ExcludedSite {
  readonly site_id: string
  readonly reason: string
}

/**
 * A `status` frame. Every field but `phase` is optional on the wire, so anything the
 * frame did not carry reads as `null` rather than as a number this client invented.
 */
export interface PlanStatusFrame {
  readonly phase: string
  /** `load_sites` — how many commons records were available to draw stops from. */
  readonly candidates: number | null
  readonly stops: number | null
  readonly tier: string | null
  readonly provider: string | null
  readonly legs: number | null
  readonly excluded: readonly ExcludedSite[]
}

/**
 * The deterministic feasibility verdict (FR-005). Lives on the `user_plan` row, never
 * inside `ItineraryV1` (itinerary card: "the feasibility verdict is not a field").
 *
 * `readable` is this client's own flag, keyed to the one field the contract always
 * sends. An unreadable or absent verdict yields `ok:false` **with no violations**, which
 * is not the same statement as "feasible with nothing wrong" — `readable` is what keeps
 * the surface from reporting the second when it only learned the first.
 */
export interface Feasibility {
  readonly ok: boolean
  /**
   * The specific budget or opening window breached, in the server's own words.
   * **Blocking**: a non-empty `violations` is why `ok` is false.
   */
  readonly violations: readonly string[]
  /**
   * Advisory only, and a **separate list** rather than a flag inside `violations`
   * (ADR-0022, amended 2026-08-14). Today every entry is a stop whose opening hours could
   * not be evaluated — "we do not know", which is not "we know it is shut". A non-empty
   * `warnings` beside `ok: true` is the normal case: most OSM records carry no
   * `opening_hours` at all, and blocking on that would make no real day approvable.
   *
   * The separation is what stops a renderer disabling approve on one, so it must not be
   * flattened into `violations` anywhere downstream.
   */
  readonly warnings: readonly string[]
  /** UTC, set only when the check ran — never mirrored from `updated_at`. */
  readonly checked_at: string | null
  readonly readable: boolean
}

/**
 * `approval` block of `GET /plans/{plan_id}`.
 *
 * `state: null` ⇒ the server named a state this client does not know — reported as
 * unrecognised rather than mapped onto whichever of the seven looks closest.
 */
export interface PlanApproval {
  readonly state: PlanState | null
  readonly approved_at: string | null
  /** Set when a newer proposal replaced this one; approve that id instead. */
  readonly superseded_by: string | null
}

/**
 * `200` body of `GET /plans/{plan_id}`.
 *
 * `plan` is an {@link ItineraryFrame}, **not `ItineraryV1 | null`**. `null` collapsed
 * the four outcomes back into two at the type level, so the read-back path reported an
 * honest zero-stop day — the contract's "not enough here", which the stream renders
 * correctly — as "could not be read": two contradictory statements about one row, the
 * second of which tells the user the app is broken when it is working. The distinction
 * has to survive the type or no render layer can restore it.
 */
export interface PlanDetail {
  readonly plan: ItineraryFrame
  readonly feasibility: Feasibility
  readonly approval: PlanApproval
  /** Union of the required credit strings, mirrored verbatim — never composed here. */
  readonly attribution: readonly string[]
}

/** The `done` frame: the persisted plan id and the state it stopped at. */
export interface PlanDoneFrame {
  readonly plan_id: string
  readonly state: PlanState | null
}

/** `200` body of `POST /plans/{plan_id}/approve`. */
export interface ApproveResult {
  readonly plan_id: string
  readonly state: PlanState | null
  readonly approved_at: string | null
}

/**
 * What the `itinerary` event carried.
 *
 * Four outcomes, because three of them would be reported as the same wrong thing:
 * an honest zero-stop day (the contract's "not enough here", never padded) is not an
 * unreadable frame, and a stream that never proposed at all is neither.
 */
export type ItineraryFrame =
  | { readonly kind: 'itinerary'; readonly itinerary: ItineraryV1 }
  | { readonly kind: 'empty' }
  | { readonly kind: 'unreadable' }
  | { readonly kind: 'absent' }
