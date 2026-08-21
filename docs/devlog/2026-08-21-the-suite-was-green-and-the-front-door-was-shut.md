# 2026-08-21 — The suite was green and the front door was shut

**Covers: 2026-08-21**

**Goal:** execute Wave A of `docs/design/ux-review-2026-08-21.md` — five fixes, one PR — and then
**stop** at the plan's hard gate to re-measure and hand a diff back. Not to keep going into
Waves B and C, which is the whole reason the plan put a gate there.

**Shape:** one branch (`agent/ux-A-front-door`), two commits, 482 changed lines, five findings
closed, one `KNOWN_RED` row deleted, one failure entry filed, one ADR drafted as *proposed*.
Ben answered all four open decisions up front; only D-4 touched this session's code.

---

## The finding that mattered, and why the one-line fix was not the task

`POST /areas {name}` runs for ~65 s and then said nothing at all. The server had answered
correctly the whole time — `404` with eight candidates, their bboxes and their source stamps —
and the client threw all of it away, because `areas.ts` read `candidates` from the **root** of the
body while FastAPI nests an `HTTPException`'s payload under `detail`.

The fix is one expression. **The interesting part is that a 265-line test suite was green over it
for five days.** Its fixture built the `404` as `{message, candidates}`, un-nested — so the double
and the client agreed with each other about a shape the server does not send. Meanwhile
`tests/test_api_areas.py` had been asserting `response.json()["detail"]["candidates"]` — the
correct shape — since the endpoint was written.

**Two suites, two shapes, one of them fictional, and no assertion anywhere that they were the
same shape.** Both sides were tested. Neither side was ever compared.

That is FAIL-013 recurring *inside its own fix*, and FAIL-015's lesson with a fixture in the role
the dev server played there. Filed as **FAIL-017**. The rule it leaves behind:

> A test double is evidence about the double until something proves it matches the original.
> Coverage of the consumer plus coverage of the producer does not add up to coverage of the
> contract between them — and the gap is invisible from inside either suite.

So the guardrail is not "the chooser renders". `tests/test_api_areas.py` now drives the real app
and writes `web/test/fixtures/area-404-wire.json` from a live response, asserting equality; the
web suite **replays that file byte for byte**. The client reads `detail` **only** — tolerating
both shapes would have let the original fixture keep passing, which is the trap rather than the
fix. Mutation-proved both ways before landing.

The repo already knew this, incidentally. `api/plans.py` builds its `409`s as a bare
`JSONResponse` *precisely to avoid the nesting*, and says so in a comment naming the client that
reads them. The knowledge existed, in prose, one file away, and nothing enforced it.

## The fix that was bigger than its line count

R-02 read as trivial — "never disable `Use this view` while a name search runs" — and wasn't.
`emit()` guarded double-submits on a single `busy` flag, so simply enabling the button would have
produced a control that the guard silently swallows: the same dead end, restyled.

It needed pre-emption. `inFlight` became `'search' | 'viewport' | null`; a viewport delimit during
a search now supersedes it, and the finishing loser no longer tears down the winner's busy line.
Which then created a race that did not exist before — two delimits in flight, and the 65 s one can
land **last**, re-pointing the reuse surface, the form and the map at an area the user walked away
from. **An escape hatch that becomes a delayed hijack is not an escape hatch**, so
`resolveAndApply` took a `stillWanted` predicate and `main.ts` a generation counter.

Worth recording: the guard went *inside* `resolveAndApply` rather than inlining its two halves in
`main.ts`, because inlining would have left a helper that `areas.test.ts` tests end-to-end and
that nothing ships — FAIL-015's shape, created while cleaning up after FAIL-013's.

## The gate corrected me, which is what it is for

D-4 (Ben: raise the six form labels to 14 px, keep the ODbL credit at 12 px behind its own named
exemption) looked done after the CSS change. The DU-06a gate said `T-03-type` was still red at all
three widths, and `SIYUR_GATE_NO_XFAIL=1` said why: the credit's text lives in MapLibre's
`.maplibregl-ctrl-attrib-inner` children, not on the `.siyur-attrib` class I had exempted.

The plan's constraint — *run the gate before deleting, and let it tell you which rows flipped, do
not delete the ones you expect to* — is the only reason that was caught before the row was
deleted. The exemption is now anchored through our own class (`.siyur-attrib *`) so a MapLibre
rename narrows it to nothing rather than silently keeping it.

## What the re-measure could and could not close

Verified against a **running** API and browser, not a fixture: the chooser renders eight real
candidates (`hostChildren` 0 → 1); `Use this view` measured `disabled: false` eight seconds into a
real 65 s search; the search glyph is 7.85 : 1; the AREA field reads "The area shown on the map"
with the UUID moved onto the element.

**Not closed, and stated rather than explained away:** Chrome would not resize below ~1461 px this
session, so the width-dependent numbers in Appendix A — scaffolding height, distinct type sizes
and radii — are **not comparable to the 500 px baseline** and are reported as such. The Playwright
gate covers the type floor at a true 375/390/430; it does not cover the other two.

## Decisions

Ben answered all four. **D-4** raised the labels (done here). **D-1** chose per-stop attribution,
which reopens ADR-0019 — whose ratified invariant forbids a per-stop sources line in as many
words. **ADR-0037 is therefore drafted as `proposed`, not accepted**, and C-1 stays blocked until
it is ratified. Writing it up as a compatible reading would have been the convenient lie; 29 % of
the plan panel being two strings repeated twenty-one times is a real problem and still not a
licence to reinterpret a ratified decision quietly.

D-2 (what names an area) and D-3 (budget vs planner fit) remain open and gate Wave C.

## Housekeeping

Three peer worktrees and two live sessions; a peer merged #137 mid-session and another held ports
8000/5173/5432. Ran on 5178/8004/5436 throughout — FAIL-011 says a worktree isolates files and
nothing else, and the peer's stack was verified untouched at the end.

**Devlog debt noted, not paid:** 2026-08-18 and 2026-08-20 have commits and no entry.
