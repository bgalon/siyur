/**
 * Phase B — the app is usable with a thumb (F-01 … F-06 of `ux-audit-2026-08-15.md`).
 *
 * ────────────────────────────────────────────────────────────────────────────────
 * ## What this file can and cannot prove, stated up front
 * ────────────────────────────────────────────────────────────────────────────────
 *
 * **jsdom performs no layout.** `getBoundingClientRect()` returns zeroes here, so a
 * vitest file physically cannot measure a tap target, an occlusion or a viewport
 * overflow. That is exactly how the audited defects survived 370 green tests: every one
 * of them lived in the gap between a correct attribute and a rendered pixel.
 *
 * So the work is split, and both halves are real:
 *
 * - **`test/e2e/mobile-layout.spec.ts`** measures the *built* app in Chromium at 375 ×
 *   667 and 390 × 844 — rendered rects, `elementFromPoint` hit tests, computed font
 *   sizes. That is the assertion about pixels.
 * - **This file** asserts the two things a headless DOM *can* prove: the **causes** in
 *   the stylesheets (the numbers that produce those pixels), and the **behaviour** of
 *   the states — which is where "the attribute is right and the sentence is a lie"
 *   lives, and where no amount of pixel measurement helps.
 *
 * The CSS scans follow `test/plan-list.test.ts`'s idiom, which was written for the same
 * reason: comments are stripped first, because this project's stylesheets discuss the
 * very rules they forbid.
 */

import { describe, it, expect, vi } from 'vitest'

import { buildCoverageCard } from '../src/map/areas'
import { mountDelimitControl } from '../src/map/delimit'
import { formatElapsed } from '../src/map/elapsed'
import { sanitiseAreaResolution } from '../src/map/guards'
import { ResearchProgressSurface, runResearch } from '../src/map/research'
import { mountPlanForm } from '../src/plan/form'
import {
  EMPTY_PLAN_MODEL,
  approvability,
  mountPlanReview,
  renderFeasibility,
  renderPlanPanel,
  type PlanReviewModel,
} from '../src/plan/render'

/* ------------------------------------------------------------ css helpers --- */

const readCss = async (name: string): Promise<string> => {
  const { readFileSync } = await import('node:fs')
  const { join } = await import('node:path')
  return readFileSync(join(process.cwd(), 'src', name), 'utf8')
}

/** Declarations only. Every prose block in these files names rules it argues against. */
const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, '')

/**
 * `selector { body }` pairs, flattened across `@media` nesting.
 *
 * The inner-rule regex skips an at-rule's prelude by construction: `[^{}]*` cannot span
 * the inner `{`, so the match that succeeds is always the innermost rule. Good enough to
 * ask "which rule declares this number", which is the only question here.
 */
const rules = (css: string): { selector: string; body: string }[] =>
  [...strip(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
    selector: (m[1] ?? '').trim().replace(/\s+/g, ' '),
    body: m[2] ?? '',
  }))

/** The `@media (width <= 760px)` block of a stylesheet, by brace matching. */
const mobileBlock = (css: string): string => {
  const stripped = strip(css)
  const at = stripped.indexOf('@media (width <= 760px)')
  expect(at, 'no @media (width <= 760px) block in this stylesheet').toBeGreaterThan(-1)
  let depth = 0
  for (let i = stripped.indexOf('{', at); i < stripped.length; i += 1) {
    if (stripped[i] === '{') depth += 1
    else if (stripped[i] === '}') {
      depth -= 1
      if (depth === 0) return stripped.slice(at, i + 1)
    }
  }
  throw new Error('unterminated @media block')
}

/* ═══════════════════════════════════════════════ F-01 — nothing overlays ═══ */

