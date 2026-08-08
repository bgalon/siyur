# 0015 — A persisted `area` is private, row-scoped to `created_by` (and what would change that)

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-08-01 · accepted: 2026-08-07

## Context and Problem Statement

`POST /areas` now writes a durable row (`commons/db.py::Area`, `alembic/versions/0002_area.py`)
so that `POST /areas/{area_id}/research` can answer `404` on an unknown id across restarts and
processes. Persisting it forced a question the in-memory version never had to answer: **is a
delimited area commons data or personal data?**

The implementing session chose **private** — `api/areas.py::_load_area` filters
`Area.created_by == subject`, so another user's `area_id` is a `404`, never someone else's
polygon — and flagged it rather than settling it, because §13 #4 (commons write policy) is
Ben's. That was the right call, and this ADR records the choice and its alternatives without
pre-empting §13 #4.

**Why no existing rule decided it.** Three documents each stop just short:

- `api/AGENTS.md` states the privacy boundary **by table-name prefix**: "every `user_*` query is
  scoped to the authenticated subject; the commons (`site*`) is world-readable to any signed-in
  user." `area` matches neither prefix. The rule is silent on it by construction.
- `docs/data/area.md` § Persistence lists "whether an area is per-user or shared" as an open
  question and says outright: **"Unresolved — this card does not assert an answer."**
- **ADR-0008** resolved §13 #4 only for the *entry mechanism* of `site` records (direct
  auth-gated write to the shared commons) and explicitly left governance open. This is a
  different sub-question: not "who may write into the commons" but "**is the delimitation itself
  part of it**". ADR-0008 does not reach it.

The tension is real and it is not a matter of taste. A **name-resolved administrative division**
("Rhodes old town") is reference data that already exists in an ODbL/CDLA source and is
world-readable there; a **user-drawn ring** is a shape the user drew around something they care
about — potentially their own home — and `docs/data/area.md` already stamps it `kind="user"` /
`license="user-owned"` / `bundleable=false`, a class FR-010 refuses at the commons write
boundary altogether. Those two things are on opposite sides of the line and today share one
table with one policy.

## Considered Options

**A — Always private (shipped).** Every read of `area` filters on `created_by`. One rule, no
provenance reasoning, no way to leak a ring.

**B — Always shared.** `area` joins `site*` as world-readable to any signed-in user;
`created_by` degrades to an audit column. Areas become nameable across users.

**C — Split by provenance kind.** A polygon resolved from a source (Overture division /
Nominatim) is shared; a user-supplied `bbox` or `polygon` is private. The line follows the
`SourceRef.kind` the resolver already computes.

## Decision Outcome

Chosen: **A — always private**, as shipped, held as the **reversible default** rather than as
the answer to §13 #4.

**A is right for now for one reason worth stating precisely: it costs almost nothing today,
because reuse does not go through `area_id`.** Commons reuse is keyed on **geometry**, not on
the area row — `commons/repository.py::coverage` is `ST_Within(site.geom, :polygon)` and
`GET /sites` takes a bbox. A second user who re-delimits the same old town gets a *different*
`area_id` and **the same sites**. The commons value proposition is therefore untouched by this
choice; what A forecloses is narrower and enumerated below.

**B was rejected on the concrete harm, not on principle.** A shared `area` table publishes two
personal data items: the ring the user drew (the shape itself is the datum — "where I asked
about" can be a home, a hotel, a client's site) and `Area.name`, the free text they typed, which
is search-history-shaped. FR-010's refusal of `source.kind="user"` values at the commons
boundary already expresses the repo's position on the first of these; B would contradict it for
the one table where the rule was never written down.

**C is the shape the evidence points at, and it is the option this ADR most wants Ben to look
at — but it is not implementable on today's schema.** The `Area` row stores `id`, `polygon`,
`name`, `created_by`, `created_at` and **no provenance at all**: the `SourceRef` that
`resolve_area` computed for the winning polygon is used to build the response and then dropped.
`name IS NOT NULL` is a weak proxy — it does not distinguish "Use this view" from a drawn ring,
and it does not record *which* division answered. So C costs a migration plus carrying the
resolver's `SourceRef` into the row (a question `docs/data/area.md` already lists as open:
"whether the `SourceRef` is stored inline or in a provenance row").

