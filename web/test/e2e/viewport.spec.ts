import { expect, test, type Page } from '@playwright/test'

/**
 * DU-06a — the rendered-viewport gate (FAIL-012's guardrail).
 *
 * Nine deliverable units merged with green CI and **not one of them was ever
 * rendered below desktop width.** `docs/design/ux-handoff/README.md` opens with a
 * mobile-first contract — 375–430 px, tap targets ≥ 44 px, body ≥ 14 px — and
 * nothing in the repo measured it, so the contract was documentation rather than a
 * requirement. Every existing web test runs in **jsdom, which has no layout**:
 * `getBoundingClientRect()` returns zeros there, so occlusion, tap size and type
 * scale are all literally unobservable. That is how "the tests pass" and "the
 * product is unusable on a phone" were both true at the same time.
 *
 * **This suite is designed to land RED.** Marking a known failure `test.fail()` is
 * the same discipline `airplane.spec.ts` uses for the zero-requests assertion: the
 * finding is kept, CI stays green on a red gate, and the marker **fails loudly the
 * moment the assertion starts passing** — so a DU-06b task cannot land its fix
 * without being told to delete the line. A gate nobody has watched fail is not yet
 * a control (FAIL-014).
 *
 * ## Why Playwright and not the audit's iframe harness
 *
 * The audit could not resize Chrome below ~400 px on macOS and worked around it
 * with a same-origin iframe, which makes media queries respond to the iframe width.
 * That workaround is **not needed here**: Playwright sets the viewport
 * programmatically at the browser level, so 375 px is a real 375 px, with a real
 * `window.innerWidth` and real media-query evaluation.
 *
 * ## What this gate does and does not see
 *
 * It runs against the **built, previewed app with no API behind it**, which is the
 * only state reachable deterministically in CI. So it covers the delimit screen as
 * loaded: the control pill, the plan panel and its form, the bottom sheet, and the
 * map's attribution control. It does **not** yet cover the coverage card, the
 * research progress or a rendered day — those need `POST /areas` and `POST /plans`,
 * and the areas contract is being rewritten in another PR as this lands. Stated
 * plainly rather than left to be discovered: **the surfaces behind the API join the
 * gate in Wave 2**, and until they do, a green run here is not a claim about them.
 */

// ── the contract, from docs/design/ux-handoff/README.md ────────────────────────

/** Tap targets. 44 px is the handoff's floor and the WCAG 2.5.5 target size. */
const TAP_TARGET_MIN = 44

/** Body copy. */
const BODY_TYPE_MIN = 14

/**
 * Provenance micro-stamps are allowed to be smaller than body copy — they are
 * labels on a value, not prose, and the handoff's signature motif is explicitly a
 * mono micro-chip. **Listed explicitly rather than matched by a pattern**, so that
 * widening the exemption is a visible diff and not a regex growing a branch.
 */
const CHIP_TYPE_MIN = 11
const CHIP_ALLOWLIST = [
  '.siyur-chip', // provenance stamp beside a value (ADR-0019)
  '.siyur-value__label', // the uppercase field label in a stamped value
  '.siyur-research__chip', // the pulsing "live" chip on the research strip
  '.siyur-research__source', // per-source pills on the research strip
] as const

/**
 * Controls excluded from the tap-target and reachability rules, each with a reason.
 *
 * - `.siyur-marker` — 1,914 map dots. Marker density is **F-11, deferred to M2**;
 *   holding them to 44 px would assert a decision that was explicitly not taken.
 * - `.maplibregl-canvas` — a drawing surface carrying `tabindex="0"`, not a control.
 *   Its centre is legitimately covered by whatever floats over the map.
 */
const NOT_A_CONTROL = ['.siyur-marker', '.siyur-marker__pin', '.maplibregl-canvas'] as const

const INTERACTIVE = 'button, [role="button"], a[href], input, select, textarea, summary'

const PHONES = [
  { label: '375×667', width: 375, height: 667 },
  { label: '390×844', width: 390, height: 844 },
  { label: '430×932', width: 430, height: 932 },
] as const

/** T-05 asserts the attribution at desktop too — the audit found it occluded at 1440. */
const DESKTOP = { label: '1440×900', width: 1440, height: 900 } as const

// ── in-page probes ─────────────────────────────────────────────────────────────