describe('F-01 — below 760px the surfaces stop being layers (UX-01, UX-05, UX-06, UX-10)', () => {
  /**
   * The four surfaces that were stacked on top of each other and on the map. Measured at
   * 390 × 844 before this pass: `Use this view` hit-tested to `.siyur-plan-panel__title`
   * with no scroll position at which it was reachable, the attribution hit-tested to
   * `.siyur-sheet`, and **0 of 957** markers hit-tested to themselves.
   */
  it('gives the map back a real share of the screen', async () => {
    const map = rules(mobileBlock(await readCss('style.css'))).find((r) => r.selector === '#map')
    expect(map?.body).toMatch(/position:\s*static|position:\s*relative/)
    expect(map?.body).toMatch(/block-size:\s*\d+svh/)
  })

  /**
   * **Each surface is un-fixed in the file that fixed it, and after it — because a media
   * query adds no specificity, so only order decides.**
   *
   * This assertion is shaped by a defect it did not catch the first time. The override
   * for `.siyur-plan-panel` was written into `style.css`'s shell block while its
   * `position: fixed` lives in `plan.css`; `main.ts` imports `style.css` first, the
   * bundler concatenates in that order, and the base rule therefore won **in the built
   * artifact** while the dev server rendered it correctly. A scan that only asked "is
   * there a `position: static` inside a `<= 760px` block" passed happily and the shipped
   * app had a 398px panel in a 375px viewport.
   *
   * So the invariant asserted here is the one that survives bundling: *same file, later
   * offset*. `test/e2e/mobile-layout.spec.ts` measures the built artifact, which is what
   * found this; this test is what keeps it found.
   */
  it.each([
    ['style.css', '.siyur-controls'],
    ['style.css', '.siyur-sheet'],
    ['plan.css', '.siyur-plan-panel'],
  ])('%s un-fixes %s in its own file, after the rule that fixed it', async (file, selector) => {
    const css = strip(await readCss(file))
    const fixedAt = css.search(
      new RegExp(`\\${selector}\\s*\\{[^}]*position:\\s*fixed`, 's'),
    )
    expect(fixedAt, `${selector} declares no position: fixed in ${file}`).toBeGreaterThan(-1)

    const block = mobileBlock(css)
    const override = rules(block).find(
      (r) => r.selector.split(', ').includes(selector) && /position:\s*static/.test(r.body),
    )
    expect(override, `${selector} is never un-fixed inside ${file}'s <= 760px block`).toBeDefined()
    expect(css.indexOf(block), 'the override must come after the base rule').toBeGreaterThan(
      fixedAt,
    )

    // And no *other* stylesheet tries to do it from outside, where order is the
    // bundler's to choose rather than this file's.
    for (const other of ['style.css', 'plan.css', 'library.css'].filter((f) => f !== file)) {
      const foreign = rules(strip(await readCss(other))).filter(
        (r) => r.selector.split(', ').includes(selector) && /position:/.test(r.body),
      )
      expect(foreign, `${other} reaches across files to position ${selector}`).toEqual([])
    }
  })

  it('caps nothing, so no primary CTA can be clipped below an invisible fold', async () => {
    // UX-05: `max-height: 40vh` + `overflow: auto` clipped **294px** at 390px, with
    // `Plan this day →` and `Approve this day` both under the fold of an inner scroller
    // that had no scroll affordance. UX-06 put the validation messages there too.
    const panel = mobileBlock(await readCss('plan.css'))
    const sheet = mobileBlock(await readCss('style.css'))
    expect(panel).toMatch(/max-height:\s*none/)
    expect(sheet).toMatch(/max-height:\s*none/)
    // And the cap itself is gone from the file, not merely overridden further down.
    expect(strip(await readCss('plan.css'))).not.toMatch(/max-height:\s*40vh/)
  })
})

/* ══════════════════════════════════ F-03 — the ODbL credit is never covered ═══ */

