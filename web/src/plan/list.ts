/**
 * Phase A transport — `GET /plans` and `GET /areas`, the two reads that give the app a
 * memory (`docs/design/usable-m1-plan.md` § Phase A).
 *
 * Until these existed, every id lived only in the response that created it: close the tab
 * and the plan was unreachable forever. Everything here exists to turn that response into
 * a list a person can come back to.
 *
 * Three properties this module holds, each keyed to a way the list can lie:
 *
 * **1. Empty is a success, never an error.** `{"plans": []}` with status `200` is the
 * contract's answer for an account that has planned nothing, and it renders as an empty
 * state. A `404` is a different fact and stays an error.
 *
 * **2. An unreadable body is an error, never an empty list.** This is not defensive
 * padding — it is the one failure this repo has already been bitten by. `vite.config.ts`'s
 * `navigateFallback` answers an unproxied *navigation* GET with `index.html` and status
 * `200`, so a proxy hole turns `GET /plans` into the app shell: `response.ok` is true and
 * the body is HTML. Folding that into `[]` would render **"You have not planned a day
 * yet"** to a user with seven plans — a sentence that is both wrong and unfalsifiable.
 * So a body that carries no `plans` array at all throws {@link ListResponseError}, and the
 * surface says the list could not be read.
 *
 * **3. Server order is preserved verbatim.** Both endpoints promise newest-first, and
 * re-sorting here would mean parsing `created_at` — which this package may not do (see
 * `./render`'s header: no `Date`, ever, anywhere under `src/plan/`). The list is the
 * server's order or it is nothing.
 *
 * Rows degrade per field rather than per list: a summary missing its `state` reads as an
 * unrecognised state, not as a dropped plan. Only a row with no id is dropped — there is
 * nothing to address it by, so there is nothing a tap on it could open.
 */

import { DEFAULT_PLANS_ENDPOINT, PlanRequestError } from './client'
import { planState } from './parse'
import type { PlanState } from './types'

/**
 * Default same-origin endpoint for the area list. The plan list reuses
 * `./client::DEFAULT_PLANS_ENDPOINT` — `GET /plans` and `POST /plans` are the same
 * collection, and a second constant for it would be a second thing to keep in step.
 */
export const DEFAULT_AREAS_ENDPOINT = '/areas'

/**
 * A `200` whose body was not the shape the contract promises.
 *
 * Separate from {@link PlanRequestError} on purpose: "the server refused" and "the server
 * answered with something else entirely" need different words, and the second is what a
 * missing dev-proxy entry looks like from here.
 */
export class ListResponseError extends Error {
  constructor(readonly path: string) {
    super(`${path} answered 200 with a body this client could not read`)
    this.name = 'ListResponseError'
  }
}

/* ------------------------------------------------------------------ types --- */

/**
 * One row of `GET /plans`.
 *
 * `state: null` ⇒ the server named a state outside ADR-0023's closed seven, reported as
 * unrecognised rather than mapped onto whichever one looks closest (`./parse`, same rule).
 *
 * `feasible: null` is a **third value**, not a `false`: "the verdict was not reported" and
 * "the day does not fit" are different things to tell someone, and `./render` already
 * carries that distinction on `data-feasible`.
 */
export interface PlanSummary {
  readonly plan_id: string
  readonly area_id: string | null
  /** `YYYY-MM-DD`, area-local. Rendered verbatim — never re-formatted through a locale. */
  readonly date: string | null
  readonly state: PlanState | null
  readonly feasible: boolean | null
  readonly stop_count: number | null
  /**
   * Tz-aware UTC **audit** instants, kept as the server's own strings — part of the
   * contract, parsed by nothing and **rendered nowhere**. See `./library`'s "two clocks"
   * note: showing one legibly requires a timezone conversion, and this package may not
   * construct a `Date`. Recency reaches the user through the list's order instead.
   *
   * The wire form is `…+00:00` (pydantic), not `…Z`. Nothing may pattern-match either:
   * that is one endpoint's serialisation, not a fact about the column.
   */
  readonly created_at: string | null
  readonly approved_at: string | null
  /** Set when a newer revision replaced this plan — the id to open instead. */
  readonly superseded_by: string | null
}

/**
 * One row of `GET /areas`.
 *
 * `name` is **the free-text name the user asked for** (`api/areas.py::AreaSummaryBody`),
 * `null` when they delimited by bbox or drew a ring. It is user-owned data, not commons
 * data — which is why it carries no `SourceRef` and renders with no chip, under a stated
 * credit, exactly as plan structure does (ADR-0019 / `./render` mechanism point 2).
 */
export interface AreaSummary {
  readonly area_id: string
  readonly name: string | null
  /** `[minLon, minLat, maxLon, maxLat]`, EPSG:4326, or `null` — never repaired. */
  readonly bbox: readonly [number, number, number, number] | null
  /** A tz-aware UTC audit instant. Kept because it is the contract; **rendered nowhere**. */
  readonly created_at: string | null
  /**
   * When a research *pass* over this area last committed.
   *
   * ⚠️ **`null` does NOT mean "never researched", and must never be rendered as one.**
   * The field answers *"did a pass run?"*, and `api/areas.py:474` short-circuits an
   * already-covered area to `_reuse_frames` — a reuse hint, not a pass — which returns
   * without stamping it. So a reused area is fully researched and still `null` here.
   * Measured on the live database: **14 areas, 3 stamped**, and the newest ones were null
   * minutes after being researched.
   *
   * The rule that follows: **show the date when present, show nothing when absent.**
   * Absence of a timestamp is not evidence of absence of research, and a row reading "not
   * researched" over 958 cited places is the third instance in this codebase of one shape
   * — *a truthful field rendered as the answer to a question it was not asked* — after the
   * coverage card's "No cited places here yet" against `known_site_count: 958` and the
   * plan panel's `data-plan-state="proposing"` beside "No day has been proposed yet".
   *
   * The real "is this covered?" signal is **not on this endpoint**: `known_site_count` was
   * deliberately kept off it because it is a PostGIS count per row and would turn one list
   * into N spatial queries. Do not reconstruct it client-side by fanning out `POST /areas`
   * per row — if the list genuinely needs coverage, that is a server decision to make.
   */
  readonly researched_at: string | null
}

