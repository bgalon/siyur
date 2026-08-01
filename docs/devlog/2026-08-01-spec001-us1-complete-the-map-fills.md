# 2026-08-01 — Spec 001 US1 complete: the map fills with real cited places

**Goal:** finish US1 so the map shows real cited places, with maximum parallelism and minimum supervision.

## Where to pick up (read this first in a new session)

**`main` is green: 649 tests + 24 evals, 15 PRs merged this session, CI 1–8 passing.**

US1 is **done and demonstrated end to end on live data**. `specs/001-research-cited-sites/tasks.md` is now marked up: **T001–T067 complete**, three left — **T068** (quickstart), **T069** (seven-gate + airplane-mode re-verification), **T070** (this entry).

What a fresh session should know:

| Thing | State |
|---|---|
| `docs/TRY-IT.md` | Rewritten against what was actually run. Trust it over this file. |
| Marker labels | Always visible ⇒ unreadable at a few hundred markers. **The most visible thing between here and a stranger-demoable product.** |
| `POST /areas` by *name* | Scans Overture divisions with no bbox pushdown; hangs for minutes and froze a browser tab. The `bbox` path is instant. |
| Prompt caching | Requested correctly, but the prefix is under the tier minimum, so it is off in practice. FAIL entry + fix in flight. |
| `409` research guard | Process-local, not database-backed. |
| SC-005 | Evidenced (Rhodes + Takayama, no place-specific code) but both fixtures are committed, so it is *rehearsed*; the constitution's ≥3-areas-including-an-unrehearsed-one gate is not met. |

## What happened

Fanned out **nine background sessions across three waves**, 15 PRs. The thing that made wide parallelism work rather than thrash: **fixing the module interfaces before fanning out.** Wave 1 (`commons/repository.py`, `planner/nodes/research.py`+`curate.py`, `planner/nodes/resolve_area.py`) coded against one written contract, never saw each other's work, and integrated with **zero merge conflicts and zero interface drift** — every name landed verbatim. That is the reusable lesson, more than any individual result.

The counterpart was that every agent's load-bearing claim got checked rather than believed. That caught four real things (below). The ownership boundary held again too: three sessions found defects in files they did not own, reported them precisely instead of editing, and the fixes landed as separate reviewed changes.

**US1 verified live, not just in tests.** PostGIS + uvicorn + Vite, real Overture cloud release and real Overpass: `POST /areas` → SSE → 508 Overture + 384 OSM → **784 merged in 42 s**; `GET /sites` → **782 sites, 4353 sourced values, 0 without a source, 0 without a licence**, `attribution: ["© OpenStreetMap contributors"]`, 0 locations from a non-geodata source. In the browser at z16 over Rhodes old town: **782 markers spread 691 × 731 px**, each carrying its own `OVERTURE · CDLA-PERMISSIVE-2.0` or `OSM · ODbL-1.0` chip. The real response passes the client's own provenance gate with zero records dropped.

**The licence spread is not a Rhodes quirk.** Live Rhodes: CDLA 2965 · ODbL 1322 · **Apache-2.0 56** · CC0 10. Independently on the other side of the world (Takayama, Japan): CDLA 325 · **Apache-2.0 55** (all Foursquare) · CC0 20. ADR-0012's allowlist addition is load-bearing in general, not locally.

**Three gaps that only running it could reveal.** Every layer was tested and the map still showed nothing useful: no committed dev proxy (the client calls same-origin paths, so Vite answered its SPA fallback with a `200` — a status-code check *passes* against that, which is how it hid); nothing ever framed the resolved area, so a researched old town was sub-pixel at `zoom 1` and 782 correct markers looked like an empty world; and no handle for automated checks, since MapLibre ignores synthetic wheel/click events. Fixed in #56.

## Decisions

