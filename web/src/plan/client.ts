/**
 * Transport for the plan endpoints — `POST /plans` (SSE), `GET /plans/{id}` and
 * `POST /plans/{id}/approve`.
 *
 * Written against `specs/002-plan-compile-offline/contracts/plans.md`; `api/plans.py`
 * is being built in a parallel workstream, so the contract's own worked frame sequence
 * is the interface and the fixture.
 *
 * `SseFrameParser` is **reused from `../map/research`** rather than re-derived. Its
 * chunk-boundary handling (a frame split mid-JSON, a `\r\n` straddling two chunks) is
 * the part a second implementation would get wrong, and `research.ts` already carries
 * an exhaustive every-byte-boundary test for it.
 *
 * **`409` is a value here, not a crash.** Approval is blocked by design while a plan is
 * infeasible or superseded; {@link PlanApprovalConflictError} carries the server's own
 * violations and `superseded_by` so the surface can say which of the two happened.
 */

import { SseFrameParser } from '../map/research'
import {
  readItineraryFrame,
  sanitiseApproveResult,
  sanitiseFeasibility,
  sanitisePlanDetail,
  sanitisePlanDone,
  sanitisePlanStatus,
} from './parse'
import type {
  ApproveResult,
  ExcludedSite,
  Feasibility,
  ItineraryFrame,
  PlanDetail,
  PlanDoneFrame,
  PlanStatusFrame,
  ProposeRequest,
} from './types'

/** Default same-origin endpoint; override for a split API host. */
export const DEFAULT_PLANS_ENDPOINT = '/plans'

export function planPath(planId: string, endpoint = DEFAULT_PLANS_ENDPOINT): string {
  return `${endpoint}/${encodeURIComponent(planId)}`
}

export function approvePath(planId: string, endpoint = DEFAULT_PLANS_ENDPOINT): string {
  return `${planPath(planId, endpoint)}/approve`
}

/** A non-2xx answer from any plan endpoint (`401`, `404`, `422`, `409`). */
export class PlanRequestError extends Error {
  constructor(
    readonly status: number,
    message?: string,
  ) {
    super(message ?? `plan request failed with status ${status}`)
    this.name = 'PlanRequestError'
  }
}

/** `200` with no readable event stream. */
export class PlanStreamError extends Error {
  constructor(message = 'plan response carried no event stream') {
    super(message)
    this.name = 'PlanStreamError'
  }
}

/** The `409` the HITL gate answers with — the mechanical form of FR-005. */
export class PlanApprovalConflictError extends PlanRequestError {
  constructor(
    /** `infeasible` | `plan_superseded`, or `null` when the body named neither. */
    readonly reason: string | null,
    readonly violations: readonly string[],
    readonly supersededBy: string | null,
  ) {
    super(409, `approval refused: ${reason ?? 'conflict'}`)
    this.name = 'PlanApprovalConflictError'
  }
}

function headers(token: string | null | undefined, accept: string): Record<string, string> {
  const out: Record<string, string> = { Accept: accept, 'Content-Type': 'application/json' }
  if (token) out.Authorization = `Bearer ${token}`
  return out
}

/** `JSON.parse` answering `undefined` instead of throwing on a bad frame or body. */
async function readJsonBody(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown
  } catch {
    return undefined
  }
}

/* ------------------------------------------------------ POST /plans (SSE) --- */

/** Per-frame callbacks. Each receives an already-narrowed value, never `unknown`. */
export interface PlanHandlers {
  readonly onStatus?: (status: PlanStatusFrame) => void
  readonly onItinerary?: (frame: ItineraryFrame) => void
  readonly onFeasibility?: (feasibility: Feasibility) => void
  readonly onDone?: (done: PlanDoneFrame | null) => void
}

