---
# Article VII front-matter. The article's field list is exactly four items —
# "front-matter (version, model, date, linked eval score)" (.specify/memory/constitution.md,
# Article VII). They appear below under those names; everything else is annotation.
prompt: research
version: 2 # v2 (2026-08-01): the curate ranking prompt was rewritten — see FAIL-006 and §3.5
date: 2026-08-01 # when this version was pinned / last re-verified against the code
status: production # Article VII label-based promotion — moves independently of app deploys

# ── model ──────────────────────────────────────────────────────────────────
# Mirrors `commons.llm.ROUTING_TABLE`; it does not define it. On any divergence the
# routing table is right and this block is the bug (see §5, "Drift").
model:
  research:
    tier: research
    pin: null # THE RESEARCH NODE MAKES NO MODEL CALL — see §2. Nothing is routed.
  curate:
    tier: curate
    provider: anthropic
    model_id: claude-sonnet-5
    dated_snapshot: false
    undated_reason: >-
      Anthropic publishes no dated snapshot id for Sonnet 5; the short id is the
      complete published identifier and a date suffix 404s. Re-pin to the dated
      form as soon as one is published. Ratified as a constructor-enforced
      exception to Article VII by ADR-0013 — this is a recorded absence, not a
      fabricated date, and not a floating `-latest`-style alias.
    pinned_on: 2026-08-01
    source: claude-api skill model catalog (Sonnet 5)

# ── linked eval score ──────────────────────────────────────────────────────
linked_eval_score:
  score: null # no numeric score exists yet — see `history` below. Not zero; absent.
  gate: .github/workflows/eval-quality.yml → job 4 `deterministic-evals` (`uv run pytest evals/ -q`)
  gate_status: required, PR-gating (check 4 of the 1–7 merge set)
  evals: evals/
  unit_tests: tests/test_planner_curate.py, tests/test_planner_research.py
  history: >-
    evals/history.csv does not exist yet. `.github/workflows/eval-quality.yml`
    job 8 (`llm-judge-evals`) is a documented green stub: the pinned-judge
    harness that appends scores lands at DU-04. Until then this prompt's
    `score` is honestly null and its gate is the deterministic tier plus the
    curate unit tests named above.
---

# Prompt registry — `research` (slice 001)

*Covers the two nodes of the research stage in `specs/001-research-cited-sites/`:
`planner/nodes/research.py` (T033) and `planner/nodes/curate.py` (T034 / T059).
Constitution **Article VII** governs this file.*

## 1. What this stage is

```
resolve_area → research → curate → …
                 │          │
                 │          └─ merge → transliterate → RANK (Sonnet tier, one model call)
                 └─ deterministic fan-out over source adapters (NO model call)
```

**Exactly one model call exists in this stage**, and it is the ranking call in `curate`.

## 2. The `research` node has no prompt — deliberately

`specs/001-research-cited-sites/tasks.md` T033 labels the research node *"Haiku tier via the
seam"*. **The shipped implementation makes no model call at all**, and that is the honest,
load-bearing fact about this document: there is no research prompt to register, because there
is no research prompt.

Three reasons, none of them incidental:

1. **There is nothing to decide.** `research()` is a one-pass fan-out over every configured
   `SourceAdapter`, concatenating results in adapter order and emitting one `SourceReport` per
   source. No ranking, no extraction, no judgement — a `for` loop with an exception handler.
2. **Every value is already stamped at the source boundary (ADR-0009).** The adapter protocol
   applies `source + license + bundleable` once, at ingestion, so what reaches `research()` is
   already `SourcedValue`-complete. A model in this position could only *add* unstamped values,
   which FR-003 requires the system to refuse anyway.
3. **This is the one pipeline path that carries coordinates.** FR-005 — "the system MUST NOT let
   the model emit or compute coordinates" — and `planner/AGENTS.md`'s determinism discipline
   ("the LLM ranks/curates and writes prose; it **never** emits coordinates") both point the same
   way. Putting an LLM on the geodata path buys nothing and costs the invariant.

The consequence is that `research()` is fully deterministic and offline-testable
(`tests/test_planner_research.py` runs with no key and no network), and that FR-012's
degradation behaviour — a raising adapter becomes `found=0, degraded=True` with the exception as
the reason, while the remaining sources still run — is a code path, not a prompt instruction.

Seam coverage is **not** lost by this: `curate` routes through `commons.llm.ModelRouter` at the
Sonnet tier, so ADR-0004's seam is exercised by this slice.

