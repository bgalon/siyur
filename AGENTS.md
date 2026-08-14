# AGENTS.md — Siyur

**Siyur** (סיור — "a tour"): a multi-user platform to **research any area with an LLM into a shared cited commons → plan a day tour (with Plan B/C) → compile a self-contained offline bundle → travel guided with zero connectivity**. This repo is *also* the dogfooded case study for a GeoAI course — the way we build and document is a first-class deliverable, not overhead.

> Tool-neutral instructions live here. `CLAUDE.md` is a one-line shim that imports this file.

## Read first (before acting)

1. `docs/planning/prd.md` — the approved product contract (v2.0). **Do not re-open product decisions**; ambiguities → ask; decisions → ADR.
2. `docs/design/` — the design: `tech-design.md` (architecture + data spine + M1 slice), `delivery-plan.md` (deliverable units), `test-strategy.md` (three test tiers + CI), `agent-ops.md` (evolving agents), and `ux-handoff/` (the authoritative UX spec "The Field Atlas" + `INTEGRATION.md` mapping it to the data model & milestones).
3. `docs/planning/methods-ramp-up-standards.md` — the process standard (28-step ramp-up checklist, §7). `methods-stack-reference.md` — pinned components/versions/licenses.
4. `RAMP-UP-PROMPT.md` — the bootstrap instruction for the full ramp-up (executed after the design docs + discovery spike).

## Project map

```
docs/planning/   Approved PRD v2.0, methods standards, stack reference, transition plan  ← source of truth
docs/design/     Technical design + delivery/test/agent-ops docs (this phase)
docs/adr/        Decision records (MADR 4.0 minimal) — drafted by /adr
docs/devlog/     Per-session dev diary — distilled by /devlog
docs/failures/   Failure catalog FAIL-NNN — /failure; every entry adds a regression eval
.claude/         Agent governance: settings.json (permissions + logging hooks), commands/, hooks/
```

Code packages (`commons/`, `planner/`, `compiler/`, `api/`, `web/`) appear during the ramp-up / M1 — **not present yet**. We are currently in the *governance-bootstrap + design* phase.

## Phase & workflow (where we are)

Governance-first sequence (see the approved plan): **D0 bootstrap (this)** → design docs D1–D4 → throwaway discovery spike D5 → fold findings → remaining ramp-up (constitution, schema cards, CI, Spec Kit, evals) → Spec 001 → deliverable units DU-00+. Do not skip ahead to product code before the ramp-up creates the package skeleton.

## Commands (toolchain arrives at ramp-up, step 2)

Python 3.12 / `uv`. Until the ramp-up adds `pyproject.toml`, there is no app to run; the live surface is docs + `.claude/` governance. Planned once present:
- `uv sync` — install; `uv run pytest` — tests; `uv run ruff check .` — lint; `uv run mypy .` — types.
- Tests are three-tier (unit / integration+component / e2e airplane-mode) — see `docs/design/test-strategy.md`.

## Conventions

