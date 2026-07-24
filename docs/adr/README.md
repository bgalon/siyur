# Architecture Decision Records

Every session that chose between libraries, schemas, or architectures ends with `/adr`. Format: **MADR 4.0 minimal**. Filename: `NNNN-kebab-title.md` (zero-padded, sequential). Status: `proposed` → `accepted` | `superseded by NNNN`.

The **Confirmation** section is required — it names the eval or CI check that verifies the decision is actually implemented and holding (the course harvests this).

## Template

```markdown
# NNNN — <decision title>

- Status: proposed | accepted | superseded by NNNN
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: <name> · Date: YYYY-MM-DD

## Context and Problem Statement
<what forced a decision; link the PRD/spec clause or failure that triggered it>

## Considered Options
- Option A — …
- Option B — …

## Decision Outcome
Chosen: **<option>**, because <driver>.

### Consequences
- Good: …
- Bad / accepted cost: …

### Confirmation
<how we verify it's implemented and holding: eval id / CI job / test path>
```

ADR-0001 (written at ramp-up) = "Adopt the ramp-up standard." A likely early one from this bootstrap: "Split the ramp-up — governance-first."
