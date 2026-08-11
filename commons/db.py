"""Commons persistence — the PostGIS schema (data-model §4, tech-design §2), T014.

Six tables, one privacy boundary:

- ``area`` — a user's delimitation, resolved to one polygon (`api/areas.py`, T037). It is
  what makes ``POST /areas/{area_id}/research`` able to answer ``404`` on an unknown id
  honestly, and it is **per-user**: ``created_by`` is the auth subject and every read of
  this table filters on it, exactly like :class:`UserNote`. An area is a personal
  delimitation, not commons data — the *sites* a pass finds are shared, the "where I asked
  about" is not. ``researched_at`` records that a pass over the polygon actually completed,
  which is what :func:`~commons.repository.coverage` reads to answer "was this looked at"
  instead of "does this contain a site" (ADR-0018).
- ``site`` — one row per real-world place. ``fields`` (jsonb) holds the ``SourcedValue``
  map (names / categories / address / opening_hours); ``geom`` is a **GiST-indexed**
  ``geometry(Point,4326)`` that mirrors ``location.value`` — the model's ``location`` is
  the single source of truth, ``geom`` is its queryable projection, and the two are kept
  consistent through :func:`to_db_point` / :func:`from_db_point` so PostGIS/shapely (never
  an LLM) own the coordinates (FR-005). The coverage query is
  ``SELECT … FROM site WHERE ST_Within(geom, :area_polygon)``.
- ``site_source`` — **append-only** provenance audit; one row per (site, field, source)
  observation. Never updated, never deleted: a merge can be re-run with a better policy
  without losing a source, and the UI can show *why* a value is what it is. The FK is
  deliberately plain ``NO ACTION`` (no cascade) — the database refuses to drop a site out
  from under its own audit trail.
- ``site_conflict`` — recorded disagreements (``candidates`` jsonb + ``resolution``).
- ``user_note`` — **private, row-scoped to ``user_id``**. Validation rule 7 (FR-010): no
  ``source.kind="user"`` value ever reaches ``site``/``site_source``, and no commons read
  joins this table. It is a sibling of the commons, never a part of it.
- ``user_plan`` — the planned day **and the HITL pause** (ADR-0023), private and row-scoped
  to ``user_id`` exactly like ``user_note``. The approval is a row state rather than a
  waiting coroutine, which is what makes it survive a restart; see :class:`UserPlan`.

The DB URL comes from the ``SIYUR_DATABASE_URL`` process environment variable only — never
a file, never ``.env*`` (AGENTS.md), matching ``alembic/env.py``.

**Idempotence is a database invariant, not a code path.** ``site_source`` and
``site_conflict`` each carry a unique index over their natural key
(:data:`SITE_SOURCE_NATURAL_KEY` / :data:`SITE_CONFLICT_NATURAL_KEY`), so a re-run of a
research pass cannot duplicate a row *even when two sessions upsert the same area
concurrently* — which read-then-write dedupe in Python structurally cannot prevent, because
both readers finish before either writer starts. See :func:`jsonb_digest` for why the
``jsonb`` halves of those keys are compared through ``md5(col::text)`` rather than directly.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any, Final, Literal, get_args
from uuid import UUID, uuid4

from geoalchemy2 import Geometry, WKBElement
from geoalchemy2.shape import from_shape, to_shape
from pydantic import JsonValue
from shapely import Point
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ColumnElement,
    Date,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SQLColumnExpression,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUuid
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.sql.roles import DDLConstraintColumnRole

from commons.geo import validate_point

__all__ = [
    "DATABASE_URL_ENV",
    "PLAN_STATUSES",
    "POST_APPROVAL_STATUSES",
    "SITE_CONFLICT_NATURAL_KEY",
    "SITE_SOURCE_NATURAL_KEY",
    "SRID",
    "Area",
    "Base",
    "PlanStatus",
    "Site",
    "SiteConflict",
    "SiteSource",
    "UserNote",
    "UserPlan",
    "create_db_engine",
    "create_session_factory",
    "database_url",
    "from_db_point",
    "jsonb_digest",
    "metadata",
    "to_db_point",
]

#: The one CRS the commons stores geometry in — EPSG:4326, (lon, lat).
SRID: Final[int] = 4326

#: The only place a DB URL may come from (never ``alembic.ini``, never ``.env*``).
DATABASE_URL_ENV: Final[str] = "SIYUR_DATABASE_URL"

# Deterministic constraint/index names, so Alembic autogenerate and hand-written
# migrations agree on what to name (and therefore how to drop) things.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base; ``Base.metadata`` is what ``alembic/env.py`` diffs against."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


#: Imported lazily by ``alembic/env.py`` as ``target_metadata``.
metadata = Base.metadata


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Area(Base):
    """A resolved user delimitation — server-side area state (`contracts/areas.md`).

    ``POST /areas`` writes one row; ``POST /areas/{area_id}/research`` reads it back, so an
    unknown id is a real ``404`` across processes and restarts rather than a claim that only
    holds until the server is redeployed.

    The column is ``geometry(Geometry,4326)``, **not** ``geometry(Polygon,4326)``:
    :func:`~planner.nodes.resolve_area.resolve_area` legitimately returns a ``MultiPolygon``
    (an island group, a divided municipality, a user-drawn multi-part ring), and a
    ``Polygon``-typed column rejects one outright. The areal check lives where the geometry
    is built — ``resolve_area._validate_area`` refuses points, lines, empty and
    self-intersecting rings before anything reaches here — so widening the column loses no
    guarantee and buys genericity (FR-001).

    **``researched_at`` is what makes "covered" answerable.** Delimiting an area records the
    polygon; *researching* it is a separate event, and until 0004 nothing recorded that the
    second one happened. :func:`~commons.repository.coverage` needs it: without a researched
    extent the only available proxy was "does this polygon contain a site", which reports a
    region nobody has ever looked at as covered the moment one known site drifts inside it
    (ADR-0018). The rows this table already holds are therefore genuinely unknowable — they
    stay ``NULL``, i.e. *not researched*, which over-researches rather than silently skipping.

    This column also makes the table load-bearing for a **read**, which it was not before, so
    ``polygon`` now carries a GiST index. ADR-0015's per-user scoping is unchanged: coverage
    unions only the *caller's* researched areas.

    **``timezone`` / ``country_code`` are the area's local frame** (ADR-0025 ruling 2,
    ADR-0029) — columns rather than a derivation at read time, because a plan must stay
    readable in the frame it was planned in even after the boundary data or the derivation
    rule changes. They are written from :func:`~commons.frame.resolve_frame` at resolve time
    and never recomputed on a read.

    **They are nullable, and `docs/data/area.md` sketches them as ``NOT NULL``.** The card is
    describing the end state; this is the shape the transition has to pass through. Rows
    predate the columns — 8 of them in the live database — so the migration (T009) adds the
    columns nullable, backfills each row from its own ``polygon`` by the same derivation, and
    only then could tighten. Until then ``None`` means **unresolved**, and unresolved must
    reach the traveller as ``hours_unknown`` (`commons/opening_hours.py` refuses a
    ``PH``-bearing expression with no country): a wrong country is invisible, so the honest
    absence has to outrank any default that would make the row *look* complete.

    Worth adding in T009 and deliberately not declared here: a
    ``CHECK (country_code ~ '^[A-Z]{2}$')``. It is the cheap database-level catch for the one
    corruption ADR-0029 names by hand — Natural Earth's ``-99`` sentinel reaching a row. It is
    left to the migration rather than to ``__table_args__`` so the model cannot claim a
    constraint the database has not been told about.
    """

    __tablename__ = "area"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    #: EPSG:4326 ``Polygon``/``MultiPolygon``, stamped and validated by ``resolve_area``.
    polygon: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=SRID, spatial_index=False), nullable=False
    )
    #: The free-text name the user asked for, when they asked by name; ``None`` otherwise.
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The auth subject (``SessionUser.sub``). Every read of this table filters on it.
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    #: When a research pass over this polygon last **committed**; ``None`` = never researched.
    #: Set by `api/areas.py` after the stream completes, so a pass that failed or was
    #: abandoned mid-stream leaves the area honestly unresearched.
    researched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    #: IANA timezone id (``Europe/Athens``) — the wall clock every planned time on this area
    #: is read in. Derived from ``polygon`` at resolve time by
    #: :func:`~commons.frame.resolve_frame` and **stored**, so a plan stays readable in the
    #: frame it was planned in even after the boundary data or the rule changes.
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    #: ISO 3166-1 alpha-2 (``GR``) — the public-holiday calendar ``PH`` resolves against
    #: (`commons/opening_hours.py`). Derived from ``polygon`` by the same rule, never
    #: model-supplied and never asked of the user.
    country_code: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    __table_args__ = (Index("ix_area_polygon", "polygon", postgresql_using="gist"),)


class Site(Base):
    """A commons place. ``fields`` is the ``SourcedValue`` map; ``geom`` mirrors it."""

    __tablename__ = "site"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    #: Overture GERS id — the cross-source join key when present (indexed for merge joins).
    gers_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    # spatial_index=False: the GiST index is declared explicitly below so it is visible in
    # the migration rather than conjured by a GeoAlchemy2 DDL side effect.
    geom: Mapped[WKBElement] = mapped_column(
        Geometry(geometry_type="POINT", srid=SRID, spatial_index=False), nullable=False
    )
    #: The ``SourcedValue`` map — names / categories / address / opening_hours.
    #: **Never** user notes: those are quarantined in :class:`UserNote` (FR-010).
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_site_geom", "geom", postgresql_using="gist"),)


class SiteSource(Base):
    """Append-only provenance: every observation of every field, never overwritten.

    "Append-only" is bounded by :data:`SITE_SOURCE_NATURAL_KEY`: re-observing the *same*
    value from the *same* source on the *same* date is not new information and is refused
    by the database, while the same value observed on a **different** date is a genuinely
    new observation and still inserts. ``observed_at`` is part of the key precisely so that
    the constraint cannot turn a legitimate second observation into an error.
    """

    __tablename__ = "site_source"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    site_id: Mapped[UUID] = mapped_column(
        PgUuid(as_uuid=True), ForeignKey("site.id"), nullable=False, index=True
    )
    #: The ``SiteRecordV1`` field this observation is about (``names.en``, ``location``, …).
    field: Mapped[str] = mapped_column(Text, nullable=False)
    #: The serialised ``SourceRef`` — always a JSON *object*.
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: The observed value, as stored inside its ``SourcedValue``.
    #:
    #: **Any** JSON value, not an object: ``SourcedValue[T]`` is generic, so a ``names.en``
    #: observation stores the bare string ``"Palace of the Grand Master"`` while a
    #: ``location`` observation stores a GeoJSON ``{"type": "Point", …}`` mapping, and a
    #: future ``SourcedValue[float]`` would store a number. Typing this ``dict[str, Any]``
    #: only ever type-checked because ``model_dump()`` returns ``dict[str, Any]`` and the
    #: subscript that feeds it is therefore ``Any``; a caller holding a typed ``str`` would
    #: have been rejected outright.
    value: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[date] = mapped_column(Date, nullable=False)


class SiteConflict(Base):
    """A recorded disagreement — merge never discards a source (data-model §5.4)."""

    __tablename__ = "site_conflict"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    site_id: Mapped[UUID] = mapped_column(
        PgUuid(as_uuid=True), ForeignKey("site.id"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(Text, nullable=False)
    #: The disagreeing ``SourcedValue``s, each still fully stamped.
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    #: ``"unresolved"`` | ``"picked:<source.id>"`` | ``"user-override"``.
    resolution: Mapped[str] = mapped_column(Text, nullable=False)


class UserNote(Base):
    """A private note. Row-scoped to ``user_id``; **never** joined into a commons read."""

    __tablename__ = "user_note"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    #: The auth subject (ADR-0008). Every read of this table filters on it.
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    site_id: Mapped[UUID] = mapped_column(
        PgUuid(as_uuid=True), ForeignKey("site.id"), nullable=False
    )
    #: The serialised ``SourcedValue`` with ``source.kind="user"``.
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_user_note_user_id_site_id", "user_id", "site_id"),)


#: ADR-0023's lifecycle, in transition order. The API exposes this vocabulary **verbatim**
#: (`contracts/plans.md`) — a DB→API translation table is a thing that drifts, and hiding
#: ``compiling`` would leave the UI unable to tell "approved, idle" from "compile running".
PlanStatus = Literal[
    "proposing", "proposed", "approved", "superseded", "compiling", "compiled", "failed"
]

#: One definition, derived — so the `CHECK` below and any ``match`` over the enum cannot
#: disagree about which states exist.
PLAN_STATUSES: Final[tuple[PlanStatus, ...]] = get_args(PlanStatus)

#: The states at or beyond approval. Both approval `CHECK`s are written over this **set**,
#: never over the single ``approved`` state — see :class:`UserPlan`.
POST_APPROVAL_STATUSES: Final[tuple[PlanStatus, ...]] = ("approved", "compiling", "compiled")


def _quoted(statuses: tuple[PlanStatus, ...]) -> str:
    return ", ".join(f"'{status}'" for status in statuses)


class UserPlan(Base):
    """A planned day and its approval state — **private, row-scoped to ``user_id``**.

    The sibling of :class:`UserNote`, one level up: an itinerary is personal data, so it
    lives here and never touches ``site``/``site_source``/``site_conflict`` (PRD §13 #4,
    data-model §6). Every read of this table filters on ``user_id``; another subject's plan
    must be **indistinguishable from a missing one**, which is why
    `commons/repository.py`'s reads take the subject as a keyword rather than offering an
    unscoped overload someone could reach for.

    **The pause is this row's ``status``, not a suspended coroutine** (ADR-0023). That is
    the whole reason an approval survives a restart: there is nothing in memory to lose. It
    also means compile's precondition is a ``WHERE`` clause in the transaction that consumes
    it — a ``proposed`` plan is not refused by an ``if``, it is *unclaimable*.

    **Both approval `CHECK`s are implications over :data:`POST_APPROVAL_STATUSES`, and the
    obvious biconditional has been written wrong twice.** ``(status='approved') =
    (approved_at IS NOT NULL)`` rejects ``compiling``/``compiled`` outright; widening the
    left side but keeping the biconditional then rejects a ``superseded`` row that keeps
    ``approved_at`` as history — which makes "edit an approved plan" impossible at runtime,
    the exact transition T026 asserts. An implication says post-approval states *require* a
    timestamp; it does not say only they may have one. ``(approved_at IS NULL) =
    (approved_by IS NULL)`` stays a biconditional because those two genuinely move together.

    Transcribed from ``alembic/versions/0005_user_plan.py``, which is **already applied to
    the live database** — the constraints are declared here so `alembic/env.py`'s
    autogenerate diff sees what 0005 created rather than proposing to add it again.
    """

    __tablename__ = "user_plan"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    #: The auth subject (``SessionUser.sub``). Every read of this table filters on it.
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    area_id: Mapped[UUID] = mapped_column(
        PgUuid(as_uuid=True), ForeignKey("area.id"), nullable=False
    )
    #: The serialised :class:`~commons.models.ItineraryV1`.
    itinerary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[PlanStatus] = mapped_column(Text, nullable=False)
    #: Incremented per edit — an edit writes a **new row**, never an update in place.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    #: The successor row when an edit replaced this one. Self-FK, set in the same
    #: transaction that writes ``revision+1``.
    superseded_by: Mapped[UUID | None] = mapped_column(
        PgUuid(as_uuid=True), ForeignKey("user_plan.id"), nullable=True, default=None
    )
    #: UTC, set at approval. **Never cleared** — a superseded row keeps it as history.
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    #: SHA-256 over the itinerary serialised per **RFC 8785 (JCS)** —
    #: :meth:`~commons.models.ItineraryV1.canonical_sha256`, never a second canonicaliser.
    itinerary_hash: Mapped[str] = mapped_column(Text, nullable=False)
    feasible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The **named** violations of FR-005; a JSON array of strings.
    violations: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    #: UTC; set **only** when feasibility runs — never from ``updated_at``, which bumps on
    #: any write and would report a time the check did not happen.
    feasibility_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Constraint names are BARE: NAMING_CONVENTION templates them as
    # "ck_%(table_name)s_%(constraint_name)s", so an already-prefixed name would yield
    # "ck_user_plan_ck_user_plan_status".
    __table_args__ = (
        CheckConstraint(f"status IN ({_quoted(PLAN_STATUSES)})", name="status"),
        CheckConstraint(
            f"status NOT IN ({_quoted(POST_APPROVAL_STATUSES)}) OR approved_at IS NOT NULL",
            name="approved_at",
        ),
        # An infeasible plan is unapprovable in the DATABASE, not merely in code (FR-005).
        CheckConstraint(
            f"status NOT IN ({_quoted(POST_APPROVAL_STATUSES)}) OR feasible", name="feasible"
        ),
        CheckConstraint("(approved_at IS NULL) = (approved_by IS NULL)", name="approver"),
        CheckConstraint("(status = 'superseded') = (superseded_by IS NOT NULL)", name="superseded"),
        Index("ix_user_plan_user_id_area_id", "user_id", "area_id"),
        Index("ix_user_plan_user_id_status", "user_id", "status"),
    )


# ── the idempotence invariant (T054, enforced in the database) ─────────────────────


def jsonb_digest(column: SQLColumnExpression[Any]) -> ColumnElement[str]:
    """``md5`` of Postgres's own canonical text rendering of a ``jsonb`` value.

    This is the **one** definition of "the same JSON payload" in Siyur, and it lives in
    Postgres rather than in Python on purpose — the FAIL-005 lesson applied to jsonb: when
    two engines each hold a notion of equality, do not try to make them agree, remove the
    need for them to agree by folding in a single engine. `commons/repository.py` no longer
    canonicalises anything; it hands the payload to the database and lets the constraint
    below decide, so there is no second notion of equality left to drift. That is the first
    arm of ADR-0017's rule ("fold both operands in one engine, or derive both arms from one
    definition and assert them equal exhaustively") — the cheaper arm, available here
    because nothing needs to compare these payloads *outside* the database.

    Note this is **not** a text fold: no case, accent or Unicode normalisation happens, so it
    does not add a third arm to ADR-0017's ``normalize_name`` problem. It compares JSON
    structure, and ``jsonb`` has exactly one notion of that.

    Three questions this expression answers, in the order they bite:

    1. **Why hash instead of indexing the ``jsonb`` column directly?** A btree index tuple
       is capped at 2704 bytes and a plain composite unique index over ``jsonb`` *rejects
       the INSERT* once a payload exceeds it (``index row size N exceeds btree version 4
       maximum 2704``) — Postgres's own HINT for that error recommends exactly this md5
       function index. ``site_conflict.candidates`` is an array of fully stamped
       ``SourcedValue``s and reaches kilobytes on a well-sourced field, so the direct index
       would fail legitimate writes: the precise failure the constraint exists to prevent,
       inverted.
    2. **Is ``md5(col::text)`` order-independent?** Yes, and *natively* — ``jsonb`` parses
       to a normalised binary form (keys sorted, duplicates dropped, whitespace gone), so
       ``jsonb_out`` is already a canonical serialisation and ``{"a":1,"b":2}`` and
       ``{"b":2,"a":1}`` hash identically. That is the same order-independence the old
       Python ``json.dumps(sort_keys=True)`` provided; the two strings are *not* byte-equal
       (Postgres sorts keys by length-then-bytes and emits ``{"a": 1}`` with a space) and no
       longer need to be, because only Postgres computes this now.
    3. **Where does it differ from ``jsonb``'s own ``=``?** Only in numeric scale: ``1.0``
       and ``1.00`` are ``jsonb``-equal but render differently. Unreachable from this
       codebase — a Python float has a single ``repr``, so ``model_dump(mode="json")``
       cannot emit two spellings of one number — and the residual risk points the safe way
       (a spurious extra row, never a rejected legitimate write).

    ``md5`` and ``jsonb_out`` are ``IMMUTABLE``, which an index expression requires.
    ``sha256`` is not an option: it takes ``bytea``, and ``convert_to`` is ``STABLE``.
    Collisions are not an adversarial concern here — nothing user-controlled picks these
    payloads — and the cost of one would be a single skipped duplicate observation.
    """
    return func.md5(sql_cast(column, Text))


#: What makes one provenance observation unique (`commons/repository.py` §2).
#:
#: ``observed_at`` is a **plain column, deliberately**: it keeps ``site_source`` genuinely
#: append-only (the same value seen on a later date is a new row, not a constraint
#: violation), and ``date_out`` is ``STABLE`` so it could not be folded into the digest even
#: if we wanted to. ``site_id``/``field`` stay plain for the same reason ``jsonb_digest``
#: exists in reverse: they are small and bounded, so they cost nothing in the index tuple
#: and stay readable to anyone reading ``\d site_source``.
SITE_SOURCE_NATURAL_KEY: Final[tuple[DDLConstraintColumnRole, ...]] = (
    SiteSource.site_id,
    SiteSource.field,
    SiteSource.observed_at,
    jsonb_digest(SiteSource.source),
    jsonb_digest(SiteSource.value),
)

#: What makes one recorded disagreement unique. ``candidates`` is the unbounded half.
SITE_CONFLICT_NATURAL_KEY: Final[tuple[DDLConstraintColumnRole, ...]] = (
    SiteConflict.site_id,
    SiteConflict.field,
    jsonb_digest(SiteConflict.candidates),
    SiteConflict.resolution,
)

# Declared on the metadata (not only in the migration) so `alembic/env.py`'s autogenerate
# diff sees them and never proposes dropping what 0003 created.
Index("uq_site_source_observation", *SITE_SOURCE_NATURAL_KEY, unique=True)
Index("uq_site_conflict_record", *SITE_CONFLICT_NATURAL_KEY, unique=True)


def to_db_point(point: Point) -> WKBElement:
    """shapely ``Point`` → PostGIS EWKB (SRID 4326), validated **(lon, lat)** on the way in."""
    return from_shape(validate_point(point), srid=SRID)


def from_db_point(geom: WKBElement) -> Point:
    """PostGIS geometry → shapely ``Point``, re-validated: a swapped pair cannot survive."""
    return validate_point(to_shape(geom))


def database_url() -> str:
    """The DB URL from ``SIYUR_DATABASE_URL``; raises rather than guessing a default."""
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(
            f"{DATABASE_URL_ENV} is not set in the process environment. Siyur reads the DB "
            "URL from this variable only (never alembic.ini, never a .env* file — "
            "AGENTS.md). For the local docker-compose PostGIS service: export "
            f"{DATABASE_URL_ENV}='postgresql+psycopg://siyur:siyur@localhost:5432/siyur'"
        )
    return url


def create_db_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Engine for ``url`` (default: :func:`database_url`). psycopg 3 driver, pre-ping on."""
    return create_engine(url or database_url(), echo=echo, pool_pre_ping=True)


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Session factory bound to ``engine`` (default: a new :func:`create_db_engine`)."""
    return sessionmaker(bind=engine if engine is not None else create_db_engine())
