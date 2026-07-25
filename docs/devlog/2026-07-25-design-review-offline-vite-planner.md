# 2026-07-25 — Design review: offline sequencing, Vite, and the planner architecture

**Goal:** review the technical design together, stress-test the risky parts, and resolve three architecture questions that had been left as `tech-design.md` §6 locks or open assumptions — how to sequence offline, whether Vite can carry the frontend, and whether LangGraph is the right planner framework given new efficiency/multi-provider requirements.

## What happened

A collaborative walkthrough of `tech-design.md`, then a drill into where it can fail. The failure review reframed the scary cases as **silent quality/economic failures**, not crashes: a false-merged POI in the commons, a low commons cache-hit rate quietly breaking the cost thesis, and an evicted offline bundle leaving a traveler with a blank map. That last one drove the rest of the session.

Three threads resolved, each landing an ADR:

1. **Offline sequencing.** Ben asked whether we can ship online-first and add offline later without a rewrite. The trap: if the client reads *live* from the API, adding offline later flips the entire read path — a real refactor. The resolution is the recurring move of this session — **build the seam now, defer the implementation**: promote the compiled bundle to the client's read model from day one (HTTP now, OPFS later), so offline becomes a *transport swap*, not a read-path rewrite. The genuinely expensive-to-retrofit pieces (`SourcedValue.bundleable`, the license quarantine) stay in M1; only browser storage is deferred.

   Surprise that reshaped the browser decision: **Chrome on iPhone is WebKit** (Apple mandates it), so a "Chrome-only" gate does *not* escape the iOS 7-day-eviction / OPFS pain on iPhone — only on Android + desktop. So Chromium-first genuinely deletes the worst offline failure mode, but *only* if iPhone scope is decided separately. Flagged, not decided.

2. **Vite.** Reframed "can Vite support a complex design?" as: the complexity is *runtime* (OPFS, workers, streaming), not *build*. Every hard capability the design needs — module workers for the OPFS reader, `vite-plugin-pwa`/Workbox, WASM, code-split shell, dev proxy, static output — is in Vite's core. Two footguns written down: keep the big PMTiles/glyph/sprite artifacts **out of Vite's asset graph**, and prove the worker+PWA+WASM config interaction once. Verdict: Vite is the conventional correct choice; pin it.

3. **Planner framework.** Ben sharpened the requirements: fast, token-frugal, multi-provider *over time*, model-switchable *per task*. Named the central tension — **provider-native token features (Anthropic caching/compaction) vs. provider-agnostic switchability pull opposite ways**. Resolution: a two-layer split — own orchestration + context management (where tokens are saved), put model calls behind a thin capability-oriented `ModelRouter` seam (where provider/routing lives). I drafted the choice as a bake-off; **Ben decided directly: Option B — PydanticAI + LiteLLM + own Postgres checkpoint**, for token transparency and low code weight. LangGraph's checkpointer/HITL was judged not to earn its dependency weight over a single owned `user_plan` row.

   "How easy to replace the Anthropic SDK later?" → *the plumbing is easy if the seam is narrow* (enforced by a seam-purity test); *the token optimizations don't port* — each provider needs its own caching strategy re-derived. So M1's seam adapter stays **Anthropic-native** (full caching), with LiteLLM slotting in when cross-provider lands.

Wrote a reference validation spike (`spike/planner_spike/`, gitignored) implementing the seam + Anthropic-native adapter + the pipeline slice + a token harness. **Not executed** — no `uv` env / API key this session, and the app packages don't exist yet; it's a reference for `planner/` at ramp-up, honest about that in its README.

## Decisions

- Online-first delivery on an offline-ready data contract (bundle = client read model from day one) → **ADR-0002** (accepted).
- Vite (pinned) as the `web/` build tool + dev server → **ADR-0003** (accepted).
- Layered planner over a provider-routable model seam; **Option B — PydanticAI + LiteLLM**, Anthropic-native in M1, per-task routing now, cross-provider deferred → **ADR-0004** (accepted; amends the §6 LangGraph lock).
- **Flagged for Ben, not decided:** (a) whether iPhone/WebKit is in M1 offline scope — the thing that actually determines offline difficulty; (b) ratifying the AGENTS.md "one sanctioned hosted LLM dependency" change before any non-Anthropic adapter is built.

## Failures

- None filed this session (design review; no runtime failure to catalog).

## Cost / turns

One extended conversational design-review session, ~a dozen user turns. No token accounting. Artifacts produced: 3 ADRs (0002–0004), 5 spike files (`spike/planner_spike/`), this devlog. One Bash syntax-check of the spike was blocked by sandbox permissions — spike remains unverified-by-execution (as its README states).

## Exhibit-tag candidates

- `exhibit/U5-seam-defer-implementation` — the reusable "build the seam now, defer the implementation" pattern applied twice in one session (offline HTTP→OPFS; Anthropic→multi-provider), as a way to sequence for velocity without a later refactor. (proposed)
- `exhibit/U0-chrome-on-ios-is-webkit` — the browser-engine gotcha that reframes an entire offline decision, and why "which browser" is really "which engine." (proposed)
- `exhibit/U6-native-vs-agnostic-token-tension` — provider-native token features vs. provider-agnostic switchability, and the capability-oriented seam that resolves it (`cache_prefix`/`long_run` as requests, not methods). (proposed)
- `exhibit/U3-lock-then-reopen-with-evidence` — reopening a `tech-design` §6 lock (LangGraph) when requirements sharpened, and settling it by decision + a measured validation spike rather than inertia. (proposed)
