"""user_plan (the HITL gate) + the area's local frame

Two things, because they arrive together (T008/T009): the ``area`` columns that give every
planned time a calendar frame, and the ``user_plan`` table the approval pause lives in.

**The pause is a row state, not a suspended coroutine** (ADR-0023). That is why the gate
survives a restart: there is nothing in memory to lose. Compile claims a plan with a
conditional ``UPDATE … WHERE status='approved'`` in the same transaction that consumes it,
so a ``proposed`` plan is not refused by an ``if`` — it is unclaimable.

**The `approved_at` CHECK is an IMPLICATION and has been written wrong twice.** First as
``(status='approved') = (approved_at IS NOT NULL)``, which rejects ``compiling``/``compiled``
outright — an approved plan violates its own constraint the moment the state machine
advances it. Then widened to the post-approval set but still a biconditional, which rejects
a ``superseded`` row that keeps ``approved_at`` as history, making "edit an approved plan"
impossible at runtime — the exact transition ADR-0023's Confirmation (d) and T026 assert.
An implication says *post-approval states require a timestamp*; a biconditional additionally
says *only those states may have one*, which is the false half. Copied from
``specs/002-plan-compile-offline/data-model.md`` §6 rather than re-derived.

**The `area` columns are NULLable on purpose.** Eight rows already exist, so a ``NOT NULL``
add would fail against a live database; and ``NULL`` is a meaningful state — "frame
unresolved" — which ``commons/opening_hours.py`` turns into ``hours_unknown`` rather than a
guess. A later migration may tighten once every row is populated. The backfill below is
therefore **best-effort and never fatal**: a row it cannot resolve stays ``NULL`` and fails
closed downstream.

There is no ``downgrade``. ``alembic downgrade`` is denied outright in this repo
(``CLAUDE.md``); the body raises rather than pretending to be reversible.

Revision ID: 0005_user_plan
Revises: 0004_area_researched_at
Create Date: 2026-08-08

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_user_plan"
down_revision: str | Sequence[str] | None = "0004_area_researched_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

# Constraint names below are BARE, and the FKs carry none. commons/db.py's
# NAMING_CONVENTION templates them as "ck_%(table_name)s_%(constraint_name)s", so an
# already-prefixed name yields "ck_user_plan_ck_user_plan_status".
#
# And a jsonb server_default must be sa.text("'[]'::jsonb"): a bare Python string is
# re-quoted by SQLAlchemy into three single quotes, which Postgres rejects as invalid
# json. Both were caught by the first run of this migration failing and rolling back.

#: ADR-0023's lifecycle. The API exposes this vocabulary verbatim — no mapping layer,
#: because a DB→API translation table is a thing that drifts (`contracts/plans.md`).
_STATUSES = ("proposing", "proposed", "approved", "superseded", "compiling", "compiled", "failed")

#: The states at or beyond approval. Both approval CHECKs are written over this set, not
#: over the single `approved` state.
_POST_APPROVAL = "'approved','compiling','compiled'"


def upgrade() -> None:
    """Add the area frame columns, backfill them, then create ``user_plan``."""
    # ── 1. area: the local frame ──────────────────────────────────────────────────
    op.add_column("area", sa.Column("timezone", sa.Text(), nullable=True))
    op.add_column("area", sa.Column("country_code", sa.Text(), nullable=True))
    # Recommended by T008's implementer rather than declared on the SQLAlchemy model, so
    # the model cannot claim a constraint the database was never told about.
    op.create_check_constraint(
        "country_code_alpha2",
        "area",
        "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
    )

    _backfill_area_frame()

    # ── 2. user_plan: the HITL gate ───────────────────────────────────────────────
    op.create_table(
        "user_plan",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # The row-scope key. Every read filters on it — the PRD §13 #4 privacy boundary.
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("area_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("itinerary", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        # Self-FK: a superseded row points at its successor. `contracts/plans.md` returns
        # this on GET /plans/{id} and in the 409 body; ADR-0023 originally had no column
        # for it, so a superseded row could not name what replaced it.
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        # SHA-256 over the ItineraryV1 serialized per RFC 8785 (ADR-0025 A5) — what an
        # approval is *of*. "Canonical JSON" without a named standard is insufficient:
        # Python and JS disagree on non-ASCII escaping and on integral floats.
        sa.Column("itinerary_hash", sa.Text(), nullable=False),
        sa.Column("feasible", sa.Boolean(), nullable=False),
        sa.Column(
            "violations", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        # Set ONLY when feasibility runs — never from `updated_at`, which bumps on any
        # write and would report a time the check did not happen.
        sa.Column("feasibility_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["area_id"], ["area.id"]),
        sa.ForeignKeyConstraint(["superseded_by"], ["user_plan.id"]),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")",
            name="status",
        ),
        # IMPLICATION, not a biconditional — see the module docstring. A `superseded` row
        # keeps its approval as history, and a biconditional rejects exactly that row.
        sa.CheckConstraint(
            f"status NOT IN ({_POST_APPROVAL}) OR approved_at IS NOT NULL",
            name="approved_at",
        ),
        # An infeasible plan is unapprovable in the DATABASE, not merely in code (FR-005).
        sa.CheckConstraint(
            f"status NOT IN ({_POST_APPROVAL}) OR feasible",
            name="feasible",
        ),
        # These two genuinely move together, so this one IS a biconditional.
        sa.CheckConstraint(
            "(approved_at IS NULL) = (approved_by IS NULL)",
            name="approver",
        ),
        sa.CheckConstraint(
            "(status = 'superseded') = (superseded_by IS NOT NULL)",
            name="superseded",
        ),
    )
    # Mirrors ix_user_note_user_id_site_id: the scoped list read, and the status filter
    # compile uses to find claimable plans.
    op.create_index("ix_user_plan_user_id_area_id", "user_plan", ["user_id", "area_id"])
    op.create_index("ix_user_plan_user_id_status", "user_plan", ["user_id", "status"])


def _backfill_area_frame() -> None:
    """Derive ``timezone``/``country_code`` for rows that predate the columns.

    **Best-effort by design.** A migration that imports application code is normally a
    smell — the code moves, the migration is replayed years later, and it breaks. Here the
    alternative is worse: leaving every existing area frameless means every `PH` rule over
    them evaluates wrongly, and that failure is silent and points toward "open".

    So the import is guarded and every failure is tolerated: an unresolvable row (open
    ocean, a geometry the resolver refuses) simply keeps ``NULL``, which is a *meaningful*
    state that fails closed downstream. This migration cannot fail because of a backfill.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, ST_AsText(polygon) AS wkt FROM area")).fetchall()
    if not rows:
        return

    try:
        from shapely import from_wkt

        from commons.frame import resolve_frame
    except Exception as exc:  # noqa: BLE001 - see docstring; never fatal
        _log.warning("area frame backfill skipped (%s); %d rows stay NULL", exc, len(rows))
        return

    resolved = 0
    for row in rows:
        try:
            frame = resolve_frame(from_wkt(row.wkt))
        except Exception as exc:  # noqa: BLE001 - an unresolvable area is not an error here
            _log.warning("area %s frame unresolved (%s); stays NULL", row.id, exc)
            continue
        bind.execute(
            sa.text("UPDATE area SET timezone = :tz, country_code = :cc WHERE id = :id"),
            {"tz": frame.timezone, "cc": frame.country_code, "id": row.id},
        )
        resolved += 1
    _log.info("area frame backfill: %d of %d rows resolved", resolved, len(rows))


def downgrade() -> None:
    """Not supported — ``alembic downgrade`` is denied outright in this repo."""
    raise NotImplementedError(
        "0005_user_plan is forward-only: `alembic downgrade` is denied in this repo "
        "(CLAUDE.md). Dropping user_plan would discard every approval decision, and "
        "dropping area.timezone/country_code would silently re-open the PH failure that "
        "reports a closed place as open."
    )