describe('F-03 — the attribution is not painted over at any width (UX-11)', () => {
  it('anchors the desktop sheet above the strip the attribution control occupies', async () => {
    // The one **compliance** finding in the audit (Constitution Article V): the credit
    // hit-tested to `.siyur-sheet` at 375, 390, 430 **and 1440**. The mobile half is
    // structural — the sheet is not an overlay there at all (F-01 above) — so what is
    // left to pin is the width the team has always looked at.
    const css = strip(await readCss('style.css'))
    const sheet = rules(css).find((r) => r.selector === '.siyur-sheet')
    const inset = /inset-block-end:\s*([\d.]+)px/.exec(sheet?.body ?? '')?.[1]
    expect(inset, '.siyur-sheet declares no inset-block-end').toBeDefined()
    // 24px is the control's measured height; anything less re-covers it.
    expect(Number(inset)).toBeGreaterThanOrEqual(24)

    // The plan panel is the other fixed layer that reaches the bottom edge.
    const panelCss = strip(await readCss('plan.css'))
    const panel = rules(panelCss).find((r) => r.selector === '.siyur-plan-panel')
    const block = /inset-block:\s*\d+px\s+([\d.]+)px/.exec(panel?.body ?? '')?.[1]
    expect(Number(block)).toBeGreaterThanOrEqual(24)
  })
})

/* ═════════════════════════════════════════ F-02 — thumbs and legibility ═══ */

