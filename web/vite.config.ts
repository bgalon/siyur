/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config'
import { VitePWA } from 'vite-plugin-pwa'

// Siyur web config. Mirrors the ADR-0003 spike (spike/vite_spike/, proven 2026-07-25)
// so the DU-06 OPFS/PMTiles module worker slots in without re-deriving the two footguns:
//
//   Footgun 1 — big binary must stay OUT of the asset graph. The PMTiles archive is
//   fetched at RUNTIME from GCS into OPFS: never `import`ed, never placed in public/
//   (public/ copies verbatim into dist/, defeating the goal). `workbox.globPatterns`
//   below therefore lists no archive extension, and `maximumFileSizeToCacheInBytes`
//   is held low as a leak tripwire — if a multi-MB archive ever enters the graph the
//   precache step refuses it loudly instead of silently baking it into the SW.
//
//   Footgun 2 — the OPFS reader (DU-06) is a *module* worker; Vite's historical `iife`
//   worker default cannot host ESM `import` and breaks it after build. `worker.format`
//   is pinned to 'es' now so DU-06 needs no config change. `base:'/'` keeps SW scope
//   aligned; a non-root CDN path would require base + SW-scope re-alignment (untested).
export default defineConfig({
  base: '/',
  // Dev-only same-origin proxy to the FastAPI service. The client calls `/areas`,
  // `/sites`, `/auth`, `/me` as same-origin paths (see web/src/map/sites.ts), so
  // without this the dev server 404s them and the map stays empty — which is
  // exactly the symptom that reads as "the backend is broken" when it is not.
  //
  // Same-origin rather than CORS on purpose: the session is a cookie
  // (api/security.py), and `same_site='lax'` means a cross-origin XHR would not
  // send it. Proxying keeps one origin, so the cookie rides along and dev matches
  // the deployed shape (one origin, API behind it) instead of diverging from it.
  //
  // Dev server only — `vite build` does not read `server`, so nothing here reaches
  // the bundle. Point it elsewhere with SIYUR_API_ORIGIN.
  server: {
    proxy: Object.fromEntries(
      ['/areas', '/sites', '/auth', '/me', '/healthz'].map((path) => [
        path,
        {
          target: process.env.SIYUR_API_ORIGIN ?? 'http://127.0.0.1:8000',
          changeOrigin: false,
        },
      ]),
    ),
  },
  worker: {
    // DU-06: the OPFS/PMTiles reader is an ES module worker. Set now, no worker exists yet.
    format: 'es',
  },
  build: {
    target: 'es2022',
  },
  plugins: [
    VitePWA({
      registerType: 'autoUpdate',
      strategies: 'generateSW',
      workbox: {
        // App shell only. NO archive/tile extensions here — the PMTiles archive lives
        // in OPFS (runtime-fetched from GCS), never in the precache manifest.
        globPatterns: ['**/*.{js,css,html,wasm,svg,ico,webmanifest}'],
        // Leak tripwire: comfortably above the MapLibre app-shell chunk (~1 MiB) yet far
        // below any city PMTiles archive (5–150 MiB). If precache ever needs this raised,
        // a binary has leaked into the asset graph — fix the leak, don't raise the cap.
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
        cleanupOutdatedCaches: true,
        navigateFallback: 'index.html',
      },
      manifest: {
        name: 'Siyur',
        short_name: 'Siyur',
        description: 'Research an area, plan a day tour, travel guided offline.',
        theme_color: '#0f1720',
        background_color: '#0f1720',
        display: 'standalone',
        start_url: '/',
        // Icons are added when brand assets land; an installable manifest works without them.
        icons: [],
      },
      devOptions: {
        // Test the *built* SW (as the spike did), not a dev shim.
        enabled: false,
      },
    }),
  ],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['test/**/*.test.ts'],
  },
})