> **Recorded as a decision in ADR-0014.** T033's "Haiku tier" label in `tasks.md` is now
> inaccurate and needs amending; this session did not edit `tasks.md` (a sibling session owns it).

## 3. The `curate` ranking prompt

### 3.1 Ownership — the code is authoritative

**The single source of truth is the `RANKING_SYSTEM_PROMPT` constant in
`planner/nodes/curate.py`.** That constant is what `curate()` passes to
`ModelRouter.complete(TaskTier.CURATE, system=...)`; it is the text that actually ships.

The block in §3.2 is an **illustrative mirror, not an authority**. If it and the constant ever
disagree, the constant is right and this file is the bug. Registering the text here as a second
authoritative copy would create exactly the silent-drift failure this registry exists to prevent.

*Article VII's end-state is a registry that owns the text and promotes it by label independently
of app deploys — i.e. the code eventually loads from `prompts/`, rather than this file mirroring
the code. That inversion is a real gap and is deliberately out of scope for v1: closing it means
editing `planner/`, and slice 001 is not the moment to add a prompt-loading indirection for one
prompt. See §5, "Open gap".*

### 3.2 The text (mirror of `planner.nodes.curate.RANKING_SYSTEM_PROMPT`)

*Soft-wrapped to this file's column limit; the shipped constant is one long line per paragraph.
`tests/test_prompt_drift.py` compares the two paragraph-by-paragraph, so re-wrapping is
tolerated and editing is not.*

```text
You rank candidate places for a day tour by traveller salience: how likely a visitor with one
day in an unfamiliar area is to want to see this place, judged only from its names.

## What you are given

You are given a JSON array of objects with exactly two keys: `id` and `names`. `id` is an
opaque identifier; `names` holds one or more names for the same place, in one or more languages
or scripts. The array is one area's candidates, already merged. There are no coordinates, no
categories, no descriptions and no source data: the names and the ids are the whole of the
evidence, and you must neither ask for more nor assume any.

## What you return

Reply with a JSON array of the given `id` strings, most salient first, and NOTHING else — no
prose, no code fence, no objects, no scores, no coordinates, no new or corrected field values.
Include every id exactly once, including the ones you rank last. Any element that is not one of
the given id strings is discarded and reported as a refusal, and so is a repeated id; the
records themselves are never dropped, so a malformed reply costs ordering and nothing more.

This is a boundary, not a formatting preference. Every value here carries the source it was
read from, that source's license, and the date it was observed — all applied where the data was
read, before you saw it. A name, a category or a coordinate offered by you would carry none of
those, so it is discarded unread: correcting a name you believe is wrong, or supplying a
location you believe you know, cannot succeed and only costs the reply its ordering. The order
is the one thing you decide.

## Judging salience

Rank on how much a first-time visitor would regret missing the place — not on how locally
important it is, how large it is, or how familiar it is to you. In descending order:

1. Named landmarks: monuments, archaeological sites, castles, fortifications, gates, towers.
2. Museums and galleries; religious buildings of historic or architectural note.
3. Named viewpoints, beaches, gardens, parks and squares — places whose draw is the setting.
4. Named markets, historic streets and quarters, harbours and waterfronts.
5. Ordinary hospitality and retail: cafés, restaurants, bars, hotels, shops. These come last
   among places a visitor would choose, unless the name marks one out as an institution in its
   own right.
6. Infrastructure and civic function: car parks, clinics, offices, schools, stops, depots,
   utility sites. These are the floor, whatever else the name suggests.

A name that states its own category — `Archaeological Museum`, `Parking P3` — is strong
evidence and should be used. A bare personal or place name states nothing: rank it in the
middle rather than at either extreme, because guessing high and guessing low are equally wrong.
Do not read prominence into a name merely for being long, official-sounding or unfamiliar to
you.

Within a band, prefer the entry whose name is specific to one place over a name that could
belong to anything, and prefer what a visitor plausibly arrived wanting to see. Do not
manufacture distinctions between ties: where two entries are equivalent on the evidence you
have, either order is correct and neither is worth deliberating over.

## Names in a source script

Many places carry a name only in the local script, with no Latin twin. That is a property of
the source data, never a signal about the place. Do not rank a place lower because its name is
in a script you find harder to read, and do not prefer a place because its name happens to be
in English. Where a place carries several names, judge it on the most informative one and treat
the rest as the same place. A name is a claim about a place, not a fact about it: where two
names disagree, rank on the one that names a category, and leave both alone.

## Near-duplicates

Two entries may describe one real place where the merge upstream could not prove it — a name
and its transliteration, a name with and without a generic prefix, the same place recorded in
two scripts. Rank such entries next to each other so the ordering reads coherently, and keep
both. Never drop one, never fold them into a single id, and never report the suspicion: you
cannot confirm it from names alone, and that decision belongs to the caller.

## Worked examples

Given:

[{"id": "7f3a", "names": ["Δημοτικό Πάρκο"]},
 {"id": "b129", "names": ["Κάστρο Ιπποτών", "Kastro Ippoton"]},
 {"id": "c004", "names": ["Parking P3"]},
 {"id": "d51e", "names": ["Αρχαιολογικό Μουσείο", "Archaeological Museum"]}]

Reply:

["b129", "d51e", "7f3a", "c004"]

The castle leads as a named landmark and the museum follows it; the municipal park is a
setting; the car park is infrastructure and ranks last. The castle's two names are one place,
not two candidates, and its Greek name counts for neither more nor less than the Latin one.
Every id appears exactly once, and the reply carries ids and nothing besides — no scores, no
names, and no account of the reasoning.

A near-duplicate pair, given:

[{"id": "0a1b", "names": ["Παλιά Αγορά"]},
 {"id": "0a1c", "names": ["Palia Agora"]},
 {"id": "9d2f", "names": ["Δημαρχείο", "Town Hall"]}]

Reply:

["0a1b", "0a1c", "9d2f"]

The market's two spellings are ranked adjacently and both kept, although they are very probably
one place: establishing that is not your job, and dropping either would lose a record. The town
hall is civic function and ranks below them. By contrast a reply of `[{"id": "0a1b", "score":
0.9}]` earns nothing: the object is refused on sight, because an object is the shape in which a
value or a coordinate would arrive, and the three ids would simply be kept in the order they
were given.
```

