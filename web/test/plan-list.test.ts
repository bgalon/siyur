/**
 * Phase A — "your plans": the surface that gives the app a memory
 * (`docs/design/usable-m1-plan.md` § Phase A).
 *
 * The load-bearing assertions, each named with the bug it catches:
 *
 * - **an unreadable body is an error, never an empty list.** `vite.config.ts`'s
 *   `navigateFallback` answers an unproxied navigation GET with `index.html` and status
 *   `200`; folding that into `[]` would render "You have not planned a day yet" to a user
 *   with seven plans. `{"plans": []}` is still an ordinary empty state;
 * - **no message contradicts its own attribute.** The audit's emblematic defect was
 *   `data-plan-state="proposing"` beside "No day has been proposed yet." Every one of
 *   ADR-0023's seven states is asserted here as *attribute plus sentence*, and a model
 *   claiming `empty` while holding plans is asserted to render the plans;
 * - **server order is preserved.** Both endpoints promise newest-first and this package
 *   may not construct a `Date`, so a client-side sort would be both forbidden and wrong;
 * - **an unrecognised state stays unrecognised** rather than being mapped onto whichever
 *   of the seven looks closest;
 * - **the mobile floors are met by the CSS that actually ships** — `library.css` is
 *   scanned for the ≥14px body / ≥44px tap-target / logical-properties rules, because a
 *   DOM test in jsdom cannot see a layout and a comment claiming a property is not a
 *   measurement.
 *
 * No network and no clock: `fetchImpl` is injected, exactly as `plan.test.ts` does.
 */

import { describe, expect, it, vi } from 'vitest'

import {
  EMPTY_LIBRARY_MODEL,
  ListResponseError,
  PlanRequestError,
  areaLabel,
  describeListFailure,
  fetchAreaList,
  fetchPlanList,
  indexAreas,
  libraryHeadline,
  listBbox,
  mountPlanLibrary,
  renderPlanLibrary,
  sanitiseAreaList,
  sanitisePlanList,
  sanitisePlanSummary,
  stateLabel,
  type AreaSummary,
  type LibraryState,
  type PlanLibraryModel,
  type PlanSummary,
} from '../src/plan'
import { PLAN_STATES } from '../src/plan/types'

/* -------------------------------------------------------------- fixtures --- */

const PLAN_A = '1356bbae-24d7-4b06-8a03-3745a1c22535'
const PLAN_B = 'ed1420df-bf5b-4025-997e-969e3f4b3a71'
const AREA_A = '5ec4f27e-e77a-468f-ad93-e5e84b432d0f'

/** A row exactly as `GET /plans` sends it (verified against the running API). */
const wirePlan = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  plan_id: PLAN_A,
  area_id: AREA_A,
  date: '2026-09-01',
  state: 'approved',
  feasible: true,
  stop_count: 6,
  // `…Z` — what pydantic 2.13.4 and the running API actually emit, verified by curl.
  // Nothing under test depends on the spelling; see the round-trip case below.
  created_at: '2026-08-14T17:14:08.017944Z',
  approved_at: '2026-08-14T17:14:08.077780Z',
  superseded_by: null,
  ...over,
})

const plan = (over: Partial<PlanSummary> = {}): PlanSummary => ({
  plan_id: PLAN_A,
  area_id: AREA_A,
  date: '2026-09-01',
  state: 'approved',
  feasible: true,
  stop_count: 6,
  // `…Z` — what pydantic 2.13.4 and the running API actually emit, verified by curl.
  // Nothing under test depends on the spelling; see the round-trip case below.
  created_at: '2026-08-14T17:14:08.017944Z',
  approved_at: '2026-08-14T17:14:08.077780Z',
  superseded_by: null,
  ...over,
})

const area = (over: Partial<AreaSummary> = {}): AreaSummary => ({
  area_id: AREA_A,
  name: null,
  bbox: [28.21, 36.413882, 28.24, 36.466111],
  created_at: '2026-08-14T21:10:42.008711+00:00',
  // Null on live data even for fully-researched areas — the default here on purpose.
  researched_at: null,
  ...over,
})