**The one-way door, stated plainly:** provenance for C has to be captured **at write time or not
at all**. Rows accumulated without it cannot be classified afterwards — re-running the resolver
against a later Overture release is not the same answer, and nothing recovers whether a bbox
came from the viewport button or from a lasso. If Ben expects to land on C, persisting the
`SourceRef` on the area row is worth doing *before* there is data to backfill; that is a
schema addition A does not need but C requires, and it decides nothing on its own.

> **Accepted 2026-08-07: keep the door open.** The resolver's `SourceRef` **is** to be persisted on
> the `area` row, landing in the **Spec 002 T009 migration** — the migration already touching
> `area` to add `timezone` and `country_code` (ADR-0025 ruling 2) — so it costs one migration
> instead of two, and lands while the table is still effectively empty.
>
> This is **not** a decision to adopt option C, and it does not resolve PRD §13 #4. Policy stays
> **A — always private**. What it buys is that C remains *available*: the column records which
> division answered, or that no source did, at the only moment that information exists. If Ben
> later settles on A or B permanently, the column is a small piece of honest provenance on a row
> that has none; if he settles on C, it is the difference between a policy change and an
> unrecoverable one.
>
> **Owed by T009's author:** the column is written on **every** insert path, including the
> user-supplied `bbox`/`polygon` path — where the honest value records that the geometry came from
> the user (`kind="user"`), not `NULL`. A nullable column filled only on the division path
> reproduces the exact ambiguity it exists to remove, because "no source" and "not recorded" become
> indistinguishable.

**This ADR does not resolve §13 #4.** It records the slice-001 posture for one table and names
what would change it. The reserved-decisions list is not amended.

### Consequences

- Good: no read of `area` can return another user's polygon or their typed name; the strictest
  option is the default, and the direction of travel (private → shared) is the safe one to
  loosen later. The reverse is not.
- Good: consistent with `user_note`, which the migration comment already cites as the model
  ("An area is private, like `user_note`").
- Good: costs nothing measurable today, because reuse is geometric (see above).
- Bad / accepted cost — **areas are not linkable.** "Research the area I researched" cannot be
  expressed; a shared itinerary cannot reference an `area_id`; a "what has this community already
  researched" gallery has no table to read.
- Bad / accepted cost — **it forecloses the obvious fix for the slow name path.** `POST /areas`
  by name scans the hosted divisions theme with no bbox pushdown and can hang for minutes
  (`docs/TRY-IT.md`); a shared `area` table is the natural cache for a name→polygon resolution,
  and under A each user pays the full scan again. Not a reason to change the policy, but a real
  cost that belongs on the ledger rather than discovered later.
- Accepted: the private/shared line for `area` currently lives in code and in a migration
  comment, and `docs/data/area.md` still says the question is open. That card should be updated
  to *record* A (and to drop its stale "nothing here is shipped yet" framing, since
  `commons/db.py::Area` and `0002_area.py` have shipped) — **not this session's file.**

### Confirmation

- **`tests/test_api_research.py::test_another_users_area_is_404_not_a_borrowed_polygon`** — the
  decision's enforcement: a second subject's `area_id` is indistinguishable from a missing one.
- **`tests/test_api_research.py::test_an_unknown_area_id_is_404`** — the scope filter cannot be
  satisfied by simply never finding anything.
- No confirmation is claimed for the *general* §13 #4 policy; it remains open and will carry its
  own ADR.

### Revisit trigger — the second human, not a date

Revisit when **two distinct auth subjects exist in one deployment**, or earlier if any of these
lands first, because each one forces the question on its own:

1. a feature that must name an area **across** users (a shared itinerary referencing `area_id`,
   a researched-areas gallery, a hand-off link);
2. an **area-level cache** for the name-resolution path (the cost above becomes the motive);
3. Ben resolving §13 #4, which may settle it directly.

Until one of those exists there is exactly one writer and one reader, and A is unobservable.
When one does, **do not default to flipping the whole table to B** — re-open option C first,
because the evidence (`kind="user"` ⇒ `bundleable=false`, FR-010's refusal) says the line runs
*through* the table, not around it.
