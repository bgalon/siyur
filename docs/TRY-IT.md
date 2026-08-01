# Try it — what actually runs today

**Status as of 2026-08-01.** Every command here was run and its output verified. Where something *doesn't* work yet, that's stated plainly rather than omitted.

Full session context: [`docs/devlog/2026-08-01-spec001-parallel-build-and-three-ratifications.md`](devlog/2026-08-01-spec001-parallel-build-and-three-ratifications.md).

---

## 0. Setup (once)

```bash
uv sync
```

---

## 1. The whole test suite — about a minute, no setup

```bash
uv run pytest -q                 # 586 passed with a database; 549 passed + 37 skipped without one
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

This is the fastest confidence check. Tier-2 tests that need PostGIS **skip** cleanly when no database is reachable — and start one themselves via testcontainers when Docker *is* running — so a bare `pytest` run is green either way.

---

## 2. See the data spine work end to end ⭐ start here

```bash
uv run python scripts/try_it.py
```

Drives the **real** Overture and OSM adapters, the per-field merge, and the Greek transliteration over the committed Rhodes fixtures. No API key, no database, no network. It prints:

- **Ingestion** — 200 Overture + 25 OSM records, every value stamped, with the per-record license breakdown (Overture mixes `CDLA-Permissive-2.0`, `Apache-2.0` and `CC0-1.0` *inside one theme*).
- **Merge** — the cross-source anchor: the same gate seen by both sources, 3.44 m apart, joined into one record with **both** source refs retained; and the proof that distance alone never merges.
- **Transliteration** — `Ρολόι → Roloi`, `Ευαγγελισμός → Evangelismos` vs `Ευτυχία → Eftychia`, plus the FAIL-001 guard rejecting Cyrillic in an `el` field.

Fast, offline and deterministic — the fixtures, not the live sources. For the live path, see §4.

---

## 3. Real PostGIS — migration + Tier-2 tests

Needs Docker running.

```bash
docker compose up -d                      # postgis/postgis:16-3.4, healthy in ~6s
export SIYUR_DATABASE_URL="postgresql+psycopg://siyur:siyur@localhost:5432/siyur"