- **Commits:** conventional commits; subject describes the change; end with a `Co-Authored-By:` trailer naming the model. Small, reviewable increments.
- **Branches & PRs (ADR-0005):** one branch per unit of work — `agent/<ticket>-<slug>` (`<ticket>` = `DU-NN` · issue # · slug). Integrate **via PR to `main`** (`.github/PULL_REQUEST_TEMPLATE.md`), not direct pushes.
- **Merge gate is MACHINE-ENFORCED as of 2026-08-14 — the repo went public and branch protection is live.** All seven checks are required on `main` (`ci.yml` jobs 1–3/5–7 + `eval-quality.yml` job 4), with `enforce_admins: true`, `strict: true`, and force-pushes and branch deletion blocked. **This reverses the DU-00 position**, which held that protection was unavailable on a private free-tier repo and the gate was therefore a discipline only; that is ADR-0005's original intent, arriving late. Never push straight to `main`; open a PR; merge when the checks are green. Two things the machine still does *not* do, so the discipline is narrower but not gone: `required_approving_review_count` is **0**, so nothing forces a second pair of eyes, and **`strict: true` means your branch must be up to date with `main`** — rebase before you push or the merge is refused (this also kills the stale-`BASE_SHA` phantom failures that produced #93's "1005 changed lines" on a 125-line PR).
- **Gate the merge with `scripts/merge-guard.sh <pr>`, not with your eyes (FAIL-009).** It reads the check state as JSON, counts non-passing rows, and separately asserts all seven required jobs are **present** — a job that never ran is not a job that passed. `main` went red twice in two days because `gh pr checks … | tail -4` hid the failures, which sort *above* the passes: both operators knew the rule and both checked, and the check lied. **Never pipe *any* status command into `head`/`tail` and treat the remainder as the answer** — the habit transfers between tools, and the guard only fixed one of them. Hours after that guard landed, the same author ran `git status --short | head -12` / `| tail -20`, missed three modified files in the hidden middle, and reset a worktree over a subagent's in-progress work. If output is too long to read, **count it** (`--porcelain | wc -l`), **filter it by predicate** (`git diff --name-only -- <paths>`), or ask for JSON — truncating one end is never the fix.
- **Triage a red check by duration before you debug it.** These jobs finish in **7s–1m40s**. A "failure" that ran 45m–2h30m is almost always platform infrastructure, not your code — on 2026-08-06 an Actions incident produced nine red checks of which eight were debris. **Confirm a failure reproduces on a fresh run before debugging it**, and prefer `gh pr checks <n> --watch` (plain `gh pr checks` exits non-zero whenever a check is not passing, which kills `until` loops under `set -e` — 22 recorded tool failures came from exactly that).
- **`size-override` records a claim, so only use it when the claim is true.** The label is the honest escape hatch for a legitimately large PR; it is *not* a way past a red count you disagree with. Clearing a **measurement artifact** with it writes "this PR is >500 lines, override justified" into the governance trail about a PR that isn't — fix the measurement instead (see `exhibit/U2-override-that-lies`).
- **Parallel sessions = isolation:** each concurrent session works in its **own** checkout — a `git worktree` locally (`EnterWorktree`, or `git worktree add ../wt-<slug> -b agent/<ticket>-<slug>`), a separate branch/sandbox in the cloud. **Never run two sessions in one working directory** — they race on files.
- **A worktree isolates FILES. It does not isolate the database, the ports, the Docker volumes, or the Valhalla graph (FAIL-011).** `docker-compose.yml` binds fixed ports, so every checkout reaches the *same* containers by default — and `tests/conftest.py`'s integration fixture **deletes every row in every table**. Running Tier 2 while another session has a dev stack up destroys its data mid-request, invisibly to both: nothing in `docker ps`, the API log or the test output names the other party. **Before `pytest -m integration`, point it at a private database** — `SIYUR_DB_PORT=5433 docker compose -p siyur-test up -d postgis`, then `SIYUR_DATABASE_URL=…@localhost:5433/siyur`. The same applies to any shared resource: FAIL-010 was a Docker volume serving the wrong routing graph for six days. `git worktree` *looks* like an isolation primitive and is only a filesystem one, so the surface everyone reasons about is the one already safe.
- **Look for the other session before you pick up work — isolation does not prevent duplication (FAIL-008).** Isolation stops two sessions corrupting one file; it does nothing to stop them doing the same *task*, and a worktree makes that duplication tidier rather than less likely. **Before taking anything off a task list, run `git worktree list` and `ListAgents`, and check `gh pr list`.** If another checkout or session exists, find out what it holds — its branch name usually says — and take the complement. Two sessions once spent ~2h independently reconciling the same five documents; one command would have shown it. Then **branch before the first edit** (`agent/<ticket>-<slug>`): starting on `main` is not yet an ADR-0005 violation, but every one of them starts there, and branching afterwards means salvaging a dirty tree. `.claude/hooks/concurrent_sessions.py` warns about both at session start — the warning is a prompt to do this, not a substitute for it.
- **A rule change reaches other sessions only after merge → their checkout updates → they restart.** `CLAUDE.md`/`AGENTS.md`, `.claude/settings.json` and the hooks are all read **once, at session start**, from that checkout. Editing them under a running session changes nothing for it, and a worktree on an older branch does not have them at all. **To reach a live session, message it** — that is the only channel that works.
- **Decisions → ADR:** any session that chose between libraries/schemas/architectures ends with `/adr`. Mark `drafted-by` / `approved-by`.
- **Failures → catalog:** every real failure → `/failure` → a FAIL-NNN entry **plus a regression eval/guardrail** before it closes. No exceptions.
- **Sessions → devlog:** decision-bearing sessions end with `/devlog`.
- **Licensing is mechanical:** every data value is stamped with source + license + a `bundleable` flag; nothing `bundleable=false` may enter an offline bundle. ODbL attribution renders on every map. Never read or write `.env*` or `secrets/`.
- **Credentials: use them, never see them.** Local dev keys live in the **macOS Keychain**, not in any file — `.env*` is blocked by `.claude/hooks/guard_secrets.py` because the primary reader of this filesystem is an agent, and a key that reaches a transcript reaches `logs/` and the course feed. **`scripts/dev.sh` already loads `ANTHROPIC_API_KEY` from the Keychain**, so the normal answer is *run `scripts/dev.sh start`* and stop thinking about it. To run one command that needs a key, borrow it for that process only and never assign it to a shell variable you might later echo:

  ```bash
  ANTHROPIC_API_KEY="$(security find-generic-password -a "$USER" -s siyur-anthropic-api-key -w)" \
    uv run <command>
  ```

  An already-set environment variable always wins, so CI and cloud secret injection are unaffected. **Never print a key, never write one into a file, a test fixture, a commit message or a PR body, and never ask Ben to paste one into the chat** — a value in the transcript is a leaked value. If a key is missing, say which variable is unset and how to store it; do not work around it by hardcoding one.