/* -------------------------------------------------------------- narrowing --- */

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)

const count = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.trunc(v) : null

/**
 * An EPSG:4326 extent with actual area.
 *
 * A degenerate or out-of-range bbox is `null` rather than clamped: the client's one job
 * with a geometry it does not understand is to decline it. `map/delimit.ts::isUsableBbox`
 * applies the same rule to the outgoing direction.
 */
export function listBbox(v: unknown): readonly [number, number, number, number] | null {
  if (!Array.isArray(v) || v.length !== 4) return null
  const [minLon, minLat, maxLon, maxLat] = v as unknown[]
  const nums = [minLon, minLat, maxLon, maxLat]
  if (!nums.every((n): n is number => typeof n === 'number' && Number.isFinite(n))) return null
  const [a, b, c, d] = nums as [number, number, number, number]
  if (a < -180 || c > 180 || b < -90 || d > 90) return null
  return c > a && d > b ? [a, b, c, d] : null
}

/** One `GET /plans` row. `null` without a `plan_id` — nothing could open it. */
export function sanitisePlanSummary(raw: unknown): PlanSummary | null {
  if (!isRecord(raw)) return null
  const plan_id = str(raw.plan_id)
  if (!plan_id) return null
  return {
    plan_id,
    area_id: str(raw.area_id),
    date: str(raw.date),
    state: planState(raw.state),
    // Strictly the server's own boolean. Anything else is "not reported", which is not
    // the same claim as "not feasible" and must not be rendered as one.
    feasible: typeof raw.feasible === 'boolean' ? raw.feasible : null,
    stop_count: count(raw.stop_count),
    created_at: str(raw.created_at),
    approved_at: str(raw.approved_at),
    superseded_by: str(raw.superseded_by),
  }
}

/** One `GET /areas` row. `null` without an `area_id`. */
export function sanitiseAreaSummary(raw: unknown): AreaSummary | null {
  if (!isRecord(raw)) return null
  const area_id = str(raw.area_id)
  if (!area_id) return null
  return {
    area_id,
    name: str(raw.name),
    bbox: listBbox(raw.bbox),
    created_at: str(raw.created_at),
    researched_at: str(raw.researched_at),
  }
}

/**
 * The whole `GET /plans` body — `null` when it carried no `plans` array at all.
 *
 * `null` and `[]` are the two answers this function exists to keep apart. See property 2
 * in the module header: collapsing them is how HTML-with-200 becomes "you have no plans".
 */
export function sanitisePlanList(raw: unknown): readonly PlanSummary[] | null {
  if (!isRecord(raw) || !Array.isArray(raw.plans)) return null
  return raw.plans
    .map(sanitisePlanSummary)
    .filter((row): row is PlanSummary => row !== null)
}

/** The whole `GET /areas` body — `null` when it carried no `areas` array. */
export function sanitiseAreaList(raw: unknown): readonly AreaSummary[] | null {
  if (!isRecord(raw) || !Array.isArray(raw.areas)) return null
  return raw.areas
    .map(sanitiseAreaSummary)
    .filter((row): row is AreaSummary => row !== null)
}

/* ------------------------------------------------------------------ fetch --- */

export interface ListFetchOptions {
  readonly endpoint?: string
  /** Injectable for tests; defaults to the global `fetch`. */
  readonly fetchImpl?: typeof fetch
  readonly token?: string | null
  readonly signal?: AbortSignal
}

/** `JSON.parse` answering `undefined` instead of throwing — an HTML body lands here. */
async function readJsonBody(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown
  } catch {
    return undefined
  }
}

async function getJson(path: string, options: ListFetchOptions): Promise<unknown> {
  const { fetchImpl = globalThis.fetch, token, signal } = options
  const head: Record<string, string> = { Accept: 'application/json' }
  if (token) head.Authorization = `Bearer ${token}`
  const response = await fetchImpl(path, {
    method: 'GET',
    headers: head,
    ...(signal ? { signal } : {}),
  })
  if (!response.ok) {
    throw new PlanRequestError(response.status, `GET ${path} failed with status ${response.status}`)
  }
  return readJsonBody(response)
}

/** The caller's plans, newest first. An account with none yields `[]`, not a throw. */
export async function fetchPlanList(options: ListFetchOptions = {}): Promise<readonly PlanSummary[]> {
  const path = options.endpoint ?? DEFAULT_PLANS_ENDPOINT
  const plans = sanitisePlanList(await getJson(path, options))
  if (!plans) throw new ListResponseError(path)
  return plans
}

/** The caller's areas, newest first — so an area can be revisited without re-delimiting. */
export async function fetchAreaList(options: ListFetchOptions = {}): Promise<readonly AreaSummary[]> {
  const path = options.endpoint ?? DEFAULT_AREAS_ENDPOINT
  const areas = sanitiseAreaList(await getJson(path, options))
  if (!areas) throw new ListResponseError(path)
  return areas
}

/** Index a fetched area list by id, for the plan rows that name an `area_id`. */
export function indexAreas(areas: readonly AreaSummary[]): ReadonlyMap<string, AreaSummary> {
  return new Map(areas.map((area) => [area.area_id, area]))
}
