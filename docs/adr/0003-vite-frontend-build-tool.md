# 0003 — Vite (pinned) as the `web/` build tool and dev server

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-25

## Context and Problem Statement

The `web/` PWA carries the design's hardest runtime concerns: MapLibre + PMTiles rendering, a dedicated Web Worker doing OPFS byte-range tile reads (`FileSystemSyncAccessHandle`), a Workbox service worker for the app shell, WASM decode paths, SSE streaming from the planner, and the offline read-abstraction seam from ADR-0002 (HTTP↔OPFS transport swap). The stack reference (`methods-stack-reference.md` §2) already names "Vite PWA + Workbox v7" as the recommended choice, and the local-dev topology (`tech-design.md` §4) lists a Vite dev server on :5173. This session reviewed whether Vite can actually carry a design this complex before it becomes a de-facto lock.

The reframing that settled it: **Vite is a build tool and dev server, not an application framework.** Siyur's complexity is overwhelmingly *runtime* (offline, OPFS, workers, streaming), not *build*. The question is therefore whether Vite supports the specific build-time capabilities this runtime needs — module workers, PWA/Workbox, WASM, a code-split shell, a dev proxy, and static output — not whether it "handles complexity" in the abstract.

## Considered Options

- **A — Vite (pinned).** The 2026-conventional choice for an offline map PWA; the offline-PMTiles prior art the design draws on (§2) is Vite-based. First-class module workers, `vite-plugin-pwa` (Workbox), WASM support, Rollup code-splitting, `server.proxy` for `/api`→FastAPI, static output for Cloud CDN.
- **B — A meta-framework (Next/Nuxt/SvelteKit-class).** Adds SSR/routing/server conventions Siyur does not need — the app is a static SPA/PWA on Cloud CDN with no SSR — while complicating the worker/OPFS/service-worker control the offline model requires.
- **C — Hand-rolled bundler (esbuild/Rollup direct).** Maximum control, but re-implements exactly what Vite already integrates (worker bundling, PWA plugin, WASM, dev server, HMR) with no offsetting benefit.

## Decision Outcome

Chosen: **A — Vite, pinned**, because every genuinely hard frontend capability this design needs sits in Vite's supported core (module workers for the OPFS reader, `vite-plugin-pwa`/Workbox for the shell, WASM, code-split shell, dev proxy, static CDN output), and the design's real risk lives in *our* offline code behind the ADR-0002 seam — which Vite bundles but does not constrain. Vite is not the risky part of the frontend; it is the conventional, prior-art-validated substrate for this class of app. The version is pinned with the same discipline as the geo libs (§6) because Vite moves fast and the Rolldown bundler transition (`rolldown-vite`, opt-in) is landing in this era — pinning keeps M1 off a moving target.

## Consequences

- Good: the offline-critical worker + service-worker + WASM paths are first-class, not fought; the read-abstraction seam (ADR-0002) drops into a module worker cleanly, so online→offline stays a transport swap.
- Good: static build output matches the Cloud Storage + CDN topology (§3) with no SSR layer to operate.
- Bad / accepted cost: two config interactions are sharp and must be proven before `web/` grows — (1) **big binary artifacts must stay out of Vite's asset graph**: the PMTiles archive, glyphs, and sprites are runtime-downloaded to OPFS/GCS and must never be `import`ed or hashed by the build; (2) worker output format, service-worker scope, and OPFS-served base paths interact across the worker+PWA+WASM combination.
- Accepted cost: a fast-moving tool pinned exactly means periodic deliberate upgrades (incl. the eventual Rolldown move), tracked like any other pin.

## Confirmation

A **config spike** (throwaway, before `web/` is scaffolded at ramp-up): an empty Vite app that builds and serves — under `vite build`, static — a module worker performing an OPFS sync-access-handle read, a Workbox precache, and a WASM import, all four together. This proves the sharp interactions above once. Durable confirmation at build time: Vite + `vite-plugin-pwa` + Workbox pinned in `methods-stack-reference.md` (same exact-pin discipline as `shapely~=2.1` et al.), and the airplane-mode e2e (ADR-0002 scoped tripwire → §5.5) exercises the produced worker/OPFS read path in CI. TODO: add the spike task to `delivery-plan.md` and the version-pin lines to the stack reference on implementation.
