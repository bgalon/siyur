# 0014 — The `research` node makes no model call: a deterministic adapter fan-out

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: _pending_ · Date: 2026-08-01

## Context and Problem Statement

`specs/001-research-cited-sites/tasks.md` **T033** specifies the research node as: *"fan out to the source adapters over the polygon, collect stamped records, report per-source counts and degradation **(Haiku tier via the seam)**"*. The parenthetical is a routing label, and it matches ADR-0004's per-task routing table (`research` → Haiku 4.5) and `planner/AGENTS.md`'s "Haiku=research, Sonnet=curate, Opus=plan".

Implementing T033 (`planner/nodes/research.py`, merged in `0489608`) surfaced that **there is no work at that stage for a model to do**. The label describes a tier the node never calls. Writing a model call in anyway — to make the implementation match its own task line — would have been cargo-cult routing, so the node shipped deterministic and the discrepancy is recorded here rather than quietly absorbed.

Three facts force the question now, at the moment T064 authors `prompts/research.md`:

1. **Nothing at that stage is a judgement.** `research()` is one pass over every configured `SourceAdapter`, concatenating records in adapter order and emitting one `SourceReport` per source. There is no ranking, no extraction, no disambiguation — the whole node is a `for` loop with an exception handler implementing FR-012 (a raising adapter degrades to `found=0, degraded=True`; the remaining sources still run).
2. **Every value it handles was already stamped at the source boundary (ADR-0009).** The adapter protocol applies `source + license + bundleable` exactly once, at ingestion, precisely so provenance is single-sited rather than a matter of downstream vigilance. What reaches `research()` is already `SourcedValue`-complete. A model here could only *add* unstamped values — which FR-003 requires the system to refuse anyway.
3. **This is the one pipeline path that carries coordinates.** FR-005: *"the system MUST NOT let the model emit or compute coordinates."* `planner/AGENTS.md`: *"the LLM ranks/curates and writes prose; it **never** emits coordinates or does spatial/temporal arithmetic."* An LLM sitting between the geodata adapters and the commons is exactly the placement those rules exist to forbid.

So the tier label implies a call whose only available effects are forbidden ones, at zero benefit, on the highest-risk path in the slice. A prompt-registry entry for a research prompt would have had to invent one.

## Considered Options

1. **Ratify the deterministic implementation; correct the label.** `research` makes no model call; T033's "(Haiku tier via the seam)" is amended to say so; `prompts/research.md` records the absence explicitly.
2. **Add a Haiku call to honour the label** — have the model normalise, classify or pre-filter records inside the research pass.
3. **Leave the discrepancy unrecorded** — the code is right, the task line is stale, move on.

## Decision Outcome

**Option 1.** The `research` node is a deterministic fan-out and makes **no** model call. This is a property of the design, not an unfinished implementation: the node's inputs are already provenance-complete and its outputs carry coordinates, which is the exact combination that leaves a model nothing legitimate to contribute.

Option 2 was rejected as actively harmful. Every plausible job for a model there is either already done (stamping, ADR-0009) or forbidden (touching values or coordinates, FR-003 / FR-005). A pre-filter would additionally undermine FR-012's honesty guarantee — the user is told what was and was not found, and a silent model-side drop is precisely the "silently-incomplete result presented as complete" that FR-012 forbids. It would also cost the node its offline testability: `tests/test_planner_research.py` runs today with no key and no network.

Option 3 was rejected because the label is load-bearing in three places (`tasks.md` T033, ADR-0004's routing table, `planner/AGENTS.md`) and a reader who trusts it will look for a research prompt that does not exist — as T064 did. `docs/design/delivery-plan.md` DU-02 also lists a **caching-regression eval** ("`cache_read > 0` on repeated same-area research", also T065) whose name presumes a research-tier call; that eval must be re-aimed at the `curate` tier, which is where the cached prefix actually is. An unrecorded gap between the spec's routing story and the code is the kind of thing that gets "fixed" later by adding the call.

`TaskTier.RESEARCH` and its Haiku 4.5 pin **stay in `commons/llm.py`**. The tier is unrouted today, not deleted: slice 002 adds narration/story adapters (FR-011 defers them), which is a plausible high-volume, cheap-model workload. Removing the pin now and re-deriving it later costs more than leaving it.

### Consequences

- Good: **the seam is still covered by this slice.** `curate` routes through `commons.llm.ModelRouter` at the Sonnet tier (`planner/nodes/curate.py`), with `cache_prefix=True` — so ADR-0004's seam, its per-task routing and its caching lever are all exercised. Nothing is lost by research staying deterministic.
- Good: the one path carrying coordinates has no LLM on it, so FR-005 holds *structurally* at the research stage rather than by prompt instruction.
- Good: `research()` is fully offline and reproducible — no key, no network, no non-determinism in the tests or the trajectory eval's `resolve_area → research → curate` sequence.
- Good: cheaper. The highest-volume stage of the slice makes zero token spend.
- **Bad / accepted cost — `tasks.md` T033's tier label is now inaccurate** and must be amended to drop "(Haiku tier via the seam)". This ADR does **not** make that edit: a sibling session holds `specs/001-research-cited-sites/tasks.md` and two sessions must not race one file (ADR-0005). **Follow-up, unowned:** amend T033's label, and re-aim the T065 caching-regression eval (and the matching `delivery-plan.md` DU-02 line) at the `curate` tier.
- Bad / accepted cost: `TaskTier.RESEARCH` is a routing entry with no caller until slice 002. `tests/test_llm_router.py` still asserts it resolves, so it cannot rot silently, but it is dead weight until then.
- Accepted: `prompts/research.md` v1 registers **one** prompt (the `curate` ranking prompt) under a filename that names the stage, not the node. The file says so in its first section rather than being renamed, because "the research stage" is the unit slice 001 talks about.

### Confirmation

- **`planner/nodes/research.py`** — the module imports no router and its signature takes none (`research(polygon, adapters, *, on_source)`). A model call cannot be added without changing the signature, which is a visible diff.
- **`tests/test_planner_research.py`** — exercises the node with no `ModelRouter`, no API key and no network; a reintroduced model call would fail it or hang it.
- **`tests/test_llm_seam.py`** — the AST seam-purity tripwire; `planner/` may not import a provider SDK regardless.
- **`prompts/research.md` §2** — the registry states the absence explicitly and links here, so the next reader of T033's label finds the reconciliation.
- **Trajectory eval** — `resolve_area → research → curate` superset match still holds; the node sequence is unchanged by this decision, only the tool-vs-model character of one step.
- **Open until closed:** T033's label still reads "(Haiku tier via the seam)" in `tasks.md`. That is the one live inconsistency this ADR knowingly leaves behind.
