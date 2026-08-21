# 2026-08-21 — The gate that caught a defect, then caught itself

**Covers: 2026-08-15 → 2026-08-21**

**Goal:** build DU-06a — a rendered-viewport gate that lands **red** — then use it to drive DU-06b.
Two of the audit's twelve controls were the whole point: `Use this view`, painted under the plan
panel and unreachable at 375/390, and the ODbL credit, occluded at every width including 1440.

> Companion entries: `2026-08-15-the-audit-that-had-to-not-fix-anything.md` (the measurement) and
> `2026-08-16-nine-green-units-and-a-product-nobody-could-use.md` (the peer session that did the
> phone fixes). This one covers the gate, ADR-0036, two `dev.sh` defects, and the review that
> followed the fixes.

## What happened

**The spec wasn't on `main`.** The plan naming my tasks lived unmerged on another branch, and a
*second* plan — `usable-m1-plan.md`, with Ben's decisions already recorded — was on `main` saying
something different about sign-in. Two live plans, one of them answering a question my brief still
posed as open. Ten minutes of reading saved a wrong decision.

**PR #128 had already done T-15**, opened four hours earlier by a session that was still running.
The first real work of the session was not writing anything.

**The gate landed red, and the red was the deliverable.** 13 failed / 4 passed against `main`,
reproducing the audit independently: 11 of 13 controls under the 44 px floor, 17 text elements
under the type floor, `Use this view` covered by the plan panel's own `Plan a day` heading. Known
failures ship as `test.fail()`, keyed **per (assertion, width)** — which mattered four days later
when the peer's flow column cleared 375 and 390 and *did not* clear 430. A single marker across
three widths would have been deleted wholesale.

**Review caught the gate going blind exactly where it mattered.** The first draft skipped any
control whose centre fell outside the viewport. Fine on a fixed-panel layout; catastrophic the
moment ADR-0035's scroll column landed, because every control below the fold would have silently
left the checked set — while T-10 deleted the markers on the strength of that green. A gate that
loses its sight precisely when the thing it guards changes.

**Then the gate found a defect nobody had reported.** Its first CI run went red on something the
audit never saw: the plan form is `grid-template-columns: 1fr 1fr`, a bare `1fr` is
`minmax(auto, 1fr)`, and an `<input>`'s automatic minimum is its ~20-character `size`. The form had
a **hard floor and did not respond to the viewport at all** — 340 px on macOS, **388 px under CI's
Linux fonts, inside a 375 px screen**. `scrollWidth === clientWidth` was green throughout and always
would have been: every panel is `position: fixed`, so an overflowing form never reaches the
document's scroll width. **It does not reproduce on the machine the app is developed on.**

That forced the session's one deliberate rule-break. DU-06a was scoped to fix nothing, but marking
it `test.fail()` would have made the marker *environment-dependent* — red in CI, "Expected to fail,
but passed" on every Mac — and deleting an assertion because it found a bug is FAIL-007 in new
clothes. So DU-06a shipped exactly one behavioural fix, and it was the one the gate produced.

**A near-miss that cost nothing only because of one command.** About to start the CSS work, a
`git worktree list` showed the locked checkout had switched to `agent/phase-b-mobile` with ten
uncommitted files — the whole of Phase B, plus a *second* viewport suite built in parallel. Stopped
before touching a stylesheet and messaged the session. We split in two exchanges: they held Phase B,
I took D-1 and the walkthrough, they deleted their duplicate suite in favour of the required one.
The thing that saved it was checking **before** the first edit, which is exactly what FAIL-008 says
and exactly what is easy not to do when the task list is in front of you.

**ADR-0036 nearly shipped a wrong answer confidently.** The `window` parameter cuts a name lookup
from 73 s to 18 s, but it is a `WHERE` clause, so searching "Paris" while looking at Rhodes returns
nothing. Ben chose windowed-first-with-fallback. Review then found something I had read past twice:
a windowed miss **fell through to the unwindowed Nominatim geocoder**, so a window silently changed
*which source answers* — a confident `200` carrying an OSM ring instead of the Overture division
that exists, and the caller never sees the empty result it is required to widen on. **The bug hides
because it succeeds.** Two of the tests the ADR cited as its own confirmation could not fail.

**Two `dev.sh` defects, found only by trying to run the thing.** Following AGENTS.md's own isolation
recipe — take your own ports — brought up a stack that reported healthy and `502`'d every browser
request: `SIYUR_API_PORT` moved the API and nothing told the proxy. Then, minutes into a plan,
`Missing Anthropic API Key` — because `grep -ci anthropic scripts/dev.sh` returned **0**, while
AGENTS.md tells every agent the script loads it "so you can stop thinking about it". Both are the
same shape: **the script's environment was incomplete and the documentation asserted otherwise.**

**The 390 px journey completed** — delimit → reuse 748 cited places → open a site → plan → approve.
Worth more than a green tick: the first proposal came back `hours 4.97 > budget 4.00` with approve
correctly **disabled**, so the recording exercises the HITL gate *and* the recovery.