interface Box {
  readonly label: string
  readonly width: number
  readonly height: number
}

interface Occlusion extends Box {
  readonly at: string
  /** `is covered by` (painted over) or `is clipped out of` (an overflow scroller). */
  readonly how: string
  readonly occludedBy: string
}

interface TypeSize extends Box {
  readonly fontSize: number
  readonly floor: number
}

interface Report {
  readonly controls: number
  /** How many controls had to be scrolled to before hit-testing — see the T-02 note. */
  readonly scrolledIntoView: number
  /** Painted over by something else, after scrolling into view. */
  readonly covered: Occlusion[]
  /** Rendered outside an overflow ancestor's box — reachable only by scrolling a panel. */
  readonly clipped: Occlusion[]
  /** Extends past the viewport horizontally. Never legitimate. */
  readonly overflowing: Box[]
  readonly undersized: Box[]
  readonly smallType: TypeSize[]
  readonly scrollWidth: number
  readonly clientWidth: number
  readonly attribution: Occlusion | { readonly label: string; readonly reachable: true } | null
}

/**
 * Load the app and measure it.
 *
 * One `evaluate` per page rather than one per assertion: the measurements are all
 * taken from a single settled layout, so a T-02 failure and a T-03 failure are
 * always statements about the same rendered frame.
 */
