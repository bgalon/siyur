/**
 * T028 — the plan review UI.
 *
 * `api/plans.py` does not exist yet; the CONTRACT
 * (`specs/002-plan-compile-offline/contracts/plans.md`) is the interface, so the frame
 * sequence below is its own worked example. No network, no running API, no clock.
 *
 * The load-bearing assertions — each named with the bug it catches:
 *
 * - **a value with no `source` renders nothing at all** (FR-008 / SC-004): smuggling an
 *   unstamped name past the funnel must leave the name off the screen entirely;
 * - **approve is unavailable while infeasible** (FR-005): the server answers `409`, so a
 *   button that fires the request is a button that lies — it is disabled *and* unwired;
 * - **the date is never defaulted client-side**: a UTC "today" stamps the wrong calendar
 *   day either side of the date line, and every stop is then checked against the wrong
 *   weekday. Asserted twice: on the mounted input, and structurally by a source scan
 *   proving nothing in `src/plan/` touches `Date` at all;
 * - **feasibility fails closed**: an absent or unreadable verdict blocks approval rather
 *   than reading as "no violations, so fine";
 * - **violations are named, never collapsed** to the word "infeasible".
 */

import { describe, expect, it, vi } from 'vitest'

import {
  EMPTY_PLAN_MODEL,
  PlanApprovalConflictError,
  PlanRequestError,
  PlanReviewSurface,
  approvability,
  approvePlan,
  mountPlanForm,
  readItineraryFrame,
  renderFeasibility,
  renderPlanPanel,
  sanitiseApproval,
  sanitiseFeasibility,
  sanitisePlanDetail,
  streamPlan,
  validatePlanRequest,
  type Feasibility,
  type PlanReviewModel,
  type ProposeRequest,
} from '../src/plan'
import type { SiteRecordV1 } from '../src/map/types'

/* -------------------------------------------------------------- fixtures --- */

const OSM = { kind: 'osm', license: 'ODbL-1.0', attribution: '© OpenStreetMap contributors' }
const OVERTURE = { kind: 'overture', license: 'CDLA-Permissive-2.0' }

const SITE_A = '6f1c-uuid'
const SITE_B = 'c9d1-uuid'

const site = (id: string, name: string): SiteRecordV1 => ({
  id,
  names: { en: { value: name, source: OVERTURE } },
  location: { value: { type: 'Point', coordinates: [28.2247, 36.4443] }, source: OVERTURE },
  address: { value: '1 Ippoton', source: OSM },
  opening_hours: { value: 'Tu-Su 09:00-14:00', source: OSM },
})

const SITES = new Map<string, SiteRecordV1>([
  [SITE_A, site(SITE_A, 'Palace of the Grand Master')],
  [SITE_B, site(SITE_B, 'Archaeological Museum')],
])

/** `docs/data/itinerary.md` example 1, verbatim in shape. */
const ITINERARY = {
  id: '7be2-uuid',
  area_id: '3a11-uuid',
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
          [28.2238, 36.4447],
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

/** The contract's two named violations. */
const VIOLATIONS = [
  'walking_m 4200 > budget 3000',
  'stop 2 outside opening window Tu 09:00-14:00',
]

const CONTRACT_STREAM =
  'event: status\ndata: {"phase":"load_sites","area_id":"3a11-uuid","candidates":39}\n\n' +
  'event: status\ndata: {"phase":"propose_itinerary","tier":"opus","stops":5}\n\n' +
  'event: status\ndata: {"phase":"route","provider":"valhalla","legs":4,' +
  `"excluded":[{"site_id":"${SITE_B}","reason":"unroutable"}]}\n\n` +
  `event: itinerary\ndata: ${JSON.stringify(ITINERARY)}\n\n` +
  `event: feasibility\ndata: {"ok":false,"violations":${JSON.stringify(VIOLATIONS)}}\n\n` +
  'event: done\ndata: {"plan_id":"7be2-uuid","state":"proposed"}\n\n'

const REQUEST: ProposeRequest = {
  area_id: '3a11-uuid',
  budgets: { walking_m: 4000, hours: 4 },
  interests: 'art and coffee, nothing crowded',
  date: '2026-08-14',
  day_start: '10:00',
  lang: 'en',
}

/** A `fetch` answering `200 text/event-stream` with `chunks` in order. */
const streamingFetch = (chunks: readonly string[]): typeof fetch =>
  vi.fn(async () => {
    const encoder = new TextEncoder()
    return new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
          controller.close()
        },
      }),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )
  }) as unknown as typeof fetch

const jsonFetch = (status: number, body: unknown): typeof fetch =>
  vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch

const CHECKED_AT = '2026-08-07T09:12:00Z'
const FEASIBLE: Feasibility = { ok: true, violations: [], checked_at: CHECKED_AT, readable: true }
const INFEASIBLE: Feasibility = {
  ok: false,
  violations: VIOLATIONS,
  checked_at: CHECKED_AT,
  readable: true,
}

/** Let every already-queued microtask (and the `setTimeout(0)` after them) run. */
const flush = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0))

const model = (patch: Partial<PlanReviewModel> = {}): PlanReviewModel => ({
  ...EMPTY_PLAN_MODEL,
  planId: '7be2-uuid',
  itinerary: readItineraryFrame(ITINERARY),
  feasibility: FEASIBLE,
  approval: { state: 'proposed', approved_at: null, superseded_by: null },
  sites: SITES,
  ...patch,
})

