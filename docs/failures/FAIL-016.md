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
