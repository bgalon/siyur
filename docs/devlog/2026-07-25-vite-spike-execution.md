# 2026-07-25 — Vite spike execution: proving the ADR-0003 frontend build

**Goal:** run the throwaway Vite config spike (`spike/vite_spike/`) before `web/` is scaffolded, and prove ADR-0003's Confirmation for real — that the four sharp frontend capabilities **build static under `vite build` and run together under a Chromium engine**: an ES module worker doing an OPFS `FileSystemSyncAccessHandle` read, a Workbox precache (vite-plugin-pwa), a WASM import, and the big binary kept **out of the asset graph**. Build it and drive it, don't reason about it.

## What happened

Stood up a minimal Vite app implementing all four items, built it, and drove the built output under Chromium (Claude-in-Chrome) through an online load and a **server-killed offline reload**. All four work together; the offline reload served the shell from the Workbox precache and read the 5 MiB archive from OPFS with **zero network bytes**.

Two ADR-0003 footguns both cleared, and the exercise earned two concrete config invariants a careful read would have under-specified:

1. **`worker.format: 'es'` is mandatory.** The OPFS reader is a *module* worker (`{ type: 'module' }`) that `import`s the wasm-init helper; Vite's historical `iife` worker default cannot host ESM `import` and produces a broken worker after build. This is proven by execution — the worker ran and imported the helper, which an `iife` worker literally cannot do.
2. **The big binary must be served from outside the build tree — `public/` is a trap.** Files in `public/` are copied verbatim into `dist/`; they dodge *hashing* but still ship in the build output, defeating "out of the build" from ADR-0003. Kept the 5 MiB PMTiles stand-in in `server-assets/` (served by a zero-dep static server; GCS/CDN in prod), `fetch()`ed at runtime into OPFS — never `import`ed. `dist/` after build contains **no `.bin`**; the precache manifest is exactly the 4 shell entries. A low `maximumFileSizeToCacheInBytes` (512 KiB) is set as a standing leak tripwire.

**Offline proof (server killed, then reload):** every resource `transferSize=0`; a fresh `fetch` of an uncached URL throws `TypeError` (origin genuinely down, so the shell 200s are the service worker serving from Cache API); worker read the archive from OPFS; wasm ran. Full detail in `spike/vite_spike/FINDINGS.md`.

**Versions pinned (verified live on npm 2026-07-25):** `vite@8.1.5`, `vite-plugin-pwa@1.3.0`, `workbox-build@7.4.1` / `workbox-window@7.4.1`. Load-bearing compatibility fact: `vite-plugin-pwa@1.3.0` supports Vite 8 *and* owns the Workbox major (pins it to 7.4.x) — so bump the plugin, never Workbox directly. Note the ADR/stack-ref predate Vite 8 and say "Vite PWA + Workbox v7" generically; Vite **8** is the current line and works.

## Decisions

- ADR-0003 **Confirmation satisfied** — the config spike proved the four sharp interactions together once, as specified.
- Version pins folded into `methods-stack-reference.md` (Table A: three explicit rows; §2: the four config invariants), closing the ADR-0003 "add version-pin lines" TODO. `navigator.storage.persist()` returned `false` under plain Chromium (granted only heuristically for installed PWAs) — reconfirms risk C.1: eviction is a UX problem, mitigated by re-downloadable bundles + launch-time integrity check, not `persist()`.
- Config invariants to carry into `web/`: `worker.format:'es'`; archive fetched at runtime into OPFS (never imported / never in `public/`); `globPatterns` excludes the archive; keep `base:'/'` unless a CDN sub-path forces otherwise (non-root base needs `base` + SW-scope alignment — untested).

## Failures

- None filed as FAIL-NNN — nothing broke; both footguns were anticipated from the ADR and handled proactively (chose `worker.format:'es'` and kept the binary out of the tree from the start). The `public/`-leaks-to-`dist/` and `persist()→false` items are recorded as design notes in FINDINGS.md, not encountered failures.
- **Process failure (real, this session): two spike sessions shared one `main` checkout and raced** — clobbered `.claude/settings.local.json`, edited `methods-stack-reference.md` / `0004-*.md` simultaneously, and one session moved the shared checkout onto a branch under the other's feet. Fixed at the process layer by **ADR-0005** (branch-per-unit + PRs; worktrees for local parallelism, separate branches for cloud). This devlog and the stack-ref pins were re-homed onto their own branches + PRs as the correction.

## Cost / turns

One working session, ~a dozen user turns. One `npm install` + a handful of `vite build`s; one Chromium session driving online load + offline (server-killed) reload; ~5 MiB dummy binary generated locally. Spike remains gitignored — not merged. The only tracked outputs are the stack-ref pins (merged via PR) and this devlog.

## Exhibit-tag candidates

- `exhibit/D5-vite-offline-zero-bytes` — kill the static server, reload, and the app still works: shell from Workbox precache, 5 MiB archive from OPFS, `transferSize=0` everywhere. The clearest single demonstration of the offline-first data contract (ADR-0002) at the frontend build layer. (proposed)
- `exhibit/U2-public-dir-is-a-trap` — why the big binary must be served from outside the build tree, not `public/` (which copies verbatim into `dist/`), plus the `maximumFileSizeToCacheInBytes` leak tripwire. A reusable Vite/PWA gotcha. (proposed)