describe('F-02 — tap targets and type meet the spec (UX-12, UX-15)', () => {
  /**
   * Everything a person taps to move through the two journeys this phase covers.
   * Rendered heights before the pass, at 390px: 19, 34, 34, 42 × 5, 36, 38.
   */
  const CONTROLS = [
    ['style.css', '.siyur-delimit__input'],
    ['style.css', '.siyur-delimit__submit, .siyur-delimit__viewport'],
    ['style.css', '.siyur-coverage__action'],
    ['style.css', '.siyur-research__cancel'],
    ['plan.css', '.siyur-plan-form__input'],
    ['plan.css', '.siyur-plan-form__submit'],
    ['plan.css', '.siyur-plan-approve__button'],
  ] as const

  it.each(CONTROLS)('%s %s declares a 44px tap target', async (file, selector) => {
    const rule = rules(await readCss(file)).find((r) => r.selector === selector)
    const min = /min-block-size:\s*([\d.]+)px/.exec(rule?.body ?? '')?.[1]
    expect(min, `${selector} declares no min-block-size`).toBeDefined()
    expect(Number(min), selector).toBeGreaterThanOrEqual(44)
  })

  /**
   * **The one control deliberately below the floor**, recorded by name so it cannot pass
   * unremarked. ODbL wants the credit visible and its licence reachable; a 44px credit
   * strip on a 390px phone eats the map it is crediting, which is how it ended up under
   * the sheet in the first place. It gets the legible floor and an enlarged link.
   */
  it('records the attribution link as the documented exception, not an oversight', async () => {
    const css = await readCss('style.css')
    const link = rules(css).find((r) => r.selector === '.siyur-attrib a')
    expect(link?.body).toMatch(/padding-block:\s*[\d.]+px/)
    expect(strip(css)).not.toMatch(/\.siyur-attrib a\s*\{[^}]*min-block-size/)
    // The exception is argued in the file, not just implemented in it.
    expect(css).toMatch(/deliberately below the 44px tap floor/)
  })

  it.each(['style.css', 'plan.css'])('%s sets no font-size below the floors', async (file) => {
    const found = rules(await readCss(file)).flatMap((rule) =>
      [...rule.body.matchAll(/font-size:\s*([\d.]+)px/g)].map((m) => ({
        selector: rule.selector,
        size: Number(m[1]),
      })),
    )
    expect(found.length).toBeGreaterThan(8) // the scan found rules, not nothing

    // Body text: >= 14px (ux-handoff § Typography, at real 375–430px widths).
    // Provenance/mono micro-stamps: >= 11px, and only these. The mock's 8–8.5px chip is
    // quoted at a 340px design width and measured 8.5px on a real phone (UX-15).
    const CHIPS = [
      '.siyur-chip',
      '.siyur-value__label',
      '.siyur-research__chip',
      '.siyur-research__source',
      '.siyur-plan-form__label',
      // The ODbL credit, the same documented exception as its tap target above: it is a
      // legally required caption on the map, not body copy, and it is listed here by
      // name so that adding a *sixth* sub-14px selector fails this test.
      '.siyur-attrib',
    ]
    const below14 = found.filter((f) => f.size < 14)
    expect(below14.filter((f) => !CHIPS.includes(f.selector))).toEqual([])
    expect(below14.filter((f) => f.size < 11)).toEqual([])
  })

  it.each(['style.css', 'plan.css'])('%s stays free of physical direction properties', async (file) => {
    // The audit found **zero** against 35 logical ones. RTL is deferred to M2/M3 and this
    // pass rewrote a large share of both files: keeping the record costs nothing now.
    const physical =
      /(?:^|[\s;{])(?:margin|padding|border)-(?:left|right)\b|(?:^|[\s;{])(?:left|right):|text-align:\s*(?:left|right)\b/g
    expect([...strip(await readCss(file)).matchAll(physical)].map((m) => m[0].trim())).toEqual([])
  })
})

/* ══════════════════════════════════════════ F-04 — the coverage card ═══ */

describe('F-04 — the coverage card reads known_site_count in both branches (UX-09)', () => {
  const resolution = (known_site_count: number, covered: boolean) =>
    sanitiseAreaResolution({
      area_id: 'a-uuid',
      polygon: null,
      coverage: {
        known_site_count,
        covered,
        stalest_observed_at: null,
        refresh_available: false,
      },
    })!

  it('reports the places that are there for an area that is NOT covered', () => {
    // The audit's live numbers: the API answered `known_site_count: 958, covered: false`
    // and the card said "No cited places here yet" — the product's core asset reported
    // to the user as none, because the uncovered branch was a hardcoded string.
    const card = buildCoverageCard(resolution(958, false))
    expect(card.dataset.covered).toBe('false')
    expect(card.dataset.knownSiteCount).toBe('958')
    expect(card.textContent).toMatch(/958 cited places/)
    expect(card.textContent).not.toMatch(/No cited places here yet/)
    // …and it still does not claim the area was researched, because it was not.
    expect(card.querySelector('.siyur-coverage__title')?.textContent).toBe('Not researched yet')
    expect(card.querySelector<HTMLElement>('.siyur-coverage__action')?.dataset.action).toBe(
      'research',
    )
  })

  it('keeps "none yet" for a genuine zero, because 0 places is not a number to print', () => {
    const card = buildCoverageCard(resolution(0, false))
    expect(card.textContent).toMatch(/No cited places here yet/)
    expect(card.dataset.knownSiteCount).toBe('0')
  })

  it('says "1 cited place" for one, in both branches', () => {
    expect(buildCoverageCard(resolution(1, false)).textContent).toMatch(/1 cited place\b/)
    expect(buildCoverageCard(resolution(1, true)).textContent).toMatch(/1 cited place\b/)
  })
})

/* ═════════════════════════════ F-05 — in flight is a state, and it is told ═══ */

describe('F-05 — nothing pretends to be idle while it is working (UX-07, UX-08, UX-13)', () => {
  it('disables the research button for the length of the pass and says what it is doing', async () => {
    // UX-08: the button stayed enabled through a multi-minute pass and was re-tappable,
    // queueing duplicate work against a single-worker API whose `409` guard is
    // process-local.
    let release: (() => void) | undefined
    const requestResearch = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    const card = buildCoverageCard(
      sanitiseAreaResolution({
        area_id: 'a-uuid',
        polygon: null,
        coverage: {
          known_site_count: 0,
          covered: false,
          stalest_observed_at: null,
          refresh_available: false,
        },
      })!,
      { requestResearch },
    )
    const button = card.querySelector<HTMLButtonElement>('.siyur-coverage__action')!
    expect(button.dataset.pending).toBe('false')

    button.click()
    expect(button.dataset.pending).toBe('true')
    expect(button.disabled).toBe(true)
    expect(button.getAttribute('aria-busy')).toBe('true')
    expect(button.textContent).toBe('Researching…')

    // A second tap while it runs starts nothing.
    button.click()
    expect(requestResearch).toHaveBeenCalledTimes(1)

    release?.()
    await Promise.resolve()
    await Promise.resolve()
    expect(button.disabled).toBe(false)
    expect(button.textContent).toBe('Start researching →')
  })

  it('restores the button to the state it mounted in, not to "enabled"', async () => {
    /**
     * A card that mounted **disabled** must not come back enabled: the pending wrapper
     * remembers the original rather than assuming `false`.
     *
     * The resolution is built directly rather than through `sanitiseAreaResolution`,
     * and that is worth saying plainly: `guards.ts::sanitiseCoverage` *raises*
     * `refresh_available` to `true` for any covered area (FR-006 — a covered area always
     * gets a refresh offer), so this combination cannot arrive off the wire today. It is
     * the type's shape, and `buildCoverageCard` is exported against the type, so the
     * defensive line is asserted where it can be — and the reader is told it is a
     * contract-shape case, not a live one.
     */
    const requestResearch = vi.fn(() => Promise.resolve())
    const card = buildCoverageCard(
      {
        area_id: 'a-uuid',
        polygon: null,
        coverage: {
          known_site_count: 3,
          covered: true,
          stalest_observed_at: null,
          refresh_available: false,
        },
      },
      { requestResearch },
    )
    const button = card.querySelector<HTMLButtonElement>('.siyur-coverage__action')!
    expect(button.disabled).toBe(true)
    button.dispatchEvent(new Event('click')) // past `disabled`, the way a DOM tamper would
    await Promise.resolve()
    await Promise.resolve()
    expect(button.disabled).toBe(true)
  })

  it('builds the elapsed figure without a clock, structurally', async () => {
    /**
     * `test/plan.test.ts` scans `src/plan/` for `new Date` and friends, and that scan is
     * **not** widened to `src/map/` here — `map/areas.ts::describeStaleness` parses a
     * server-sent observation date on purpose, so a blanket ban would be wrong rather
     * than strict. What is asserted instead is the narrow, true thing: the module the
     * two long-running surfaces count with contains no calendar API at all. A duration
     * is not a time of day, and "12s" must be the same string in every timezone.
     */
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const code = readFileSync(join(process.cwd(), 'src', 'map', 'elapsed.ts'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/.*$/gm, '')
    expect(code).toMatch(/formatElapsed/) // the comment strip did not eat the code
    expect(code).not.toMatch(
      /\bnew Date\b|\bDate\.now\b|\bDate\.parse\b|\bDate\.UTC\b|\bIntl\.|\bTemporal\.|toISOString|toLocale[A-Za-z]*|getTimezoneOffset/,
    )
    expect(code).toMatch(/performance\.now/) // monotonic, so an NTP step cannot move it
  })

  it('counts elapsed time as a duration — no clock, no zone, no locale', () => {
    expect(formatElapsed(0)).toBe('0s')
    expect(formatElapsed(9_400)).toBe('9s') // floored: 400ms is not a second
    expect(formatElapsed(59_999)).toBe('59s')
    expect(formatElapsed(60_000)).toBe('1m 00s')
    expect(formatElapsed(754_000)).toBe('12m 34s')
    expect(formatElapsed(-5)).toBe('0s')
  })

  it('shows the elapsed figure while a pass runs and keeps it when it ends', async () => {
    // UX-13: a pass sat on one frozen label for over nine minutes with no elapsed time,
    // no percentage and no cancel — the user could not tell working from hung.
    let now = 1000
    const container = document.createElement('div')
    const surface = new ResearchProgressSurface(container, { now: () => now, tickMs: 1000 })
    const root = surface.element
    const elapsed = () => root.querySelector('.siyur-research__elapsed')

    expect(elapsed()?.hasAttribute('hidden')).toBe(true)
    surface.start(() => {})
    expect(elapsed()?.textContent).toBe('0s elapsed')
    now = 1000 + 95_000
    surface.finish()
    // Frozen at the pass's real duration, not blanked and not left at the last tick.
    expect(elapsed()?.textContent).toBe('1m 35s elapsed')
  })

  it('cancels a pass for real: it aborts the request, and says nothing was saved', async () => {
    const container = document.createElement('div')
    const surface = new ResearchProgressSurface(container)
    const seen: (AbortSignal | undefined)[] = []
    const hanging = vi.fn(
      (_input: unknown, init?: { signal?: AbortSignal }) =>
        new Promise<Response>((_resolve, reject) => {
          seen.push(init?.signal)
          init?.signal?.addEventListener('abort', () => {
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
          })
        }),
    ) as unknown as typeof fetch

    const pass = runResearch(surface, {
      request: { area_id: 'a', force_refresh: false },
      fetchImpl: hanging,
    })
    const cancel = surface.element.querySelector<HTMLButtonElement>('.siyur-research__cancel')!
    expect(cancel.hidden).toBe(false)
    // The signal reached the request — a cancel that only changes the screen is a lie.
    expect(seen[0]).toBeInstanceOf(AbortSignal)

    cancel.click()
    expect(cancel.textContent).toBe('Stopping…')
    await pass

    expect(surface.currentState).toBe('cancelled')
    expect(surface.element.dataset.state).toBe('cancelled')
    // `cancelled`, not `error`: the user stopped it, and `api/areas.py` commits only
    // after the stream completes, so the commons is genuinely unchanged.
    expect(surface.element.querySelector('.siyur-research__result')?.textContent).toMatch(
      /Nothing from this pass was saved/,
    )
    expect(cancel.hidden).toBe(true)
  })

  it('offers no cancel control when the caller owns the signal', async () => {
    // Two things cancelling one request is a race with a wrong answer available.
    const container = document.createElement('div')
    const surface = new ResearchProgressSurface(container)
    const controller = new AbortController()
    const empty = vi.fn(
      async () =>
        new Response(new ReadableStream({ start: (c) => c.close() }), {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
    ) as unknown as typeof fetch
    await runResearch(surface, {
      request: { area_id: 'a', force_refresh: false },
      fetchImpl: empty,
      signal: controller.signal,
    })
    expect(surface.element.querySelector<HTMLButtonElement>('.siyur-research__cancel')!.hidden).toBe(
      true,
    )
  })

  it('holds the plan submit for the length of the proposal', async () => {
    let release: (() => void) | undefined
    const onSubmit = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    const host = document.createElement('div')
    const form = mountPlanForm(host, { onSubmit })
    form.setArea('a-uuid')
    host.querySelector<HTMLInputElement>('[name="date"]')!.value = '2026-08-20'

    const submit = host.querySelector<HTMLButtonElement>('.siyur-plan-form__submit')!
    const first = form.submit()
    expect(form.element.dataset.pending).toBe('true')
    expect(submit.disabled).toBe(true)

    await form.submit() // a second attempt while the first is open
    expect(onSubmit).toHaveBeenCalledTimes(1)

    release?.()
    await first
    expect(form.element.dataset.pending).toBe('false')
    expect(submit.disabled).toBe(false)
  })
})

/* ═══════════════ F-05 — the audit's emblematic defect, in the one place it lived ═══ */

describe('F-05 — a day being produced says so (UX-07)', () => {
  const proposing = (patch: Partial<PlanReviewModel> = {}): PlanReviewModel => ({
    ...EMPTY_PLAN_MODEL,
    approval: { state: 'proposing', approved_at: null, superseded_by: null },
    ...patch,
  })

  it('never renders "No day has been proposed yet" under data-plan-state="proposing"', () => {
    // THE defect: the element carried the right attribute and the opposite sentence.
    const panel = renderPlanPanel(proposing())
    expect(panel.dataset.planState).toBe('proposing')
    const empty = panel.querySelector<HTMLElement>('.siyur-plan__empty')
    expect(empty?.textContent).toBe('This day is still being produced.')
    expect(empty?.dataset.reason).toBe('proposing')
    expect(panel.textContent).not.toMatch(/No day has been proposed yet/)
  })

  it('gives the same answer at the gate as it gives in the panel', () => {
    // The blocked reason used to be "This day has not been saved yet" — true (there is no
    // plan id mid-stream) and a dead end, where the news is *wait*.
    expect(approvability(proposing()).reason).toBe('This day is still being produced.')
    expect(approvability(proposing()).approvable).toBe(false)
  })

  it('does not report a verdict in the past tense before the verdict frame arrives', () => {
    const pending = renderFeasibility(EMPTY_PLAN_MODEL.feasibility, { proposing: true })
    expect(pending.dataset.verdict).toBe('pending')
    expect(pending.textContent).toMatch(/has not been checked yet/)
    const settled = renderFeasibility(EMPTY_PLAN_MODEL.feasibility)
    expect(settled.dataset.verdict).toBe('unreported')
    expect(settled.textContent).toMatch(/Feasibility was not reported/)
  })

  it('still says "none proposed" when nothing is in flight — the two are different facts', () => {
    const idle = renderPlanPanel(EMPTY_PLAN_MODEL)
    expect(idle.querySelector('.siyur-plan__empty')?.textContent).toBe(
      'No day has been proposed yet.',
    )
    expect(idle.querySelector<HTMLElement>('.siyur-plan__empty')?.dataset.reason).toBe('absent')
  })

  it('reaches that state through the surface a user actually drives', () => {
    // `PlanReviewSurface.start()` is what `main.ts` calls before `POST /plans`; asserting
    // only on a hand-built model would leave the live path unproven.
    const host = document.createElement('div')
    const review = mountPlanReview(host)
    review.start()
    expect(review.element.dataset.planState).toBe('proposing')
    expect(review.element.querySelector('.siyur-plan__empty')?.textContent).toBe(
      'This day is still being produced.',
    )
  })

  it('has a visual treatment for the state, not only a correct sentence', async () => {
    // `grep "plan-state" plan.css` returned **nothing** before this pass: the state was
    // styled identically to the app's resting state, so even true copy read as idle.
    expect(strip(await readCss('plan.css'))).toMatch(
      /\.siyur-plan\[data-plan-state='proposing'\]/,
    )
  })
})

/* ══════════════════════════════════════ F-06 — the delimit control speaks ═══ */

describe('F-06 — a name search says what it is doing for the minute it takes (UX-02)', () => {
  const mount = (onDelimit: (area: unknown) => Promise<void>, now = () => 0) => {
    const container = document.createElement('div')
    const control = mountDelimitControl(container, {
      getBounds: () => ({
        getWest: () => 1,
        getSouth: () => 2,
        getEast: () => 3,
        getNorth: () => 4,
      }),
      onDelimit: onDelimit as never,
      now,
      tickMs: 1000,
    })
    return { container, control, root: control.element }
  }

  it('states the wait, counts it, and disables both paths while it runs', async () => {
    let release: (() => void) | undefined
    let now = 0
    const { root } = mount(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
      () => now,
    )
    root.querySelector<HTMLInputElement>('.siyur-delimit__input')!.value = 'somewhere'
    root.querySelector<HTMLFormElement>('.siyur-delimit__search')!.dispatchEvent(
      new Event('submit'),
    )

    const busy = root.querySelector<HTMLElement>('.siyur-delimit__busy')!
    expect(busy.hidden).toBe(false)
    expect(busy.textContent).toMatch(/can take up to a minute/)
    expect(busy.getAttribute('role')).toBe('status')
    expect(root.querySelector<HTMLElement>('.siyur-delimit__elapsed')?.textContent).toBe('0s')
    expect(root.querySelector<HTMLButtonElement>('.siyur-delimit__submit')!.disabled).toBe(true)
    expect(
      root.querySelector<HTMLButtonElement>('.siyur-delimit__viewport')!.getAttribute('aria-busy'),
    ).toBe('true')

    now = 42_000
    release?.()
    await Promise.resolve()
    await Promise.resolve()
    expect(busy.hidden).toBe(true)
    expect(root.dataset.busy).toBe('false')
  })

  it('says something different for the 0.18s path than for the 61s one', async () => {
    // Both are "delimit", and telling a user that a bbox resolve "can take up to a
    // minute" would be as much of an invention as saying nothing about the name search.
    const { root } = mount(() => Promise.resolve())
    root.querySelector<HTMLButtonElement>('.siyur-delimit__viewport')!.click()
    // Synchronously after the click, before the microtask that clears it.
    const busy = root.querySelector<HTMLElement>('.siyur-delimit__busy')!
    expect(busy.textContent).toMatch(/Reading the area shown on the map/)
    expect(busy.textContent).not.toMatch(/up to a minute/)
  })
})