export interface StreamPlanOptions {
  readonly request: ProposeRequest
  readonly endpoint?: string
  /** Injectable for tests; defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch
  readonly token?: string | null
  readonly signal?: AbortSignal
  readonly handlers?: PlanHandlers
}

/** Everything one proposal stream produced. */
export interface PlanProposal {
  readonly itinerary: ItineraryFrame
  readonly feasibility: Feasibility
  readonly done: PlanDoneFrame | null
  /** `load_sites.candidates` — `0` is the honest "not enough here", not a failure. */
  readonly candidates: number | null
  readonly excluded: readonly ExcludedSite[]
}

/** No `feasibility` frame arrived at all. Not approvable — the gate fails shut. */
const UNREPORTED_FEASIBILITY: Feasibility = {
  ok: false,
  violations: [],
  warnings: [],
  checked_at: null,
  readable: false,
}

/**
 * Propose a day and consume the SSE stream to completion.
 *
 * The request body is sent **exactly as given** — in particular `date`, which this
 * module neither defaults nor reformats. See `./form` for why.
 */
export async function streamPlan(options: StreamPlanOptions): Promise<PlanProposal> {
  const {
    request,
    endpoint = DEFAULT_PLANS_ENDPOINT,
    fetchImpl = globalThis.fetch,
    token,
    signal,
    handlers = {},
  } = options

  const response = await fetchImpl(endpoint, {
    method: 'POST',
    headers: headers(token, 'text/event-stream'),
    body: JSON.stringify(request),
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) throw new PlanRequestError(response.status)
  if (!response.body) throw new PlanStreamError()

  // Held on an object so assignments inside `dispatch` survive TS's control-flow
  // analysis of the closure (same shape as `streamResearch`).
  const state: {
    itinerary: ItineraryFrame
    feasibility: Feasibility
    done: PlanDoneFrame | null
    candidates: number | null
    excluded: ExcludedSite[]
  } = {
    itinerary: { kind: 'absent' },
    feasibility: UNREPORTED_FEASIBILITY,
    done: null,
    candidates: null,
    excluded: [],
  }

  const dispatch = (event: string, data: unknown): void => {
    switch (event) {
      case 'status': {
        const status = sanitisePlanStatus(data)
        if (!status) break
        if (status.candidates !== null) state.candidates = status.candidates
        state.excluded.push(...status.excluded)
        handlers.onStatus?.(status)
        break
      }
      case 'itinerary': {
        state.itinerary = readItineraryFrame(data)
        handlers.onItinerary?.(state.itinerary)
        break
      }
      case 'feasibility': {
        state.feasibility = sanitiseFeasibility(data)
        handlers.onFeasibility?.(state.feasibility)
        break
      }
      case 'done': {
        state.done = sanitisePlanDone(data)
        handlers.onDone?.(state.done)
        break
      }
      default:
        break // an event this client does not know is ignored, never guessed at
    }
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseFrameParser()
  /**
   * The `try` wraps **only** `JSON.parse`. Wrapping `dispatch` too would swallow a
   * throw from a caller's handler and then re-dispatch the same frame with `undefined`
   * data — a handler bug reported as a malformed frame, and the frame delivered twice.
   */
  const emit = (frames: readonly { event: string; data: string }[]): void => {
    for (const frame of frames) {
      let data: unknown
      try {
        data = JSON.parse(frame.data) as unknown
      } catch {
        data = undefined
      }
      dispatch(frame.event, data)
    }
  }
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      emit(parser.push(decoder.decode(value, { stream: true })))
    }
    emit(parser.push(decoder.decode()))
    // A server that closed without the final blank line still gets its last frame read.
    emit(parser.flush())
  } finally {
    reader.releaseLock()
  }

  return {
    itinerary: state.itinerary,
    feasibility: state.feasibility,
    done: state.done,
    candidates: state.candidates,
    excluded: state.excluded,
  }
}

/* ------------------------------------------------- GET / approve (plain) --- */

export interface PlanFetchOptions {
  readonly endpoint?: string
  readonly fetchImpl?: typeof fetch
  readonly token?: string | null
  readonly signal?: AbortSignal
}

/** Read a plan and its approval state. `404` covers another user's plan by design. */
export async function fetchPlan(
  planId: string,
  options: PlanFetchOptions = {},
): Promise<PlanDetail> {
  const { endpoint, fetchImpl = globalThis.fetch, token, signal } = options
  const response = await fetchImpl(planPath(planId, endpoint), {
    method: 'GET',
    headers: headers(token, 'application/json'),
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) throw new PlanRequestError(response.status)
  return sanitisePlanDetail(await readJsonBody(response))
}

/**
 * The HITL gate. Empty body; `proposed → approved`, the only transition that unlocks
 * compilation.
 *
 * Idempotent server-side: a second approve returns the **same** `approved_at`, so a
 * raced pair cannot produce two divergent bundles.
 */
export async function approvePlan(
  planId: string,
  options: PlanFetchOptions = {},
): Promise<ApproveResult> {
  const { endpoint, fetchImpl = globalThis.fetch, token, signal } = options
  const response = await fetchImpl(approvePath(planId, endpoint), {
    method: 'POST',
    headers: headers(token, 'application/json'),
    body: '{}',
    ...(signal ? { signal } : {}),
  })

  if (response.status === 409) {
    const body = await readJsonBody(response)
    const record = (typeof body === 'object' && body !== null ? body : {}) as Record<
      string,
      unknown
    >
    const violations = Array.isArray(record.violations)
      ? record.violations.filter((v): v is string => typeof v === 'string' && v.trim() !== '')
      : []
    throw new PlanApprovalConflictError(
      typeof record.error === 'string' ? record.error : null,
      violations,
      typeof record.superseded_by === 'string' ? record.superseded_by : null,
    )
  }
  if (!response.ok) throw new PlanRequestError(response.status)

  const result = sanitiseApproveResult(await readJsonBody(response))
  if (!result) throw new PlanRequestError(response.status, 'approve returned no plan_id')
  return result
}
