# 2026-08-07 — Landing through an Actions incident; a basemap; the course feed audited

**Goal:** triage and land the three open PRs (#66/#67/#68), then make the running app legible enough to actually look at.

## What happened

**Three PRs, nine red checks, one real failure.** A GitHub Actions platform incident (opened 2026-08-06T15:22:49Z, critical, ~14h) had starved runners and then stopped creating workflow runs entirely. The distinguishing signal was **duration**: these jobs finish in 7s–1m40s, and every red one had run 45m–2h36m before dying. Re-running the failed jobs turned #67 and #68 fully green with no code change. That is the whole triage method — nothing was debugged that had not first been shown to reproduce.

**#66's diff-guard was the exception, and it mattered that it failed *fast*.** 14 seconds, not 1h39m. It reported **789 changed lines for a PR that is one file, +261/−0**. Cause: the job compares `BASE_SHA...HEAD`, where `BASE_SHA` is the base sha frozen in the *event payload* (`32f4dba`, PR #63's merge, from the previous day) while `HEAD` is the PR **merge ref**, which GitHub recomputes against current `main`. `main` had since gained #69/#70/#71, so ~528 lines of unrelated drift were charged to the ADR. #67 escaped only because its recorded base (`2c59d1e`) happened to be newer — the same latent bug, different luck.

Three ways to clear it, and the choice is the teachable part: rebase (honest, costs a CI cycle), add `size-override` (no push needed, but records "this PR is >500 lines, override justified" about a 261-line PR — **a false entry in the governance trail**), or merge red (defeats the self-enforced gate). Ben chose the rebase. diff-guard then passed in 6s counting 261.

**A near-miss on the migration.** After merging, `alembic current` reported `0003` — and so did `alembic heads`. The local checkout was **6 commits behind**, so migration `0004` was on `main` but not in the working tree. Had I not compared `heads` against `current`, `alembic upgrade head` would have reported success while doing nothing at all. Pulled, upgraded, verified the column and the GiST index on the live schema rather than trusting the exit code.

**The coverage fix, demonstrated on live data.** A real research pass (785 sites, Overture 508 + OSM 386, no degraded sources) then made ADR-0018's failure concrete: pan out one step and the viewport still contains all 783 sites, but now reports `covered=false, researched_fraction=0.111`. Under the old `known_site_count > 0` rule that region — never researched — reported covered, and the client served reuse instead of research, silently, over a populated map.

**Then the map: 783 markers floating over nothing.** `EMPTY_STYLE` carries no tile source. Correct for M1, unreadable in practice. The instinct is to point MapLibre at a hosted style; the repo had already decided otherwise — `docs/data/tile-source.md` specifies Protomaps daily build → PMTiles → MapLibre down to the exact `pmtiles extract` invocation, with the ADR scheduled for DU-05. So the work was to build the *client half* of a decision already made, dev-gated, pre-empting no ADR. 1.3 MB extract via HTTP range requests against a 137 GB planet build.

**Two self-inflicted defects, both caught by measuring instead of believing my own comments.** This is the part worth keeping:

1. I put the assets in `web/public/` — which `vite.config.ts`'s own **"Footgun 1"** comment explicitly forbids, in the very file I was editing: *"never placed in public/ (public/ copies verbatim into dist/, defeating the goal)"*. 4.1 MB of dev fixture shipped into the production build. Moved to `web/dev-assets/` behind a dev-only middleware with real HTTP `Range` support (`206`/suffix/`416`), because the PMTiles reader addresses the archive by byte range.
2. I wrote a code comment asserting the basemap was *"tree-shaken out of the production bundle."* It was not. Grepping the built bundle showed `@protomaps/basemaps` shook out but the **`pmtiles` reader did not** and reached production. Fixed with a dev-gated dynamic `import()`, plus deliberately **not** re-exporting `basemap` from the barrel — a re-export would silently restore the leak. `dist`: 5.3M → 1.1M.

Neither would have been caught by a test. Both were caught by building the thing and looking at the output.

**Course-feed audit (the session's last act).** Findings below; the significant one is that `agent-ops.md` lists "git-cliff changelog" under *"In place from D0 + the ramp-up"* and it was never in place — no `cliff.toml`, no `CHANGELOG.md`, git-cliff in no manifest. Ramp-up steps 20 and 26 were skipped. Fixed this session: `cliff.toml` + a generated `CHANGELOG.md`; 183 commits = 75 merges (deliberately skipped) + 108 conventional commits, all 108 present, none lost.

| Course artifact | State |
|---|---|
| `docs/adr/` | ✅ 0001–0019. **Six pending Ben's approval** (0014–0019) |
| `docs/failures/` | ✅ FAIL-001…006, each with a regression eval |
| `prompts/` | ✅ `research.md`; `planner.md` due DU-04 |
| `evals/history.csv` | ✅ **correctly absent** — scheduled for DU-04, documented in `eval-quality.yml` job 8 |
| `CHANGELOG.md` + `cliff.toml` | ❌ → ✅ **fixed this session** |
| `docs/devlog/` | ⚠️ this entry closes 08-07; **2026-08-06's DU-03 batch (PRs #63–#71) has no entry** |
| `exhibit/*` tags | ⚠️ only 3, all U1/U2 ramp-up era. **DU-01…03 candidates never tagged — that is unchecked T070** |
| `course-wishlist` issues | ⚠️ none opened |

## Decisions

- **The dev basemap is explicitly not DU-05** — production keeps `EMPTY_STYLE`, no `TileSourceV1` manifest entry, no ADR. Ben chose this over both a full DU-05 tile slice and a throwaway hosted style. **No ADR: nothing was decided that `docs/data/tile-source.md` had not already decided.** Noted at the top of that card instead.
- **Cleared #66's diff-guard by rebasing, not by labelling.** `size-override` would have worked and cost nothing, and that is exactly why it was wrong — it writes a false claim into the record to save a two-minute CI run.
- **Dev basemap assets live outside `publicDir`**, served by a dev-only Vite middleware, rather than in `public/` — enforcing the config's own Footgun 1.
- **`basemap` is not re-exported from `src/map/index.ts`**, deliberately, with the reason in the file: the barrel is what `main.ts` imports and a re-export defeats the dynamic-import gate.

## Failures

No product FAIL-NNN entries this session — nothing shipped broken. Recorded here as **process**, following the 2026-08-01 precedent for operator error:

- **Violated a documented invariant written in the file I was editing** (Footgun 1). The comment was correct, present, and unread. Caught by inspecting `dist/`, not by review.
- **Asserted a build property in a code comment without measuring it.** "Tree-shaken" was plausible, conventional, and false.

**Recommendation for Ben, not taken unilaterally:** the second one argues for a **FAIL entry plus a guardrail** — a test asserting the dev basemap never reaches `dist/`. The existing tripwire (`maximumFileSizeToCacheInBytes`) does not cover this path, because `globPatterns` excludes `.pmtiles` from precache, so the leak was invisible to it. That is a genuine hole in an existing guard, which is usually the bar for a FAIL entry. Deferred rather than decided.

## Cost / turns

One interactive session, ~382 captured hook events, no subagents dispatched. Four PRs merged (#66, #67, #68, #72), one migration applied, one live research pass (~40 s, 785 sites). Wall-clock dominated by CI waits, not work.

## Exhibit-tag candidates

- `exhibit/U2-red-check-that-was-not` — **the strongest of these.** Nine red checks, eight of them infrastructure debris, one real; the discriminator was *job duration*, not the logs. Teaches "reproduce before you debug" and the cost of the opposite.
- `exhibit/U2-false-green-migration` — `alembic upgrade head` on a stale checkout: reports success, changes nothing, because `heads` was computed from the working tree. A whole class of "the command succeeded" bugs.
- `exhibit/U2-override-that-lies` — three ways to clear a red check, two of which work, one of which is honest. Governance as a habit rather than a mechanism (branch protection is unavailable on this repo).
- `exhibit/U5-footgun-in-the-file-i-edited` — a correct, well-written warning comment sitting in the file being modified, and violated anyway. Argues for executable guards over prose.
- `exhibit/U3-covered-means-researched` — ADR-0018 on live data: 783 sites inside a viewport that is 11% researched. A proxy metric that is right until the user pans.

Also still outstanding from T070 and never tagged: `exhibit/U4-area-resolution`, `exhibit/U4-duckdb-overture`, `exhibit/U3-grounding`, `exhibit/U3-merge-provenance`.
