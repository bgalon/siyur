import { expect, test, type Page } from '@playwright/test'

/**
 * Phase B — the layout assertion that jsdom cannot make.
 *
 * `test/mobile-usability.test.ts` asserts the *causes* (the numbers in the stylesheets)
 * and the *behaviour* (the states). Neither is a measurement: **jsdom performs no
 * layout**, so every rect there is zero, and that is precisely how a product with 370
 * green tests turned out to be unusable on a phone. This file measures the **built** app
 * in Chromium at real phone viewports and asserts on rendered pixels.
 *
 * ## What each assertion is standing in for
 *
 * | Assertion | The finding it closes |
 * |---|---|
 * | nothing wider than the viewport | the classic mobile failure; the audit found none — keep it that way |
 * | attribution hit-tests to itself | **UX-11**, the audit's one *licence* finding (Constitution Article V) |
 * | every control hit-tests to itself | **UX-01** — `Use this view` was unreachable in every scroll state |
 * | every control ≥ 44px | **UX-12** — 11 of 13 were under it |
 * | every rendered font ≥ 11px, body ≥ 14px | **UX-15** — chips rendered at 8.5px |
 * | the map holds ≥ 40% of the screen | **UX-10** — it held 18.3%, with 0 of 957 markers reachable |
 *
 * ## Why `elementFromPoint` and not a rect comparison
 *
 * Two rects overlapping does not mean the top one is *painted* over the bottom one —
 * z-index, transparency and pointer-events all intervene. The question a thumb asks is
 * "what does this tap hit", and `document.elementFromPoint` at a control's own centre is
 * that question, exactly. It is also what the audit used, so these numbers are directly
 * comparable to the ones in `docs/design/ux-audit-2026-08-15.md` § 5.
 *
 * **Signed out is the honest default here.** `GET /sites` answers `401` without a session
 * cookie, so no markers render — which is fine, because every assertion below is about
 * the *shell*, and the shell mounts identically either way (UX-03). Set `SIYUR_COOKIE` to
 * run the same measurements against a populated map.
 */

/** iPhone SE and iPhone 14 — the narrow end and the common end of the spec's 375–430px. */
const VIEWPORTS = [
  { name: '375x667 (SE)', width: 375, height: 667 },
  { name: '390x844 (14)', width: 390, height: 844 },
] as const

/** The spec's floors (`docs/design/ux-handoff/README.md` § Typography). */
const TAP_FLOOR = 44
const BODY_FLOOR = 14
const STAMP_FLOOR = 11

/**
 * Two documented exceptions, by selector, each argued in `src/style.css`:
 *
 * - the **ODbL attribution link** — a legally required credit on the map, not a task
 *   control; a 44px credit strip on a 390px phone eats the map it credits;
 * - the **site markers** — 11px dots at a coordinate. Their size is the geometry, and
 *   growing them is the clustering work in F-11 (Tier 3), not a padding change.
 */
const TAP_EXCEPTIONS = '.maplibregl-ctrl-attrib, .siyur-marker'

interface Measured {
  readonly selector: string
  readonly text: string
  readonly width: number
  readonly height: number
  readonly hit: string
  /** False for the two documented exceptions above — decided in the page, by selector. */
  readonly graded: boolean
}

/**
 * Every interactive control, with its rendered box and what a tap at its centre hits.
 *
 * **Each control is scrolled into view before it is hit-tested**, and that is a
 * correctness point rather than a convenience. The phone layout is now a single scroll
 * column, so most controls are below the first fold — which is *reachable*, and treating
 * "outside the initial viewport" as a failure would flag the whole design as broken. The
 * audit tested "in both scroll states" for the same reason; the difference it was
 * looking for is a control that **no** scroll position can reach, which is what
 * `Use this view` was.
 */
async function measureControls(page: Page): Promise<Measured[]> {
  return page.evaluate((exceptions: string) => {
    const name = (el: Element): string => {
      const cls = typeof el.className === 'string' ? el.className.split(/\s+/)[0] : ''
      return cls ? `${el.tagName.toLowerCase()}.${cls}` : el.tagName.toLowerCase()
    }
    const out: {
      selector: string
      text: string
      width: number
      height: number
      hit: string
      graded: boolean
    }[] = []
    for (const el of document.querySelectorAll('button, input, textarea, select, a[href]')) {
      if (el.getBoundingClientRect().height <= 0) continue
      el.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior })
      const box = el.getBoundingClientRect()
      const cx = box.x + box.width / 2
      const cy = box.y + box.height / 2
      const outside = cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight
      const top = outside ? null : document.elementFromPoint(cx, cy)
      out.push({
        selector: name(el),
        text: (el.textContent ?? '').trim().slice(0, 40),
        width: Math.round(box.width),
        height: Math.round(box.height),
        hit: outside
          ? 'unreachable at any scroll position'
          : !top
            ? 'nothing'
            : el.contains(top) || top.contains(el)
              ? 'self'
              : name(top),
        graded: el.closest(exceptions) === null,
      })
    }
    return out
  }, TAP_EXCEPTIONS)
}

