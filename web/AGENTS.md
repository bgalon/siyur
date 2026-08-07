# AGENTS.md — `web/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** the PWA (`docs/design/tech-design.md` §5.3) — Vite + TypeScript + MapLibre +
Workbox, **pnpm not npm**. Delimit an area, stream a research pass, render cited sites on
a map, and (from DU-06) read the compiled bundle from OPFS in airplane mode.
`pnpm -C web test` (vitest) and `pnpm -C web typecheck` (`tsc --noEmit`) both gate CI.

**Invariants enforced here:**
- **Nothing large goes in `public/` — `vite.config.ts` "Footgun 1".** `publicDir` copies
  **verbatim into `dist/`**, so a binary parked there ships to production. The PMTiles
  archive is fetched at runtime into OPFS: never `import`ed, never in `public/`. Dev-only
  assets live in `web/dev-assets/` behind the dev middleware. *This has been violated once
  (2026-08-07) — by an agent editing that same file.*
- **`EMPTY_STYLE` is the production default.** `src/map/basemap.ts` is a **dev affordance**,
  reached only through a dev-gated dynamic `import()` and deliberately **not** re-exported
  from `src/map/index.ts` — a barrel re-export pulls the `pmtiles` reader into the
  production graph. The real tile source arrives inside the bundle at DU-05
  (`docs/data/tile-source.md`).
- **The client never invents a credit.** ODbL renders on *every* map unconditionally
  (Constitution Article V); every other credit is mirrored verbatim from the server's
  `attribution[]`. Server-sent strings are escaped — attribution text is data, not a template.
- **Value and stamp are co-present (ADR-0019).** A displayed value carries its source chip in
  the same element, same frame — permanently at or below `SITE_LABEL_DENSITY_LIMIT` (12)
  sites, behind hover/focus above it. "Behind an interaction" is allowed; "value without its
  stamp" never is.
- **Unstamped data is dropped, not rendered.** `sanitiseSite` / `sanitiseSitesResponse` are the
  provenance gate; a record without a stamped location does not reach the map.
- **A focusable marker always has an accessible name** (WCAG 2.2 SC 4.1.2). Markers are
  `role="button"` and `tabIndex 0`; a site with no stamped name gets a name built from what the
  record *does* carry — never an invented one.
- **Genericity (FR-001):** no city, bbox or place name in source. Rhodes is a demo default.
- **`worker.format: 'es'`** is pinned for DU-06's OPFS module worker — do not change it.

**Known gap:** vendored Noto glyphs cover **U+0000–U+04FF only**. Hebrew, Arabic and CJK
labels render as *nothing*, silently. Widening is DU-05/M3 work.