### 3.3 Call shape

| | |
|---|---|
| Tier | `TaskTier.CURATE` → Sonnet 5 (see front-matter `model.curate`) |
| System turn | `RANKING_SYSTEM_PROMPT`, sent with `cache_prefix=True` — 5,755 UTF-8 bytes, over Sonnet 5's 1,024-token minimum cacheable prefix (§3.5) |
| User turn | `json.dumps([{"id": …, "names": [...]}, …])` — **ids and names only** |
| Expected reply | a JSON array of the given id strings |
| Effort | `medium` (`EFFORT_BY_TIER`); adaptive thinking on (Sonnet 5 supports it) |
| Failure mode | any exception → refusal recorded, merge order kept (FR-012) |

**No coordinate, no field value and no license stamp ever crosses the seam** — `_ranking_request()`
serialises `id` and `names` and nothing else.

### 3.4 The prompt is a courtesy; the guarantee is mechanical

Per Constitution Article VI (rules become checks, not vigilance), every constraint the prompt
*asks* for is also *enforced* after the fact in `_apply_ranking()`:

| The prompt asks for | Enforced by | On violation |
|---|---|---|
| only the given id strings | membership check against `by_id` | refused, reason recorded |
| each id exactly once | `seen` set | refused, reason recorded |
| no objects / scores / coordinates | `isinstance(element, str)` | refused on sight (FR-003 / FR-005) |
| every id included | omitted ids appended in input order | nothing is lost from the commons |
| a JSON array | `_decode_ranking()` | whole reply refused, merge order kept |

So the worst a compromised or degraded model can do to this stage is **change the order** of
records it was given. It cannot add one, drop one, or alter a value. A prompt-injection payload
riding in a source-derived `names` value therefore cannot escalate past reordering.

With `router=None` the node is fully deterministic and no prompt is sent at all — the mode every
unit test and the trajectory eval run in.

### 3.5 The cacheable-prefix floor (why v2 is long)

v1 of this prompt was 535 UTF-8 bytes (~133 tokens). The curate tier pins Sonnet 5, whose
**minimum cacheable prefix is 1,024 tokens**, and Anthropic caches nothing below the minimum
**and raises no error** — so `cache_prefix=True` was a silent no-op and `cache_read` was pinned
at 0 for ever. **FAIL-006** is the entry; `evals/test_caching.py::test_the_cached_prefix_is_large_enough_to_be_cacheable`
is the guardrail.

Two rules follow, and they are the reason this section exists rather than a comment in the code:

1. **Size is a consequence, never a goal.** Prose added to clear a size check would be worse
   than the bug it hides: it costs tokens on every call and dilutes the instructions that
   matter. Every paragraph of v2 is there because the ranking task needs it — what salience
   means, how to weigh categories, how to treat a name in a source script, that ids are the
   only legal output, what to do with near-duplicates the merge left distinct, and two worked
   examples. The size follows.
