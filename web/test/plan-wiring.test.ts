/**
 * The plan surfaces, **reached through the app shell** — `src/main.ts`, not the package.
 *
 * `web/test/plan.test.ts` proves `src/plan/` behaves; this file proves a person can get
 * to it. The two are not the same claim, and the gap between them is what "tons of code
 * without any ability to verify the work" describes: every module here was already built,
 * merged and unit-tested while `main.ts` imported none of it.
 *
 * So the assertions are all about *reachability and wiring*:
 *
 * - the form and the review panel mount in the running shell;
 * - delimiting an area binds that area's id into the request — the plan is for the area
 *   on screen, never for whatever was typed last;
 * - a submitted plan streams into the panel, with the commons records the map fetched
 *   supplying the stamped values the stops render (the `onSites` → `sites` wiring, which
 *   nothing in the package can test because the package never sees `SitesLayer`);
 * - the gate: an infeasible day's approve button fires **nothing** when clicked, and a
 *   feasible day's does, and the panel then shows the state the server reported.
 *
 * Two structural guards, both for silent failures:
 *
 * - **every client endpoint constant is in the dev proxy list.** A path Vite does not
 *   proxy is answered by `navigateFallback` with the app shell — HTML, status `200` —
 *   so the failure surfaces as a JSON parse error somewhere else entirely, if at all;
 * - **no `Date`/`Intl` in `src/main.ts`.** `plan.test.ts`'s scan walks `src/plan/` only,
 *   and the wiring layer handles the same wall-clock strings.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const bounds = {
  getWest: () => 28.216,
  getSouth: () => 36.44,
  getEast: () => 28.232,
  getNorth: () => 36.451,
}

vi.mock('virtual:pwa-register', () => ({ registerSW: vi.fn() }))

vi.mock('maplibre-gl', () => {
  class FakeMap {
    constructor(public opts: Record<string, unknown>) {}
    getBounds() {
      return bounds
    }
    getContainer() {
      return this.opts.container
    }
    on() {}
    off() {}
    addControl() {}
    fitBounds() {}
    setStyle() {}
    remove() {}
  }
  class FakeMarker {
    element: HTMLElement
    constructor(opts?: { element?: HTMLElement }) {
      this.element = opts?.element ?? document.createElement('div')
    }
    setLngLat() {
      return this
    }
    setPopup() {
      return this
    }
    addTo() {
      return this
    }
    remove() {}
  }
  class FakePopup {
    on() {
      return this
    }
    setDOMContent() {
      return this
    }
  }
  const addProtocol = vi.fn()
  const removeProtocol = vi.fn()
  return {
    default: { Map: FakeMap, Marker: FakeMarker, Popup: FakePopup, addProtocol, removeProtocol },
    Map: FakeMap,
    Marker: FakeMarker,
    Popup: FakePopup,
    addProtocol,
    removeProtocol,
  }
})

/* -------------------------------------------------------------- fixtures --- */

const OSM = { kind: 'osm', license: 'ODbL-1.0', attribution: '© OpenStreetMap contributors' }
const OVERTURE = { kind: 'overture', license: 'CDLA-Permissive-2.0' }

const SITE_A = '6f1c-uuid'
const SITE_B = 'c9d1-uuid'
const AREA_ID = 'area-1'
const PLAN_ID = '7be2-uuid'

const site = (id: string, name: string): unknown => ({
  id,
  names: { en: { value: name, source: OVERTURE } },
  location: { value: { type: 'Point', coordinates: [28.2247, 36.4443] }, source: OVERTURE },
  address: { value: '1 Ippoton', source: OSM },
  opening_hours: { value: 'Tu-Su 09:00-14:00', source: OSM },
})

const SITES = [site(SITE_A, 'Palace of the Grand Master'), site(SITE_B, 'Archaeological Museum')]

const AREA = {
  area_id: AREA_ID,
  polygon: {
    type: 'Polygon',
    coordinates: [
      [
        [28.216, 36.44],
        [28.232, 36.44],
        [28.232, 36.451],
        [28.216, 36.451],
        [28.216, 36.44],
      ],
    ],
  },
  coverage: {
    known_site_count: 2,
    covered: true,
    stalest_observed_at: null,
    refresh_available: true,
  },
}