async function measure(page: Page): Promise<Report> {
  await page.goto('/')
  // The shell is appended synchronously by main.ts, but MapLibre mounts its
  // attribution control on the next frame. Waiting for both means a failure here is
  // never "the app had not finished rendering".
  await page.waitForSelector('.siyur-delimit__viewport', { state: 'attached' })
  await page.waitForSelector('.maplibregl-ctrl-attrib', { state: 'attached' })
  await page.waitForFunction(() => document.fonts.status === 'loaded').catch(() => undefined)

  return page.evaluate(
    ({ interactive, notControl, chips, tapMin, bodyMin, chipMin }) => {
      const describe = (el: Element): string => {
        const tag = el.tagName.toLowerCase()
        const raw = typeof el.className === 'string' ? el.className.trim() : ''
        const cls = raw ? `.${raw.split(/\s+/).join('.')}` : ''
        const text = (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 44)
        // **Visible text wins over `title`.** The delimit button's title is
        // "Delimit the area currently shown on the map"; what is written on it — and
        // what the audit, ADR-0035 and every conversation about this call it — is
        // "Use this view". A gate that names a control by a string nobody can see
        // makes the reader search for it.
        const name =
          text || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || ''
        return `${tag}${cls}${name ? ` "${name}"` : ''}`
      }

      /**
       * The nearest ancestor that clips this point away.
       *
       * Without this the report blames whatever happens to be painted underneath —
       * and the honest answer for the plan form is not "the map canvas is on top of
       * it" but "`.siyur-plan-panel` is `max-height: 40vh` with `overflow-y: auto`,
       * so its own content extends past its box and is not painted at all"
       * (ADR-0035 measured 294 px of clipped content at 390 px). Naming the
       * scroller points at the fix; naming the canvas points at the wrong file.
       */
      const clippedBy = (el: Element, x: number, y: number): string | null => {
        for (let node = el.parentElement; node; node = node.parentElement) {
          const style = getComputedStyle(node)
          if (style.overflowX === 'visible' && style.overflowY === 'visible') continue
          const box = node.getBoundingClientRect()
          if (x < box.left || x > box.right || y < box.top || y > box.bottom) {
            return `${describe(node)} (overflow-y: ${style.overflowY}, clipped at ${Math.round(box.top)}–${Math.round(box.bottom)})`
          }
        }
        return null
      }

      const visible = (el: Element): boolean =>
        (el as HTMLElement).checkVisibility?.({
          checkOpacity: true,
          checkVisibilityCSS: true,
        }) ?? false

      const excluded = (el: Element): boolean => notControl.some((sel) => el.matches(sel))

      const controls = [...document.querySelectorAll(interactive)].filter(
        (el) => visible(el) && !excluded(el),
      )

      /**
       * Is any of this element's own boxes hit-testable at its centre?
       *
       * `getClientRects()` rather than the bounding box: an inline link wrapped
       * across two lines has a *union* rect whose centre falls in the gap between
       * the lines, so hit-testing the union reports a perfectly tappable link as
       * occluded. The ODbL credit is exactly such a link and wraps at 375 px as soon
       * as `/sites` adds a second credit — a false red on the licence assertion is
       * the fastest way to teach people to ignore it.
       */
      const hittable = (el: Element): { ok: boolean; at: string; hit: Element | null } => {
        const rects = [...el.getClientRects()]
        const boxes = rects.length > 0 ? rects : [el.getBoundingClientRect()]
        let last: { at: string; hit: Element | null } = { at: '—', hit: null }
        for (const box of boxes) {
          if (box.width === 0 || box.height === 0) continue
          const x = box.left + box.width / 2
          const y = box.top + box.height / 2
          const hit = document.elementFromPoint(x, y)
          last = { at: `${Math.round(x)},${Math.round(y)}`, hit }
          if (hit && (hit === el || el.contains(hit))) return { ok: true, ...last }
        }
        return { ok: false, ...last }
      }

      // ── T-02 · reachability, and T-04's per-element half ───────────────────
      //
      // **A centre outside the viewport is never skipped.** The first draft of this
      // probe `continue`d on off-screen centres, which would have made the whole
      // assertion blind the moment ADR-0035 lands: in a scroll column every control
      // below the fold has `cy >= innerHeight`, so the checked set would silently
      // shrink to whatever happens to be above the fold — while T-10 deleted the
      // `test.fail()` markers on the strength of that green. Instead:
      //
      //   · **vertically** off-screen → `scrollIntoView` and hit-test there. Content
      //     below the fold is normal in a scroll column and in a scrollable panel;
      //     what matters is whether it is reachable *once you scroll to it*. Fixed
      //     overlays stay fixed, so a sticky header covering scrolled content is
      //     still caught — which is the defect class that matters.
      //   · **horizontally** out of bounds → a violation in its own right, reported
      //     to T-04. There is no scroll gesture that legitimises it, and
      //     `scrollWidth === clientWidth` cannot see it when the offender is
      //     `position: fixed` (which every panel in this app is).
      //
      // Clipping is reported separately from covering, because they have different
      // fixes and different fixers — see `clipped` below.
      const covered: Occlusion[] = []
      const clipped: Occlusion[] = []
      const overflowing: Box[] = []
      let scrolledIntoView = 0

      for (const el of controls) {
        const before = el.getBoundingClientRect()

        // Horizontal bounds, measured before any scrolling.
        if (before.right > innerWidth + 0.5 || before.left < -0.5) {
          overflowing.push({
            label: describe(el),
            width: Math.round(before.width),
            height: Math.round(before.height),
          })
        }

        // Clipping is a property of where the element is *rendered*, so it is
        // detected before scrolling — scrolling the inner panel would hide it.
        const clip = clippedBy(el, before.left + before.width / 2, before.top + before.height / 2)
        if (clip) {
          clipped.push({
            label: describe(el),
            width: Math.round(before.width),
            height: Math.round(before.height),
            at: `${Math.round(before.left + before.width / 2)},${Math.round(before.top + before.height / 2)}`,
            how: 'is clipped out of',
            occludedBy: clip,
          })
          // Reported as clipped and *not* also as covered. Whatever `elementFromPoint`
          // returns at a clipped control's coordinates is the map canvas showing
          // through the hole — true, useless, and it points at the wrong file. One
          // defect, one report, naming the scroller that causes it.
          continue
        }

        const offscreen = before.bottom < 0 || before.top >= innerHeight
        if (offscreen) {
          el.scrollIntoView({ block: 'center', inline: 'nearest' })
          scrolledIntoView += 1
        }

        const probe = hittable(el)
        if (probe.ok) continue
        const r = el.getBoundingClientRect()
        covered.push({
          label: describe(el),
          width: Math.round(r.width),
          height: Math.round(r.height),
          at: probe.at,
          how: 'is covered by',
          occludedBy: probe.hit ? describe(probe.hit) : '(nothing painted there)',
        })
      }
      // Undo the probe's scrolling so the type/attribution scans below measure the
      // page as it loads, not as this loop left it.
      scrollTo(0, 0)

      // ── T-03a · tap targets ────────────────────────────────────────────────
      const undersized: Box[] = []
      for (const el of controls) {
        const r = el.getBoundingClientRect()
        if (r.width >= tapMin && r.height >= tapMin) continue
        undersized.push({
          label: describe(el),
          width: Math.round(r.width * 10) / 10,
          height: Math.round(r.height * 10) / 10,
        })
      }

      // ── T-03b · type scale ─────────────────────────────────────────────────
      // Only elements holding their own text: a container inherits its child's
      // problem and would report the same violation twice.
      const smallType: TypeSize[] = []
      for (const el of document.querySelectorAll('body *')) {
        if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue
        // Marker labels are 12px and there are ~1,900 of them once a researched area
        // renders. Holding them to the body floor would bury every real finding under
        // marker noise, for a decision (marker density, F-11) the audit deferred to
        // M2. Excluded on the same grounds as the tap-target rule, not silently.
        if (excluded(el) || el.matches('.siyur-marker__name')) continue
        if (!visible(el)) continue
        const ownText = [...el.childNodes].some(
          (n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? '').trim().length > 0,
        )
        if (!ownText) continue
        const floor = chips.some((sel) => el.matches(sel)) ? chipMin : bodyMin
        const fontSize = Number.parseFloat(getComputedStyle(el).fontSize)
        if (fontSize >= floor) continue
        const r = el.getBoundingClientRect()
        smallType.push({
          label: describe(el),
          width: Math.round(r.width),
          height: Math.round(r.height),
          fontSize,
          floor,
        })
      }

      // ── T-05 · the ODbL attribution specifically ───────────────────────────
      // Constitution Article V / ODbL: the credit must render on every map. A
      // credit painted underneath an opaque sheet is not rendered to anyone, which
      // makes this a licence-compliance assertion, not a cosmetic one.
      /**
       * What is painted over this element, *whether or not it accepts a tap.*
       *
       * `elementFromPoint` answers "what would a finger hit", and it ignores anything
       * with `pointer-events: none`. For a tap target that is the right question; for
       * a **licence obligation it is the wrong one** — an opaque non-interactive
       * overlay (a toast, a scrim, `.siyur-marker__name` already uses exactly this
       * pattern) hides the ODbL credit completely while the hit test reports it
       * perfectly reachable. Article V is about what a reader can see.
       *
       * Stacking order is approximated rather than computed: an element counts as
       * above `el` if its resolved `z-index` is higher, or equal/auto and it comes
       * later in document order. That is a heuristic — it does not model nested
       * stacking contexts — but it is deliberately biased toward *reporting* a
       * cover, which is the safe direction for a compliance check.
       */
      const paintedOver = (el: Element, x: number, y: number): string | null => {
        const zOf = (node: Element): number => {
          const z = Number.parseInt(getComputedStyle(node).zIndex, 10)
          return Number.isNaN(z) ? 0 : z
        }
        const mine = zOf(el)
        for (const node of document.querySelectorAll('body *')) {
          if (node === el || el.contains(node) || node.contains(el)) continue
          if (!visible(node)) continue
          if (notControl.some((sel) => node.matches(sel))) continue
          const style = getComputedStyle(node)
          const opaque =
            (style.backgroundColor !== '' &&
              style.backgroundColor !== 'transparent' &&
              !/^rgba\(0,\s*0,\s*0,\s*0\)$/.test(style.backgroundColor)) ||
            style.backgroundImage !== 'none'
          if (!opaque) continue
          const box = node.getBoundingClientRect()
          if (x < box.left || x > box.right || y < box.top || y > box.bottom) continue
          const theirs = zOf(node)
          const above =
            theirs > mine ||
            (theirs === mine &&
              (el.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0)
          if (above) return `${describe(node)} (z-index ${theirs} vs ${mine})`
        }
        return null
      }

      const attrib = document.querySelector('.maplibregl-ctrl-attrib')
      let attribution: Report['attribution'] = null
      if (attrib && visible(attrib)) {
        const r = attrib.getBoundingClientRect()
        const cx = r.left + r.width / 2
        const cy = r.top + r.height / 2
        const probe = hittable(attrib)
        const clip = clippedBy(attrib, cx, cy)
        const painted = paintedOver(attrib, cx, cy)
        attribution =
          probe.ok && !painted
            ? { label: describe(attrib), reachable: true }
            : {
                label: describe(attrib),
                width: Math.round(r.width),
                height: Math.round(r.height),
                at: probe.ok ? `${Math.round(cx)},${Math.round(cy)}` : probe.at,
                how: clip ? 'is clipped out of' : probe.ok ? 'is painted over by' : 'is covered by',
                occludedBy:
                  clip ?? (probe.ok ? painted : (painted ?? describe(probe.hit ?? attrib))) ?? '(unknown)',
              }
      }

      return {
        controls: controls.length,
        scrolledIntoView,
        covered,
        clipped,
        overflowing,
        undersized,
        smallType,
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        attribution,
      }
    },
    {
      interactive: INTERACTIVE,
      notControl: NOT_A_CONTROL as readonly string[],
      chips: CHIP_ALLOWLIST as readonly string[],
      tapMin: TAP_TARGET_MIN,
      bodyMin: BODY_TYPE_MIN,
      chipMin: CHIP_TYPE_MIN,
    },
  )
}

const list = (rows: readonly string[]): string => rows.map((r) => `  · ${r}`).join('\n')

/**
 * **The red state of `main` at 52e2fba, measured rather than assumed.**
 *
 * Each entry is a `test.fail()`: Playwright treats the test as passing while it
 * fails, and **fails the run the moment it starts passing**. So this table is a
 * countdown — a DU-06b task that fixes the layout cannot merge without deleting the
 * row it fixed, which is the property a "we'll add the test after" gate never has.
 *
 * The widths are not uniform, and that is the finding, not an inconsistency:
 * `Use this view` is occluded at 375 and 390 but **reachable at 430**, because the
 * control row only wraps to a second line under the plan panel once it runs out of
 * inline space. A single `test.fail()` across all three widths would have hidden
 * that the defect is width-dependent.
 *
 * Recorded per (assertion, width) so that a partial fix shows up as a partial pass.
 */
const KNOWN_RED: Readonly<Record<string, readonly string[]>> = {
  // T-10 (F-01, ADR-0035 flow column) deletes these six — three T-02 and three T-02b.
  //
  // T-02, true occlusion (something painted on top):
  //   375 & 390: 2 of 13 — the ODbL link under `.siyur-sheet`, and
  //        `Use this view` under the plan panel's own `Plan a day` heading
  //   430: 1 of 13 — **`Use this view` is REACHABLE here.** The control row only
  //        wraps under the panel once it runs out of inline space, so the audit's
  //        headline defect is genuinely width-dependent and a single marker across
  //        all three widths would have hidden that.
  // F-01 (ADR-0035 flow column) cleared 375 and 390. **430 did not flip**: the control
  // row only wraps under the panel once it runs out of inline space, so at 430 the
  // occlusion survives the flow column. Keyed per width, which is what made that
  // visible — a single marker across all three would have been deleted wholesale here.
  'T-02': ['430×932'],
  // T-02b, clipped out of `.siyur-plan-panel`'s 40vh `overflow-y: auto` box:
  //   375: 5 of 13 · 390: 4 of 13 · 430: 2 of 13 — `Plan this day →` and
  //   `Approve this day` are below the fold of an inner scroller at every width.
  // T-02b is GREEN at every width: the 40vh `overflow-y: auto` box is gone, so nothing
  // is clipped out of an inner scroller any more. Row deleted rather than emptied.

  // T-11 (F-02) deletes these six. 11 of 13 controls under 44px at every width, and
  // 17 text elements below their floor (9px form labels through 13.5px buttons).
  //
  // Exactly two controls pass, and only one of them on purpose:
  //   · `.siyur-library__toggle` — 342×44, built to the floor (#128)
  //   · `textarea.siyur-plan-form__input` — 342×50, which clears it by accident of
  //     `rows="2"` × inherited line-height. Worth naming, because a later
  //     `line-height` tweak would move that number with nothing to explain why.
  'T-03-tap': ['375×667', '390×844', '430×932'],
  'T-03-type': ['375×667', '390×844', '430×932'],
  // T-12 (F-03) deletes these four. `.siyur-sheet` is bottom-anchored with
  // `inset-inline: 0` at EVERY width, so the ODbL credit is painted underneath it on
  // desktop too — the only finding in the audit that also fails at 1440.
  // T-05 is GREEN at all four widths including 1440, so the ODbL obligation is now
  // discharged everywhere it is displayed. Row deleted.

}

/**
 * `SIYUR_GATE_NO_XFAIL=1 pnpm exec playwright test` drops every marker and reports
 * the **true** red state.
 *
 * This exists so "demonstrate it red" stays a command rather than a manual edit
 * somebody did once. The numbers in the PR body, in FAIL-012 and in the table above
 * were all produced by this switch, and anyone can reproduce or refresh them — which
 * is the difference between a measurement and a claim about a measurement.
 */
const NO_XFAIL = process.env.SIYUR_GATE_NO_XFAIL === '1'

const isKnownRed = (assertion: string, width: string): boolean =>
  !NO_XFAIL && (KNOWN_RED[assertion]?.includes(width) ?? false)

// ── the gate ───────────────────────────────────────────────────────────────────

for (const phone of PHONES) {
  test.describe(`${phone.label}`, () => {
    test.use({ viewport: { width: phone.width, height: phone.height } })

    /**
     * **T-02 — every control is reachable where it is painted.**
     *
     * The audit's headline finding: `.siyur-plan-panel` is `z-index: 3` starting at
     * `inset-block-start: 58px`, and `.siyur-controls` is `z-index: 2` with a
     * wrapped second row at y ≈ 66. So **`Use this view` — the delimit path that
     * resolves in 0.18 s — is painted underneath the plan panel and cannot be
     * tapped in any scroll state.** The only reachable delimit control is the
     * search pill, which takes 61.6 s to return a 404.
     *
     * **T-10 (the flow column, ADR-0035) turns this green.**
     */
    test('T-02 · every visible control is reachable at its own centre', async ({ page }) => {
      if (isKnownRed('T-02', phone.label)) test.fail()
      const report = await measure(page)
      expect(report.controls, 'no controls found — the app did not render').toBeGreaterThan(0)
      expect(
        report.covered,
        `${report.covered.length} of ${report.controls} controls are painted under something else ` +
          `(${report.scrolledIntoView} were scrolled into view first):\n` +
          list(report.covered.map((o) => `${o.label} at (${o.at}) ${o.how} ${o.occludedBy}`)),
      ).toEqual([])
    })

    /**
     * **T-02b — nothing is stranded outside its own scroller.**
     *
     * Split from T-02 because it is a different defect with a different fix: these
     * controls are not painted *under* anything, they are rendered past the edge of
     * `.siyur-plan-panel`'s `max-height: 40vh` `overflow-y: auto` box and reachable
     * only by scrolling an inner panel that offers no affordance that it scrolls
     * (ADR-0035 measured 294 px of clipped content at 390 px).
     *
     * Kept as its own assertion so that T-10 flipping one and not the other is
     * visible rather than averaged away.
     */
    test('T-02b · no control is clipped out of an inner scroller', async ({ page }) => {
      if (isKnownRed('T-02b', phone.label)) test.fail()
      const report = await measure(page)
      expect(report.controls, 'no controls found — the app did not render').toBeGreaterThan(0)
      expect(
        report.clipped,
        `${report.clipped.length} of ${report.controls} controls are rendered outside their scroller:\n` +
          list(report.clipped.map((o) => `${o.label} at (${o.at}) ${o.how} ${o.occludedBy}`)),
      ).toEqual([])
    })

    /** **T-03a — tap targets. T-11 turns this green.** */
    test('T-03 · every visible control is at least 44×44 px', async ({ page }) => {
      if (isKnownRed('T-03-tap', phone.label)) test.fail()
      const report = await measure(page)
      expect(report.controls, 'no controls found — the app did not render').toBeGreaterThan(0)
      expect(
        report.undersized,
        `${report.undersized.length} of ${report.controls} controls are under the ${TAP_TARGET_MIN}px floor:\n` +
          list(report.undersized.map((u) => `${u.label} — ${u.width}×${u.height}`)),
      ).toEqual([])
    })

    /** **T-03b — type scale. T-11 turns this green.** */
    test(`T-03 · body text is at least ${BODY_TYPE_MIN}px (chips ${CHIP_TYPE_MIN}px)`, async ({
      page,
    }) => {
      if (isKnownRed('T-03-type', phone.label)) test.fail()
      const report = await measure(page)
      expect(
        report.smallType,
        `${report.smallType.length} text elements are below their floor:\n` +
          list(report.smallType.map((t) => `${t.label} — ${t.fontSize}px (floor ${t.floor}px)`)),
      ).toEqual([])
    })

    /**
     * **T-04 — nothing runs off the side.**
     *
     * Passes today, and landing it now locks in a property the app holds so the
     * flow-column rewrite cannot quietly break it. But the document-level half of
     * this assertion is **structurally incapable of failing right now**, and saying
     * so is the difference between a gate and a decoration: `.siyur-controls`,
     * `.siyur-sheet`, `.siyur-plan-panel` and `.siyur-disambiguation-host` are all
     * `position: fixed`, and `#map` is absolute inside MapLibre's `overflow: hidden`
     * container — so nothing in the app contributes to the document's scrollable
     * overflow at any width, and `scrollWidth === clientWidth` is a tautology.
     *
     * It stops being one after T-10 puts real content in normal flow. Until then the
     * **per-element** check below is the half that can actually catch something, and
     * it is the only one that can see a `position: fixed` panel hanging off the
     * inline edge — which is precisely how this app would regress.
     */
    test('T-04 · nothing overflows the viewport horizontally', async ({ page }) => {
      const report = await measure(page)
      expect(
        report.overflowing,
        `${report.overflowing.length} controls extend past the ${report.clientWidth}px viewport:\n` +
          list(report.overflowing.map((o) => `${o.label} — ${o.width}×${o.height}`)),
      ).toEqual([])
      expect(
        report.scrollWidth,
        `horizontal overflow: scrollWidth ${report.scrollWidth} > clientWidth ${report.clientWidth}`,
      ).toBe(report.clientWidth)
    })

    /**
     * **T-06 — a map product gives the map a usable share of the screen.**
     *
     * Ported from `mobile-layout.spec.ts`, which this gate otherwise superseded; it was
     * the one assertion that suite made and this one did not. UX-10 measured the map at
     * **154 px — 18.3 %** of a 390×844 screen with **0 of 957** markers reachable.
     *
     * Deliberately NOT "the element is 844 px tall". The old full-bleed map *did* measure
     * 390×844 and was still unusable, because three layers were painted over all of it —
     * so element size answers a different question from the one that matters. What is
     * measured is the band a tap actually lands on: sample down the map's centre line and
     * count the rows where `elementFromPoint` returns the map itself.
     *
     * The floor is 40 %, not the 55 % the flow column currently achieves. A gate should
     * fail when the product becomes unusable, not whenever a layout is retuned.
     */
    test('T-06 · the map gets a usable share of the screen', async ({ page }) => {
      // Same navigation and waits `measure()` performs. Without them this evaluated
      // against a blank page and reported 0.0% — which the gate caught on its first run,
      // and which is exactly the vacuous pass a weaker assertion would have shipped.
      await page.goto('/')
      await page.waitForSelector('.siyur-delimit__viewport', { state: 'attached' })
      await page.waitForSelector('.maplibregl-ctrl-attrib', { state: 'attached' })

      const share = await page.evaluate(() => {
        const map = document.getElementById('map')
        if (!map) return 0
        const box = map.getBoundingClientRect()
        let reachable = 0
        const step = 8
        for (let y = Math.max(0, box.y); y < Math.min(box.bottom, innerHeight); y += step) {
          const top = document.elementFromPoint(Math.min(box.x + box.width / 2, innerWidth - 1), y)
          if (top && map.contains(top)) reachable += step
        }
        return reachable / innerHeight
      })
      expect(
        share,
        `the map is reachable across only ${(share * 100).toFixed(1)}% of the viewport height`,
      ).toBeGreaterThanOrEqual(0.4)
    })
  })
}

/**
 * **T-07 — a control that issues a request must visibly say something.**
 *
 * FAIL-013's guardrail (2), which that entry does not close without. It could only ever go
 * green once the pending states of F-05 existed, which is why it lands here rather than with
 * the entry itself.
 *
 * The defect it catches: *the app knew something and did not say it.* `Find` issued a request,
 * the server answered `404` with twenty disambiguation candidates, and the UI stayed exactly as
 * it was — so a user could not distinguish "still working" from "failed" from "ignored me".
 * UX-02, UX-06, UX-07 and UX-13 are that same defect wearing different clothes.
 *
 * Deliberately weak about *what* appears and strict about *something* happening. A result, a
 * rendered error, or an explicit pending indicator all pass; **unchanged is a failure**.
 * Anything stronger would pin one design, and this has to survive the surface being redesigned.
 *
 * It passes here on the *error* path: with no session the request 401s, and saying so is the
 * correct behaviour. A gate needing a happy path would need a fixture, and would then be
 * testing the fixture.
 */
test.describe('T-07 · a request-triggering control reaches a terminal visible state', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('activating `Find` changes the DOM within a bounded time', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('.siyur-delimit__search', { state: 'attached' })

    const region = '.siyur-delimit'
    const before = await page.locator(region).innerHTML()

    await page.locator('.siyur-delimit__input').fill('an area that does not exist anywhere')
    await page.locator('.siyur-delimit__submit').click()

    // 10s: long enough for a slow answer, short enough that "nothing happened" is a
    // conclusion rather than impatience.
    await expect
      .poll(async () => (await page.locator(region).innerHTML()) !== before, {
        timeout: 10_000,
        message:
          'the delimit region was byte-identical 10s after activating `Find` — no pending ' +
          'state, no result, no error. The app either knew something and did not say it, ' +
          'or issued no request at all.',
      })
      .toBe(true)
  })
})

