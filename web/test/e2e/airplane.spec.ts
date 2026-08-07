import { expect, test, type Page, type BrowserContext } from '@playwright/test'

/**
 * T029 — the airplane-mode gate, stood up at **DU-04** rather than DU-06.
 *
 * CI job 5 is labelled "THE MERGE GATE" and has been three `echo` lines since DU-00, so
 * **no milestone in this repo has ever actually been gated on the airplane-mode check**
 * (recorded honestly in slice 001's quickstart at T069). Building it last is how it stays
 * a stub, so it lands here against the *existing* empty map and DU-05/06 only add
 * assertions: itinerary, timeline, narration, recovery route.
 *
 * ## Why requests are counted, not merely failed
 *
 * `context.setOffline(true)` proves the app *works* without a network. It does not prove
 * the app *made no request* — an attempted request is invisible unless you also listen
 * for `requestfailed`, and **a request served from the SW Cache API never fails at all**.
 * So "it still rendered" can hide a live network dependency. FR-018/SC-005 is **zero
 * requests**, so the assertion is over requests **observed**.
 *
 * Both mechanisms are used, layered, because they answer different questions:
 * `setOffline` puts the app on the offline code path (`navigator.onLine`-conditioned
 * branches take the right turn), and a catch-all route recorder proves nothing was asked
 * for.
 *
 * ## Two Chromium footguns, pre-armed
 *
 * 1. `page.on('request')` **misses requests issued by the service worker and by workers**.
 *    Siyur's OPFS reader is itself a module worker (ADR-0003), so the recorder is
 *    installed on the **context** with `serviceWorkers: 'allow'` (playwright.config.ts).
 * 2. `data:` and `blob:` URLs are not network. Filter by **scheme**, never by a host
 *    allowlist — a host allowlist is how a real leak gets waved through.
 *
 * **Nothing is allowlisted.** If some request must ever be permitted, it gets named in an
 * ADR, not a glob here.
 */

/** Schemes that never touch the network, whatever the recorder sees. */
const NON_NETWORK = /^(data|blob|about|chrome-extension):/

/**
 * Record every request the context makes and abort it.
 *
 * Returns the live array — assert on it *after* the flow, not during.
 */
async function recordAllRequests(context: BrowserContext): Promise<string[]> {
  const seen: string[] = []
  await context.route('**/*', (route) => {
    const url = route.request().url()
    if (!NON_NETWORK.test(url)) seen.push(url)
    // Abort rather than continue: offline means offline. Letting it through would make
    // the assertion a formality.
    route.abort()
  })
  return seen
}

/**
 * Get the app to the state where it can actually survive losing the network:
 * a service worker that is **controlling this page**, with the shell **in the cache**.
 *
 * `activated` is not enough, and assuming it is produces a false red. A service worker
 * takes control only on the **next navigation**, so immediately after first load
 * `navigator.serviceWorker.controller` is `null` and the precache may still be filling.
 * Going offline at that moment fails with `ERR_INTERNET_DISCONNECTED` — which looks
 * exactly like "the app is broken offline" and is really "the test went offline too
 * early". Measured on this app: `controller: false, cached: []` before the reload,
 * `controller: true` after it, offline reload fine thereafter.
 *
 * So: wait for `activated` → **reload once online** to hand over control → confirm the
 * controller and a non-empty cache. Only then is offline a meaningful question.
 */
async function waitForServiceWorkerControl(page: Page): Promise<void> {
  await page.waitForFunction(
    async () => (await navigator.serviceWorker?.getRegistration())?.active?.state === 'activated',
    undefined,
    { timeout: 30_000 },
  )
  await page.reload({ waitUntil: 'load' })
  await page.waitForFunction(() => !!navigator.serviceWorker.controller, undefined, {
    timeout: 30_000,
  })
  // The shell must actually be cached, or the offline assertion below would pass for the
  // wrong reason on an app that simply made no requests.
  await page.waitForFunction(
    async () => {
      const names = await caches.keys()
      const counts = await Promise.all(
        names.map(async (n) => (await (await caches.open(n)).keys()).length),
      )
      return counts.some((c) => c > 0)
    },
    undefined,
    { timeout: 30_000 },
  )
}