/* ------------------------------------------------------------ the request --- */

describe('the request form never defaults the date (contracts/plans.md § POST /plans)', () => {
  it('mounts the date input with NO value — not from a clock, not from anything', () => {
    // A UTC-truncated "today" stamps the wrong calendar day either side of the date
    // line; the browser's own local day is not the AREA's day either. Both are the same
    // bug, and the only correct client behaviour is to have no opinion.
    const host = document.createElement('div')
    mountPlanForm(host)
    const date = host.querySelector<HTMLInputElement>('[name="date"]')
    expect(date).not.toBeNull()
    expect(date?.value).toBe('')
    expect(date?.getAttribute('value')).toBeNull()
    expect(date?.required).toBe(true)
  })

  it('refuses an empty date by name, and says why it matters', () => {
    const result = validatePlanRequest({ ...REQUEST, hours: '4', walking_m: '4000', date: '' })
    expect(result.ok).toBe(false)
    if (result.ok) throw new Error('unreachable')
    expect(result.problems.map((p) => p.field)).toContain('date')
    expect(result.problems.find((p) => p.field === 'date')?.message).toMatch(/opening hours/i)
  })

  it('refuses a date that is not YYYY-MM-DD rather than coercing it', () => {
    const result = validatePlanRequest({
      ...REQUEST,
      hours: '4',
      walking_m: '4000',
      date: '14/08/2026',
    })
    expect(result.ok).toBe(false)
  })

  it('passes a valid date through verbatim, with the budgets as numbers', () => {
    const result = validatePlanRequest({
      area_id: '3a11-uuid',
      hours: '4',
      walking_m: '4000',
      interests: '  art and coffee  ',
      date: '2026-08-14',
      day_start: '10:00',
    })
    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error('unreachable')
    expect(result.request).toEqual({
      area_id: '3a11-uuid',
      budgets: { walking_m: 4000, hours: 4 },
      interests: 'art and coffee',
      date: '2026-08-14',
      day_start: '10:00',
      lang: 'en',
    })
  })

  it('mirrors the contract’s 422 conditions: non-positive budgets, unparseable day_start', () => {
    const base = { area_id: 'a', interests: '', date: '2026-08-14', day_start: '10:00' }
    expect(validatePlanRequest({ ...base, hours: '0', walking_m: '4000' }).ok).toBe(false)
    expect(validatePlanRequest({ ...base, hours: '4', walking_m: '-1' }).ok).toBe(false)
    expect(validatePlanRequest({ ...base, hours: '4', walking_m: '' }).ok).toBe(false)
    const bad = { ...base, hours: '4', walking_m: '4000' }
    expect(validatePlanRequest({ ...bad, day_start: 'noon' }).ok).toBe(false)
    // …and an area the user has not chosen yet.
    expect(validatePlanRequest({ ...bad, area_id: '  ' }).ok).toBe(false)
  })

  it('does not submit an invalid form, and lists the problem against its field', async () => {
    const onSubmit = vi.fn()
    const host = document.createElement('div')
    const form = mountPlanForm(host, { onSubmit })
    form.setArea('3a11-uuid') // …but no date: the one field with no default.

    const result = await form.submit()
    expect(result.ok).toBe(false)
    expect(onSubmit).not.toHaveBeenCalled()
    const problem = host.querySelector<HTMLElement>('.siyur-plan-form__problem')
    expect(problem?.dataset.field).toBe('date')
  })

  it('submits the validated body once the user supplies the date', async () => {
    const onSubmit = vi.fn()
    const host = document.createElement('div')
    const form = mountPlanForm(host, { onSubmit })
    form.setArea('3a11-uuid')
    host.querySelector<HTMLInputElement>('[name="date"]')!.value = '2026-08-14'
    host.querySelector<HTMLTextAreaElement>('[name="interests"]')!.value = 'art and coffee'

    await form.submit()
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      area_id: '3a11-uuid',
      date: '2026-08-14',
      day_start: '10:00',
      budgets: { walking_m: 4000, hours: 4 },
    })
  })

  it('no module in src/plan constructs a Date, converts a zone, or reads a clock', async () => {
    // The structural half of the rule above. A client-side date default, or a
    // `toLocaleTimeString()` on a planned time, would land here even if it slipped past
    // every DOM assertion. Comments are stripped first — this file's own prose names the
    // very calls it forbids.
    //
    // `Intl.` and `Date.parse` are in the pattern because the surviving mutation was
    // `new Intl.DateTimeFormat(undefined, { timeStyle: 'short' })
    //    .format(Date.parse(checkedAt))`: every DOM assertion still passed while the
    // panel printed a **device-timezone** time beside an area-local day — the exact
    // confusion this rule exists to prevent, using neither `new Date` nor `toLocale*`.
    // `Temporal.` is here before it ships, for the same reason.
    const { readdirSync, readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const dir = join(process.cwd(), 'src', 'plan')

    // Recursive: a subdirectory was outside the old scan entirely, so the cheapest way
    // past it was to put the offending module one level down.
    const walk = (from: string): string[] =>
      readdirSync(from, { withFileTypes: true }).flatMap((entry) => {
        const full = join(from, entry.name)
        if (entry.isDirectory()) return walk(full)
        return entry.name.endsWith('.ts') ? [full] : []
      })
    const files = walk(dir)
    expect(files.length).toBeGreaterThan(3) // the walk found something, not nothing

    const forbidden =
      /\bnew Date\b|\bDate\.now\b|\bDate\.parse\b|\bDate\.UTC\b|\bIntl\.|\bTemporal\.|toISOString|toLocale[A-Za-z]*|getTimezoneOffset/
    const offenders: string[] = []
    for (const file of files) {
      const code = readFileSync(file, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/\/\/.*$/gm, '')
      // The strip must not have eaten the code: if it did, the scan passes vacuously.
      if (file.endsWith('render.ts')) expect(code).toMatch(/renderSourcedValue/)
      const name = file.slice(dir.length + 1)
      for (const [index, line] of code.split('\n').entries()) {
        if (forbidden.test(line)) offenders.push(`${name}:${index + 1} ${line.trim()}`)
      }
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })
})

