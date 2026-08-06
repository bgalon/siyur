"""idempotence as a database invariant: unique keys on site_source / site_conflict

`commons/repository.py` deduped its two ledger tables in **Python** — it read the existing
rows, canonicalised each ``jsonb`` payload, and skipped what it had already seen. That made
"a refresh appends nothing" (T054) a property of one code path rather than of the data: two
sessions refreshing the same area both finish *reading* before either *writes*, so both find
the key absent and both append it. Only a constraint can close that window.

The keys hash their ``jsonb`` halves as ``md5(col::text)`` rather than indexing the columns
directly. The full rationale — btree's 2704-byte index-tuple limit, why ``jsonb``'s canonical
text is already key-order-independent, why not ``sha256``, and how this leaves exactly one
engine deciding JSON equality (FAIL-005 / ADR-0017) — lives with the expression itself in
``commons.db.jsonb_digest`` and is not restated here. ``observed_at`` stays a **plain** key
column: it is what keeps ``site_source`` genuinely append-only, since the same value observed
on a later date is a new observation and must still insert.

Two deliberate asymmetries between ``upgrade`` and ``downgrade``:

1. ``upgrade`` **deletes pre-existing duplicates** before creating the indexes — a migration
   that only works on an empty table is a trap, and any database written by the previous
   read-then-write path may legitimately hold them. Only exact duplicates under the very key
   the index is about to enforce are removed, keeping the lowest ``id`` of each group, so no
   distinct observation and no source ref can be lost. It is a no-op where there are none.
2. ``downgrade`` drops the indexes but **cannot resurrect those rows**. It does not try: the
   deleted rows were byte-identical restatements of rows that survive, so the reversal
   restores the *schema* exactly and the data up to duplicates that carried no information.
   This is the one thing this migration does not round-trip, stated rather than discovered.

The SQL below is written out per table rather than generated from a table name: a migration
is a historical record, and static statements are what make it reviewable (and keep dynamic
SQL construction out of a file that runs with DDL privileges).

Revision ID: 0003_dedupe_natural_keys
Revises: 0002_area
Create Date: 2026-08-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_dedupe_natural_keys"
down_revision: str | Sequence[str] | None = "0002_area"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``(table, index name, key expressions)``. The expressions are the literal SQL the index is
#: built from and must stay in step with ``commons.db.SITE_*_NATURAL_KEY``, which is what
#: ``commons/repository.py`` infers on in its ``ON CONFLICT`` clause.
KEYS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "site_source",
        "uq_site_source_observation",
        ("site_id", "field", "observed_at", "md5(source::text)", "md5(value::text)"),
    ),
    (
        "site_conflict",
        "uq_site_conflict_record",
        ("site_id", "field", "md5(candidates::text)", "resolution"),
    ),
)

#: One ``DELETE`` per ledger table, partitioned by exactly the key its index is about to
#: enforce and ordered by the primary key, so the surviving row is deterministic rather than
#: whichever one the heap happened to return first.
DEDUPE: tuple[str, ...] = (
    """
    DELETE FROM site_source WHERE id IN (
        SELECT id FROM (
            SELECT id, row_number() OVER (
                PARTITION BY site_id, field, observed_at,
                             md5(source::text), md5(value::text)
                ORDER BY id
            ) AS rn
            FROM site_source
        ) ranked WHERE rn > 1
    )
    """,
    """
    DELETE FROM site_conflict WHERE id IN (
        SELECT id FROM (
            SELECT id, row_number() OVER (
                PARTITION BY site_id, field, md5(candidates::text), resolution
                ORDER BY id
            ) AS rn
            FROM site_conflict
        ) ranked WHERE rn > 1
    )
    """,
)


def upgrade() -> None:
    """Collapse any pre-existing duplicates, then make duplicates impossible."""
    for statement in DEDUPE:
        op.execute(statement)
    for table, name, key in KEYS:
        op.create_index(name, table, [sa.text(part) for part in key], unique=True)


def downgrade() -> None:
    """Drop the unique indexes (the collapsed duplicates are not restored — see docstring)."""
    for table, name, _key in reversed(KEYS):
        op.drop_index(name, table_name=table)
