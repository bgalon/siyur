"""area.researched_at: record that a pass actually happened, not just that a polygon existed

`commons/repository.py::coverage` answered "is this area covered?" with ``ST_Within`` site
count ``> 0``. That is a different question — "do we hold a site anywhere inside this
polygon" — and it misfires exactly the way ADR-0018 predicted: pan the map out one step, the
enlarged viewport still contains the sites researched inside it, and a region nobody has ever
researched reports ``covered: true``, so the client reuses instead of researching (FR-006).

Answering the real question needs state that did not exist. ``area`` recorded *delimitation*
(`0002_area`) and nothing recorded *completion*, so this migration adds the one column that
does: ``researched_at``, written by `api/areas.py` after a research stream commits.

Three deliberate choices:

1. **Nullable, no backfill.** Whether an existing row was ever researched is genuinely
   unrecoverable — nothing in the schema distinguishes "delimited and researched" from
   "delimited and abandoned", and re-deriving it from site counts would rebuild the very
   proxy this change removes. Existing rows therefore read as *not researched*, so the first
   ask over an old area runs a pass that may be redundant. That is the safe direction: this
   whole change exists because the failure of over-*reporting* coverage is silent, while the
   failure of over-researching is visible, idempotent (``upsert_sites`` enriches, never
   forks) and costs only adapter fan-out.
2. **A timestamp, not a boolean.** ``NULL``/not-``NULL`` already carries the boolean, and the
   moment is what a later staleness policy would need; a boolean would have to be migrated
   again to get it.
3. **A GiST index on ``polygon``.** `0002_area` said "no spatial index: this table is only
   ever read by primary key" — true then, false now. Coverage unions the caller's researched
   polygons that intersect the requested one, which is a spatial predicate over a table that
   grows with every delimitation. The index is what keeps that from degrading into a full
   scan of every area ever drawn.

Both halves reverse exactly: dropping the column and the index restores `0002_area`'s schema,
and no row is deleted or rewritten, so the downgrade loses only the completion timestamps —
which is the information the column was added to hold, and nothing else.

Revision ID: 0004_area_researched_at
Revises: 0003_dedupe_natural_keys
Create Date: 2026-08-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_area_researched_at"
down_revision: str | Sequence[str] | None = "0003_dedupe_natural_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``area.researched_at`` and the GiST index coverage's new read needs."""
    op.add_column(
        "area",
        sa.Column("researched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_area_polygon", "area", ["polygon"], postgresql_using="gist")


def downgrade() -> None:
    """Back to `0002_area`'s shape: no completion state, no spatial index."""
    op.drop_index("ix_area_polygon", table_name="area", postgresql_using="gist")
    op.drop_column("area", "researched_at")
