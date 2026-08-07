import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright — the airplane-mode release gate (Constitution Article I, CI job 5).
 *
 * Job 5 has been an `echo` stub since DU-00, so **no milestone in this repo has ever
 * actually been gated on the airplane-mode check** (slice 001's quickstart records this
 * honestly at T069). This config is what makes replacing it possible; the suite grows at
 * T029 → T056.
 *
 * Three choices worth stating, because each is load-bearing rather than boilerplate:
 *
 * **Chromium only.** ADR-0002 makes the offline runtime Chromium-first and leaves
 * iPhone/WebKit to a flagged future ADR. Adding browsers here would buy failures we have
 * not decided to care about, in the one suite that must stay trustworthy.
 *
 * **`serviceWorkers: 'allow'`.** The airplane-mode assertion counts requests *observed*,
 * not requests *failed* — and `page.on('request')` misses everything issued by a service
 * worker or a worker. Siyur's OPFS reader is itself a module worker (ADR-0003), so the
 * recorder must be installed on the **context** with service workers visible, or the
 * gate passes while the app is quietly online.
 *
 * **`retries: 0`, even in CI.** A flaky release gate that passes on retry is a stub with
 * extra steps. If this suite is flaky, that is the finding.
 */
export default defineConfig({
  testDir: './test/e2e',
  // The offline flow is inherently serial: it loads online, downloads a bundle into
  // OPFS, then cuts the network for the whole context. Parallel workers would share
  // neither meaningfully nor safely.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['github'], ['list']] : [['list']],
  timeout: 60_000,
  expect: {
    // The bundle download into OPFS is the slow step, not the assertion.
    timeout: 15_000,
  },
  use: {
    baseURL: process.env.SIYUR_WEB_URL ?? 'http://localhost:4173',
    serviceWorkers: 'allow',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  /**
   * **Build + preview, never the dev server.** `vite.config.ts` sets
   * `VitePWA.devOptions.enabled: false` — "test the *built* SW, not a dev shim" — so
   * `vite dev` serves **no service worker at all**. Pointing this suite at :5173 would
   * exercise an app with no precache, and an offline reload there fails for a reason
   * that has nothing to do with the bundle. A gate that green-lights the wrong thing is
   * the stub we are replacing, so the port is deliberately 4173 and not 5173.
   */
  webServer: {
    command: 'pnpm run build && pnpm run preview --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
})
