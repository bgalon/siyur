# 2026-08-15 — The audit that had to not fix anything

**Covers: 2026-08-14 → 2026-08-15**

**Goal:** Find out precisely how bad Siyur is as a *product* on a phone, after nine deliverable
units merged green and the operator opened it and said: *"many pieces but they are not connected
together at all, with many errors, and this is not usable on mobile at all."* Measure, record,
then propose a fix plan. **Explicitly forbidden from fixing anything during the audit** — the
brief was blunt that the urge to fix a CSS bug on sight would end the sweep.

## What happened

**The isolation step paid for itself immediately, in an unexpected way.** Standard opening — `git
worktree list`, `gh pr list`, `ListAgents` — found five other worktrees, one peer session idle 7
days, and no open PRs. New worktree on `origin/main`, which turned out to be *one commit ahead*
of local `main`. Fine.

The thing that nearly invalidated the whole audit surfaced 40 minutes later, from an unrelated
`ps`: **the dev stack on 5173/8000 is not served from `main`.** Both processes run out of
`.claude/worktrees/spec-002-plan-compile-offline`, branch `agent/du06-offline` — another
session's checkout, reached through fixed ports. This is FAIL-011's exact shape, encountered from
the other side: not a test fixture wiping a live database, but an *auditor measuring a checkout
they did not think they were measuring*. Everything to that point had been recorded as a finding
about `main`. The salvage was one command —

```
git diff --name-only origin/main c0cb945
docker-compose.yml
scripts/dev.sh
```

— `web/`, `api/`, `planner/`, `commons/` byte-identical, so every measurement stood. It stood by
luck, not by method, and the method (diff before trusting) is now written into the report's
§2 rather than left as a war story.

**Two tool constraints forced a better technique than the one intended.** Chrome on macOS refuses
to size a window below ~400 CSS px, and `resize_window` was unreliable besides — a request for
390 × 1024 produced a 606 × 847 viewport. The spec's 375 px target was therefore *unmeasurable* by
the obvious route. Loading the app into a **same-origin iframe sized to exact CSS pixels** gave
true 375/390/430 viewports plus full JS access for measurement, which is strictly better than
screenshots: every number in the report is a `getBoundingClientRect` / `getComputedStyle` value.
The honest cost — no touch, DPR or mobile-UA emulation — is stated as a caveat rather than
buried.

Separately, `javascript_tool` got blocked by the permission classifier twice, both times on calls
that touched `document.cookie`. Correct behaviour on its part; the workaround was simply to stop
reading cookies in JS.

**The first cold open was not a cold open.** The browser already held a session cookie from the
operator's earlier use, so the "unauthenticated" screenshot was authenticated. Caught it by
actually reading `document.cookie` rather than assuming, cleared it, and re-ran. The real cold
open is worse than the fake one: the app renders *identically* signed out, and `GET /auth/login`
returns **503 — Google SSO is not configured**, so there is no sign-in path at all.

**The single most useful assertion turned out to be three lines.** Measuring tap-target heights
found 11 of 12 controls under 44 px, which is bad but tunable. Running `elementFromPoint` at each
control's own centre found something categorically worse: `Use this view` — the delimit path that
resolves in **0.18 s** — is painted under the plan panel and is unreachable **in every scroll
state** at 375 and 390 px. I nearly reported `Plan this day` and `Approve this day` as unreachable
too, then scrolled the panel's inner scroller and found they came back. That correction is the
difference between "blocker" and "major" and it only exists because I re-tested instead of
shipping the first reading — the same discipline FAIL-011 records as the thing that saved it.

**Then the two halves met.** The only *reachable* delimit control is the search pill. Timed
against the API: `{bbox}` → **200 in 0.18 s**; `{name}` → **404 in 61.6 s**, carrying a genuinely
well-designed payload — *"20 plausible areas match…; ask the user which one"* with confidences,
bboxes and ODbL source stamps. `web/src/map/areas.ts:85` is
`if (!response.ok) throw new AreaRequestError(response.status)`: **body dropped, status kept**,
routed to `console.warn`. `grep -rn "candidates" web/src/` returns nothing in the area path. So
the mobile cold-start is: the reachable control appears to do nothing for a minute, then still
appears to do nothing. That is the audit's headline and it is a *seam* defect — both sides
green, the payload lost between them.

**The emblematic finding is one DOM element.** During an active `POST /plans` stream the review
element carries `data-plan-state="proposing"` while rendering the words **"No day has been
proposed yet."** The correct copy exists at `render.ts:199` and is never reached;
`grep "plan-state" plan.css` returns nothing. The state machine is right, the attribute a test
would assert is right, and the sentence a human reads is the opposite of the truth. Nine units of
`curl` + `pytest` + `vitest` cannot see that, because every one of those tools reads attributes
and status codes.

**Reporting the good parts honestly changed my own read of the codebase.** The approve gate
separates warnings from violations three independent ways — different classes, **dashed vs solid
(a non-colour separator)**, distinct hues — and its CSS comment discloses its own weakest number
(a 1.88:1 violation border) rather than hiding it. I recomputed every WCAG figure it claims from
the hex values: 8.44, 7.69, 3.66, 7.56, 7.72 — accurate. And the CSS has **zero** physical
direction properties against 35 logical ones, so the RTL debt the brief expected me to find
simply is not there. An audit that had only listed problems would have been a worse document and,
more to the point, a less accurate one.

