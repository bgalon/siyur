"""idempotence as a database invariant: unique keys on site_source / site_conflict

`commons/repository.py` deduped its two ledger tables in **Python** — it read the existing
rows, canonicalised each ``jsonb`` payload with ``json.dumps(sort_keys=True)``, and skipped
what it had already seen. That made "a refresh appends nothing" (T054) a property of one
code path rather than of the data: two sessions refreshing the same area both finish
*reading* before either *writes*, so both find the key absent and both append it. Only a
constraint can close that window, and this migration adds it.

**Why the keys are hashed rather than indexed directly.** A btree index tuple is capped at
2704 bytes, and a plain composite unique index over a ``jsonb`` column does not merely index
poorly — it makes the ``INSERT`` *fail*::

    ERROR:  index row size 3024 exceeds btree version 4 maximum 2704 for index "..."
    HINT:   Consider a function index of an MD5 hash of the value, ...

``site_conflict.candidates`` is an array of fully stamped ``SourcedValue``s and reaches
kilobytes on a well-sourced field, so the direct index would reject legitimate writes: the
exact failure the constraint exists to prevent, inverted. This takes Postgres's own advice.

**Which equality the hash expresses.** ``jsonb`` parses to a normalised binary form (keys
sorted, duplicates dropped, whitespace gone), so ``jsonb_out`` is already a canonical
serialisation and ``md5(col::text)`` is order-independent natively — ``{"a":1,"b":2}`` and
``{"b":2,"a":1}`` collide, which is the property the Python ``sort_keys=True`` form used to
provide. It is deliberately **not** byte-equal to that Python form (Postgres orders keys by
length-then-bytes and emits ``{"a": 1}`` with a space), and no longer needs to be: the
repository stopped canonicalising anything, so exactly one engine now decides whether two
payloads are the same. That is FAIL-005's lesson — the fix there was not to make two engines'
folds agree, it was to remove the need for them to agree.

``md5``/``jsonb_out`` are ``IMMUTABLE``, which an index expression requires; ``sha256`` is
not usable here because it takes ``bytea`` and ``convert_to`` is ``STABLE``. ``observed_at``
stays a **plain** key column — both because ``date_out`` is ``STABLE`` and, more importantly,
because it is what keeps ``site_source`` genuinely append-only: the same value observed on a
**later date** is a new observation and must still insert.

Two deliberate asymmetries between ``upgrade`` and ``downgrade``:

1. ``upgrade`` **deletes pre-existing duplicates** before creating the indexes — a migration
   that only works on an empty table is a trap, and any database written by the pre-existing
   read-then-write path may legitimately hold them. Only exact duplicates under the very key
   the index is about to enforce are removed, keeping the lowest ``id`` of each group, so no
   distinct observation and no source ref can be lost. It is idempotent and a no-op on a
   database that has none.
2. ``downgrade`` drops the indexes but **cannot resurrect those rows**. It does not try:
   the deleted rows were byte-identical restatements of rows that survive, so the reversal
   restores the *schema* exactly and the data only up to duplicates that carried no
   information. This is the one thing this migration does not round-trip, stated here rather
   than discovered later.

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

#: ``(table, index name, key expressions)`` — the expressions are the literal SQL the index
#: is built from, and must stay in step with ``commons.db.SITE_*_NATURAL_KEY``, which is what
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


def _drop_duplicates(table: str, key: tuple[str, ...]) -> None:
    """Delete every row but the lowest-``id`` one of each group equal under ``key``.

    Ordered by the primary key, so the survivor is deterministic rather than
    whichever row the heap happened to return first.
    """
    op.execute(
        sa.text(
            f"""
            DELETE FROM {table}
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY {", ".join(key)}
                               ORDER BY id
                           ) AS rn
                    FROM {table}
                ) ranked
                WHERE rn > 1
            )
            """  # noqa: S608 — `table`/`key` are module constants, never caller input.
        )
    )


def upgrade() -> None:
    """Collapse any pre-existing duplicates, then make duplicates impossible."""
    for table, name, key in KEYS:
        _drop_duplicates(table, key)
        op.create_index(name, table, [sa.text(part) for part in key], unique=True)


def downgrade() -> None:
    """Drop the unique indexes (the collapsed duplicates are not restored — see docstring)."""
    for table, name, _key in reversed(KEYS):
        op.drop_index(name, table_name=table)
