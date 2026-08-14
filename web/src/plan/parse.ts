/**
 * Narrowing for the plan API — the same discipline `../map/guards.ts` applies to
 * `GET /sites`: the contract promises a shape, and the client checks anyway.
 *
 * Two properties this module exists to hold:
 *
 * 1. **Feasibility fails closed.** A missing, malformed or unreadable verdict is
 *    `ok:false, readable:false` — never `ok:true` by omission and never "no violations,
 *    so fine". A verdict that says `ok:true` while *naming* violations contradicts
 *    itself and is read as not-ok, with the violations kept. Approval is gated on this
 *    value, and the only safe direction for a gate to fail is shut. **`warnings` are
 *    outside that rule entirely**: `ok:true` beside a full `warnings[]` is the contract's
 *    normal answer (ADR-0022, amended 2026-08-14), so folding them into the same check
 *    would re-block every day the amendment exists to unblock. A server that has not been
 *    updated simply sends no `warnings`, which reads as `[]` and changes nothing.
 * 2. **An unknown `approval.state` stays unknown.** The seven states are a closed set;
 *    an eighth is narrowed to `null` and reported as unrecognised, rather than mapped
 *    onto whichever known state looks closest.
 *
 * `parseItinerary` is **reused from `../travel/parse`**, deep-imported rather than
 * through `../travel` — the barrel pulls `geojson-path-finder` and the bundle storage
 * reader in behind it, which the planning surface has no use for (the same reason
 * `../map/index.ts` refuses to re-export `./basemap`). It is the transcription of
 * `docs/data/itinerary.md`; a second copy here would be a second thing to keep in
 * agreement with the card.
 */

import { parseItinerary } from '../travel/parse'
import type {
  ApproveResult,
  ExcludedSite,
  Feasibility,
  ItineraryFrame,
  PlanApproval,
  PlanDetail,
  PlanDoneFrame,
  PlanState,
  PlanStatusFrame,
} from './types'
import { PLAN_STATES } from './types'

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

const str = (v: unknown): string | null =>
  typeof v === 'string' && v.trim() !== '' ? v : null

const count = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null

/** A state string, or `null` for anything outside the closed seven. */
export function planState(v: unknown): PlanState | null {
  return typeof v === 'string' && (PLAN_STATES as readonly string[]).includes(v)
    ? (v as PlanState)
    : null
}

/** `route.excluded[]` — a row needs both halves to be worth showing. */
function excludedSites(v: unknown): readonly ExcludedSite[] {
  if (!Array.isArray(v)) return []
  const out: ExcludedSite[] = []
  for (const row of v) {
    if (!isRecord(row)) continue
    const site_id = str(row.site_id)
    const reason = str(row.reason)
    if (site_id && reason) out.push({ site_id, reason })
  }
  return out
}

/**
 * A `status` frame. `null` without a `phase` — a progress line that cannot name its
 * phase is not progress, and this client will not label it "working…" on the server's
 * behalf (`../map/guards.ts::sanitiseResearchStatus`, same rule).
 */
export function sanitisePlanStatus(raw: unknown): PlanStatusFrame | null {
  if (!isRecord(raw) || !str(raw.phase)) return null
  return {
    phase: raw.phase as string,
    candidates: count(raw.candidates),
    stops: count(raw.stops),
    tier: str(raw.tier),
    provider: str(raw.provider),
    legs: count(raw.legs),
    excluded: excludedSites(raw.excluded),
  }
}

/**
 * The verdict, failing closed. See the module note.
 *
 * **`ok:true` with a non-empty `violations[]` is read as not-ok.** The contract pairs
 * `ok:false` with the named breaches, so the combination is a self-contradicting body —
 * a half-deployed checker, a partial write, a merge of two verdicts. Believing `ok`
 * would open the one irreversible gate in the product *and* drop the sentence saying
 * what is wrong, and the server would then answer `409` anyway. Believing `violations`
 * costs a user a re-plan. Nothing is discarded: both fields are kept, so the panel still
 * shows the server's own words.
 *
 * **`warnings` take no part in that rule and never touch `ok`.** They are read with the
 * same filter and kept in their own list; an absent `warnings` is `[]`, which is what an
 * older server sends and is indistinguishable from a day with nothing to warn about —
 * correctly, since neither is a reason to refuse anything.
 */
