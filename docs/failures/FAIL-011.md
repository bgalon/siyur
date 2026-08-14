# FAIL-011 — A test fixture deleted every row of a live dev database, from another session

- Date: 2026-08-14 · Severity: high (no production data; a live demo destroyed mid-run, ~30
  minutes lost to diagnosing a defect that did not exist, and a wrong bug report to the
  operator that was moments from becoming a wrong fix)
- Root-cause class: environment isolation (a rule that isolates the *right* thing at the
  *wrong* layer)

## Symptom

A first genuine end-to-end run — real commons, real model, real routing graph — behaved
impossibly:

```
POST /areas                       -> 200  {"area_id":"6732186e-…"}
POST /areas/6732186e-…/research   -> 200  508 sites written
POST /plans        (same area)    -> 404  "no area 6732186e-… for this user"
POST /areas/6732186e-…/research   -> 404  ← the request that worked a minute ago
```

`SELECT count(*) FROM area` returned **0**, immediately after an endpoint returned `200`
with an area id and a second endpoint successfully acted on it.

## The wrong conclusion, and how close it came to shipping

The obvious reading is that `POST /areas` returns an id without persisting the row. That is
a serious defect in a file two sessions had touched that day, and I was about to report it as
one.

Three observations killed it:

1. **`site` count went 509 → 459.** Nothing in the flow deletes sites. A persistence bug
   cannot *remove* rows that were already committed.
2. **A row inserted directly through the ORM survived**, and the API then found it —
   `research 200`, `plan 200`. So the endpoint's scoping, the models, and the connection
   were all correct.
3. `create_area` reads `add` → `flush` → `commit`, with no branch that skips the commit.

So rows were being committed and then **deleted by something else**.

## Root cause

`tests/conftest.py`'s `db_session` fixture clears the database before **every** integration
test:

```python
with db_engine.begin() as connection:
    for table in reversed(Base.metadata.sorted_tables):
        connection.execute(table.delete())
```

A second session was running Tier 2 against the same PostGIS — five or six full runs that
afternoon, 127 tests each. `docker-compose.yml` binds a fixed `${SIYUR_DB_PORT:-5432}`, so
both checkouts reach the same container by default. Each test truncated `area`, `user_plan`
and `site` out from under a running API server.

The fixture is correct in isolation. It is destructive in company, and **neither session could
see the other**: nothing in `docker ps`, the API log, or the test output mentions the other
party. The only visible evidence was a row count moving in a direction nothing in either
session's code would move it.

## Why the isolation rule did not prevent it

ADR-0005 requires a separate checkout per concurrent session, and both sessions complied —
this one in a git worktree, correctly. FAIL-008 already recorded that worktrees prevent *file*
races but not *task* duplication.

This is the same lesson one layer further down:

> **A worktree isolates files. It does not isolate a database, a port, a Docker volume, or a
> routing graph.**

Every shared-state failure this project has hit is on that list. FAIL-010 was a Docker volume
holding the wrong graph for six days. This is a database. The pattern is that `git worktree`
looks like an isolation primitive and is only a *filesystem* one, so the surface everyone
reasons about is the one already safe.

## Guardrail

Owned by the DU-05 session, landing with its next PR, and **this entry does not close until it
is in** (Article IV).

**The fixture derives and creates its own database** rather than trusting the one it is handed:
take whatever `SIYUR_DATABASE_URL` points at, append `_test`, `CREATE DATABASE` if absent, and
run every destructive fixture against that. "Delete every row" then becomes *structurally*
incapable of reaching a database anyone works in.

Two designs were considered and this is the better one:

- **Rejected — require an explicit `SIYUR_TEST_DATABASE_URL`, or refuse a database not named
  `*_test`.** CI's database is also named `siyur` (`ci.yml` job 3, `POSTGRES_DB: siyur`), so
  either form breaks CI and needs an ask-gated `.github/workflows/**` edit. Worse, an opt-in
  variable protects only until somebody forgets it once — and the failure mode of forgetting
  is silent data loss.
- **Chosen — derive it.** No configuration anywhere: CI, both sessions and any future clone
  keep their existing URLs and gain the guarantee. Verified before proposing that the local
  role and CI's `postgres` role both hold `createdb`.

**Interim, and useful regardless:** a second database on another port costs two lines and
isolates completely —
`SIYUR_DB_PORT=5433 docker compose -p siyur-test up -d postgis`, then point
`SIYUR_DATABASE_URL` at `:5433`. That is what the B1 work ran against while this was open.

## What it cost, stated plainly

About thirty minutes of debugging, a destroyed demo mid-run, and a **wrong bug report to the
operator** — I told him `POST /areas` did not persist, which was false, and the next step
would have been a fix to code that was already correct. The diagnostic that saved it was
cheap and generic: *when a row disappears, check whether counts are moving in a direction your
own code cannot move them.*

## Related

- **FAIL-008** — worktrees prevent file races, not task races. Same rule, one layer up.
- **FAIL-010** — a Docker volume holding the wrong data for six days, also shared, also
  invisible to every health signal.
- **ADR-0005** — the isolation rule itself. Its scope is filesystem-level and it does not
  claim otherwise; what was missing is that nobody had written down what it therefore does
  *not* cover.