test.describe('airplane mode — the release gate', () => {
  /**
   * **Known-failing by design until DU-06 — and that is the point.**
   *
   * On its first real run this gate immediately found what it exists to find: offline,
   * the app still issues
   *
   *     GET /sites?bbox=-180,-77.466028,180,77.466028
   *
   * — a **live commons read** from `web/src/map/sites.ts`, for the whole world, because
   * the map opens at world zoom. FR-018/SC-005 say **zero** network requests; this is one.
   *
   * It is not a defect *yet*: DU-01→03 is deliberately the online phase, and the fetch
   * simply fails offline. It becomes a defect the moment DU-06 claims the offline runtime,
   * because the traveller's sites must come from the bundle, not from `/sites`.
   *
   * Marked `test.fail()` rather than deleted, weakened, or allowlisted:
   * - deleting it loses the finding;
   * - relaxing the assertion to "≤ 1 request" is the FAIL-007 mistake again — asserting
   *   what the code does instead of what the product requires;
   * - `test.fail()` keeps CI honest *and* **fails loudly the moment it starts passing**,
   *   so DU-06 cannot land the fix without being told to flip this line.
   *
   * Same discipline as the FAIL-006 `xfail` in `evals/test_caching.py`, pinned so a
   * silent regression cannot creep back unnoticed. **DU-06 (T056) removes the `.fail()`.**
   */
  test('renders the map with ZERO network requests after going offline', async ({
    page,
    context,
  }) => {
    test.fail() // see the note above — DU-06 (T056) removes this line
    // ── online phase: load, and let the service worker precache the shell ──────────
    await page.goto('/')
    await waitForServiceWorkerControl(page)
    await expect(page.locator('canvas')).toBeVisible()

    // ── cut the network, *then* start recording, then reload ──────────────────────
    // Offline is set AFTER first load on purpose: WebKit and Firefox error when a
    // context starts offline, and the precache must exist before we can prove it is
    // what served the reload (test-strategy.md Tier 3).
    const requests = await recordAllRequests(context)
    await context.setOffline(true)
    await page.reload({ waitUntil: 'load' })

    // ── the assertions ────────────────────────────────────────────────────────────
    // Assert on the rendered canvas, not `navigator.onLine` — which lies, and which
    // would pass against a blank page.
    await expect(page.locator('canvas')).toBeVisible()

    expect(
      requests,
      `expected zero network requests offline, saw ${requests.length}:\n` +
        requests.slice(0, 20).join('\n'),
    ).toEqual([])
  })

  test('the app shell survives an offline reload at all', async ({ page, context }) => {
    // Guards the precache itself: without it the reload yields a browser error page and
    // the zero-requests assertion above would pass for entirely the wrong reason.
    await page.goto('/')
    await waitForServiceWorkerControl(page)

    await context.setOffline(true)
    await page.reload({ waitUntil: 'load' })

    await expect(page).toHaveTitle(/siyur/i)
    await expect(page.locator('canvas')).toBeVisible()
  })
})

/**
 * The negative control — **the most important test in this file.**
 *
 * A gate that cannot fail is exactly the stub being replaced, and this repo has already
 * shipped one assertion that was green *because* the code was wrong (FAIL-007). So the
 * recorder is proven to catch a request before it is trusted to prove their absence.
 */
test('NEGATIVE CONTROL: the recorder catches a request that should not happen', async ({
  page,
  context,
}) => {
  await page.goto('/')
  const requests = await recordAllRequests(context)
  await context.setOffline(true)

  await page
    .evaluate(() => fetch('https://example.invalid/should-be-caught.json').catch(() => undefined))
    .catch(() => undefined)

  expect(
    requests.some((u) => u.includes('example.invalid')),
    'the recorder failed to observe a deliberate network request — every other ' +
      'assertion in this file is therefore worthless',
  ).toBe(true)
})
