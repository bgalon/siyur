# Try it — what actually runs today

**Status as of 2026-08-01.** Every command here was run and its output verified. Where something *doesn't* work yet, that's stated plainly rather than omitted.

Full session context: [`docs/devlog/2026-08-01-spec001-parallel-build-and-three-ratifications.md`](devlog/2026-08-01-spec001-parallel-build-and-three-ratifications.md).

---

## 0. Setup (once)

```bash
uv sync
```

---

## 1. The whole test suite — 30 seconds, no dependencies

```bash
uv run pytest -q                 # 464 passed
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

This is the fastest confidence check. Tier-2 tests that need PostGIS **skip** cleanly when no database is reachable, so a bare `pytest` run is green without Docker.

---

## 2. See the data spine work end to end ⭐ start here

```bash
uv run python scripts/try_it.py
```

Drives the **real** Overture and OSM adapters, the per-field merge, and the Greek transliteration over the committed Rhodes fixtures. No API key, no database, no network. It prints:

- **Ingestion** — 200 Overture + 25 OSM records, every value stamped, with the per-record license breakdown (Overture mixes `CDLA-Permissive-2.0`, `Apache-2.0` and `CC0-1.0` *inside one theme*).
- **Merge** — the cross-source anchor: the same gate seen by both sources, 3.44 m apart, joined into one record with **both** source refs retained; and the proof that distance alone never merges.
- **Transliteration** — `Ρολόι → Roloi`, `Ευαγγελισμός → Evangelismos` vs `Ευτυχία → Eftychia`, plus the FAIL-001 guard rejecting Cyrillic in an `el` field.

This is the closest thing to "watch the product work" until the API lands.

---

## 3. Real PostGIS — migration + Tier-2 tests

Needs Docker running.

```bash
docker compose up -d                      # postgis/postgis:16-3.4, healthy in ~6s
export SIYUR_DATABASE_URL="postgresql+psycopg://siyur:siyur@localhost:5432/siyur"

uv run alembic upgrade head               # creates the commons spine
uv run pytest -q -m integration           # 3 passed  (461 deselected)
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

Expect `site`, `site_source`, `site_conflict`, `user_note` plus `ix_site_geom` (GiST). `uv run alembic downgrade base` also works.

Tear down with `docker compose down -v`.

---

## 4. The API — ops endpoints and SSO

```bash
SIYUR_SESSION_SECRET=devsecret uv run uvicorn api.app:app --port 8000
```

| Endpoint | Expect |
|---|---|
| `GET /healthz` | `200 {"status":"ok"}` |
| `GET /me` | `401` (unauthenticated) |
| `GET /auth/login` | `503` until Google OAuth credentials are set |

For real Google sign-in, set `SIYUR_GOOGLE_CLIENT_ID` / `SIYUR_GOOGLE_CLIENT_SECRET` in the environment (never in a file — Constitution Article V) and register `http://localhost:8000/auth/callback` as an authorised redirect URI. See `api/README.md`.

---

## 5. The web app

```bash
pnpm -C web install --frozen-lockfile
pnpm -C web dev            # http://localhost:5173
pnpm -C web test           # vitest
pnpm -C web typecheck
pnpm -C web build
```

**What you'll see: an empty dark map with the ODbL attribution control.** That is the DU-00 walking skeleton and is correct — see the honest limitation below.

---

## What does NOT work yet

The map renders **no places**, and that is expected rather than a bug.

The web layer (`web/src/map/sites.ts`, `attribution-chip.ts`) *is* built and tested — it fetches `GET /sites?bbox=…`, renders a marker per site, resolves display names `en → <lang>-Latn → source-script`, and refuses to render any value lacking a source stamp. It was built against `specs/001-research-cited-sites/contracts/sites.md` with `fetch` mocked, because **the contract is the interface**.

But the backend it calls doesn't exist yet:

| Missing | Task |
|---|---|
| `commons/repository.py` — commons upsert | T030 |
| `planner/` pipeline: `resolve_area → research → curate` | T032–T036 |
| `POST /areas`, research SSE, `GET /sites` | T037–T041 |

Until those land there is no path from "delimit an area" to "markers on the map". Everything *behind* that path — ingestion, provenance stamping, merge, transliteration, persistence — works today and is what `scripts/try_it.py` demonstrates.

Also outstanding: **T059**, wiring transliteration into `planner/nodes/curate.py`. `commons/translit.py` works standalone but nothing calls it in a pipeline yet.

---

## Handy checks

```bash
gh pr list --state open                   # should be empty
git worktree list                         # should be just the repo root
uv run pytest -q -k merge                 # 56 merge tests
uv run pytest -q -k translit              # transliteration + FAIL-001 guard
uv run pytest tests/test_llm_seam.py -q   # seam-purity AST tripwire
```

To see the seam tripwire actually bite:

```bash
printf 'import anthropic\n' > planner/_probe.py
uv run pytest tests/test_llm_seam.py -q   # fails, naming planner/_probe.py:1
rm planner/_probe.py
```
