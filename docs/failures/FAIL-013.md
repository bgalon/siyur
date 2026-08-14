# FAIL-013 — The server answered carefully; the client kept the status code and threw the answer away

- Date: 2026-08-15 · Severity: high (the only delimit path reachable on a phone appears, to the
  user, to do nothing — after 61 seconds)
- Root-cause class: **an error boundary that narrows a rich response to a number**

## Symptom

Typing a place name and pressing `Find` — the only delimit control reachable at 375/390 px
(FAIL-012, UX-01) — produces, from the user's side, **nothing**. No spinner, no message, no
change of any kind, indefinitely.

Measured against the API directly, the server is behaving well:

```
POST /areas {"name":"Rhodes Old Town"}   ->  404 in 61.6 s
{"detail":{"message":"20 plausible areas match 'Rhodes Old Town'; ask the user which one",
           "candidates":[{"name":"Old Town","confidence":0.45,
                          "bbox":[-76.6074482,39.2946995,-76.5991322,39.3016717],
                          "source":{"kind":"overture","license":"ODbL-1.0",...}}, ...]}}
```

That is not a failure. It is `resolve_area` refusing to guess between 20 real candidates and
handing the client everything needed to ask — names, confidences, bboxes, licence-stamped
sources. The contract is deliberate and documented (`resolve_area.py`: "a name is the only input
that needs guessing").

## Root cause

One line, at the client's error boundary:

```ts
// web/src/map/areas.ts:85
if (!response.ok) throw new AreaRequestError(response.status)
```

The body is never read. `AreaRequestError` carries a `number`. `main.ts:44` then routes every
such throw to:

```ts
const onError = (error: unknown): void => { console.warn('[siyur]', error) }
```

`grep -rn "candidates" web/src/` returns no match anywhere in the area path — the only hits are
the *plan* pipeline's unrelated site-candidate count. **There is no disambiguation UI in the
application at all**, and no code path that could have consumed the field.

So the pipeline is: a thoughtful 404 → narrowed to `404` → routed to a console the user cannot
see → nothing rendered. Three separate lossy steps, each individually defensible.

## Why nothing caught it

- `web/test/areas.test.ts` asserts that a non-2xx **throws** — which it does. The test encodes
  the narrowing as the intended behaviour, so it will keep passing forever.
- The API side has its own tests, and they pass: the 404 and its `candidates` payload are
  correct and covered. **Both halves are green and the seam between them drops the payload.**
- Nobody ran the name path end to end in a browser. It costs 61.6 s per attempt, which is its own
  quiet disincentive — and the bbox path used in every demo returns `200` in **0.18 s**, so the
  fast, working path is the one that got exercised and the slow, broken one is the one shipped to
  thumbs.

The compounding detail: `resolve_area.py:329-330` records that an unwindowed name search takes
**73 s** against **18 s with a caller-supplied window**. The web client holds the map viewport
and sends no window. The slowest possible variant of the only reachable control.

## Guardrail

**Two assertions, because there are two distinct defects here — the lost payload and the
invisible failure.**

1. **Error bodies survive the boundary.** A contract test in `web/test/areas.test.ts`: given a
   `404` whose body carries `detail.candidates`, `resolveArea` must reject with an error that
   **still carries the parsed detail**. Assert on the candidate array, not on the status. This
   generalises — write it so any structured `detail` survives, not just this shape.

2. **Every request-triggering control has a terminal visible state.** A Playwright assertion, on
   the same viewport harness FAIL-012 introduces: after activating a control that issues a
   request, the DOM must reach a state that is *not* the pre-request state — a result, a rendered
   error, or an explicit pending indicator — within a bounded time. "Unchanged" is a test
   failure. This is the assertion that catches the whole family (UX-02, UX-06, UX-07, UX-13), all
   of which are the same defect wearing different clothes: **the app knew something and did not
   say it.**

**Additionally, and cheap:** send the map viewport as `window` on the name path. It is not a
correctness fix, but a 61.6 s control that a user must not abandon is a usability defect in its
own right, and the server already supports the parameter.

**This entry does not close until (1) and (2) are in CI.**

## What it cost, stated plainly

No data loss and no wrong code — the server was right the whole time. What it cost is the
audit's headline: the product's first step is unusable on its primary form factor, and the reason
is not a missing feature but **a correct answer that was thrown away three times on its way to
the screen.** The generalisable lesson is that `if (!response.ok) throw new Error(status)` is not
a neutral default — for any API that returns structured problem details, it is a deliberate
decision to discard them, and it should be written as though it were one.

## Related

- **FAIL-012** — the viewport class this defect hides inside; `Use this view` being occluded is
  what makes this the *only* reachable path.
- **FAIL-007** — an absolute-vs-relative URL contract mismatch across the same client/server seam.
- `docs/design/ux-audit-2026-08-15.md` — findings UX-02, UX-03, UX-07, UX-13.
</content>