**One thing I could not finish, stated plainly.** The research pass ran **>9 minutes without
completing**, frozen on one stage label at 99% CPU, and the plan submitted behind it queued
**8+ minutes** behind the single uvicorn worker. So the approve gate was never audited against a
*live* proposal with real warnings and violations — that part was assessed from computed styles
and the schema instead, which is weaker evidence and is labelled as such in the report. I also
contributed to the contention by starting a plan during a research pass, and said so rather than
presenting the timing as clean.

**Deliverables:** `docs/design/ux-audit-2026-08-15.md` (17 findings UX-01…UX-17, measurement
tables, an accurate "what works", and a 3-tier fix plan sized in days), three FAIL entries, one
proposed ADR. **No code changed** — `git status` at commit time: 5 new docs files, nothing else.

## Decisions

- Below 760 px the app becomes one scroll column rather than three independently-positioned fixed
  layers — chosen over tuning offsets (preserves the bug's generator) and over the real bottom
  sheet with snap points (right answer, multi-day, wants unbuilt work), and shaped so the sheet
  can absorb it later → **ADR-0035** (`proposed`)
- Findings catalogued as UX-NN in the report and rolled up into three root-cause FAIL entries,
  rather than 17 separate ones — a judgment call against a literal reading of Article IV, flagged
  in the report's §8 for Ben to overrule
- The audit measures the *running* stack, with a mandatory `git diff` against `origin/main` before
  any measurement is trusted — now written into the report's method section

## Failures

- Nine units shipped green; no phone viewport was ever rendered, and every gate the project runs
  is structurally blind to layout (jsdom does no layout; `curl` sees status codes) →
  **FAIL-012** (guardrail: Playwright rendered-viewport suite at 375/390/430 asserting
  `elementFromPoint` reachability, ≥44 px targets, ≥14 px type, no h-overflow, and attribution
  visible *including at 1440* — must be demonstrated red on today's `main` before it may go green)
- A structured 404 body narrowed to a status code at the client boundary, then routed to a console
  the user cannot see → **FAIL-013** (guardrail: a contract test that a `404` with
  `detail.candidates` rejects with the *parsed detail* intact; plus a Playwright assertion that
  any request-triggering control reaches a terminal visible state — "unchanged" is a test failure)
- ADR-0031's reader half is open — glyph/sprite digests typed away and never verified. **The ADR
  says so itself, in bold**; the failure is that `accepted` is a binary status doing a tri-state's
  job → **FAIL-014** (guardrail: conformance test that every `sha256` in the schema card's own
  example survives `parseManifest`; a corrupted-fixture test proving the launch check refuses;
  and a lint rejecting plain `accepted` on an ADR whose body says it is not discharged)

*(All three guardrails are specified, none is implemented — the brief was audit-only. Each entry
states explicitly that it does not close until its guardrail is in CI.)*

## Cost / turns

~50 assistant turns over roughly 3½ hours wall-clock, spanning 2026-08-14 → 2026-08-15. Heaviest
consumers were browser measurement round-trips and two long API timings (61.6 s name resolve;
a research pass abandoned after 9+ minutes). Output: 911 lines of documentation across 5 files,
0 lines of code. The PR will exceed the 500-line diff-guard and legitimately warrants
`size-override` — the claim "this PR is >500 lines, override justified" is true here, which per
AGENTS.md is the only condition under which the label may be used.

## Exhibit-tag candidates

Proposed, for Ben to approve:

- **`exhibit/U2-green-tests-blind-to-pixels`** — the `data-plan-state="proposing"` element
  rendering "No day has been proposed yet." One screenshot and one grep show why a green suite and
  a working product are different claims, and exactly which class of tool cannot tell them apart.
  The strongest single teaching artifact this session produced.
- **`exhibit/U2-the-assertion-that-was-three-lines`** — `elementFromPoint` at each control's
  centre, versus measuring tap-target sizes. Same afternoon, same file; one finds tunable friction,
  the other finds an unreachable primary control. Teaches choosing the assertion that can express
  the failure.
- **`exhibit/U7-the-checkout-i-was-not-measuring`** — the dev stack served from another session's
  worktree, found by accident, salvaged by one `git diff`. FAIL-011 from the auditor's side, and a
  concrete lesson in verifying *what* you measured before trusting *what* you measured.
- **`exhibit/U0-the-answer-thrown-away-three-times`** — a thoughtful 404 with 20 candidates →
  narrowed to `404` → `console.warn` → nothing. Three individually defensible steps composing into
  a product that appears broken. Good material on seams between green halves.
- **`exhibit/U2-auditing-the-good-parts`** — recomputing the approve gate's own claimed WCAG
  figures and finding them exact, plus the zero-physical-CSS-properties result that killed an
  expected RTL-debt finding. Teaches that an audit's credibility rests on what it *declines* to
  call a defect.
</content>
