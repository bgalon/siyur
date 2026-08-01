# web/ — Siyur PWA (Vite + MapLibre + PMTiles + OPFS)

The Siyur frontend: a Vite PWA that renders MapLibre maps and (from DU-06) reads a
compiled bundle from OPFS for a fully offline experience. Scaffolded at **DU-00** from
the ADR-0003 config spike (`spike/vite_spike/`, proven 2026-07-25).

`web/` is a JS/Vite application, **not** a Python package — it is intentionally
excluded from `pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages`.

## What DU-00 delivers

An **empty MapLibre map** renders cleanly (a version-8 style with a single background
layer, no tile source, zero network dependency) with the **ODbL attribution control**
always visible (Constitution Article V). The app shell is **precached via Workbox** so
the skeleton loads offline. No product features beyond the empty map — DU-01+ adds real
content; DU-06 swaps the tile transport to OPFS.

## What Spec 001 adds (T042–T045)

The cited commons rendered on the map. `src/map.ts` became **`src/map/`** (the DU-00
import path `./map` still resolves, via `src/map/index.ts`, and the empty-map +
precache behaviour is unchanged):

| Module | Role |
|---|---|
| `src/map/map.ts` | DU-00 empty map + `createMapWithAttribution()` (built-in control off, ours on) |
| `src/map/types.ts` | wire types for `GET /sites` (contract + `docs/data/poi-site.md`) |
| `src/map/guards.ts` | **the provenance gate** — narrows every wire value; drops anything unstamped |
| `src/map/attribution-chip.ts` | per-value source+license chip; `renderSourcedValue()` is the only data→DOM path |
| `src/map/attribution.ts` | `OdblAttributionControl` — ODbL always, plus the response's `attribution[]` |
| `src/map/sites.ts` | viewport `bbox` fetch, display-name preference, markers, `SitesLayer` |

**Provenance is structural, not a review discipline.** `renderSourcedValue()` returns
`null` for a value with no usable `source` stamp (`kind` **and** `license`), and nothing
else in `web/` reads `.value` for display. A site with an unstamped `location` is dropped
whole. The chip's text is built from that value's own stamp only — there is no license
lookup table and no default attribution string, so the client cannot invent credit
(FR-003 / FR-004 / SC-002).

**Display name** resolves `en` → `<lang>-Latn` → source-script, so an English-first user
always sees a readable name while the original script stays on the record (FR-008 / US3).

`GET /sites` is built by a sibling task; the web tests mock `fetch` with the contract's
worked example (`specs/001-research-cited-sites/contracts/sites.md`) — the contract is
the interface.

## Toolchain

Managed with **pnpm**. Pins (ADR-0003 spike-proven; stack-reference §Table A):

| Package | Pin |
|---|---|
| `vite` | `8.1.5` |
| `vite-plugin-pwa` | `1.3.0` (owns the Workbox major) |
| `workbox-build` / `workbox-window` | `7.4.1` |
| `maplibre-gl` | `~5.19` (stack-ref MVP pin; v6 ESM migration deferred) |
| `vitest` | `~4.1` (the vite-8-compatible line) |

```
pnpm install          # esbuild build script is approved in pnpm-workspace.yaml
pnpm dev              # dev server on :5173
pnpm build            # static build → dist/ (SW + precache manifest)
pnpm preview          # serve the built app
pnpm test             # vitest (jsdom)
pnpm typecheck        # tsc --noEmit
```

## Config invariants carried from the spike (so DU-06 slots in)

`vite.config.ts` mirrors the two footguns the spike cleared, so the DU-06 OPFS/PMTiles
module worker needs no config change:

- **`worker.format: 'es'`** — the OPFS reader (DU-06) is an ES *module* worker; Vite's
  historical `iife` worker default cannot host ESM `import` and breaks it after build.
  Set now even though no worker exists yet.
- **The PMTiles archive stays OUT of the build graph** — it is runtime-fetched from GCS
  into OPFS, never `import`ed and **never placed in `public/`** (`public/` copies verbatim
  into `dist/`). `workbox.globPatterns` lists no archive extension, and
  `maximumFileSizeToCacheInBytes` is held at 3 MiB as a **leak tripwire**: comfortably
  above the MapLibre app-shell chunk (~1 MiB) yet far below any city PMTiles archive
  (5–150 MiB), so a leaked archive is refused by precache loudly instead of silently baked
  into the service worker.
- **`base: '/'`** — keeps SW scope aligned; a non-root CDN path would need base + SW-scope
  re-alignment (untested, per the spike).

**DU-06 is NOT built here** — only the config seam for it.
