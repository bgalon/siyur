# 0004 — Layered planner: owned orchestration over a provider-routable model seam

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-25

## Context and Problem Statement

`tech-design.md` §6 locks "LangGraph planner + PostgresSaver" for M1. This session reopened that lock against four sharpened requirements: the planner must be (1) fast/efficient, (2) token-frugal via good context handling, (3) able to support different companies' models over time, and (4) able to switch model per task.

Requirements (2) and (3) are in tension. The strongest token-savers — Anthropic prompt caching, server-side compaction, context editing, task budgets — are **provider-specific**. A provider-agnostic abstraction that makes (3) easy typically collapses to a lowest-common-denominator (messages + tool calls) that *loses* those savings. Solving both inside one monolithic framework forces a bad trade; the design question is how to structure the layers so each requirement is served where it belongs. Related: the standing decision that Anthropic is the *one* sanctioned hosted LLM dependency (AGENTS.md) — going multi-provider expands that posture and is flagged for Ben, not decided here.

## Considered Options

- **A — Keep the §6 lock: LangGraph, monolithic.** Orchestration, context handling, and model calls all through LangChain/LangGraph. Multi-provider and per-node model binding work, but the abstraction can bloat prompts and silently break the prompt-cache prefix, and its default context handling is not token-optimal — you build the token layer anyway, inside a heavy dependency.
- **B — Lighter monolith: PydanticAI + LiteLLM, no durable graph.** Thin, model-agnostic, per-task model native; but no built-in checkpointer/HITL — durability is hand-rolled.
- **C — Layered, with a model-invocation seam.** Own the **orchestration + context-management** layer (the graph and how context is trimmed/cached/compacted — this is where tokens are saved and it is provider-independent). Put all model calls behind a **thin invocation seam** (`ModelRouter.complete(task, …)`) so provider/model choice is config, not code. In M1 the seam has a single **Anthropic-native adapter** that uses caching/compaction/context-editing to the full; per-task model routing (Haiku→research, Sonnet→curate, Opus→plan) happens *within* Anthropic now; cross-provider adapters are deferred behind the seam. The orchestrator sub-choice (LangGraph vs. PydanticAI + own Postgres checkpoint) is settled by a measured prototype bake-off, not asserted.

## Decision Outcome

Chosen: **C — layered planner over a provider-routable model seam**, because it is the only structure that serves all four requirements without the (2)↔(3) trade: token optimizations live in an owned layer and stay maximal for Anthropic in M1; provider-switch and per-task routing live at a narrow seam where they are additive. This **amends the §6 lock**. The *framework* sub-choice — reopened here — was decided directly by Ben (2026-07-25): **Option B, PydanticAI + LiteLLM + own Postgres checkpoint**, for its token transparency (thin layer → tighter prompts, no abstraction between the orchestrator and the cache prefix) and low code weight (durability is one `UPSERT` per step over the `user_plan` row we already persist). LangGraph's checkpointer/HITL machinery was judged not to earn its dependency weight over a single owned Postgres row. Firm regardless: (a) the `ModelRouter` seam with no provider types leaking above it; (b) Anthropic-native token features used fully in M1 — the M1 seam adapter is **Anthropic-native** (direct `anthropic` SDK for full caching/compaction/context-editing), with LiteLLM as the routing implementation introduced when cross-provider lands, so M1 pays no lowest-common-denominator tax; (c) per-task model routing enabled now, within Anthropic (Haiku→research, Sonnet→curate, Opus→plan); (d) cross-provider deferred, and gated on Ben ratifying the AGENTS.md sanctioned-dependency change.

The seam is capability-oriented, not lowest-common-denominator: the orchestration layer requests capabilities abstractly (`cache_prefix=True`, `long_run=True`) and each adapter implements them natively (Anthropic → `cache_control` / server-side compaction) or degrades gracefully. That is what keeps token optimizations from being flattened away by the abstraction.

## Consequences

- Good: M1 keeps Anthropic's best-in-class token features (caching, compaction, context editing, task budgets) — no lowest-common-denominator tax paid for a multi-provider future that is "might, over time," not now.
- Good: per-task model routing (the biggest cost lever) ships in M1 within one provider; cross-provider becomes "write one adapter," localized.
- Good: reopening the framework choice as a measured bake-off replaces an asserted lock with evidence, consistent with the discovery-spike ethos.
- Bad / accepted cost: the seam is a real abstraction to design and hold — no `anthropic.*` type may appear above it, or the "easy to replace" property is lost (see Confirmation). Provider-native token features do **not** port: each new provider needs its own caching/compaction strategy re-derived inside its adapter; the seam ports the *plumbing*, not the *optimizations*.
- Accepted cost: multi-provider is a standing-decision change (AGENTS.md sanctioned-dependency posture) that Ben must ratify before any non-Anthropic adapter is built.

## Confirmation

Validation spike (throwaway, `spike/planner_spike/`, before `planner/` is scaffolded): the M1 planner slice (resolve_area→research→curate→propose→HITL→compile) on the chosen stack — PydanticAI orchestration over the `ModelRouter` seam with an Anthropic-native adapter — measured on **output tokens per completed plan**, and verifying per-task model routing and prompt-cache hits. It is the reference implementation `planner/` is scaffolded from at ramp-up, not an A/B (the framework choice is settled). Durable architecture confirmation at build time: a **seam-purity test** (`tests/test_llm_seam.py`) asserting no `anthropic`/`openai`/`litellm` import appears in `planner/` or `commons/` above `commons/llm.py` — the tripwire that keeps "replace/extend the SDK later" a localized change rather than a pervasive refactor, and that keeps LiteLLM confined to the adapter. Prompt-cache effectiveness confirmed via `usage.cache_read_input_tokens > 0` on repeated same-area research (a caching-regression eval). TODO: add the seam-purity test path and the validation-spike task to `delivery-plan.md` on implementation.
