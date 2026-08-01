"""Commons persistence — the PostGIS schema (data-model §4, tech-design §2), T014.

Five tables, one privacy boundary:

- ``area`` — a user's delimitation, resolved to one polygon (`api/areas.py`, T037). It is
  what makes ``POST /areas/{area_id}/research`` able to answer ``404`` on an unknown id
  honestly, and it is **per-user**: ``created_by`` is the auth subject and every read of
  this table filters on it, exactly like :class:`UserNote`. An area is a personal
  delimitation, not commons data — the *sites* a pass finds are shared, the "where I asked
  about" is not.
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

The DB URL comes from the ``SIYUR_DATABASE_URL`` process environment variable only — never
a file, never ``.env*`` (AGENTS.md), matching ``alembic/env.py``.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from geoalchemy2 import Geometry, WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely import Point
from sqlalchemy import Date, DateTime, Engine, ForeignKey, Index, MetaData, Text, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUuid
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from commons.geo import validate_point

__all__ = [
    "DATABASE_URL_ENV",
    "SRID",
    "Area",
    "Base",
    "Site",
    "SiteConflict",
    "SiteSource",
    "UserNote",
    "create_db_engine",
    "create_session_factory",
    "database_url",
    "from_db_point",
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
    """Append-only provenance: every observation of every field, never overwritten."""

    __tablename__ = "site_source"

    id: Mapped[UUID] = mapped_column(PgUuid(as_uuid=True), primary_key=True, default=uuid4)
    site_id: Mapped[UUID] = mapped_column(
        PgUuid(as_uuid=True), ForeignKey("site.id"), nullable=False, index=True
    )
    #: The ``SiteRecordV1`` field this observation is about (``names.en``, ``location``, …).
    field: Mapped[str] = mapped_column(Text, nullable=False)
    #: The serialised ``SourceRef``.
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: The observed value, as stored inside its ``SourcedValue``.
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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
