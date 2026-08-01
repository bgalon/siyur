"""area: server-side state for a resolved user delimitation

Adds the one table `api/areas.py` needs to answer ``POST /areas/{area_id}/research`` with a
truthful ``404`` (T037/T038). Written by hand, like ``0001_commons_spine``, so the geometry
type and the per-user column are explicit and reviewable.

``polygon`` is ``geometry(Geometry,4326)`` rather than ``geometry(Polygon,4326)``: an area
resolved from Overture divisions or drawn by the user may legitimately be a
``MultiPolygon``, and a ``Polygon``-typed column would reject it at insert time. The
areal-validity check lives in ``planner/nodes/resolve_area._validate_area`` (no points, no
lines, no empty or self-intersecting rings), which every write to this table passes through.

No spatial index: this table is only ever read by primary key, scoped to ``created_by``.
The PostGIS extension is created by ``0001_commons_spine`` and is untouched here.

Revision ID: 0002_area
Revises: 0001_commons_spine
Create Date: 2026-08-01

"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_area"
down_revision: str | Sequence[str] | None = "0001_commons_spine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SRID = 4326


def upgrade() -> None:
    """Create the ``area`` table."""
    op.create_table(
        "area",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "polygon",
            # spatial_index=False: no GiST index — see the module docstring.
            geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=SRID, spatial_index=False),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=True),
        # The auth subject that delimited it. An area is private, like `user_note`.
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_area")),
    )


def downgrade() -> None:
    """Drop the ``area`` table. Nothing references it, so nothing else has to move."""
    op.drop_table("area")