/** `docs/data/itinerary.md` example 1, verbatim in shape. */
const ITINERARY = {
  id: PLAN_ID,
  area_id: AREA_ID,
  date: '2026-08-14',
  lang: 'en',
  schema_ver: 'ItineraryV1',
  budgets: { walking_m: 4000, hours: 4.0 },
  stops: [
    { site_id: SITE_A, order: 0, planned_start: '10:00', dwell_min: 60 },
    { site_id: SITE_B, order: 1, planned_start: '11:15', dwell_min: 45 },
  ],
  legs: [
    {
      id: 'leg-0',
      from_stop: 0,
      to_stop: 1,
      mode: 'walk',
      distance_m: 380,
      duration_s: 300,
      geometry: {
        type: 'LineString',
        coordinates: [
          [28.2247, 36.4443],
          [28.2242, 36.4446],
        ],
      },
      source: { ...OSM, id: 'valhalla:pedestrian' },
    },
  ],
  timeline: {
    entries: [
      { stop_order: 0, start: '10:00', duration_min: 60 },
      { leg_id: 'leg-0', start: '11:00', duration_min: 5 },
      { stop_order: 1, start: '11:15', duration_min: 45 },
    ],
  },
}

const VIOLATIONS = ['walking_m 4200 > budget 3000', 'stop 2 outside opening window Tu 09:00-14:00']

const planStream = (feasibility: string): string =>
  `event: status\ndata: {"phase":"load_sites","area_id":"${AREA_ID}","candidates":39}\n\n` +
  'event: status\ndata: {"phase":"propose_itinerary","tier":"opus","stops":2}\n\n' +
  `event: itinerary\ndata: ${JSON.stringify(ITINERARY)}\n\n` +
  `event: feasibility\ndata: ${feasibility}\n\n` +
  `event: done\ndata: {"plan_id":"${PLAN_ID}","state":"proposed"}\n\n`

const INFEASIBLE = `{"ok":false,"violations":${JSON.stringify(VIOLATIONS)}}`
const FEASIBLE = '{"ok":true,"violations":[],"checked_at":"2026-08-11T09:00:00Z"}'

/* --------------------------------------------------------------- backend --- */

interface Call {
  readonly url: string
  readonly method: string
}

/** One in-memory backend over the four contracts the shell talks to. */
const backend = (feasibility: string): { fetch: typeof fetch; calls: Call[] } => {
  const calls: Call[] = []
  let approved = false
  const impl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input)
    calls.push({ url, method: init?.method ?? 'GET' })

    if (url === '/plans' && init?.method === 'POST') {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(planStream(feasibility)))
          controller.close()
        },
      })
      return new Response(body, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith('/approve')) {
      approved = true
      return Response.json({
        plan_id: PLAN_ID,
        state: 'approved',
        approved_at: '2026-08-11T09:05:00Z',
      })
    }
    if (url.startsWith('/plans/')) {
      return Response.json({
        plan: ITINERARY,
        feasibility: JSON.parse(feasibility) as unknown,
        approval: {
          state: approved ? 'approved' : 'proposed',
          approved_at: approved ? '2026-08-11T09:05:00Z' : null,
          superseded_by: null,
        },
        attribution: ['© OpenStreetMap contributors'],
      })
    }
    if (url.startsWith('/areas')) return Response.json(AREA)
    // `GET /sites` — the commons the map has, and the source of every chip below.
    return Response.json({ sites: SITES, attribution: ['© OpenStreetMap contributors'] })
  }
  return { fetch: vi.fn(impl) as unknown as typeof fetch, calls }
}

