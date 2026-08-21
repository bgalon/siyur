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

Both land with **Wave A / task A-1** (`docs/design/ux-review-2026-08-21.md`). **This entry does not
close until (2) is in CI** — (1) alone re-creates the failure with an extra step.

## What it cost, stated plainly

Six days of believing a shipped feature worked. It was merged on 2026-08-15 in #128, described in
that PR as *"the server returned twenty because it could not decide, and choosing on its behalf is
how someone ends up in the wrong city"* — a correct and careful argument about behaviour that has
never run. FAIL-013 was written the same week and its guardrail (1) is the very test that is wrong.

The cheaper lesson is the one FAIL-015 already paid for and this repeats in a new place: **the
artifact under test was not the artifact that ships.** There it was a stylesheet in `dist/`; here
it is a JSON envelope. In both cases every source-level check was green and one minute of driving
the real thing was decisive.

## Related

- **FAIL-013** — the original discarded `404` body. This is that failure recurring inside its own fix.
- **FAIL-015** — the same class one layer over: dev-server CSS vs bundled CSS.
- **FAIL-012** — the gate that runs against the build with no API, which is why it could not see this.
- `docs/design/ux-review-2026-08-21.md` — R-01, and the measurement harness that found it.