/* --------------------------------------------------------- the itinerary --- */

describe('the itinerary panel renders the proposed day', () => {
  it('renders stops and legs in timeline order, addressed by stop_order and leg_id', () => {
    const panel = renderPlanPanel(model())
    const rows = [...panel.querySelectorAll('.siyur-plan-timeline > *')] as HTMLElement[]
    expect(rows.map((r) => r.dataset.stopOrder ?? r.dataset.legId)).toEqual(['0', 'leg-0', '1'])
  })

  it('renders planned times as area-local wall clock, verbatim', () => {
    // `10:00` means ten in the morning where the traveller will be standing. A device on
    // another zone must show the same string — so no `Date`, no locale formatting.
    const panel = renderPlanPanel(model())
    const when = [...panel.querySelectorAll('.siyur-plan-stop__when')].map((n) => n.textContent)
    expect(when).toEqual(['10:00 · 60 min', '11:15 · 45 min'])
    expect(panel.querySelector('.siyur-plan__date')?.textContent).toBe('2026-08-14')
  })

  it('falls back to stop order + the joining leg when the timeline is empty', () => {
    // The itinerary card's own INFEASIBLE example carries `timeline.entries: []` and the
    // 4200 m leg that caused the violation — the one thing the reviewer must see.
    const flat = renderPlanPanel(
      model({ itinerary: readItineraryFrame({ ...ITINERARY, timeline: { entries: [] } }) }),
    )
    const rows = [...flat.querySelectorAll('.siyur-plan-timeline > *')] as HTMLElement[]
    expect(rows.map((r) => r.dataset.stopOrder ?? r.dataset.legId)).toEqual(['0', 'leg-0', '1'])
    // …and the times fall back to the stop's own planned window, still verbatim.
    expect(flat.querySelector('.siyur-plan-stop__when')?.textContent).toBe('10:00 · 60 min')
  })

  it('states that times, order and budgets are the user’s own plan, not sourced data', () => {
    const credit = renderPlanPanel(model()).querySelector<HTMLElement>('.siyur-plan-credit')
    expect(credit?.dataset.planSource).toBe('user-owned')
    expect(credit?.textContent).toMatch(/your own plan/i)
    // The absent chip is explained, not left as a gap indistinguishable from a bug.
    expect(credit?.querySelector('.siyur-chip')).toBeNull()
  })

  it('reports a place the walking network could not reach, rather than straight-lining it', () => {
    const panel = renderPlanPanel(model({ excluded: [{ site_id: SITE_B, reason: 'unroutable' }] }))
    const item = panel.querySelector<HTMLElement>('.siyur-plan-excluded__item')
    expect(item?.dataset.reason).toBe('unroutable')
  })

  it('says "not enough here" for an honest empty day instead of reporting a broken one', () => {
    const empty = renderPlanPanel(
      model({ itinerary: readItineraryFrame({ ...ITINERARY, stops: [] }), candidates: 0 }),
    )
    expect(empty.querySelector('.siyur-plan__empty')?.textContent).toMatch(/not enough/i)
    expect(empty.querySelector('.siyur-plan__candidates')).not.toBeNull()
    // An unreadable frame is a DIFFERENT statement and must not borrow that wording.
    const broken = renderPlanPanel(model({ itinerary: readItineraryFrame({ nonsense: true }) }))
    expect(broken.querySelector('.siyur-plan__empty')?.textContent).toMatch(/could not be read/i)
  })
})

/* --------------------------------------------------------- co-presence ----- */

