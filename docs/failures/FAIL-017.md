# FAIL-017 — The fix for FAIL-013 shipped broken, because the test that proved it invented the wire shape

- Date: 2026-08-21 · Severity: high (the product's front door failed silently for five days:
  a name search took 65 s and then said nothing at all, under a 265-line test suite that was
  green the whole time)
- Root-cause class: test double diverged from the artifact it doubles — FAIL-013 recurring
  *inside its own fix*, and FAIL-015's lesson with a fixture standing in for a bundle

## Symptom

Typing a name into the delimit control and pressing **Find** ran for ~65 s and then returned the
screen to its initial state. No chooser, no error, no message. Measured in the page:
`hostChildren: 0`, hint empty, no error text anywhere in the DOM.

The server had answered correctly the whole time. Against the running API, `POST /areas
{"name": "Old Town"}` returns `404` carrying **eight** candidates with their names, bboxes and
source stamps. Every one of them was thrown away by the client.

## Root cause

`api/areas.py` raises `HTTPException(status_code=404, detail=_unresolved_detail(error))`, and
**FastAPI nests an `HTTPException`'s payload under `detail`**. So the body on the wire is:

```json
{"detail": {"message": "8 plausible areas match 'Old Town'; ask the user which one",
            "candidates": [ … ]}}
```

`web/src/map/areas.ts` read `record.candidates` from the **root** of the body, found nothing, and
fell through to `throw new AreaRequestError(404)` — the status-only error that FAIL-013 was filed
about and that this code path was written to eliminate.

The repo already knew this. `api/plans.py` builds its `409`s as a bare `JSONResponse` **precisely
to avoid the nesting**, and says so in a comment naming the client that reads them:

> the `409` bodies are built as a `JSONResponse` and **not** as an `HTTPException`, because
> FastAPI nests the latter under `detail` and the client reads `error` / `violations` /
> `superseded_by` at the top level

The knowledge existed, in prose, one file away. Nothing enforced it.

## Why nothing caught it

**`web/test/area-disambiguation.test.ts` is 265 lines long, has eleven assertions, and every one
of them passed against a client that had never once worked.** Its fixture built the body as:

```ts
new Response(JSON.stringify({ message: 'several places match that name', candidates }), …)
```

— un-nested. The double and the client agreed with each other about a shape the server does not
send, so the suite was internally consistent and externally wrong.

Both sides were tested. **Neither side was ever compared.** `tests/test_api_areas.py` asserted
`response.json()["detail"]["candidates"]` — the *correct* shape — in a test that had passed since
the endpoint was written. Two suites, two shapes, one of them fictional, and no assertion
anywhere that they were the same shape.

The general form, which is why this entry earns its length:

> **A test double is evidence about the double until something proves it matches the original.**
> Coverage of the consumer and coverage of the producer do not add up to coverage of the
> contract between them. The gap is invisible from inside either suite, and it widens silently:
> nothing fails when the producer changes, because the consumer's tests never see the producer.

This is the same shape as FAIL-015 (*the artifact under test was not the artifact that ships*),
with a fixture in the role the dev server played there.

## Guardrail

**The double is now generated from the original, not written by hand.**

1. `tests/test_api_areas.py::test_the_wire_capture_the_web_suite_doubles_is_the_body_this_endpoint_sends`
   drives the real app through `TestClient`, and asserts the live `404` body equals the committed
   `web/test/fixtures/area-404-wire.json` byte for byte. It also asserts the nesting by name —
   `body["detail"]["candidates"]` is non-empty and `"candidates" not in body`. Regenerate with
   `SIYUR_UPDATE_WIRE_CAPTURES=1`; the file is generated and never hand-edited.
2. `web/test/area-disambiguation.test.ts` **replays that capture verbatim** (`capturedNotResolved`)
   rather than composing a body, and its remaining hand-built doubles are shaped from the same
   envelope.
3. A test named *"does not honour candidates at the root of the body"* pins the client to reading
   `detail` **only**. Tolerating both shapes would have let the original fixture keep passing,
   which is the trap rather than the fix.

Mutation-proved in both directions before landing: renaming `candidates` → `options` in
`_unresolved_detail` fails Tier 1 with a message telling the operator to regenerate; reverting
`unresolvedDetail` to the root read fails three web tests, including the pre-existing
twenty-candidate test that had been green throughout the defect.

Verified against the running API, not only the suite: eight candidates rendered in the chooser at
`hostChildren: 1` after a real search.

## What it cost, stated plainly

Five days during which the primary way into the product — naming the place you want to research —
did nothing, while CI was green and a fix for exactly this defect was considered shipped. It was
found by driving the app, not by running the tests, and the tests are the thing that said it was
fine.

## Related

- **FAIL-013** — the original: the server answered carefully and the client kept only the status.
  This is that failure recurring inside its own remedy.
- **FAIL-015** — same class one layer down: the artifact under test was not the artifact that
  ships.
- **FAIL-012** — the DU-06a gate exists because layout claims needed a rendered viewport rather
  than a stylesheet grep. The same argument, for the wire instead of the screen.