const model = (over: Partial<PlanLibraryModel> = {}): PlanLibraryModel => ({
  ...EMPTY_LIBRARY_MODEL,
  state: 'ready',
  plans: [plan()],
  open: true,
  ...over,
})

/** A `fetch` that answers one JSON body. */
const jsonFetch = (body: unknown, status = 200): typeof fetch =>
  vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch

/* ------------------------------------------------------------ the wire --- */

describe('empty is a success and unreadable is an error — they are never the same answer', () => {
  it('reads {"plans": []} as an empty list, not as a failure', async () => {
    await expect(fetchPlanList({ fetchImpl: jsonFetch({ plans: [] }) })).resolves.toEqual([])
    expect(sanitisePlanList({ plans: [] })).toEqual([])
  })

  it('rejects an HTML-with-200 body rather than reporting "you have no plans"', async () => {
    // The dev-proxy hole `vite.config.ts` documents: `navigateFallback` returns the app
    // shell with status 200, so `response.ok` is true and `.json()` is what notices.
    const htmlFetch = vi.fn(
      async () =>
        new Response('<!doctype html><title>Siyur</title>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        }),
    ) as unknown as typeof fetch
    await expect(fetchPlanList({ fetchImpl: htmlFetch })).rejects.toBeInstanceOf(
      ListResponseError,
    )
  })

  it('rejects a 200 whose body carries no plans array', async () => {
    expect(sanitisePlanList({})).toBeNull()
    expect(sanitisePlanList({ plans: 'soon' })).toBeNull()
    await expect(fetchPlanList({ fetchImpl: jsonFetch({ ok: true }) })).rejects.toBeInstanceOf(
      ListResponseError,
    )
  })

  it('reports a non-2xx with its status, so a 404 stays a 404', async () => {
    const error = await fetchPlanList({ fetchImpl: jsonFetch({}, 404) }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(PlanRequestError)
    expect((error as PlanRequestError).status).toBe(404)
    // The path is in the message: "the plan list 404ed" and "this plan 404ed" are
    // different facts and a support conversation needs to know which.
    expect((error as PlanRequestError).message).toContain('/plans')
  })
})

describe('a row degrades per field; only an unaddressable row is dropped', () => {
  it('keeps a row whose state the server named outside ADR-0023’s seven', () => {
    expect(sanitisePlanSummary(wirePlan({ state: 'archived' }))?.state).toBeNull()
  })

  it('reads a missing feasibility as unknown, never as false', () => {
    expect(sanitisePlanSummary(wirePlan({ feasible: undefined }))?.feasible).toBeNull()
    expect(sanitisePlanSummary(wirePlan({ feasible: 'yes' }))?.feasible).toBeNull()
    expect(sanitisePlanSummary(wirePlan({ feasible: false }))?.feasible).toBe(false)
  })

  it('drops only the row with no plan_id — there is nothing a tap could open', () => {
    const rows = sanitisePlanList({
      plans: [wirePlan(), { area_id: AREA_A, date: '2026-09-02' }, wirePlan({ plan_id: PLAN_B })],
    })
    expect(rows?.map((row) => row.plan_id)).toEqual([PLAN_A, PLAN_B])
  })

  it('preserves the server’s order verbatim — nothing is re-sorted client-side', () => {
    // Deliberately not newest-first: whatever order the server sent is the order rendered.
    const rows = sanitisePlanList({
      plans: [
        wirePlan({ plan_id: PLAN_A, created_at: '2026-01-01T00:00:00Z' }),
        wirePlan({ plan_id: PLAN_B, created_at: '2026-12-31T00:00:00Z' }),
      ],
    })
    expect(rows?.map((row) => row.plan_id)).toEqual([PLAN_A, PLAN_B])
  })
})

describe('GET /areas — the bbox is checked, never repaired', () => {
  it('accepts the extent the running API returns', () => {
    expect(listBbox([28.21, 36.413882, 28.24, 36.466111])).toEqual([
      28.21, 36.413882, 28.24, 36.466111,
    ])
  })

  it('answers null for a degenerate or out-of-range extent instead of clamping it', () => {
    expect(listBbox([28.21, 36.4, 28.21, 36.5])).toBeNull() // zero width
    expect(listBbox([-181, 36.4, 28.24, 36.5])).toBeNull()
    expect(listBbox([28.21, 36.4, 28.24])).toBeNull()
    expect(listBbox('28,36,29,37')).toBeNull()
  })

  it('reads the list and indexes it by id', async () => {
    const areas = await fetchAreaList({
      fetchImpl: jsonFetch({ areas: [{ area_id: AREA_A, name: 'Old Town', bbox: null }] }),
    })
    expect(indexAreas(areas).get(AREA_A)?.name).toBe('Old Town')
    expect(sanitiseAreaList({ areas: [{ name: 'nameless' }] })).toEqual([])
  })
})

/* ------------------------------------------------------------- rendering --- */

describe('every one of the seven states is legible, and no sentence contradicts its attribute', () => {
  it('renders each state with a label of its own, matching data-plan-state', () => {
    const seen = new Set<string>()
    for (const state of PLAN_STATES) {
      const list = renderPlanLibrary(model({ plans: [plan({ state })] }))
      const row = list.querySelector<HTMLElement>('.siyur-library__row')
      expect(row?.dataset.planState).toBe(state)
      const label = row?.querySelector('.siyur-library__state')?.textContent ?? ''
      expect(label).toBe(stateLabel(state))
      expect(label).not.toBe('')
      // Seven distinct sentences: a shared label would make two states indistinguishable
      // to the person reading, which is the whole failure mode being guarded against.
      expect(seen.has(label)).toBe(false)
      seen.add(label)
    }
    expect(seen.size).toBe(7)
  })

  it('names an unrecognised state as unrecognised rather than guessing at it', () => {
    const list = renderPlanLibrary(model({ plans: [plan({ state: null })] }))
    const row = list.querySelector<HTMLElement>('.siyur-library__row')
    expect(row?.dataset.planState).toBe('unknown')
    expect(row?.querySelector('.siyur-library__state')?.textContent).toBe('State not recognised')
  })

  it('renders the plans when the model claims to be empty but is not', () => {
    // The audit's defect in miniature: the attribute said one thing, the sentence another.
    // Here the renderer re-derives the state from the rows it is about to draw.
    const list = renderPlanLibrary(model({ state: 'empty', plans: [plan()] }))
    expect(list.dataset.state).toBe('ready')
    expect(list.querySelectorAll('.siyur-library__row')).toHaveLength(1)
    expect(list.textContent).not.toContain('You have not planned a day yet')
  })

  it('renders the empty state from a 200, and it does not read as an error', () => {
    const list = renderPlanLibrary(model({ state: 'ready', plans: [] }))
    expect(list.dataset.state).toBe('empty')
    expect(list.querySelector('.siyur-library__status')?.textContent).toContain(
      'You have not planned a day yet',
    )
    expect(list.querySelector('.siyur-library__failure')).toBeNull()
  })

  it('gives every library state its own headline, and none of them lies about a count', () => {
    const states: LibraryState[] = ['idle', 'loading', 'ready', 'empty', 'error']
    const headlines = states.map((state) => libraryHeadline(state, 7))
    expect(new Set(headlines).size).toBe(states.length)
    expect(libraryHeadline('ready', 7)).toContain('7')
    // Only `ready` may name a number: the other four have not been told one.
    for (const state of ['idle', 'loading', 'empty', 'error'] as LibraryState[]) {
      expect(libraryHeadline(state, 7)).not.toContain('7')
    }
  })
})

describe('a failed load is visible, and is never reported as an empty list', () => {
  it('states the failure in the list’s own body, as an alert', () => {
    const list = renderPlanLibrary(
      model({ state: 'error', plans: [], error: describeListFailure(new PlanRequestError(401)) }),
    )
    expect(list.dataset.state).toBe('error')
    const failure = list.querySelector('.siyur-library__failure')
    expect(failure?.getAttribute('role')).toBe('alert')
    expect(failure?.textContent).toContain('session has ended')
    expect(list.textContent).not.toContain('You have not planned a day yet')
  })

  it('never claims the plans are gone, whatever the reason', () => {
    const failures = [
      describeListFailure(new PlanRequestError(401)),
      describeListFailure(new PlanRequestError(404)),
      describeListFailure(new PlanRequestError(503)),
      describeListFailure(new ListResponseError('/plans')),
      describeListFailure(new TypeError('Failed to fetch')),
    ]
    for (const message of failures) {
      expect(message).toContain('nothing is listed')
      expect(message).not.toMatch(/have not planned|no plans yet/i)
    }
    expect(describeListFailure(new PlanRequestError(503))).toContain('503')
  })
})

describe('a superseded plan looks superseded, and keeps the id that replaced it', () => {
  it('carries the successor id as text and as an attribute', () => {
    const list = renderPlanLibrary(
      model({ plans: [plan({ state: 'superseded', superseded_by: PLAN_B })] }),
    )
    const row = list.querySelector<HTMLElement>('.siyur-library__row')
    expect(row?.dataset.planState).toBe('superseded')
    expect(row?.dataset.supersededBy).toBe(PLAN_B)
    const successor = row?.querySelector<HTMLElement>('.siyur-library__superseded')
    expect(successor?.textContent).toContain(PLAN_B)
    expect(successor?.dataset.supersededBy).toBe(PLAN_B)
  })

  it('is still openable — that is how a user reaches the plan that replaced it', () => {
    const onSelect = vi.fn()
    const list = renderPlanLibrary(
      model({ plans: [plan({ state: 'superseded', superseded_by: PLAN_B })] }),
      { onSelect },
    )
    list.querySelector<HTMLButtonElement>('.siyur-library__open')?.click()
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(PLAN_A)
  })
})

describe('a row says only what it was told', () => {
  it('reports an unreported feasibility as unreported, not as infeasible', () => {
    const list = renderPlanLibrary(model({ plans: [plan({ feasible: null })] }))
    const row = list.querySelector<HTMLElement>('.siyur-library__row')
    expect(row?.dataset.feasible).toBe('unknown')
    expect(row?.querySelector('.siyur-library__flag')?.textContent).toContain(
      'Feasibility was not reported',
    )
  })

  it('says nothing about the area when the area list was not loaded', () => {
    expect(areaLabel(plan(), null)).toBeNull()
    const list = renderPlanLibrary(model({ areas: null }))
    expect(list.querySelector('.siyur-library__area')).toBeNull()
  })

  it('distinguishes a named area, a drawn one, and one it cannot find', () => {
    const named = new Map([[AREA_A, area({ name: 'Old Town' })]])
    expect(areaLabel(plan(), named)).toBe('For Old Town')
    expect(areaLabel(plan(), new Map([[AREA_A, area()]]))).toBe(
      'For an area you delimited on the map',
    )
    expect(areaLabel(plan(), new Map())).toBe('For an area that is no longer listed')
  })

  it('puts third-party text on the page as text — never as markup', () => {
    const hostile = '<img src=x onerror="alert(1)">'
    const list = renderPlanLibrary(
      model({ areas: new Map([[AREA_A, area({ name: hostile })]]) }),
    )
    expect(list.querySelector('.siyur-library__area')?.textContent).toContain(hostile)
    expect(list.querySelector('img')).toBeNull()
  })

  it('renders the plan’s own area-local date, verbatim and unreformatted', () => {
    const list = renderPlanLibrary(model())
    expect(list.querySelector('.siyur-library__date')?.textContent).toBe('2026-09-01')
  })

  it('renders no UTC audit instant anywhere — recency comes from the server’s order', () => {
    // The two-clocks rule. `date` is the area-local day a person recognises; `created_at`
    // and `approved_at` are tz-aware UTC instants whose only legible form needs a timezone
    // conversion — i.e. `new Date` or `Intl.`, both banned across `src/plan/`. The ban is
    // not the inconvenience here, it is the point: a phone that has just landed is still
    // on its departure timezone.
    for (const stamp of ['2026-08-14T17:14:08.017944Z', '2026-08-14T17:14:08.017944+00:00']) {
      const list = renderPlanLibrary(
        model({ plans: [plan({ created_at: stamp, approved_at: stamp })] }),
      )
      const text = list.textContent ?? ''
      expect(text).toContain('2026-09-01') // the day the plan is FOR is shown
      expect(text).not.toContain('2026-08-14') // the instant it was WRITTEN is not
      expect(text).not.toContain('17:14')
      expect(text).not.toContain('+00:00')
    }
    const list = renderPlanLibrary(model())
    // Nor smuggled into an attribute, where it would render as a tooltip or be read back.
    expect(list.querySelector('[data-created-at]')).toBeNull()
    expect(list.querySelector('[data-approved-at]')).toBeNull()
  })

  it('carries either UTC spelling through unchanged, and sniffs for neither', () => {
    // Both forms, because **the client must not care which it gets**. Measured on this
    // stack: pydantic 2.13.4 renders a tz-aware UTC `datetime` as `…Z`, and the running
    // API returns `"created_at": "2026-08-14T17:14:08.017944Z"` on `GET /plans` — so `Z`
    // is what ships today. `+00:00` is the same instant and a serialiser change (or a
    // second endpoint) could produce it tomorrow. A client that pattern-matched either
    // would be encoding one library's formatting as a fact about a column, which is why
    // these strings are passed through opaquely and rendered nowhere.
    for (const stamp of ['2026-08-14T17:14:08.017944Z', '2026-08-14T17:14:08.017944+00:00']) {
      const row = sanitisePlanSummary(wirePlan({ created_at: stamp, approved_at: null }))
      expect(row?.created_at).toBe(stamp)
      expect(row?.approved_at).toBeNull()
    }
  })

  it('never turns a null researched_at into "not researched"', () => {
    // `api/areas.py:474` short-circuits a covered area to a reuse hint, which never stamps
    // `researched_at` — so a fully-researched area is null here (live: 14 areas, 3
    // stamped). Rendering that as a negative would be the third instance of one shape in
    // this codebase: a truthful field answering a question it was not asked.
    const areas = new Map([[AREA_A, area({ name: 'Old Town', researched_at: null })]])
    const list = renderPlanLibrary(model({ areas }))
    const text = list.textContent ?? ''
    expect(text).toContain('For Old Town')
    expect(text).not.toMatch(/not researched|never researched|no research|unresearched/i)
    // The same must hold when the field IS set: still no claim either way, just the area.
    const stamped = new Map([
      [AREA_A, area({ name: 'Old Town', researched_at: '2026-08-14T21:10:42+00:00' })],
    ])
    expect(renderPlanLibrary(model({ areas: stamped })).textContent).not.toMatch(
      /not researched|never researched/i,
    )
  })

  it('states a missing date rather than rendering a blank that reads as a glitch', () => {
    const list = renderPlanLibrary(model({ plans: [plan({ date: null })] }))
    expect(list.querySelector('.siyur-library__date')?.textContent).toBe('Date not recorded')
  })
})

/* --------------------------------------------------------------- surface --- */

describe('the mounted surface loads on demand and reports what happened', () => {
  it('fetches nothing until the list is opened', async () => {
    const host = document.createElement('div')
    const loadPlans = vi.fn(async () => [plan()])
    const library = mountPlanLibrary(host, { loadPlans })
    expect(loadPlans).not.toHaveBeenCalled()
    expect(library.element.dataset.state).toBe('idle')

    library.setOpen(true)
    await vi.waitFor(() => expect(library.element.dataset.state).toBe('ready'))
    expect(loadPlans).toHaveBeenCalledTimes(1)
    expect(library.element.querySelectorAll('.siyur-library__row')).toHaveLength(1)
  })

  it('renders a plan list even when the area list fails', async () => {
    const host = document.createElement('div')
    const library = mountPlanLibrary(host, {
      loadPlans: async () => [plan()],
      loadAreas: async () => {
        throw new PlanRequestError(500)
      },
    })
    library.setOpen(true)
    await vi.waitFor(() => expect(library.element.dataset.state).toBe('ready'))
    // The plans survived a broken courtesy read; the rows simply say nothing about areas.
    expect(library.element.querySelectorAll('.siyur-library__row')).toHaveLength(1)
    expect(library.current.areas).toBeNull()
    expect(library.element.querySelector('.siyur-library__area')).toBeNull()
  })

  it('lands a failed plan read on the surface as an error, not as an empty list', async () => {
    const host = document.createElement('div')
    const library = mountPlanLibrary(host, {
      loadPlans: async () => {
        throw new ListResponseError('/plans')
      },
    })
    library.setOpen(true)
    await vi.waitFor(() => expect(library.element.dataset.state).toBe('error'))
    expect(library.element.querySelector('.siyur-library__failure')).not.toBeNull()
    expect(library.element.textContent).not.toContain('You have not planned a day yet')
  })

  it('marks the row whose plan is open on the panel below', async () => {
    const host = document.createElement('div')
    const library = mountPlanLibrary(host, {
      loadPlans: async () => [plan(), plan({ plan_id: PLAN_B })],
    })
    library.setOpen(true)
    await vi.waitFor(() => expect(library.element.dataset.state).toBe('ready'))
    library.select(PLAN_B)
    const rows = [...library.element.querySelectorAll<HTMLElement>('.siyur-library__row')]
    expect(rows.map((row) => row.dataset.selected)).toEqual(['false', 'true'])
  })
})

/* ---------------------------------------------------------- the structure --- */

describe('the no-clock rule covers the modules Phase A added', () => {
  it('scans src/plan recursively and finds the new list modules inside it', async () => {
    // The structural half of `plan.test.ts`'s own scan, asserted from this side: the new
    // modules must be *inside* the directory that scan walks, or the rule does not reach
    // them. Comments are stripped first — this file's prose names the calls it forbids.
    const { readdirSync, readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const dir = join(process.cwd(), 'src', 'plan')
    const walk = (from: string): string[] =>
      readdirSync(from, { withFileTypes: true }).flatMap((entry) => {
        const full = join(from, entry.name)
        if (entry.isDirectory()) return walk(full)
        return entry.name.endsWith('.ts') ? [full] : []
      })
    const files = walk(dir)
    const names = files.map((file) => file.slice(dir.length + 1))
    expect(names).toContain('list.ts')
    expect(names).toContain('library.ts')

    const forbidden =
      /\bnew Date\b|\bDate\.now\b|\bDate\.parse\b|\bDate\.UTC\b|\bIntl\.|\bTemporal\.|toISOString|toLocale[A-Za-z]*|getTimezoneOffset/
    const offenders: string[] = []
    for (const file of files) {
      const code = readFileSync(file, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/\/\/.*$/gm, '')
      if (file.endsWith('library.ts')) expect(code).toMatch(/renderPlanLibrary/) // strip kept the code
      for (const [index, l] of code.split('\n').entries()) {
        if (forbidden.test(l)) offenders.push(`${file.slice(dir.length + 1)}:${index + 1} ${l.trim()}`)
      }
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })
})

/* ------------------------------------------------------------- the CSS --- */

/**
 * The mobile floors, asserted against the stylesheet that actually ships.
 *
 * jsdom performs no layout, so a DOM test cannot measure a width — and the audit's lesson
 * is that a comment claiming a property is not a measurement. What *can* be checked
 * mechanically is the cause: `plan.css` fails the spec because of the numbers written in
 * it, and these are the numbers written in `library.css`. The rendered widths were checked
 * separately in Chromium at 390 × 844 and 375 × 667.
 */
describe('library.css meets the mobile spec it was written against', () => {
  const readCss = async (): Promise<string> => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    return readFileSync(join(process.cwd(), 'src', 'library.css'), 'utf8')
  }
  /** Declarations only — the comments in this file discuss the very rules it forbids. */
  const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, '')

  it('sets no font-size below 14px (ux-handoff § Typography, 375–430px)', async () => {
    const css = strip(await readCss())
    const sizes = [...css.matchAll(/font-size:\s*([\d.]+)px/g)].map((m) => Number(m[1]))
    expect(sizes.length).toBeGreaterThan(10) // the scan found rules, not nothing
    expect(sizes.filter((size) => size < 14)).toEqual([])
  })

  it('gives every control a 44px tap target', async () => {
    const css = strip(await readCss())
    const controls = [
      '.siyur-library__toggle',
      '.siyur-library__open',
      '.siyur-disambiguation__pick',
      '.siyur-disambiguation__dismiss',
    ]
    for (const selector of controls) {
      const rule = new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? ''
      const min = /min-block-size:\s*([\d.]+)px/.exec(rule)?.[1]
      expect(min, `${selector} declares no min-block-size`).toBeDefined()
      expect(Number(min), selector).toBeGreaterThanOrEqual(44)
    }
  })

  it('uses logical properties only, so the deferred RTL pass needs no new rules', async () => {
    const css = strip(await readCss())
    const physical =
      /(?:^|[\s;{])(?:margin|padding|border)-(?:left|right)\b|(?:^|[\s;{])(?:left|right):|text-align:\s*(?:left|right)\b/g
    expect([...css.matchAll(physical)].map((m) => m[0].trim())).toEqual([])
  })

  it('layers the disambiguation picker above the plan panel that would cover it', async () => {
    // Measured, then pinned: inside the bottom sheet (z-index 2) the plan panel covered
    // the picker's own heading at 390 × 844 — the audit's occlusion finding happening to a
    // brand-new surface. The relationship is cross-file, so the comparison is too.
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const planCss = readFileSync(join(process.cwd(), 'src', 'plan.css'), 'utf8')
    const panelZ = Number(/\.siyur-plan-panel\s*\{[^}]*z-index:\s*(\d+)/.exec(planCss)?.[1])
    const hostZ = Number(
      /\.siyur-disambiguation-host\s*\{[^}]*z-index:\s*(\d+)/.exec(strip(await readCss()))?.[1],
    )
    expect(panelZ).toBeGreaterThan(0) // the scan found the panel's rule, not nothing
    expect(hostZ).toBeGreaterThan(panelZ)

    // And it clears the bottom edge, where the ODbL attribution control sits. Covering
    // that is a licence failure, not a layout one (Constitution Article V).
    const bottom = /\.siyur-disambiguation-host\s*\{[^}]*inset-block-end:\s*([\d.]+)px/.exec(
      strip(await readCss()),
    )?.[1]
    expect(Number(bottom)).toBeGreaterThanOrEqual(24)
  })

  it('lets every long token wrap, so a 36-character UUID cannot widen a 375px viewport', async () => {
    const css = strip(await readCss())
    // The three places a plan/area id or arbitrary user text reaches the page.
    for (const selector of ['.siyur-library__superseded', '.siyur-disambiguation__pick']) {
      const rule = new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? ''
      expect(rule, selector).toMatch(/overflow-wrap:\s*anywhere/)
    }
    // And nothing declares a fixed inline size that a 375px viewport could not hold.
    const widths = [...css.matchAll(/(?:inline-size|width):\s*([\d.]+)px/g)].map((m) =>
      Number(m[1]),
    )
    expect(widths.filter((width) => width > 320)).toEqual([])
  })
})