for (const viewport of VIEWPORTS) {
  test.describe(`the phone shell at ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } })

    test.beforeEach(async ({ page, context, baseURL }) => {
      const cookie = process.env.SIYUR_COOKIE
      if (cookie) {
        const url = new URL(baseURL ?? 'http://localhost:4173')
        await context.addCookies([
          { name: 'session', value: cookie, domain: url.hostname, path: '/' },
        ])
      }
      await page.goto('/')
      // The shell is built by `main.ts` after the map constructs; wait for the surface
      // that mounts last rather than for a timeout.
      await page.waitForSelector('.siyur-plan-panel')
    })

    test('nothing is wider than the viewport', async ({ page }) => {
      const { scrollWidth, clientWidth, wide } = await page.evaluate(() => {
        const limit = document.documentElement.clientWidth
        return {
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: limit,
          wide: [...document.querySelectorAll('body *')]
            .map((el) => ({ el, box: el.getBoundingClientRect() }))
            .filter(({ box }) => box.width > limit + 0.5 && box.height > 0)
            .map(
              ({ el, box }) =>
                `${typeof el.className === 'string' && el.className ? el.className : el.tagName} ` +
                `${Math.round(box.width)}x${Math.round(box.height)} > ${limit}`,
            )
            .slice(0, 8),
        }
      })
      expect(wide).toEqual([])
      expect(scrollWidth).toBe(clientWidth)
    })

    test('the ODbL attribution is not painted over — Constitution Article V', async ({ page }) => {
      const attribution = await page.evaluate(() => {
        const el = document.querySelector('.maplibregl-ctrl-attrib')
        if (!el) return null
        const box = el.getBoundingClientRect()
        const top = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
        return {
          text: (el.textContent ?? '').trim(),
          visible: box.width > 0 && box.height > 0,
          onScreen: box.y >= 0 && box.bottom <= innerHeight,
          hit:
            top && (el.contains(top) || top.contains(el))
              ? 'self'
              : typeof top?.className === 'string'
                ? top.className
                : (top?.tagName ?? 'nothing'),
        }
      })
      expect(attribution, 'no attribution control rendered at all').not.toBeNull()
      expect(attribution?.text).toContain('OpenStreetMap')
      expect(attribution?.visible).toBe(true)
      expect(attribution?.onScreen).toBe(true)
      // Before this pass this returned `siyur-sheet` at 375, 390, 430 **and 1440**.
      expect(attribution?.hit).toBe('self')
    })

    test('every control is reachable by a thumb, and big enough for one', async ({ page }) => {
      const controls = await measureControls(page)
      expect(controls.length).toBeGreaterThan(6) // the scan found the shell, not nothing

      const graded = controls.filter((c) => c.graded)
      const occluded = graded.filter((c) => c.hit !== 'self')
      const small = graded.filter((c) => c.height < TAP_FLOOR)

      expect(
        occluded.map((c) => `${c.selector} "${c.text}" is under ${c.hit}`),
        'a control a tap cannot reach',
      ).toEqual([])
      expect(
        small.map((c) => `${c.selector} "${c.text}" ${c.width}x${c.height}`),
        `below the ${TAP_FLOOR}px floor`,
      ).toEqual([])
    })

    test('every rendered word clears the type floor', async ({ page }) => {
      const small = await page.evaluate(
        ([body, stamp]: readonly [number, number]) => {
          // Stamps and micro-labels are allowed the lower floor; they are named here the
          // same way `test/mobile-usability.test.ts` names them in the stylesheet scan.
          // `closest`, not the element's own class: the ODbL credit's text lives in a
          // bare `<a>` inside `.maplibregl-ctrl-attrib`, and a check on the anchor's own
          // (empty) class name would grade a caption as body copy.
          const STAMPS =
            '.siyur-chip, .siyur-value__label, .siyur-research__chip, ' +
            '.siyur-research__source, .siyur-plan-form__label, .maplibregl-ctrl-attrib'
          const out: string[] = []
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
          let node = walker.nextNode()
          while (node) {
            const el = node.parentElement
            const box = el?.getBoundingClientRect()
            if (el && box && box.width > 0 && box.height > 0 && node.textContent?.trim()) {
              const size = parseFloat(getComputedStyle(el).fontSize)
              const cls = typeof el.className === 'string' ? el.className : ''
              const floor = el.closest(STAMPS) ? stamp : body
              if (size < floor) out.push(`${cls || el.tagName} ${size}px`)
            }
            node = walker.nextNode()
          }
          return [...new Set(out)]
        },
        [BODY_FLOOR, STAMP_FLOOR] as const,
      )
      expect(small).toEqual([])
    })

    test('the map gets a usable share of a map product’s screen', async ({ page }) => {
      // Not "the element is 844px tall" — the old full-bleed map measured 390 × 844 and
      // had **0 of 957** markers reachable, because three layers were painted over all of
      // it. The honest measure is the band of map that a tap actually lands on.
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
      expect(share).toBeGreaterThanOrEqual(0.4)
    })
  })
}
