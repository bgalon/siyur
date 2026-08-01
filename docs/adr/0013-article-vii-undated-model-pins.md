# 0013 — Article VII and undated model IDs: a constructor-enforced exception

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-08-01

## Context and Problem Statement

Constitution **Article VII** ("Prompts & models have a governed lifecycle") requires the app's models to be **pinned to dated snapshots, never floating aliases**. The rule exists to prevent a specific failure: a provider silently re-pointing an alias at a new model, changing the app's behaviour and invalidating its evals with no diff in our repo.

Implementing the `ModelRouter` seam (ADR-0004, `commons/llm.py`) made the article literally unsatisfiable for two of its three tiers. Checked against the current model catalog on 2026-08-01:

| Tier (ADR-0004 routing) | Model ID | Dated snapshot published? |
|---|---|---|
| `research` → Haiku 4.5 | `claude-haiku-4-5-20251001` | ✅ yes |
| `curate` → Sonnet 5 | `claude-sonnet-5` | ❌ **none exists** |
| `plan` → Opus 5 | `claude-opus-5` | ❌ **none exists** |

For the Claude 5 family the short ID **is** the complete published identifier — the catalog states the IDs "are complete as-is; never append date suffixes," and appending one returns a 404. Crucially, these are **not floating aliases in the `-latest` sense the article was written to forbid**: there is no dated form behind them that they point at. Haiku 4.5 is the exception that shows the difference — it publishes both an alias (`claude-haiku-4-5`) and a dated snapshot, and the seam pins the dated one.

So the article's *text* is unsatisfiable while its *intent* — no silent model swaps — is not actually at risk. The seam session flagged this rather than quietly writing a fabricated date or leaving the gap unrecorded.

## Considered Options

1. **Ratify a constructor-enforced exception** — keep Article VII's text; require every undated pin to carry a written `undated_reason`, enforced at construction.
2. **Amend Article VII's text** in the constitution to say "dated where published, otherwise a recorded reason."
3. **Use only models with dated IDs** — literal compliance.

## Decision Outcome

**Option 1.** Article VII's text stands unchanged. `ModelPin.__post_init__` in `commons/llm.py` **refuses to construct** an undated pin unless it carries an explicit `undated_reason`, so the two exceptions are self-documenting at the point of definition and a third cannot be added casually.

This keeps the article's guarantee mechanical rather than a matter of vigilance (Article VI), which is the same posture as the seam-purity AST tripwire and the geo-API pins tripwire.

Option 2 was rejected as premature: amending a ratified constitutional article is heavy, and Article VII may become literally correct again if dated Claude 5 IDs are published later — at which point the constructor rule simply stops granting exceptions, with no constitutional change to undo.

Option 3 was rejected as actively harmful. Only Haiku 4.5 has a dated ID, so literal compliance would delete the Sonnet `curate` and Opus `plan` tiers and gut ADR-0004's routing table — a materially worse product, in exchange for **no reduction in the risk the article guards against**, since the undated IDs are not moving pointers.

### Consequences

- Good: no code change; the routing table stands as ADR-0004 specified it.
- Good: the exception is enforced by a constructor, so it cannot spread silently — a new undated pin fails to construct until someone writes down why.
- Good: self-reversing. If dated Claude 5 IDs ship, the reasons are removed and the pins tighten with no constitutional amendment.
- Cost / accepted: the constitution's text and the implementation disagree on their face; a reader must find this ADR to reconcile them. Mitigated by the `undated_reason` strings, which carry a re-pin instruction inline.
- **Standing obligation:** re-check the catalog at each milestone. This is a snapshot of what the provider publishes today, not a permanent property.

### Confirmation

- **`ModelPin.__post_init__`** (`commons/llm.py`) — raises unless an undated pin supplies `undated_reason`. This is the enforcement; it fails closed.
- **`tests/test_llm_router.py`** — asserts the routing table resolves the expected pinned identifier per tier, fully offline (no API key, no network).
- **Article VII's remaining requirements are untouched** — `prompts/research.md` still carries version / model / date / eval-link front-matter, and no floating `-latest`-style alias appears anywhere in the seam.
- **Verified against the live catalog on 2026-08-01**, not from model recall: `claude-haiku-4-5-20251001` (dated), `claude-sonnet-5` and `claude-opus-5` (canonical, undated, date suffixes 404).