/**
 * **T-05 — the ODbL attribution, at every width including desktop.**
 *
 * Separated from T-02 and run at 1440 as well because it is the one finding in the
 * audit that is a **licence obligation rather than a quality problem**, and because
 * it is the only one that also fails on desktop — `.siyur-sheet` is bottom-anchored
 * with `inset-inline: 0` at every width, and the attribution control sits
 * bottom-right underneath it. Constitution Article V requires the credit on every
 * map; a credit nobody can see is not rendered.
 *
 * **T-12 turns this green.**
 */
test.describe('T-05 · ODbL attribution', () => {
  for (const size of [...PHONES, DESKTOP]) {
    test.describe(`${size.label}`, () => {
      test.use({ viewport: { width: size.width, height: size.height } })

      test('the attribution is visible and not covered', async ({ page }) => {
        if (isKnownRed('T-05', size.label)) test.fail()
        const report = await measure(page)
        expect(report.attribution, 'no attribution control rendered at all').not.toBeNull()
        const attribution = report.attribution!
        expect(
          'reachable' in attribution ? null : attribution,
          'reachable' in attribution
            ? ''
            : `the ODbL credit at (${attribution.at}) ${attribution.how} ${attribution.occludedBy} — ` +
              `Constitution Article V requires it to render on every map`,
        ).toBeNull()
      })
    })
  }
})