- **Generated files** (styles, changelogs, bundles): fix the generator, never hand-edit the output.
- **Read package metadata from the registry JSON, never from a summarizer.** Fetching `https://pypi.org/pypi/<name>/json` through a summarizing tool has twice reported `opening-hours-py` as **"0.11.1"** while the same response carried `info.version: 2.1.4` — two majors wrong, on the one field a supply-chain check exists to verify. Parse the fields yourself (`curl … | python3 -c`), and treat the **lockfile hashes as the authority**. Same discipline as the geo pins: verify the version, the publisher, the registration date and the licence against the source of truth, not a paraphrase of it.

## Geo APIs — READ BEFORE WRITING GEO CODE (stale-API traps)

All four core libs crossed breaking majors; models still emit the old APIs. **Pinned versions and the traps to avoid:**

| Library | Pin | v-old idioms to NOT emit → use instead |
|---|---|---|
| Shapely | `~=2.1` | not `cascaded_union`, `.type`, mutable geoms → use `unary_union`, `.geom_type`, vectorized ops |
| h3-py | `~=4.5` | **entire API renamed in v4**: `geo_to_h3`→`latlng_to_cell`, `h3_to_geo`→`cell_to_latlng`, `k_ring`→`grid_disk` |
| OSMnx | `~=2.1` | not 1.x paths/kwargs (`utils_graph`, old `graph_from_place` args) — 2.0 was a breaking rewrite |
| GeoPandas | `~=1.1` | not 0.x `.unary_union` on frames / deprecated I/O engines — 1.x defaults to pyogrio + shapely-2. **`gpd.datasets` was REMOVED in 1.0** — `gpd.datasets.get_path('naturalearth_lowres')` raises `AttributeError`, and models emit it constantly. Read committed data files instead (ADR-0029 pins Natural Earth admin-0 for country resolution) |

Python 3.12, uv-managed. CRS discipline: geometries are **EPSG:4326** (lon, lat) unless a schema card says otherwise; never let the LLM emit coordinates or spatial arithmetic — compute in PostGIS/DuckDB/shapely. A `tests/test_geo_api_pins.py` tripwire will fail CI on any stale call once code exists.

Data schemas (POI/site, itinerary, route-leg, bundle-manifest, tile-source) get `docs/data/*` schema cards at ramp-up — never guess a schema; read the card.

## Standing decisions (inherited — do not re-litigate without an ADR)

- Name = Siyur; **generic any-area**, nothing hardcoded per place.
- **Open-source-first** application stack; GCP is deployment substrate; Google SSO is the one sanctioned hosted identity dependency; license compliance is an engineering practice.
- **Narration posture = rich, CC BY-SA** bundled text with per-article attribution (PRD §7).
- **Shared research commons is a global resource**; personal data (plans, notes, prefs) is per-user and private.
- Open PRD §13 decisions (#1 constitution reframe, #2 review-data policy, #3 course-scope/GCP, #4 commons write policy, #5 schematic/timeline milestone) are Ben's to resolve — flag, don't decide.

## Course-feed (this build is teaching material)

Artifacts are near-free because hooks/commands produce them: ADRs, devlog, failure catalog (+regression eval), versioned `prompts/` + `evals/history.csv`, changelog + `exhibit/<unit>-<slug>` tags. The course repo (`~/code/siyur-course`) *observes* this repo one-way — we emit exhibit-tag candidates and `course-wishlist` issues; we never edit the course repo from here.