export function sanitiseFeasibility(raw: unknown): Feasibility {
  const body = isRecord(raw) ? raw : {}
  const strings = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((entry): entry is string => str(entry) !== null) : []
  const violations = strings(body.violations)
  return {
    ok: body.ok === true && violations.length === 0,
    violations,
    warnings: strings(body.warnings),
    checked_at: str(body.checked_at),
    // Keyed to `ok`, the one field the contract always sends.
    readable: typeof body.ok === 'boolean',
  }
}

/** The `approval` block. An absent block reads as an unknown state, not as `proposed`. */
export function sanitiseApproval(raw: unknown): PlanApproval {
  const body = isRecord(raw) ? raw : {}
  return {
    state: planState(body.state),
    approved_at: str(body.approved_at),
    superseded_by: str(body.superseded_by),
  }
}

/** The `done` frame — `null` without a `plan_id`, since there is nothing to address. */
export function sanitisePlanDone(raw: unknown): PlanDoneFrame | null {
  if (!isRecord(raw)) return null
  const plan_id = str(raw.plan_id)
  return plan_id ? { plan_id, state: planState(raw.state) } : null
}

/** `POST /plans/{id}/approve` `200` body. */
export function sanitiseApproveResult(raw: unknown): ApproveResult | null {
  if (!isRecord(raw)) return null
  const plan_id = str(raw.plan_id)
  return plan_id
    ? { plan_id, state: planState(raw.state), approved_at: str(raw.approved_at) }
    : null
}

/**
 * Read the `itinerary` event.
 *
 * `parseItinerary` folds "no addressable stops" into the same `null` it returns for a
 * malformed object — correct for the travel surface, where an empty day means the
 * bundle is broken, and wrong here, where it is the contract's explicit "a day with too
 * little in it yields a shorter honest plan … never padding". So a well-formed
 * `ItineraryV1` carrying zero stops is separated out and reported as `empty`.
 */
export function readItineraryFrame(raw: unknown): ItineraryFrame {
  const itinerary = parseItinerary(raw)
  if (itinerary) return { kind: 'itinerary', itinerary }
  if (
    isRecord(raw) &&
    raw.schema_ver === 'ItineraryV1' &&
    Array.isArray(raw.stops) &&
    raw.stops.length === 0
  ) {
    return { kind: 'empty' }
  }
  return { kind: 'unreadable' }
}

/**
 * The `plan` field of a detail body, as a frame.
 *
 * An absent or `null` `plan` is **`absent`**, not `unreadable`: a row still `proposing`
 * has no itinerary yet, which is not the statement "the itinerary is corrupt". Anything
 * else goes through {@link readItineraryFrame}, so the read-back path keeps the honest
 * zero-stop day apart from a malformed one exactly as the stream does — the two paths
 * describing one row must not contradict each other.
 */
function planFrame(raw: unknown): ItineraryFrame {
  return raw === undefined || raw === null ? { kind: 'absent' } : readItineraryFrame(raw)
}

/** The whole `GET /plans/{plan_id}` body. */
export function sanitisePlanDetail(raw: unknown): PlanDetail {
  const body = isRecord(raw) ? raw : {}
  return {
    plan: planFrame(body.plan),
    feasibility: sanitiseFeasibility(body.feasibility),
    approval: sanitiseApproval(body.approval),
    // Server-owned wording, filtered but never rewritten (`../map/attribution.ts`).
    attribution: Array.isArray(body.attribution)
      ? body.attribution.filter((v): v is string => str(v) !== null)
      : [],
  }
}