describe('ADR-0019 — every value carries its stamp, and an unstamped value carries nothing', () => {
  it('every rendered value element contains exactly one attribution chip', () => {
    // Structural rather than a list of the fields this fixture happens to carry: a field
    // added to the stop row is covered by this test the day it is added. This catches a
    // chip MOVED to a sibling element; the `textContent` assertion further down is what
    // catches an unstamped value smuggled in with no chip at all. Both are load-bearing.
    const values = [...renderPlanPanel(model()).querySelectorAll('.siyur-value')]
    expect(values.length).toBeGreaterThan(0)
    for (const value of values) expect(value.querySelectorAll('.siyur-chip')).toHaveLength(1)
  })

  it('the co-presence QUERY above detects an uncredited value element (guards the guard)', () => {
    // What this covers, precisely: the SELECTOR, not production code — it builds the
    // offending element itself. Kept because a `.siyur-value` query that silently
    // stopped matching would make the test above pass on an empty set forever; the
    // `expect(values.length).toBeGreaterThan(0)` there is the other half of that.
    const panel = renderPlanPanel(model())
    const smuggled = document.createElement('span')
    smuggled.className = 'siyur-value'
    smuggled.textContent = 'Palace of the Grand Master'
    panel.append(smuggled)
    const offenders = [...panel.querySelectorAll('.siyur-value')].filter(
      (v) => v.querySelectorAll('.siyur-chip').length !== 1,
    )
    expect(offenders).toHaveLength(1)
  })

  it('a place name with NO source renders nothing at all — not a default, not a placeholder', () => {
    // The FR-008 / SC-004 case, smuggled past the guards exactly as a bug elsewhere
    // would smuggle it: a `SiteRecordV1` whose name value has no `source` stamp.
    const unstamped = {
      ...site(SITE_A, 'Palace of the Grand Master'),
      names: { en: { value: 'Palace of the Grand Master' } },
    } as unknown as SiteRecordV1
    const panel = renderPlanPanel(model({ sites: new Map([[SITE_A, unstamped]]) }))

    expect(panel.textContent).not.toMatch(/Palace of the Grand Master/)
    const stop = panel.querySelector<HTMLElement>('.siyur-plan-stop[data-stop-order="0"]')
    expect(stop?.dataset.place).toBe('unstamped')
    // The stop is still LISTED — a silently shorter day is the wrong thing to approve.
    expect(stop?.querySelector('.siyur-plan-stop__when')?.textContent).toBe('10:00 · 60 min')
    // …and the address/hours it *did* stamp do not leak out under a nameless stop.
    expect(stop?.querySelectorAll('.siyur-value')).toHaveLength(0)
  })

  it('a stop whose commons record is not loaded says so, and invents no name', () => {
    const panel = renderPlanPanel(model({ sites: new Map() }))
    const stop = panel.querySelector<HTMLElement>('.siyur-plan-stop[data-stop-order="0"]')
    expect(stop?.dataset.place).toBe('unavailable')
    expect(stop?.querySelectorAll('.siyur-value')).toHaveLength(0)
  })

  it('a walking leg’s distance and time each carry the leg’s own ODbL stamp', () => {
    const chips = [
      ...renderPlanPanel(model()).querySelectorAll('.siyur-plan-leg .siyur-chip'),
    ] as HTMLElement[]
    expect(chips).toHaveLength(2)
    for (const chip of chips) {
      expect(chip.dataset.sourceKind).toBe('osm')
      expect(chip.dataset.license).toBe('ODbL-1.0')
      expect(chip.textContent).toMatch(/© OpenStreetMap contributors/)
    }
  })

  it('mirrors the server’s aggregate attribution verbatim, labelled as aggregate', () => {
    const panel = renderPlanPanel(model({ attribution: ['© OpenStreetMap contributors'] }))
    const credit = panel.querySelector<HTMLElement>('.siyur-plan-attribution')
    expect(credit?.dataset.creditScope).toBe('aggregate')
    expect(credit?.textContent).toBe('© OpenStreetMap contributors')
  })
})

/* -------------------------------------------------------- the HITL gate ---- */