- **The `research` node makes no model call** → **ADR-0014** (drafted, awaiting Ben). `tasks.md` T033 labelled it "Haiku tier via the seam", but it is a deterministic fan-out over values already stamped at the source boundary (ADR-0009), and putting an LLM on the one path carrying coordinates contradicts FR-005 for no benefit.
- **T065's caching eval was aimed at the wrong node** and has been re-aimed at `curate`. As written it could only fail forever, or be "fixed" by adding a pointless model call to `research`.
- **Corrected minimum-prefix figures** (from the `claude-api` skill, which overrode what `tasks.md` asserted): Sonnet 5 = **1,024**, Haiku 4.5 = 4,096, Opus 5 = **512**. `tasks.md` said 2,048 for Sonnet — that is the Opus 4.7 tier. **The minimum is not monotonic across generations**, so a re-pin can move the bar underneath a prompt that was fine yesterday.
- **`area` is private, row-scoped to `created_by`** — the conservative reading of PRD §13 #4. **ADR candidate**, flagged not decided: a name-resolved administrative division and a user-drawn ring sit on opposite sides of the commons/private split. `docs/data/area.md` deliberately refuses to answer it.
- **The `409` guard is process-local.** Stated plainly in the docstring rather than dressed up. **ADR candidate.**
- **T067 verdict: gap found.** `SiteRecordV1` stood unchanged field-for-field, but the card had drifted stale (its allowlist still omitted Apache-2.0; it stated the quarantine as "only if" where the code enforces an equivalence) and omitted six rules the code depends on.
- **T051 verdict: already held, now pinned.** No refactor was manufactured; the new tests pass before *and* after, which is the evidence.

## Failures

- **FAIL-005 — cross-engine string normalization.** DuckDB's `lower()` is *simple* case folding; Python's `casefold()` is *full* (1:N) and also decomposes precomposed Greek. A Python-folded needle matched nothing in the pushed-down SQL. Silent, and only on non-Latin input — i.e. exactly the genericity property we claim. Three-layer guardrail (differential oracle over 18 samples, a DuckDB mechanism test, an AST tripwire parsing SQL comparisons). Proof the tripwire earns its place: with the bug reintroduced, the behavioural suite still passed 35/35. Two residual divergences (final sigma; `nfc_normalize(lower(x))` normalising in the wrong order) were found while building the guardrail and are fixed separately.
- **FAIL-006 — a cached prefix under the provider minimum** (entry + fix in flight). `curate` asks for caching correctly, but `RANKING_SYSTEM_PROMPT` is ~133 tokens against Sonnet 5's 1,024 minimum. Anthropic caches nothing below the minimum **and raises no error**: the lever is off while appearing on. Caught by an eval written for the purpose, before any bill.
- **Operator error, mine: I merged #56 while four checks were still pending**, by chaining `gh pr merge` onto the check command instead of gating on it. It landed green, so no harm — but the merge gate is self-enforced discipline (branch protection is unavailable on a private free-tier repo), and chaining defeats it. The rule: check, then merge, as two steps. Recorded here rather than as a FAIL entry since it is process, not product.

**Things reported by one session and found wrong by another** — worth recording because it validates the verify-don't-believe posture in both directions. The `merge_cluster` conflict-dedupe defect was reported with a mechanism that was wrong twice over: the "accidentally idempotent" explanation was false (`_unique` never sees the derived conflicts, so the bug fires today with plainly equal conflicts), and "grows on every refresh" was also false (growth is bounded at one spurious copy, measured 2 → 3 → 3 → 3). The *conclusion* was right and the fix landed; the reasoning was not.

## Verification highlights

- **`mypy` caught a defect in my own test helper**: a wrapper adapter hard-coded `kind = "overture"` and would have mislabelled the OSM source's status frame. Fixed by delegating `kind`, then pinned by an assertion.
- **The agentevals superset matcher is blind to order.** Moving `resolve_area` *after* the research loop left the trajectory eval **green** — it answers membership only. Only the separate order assertion caught it. Both halves are now asserted.
- **Every story eval was proved to bite** by inducing a specific break for each (nine of them) and recording the observed red.
- **The genericity AST scan needed no exemptions** — axis bounds, confidence bands, `4326` and `(0.0, 0.0)` all pass on the rules as written, and it found the injected place literal and bbox.

## Cost / turns

Nine background sessions, ~1.7M subagent tokens, 15 PRs, one session. The most expensive (362k) was the caching eval — it loaded the `claude-api` skill rather than writing provider figures from memory, and that is precisely why it caught the wrong numbers in `tasks.md`.

## Exhibit-tag candidates

- `exhibit/U4-parallel-interfaces` — three sessions, disjoint files, one written interface contract, zero conflicts and zero drift. The method, not the output.
- `exhibit/U3-cross-engine-fold` — FAIL-005: two normalisations that are each individually correct, silently disagreeing across an engine boundary, invisible except in non-Latin scripts. Strong teaching material.
- `exhibit/U4-silent-cache-miss` — a lever that is on in the code, off at the provider, and errors nowhere. Found by an eval, not a bill.
- `exhibit/U3-grounding-live` — 4353 live values, zero unstamped; the provenance guarantee holding on real data rather than fixtures.
- Process candidate: **the map that was empty for three unrelated reasons**, none of which any test could have caught, because each layer was individually correct.