/** Let the fetch + stream microtasks and their reader turns settle. */
const settle = async (turns = 12): Promise<void> => {
  for (let i = 0; i < turns; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

const query = <T extends Element>(selector: string): T | null => document.querySelector<T>(selector)
const queryAll = <T extends Element>(selector: string): T[] =>
  Array.from(document.querySelectorAll<T>(selector))

/** Delimit the viewport, fill the request, submit — the whole user path. */
const planTheDay = async (): Promise<void> => {
  query<HTMLButtonElement>('[data-action="use-viewport"]')!.click()
  await settle()
  // The one field with no default, deliberately: see `src/plan/form.ts`.
  query<HTMLInputElement>('.siyur-plan-form [name="date"]')!.value = '2026-08-14'
  query<HTMLButtonElement>('.siyur-plan-form__submit')!.click()
  await settle()
}

const mountShell = async (feasibility: string): Promise<Call[]> => {
  document.body.replaceChildren()
  const map = document.createElement('div')
  map.id = 'map'
  document.body.append(map)
  const { fetch: fetchImpl, calls } = backend(feasibility)
  vi.stubGlobal('fetch', fetchImpl)
  vi.resetModules()
  await import('../src/main')
  await settle()
  return calls
}

/* ----------------------------------------------------------- reachability --- */

describe('the plan surfaces are reachable in the app shell', () => {
  let calls: Call[]
  beforeEach(async () => {
    calls = await mountShell(INFEASIBLE)
  })

  it('mounts the request form and the review panel beside the map', () => {
    expect(query('.siyur-plan-panel')).not.toBeNull()
    expect(query('.siyur-plan-form')).not.toBeNull()
    expect(query('.siyur-plan')).not.toBeNull()
    // Nothing proposed yet — and the panel says so rather than rendering blank.
    expect(query('.siyur-plan__empty')?.textContent).toMatch(/No day has been proposed yet/)
    expect(query<HTMLButtonElement>('.siyur-plan-approve__button')?.disabled).toBe(true)
  })

  it('ships the date input with no value, and refuses a submit without one', async () => {
    const date = query<HTMLInputElement>('.siyur-plan-form [name="date"]')
    expect(date?.value).toBe('')

    query<HTMLButtonElement>('.siyur-plan-form__submit')!.click()
    await settle(2)
    expect(query('.siyur-plan-form__problem[data-field="date"]')).not.toBeNull()
    // …and nothing was sent.
    expect(calls.filter((call) => call.url === '/plans')).toEqual([])
  })

  it('binds the resolved area into the request — the plan is for the area on screen', async () => {
    expect(query<HTMLInputElement>('.siyur-plan-form [name="area_id"]')?.value).toBe('')
    query<HTMLButtonElement>('[data-action="use-viewport"]')!.click()
    await settle()
    expect(query<HTMLInputElement>('.siyur-plan-form [name="area_id"]')?.value).toBe(AREA_ID)
    // Read-only: an area id is a UUID, not something a person types.
    expect(query<HTMLInputElement>('.siyur-plan-form [name="area_id"]')?.readOnly).toBe(true)
  })
})

/* ------------------------------------------------- a streamed proposal --- */

describe('a submitted plan streams into the review panel', () => {
  let calls: Call[]
  beforeEach(async () => {
    calls = await mountShell(INFEASIBLE)
    await planTheDay()
  })

  it('POSTs the form’s own values to /plans and renders what came back', () => {
    expect(calls.some((call) => call.url === '/plans' && call.method === 'POST')).toBe(true)
    expect(query<HTMLElement>('.siyur-plan')?.dataset.planId).toBe(PLAN_ID)
    expect(query('.siyur-plan__date')?.textContent).toBe('2026-08-14')
  })

  it('renders the day in stop order with the wall clock visible on every stop', () => {
    const stops = queryAll<HTMLElement>('.siyur-plan-stop')
    expect(stops.map((stop) => stop.dataset.stopOrder)).toEqual(['0', '1'])
    // Verbatim, area-local. `11:15` means quarter past eleven where the traveller is.
    expect(stops[0]?.querySelector('.siyur-plan-stop__when')?.textContent).toBe('10:00 · 60 min')
    expect(stops[1]?.querySelector('.siyur-plan-stop__when')?.textContent).toBe('11:15 · 45 min')
  })

  it('shows each stop’s stamped values from the commons the map fetched', () => {
    // This is the `SitesLayer.onSites` → `PlanReviewModel.sites` wiring: without it every
    // stop renders "not loaded on this device" and no chip appears at all.
    const first = query<HTMLElement>('.siyur-plan-stop[data-stop-order="0"]')
    expect(first?.dataset.place).toBe('stamped')
    expect(first?.querySelector('.siyur-plan-stop__name .siyur-value__text')?.textContent).toBe(
      'Palace of the Grand Master',
    )
    // ADR-0019 co-presence: the chip is inside the same element as its value.
    const chip = first?.querySelector<HTMLElement>('.siyur-plan-stop__name .siyur-chip')
    expect(chip?.dataset.sourceKind).toBe('overture')
    expect(chip?.dataset.bundleable).toBe('true')
    expect(chip?.textContent).toContain('CDLA-Permissive-2.0')
  })

  it('names the violations verbatim and captions the verdict (ADR-0030 A1)', () => {
    expect(queryAll('.siyur-plan-violation').map((li) => li.textContent)).toEqual(VIOLATIONS)
    expect(query<HTMLElement>('.siyur-plan-feasibility')?.dataset.ok).toBe('false')
    expect(query<HTMLElement>('.siyur-plan-verdict-credit')?.dataset.verdictSource).toBe(
      'server-computed',
    )
    expect(query<HTMLElement>('.siyur-plan-credit')?.dataset.planSource).toBe('user-owned')
  })

  it('leaves the approve button disabled AND unattached while infeasible', async () => {
    const button = query<HTMLButtonElement>('.siyur-plan-approve__button')!
    expect(button.disabled).toBe(true)
    expect(button.dataset.approvable).toBe('false')
    expect(query('.siyur-plan-approve__blocked')?.textContent).toMatch(/2 conflicts below/)

    // A DOM tamper clearing `disabled` still fires nothing: there is no listener.
    button.disabled = false
    button.click()
    await settle()
    expect(calls.filter((call) => call.url.endsWith('/approve'))).toEqual([])
  })
})

/* --------------------------------------------------------------- the gate --- */

describe('the approve gate, end to end in the shell', () => {
  let calls: Call[]
  beforeEach(async () => {
    calls = await mountShell(FEASIBLE)
    await planTheDay()
  })

  it('reads the plan back so the aggregate credit is on screen', () => {
    expect(calls.some((call) => call.url === `/plans/${PLAN_ID}` && call.method === 'GET')).toBe(
      true,
    )
    const credit = query<HTMLElement>('.siyur-plan-attribution')
    expect(credit?.dataset.creditScope).toBe('aggregate')
    expect(credit?.textContent).toBe('© OpenStreetMap contributors')
  })

  it('approves on the click and shows the state the server reported', async () => {
    const button = query<HTMLButtonElement>('.siyur-plan-approve__button')!
    expect(button.disabled).toBe(false)
    expect(button.dataset.approvable).toBe('true')

    button.click()
    await settle()

    expect(calls.filter((call) => call.url === `/plans/${PLAN_ID}/approve`)).toEqual([
      { url: `/plans/${PLAN_ID}/approve`, method: 'POST' },
    ])
    expect(query<HTMLElement>('.siyur-plan')?.dataset.planState).toBe('approved')
    // Approved is not approvable a second time, and the panel says which it is.
    expect(query('.siyur-plan-approve__blocked')?.textContent).toMatch(/already approved/)
  })
})

/* --------------------------------------------------- structural guards --- */

describe('the wiring layer’s structural guards', () => {
  it('proxies every endpoint the client calls — an unproxied path 200s as HTML', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const config = readFileSync(join(process.cwd(), 'vite.config.ts'), 'utf8')

    const proxied = /\[((?:\s*'\/[a-z]+',?)+)\s*\]\.map\(\(path\)/.exec(config)?.[1]
    expect(proxied, 'the proxy path list in vite.config.ts moved').toBeDefined()
    const paths = new Set(Array.from(proxied!.matchAll(/'([^']+)'/g), (match) => match[1]))

    // The constants themselves, so a new endpoint cannot be added without a proxy entry.
    const { DEFAULT_AREAS_ENDPOINT, DEFAULT_SITES_ENDPOINT } = await import('../src/map')
    const { DEFAULT_PLANS_ENDPOINT } = await import('../src/plan')
    for (const endpoint of [
      DEFAULT_AREAS_ENDPOINT,
      DEFAULT_SITES_ENDPOINT,
      DEFAULT_PLANS_ENDPOINT,
    ]) {
      expect(paths, `${endpoint} is not proxied by the dev server`).toContain(endpoint)
    }
  })

  it('constructs no Date and reads no locale in src/main.ts', async () => {
    // `plan.test.ts`'s scan walks `src/plan/` only. `main.ts` moves the same area-local
    // wall-clock strings between the form, the stream and the panel, so the identical
    // rule applies to it — and a violation here would be outside every existing scan.
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const code = readFileSync(join(process.cwd(), 'src', 'main.ts'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/.*$/gm, '')
    // The strip must not have eaten the code: if it did, the scan passes vacuously.
    expect(code).toMatch(/mountPlanReview/)

    const forbidden =
      /\bnew Date\b|\bDate\.now\b|\bDate\.parse\b|\bDate\.UTC\b|\bIntl\.|\bTemporal\.|toISOString|toLocale[A-Za-z]*|getTimezoneOffset/
    const offenders = code
      .split('\n')
      .map((line, index) => ({ line: line.trim(), index }))
      .filter(({ line }) => forbidden.test(line))
      .map(({ line, index }) => `main.ts:${index + 1} ${line}`)
    expect(offenders, offenders.join('\n')).toEqual([])
  })
})