describe('feasibility violations are named, and approval is blocked until they are gone', () => {
  it('renders one row per violation, in the server’s own words', () => {
    const panel = renderPlanPanel(model({ feasibility: INFEASIBLE }))
    const rows = [...panel.querySelectorAll('.siyur-plan-violation')].map((n) => n.textContent)
    // Not "infeasible": "walking_m 4200 > budget 3000" says what to change.
    expect(rows).toEqual(VIOLATIONS)
  })

  it('leaves approve UNAVAILABLE on an infeasible plan — the server answers 409', () => {
    const panel = renderPlanPanel(model({ feasibility: INFEASIBLE }))
    const button = panel.querySelector<HTMLButtonElement>('.siyur-plan-approve__button')
    expect(button?.disabled).toBe(true)
    expect(button?.dataset.approvable).toBe('false')
    expect(panel.dataset.approvable).toBe('false')
    // Greyed out is not enough: the reason is stated and names the conflicts.
    expect(panel.querySelector('.siyur-plan-approve__blocked')?.textContent).toMatch(/2 conflicts/)
  })

  it('fires NOTHING when an infeasible plan’s approve button is clicked anyway', () => {
    // Disabled is a hint; unwired is the guarantee. Clearing `disabled` by hand is
    // exactly what a devtools poke (or a stray CSS-driven re-enable) would do.
    const onApprove = vi.fn()
    const panel = renderPlanPanel(model({ feasibility: INFEASIBLE }), { onApprove })
    const button = panel.querySelector<HTMLButtonElement>('.siyur-plan-approve__button')!
    button.disabled = false
    button.click()
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('offers approve on a feasible, proposed plan and hands over its plan id', () => {
    const onApprove = vi.fn()
    const panel = renderPlanPanel(model(), { onApprove })
    const button = panel.querySelector<HTMLButtonElement>('.siyur-plan-approve__button')!
    expect(button.disabled).toBe(false)
    button.click()
    expect(onApprove).toHaveBeenCalledWith('7be2-uuid')
  })

  it('blocks approval when feasibility was never reported — the gate fails shut', () => {
    // `ok` absent is not `ok` true, and "no violations" is not "checked and fine".
    const verdict = approvability(model({ feasibility: sanitiseFeasibility(undefined) }))
    expect(verdict.approvable).toBe(false)
    expect(verdict.reason).toMatch(/not reported/i)
  })

  it('blocks approval in every state but `proposed`, naming which one', () => {
    const states = ['proposing', 'approved', 'superseded', 'compiling', 'compiled', 'failed'] as const
    for (const state of states) {
      const approval = { state, approved_at: null, superseded_by: null }
      const verdict = approvability(model({ approval }))
      expect(verdict.approvable, state).toBe(false)
      expect(verdict.reason, state).toBeTruthy()
    }
    // `superseded` is the 409 the contract distinguishes: point at the successor.
    const superseded = { state: 'superseded', approved_at: null, superseded_by: '9af0' } as const
    expect(approvability(model({ approval: superseded })).reason).toMatch(/newer proposal/i)
    // `compiling` must not read as an unknown state (the three-value-enum trap, T007b).
    expect(sanitiseApproval({ state: 'compiling' }).state).toBe('compiling')
    expect(sanitiseApproval({ state: 'archived' }).state).toBeNull()
    const unknown = sanitiseApproval({ state: 'archived' })
    expect(approvability(model({ approval: unknown })).reason).toMatch(/does not recognise/i)
  })

  it('has nothing to approve before the plan is saved, or when it could not be read', () => {
    expect(approvability(model({ planId: null })).approvable).toBe(false)
    expect(approvability(model({ itinerary: { kind: 'unreadable' } })).approvable).toBe(false)
    expect(approvability(model({ itinerary: { kind: 'empty' } })).approvable).toBe(false)
  })

  it('never opens the gate on a verdict that says ok while naming violations', () => {
    // A half-deployed checker emitting `{"ok":true,"violations":[…]}` contradicts
    // itself. Believing `ok` would open the one irreversible gate in the product AND
    // drop the sentence saying what to fix — and the server answers 409 regardless.
    const contradiction = sanitiseFeasibility({ ok: true, violations: VIOLATIONS })
    expect(contradiction.ok).toBe(false)
    expect(contradiction.readable).toBe(true)
    expect(contradiction.violations).toEqual(VIOLATIONS) // kept, never discarded
    expect(approvability(model({ feasibility: contradiction })).approvable).toBe(false)

    // …and again at the point of use, for a verdict assembled anywhere but the wire.
    const handBuilt: Feasibility = {
      ok: true,
      violations: VIOLATIONS,
      checked_at: CHECKED_AT,
      readable: true,
    }
    expect(approvability(model({ feasibility: handBuilt })).approvable).toBe(false)
    const section = renderFeasibility(handBuilt)
    expect(section.dataset.ok).toBe('false')
    // The reason is rendered, not swallowed by the reassurance line.
    expect(section.textContent).not.toMatch(/fits your time and walking budgets/)
    expect([...section.querySelectorAll('.siyur-plan-violation')].map((n) => n.textContent)).toEqual(
      VIOLATIONS,
    )
    const panel = renderPlanPanel(model({ feasibility: handBuilt }))
    expect(panel.querySelector<HTMLButtonElement>('.siyur-plan-approve__button')?.disabled).toBe(
      true,
    )
  })

  it('says whether feasibility was checked, separately from whether it passed', () => {
    // `data-feasible="false"` alone conflated "checked, and it does not fit" with
    // "never reported" — different facts, and only one of them is about the plan.
    const feasible = renderPlanPanel(model())
    expect(feasible.dataset.feasible).toBe('true')
    expect(renderPlanPanel(model({ feasibility: INFEASIBLE })).dataset.feasible).toBe('false')
    const unreported = renderPlanPanel(model({ feasibility: sanitiseFeasibility(undefined) }))
    expect(unreported.dataset.feasible).toBe('unknown')
  })
})

/* ------------------------------------------- a refused approval is visible --- */

describe('a refused approval lands on the surface — it is not thrown into nothing', () => {
  /** A mounted surface showing a feasible, `proposed` day, ready to approve. */
  const mounted = (
    onApprove: (planId: string) => Promise<void>,
  ): { host: HTMLElement; surface: PlanReviewSurface } => {
    const host = document.createElement('div')
    const surface = new PlanReviewSurface(host, { sites: SITES, onApprove })
    surface.update(model())
    return { host, surface }
  }

  const button = (host: HTMLElement): HTMLButtonElement =>
    host.querySelector<HTMLButtonElement>('.siyur-plan-approve__button')!

  it('states a 409 plan_superseded and names the plan that CAN be approved', async () => {
    // The scenario the `void onApprove(planId)` discard made invisible: the user
    // re-planned this area in another tab, so the server has superseded the proposal on
    // screen. Nothing about that plan looks wrong — the successor's id is the only thing
    // that resolves it, and it arrives on the error.
    const onApprove = vi.fn(async () => {
      throw new PlanApprovalConflictError('plan_superseded', [], '9af0-uuid')
    })
    const { host, surface } = mounted(onApprove)
    expect(host.querySelector('.siyur-plan-approve-failure')).toBeNull()

    button(host).click()
    await flush()

    const failure = host.querySelector<HTMLElement>('.siyur-plan-approve-failure')
    expect(failure).not.toBeNull()
    expect(failure?.dataset.reason).toBe('plan_superseded')
    expect(failure?.getAttribute('role')).toBe('alert')
    // Says what did NOT happen — a user who cannot tell a refusal from a dead click
    // simply clicks again.
    expect(failure?.textContent).toMatch(/nothing was approved/i)
    const successor = failure?.querySelector<HTMLElement>('[data-superseded-by]')
    expect(successor?.dataset.supersededBy).toBe('9af0-uuid')
    expect(successor?.textContent).toMatch(/9af0-uuid/)
    expect(surface.current.failure?.supersededBy).toBe('9af0-uuid')
    // The request is over: the button is usable again, and pending is cleared.
    expect(surface.current.approving).toBe(false)
    expect(button(host).dataset.pending).toBe('false')
    expect(button(host).disabled).toBe(false)
  })

  it('names the server’s violations when approval is refused as infeasible', async () => {
    const onApprove = vi.fn(async () => {
      throw new PlanApprovalConflictError('infeasible', VIOLATIONS, null)
    })
    const { host } = mounted(onApprove)
    button(host).click()
    await flush()

    const failure = host.querySelector<HTMLElement>('.siyur-plan-approve-failure')
    expect(failure?.dataset.reason).toBe('infeasible')
    const named = [...host.querySelectorAll('.siyur-plan-approve-failure__violation')].map(
      (n) => n.textContent,
    )
    expect(named).toEqual(VIOLATIONS)
    // The refusal's violations are queryable apart from the feasibility section's — the
    // two can disagree, and that disagreement is the news.
    expect(host.querySelectorAll('.siyur-plan-violation')).toHaveLength(0)
  })

  it('reports an expired session as a refusal, never as an approval', async () => {
    const onApprove = vi.fn(async () => {
      throw new PlanRequestError(401)
    })
    const { host } = mounted(onApprove)
    button(host).click()
    await flush()
    const failure = host.querySelector<HTMLElement>('.siyur-plan-approve-failure')
    expect(failure?.textContent).toMatch(/session has ended/i)
    expect(failure?.textContent).toMatch(/nothing was approved/i)
  })

  it('reports a rejection it has no wording for, rather than showing nothing', async () => {
    const onApprove = vi.fn(async () => {
      throw new TypeError('network down')
    })
    const { host, surface } = mounted(onApprove)
    button(host).click()
    await flush()
    expect(host.querySelector('.siyur-plan-approve-failure')?.textContent).toMatch(
      /did not complete, so nothing was approved/i,
    )
    expect(surface.current.failure?.reason).toBeNull()
  })

  it('POSTs once while a request is in flight, however many times it is clicked', async () => {
    // Approve is irreversible; server-side idempotency is the last line of defence, not
    // the first. The in-flight button is disabled AND unbound, and the listener disarms
    // itself the instant it fires — so even the click before the re-render cannot repeat.
    let release: (() => void) | undefined
    const onApprove = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    const { host, surface } = mounted(onApprove)
    button(host).click()

    expect(surface.current.approving).toBe(true)
    const pendingButton = button(host)
    expect(pendingButton.disabled).toBe(true)
    expect(pendingButton.dataset.pending).toBe('true')
    expect(pendingButton.getAttribute('aria-busy')).toBe('true')
    expect(pendingButton.textContent).toMatch(/approving/i)

    pendingButton.disabled = false // a devtools poke, or a stray re-enable
    pendingButton.click()
    pendingButton.click()
    expect(onApprove).toHaveBeenCalledTimes(1)

    release?.()
    await flush()
    expect(surface.current.approving).toBe(false)
    expect(host.querySelector('.siyur-plan-approve-failure')).toBeNull()
  })

  it('hands a bare panel’s rejection to onApproveFailure instead of discarding it', async () => {
    // Not everything mounts a surface. `renderPlanPanel` used directly must still route
    // the rejection somewhere a caller can see it.
    const error = new PlanApprovalConflictError('infeasible', VIOLATIONS, null)
    const onApproveFailure = vi.fn()
    const panel = renderPlanPanel(model(), {
      onApprove: async () => {
        throw error
      },
      onApproveFailure,
    })
    panel.querySelector<HTMLButtonElement>('.siyur-plan-approve__button')!.click()
    await flush()
    expect(onApproveFailure).toHaveBeenCalledWith(error)
  })

  it('clears a stale refusal when the plan is read back from the server', async () => {
    const onApprove = vi.fn(async () => {
      throw new PlanApprovalConflictError('plan_superseded', [], '9af0-uuid')
    })
    const { host, surface } = mounted(onApprove)
    button(host).click()
    await flush()
    expect(host.querySelector('.siyur-plan-approve-failure')).not.toBeNull()

    surface.applyDetail(
      '9af0-uuid',
      sanitisePlanDetail({
        plan: ITINERARY,
        feasibility: { ok: true, violations: [], checked_at: CHECKED_AT },
        approval: { state: 'proposed', approved_at: null, superseded_by: null },
        attribution: ['© OpenStreetMap contributors'],
      }),
    )
    expect(host.querySelector('.siyur-plan-approve-failure')).toBeNull()
    expect(surface.current.failure).toBeNull()
  })
})

/* ----------------------------------------------------- the read-back path --- */

describe('GET /plans/{id} tells the same story about a row as the stream did', () => {
  const detail = (plan: unknown): ReturnType<typeof sanitisePlanDetail> =>
    sanitisePlanDetail({
      ...(plan === undefined ? {} : { plan }),
      feasibility: { ok: false, violations: [] },
      approval: { state: 'proposed', approved_at: null, superseded_by: null },
      attribution: [],
    })

  it('reads an honest EMPTY day back as empty — not as "could not be read"', () => {
    // A sparse area with zero candidates streams "there is not enough in the commons
    // here". Reloading the page must not answer, about the same row, "the proposed day
    // could not be read, so there is nothing to approve" — the second statement tells
    // the user the app is broken when it is working exactly as specified.
    expect(detail({ ...ITINERARY, stops: [] }).plan).toEqual({ kind: 'empty' })
    const host = document.createElement('div')
    const surface = new PlanReviewSurface(host, { sites: SITES })
    surface.applyDetail('7be2-uuid', detail({ ...ITINERARY, stops: [] }))
    expect(host.querySelector('.siyur-plan__empty')?.textContent).toMatch(/not enough/i)
    expect(host.querySelector('.siyur-plan__empty')?.textContent).not.toMatch(/could not be read/i)
  })

  it('still reads a malformed plan back as unreadable', () => {
    expect(detail({ nonsense: true }).plan).toEqual({ kind: 'unreadable' })
    const host = document.createElement('div')
    new PlanReviewSurface(host, { sites: SITES }).applyDetail('7be2-uuid', detail({ nonsense: true }))
    expect(host.querySelector('.siyur-plan__empty')?.textContent).toMatch(/could not be read/i)
  })

  it('reads a row with no plan yet as "not proposed", which is a third thing again', () => {
    expect(detail(undefined).plan).toEqual({ kind: 'absent' })
    expect(detail(null).plan).toEqual({ kind: 'absent' })
    const host = document.createElement('div')
    new PlanReviewSurface(host, { sites: SITES }).applyDetail('7be2-uuid', detail(undefined))
    expect(host.querySelector('.siyur-plan__empty')?.textContent).toMatch(/no day has been proposed/i)
  })

  it('applies a readable plan, its verdict and its credits', () => {
    const host = document.createElement('div')
    const surface = new PlanReviewSurface(host, { sites: SITES })
    surface.applyDetail(
      '7be2-uuid',
      sanitisePlanDetail({
        plan: ITINERARY,
        feasibility: { ok: true, violations: [], checked_at: CHECKED_AT },
        approval: { state: 'proposed', approved_at: null, superseded_by: null },
        attribution: ['© OpenStreetMap contributors'],
      }),
    )
    expect(surface.approvable).toBe(true)
    expect(host.querySelector('.siyur-plan__date')?.textContent).toBe('2026-08-14')
    expect(host.querySelector('.siyur-plan-attribution')?.textContent).toBe(
      '© OpenStreetMap contributors',
    )
    const rows = [...host.querySelectorAll('.siyur-plan-timeline > *')] as HTMLElement[]
    expect(rows.map((r) => r.dataset.stopOrder ?? r.dataset.legId)).toEqual(['0', 'leg-0', '1'])
  })
})

/* -------------------------------------------------- the verdict's caption --- */

describe('ADR-0030 A1 — a server-computed verdict carries its own caption', () => {
  it('captions the verdict inside the feasibility section, in every branch', () => {
    // Inside `renderFeasibility`, so it travels with the section: the plan-structure
    // credit is appended only in the `itinerary` branch, and the verdict renders in all
    // of them.
    for (const feasibility of [FEASIBLE, INFEASIBLE, sanitiseFeasibility(undefined)]) {
      const caption = renderFeasibility(feasibility).querySelector<HTMLElement>(
        '.siyur-plan-verdict-credit',
      )
      expect(caption, String(feasibility.readable) + String(feasibility.ok)).not.toBeNull()
      expect(caption?.dataset.verdictSource).toBe('server-computed')
      expect(caption?.querySelector('.siyur-chip')).toBeNull() // stated, never stamped
    }
  })

  it('captions violations even when NO itinerary rendered — the gap A1 closes', () => {
    const panel = renderPlanPanel(
      model({ itinerary: { kind: 'unreadable' }, feasibility: INFEASIBLE }),
    )
    // The user-owned plan credit is absent here, exactly as before…
    expect(panel.querySelector('.siyur-plan-credit')).toBeNull()
    // …and the violations are captioned anyway, by their own section.
    expect(panel.querySelectorAll('.siyur-plan-violation').length).toBe(VIOLATIONS.length)
    expect(
      panel.querySelector('.siyur-plan-feasibility .siyur-plan-verdict-credit'),
    ).not.toBeNull()
  })

  it('does not file the verdict under the user’s own-data credit', () => {
    // "Times, stop order … are your own plan" is a claim about the user's composition.
    // A server verdict ABOUT that composition filed under it says the verdict is theirs.
    const panel = renderPlanPanel(model({ feasibility: INFEASIBLE }))
    const structure = panel.querySelector<HTMLElement>('.siyur-plan-credit')
    expect(structure?.dataset.planSource).toBe('user-owned')
    expect(structure?.textContent).not.toMatch(/verdict|conflict/i)
    const verdict = panel.querySelector<HTMLElement>(
      '.siyur-plan-feasibility .siyur-plan-verdict-credit',
    )
    expect(verdict?.textContent).toMatch(/not sourced data/i)
    expect(verdict?.textContent).not.toMatch(/your own plan/i)
  })
})

/* ------------------------------------------------------------- narrowing --- */

describe('the plan wire is narrowed before anything is shown', () => {
  it('reads the verdict fail-closed', () => {
    expect(sanitiseFeasibility(undefined)).toEqual({
      ok: false,
      violations: [],
      checked_at: null,
      readable: false,
    })
    // A body carrying violations but no `ok` is unreadable, not "feasible with notes".
    expect(sanitiseFeasibility({ violations: VIOLATIONS })).toMatchObject({
      ok: false,
      readable: false,
    })
    expect(sanitiseFeasibility({ ok: 'true' })).toMatchObject({ ok: false, readable: false })
    expect(sanitiseFeasibility({ ok: true, checked_at: CHECKED_AT })).toMatchObject({
      ok: true,
      readable: true,
      checked_at: CHECKED_AT,
    })
  })

  it('tells an honest empty day apart from an unreadable one', () => {
    expect(readItineraryFrame({ ...ITINERARY, stops: [] })).toEqual({ kind: 'empty' })
    expect(readItineraryFrame({ ...ITINERARY, schema_ver: 'ItineraryV2' })).toEqual({
      kind: 'unreadable',
    })
    expect(readItineraryFrame(null)).toEqual({ kind: 'unreadable' })
    expect(readItineraryFrame(ITINERARY).kind).toBe('itinerary')
  })
})

/* --------------------------------------------------------------- transport --- */

describe('POST /plans streams the proposal (contracts/plans.md § event sequence)', () => {
  it('collects itinerary, verdict, candidates, exclusions and the plan id', async () => {
    const fetchImpl = streamingFetch([CONTRACT_STREAM])
    const proposal = await streamPlan({ request: REQUEST, fetchImpl })
    expect(proposal.itinerary.kind).toBe('itinerary')
    expect(proposal.feasibility.ok).toBe(false)
    expect(proposal.feasibility.violations).toEqual(VIOLATIONS)
    expect(proposal.candidates).toBe(39)
    expect(proposal.excluded).toEqual([{ site_id: SITE_B, reason: 'unroutable' }])
    expect(proposal.done).toEqual({ plan_id: '7be2-uuid', state: 'proposed' })
  })

  it('sends the date exactly as the user gave it', async () => {
    const fetchImpl = streamingFetch([CONTRACT_STREAM])
    await streamPlan({ request: REQUEST, fetchImpl })
    const mock = fetchImpl as unknown as ReturnType<typeof vi.fn>
    const init = mock.mock.calls[0]?.[1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual(REQUEST)
  })

  it('reports NO verdict rather than a passing one when the stream omits feasibility', async () => {
    const truncated = CONTRACT_STREAM.replace(/event: feasibility[\s\S]*?\n\n/, '')
    const fetchImpl = streamingFetch([truncated])
    const proposal = await streamPlan({ request: REQUEST, fetchImpl })
    expect(proposal.feasibility).toMatchObject({ ok: false, readable: false })
    expect(approvability(model({ feasibility: proposal.feasibility })).approvable).toBe(false)
  })

  it('drives a mounted surface to a blocked gate from the stream alone', async () => {
    const host = document.createElement('div')
    const surface = new PlanReviewSurface(host, { sites: SITES })
    surface.start()
    await streamPlan({
      request: REQUEST,
      fetchImpl: streamingFetch([CONTRACT_STREAM]),
      handlers: surface.handlers(),
    })
    expect(surface.current.planId).toBe('7be2-uuid')
    expect(surface.approvable).toBe(false)
    const button = host.querySelector<HTMLButtonElement>('.siyur-plan-approve__button')
    expect(button?.disabled).toBe(true)
    const named = [...host.querySelectorAll('.siyur-plan-violation')].map((n) => n.textContent)
    expect(named).toEqual(VIOLATIONS)
  })
})

describe('POST /plans/{id}/approve — the 409 is a value, not a crash', () => {
  it('surfaces the named violations when approval is refused as infeasible', async () => {
    const fetchImpl = jsonFetch(409, { error: 'infeasible', violations: VIOLATIONS })
    await expect(approvePlan('7be2-uuid', { fetchImpl })).rejects.toBeInstanceOf(
      PlanApprovalConflictError,
    )
    const error = await approvePlan('7be2-uuid', { fetchImpl }).catch((e: unknown) => e)
    expect((error as PlanApprovalConflictError).reason).toBe('infeasible')
    expect((error as PlanApprovalConflictError).violations).toEqual(VIOLATIONS)
  })

  it('points at the successor when the plan was superseded', async () => {
    const fetchImpl = jsonFetch(409, { error: 'plan_superseded', superseded_by: '9af0-uuid' })
    const error = await approvePlan('7be2-uuid', { fetchImpl }).catch((e: unknown) => e)
    expect((error as PlanApprovalConflictError).supersededBy).toBe('9af0-uuid')
  })

  it('returns the approval, POSTing to the plan’s own approve path', async () => {
    const fetchImpl = jsonFetch(200, {
      plan_id: '7be2-uuid',
      state: 'approved',
      approved_at: '2026-08-07T09:14:00Z',
    })
    const result = await approvePlan('7be2-uuid', { fetchImpl })
    expect(result).toEqual({
      plan_id: '7be2-uuid',
      state: 'approved',
      approved_at: '2026-08-07T09:14:00Z',
    })
    const call = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(call?.[0]).toBe('/plans/7be2-uuid/approve')
    expect((call?.[1] as RequestInit).method).toBe('POST')
  })
})
