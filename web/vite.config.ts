/// <reference types="vitest/config" />
import { createReadStream, statSync } from 'node:fs'
import { extname, join, normalize, resolve } from 'node:path'

import { defineConfig, type Plugin } from 'vitest/config'
import { VitePWA } from 'vite-plugin-pwa'

/** Where `scripts/fetch-basemap.sh` writes the dev tiles, glyphs and sprites. */
const DEV_ASSETS = resolve(__dirname, 'dev-assets')

const CONTENT_TYPES: Record<string, string> = {
  '.pmtiles': 'application/octet-stream',
  '.pbf': 'application/x-protobuf',
  '.json': 'application/json',
  '.png': 'image/png',
}

/**
 * Serve the dev basemap from `web/dev-assets/` — **not** from `public/`.
 *
 * Footgun 1 above is the whole reason this plugin exists: `public/` copies verbatim
 * into `dist/`, so parking a multi-MB PMTiles archive there would ship it to
 * production and defeat the OPFS design. `apply: 'serve'` means none of this runs
 * during `vite build`, and `dev-assets/` is outside the asset graph entirely, so the
 * archive can never reach the precache manifest.
 *
 * **Range requests are the point.** The `pmtiles` reader addresses the archive with
 * HTTP `Range` headers and reads a few KB per tile; a middleware that answered every
 * request with the whole 1.3 MB file would technically work while transferring the
 * archive on every tile fetch. `206` handling here is load-bearing, not politeness.
 *
 * DU-05 deletes this: the archive moves into the compiled bundle and is read from
 * OPFS, which is the transport swap ADR-0002 describes.
 */
function devBasemapAssets(): Plugin {
  return {
    name: 'siyur-dev-basemap-assets',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?', 1)[0] ?? ''
        if (!url.startsWith('/tiles/') && !url.startsWith('/basemap/')) return next()

        // `decodeURIComponent` because a fontstack path contains spaces
        // ("Noto Sans Regular"); `normalize` + prefix check to keep `..` from
        // escaping dev-assets/ even though this is a dev-only surface.
        const target = normalize(join(DEV_ASSETS, decodeURIComponent(url)))
        if (!target.startsWith(DEV_ASSETS)) {
          res.statusCode = 403
          return res.end('forbidden')
        }

        let size: number
        try {
          const stat = statSync(target)
          if (!stat.isFile()) return next()
          size = stat.size
        } catch {
          // Not fetched yet — `scripts/fetch-basemap.sh` has not run. A 404 lets
          // MapLibre fail one source and still paint the rest of the style.
          return next()
        }

        res.setHeader('Content-Type', CONTENT_TYPES[extname(target)] ?? 'application/octet-stream')
        res.setHeader('Accept-Ranges', 'bytes')

        const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? '')
        if (!range) {
          res.setHeader('Content-Length', String(size))
          return createReadStream(target).pipe(res)
        }

        // An open-ended suffix range (`bytes=-N`) means "the last N bytes", which is
        // how a reader finds a trailing directory; treat it separately from `N-`.
        const [, rawStart, rawEnd] = range
        const start = rawStart === '' ? size - Number(rawEnd) : Number(rawStart)
        const end = rawStart === '' || rawEnd === '' ? size - 1 : Number(rawEnd)
        if (!Number.isFinite(start) || start < 0 || start > end || end >= size) {
          res.statusCode = 416
          res.setHeader('Content-Range', `bytes */${size}`)
          return res.end()
        }

        res.statusCode = 206
        res.setHeader('Content-Range', `bytes ${start}-${end}/${size}`)
        res.setHeader('Content-Length', String(end - start + 1))
        createReadStream(target, { start, end }).pipe(res)
      })
    },
  }
}

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
  // `/sites`, `/auth`, `/me`, `/plans` as same-origin paths (see web/src/map/sites.ts
  // and web/src/plan/client.ts), so without this the dev server 404s them and the map
  // stays empty — which is exactly the symptom that reads as "the backend is broken"
  // when it is not.
  //
  // **An unproxied path does not fail loudly, it fails as HTML.** `navigateFallback`
  // answers a *navigation* GET with `index.html` and status `200`, so a missing entry
  // here turns `GET /plans/{id}` into the app shell — `response.ok` is true, the
  // `Content-Type` is `text/html`, and the client's `await response.json()` is the
  // first thing that notices. (A `POST` to the same path 404s, because the fallback
  // only applies to navigations — so a proxy hole can be invisible on one verb and
  // visible on the other. Verified against this server on 2026-08-11.) Every path the
  // client calls belongs in this list, added at the same time as the caller.
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
      ['/areas', '/sites', '/auth', '/me', '/healthz', '/plans'].map((path) => [
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
    devBasemapAssets(),
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
