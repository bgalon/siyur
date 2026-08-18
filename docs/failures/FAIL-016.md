# FAIL-016 — The isolation recipe the docs recommend silently broke the app it was isolating

- Date: 2026-08-18 · Severity: medium (no data lost, no bad merge; cost ~20 minutes and pointed
  the investigation at the wrong tier, on the workflow AGENTS.md tells every parallel session to use)
- Root-cause class: tooling/config (an override honoured by one tier and not propagated to the
  tier that consumes it)

## Symptom

Following AGENTS.md's isolation advice for working alongside another session — take your own
ports — the whole stack came up and reported healthy:

```
$ SIYUR_WEB_PORT=5174 SIYUR_API_PORT=8001 SIYUR_DB_PORT=5433 scripts/dev.sh start
4/5  API   http://localhost:8001 — /healthz ok after 3s
5/5  Web   http://localhost:5174 — proxying /areas /sites /me to the API
```

Both tiers up. `scripts/dev.sh status` agreed. `curl localhost:8001/areas` answered in 1.7 s.

**And every request the browser made returned `502`:**

```
RESP 502 GET  /sites?bbox=28.2216,36.439767,28.2294,36.447233
RESP 502 POST /areas
[siyur] AreaRequestError: POST /areas failed with status 502
```

The map was empty, the delimit button did nothing, and the coverage card never appeared — while
the API it could not reach was demonstrably answering the identical request from the shell.

## Root cause

`web/vite.config.ts` proxies `/areas`, `/sites`, `/auth`, `/me`, `/healthz` and `/plans` to

```ts
target: process.env.SIYUR_API_ORIGIN ?? 'http://127.0.0.1:8000'
```

`scripts/dev.sh` read `SIYUR_API_PORT` and started **the API** on 8001. It never set
`SIYUR_API_ORIGIN`. So the API moved and the proxy did not follow: the dev server dutifully
forwarded every call to `127.0.0.1:8000`, where nothing was listening.

The override was honoured by the tier that owns the port and ignored by the tier that consumes
it. `SIYUR_DB_PORT` did not have this bug — `SIYUR_DATABASE_URL` is derived from it three lines
above — which is what makes this an oversight rather than a design.

## Why nothing caught it

1. **Both tiers really were up**, so every health check was honest. `status` reports processes,
   and the defect was in what one process *points at*. There is no state in which the reporting
   is wrong; the thing worth reporting was simply not among the things reported.
2. **The default path is the tested path.** Nobody hits this on `dev.sh start` with no overrides,
   because 8000 is then correct by coincidence. The only way in is the multi-session recipe,
   which is exactly the situation where a second person is least able to tell whether the
   breakage is theirs.
3. **The symptom names the wrong tier.** A `502` on `POST /areas` reads as an API fault. The
   comment already in `vite.config.ts` predicted this precisely — an unproxied path "is exactly
   the symptom that reads as *the backend is broken* when it is not" — and it was written about
   a *missing path entry*, not a wrong target. The same sentence covered both and neither was
   checked, because the message on screen was about `/areas`, so `api/` is where anyone looks.

Same shape as **FAIL-009**: a status command that was truthful about the thing it measured and
silent about the thing that was wrong.

## A second instance, found in the same hour

With the ports fixed, the walkthrough reached the plan step and failed minutes in:

```
litellm.exceptions.AuthenticationError: Missing Anthropic API Key —
  A call is being made to anthropic but no key is set
```

AGENTS.md says, in the section telling agents how to handle credentials:

> **`scripts/dev.sh` already loads the model key from the Keychain**, so the normal answer is
> *run `scripts/dev.sh start`* and stop thinking about it.

`grep -ci anthropic scripts/dev.sh` → **0**. Nothing in the repo read the Keychain. The stored
item existed; nothing looked for it.

**Same root cause, one field over:** the script's environment setup was incomplete and the
documentation asserted otherwise. It is arguably the worse of the two, because that sentence
explicitly instructs the reader to *stop thinking about it* — so the one check that would have
caught it is the one the docs told you to skip. And the failure is maximally delayed: the stack
starts, `status` is green, delimiting works, the map fills with 748 cited places, and the gap
surfaces several minutes into the first plan, inside a third-party library's error.

Fixed alongside, and deliberately non-fatal: a machine without the stored item still gets a
useful stack, because everything except research and planning works without a model. Refusing to
start would trade a late clear failure for an early one that blocks more.

## Guardrail

`tests/test_dev_script_ports.py` — **executes** the script's configuration prologue under a
given environment and reads the exported variables back:

- `SIYUR_API_PORT=8001` ⇒ `SIYUR_API_ORIGIN == http://127.0.0.1:8001`
- no overrides ⇒ the default origin and the default API port still agree
- an explicit `SIYUR_API_ORIGIN` still wins, so pointing the web tier elsewhere keeps working
- `SIYUR_DB_PORT` ⇒ `SIYUR_DATABASE_URL`, the same class one tier over, asserted so it cannot
  appear there next

Run rather than grepped, deliberately: a grep passes on a line that is commented out, mistyped
or shadowed later, and this entry exists because a variable was not set. **Mutation-proved** —
deleting the export turns two of the five red.

For the credential half: a value already present in the environment must survive untouched (CI
and cloud secret injection both set it, and this script must not fight them), the Keychain
lookup must still be present, and **`dev.sh` must never print it** — the script already prints a
session cookie and a page of guidance, so that one is pinned rather than assumed.

## What it cost, stated plainly

About twenty minutes, and it was found only because the 390 px walkthrough needed a working
stack; a session that merely wanted to run tests would have hit it later and with less context.
The wider cost is the one worth recording: **the repo's own advice for not colliding with
another session was, until now, a recipe for a silently broken app.** FAIL-011 taught that a
worktree isolates files and not ports, databases or volumes. This is the next layer — taking
your own ports isolates you, and then breaks you, unless every tier hears about it.

## Related

- **FAIL-011** — a worktree isolates files only. This is the failure of the workaround that
  failure recommends.
- **FAIL-009** — a check that was truthful about what it measured and silent about what was wrong.
- `web/vite.config.ts` — the proxy block, whose own comment predicted the symptom.
