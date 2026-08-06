# Quickstart — the Spec 001 acceptance walkthrough

**Feature**: `specs/001-research-cited-sites` · **Written**: 2026-07-31 · **Re-verified against the shipped code**: 2026-08-01 (T068)

This is the **acceptance** walkthrough for Spec 001: every US scenario and every success criterion, in order, each with the command that proves it and the result that run actually produced. It is deliberately *not* a second runbook.

> **Setup lives in one place: [`docs/TRY-IT.md`](../../docs/TRY-IT.md).** How to install, start PostGIS, migrate, run the API, mint a local session cookie and start the web app — all of it is there, written against a real run, and it is the only file that owns those instructions. Two runbooks drift, and the one that drifts is the one nobody runs. Everything below assumes you have followed TRY-IT §0–§5 and have `$COOKIE` and an API on `localhost:8000`.

Each criterion below is marked with how it was established:

- **[eval]** — a committed automated check. Reproduce it with the command shown; it needs no database, no key and no network.
- **[live]** — observed on 2026-08-01 against the **real** Overture cloud release and Overpass, over the Rhodes old-town bbox `28.216,36.440,28.232,36.451`. Numbers below are from *this* verification pass unless another document is cited, in which case they were observed there and are reproduced, not re-run. Counts move with the upstream release, so expect *different* numbers either way.
- **[unverified]** — stated honestly as not established by this pass, with the reason.

---

## Prerequisites, in one line each

| | |
|---|---|
| Toolchain | `uv sync` (Python 3.12) — everything marked **[eval]** runs after just this |
| Database | PostGIS via `docker compose up -d`, then `uv run alembic upgrade head` → `area`, `site`, `site_source`, `site_conflict`, `user_note` |
| API | `SIYUR_SESSION_SECRET=… uv run uvicorn api.app:app --port 8000` |
| Session | a signed session cookie — **Google SSO** in production, the dev-secret cookie of TRY-IT §4 locally. There is no Firebase/auth emulator in this stack, and no bearer token: auth is a `same_site=lax` session cookie |
| Web | `pnpm -C web dev` → `:5173`, which proxies `/areas`, `/sites`, `/auth`, `/me`, `/healthz` to the API |
| Fixtures | committed Overture parquet + Overpass JSON for **Rhodes** *and* **Takayama** (`tests/fixtures/`) — the genericity pair. CI never touches live Overture/OSM/Anthropic |

---

## US1 — Research a delimited area, see cited sites on the map

```bash
# 1 · delimit (the bbox path; see "Known gaps" for the name path)
curl -s -H "Cookie: session=$COOKIE" -H 'Content-Type: application/json' \
  -d '{"bbox":[28.216,36.440,28.232,36.451]}' localhost:8000/areas
# → {"area_id":"…","polygon":{…},"coverage":{"known_site_count":0,"covered":false,…}}

# 2 · research it (SSE: status per phase, one `site` frame per record, summary, done)
curl -sN -H "Cookie: session=$COOKIE" -H 'Content-Type: application/json' \
  -d '{"force_refresh":false}' localhost:8000/areas/<area_id>/research

# 3 · read the commons back
curl -s -H "Cookie: session=$COOKIE" \
  'localhost:8000/sites?bbox=28.216,36.440,28.232,36.451'
```

