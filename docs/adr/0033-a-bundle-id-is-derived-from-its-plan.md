# 0033 — A bundle id is derived from its plan, and there is no bundle table

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-14 · accepted: 2026-08-14

## Context and Problem Statement

`contracts/bundles.md` specifies three endpoints keyed on a `bundle_id`: the compile stream returns one, and `GET /bundles/{bundle_id}/manifest` and `GET /bundles/{bundle_id}/artifacts/{path}` are read on it afterwards. It shows ids of the form `bnd_01J…` and says nothing about how they are minted. `data-model.md` §9 G12 records the format as **unstated**, and the slice's data model adds `user_plan` but **no bundle table** — the manifest is an object in the store, not a row.

That leaves an unavoidable question at the first read endpoint. `GET /bundles/{bundle_id}/manifest` must enforce the same row scope as everything else: *another user's bundle is a `404`, never a `403`*, and byte-identical to the unknown-id response. With no bundle table there is nothing to join a bare, opaque `bundle_id` against, so the request cannot establish who owns it. An id with no relation to anything is unauthorizable.

The options are therefore about **where ownership lives**, not about id aesthetics.

## Considered Options

- **A — Derive the id from the plan: `bnd_<plan_id.hex>`.** Ownership travels in the id; the scope check is the ordinary `load_plan(plan_id, user_id)` every other route already performs, and an unknown or foreign id fails it exactly as an unknown or foreign plan does.
- **B — Add a `bundle` table** (`bundle_id` PK, `plan_id` FK, `user_id`, `created_at`, `size_bytes`). The general answer, and the one a second bundle per plan would need. Requires an Alembic migration, which is **ask-gated** (ADR-0005 / CLAUDE.md) and is a schema commitment made to serve a question the slice does not yet ask.
- **C — Random opaque id, ownership inferred from the object store prefix.** Makes the store the authority on scope. Rejected outright: authorization would depend on a `list`/`stat` against GCS, so a store outage becomes an authorization failure, and a bucket misconfiguration becomes an authorization *bypass*. Scope belongs in the database.
- **D — Random id recorded on the `user_plan` row** (a `bundle_id` column). No new table, but still a migration, and it makes the plan row carry a pointer to an artifact whose lifecycle it does not own.

## Decision Outcome

Chosen: **A — `bundle_id` is `bnd_<plan_id.hex>`.**

It is the only option that answers ownership with no new schema, and it answers it through the mechanism already trusted everywhere else in the API rather than a second one invented for this route. The privacy boundary the contract cares about is then not a special case: a foreign `bundle_id` becomes a foreign `plan_id`, which `load_plan` already turns into the same `404` as an unknown one, byte for byte, with no extra code to get wrong.

**Deriving rather than storing also removes a failure mode B has.** With a bundle table, a compile that publishes objects and then fails to insert the row leaves an unreachable bundle; the id and the artifacts can disagree about existence. A derived id cannot: the manifest either is at the derived prefix or is not, and that single fact is the answer.

**This does not leak anything.** The `bundle_id` reveals the `plan_id`, but both are opaque UUIDs scoped to the same subject, and a caller who can name the bundle id is by construction the caller who could already name the plan. The `404`-not-`403` rule means a stranger learns nothing from either.

## Consequences

- **One plan, one bundle, one URL spelling.** Recompiling an approved plan replaces its bundle in place rather than minting a second. This is coherent with the HITL model rather than a limitation of it: an *edit* supersedes the plan and writes a **new row with a new id** (ADR-0023), so each approved revision already gets its own bundle. What cannot exist is two bundles of the *same* approved day, which is a thing nobody has asked for and which would raise a "which one is current" question the manifest does not answer.
- **A recompile is destructive to the previous artifacts.** Acceptable while a bundle is a pure function of an approved plan; if bundles ever become independently versioned or retained, that is option B and it is a migration.
- **The id format is now a URL contract.** Moving to a bundle table later changes the spelling of every bundle URL. Cheap today (no bundle is persisted anywhere, and no client has stored one); it gets expensive once a device has downloaded a bundle and remembers where it came from.
- `data-model.md` §9 G12 is closed by this ADR: the format is `bnd_` + the plan UUID's hex, lowercase, no dashes.
- No migration is required, so nothing here is ask-gated.

## Confirmation

Satisfied when `GET /bundles/{id}/manifest` and the artifact route both return `404` for an id belonging to another subject, byte-identical to the unknown-id response, with the owner's `200` demonstrated at the same URL as the positive control — and when no `bundle` table exists in `alembic/versions/`.