/**
 * **NEGATIVE CONTROL — the most important test in this file.**
 *
 * Every assertion above is a claim that the probe *would* have noticed. This repo
 * has already shipped an assertion that was green because the code was wrong
 * (FAIL-007), and `airplane.spec.ts` carries the same guard for the same reason. So
 * the occlusion probe is proven able to catch an occlusion before its silence is
 * trusted to mean anything.
 */
test.describe('negative control', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the reachability probe catches an occlusion it is shown', async ({ page }) => {
    await page.goto('/')
    await page.waitForSelector('.siyur-delimit__search', { state: 'attached' })

    const caught = await page.evaluate(() => {
      const target = document.querySelector('.siyur-delimit__input')
      if (!target) return { mounted: false, occluded: false }
      const r = target.getBoundingClientRect()
      const cover = document.createElement('div')
      cover.style.cssText =
        `position:fixed;z-index:99999;inset-block-start:${r.top}px;` +
        `inset-inline-start:${r.left}px;width:${r.width}px;height:${r.height}px;background:#000`
      document.body.append(cover)
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      const occluded = !(hit && (hit === target || target.contains(hit)))
      cover.remove()
      return { mounted: true, occluded }
    })

    expect(caught.mounted, 'the search input never mounted').toBe(true)
    expect(
      caught.occluded,
      'the probe failed to notice a deliberately covered control — every other ' +
        'assertion in this file is therefore worthless',
    ).toBe(true)
  })
})
