# 0008 — Commons write policy: direct auth-gated write to the shared commons (slice 001 scope)

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-31

## Context and Problem Statement

Spec 001 (`specs/001-research-cited-sites/`) is the first vertical slice: research a delimited area into cited site records rendered on the map. Those records enter the **shared, global research commons** — a standing project decision (the commons is a global resource). But **how** a record enters that shared commons is **PRD §13 #4 ("commons write policy"), a decision the constitution reserves to Ben** and forbids agents from resolving silently. FR-007 therefore could not be made testable without settling it, so the `clarify` step surfaced it (Q1) rather than defaulting it.

The slice needs an answer to "where does a freshly-researched record land, and how are duplicates handled," but does **not** need the full governance apparatus (spam control, trust tiers, moderation) that a mature multi-user commons will eventually require.

## Considered Options

- **A — Open shared write (auth-gated).** Research writes directly into the shared commons; duplicates are prevented by the already-specified merge rules (union-first, spatial+name match at ε=25 m / τ=0.6, no source ever lost). Writes require a signed-in user. The richer governance policy is layered on later, additively.
- **B — Staged → promote.** Writes land in a per-user staging space and are promoted into the shared commons via a merge/review step. Protects the shared commons from bad writes, but introduces a moderation/promotion surface — real machinery — in the first slice.
- **C — Private-only for this slice.** Write to a per-user commons now; defer the shared-write policy entirely to a later DU. Thinnest to ship, but bakes in a *private* architecture that contradicts the "global resource" standing decision and would need re-plumbing to become shared.

## Decision Outcome

Chosen: **A — direct auth-gated write to the shared commons**, because it is the only option that honors the "commons is a global resource" standing decision while staying within the thin-slice discipline ("the commons ships thin"). The hard part of §13 #4 — spam control, trust tiers, rate limits, moderation — is **genuinely additive**: it can be layered on without re-architecting the write path, so deferring it costs nothing structural. B builds moderation machinery the first slice does not need; C builds the wrong (private) architecture and merely postpones the same decision while undercutting reuse (US2) and the commons value proposition.

**Scope of this decision:** it resolves §13 #4 **only to the extent slice 001 requires** — the *entry mechanism* (records write directly to the shared commons, deduped by the merge rules, gated to signed-in users). The broader commons **governance** policy (who may write at scale, abuse controls, trust tiers, moderation/promotion) remains open and reserved to Ben; a future ADR will settle it before multi-user scale. The constitution's "reserved decisions" list is **not** amended here, since #4 is only partially resolved.

### Consequences

- Good: the commons genuinely accumulates from slice 001 (reuse/US2 works across the shared store); no throwaway private-then-shared re-plumbing; no premature moderation surface.
- Good: matches the data-spine merge design already written (`docs/data/poi-site.md`) — dedupe-on-write is the existing merge, not new machinery.
- Bad / accepted cost: with open auth-gated writes there is no gate against a malicious signed-in writer polluting the shared commons. Accepted because (a) writes require Google SSO sign-in, (b) at M1 there is effectively one trusted writer, and (c) the merge never destroys data (a bad write is a recorded value/conflict, not an overwrite), so it is recoverable. The abuse-control policy is tracked as the still-open remainder of §13 #4.

### Confirmation

- **TODO (lands with Spec 001 implementation):** `test_commons_write_shared` — a researched record is readable from the shared commons by a *different* session/user (proves shared, not private); an unauthenticated write is rejected (proves auth-gated).
- **TODO (lands with Spec 001 implementation):** `test_commons_reuse_dedupe` — re-researching an overlapping area reuses existing records and creates **no** duplicate rows (proves dedupe-on-write via the merge rules), the deterministic backbone of US2 (reuse + refresh).
- The broader §13 #4 governance policy is **out of scope** for this Confirmation and will carry its own ADR + evals when multi-user scale is scheduled.