**Then the gate caught its own author.** `T-02` carried `430×932` in `KNOWN_RED` for two days with a
confident wrong explanation attached. It was never a defect — the off-screen test asked
`before.top >= innerHeight` while the hit test probes the *centre*, so an element at top=922 in a
932-tall viewport was never scrolled to and was then probed below the fold, where `elementFromPoint`
returns `null`. **`(nothing painted there)` should have read as a probe smell**: nothing in a
rendered page is painted by nobody. A red row is evidence, and this one was evidence of the wrong
thing.

**Last, the product was driven rather than tested** — Claude-in-Chrome, every flow, against a live
stack. A name search took **65 seconds and then said nothing at all**. The server answers `404`
carrying its candidates; the client reads them from the wrong level of the envelope and throws the
status-only error FAIL-013 was filed about. Its 265-line test suite passes because the fixture sends
a shape the API has never produced. **The chooser has never worked in production**, and it was
shipped, tested, documented and demoed.

Also measured, not asserted: **29 %** of the plan panel's text is attribution (840 of 2,874
characters, 2 strings × 21); the default 4 h budget produces an infeasible first plan (4.92 and 4.97
on two areas); the map is **49 %** occluded by 951 markers of which **84 %** overlap; and all eight
saved plans read "For an area you delimited on the map".

## Decisions

- **A search window is an optimisation, so it may never turn a hit into a miss** → **ADR-0036**
  (accepted 2026-08-18). Windowed first, unwindowed fallback on empty, visible "widening the
  search…"; always-window explicitly rejected; the retry lives in the client because only the
  client can render the widening state.
- **Below 760 px the app is one scroll column** → **ADR-0035** (accepted 2026-08-15, ratified by
  the peer session whose layout work it authorises).
- **DU-06a ships exactly one behavioural fix** — the grid floor its own gate found — because the
  alternatives were an environment-dependent marker or deleting an assertion for finding a bug.
- **Job 5 runs `viewport.spec.ts` only.** A scope decision, not a quality one: the airplane suite
  belongs to DU-06/T056. Recorded after establishing that its one local failure was a stale preview
  server, not the suite.

## Failures

- **`dev.sh` did not do what AGENTS.md says it does** → **FAIL-016** (guardrail:
  `tests/test_dev_script_ports.py` — *executes* the script's configuration prologue and reads the
  exported variables back, rather than grepping for a line; mutation-proved, 2 of 5 go red when the
  export is removed).
- **The fix for FAIL-013 has never worked** → **FAIL-017** — reported here, **fixed and merged the
  same day in #139** by the session that owned the code, and verified here rather than taken on
  trust. Its guardrail asserts the *negative* (nothing at the root), which is better than the one
  this entry originally specified. ~~(guardrail specified, lands with Wave A task A-1: pin the fixture to `_unresolved_detail`'s output, plus one Tier-2 test that crosses the
  wire. **Does not close until the second one is in CI** — more unit tests on the same double catch
  nothing).
- Not a catalogued failure but worth the line: **a red `KNOWN_RED` row carried a wrong explanation
  for two days.** Fixed in #133 with the reasoning left in the file rather than the git log.

## Cost / turns

Seven calendar days, one long session, ~21.7k captured hook events. Six PRs merged (**#129** the
gate, **#131** ADR-0036, **#132** its server half, **#133** the probe fix, **#134** the `dev.sh`
defects, **#135** the journey recording), plus this entry and the review. Two `code-reviewer` passes,
both of which returned findings that changed the shipped result — the off-viewport skip and the
geocoder fall-through. Roughly 3.5 engineering-days of work is now specified and unstarted
(`ux-review-2026-08-21.md`).

## Exhibit-tag candidates

*(proposed, for Ben — the peer session already proposed `exhibit/U5-the-countdown-that-makes-a-gate-a-control` for the `KNOWN_RED` mechanism, so it is not repeated here.)*

- **`exhibit/U2-the-defect-that-only-exists-on-ci`** — a form with a hard minimum width, invisible to
  `scrollWidth` because every panel is `position: fixed`, and absent on the developer's own machine
  because macOS fonts are narrower. Found by the gate's first CI run, not by anyone looking.
- **`exhibit/U2-a-red-row-that-was-evidence-of-the-wrong-thing`** — a probe that reported a real
  control as unreachable because its off-screen test and its hit test disagreed about which point
  they meant. Two days in the table with a confident explanation attached.
- **`exhibit/U3-the-bug-that-hides-because-it-succeeds`** — a windowed miss falling through to a
  different source, returning a confident `200` for the wrong place. The failure mode that a status
  code cannot express.
- **`exhibit/U4-the-fixture-that-never-met-the-wire`** — 265 passing lines asserting an envelope the
  server has never sent; a feature shipped, demoed, and dead on arrival. FAIL-013 recurring inside
  its own fix.
- **`exhibit/U1-the-recipe-that-broke-the-thing-it-isolated`** — the repo's own advice for running
  alongside another session, producing a stack that reports healthy and answers `502`.