| Acceptance criterion | How it was established |
|---|---|
| **Scenario 1** — the area's places render as markers, each with a source + license chip | **[live]** the pass emitted `resolve_area → research(overture) → research(osm) → curate`, then a `site` frame per record; `GET /sites` returned **2 367 sites**. The browser half — **782 markers** over a narrower viewport, each with its per-value chip (`OVERTURE · CDLA-PERMISSIVE-2.0`, `OSM · ODBL-1.0`) — was verified in TRY-IT §5 and is **not re-run here**; this pass verified the API and the data, not the render |
| **Scenario 2 / SC-002** — 100% of displayed values stamped, zero unstamped ever shown | **[live]** **13 309 sourced values, 0 without a source, 0 without a license.** Licenses actually present: CDLA-Permissive-2.0 9 853 · ODbL-1.0 3 101 · Apache-2.0 324 · CC0-1.0 31 — *four* licenses inside one area. **[eval]** `uv run pytest evals/test_structural.py -q` → **9 passed**; the provenance eval measures a *rate* over the whole pass and demands 1.0. An unstamped value cannot be constructed (`commons/models.py`), so it is refused before display, not filtered at it |
| **Scenario 3** — ODbL attribution visible on the map | **[live]** `GET /sites` returns `attribution: ["© OpenStreetMap contributors"]`, computed from the stamps rather than the client; the map's attribution control shows `© OpenStreetMap contributors, ODbL` (TRY-IT §5) |
| **Scenario 4 / FR-009** — disagreeing sources become a recorded conflict, no source discarded | **[live]** **485 conflicts across 275 records**, every one carrying ≥2 candidates with both sources intact plus a `resolution` marking the winner. **[eval]** `uv run pytest tests/test_merge.py -q` → **63 passed** |
| **SC-001** — ≥20 cited places for the demo area | **[live]** 2 367 ≫ 20 |
| **FR-005** — every `location` traces to authoritative geodata; a model-asserted coordinate is rejected | **[live]** every `location` stamp is `overture` or `osm`; no other source kind appears on a location. **[eval]** covered by `evals/test_structural.py` (`test_every_location_traces_to_authoritative_geodata`, `test_a_model_that_tries_to_emit_a_coordinate_moves_nothing`). Structurally, the `research` node makes **no model call at all** (ADR-0014) |
| **SC-006** — an area with no source data says "nothing found", zero fabricated places | **[live]** researched an empty ocean bbox `-30.001,-40.001,-30.0,-40.0`: `research(overture, found 0)`, `research(osm, found 0)`, `curate(merged 0)`, `summary {"sites":0,"new":0,…}`, **zero `site` frames**. **[eval]** `tests/test_api_research.py`, `tests/test_planner_research.py` |

---

## US2 — Reuse an already-researched area, and refresh

```bash
# same bbox again
curl -s -H "Cookie: session=$COOKIE" -H 'Content-Type: application/json' \
  -d '{"bbox":[28.216,36.440,28.232,36.451]}' localhost:8000/areas
# then, without force_refresh, research it again
curl -sN -H "Cookie: session=$COOKIE" -H 'Content-Type: application/json' \
  -d '{"force_refresh":false}' localhost:8000/areas/<new_area_id>/research
```

