# FAIL-017 — The fix for FAIL-013 has never worked, because its test doubled a shape the API does not send

- Date: 2026-08-21 · Severity: high (the product's front door fails silently after 65 seconds; the
  feature was shipped, tested, documented and demoed, and has never once worked against the real API)
- Root-cause class: test-fixture divergence (a hand-authored double asserting a wire format that
  was never checked against the wire)

## Symptom

Typed a name into the search pill, pressed **Find**, waited **65 seconds**. The screen returned to
exactly its initial state:

```
hostChildren : 0          ← the disambiguation container is empty
hintText     : (empty)
anyErrorVisible: false    ← no error text anywhere in the DOM
findDisabled : false      ← re-enabled, as if nothing had happened
```

No chooser. No error. No message. The server had answered `404` **carrying its candidates**, and
the user was told nothing at all.

## Root cause

FastAPI serialises `HTTPException(detail=...)` as `{"detail": …}`. So `api/areas.py`'s
`_unresolved_detail` reaches the wire as:

```json
{ "detail": { "message": "…", "candidates": [ … ] } }
```

`web/src/map/areas.ts` reads the **root**:

```ts
const record = (body as Record<string, unknown>)
const candidates = sanitiseAreaCandidates(record.candidates)   // undefined → []
if (candidates.length > 0) { throw new AreaNotResolvedError(…) }
throw new AreaRequestError(response.status)                    // ← always taken
```

`record.candidates` is `undefined`, so the guard is never entered and the client throws the
status-only error — **the exact line FAIL-013 was filed about**, still executing, inside the
change that was supposed to remove it.

## Why nothing caught it

`web/test/area-disambiguation.test.ts` — 265 lines, comprehensive, passing — builds its `404` as:

```ts
new Response(JSON.stringify({ message: 'several places match that name', candidates }), …)
```

**Un-nested.** The double asserts a shape the API has never produced. Every test in that file
passes whether the client is right or wrong, because both sides of the test agree with each other
and neither was ever compared to the server.

Three layers looked green and none of them touched the wire:

1. **The unit tests** doubled the API, so they proved the parser matches the fixture.
2. **The contract** (`specs/001-research-cited-sites/contracts/areas.md`) documents the body as
   *"`404` name not resolvable (with disambiguation candidates)"* without pinning the envelope,
   so both readings are consistent with it.
3. **The DU-06a viewport gate** runs against a build with **no API behind it** — stated as a known
   limit when it landed, and this is that limit's first bill.

The feature was also demoed. The PR that shipped it showed the chooser rendering — from the
fixture, in a unit test, never from the server.

## Guardrail

**One assertion, and it has to cross the boundary.** More unit tests on the same double would
have caught nothing.

1. **Pin the double to the producer.** A Tier-1 test that builds the `404` body from
   `api/areas.py::_unresolved_detail`'s own output — or asserts the fixture equals it — so the
   fixture cannot drift from the response again without going red.
2. **One test that actually crosses the wire.** A Tier-2 test that posts an unresolvable name to
   the running API and asserts the *client parser* accepts the real body. It is the only assertion
   in the set that could have failed.

### What actually landed — #139, 2026-08-21

Fixed by the session that wrote it, within hours of the report, and **verified here rather than
taken on trust**:

- `areas.ts` reads `detail.candidates` / `detail.message`, and **the root is deliberately not read
  as a fallback**. That is the right call and worth keeping: a fallback would let the two shapes
  drift apart again silently, and this failure is only visible when client and server are *forced*
  to agree.
- The TS fixture now sends the nested body **and names the server-side test in a comment**, so the
  two halves are coupled by something a reader will follow.
- `tests/test_api_areas.py::test_the_404_body_is_nested_under_detail_and_not_at_the_root` asserts
  the half the client got wrong, in the terms it got it wrong in: the payload is under `detail`,
  **and there is nothing at the root to read**. Flattening the body is now a visible breaking
  change. Mutation-proved: revert the client → 1 fails.

**This is a better guardrail than the one specified above**, because it asserts the *negative* —
the absence of anything at the root — which is the specific drift that caused the failure. Both
directions are covered: flatten the server and the Python test fails; point the client back at the
root and the TS test fails.

**Closed.** One residual, named rather than implied: **nothing still executes the client parser
against a real server response.** The two halves are now coupled by two tests and a comment, not by
a round trip, so a change in how FastAPI *serialises* `HTTPException` — as opposed to a change in
what this repo puts inside it — would pass both. That is a dependency-upgrade risk rather than a
drift risk, and it is the residue the Tier-2 test in (2) would have removed. Worth one integration
test the next time anything touches this envelope.

## What it cost, stated plainly

Six days of believing a shipped feature worked. It was merged on 2026-08-15 in #128, described in
that PR as *"the server returned twenty because it could not decide, and choosing on its behalf is
how someone ends up in the wrong city"* — a correct and careful argument about behaviour that has
never run. FAIL-013 was written the same week and its guardrail (1) is the very test that is wrong.

The cheaper lesson is the one FAIL-015 already paid for and this repeats in a new place: **the
artifact under test was not the artifact that ships.** There it was a stylesheet in `dist/`; here
it is a JSON envelope. In both cases every source-level check was green and one minute of driving
the real thing was decisive.

### The variant worth naming

This repo has catalogued several failures where a check was green for the wrong reason. **This one
is a distinct shape, and the sharpest statement of the family so far:**

> The test was green because **its fixture and its code shared one wrong assumption** — both halves
> wrong in the same direction, agreeing with each other, and neither ever compared to the thing
> they model.

That is not "green for the wrong reason" in the usual sense of a weak assertion. The assertion was
fine. The *model* was wrong, symmetrically, so no amount of adding assertions on the same double
could have found it — and adding them is exactly what a diligent author would have done. Only
crossing the boundary finds it: to a real server, a real browser, a real bundle.

Which is the general answer this entry argues for, and the reason the finding came from **driving
the product rather than testing it**. Every automated check in the repo was green when it was
found. *(Framing contributed by the peer session that owned and fixed the code.)*

## Related

- **FAIL-013** — the original discarded `404` body. This is that failure recurring inside its own fix.
- **FAIL-015** — the same class one layer over: dev-server CSS vs bundled CSS.
- **FAIL-012** — the gate that runs against the build with no API, which is why it could not see this.
- `docs/design/ux-review-2026-08-21.md` — R-01, and the measurement harness that found it.