uv run alembic upgrade head               # creates the commons spine + `area`
uv run pytest -q -m integration           # 38 passed  (548 deselected)
```

Verify the schema landed:

```bash
uv run python -c "
import os, psycopg
u = os.environ['SIYUR_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')
with psycopg.connect(u) as c, c.cursor() as cur:
    cur.execute(\"select tablename from pg_tables where schemaname='public' order by 1\")
    print([r[0] for r in cur.fetchall()])
    cur.execute(\"select indexname from pg_indexes where tablename='site'\")
    print('site indexes:', [r[0] for r in cur.fetchall()])
"
```

Expect `area`, `site`, `site_source`, `site_conflict`, `user_note` plus `ix_site_geom` (GiST). `uv run alembic downgrade base` also works, and so does stepping one revision at a time (`alembic downgrade -1` → `upgrade head`).

Tear down with `docker compose down -v`.

---

## 4. The API — the whole research → read path over real HTTP

```bash
docker compose up -d
export SIYUR_DATABASE_URL="postgresql+psycopg://siyur:siyur@localhost:5432/siyur"
uv run alembic upgrade head
SIYUR_SESSION_SECRET=devsecret uv run uvicorn api.app:app --port 8000
```

| Endpoint | Expect |
|---|---|
| `GET /healthz` | `200 {"status":"ok"}` — works with no database and no credentials |
| `GET /me` | `401` unauthenticated |
| `GET /auth/login` | `503` until Google OAuth credentials are set |
| `POST /areas` | resolve a delimitation + report commons coverage |
| `POST /areas/{area_id}/research` | `text/event-stream` — the research pass, streamed |
| `GET /sites?bbox=…` | the cited commons for a viewport |
| all three of the above | `401` unauthenticated · `422` on a bad bbox/geometry |

### Signing in without Google

Every data endpoint needs a session. In production that comes from Google SSO — set `SIYUR_GOOGLE_CLIENT_ID` / `SIYUR_GOOGLE_CLIENT_SECRET` in the environment (never in a file — Constitution Article V) and register `http://localhost:8000/auth/callback` as an authorised redirect URI (`api/README.md`).

For a **local** poke-around with no Google project, mint a session cookie with the dev secret you just chose. This signs a cookie exactly the way `/auth/callback` does; it uses no real credential and works only against your own `SIYUR_SESSION_SECRET`:

```bash
COOKIE=$(uv run python -c "
import base64, json, itsdangerous
data = base64.b64encode(json.dumps({'user': {'sub': 'dev-local-user'}}).encode())
print(itsdangerous.TimestampSigner('devsecret').sign(data).decode())
")
curl -s -H "Cookie: session=$COOKIE" localhost:8000/me
```

### Delimit → research → read

```bash
# 1. Delimit an area (any area — nothing is hardcoded per place).
curl -s -H "Cookie: session=$COOKIE" -H 'Content-Type: application/json' \
  -d '{"bbox":[28.216,36.440,28.232,36.451]}' localhost:8000/areas
# → {"area_id":"…","polygon":{…},"coverage":{"known_site_count":0,"covered":false,…}}

# 2. Research it. This one really does hit Overture's cloud release and Overpass.
curl -sN -H "Cookie: session=$COOKIE" -H 'Content-Type: application/json' \
  -d '{"force_refresh":false}' localhost:8000/areas/<area_id>/research

# 3. Read the commons back.
curl -s -H "Cookie: session=$COOKIE" \
  'localhost:8000/sites?bbox=28.216,36.440,28.232,36.451'
```

**Verified on 2026-08-01 against the live sources** (counts move with the upstream release, so expect *different* numbers, not these):

- the stream emitted `resolve_area → research(overture, found 1704) → research(osm, found 523) → curate(merged 2012, conflicts 371, derived_names 37)`, then 2012 `site` frames, `summary`, `done`;
- Overpass returned `504` for the `way` and `relation` sub-queries, and the stream said so — `"degraded":true`, `"reason":"Overpass unavailable (way: HTTP 504; relation: HTTP 504)"`, `"degraded_sources":["osm"]` — instead of hanging or quietly under-reporting (FR-012);
- the pass committed 2011 `site` rows, and `GET /sites` returned all of them: **11 982 sourced values, 0 without a `source`**, `attribution: ["© OpenStreetMap contributors"]`, and 37 records carrying both `el` and `el-Latn` (`Γρηγόρης → Grigoris`) with the original script intact;
- re-`POST`ing the same bbox then reported `covered: true, refresh_available: true`, and a second `research` with `force_refresh:false` returned a **reuse hint** (`phase:"reuse"`, `summary.reused:2011`, zero `site` frames) rather than researching again. `force_refresh:true` re-runs and merges onto the existing rows.

A pass takes a couple of minutes, almost all of it Overture's cloud parquet scan.

---

## 5. The web app — cited markers on the map

```bash
pnpm -C web install --frozen-lockfile
pnpm -C web dev            # http://localhost:5173
pnpm -C web test           # vitest
pnpm -C web typecheck
pnpm -C web build
```

`web/src/map/sites.ts` fetches the *same-origin* path `/sites`, so the dev server proxies `/areas`, `/sites`, `/auth`, `/me` and `/healthz` to the API (`web/vite.config.ts`). Start the API from §4 first, then `pnpm -C web dev`; point it elsewhere with `SIYUR_API_ORIGIN`. Same-origin rather than CORS because the session cookie is `same_site=lax` and a cross-origin XHR would not send it.

> If port 5173 is taken, Vite silently moves to 5174 — and the *other* server on 5173 will answer your requests with its SPA fallback (HTML, status `200`). Read the line Vite prints; a `200` from `/me` is not proof you reached the API.

Paste the §4 `$COOKIE` into the browser once (`document.cookie = "session=…; path=/"`), then reload.

**What was verified (2026-08-01), twice, on separate runs:**

- 782 sites over the Rhodes old-town bbox rendered as **782 markers**, each with its display name and a per-value attribution chip (`OVERTURE · CDLA-PERMISSIVE-2.0`, `OSM · ODBL-1.0 · © OPENSTREETMAP CONTRIBUTORS`), Greek names showing their transliterated form, and `© OpenStreetMap contributors, ODbL` in the attribution control.
- Framed on the researched area at z16, the markers spread across 691 × 731 px of the viewport — real geographic positions, not a cluster.
- Panning re-queried `/sites` with the new viewport bbox; panning off the area dropped to zero markers.
- The real API response passes the client's own provenance gate (`sanitiseSitesResponse`) with **zero records dropped**.

**What was *not* verified, and why:** browser sign-in was the dev cookie above, **not** a real Google SSO round-trip — that path is still only exercised by mocked tests. `POST /areas` **by name** was not verified in the browser: it hangs for minutes (see below), so every browser run used the `bbox` path.

---

## What does NOT work yet

- **Marker labels are always on**, so at a few hundred markers they overlap into unreadable noise (see the §5 screenshot behaviour). Every value carries its stamp, which is the constitutional requirement and is correct — it is the *presentation* that needs hover/click or clustering. This is the most visible thing standing between the current state and something demoable to a stranger.
- **`POST /areas` by name is very slow.** The Overture divisions lookup scans the hosted theme with no bbox pushdown, so a name resolve can hang for minutes; a request from the browser froze the tab. The `bbox` path returns immediately. Until that is fixed, treat the search pill as unfinished.
- **Prompt caching is off in practice.** `curate` requests it correctly, but its cached prefix (~133 tokens) is under Sonnet 5's 1,024-token minimum, and Anthropic caches nothing below the minimum without erroring. Pinned by `evals/test_caching.py`.
- **Google SSO is untested end to end.** The code path exists and is unit-tested with a mocked token exchange; nobody has yet logged in with a real Google project.
- **The `409` "research already running" guard is process-local** (a module-level set), so a second process would not see the claim.
- **Genericity is evidenced, not proven to the constitution's bar.** Rhodes + Takayama pass with no place-specific code (`evals/test_genericity.py`), but both fixtures are committed and therefore rehearsed; the ≥3-areas-including-an-unrehearsed-one milestone gate is not met.
- **Bundles, offline and the planner proper** (M2+) — not started.

---

## Handy checks

```bash
uv run pytest -q -k merge                 # merge tests
uv run pytest -q -k translit              # transliteration + FAIL-001 guard
uv run pytest -q -k api                   # the three API contract suites
uv run pytest tests/test_llm_seam.py -q   # seam-purity AST tripwire
```

To see the seam tripwire actually bite:

```bash
printf 'import anthropic\n' > planner/_probe.py
uv run pytest tests/test_llm_seam.py -q   # fails, naming planner/_probe.py:1
rm planner/_probe.py
```