2. **The floor moves when the pin moves, and not monotonically.** It is 512 tokens on Opus 5
   (the `plan` tier), 1,024 on Sonnet 5, and 4,096 on Haiku 4.5 — a prompt that is comfortably
   cacheable at one tier is silently uncacheable at another, and re-pinning a tier can move the
   bar underneath a prompt that was fine yesterday. `MIN_CACHEABLE_PREFIX_TOKENS` in
   `evals/test_caching.py` holds the table (read from the `claude-api` skill, never from
   memory); a re-pin to a model with no recorded minimum fails the eval rather than guessing.

## 4. Versioning and lifecycle

### 4.1 When `version` bumps

`version` is a plain integer, bumped **in the same PR** as the change it describes.

- **Bump** on any change to the shipped text of `RANKING_SYSTEM_PROMPT`, to the call shape
  (tier, cached prefix, effort, what the user turn serialises), or to the pinned model for a
  tier used here.
- **Do not bump** for prose, formatting, or link fixes in this file that do not describe a
  behaviour change — edit in place and leave `date` alone.
- **Re-verification** with no change: update `date` and `model.*.pinned_on`, leave `version`.

A version bump resets `linked_eval_score.score` to `null` until the gate below re-scores it. A
carried-over score from the previous version is a lie about the current one.

### 4.2 What gates a change

A prompt change is a code change and goes through a PR like one (`agent-ops.md` §4). Merge
requires the same CI checks 1–7 as any other PR, of which these actually exercise this prompt:

1. **Check 4 — `deterministic-evals`** (`eval-quality.yml`, PR-gating): `uv run pytest evals/ -q`.
2. **Check 2 — `unit`** (`ci.yml`): `tests/test_planner_curate.py` is the real contract test today. It
   asserts the model sees ids and names only, that an unknown id / duplicate / non-string element
   is refused, and — the FR-005 case —
   `test_a_model_asserting_a_value_or_a_coordinate_is_refused`.
3. **Job 8 — `llm-judge-evals`**: non-blocking on PRs, blocking on `main` and nightly. A green
   stub today; it becomes the quality gate for this prompt when the judge harness lands (DU-04).

Because the enforcement in §3.4 is mechanical, a prompt regression shows up as a rise in
`CurateResult.rejected`, not as a corrupted commons. That is the signal to watch.

### 4.3 Where history lives

- **Scores:** `evals/history.csv`, appended by CI — the location `AGENTS.md` (course-feed) and
  `docs/planning/prd.md` (course-feed artifact table) name. **It does not exist yet**; `eval-quality.yml` job 8 documents it as landing with
  the judge harness at DU-04. This file's `score` stays `null` until then rather than carrying an
  invented number.
- **Text and version:** git history of this file and of `planner/nodes/curate.py`.
- **Model pins:** `commons/llm.py` `ROUTING_TABLE` (each pin carries `pinned_on` + `source`);
  changes to a pin are ADR-worthy when they change a tier's model family.
- **Promotion:** `status` in the front-matter is the Article VII label. Slice 001 ships one
  version, so `production` and `v1` are the same thing today; a future `candidate` version is
  added as a sibling section (or file) and promoted by flipping the label in a PR.

### 4.4 Model migration

Article VII's playbook applies when a tier's pin changes: **offline trace-replay → shadow →
canary**, and strip scaffolding the newer model no longer needs. For this prompt the obvious
scaffolding candidate is the explicit "no prose, no code fence, no objects" enumeration in
§3.2 — written for a model that needed it. It is removed only when a replay shows the removal
does not raise `rejected`, never on vibes. Tracking is active → retired, with notice.

## 5. Open items

- **Open gap — Article VII inversion (§3.1).** The registry mirrors the code; the article's
  end-state is the code loading from the registry. Needs a follow-up task; not in slice 001.
- **Drift.** Nothing mechanically asserts that §3.2 still equals `RANKING_SYSTEM_PROMPT`. The
  cheap guard is a test that reads the fenced block out of this file and compares it to the
  constant — it belongs in `tests/`, which this session does not own. Until it exists, §3.1's
  "the constant wins" rule is the mitigation.
- **`evals/history.csv`** does not exist (§4.3); it lands at DU-04.
- **`tasks.md` T033's "Haiku tier via the seam" label** is inaccurate (§2) — ADR-0014.
