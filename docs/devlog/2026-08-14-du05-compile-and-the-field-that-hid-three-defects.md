# 2026-08-14 — DU-05 compile, and the field that hid three defects

**Goal:** land Spec 002 Phase 5 (DU-05, compile) as orchestrator — plan the slices, delegate
them, review, and land them through PRs rather than writing every line.

**Shape of the day:** nine subagents across five waves, eight tasks (T035, T035a, T035b, T036,
T037, T042, T043, T047), five PRs merged (#112–#116), three ADRs (0031, 0032, 0033), one
failure catalogued (FAIL-011). `main` ended at `9868201`, green: 1363 Tier 1, 128 Tier 2, 32
evals, ruff/format/mypy clean on 105 files.

## What happened

The session opened by checking what the concurrent session held rather than reading the board.
That was the right order: `specs/002-plan-compile-offline/tasks.md` said 29/45 and both the
count and the denominator were wrong — the peer session had it at 49/74 mid-resync. Filesystem
ground truth settled it in one command: `compiler/` had quarantine, manifest, attribution and
storage but **no `tiles.py`, `routes.py` or `pipeline.py`**, and `api/` had no `bundles.py`.
That missing middle was the whole critical path to M1, and it was unclaimed.

The partition with the peer session held all day without a single file collision between
sessions — they took `api/plans.py`, the web wiring and the governance resync; I took the
compile spine. Every hazard that did materialise came from **shared infrastructure**, not
shared files, which is the theme of the day.

### The field that hid three defects

T035b was written as "hash the glyph and sprite artifacts". Implementing it found that
`TileSourceV1` had **one** asset ref standing for two licences, and that single fact was
concealing three separate defects, each invisible while the field was shared:

1. **Glyphs had no hash** — the integrity gap the task named. A corrupted glyph range is not a
   crash: MapLibre requests it, cannot parse it, draws no glyph, reports no error. Every label
   renders as nothing while `manifest_sha256` still verifies, because the manifest never
   claimed anything about those bytes.
2. **Sprites had no *field at all*** — not an integrity gap but an **FR-021** one. The compiler
   wrote `sprites/*` into the bundle and nothing in the manifest pointed at them. Findable only
   because implementing (1) forced someone to name the field that would hold the hash.
3. **Every bundle credited MIT sprite sheets under OFL-1.1**, asserting OFL's "don't sell fonts
   standalone" over assets it does not cover — a compliance defect in the artifact the traveller
   downloads.

And then a fourth, found by the agent fixing (3): `ATTRIBUTION.md` asserted that `OFL.txt`
ships beside the glyphs and that MIT's licence text travels with the work, and **nothing wrote
either file**. OFL-1.1 genuinely requires the text to accompany the fonts, so we would have
shipped Noto glyphs in violation *while claiming compliance in the same artifact*. Ben chose to
fix it inside DU-05 rather than ticket it; both texts are now vendored verbatim and emitted
into the bundle through the existing digest path.

The pattern worth keeping: **asserting an obligation you do not discharge is worse than
silence**, because the claim is what a reviewer relies on. Three of the four defects were
undiscoverable until one field was split into two.

### Re-routing an approved day

The pipeline agent was told to compose `compile_routes` and refused, correctly. That function's
first act is to call the routing provider, so the obvious wiring **re-routes a day a human has
already approved** — replacing the distances and durations the feasibility gate checked, while
`content/itinerary.json` still carries the approved ones. Two hashed artifacts describing one
day, disagreeing, both verifying, read by someone with no connectivity and no way to adjudicate.

The distinction that settles it: the legs are part of what was approved (ADR-0023's CAS is over
an itinerary hash containing them); the walk graph is a recovery aid nobody approved. A second,
independent reason a reviewer can check in ten seconds: routing refuses fewer than two
waypoints, so a **one-stop day could not be compiled at all**.

`run_compile` now takes no routing provider. The absence is the enforcement.

The peer sharpened the ADR after I drafted it: I had written that compile "has no write path
over the plan state machine", which `api/bundles.py` falsifies in the very next commit. The
precise rule is **compile writes plan *status*; it never writes plan *content*** — stronger, and
it gives the reaper a principled home instead of being an exception I had to apologise for.

### A suppression that suppressed nothing

`merge-guard.sh` blocked #113 on a genuine semgrep finding — the `pmtiles` subprocess call.
Ben approved the repo's first suppression, scoped to one line. My first two attempts **silently
suppressed nothing**: the console prints a truncated `check_id`, and the real one repeats its
final segment. A control that appears active and isn't — the same family as the un-noded walk
graph and the tests that pass for the wrong reason.

Worse, I wrote `test_the_extractor_never_uses_a_shell` into the justification comment *before
writing the test*. Caught in the same breath, but that is exactly the false-claim shape the day
was otherwise spent fixing. The test now exists and pins the premise, because `shell=True` is a
one-word edit that would make the suppressed finding genuinely exploitable with nothing going
red.

## Failures

- **FAIL-011 — the Tier-2 harness truncated a live dev database.** `db_session` deletes every
  row in every table; both checkouts share one PostGIS on `:5432` because `docker-compose.yml`
  binds a fixed port. My integration runs destroyed the peer's data repeatedly, and they spent
  half an hour diagnosing it as a persistence bug in `POST /areas` — the endpoint was committing
  correctly and rows were vanishing underneath a running server. **The symptom pointed at
  innocent code.** This is FAIL-008's family one layer down: a worktree isolates *files* and
  isolates nothing else — not a database, a port, a volume, or a Valhalla graph.
  → FAIL-011 (entry by the peer session; guardrail: `tests/conftest.py::_derive_disposable_url`,
  regression test `tests/test_db_harness.py`, merged in #116)

  The guardrail derives a `<name>_test` database rather than requiring an opt-in variable, because
  CI's database is *also* named `siyur` and an opt-in protects you exactly until someone sets it
  once and moves on. Verified by measurement: after a full Tier-2 run the dev database still held
  459 sites and 3 areas.

### My own process failures, for the record

- **Both DU-05 commits landed on local `main`.** I branched before the first edit as ADR-0005
  requires, but the checkout was switched off my branch mid-session and I committed without
  re-checking. Caught by an agent's report, not by me. Never pushed; commits moved and `main`
  reset. Lesson: `git branch --show-current` before every commit, not only at session start.
- **I put two agents in one file.** `licfix` was given `tests/test_compiler_attribution.py` while
  `glyphhash` still held it — the partition rule CLAUDE.md calls non-optional. No work was lost,
  but that was luck. A file stays locked until its agent reports *done*, not until it looks idle.
- **I asserted a fact about CI I had not checked.** Told an agent that PR #103 already excluded
  licence texts from the diff-guard; the globs are root-anchored, so `data/licenses/**` is
  counted. The agent verified and corrected me, and declined the workaround available to it
  (naming the files `LICENSE-*` at the repo root) because that would misstate our own licensing
  to save a line count.
- **My first FAIL-011 guardrail failed CI**, because its unit tests called the connecting
  function and the Tier-1 lane has no database — while the PR body claimed those tests never
  connect. Fixed by splitting the pure derivation out, and this time I reproduced the CI lane
  locally instead of reasoning about it.

## Decisions

- Glyph and sprite artifacts carry their own integrity hash; the sprite ref is **created**, not
  amended → **ADR-0031** (deliberately marked *half-implemented*: `web/` records the digests
  without verifying them, so the bundle currently states coverage it does not enforce)
- The approved day is frozen at compile, never recomputed; compile writes status, never content
  → **ADR-0032**
- `bundle_id` is `bnd_<plan_id.hex>`; no bundle table → **ADR-0033** (closes `data-model.md` G12)

## Things that were true but not verified before today

`pnpm -C web install` unskipped three cross-implementation tests that had been skipping in
**every previous session** — the TS-vs-Python manifest canonicalization pair and the
`geojson-path-finder` recovery check. They pass. Until today nobody had executed the tests that
would catch the two implementations disagreeing on a manifest seal, which fails on the device,
offline, undiagnosably. Tests that have never once run are worse than absent, because their
names appear in the count.

Also corrected by the peer: Valhalla is serving the **Andorra demo extract**, so `docker compose
ps` says healthy, `/status` returns 200, and Rhodes coordinates return `171 No suitable edges`.
"The container is answering" and "routing works for our area" are different facts, and every
health check measures the first. My lane turned out immune — ADR-0032 had already removed the
routing call from compile — but the Tier-2 count is *not* evidence about live routing, because
no test in the suite routes over Valhalla at all.

## Cost / turns

~6 hours wall clock (08:30–14:30 UTC), orchestrator plus **nine subagents**: two implementers in
wave 1 (tiles, routes), one schema amendment, one pipeline, two licence fixes, one API, and two
code reviewers.

**Both reviewers were unusable** — broken Grep/Glob (no ripgrep) and no Bash — and the first
went idle three times without ever saying why. The second diagnosed its own tooling and said so,
which is the only reason the cause is known. The high-risk items were self-verified instead
(projection genericity, subprocess safety, the privacy positive-controls, the re-arm's column
writes), which is weaker than a second pair of eyes and is recorded here as a known gap in how
this code was reviewed.

## Exhibit-tag candidates

*(proposed, for Ben to approve)*

- `exhibit/U5-the-field-that-hid-three-defects` — one schema field standing for two licences
  concealed a wrong credit, a missing manifest reference, and an undischarged obligation. None
  were findable until it was split.
- `exhibit/U5-frozen-not-recomputed` — why a compile step that recomputes anything the human
  approved is a second planning pass with no gate in front of it (ADR-0032).
- `exhibit/U0-the-suppression-that-suppressed-nothing` — a truncated rule id meant the first two
  `# nosemgrep` attempts were decorative, and a suppression whose premise nothing tests is how a
  real vulnerability ends up wearing a comment saying it is fine.
- `exhibit/U0-worktrees-do-not-isolate-a-database` — FAIL-011: file isolation read as isolation,
  and the resulting symptom pointed at innocent code for half an hour.