| Acceptance criterion | How it was established |
|---|---|
| **Scenario 1 / SC-003 / FR-006** — re-delimiting shows existing data with no fresh research pass, refresh always offered | **[live]** the re-`POST` returned `covered: true, known_site_count: 2367, refresh_available: true`, and the second research emitted a single `status {"phase":"reuse"}` frame with `summary {"sites":0,"new":0,"reused":2367,"reuse":true}` — **zero `site` frames, no source call** |
| **Scenario 2 / FR-009** — refresh updates observation dates, records newly-disagreeing values, loses no source | **[live]** `force_refresh:true` re-ran the full pass (`overture found 1704`, `osm found 955`) and merged onto the existing rows: the `site` table held **2 367 rows before the refresh and 2 367 after** — dedupe-on-write, not append — with `updated_at` advanced and `site_conflict` growing 485 → **487** as two newly-disagreeing values were recorded rather than discarded. **[eval]** the dedupe rule (ε=25 m / τ=0.6, ADR-0008) is pinned by `tests/test_merge.py` and `tests/test_repository.py` |
| Degradation is reported, never hidden | **[live]** on that same refresh Overpass returned `HTTP 429` for the `relation` sub-query and the stream said so — `"degraded":true, "reason":"Overpass unavailable (relation: HTTP 429)"` — plus its own drop accounting (`unusable_language_tag: 12`, `outside_polygon: 34`) instead of quietly under-reporting (FR-012) |
| Commons is **shared**, not per-session | **[eval]** `tests/test_repository.py`. Note the counterpart: `area` rows are **private, row-scoped to `created_by`** — flagged as an open ADR candidate, not decided (PRD §13 #4) |

---

## US3 — Greek → Latin display names

```bash
uv run pytest tests/test_translit.py -q          # 207 passed
uv run python scripts/try_it.py                  # §3 prints the transliterations + the FAIL-001 guard
```

| Acceptance criterion | How it was established |
|---|---|
| **Scenario 1 / SC-004 / FR-008** — ≥95% of non-Latin names get a Latin rendering, the original preserved in **every** case | **[live]** of 2 367 records, **104 carry an `el` name and 103 also carry `el-Latn`** (99.0%). The single miss is `"Oute Lepi"` — an `el`-tagged value that is *already* Latin script, so nothing is derived, which is correct. **0** records have an `el-Latn` without the original `el` beside it. Example: `Άγιος Φανούριος → Agios Fanourios` |
| Deterministic, snapshot-tested | **[eval]** `tests/test_translit.py` → **207 passed** (ELOT 743, ADR-0010): `Ρολόι → Roloi`, `Ευαγγελισμός → Evangelismos` vs `Ευτυχία → Eftychia`, `ΡΟΔΟΣ → RODOS` |
| **Scenario 2** — a value whose script contradicts its declared language (FAIL-001) is flagged, not trusted; addresses are not transliterated | **[eval]** `tests/test_translit.py` + `evals/test_structural.py::test_a_mis_scripted_value_is_flagged_never_transliterated_never_dropped`. Visible in `scripts/try_it.py`: `Родос` declared `el` → **mismatch, refused** |

---

## SC-005 — genericity (nothing hardcoded to Rhodes)

```bash
uv run pytest evals/test_genericity.py -q         # 10 passed
```

The same parametrised code path runs Rhodes **and** Takayama (高山, Gifu) — a different hemisphere, a different script, a script with *no* transliteration transform at all — with no branching in the test bodies. A static AST scan over `commons/`, `planner/`, `api/`, `compiler/` fails on any place literal or coordinate, and is proved to bite on injected ones.

The license spread is not a Rhodes artefact either: Takayama independently shows CDLA 325 · Apache-2.0 55 · CC0 20 (**[live]**, recorded in [the US1 devlog](../../docs/devlog/2026-08-01-spec001-us1-complete-the-map-fills.md); not re-run here).

**Honest limit:** SC-005 is *evidenced, not proved to the constitution's bar*. Both areas are committed fixtures and therefore rehearsed; the ≥3-areas-including-an-unrehearsed-one bar is a milestone gate, not this slice's.

---

## Automated gate mapping

Everything above that is marked **[eval]**, and where it runs in CI (`docs/design/test-strategy.md`).

| Check | Where it lives | CI job | Proves |
|---|---|---|---|
| Provenance completeness as a rate (100%) | `evals/test_structural.py` | 4 | FR-003 / SC-002 |
| Quarantine: no `bundleable=false` value in a bundle | `evals/test_structural.py` | 4 | Constitution V |
| Every `location` traces to geodata; a model coordinate moves nothing | `evals/test_structural.py` | 4 | FR-005 |
| Merge: no source lost, conflicts recorded, winner marked | `tests/test_merge.py` | 2 | FR-009 |
| Transliteration: ≥95%, original preserved, FAIL-001 guard | `tests/test_translit.py` | 2 | FR-008 / SC-004 |
| Seam purity: no provider SDK above `commons/llm.py` | `tests/test_llm_seam.py` | 2 | ADR-0004 |
| Cross-engine string folding (DuckDB vs Python) | `tests/test_cross_engine_normalization.py` | 2 | FAIL-005 |
| `POST /areas`, research SSE, `GET /sites`, auth, `ST_Within` | `tests/test_api_*.py` (47 tests, Tier-2 over real PostGIS) | 3 | FR-001/004/006/007 |
| Trajectory `superset` **and** order of `resolve_area → research → curate` | `evals/test_trajectory.py` | 4 | Constitution II |
| Genericity: Rhodes + Takayama, plus the AST place-literal scan | `evals/test_genericity.py` | 4 | SC-005 |
| Prompt-cache breakpoint, byte-identical prefix, realistic size | `evals/test_caching.py` | 4 | ADR-0004 / FAIL-006 |

No scenario here depends on connectivity beyond the online research phase — this slice is Define→Research, and `bundleable` is stamped so the downstream offline gate (Constitution I) still has something true to stand on.

---

## Known gaps this walkthrough does not paper over

Carried forward from `docs/TRY-IT.md` and the US1 devlog, which own the full list; repeated here only where a gap limits what a criterion above can claim.

- **`POST /areas` by *name* is very slow** — the Overture divisions lookup scans the hosted theme with no bbox pushdown, so a name resolve can hang for minutes and has frozen a browser tab. **Every step above uses the `bbox` path.** The name path is **[unverified]** here for that reason.
- **Google SSO is [unverified] end to end.** The code path is unit-tested against a mocked token exchange; every run above used the local dev-secret cookie.
- **Marker labels are always on**, so a few hundred markers overlap into noise. Presentation, not provenance — every value still carries its stamp.
- **Prompt caching is off in practice** — `curate` requests it correctly but its cached prefix is under Sonnet 5's 1 024-token minimum, and Anthropic caches nothing below the minimum without erroring (FAIL-006).
- **The `409` "research already running" guard is process-local** (a module-level set); a second process would not see the claim.

---

## T069 — seven-gate + airplane-mode verification, 2026-08-01

Recorded here rather than in a new doc: this is the close-out evidence for *this spec*, and it belongs beside the criteria it certifies. Adding a third runbook-adjacent file is the drift this rewrite exists to prevent.

**`main` @ `eec0a8b5e469d73d91a154841c4f88e58bc88465`** — every job status below was read from the actual run, not inferred.

| # | Job | Status on this SHA | Real, or a placeholder? |
|---|---|---|---|
| 1 | lint + typecheck | ✅ success | real — ruff, ruff-format, mypy strict, `tsc` |
| 2 | unit | ✅ success | real — `pytest tests/` + vitest |
| 3 | integration & component | ✅ success | real — PostGIS service container, 38 Tier-2 tests |
| 4 | deterministic-evals | ✅ success | real — `pytest evals/` |
| 5 | **e2e-airplane (merge gate)** | ✅ success | ⚠️ **placeholder.** See below |
| 6 | security | ✅ success | real — Semgrep, gitleaks, pip-audit, `uv lock --locked` |
| 7 | diff-guard | ⊘ skipped | real, but PR-only (`if: github.event_name == 'pull_request'`); it runs and passes on PRs |
| 8 | llm-judge-evals | ✅ success | placeholder — skips with no `ANTHROPIC_API_KEY`; the pinned-judge harness lands at DU-04 |

### Job 5 is a green stub, and "green" here does **not** mean airplane mode was verified

`.github/workflows/ci.yml`'s `e2e-airplane` job checks out the repo and echoes three lines. That is its entire body. There is **no Playwright dependency in `web/package.json`, no `test:e2e` script, and no e2e suite anywhere in the tree** — the only occurrences of the word "airplane" are comments saying the real test lands at DU-06/DU-07.

So the honest statement is: **the check exists and is wired as the merge gate; the offline runtime it is meant to gate does not exist yet.** The DU-00 airplane-mode e2e is *not regressed* only in the sense that there is nothing there to regress — no bundle, no service worker, no OPFS archive, no offline reload. Reporting job 5's green as "airplane mode verified" would be exactly the false assurance Constitution Article I exists to prevent, so it is written down instead. Building the real suite is DU-06's work, not this task's.

### Local full gate, run on this branch

| Command | Result |
|---|---|
| `uv run pytest -q` | **660 passed, 1 xfailed** in 87 s (Docker up, so the Tier-2 tests ran rather than skipping) |
| `uv run pytest -q -m integration` | **38 passed**, 623 deselected |
| `uv run pytest evals -q` | **31 passed, 1 xfailed** |
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | 85 files already formatted |
| `uv run mypy .` | success, no issues in 64 source files |
| `pnpm -C web test` | **144 passed**, 8 test files |
| `pnpm -C web typecheck` | clean |
| `pnpm -C web build` | built in 584 ms |

The single `xfail` is FAIL-006 — the cached prefix under the provider minimum, pinned deliberately by `evals/test_caching.py` so the silent no-op cannot come back unnoticed. A fix removing it is in flight in a parallel session.

### Addendum — the security gate went red days later, on nothing we changed

Re-running the same gates on the T068/T069 PR, **job 6 failed**: `pip-audit` found **PYSEC-2026-3552** in `cryptography 49.0.0` (fix: 50.0.0), a transitive dependency reached via `authlib`, `google-auth`, `joserfc` and `pyjwt`. The PR that triggered it changes two Markdown files and cannot introduce a dependency CVE — the advisory was simply published after the `eec0a8b` run above.

This is the gate behaving exactly as designed (ADR-0007 keeps it at **zero advisory ignores**, so it reddens on genuinely new findings and only on those), and it is worth recording next to the seven-gate table for one reason: **a "gates were green" verification has a shelf life measured in days.** Fixing it is a `pyproject.toml` / `uv.lock` pin bump, outside this task's file set and deliberately not done here.
