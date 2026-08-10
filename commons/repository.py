"""The commons repository — the only place merged records reach PostGIS (T030 / T049).

`commons/merge.py` deliberately stops in memory; this module is the other half: it takes
:class:`~commons.merge.MergeResult`s and lands them on ``site`` / ``site_source`` /
``site_conflict`` (`commons/db.py`, data-model §4), and it answers the two reads the API
contracts need — coverage (`contracts/areas.md`) and the bbox/polygon site read
(`contracts/sites.md`).

Five properties this module exists to guarantee — the first four about the commons, the
fifth about the personal table that shares this module's transaction discipline:

**1. Upsert, never re-insert (FR-006 / T054).** An incoming record is joined to the row it
already belongs to — ``gers_id`` when both sides carry one, otherwise the *existing* fuzzy
rule (:func:`~commons.merge.decide_match`, ε from :data:`~commons.merge.EPSILON_METERS`)
against candidates PostGIS returns within ε. The predicate is **not re-implemented here**:
distance alone never merges, and the only place that rule lives is `commons/merge.py`. On a
hit the two records are re-merged with :func:`~commons.merge.merge_cluster` and written back
**onto the existing row's id**, so a refresh enriches rather than forks.

**2. Provenance is append-only *and* idempotent.** Every :class:`FieldObservation` plus
every ``SourcedValue`` on the merged record becomes a ``site_source`` row, deduped on the
natural key ``(site_id, field, source, value, observed_at)``. Re-running the same upsert
therefore appends nothing — "append-only" must not mean "grows without bound on refresh".
``site_conflict`` is written the same way.

That dedupe is the **database's** job, not this module's. Both writes are a single
``INSERT … ON CONFLICT DO NOTHING`` against the unique indexes declared in `commons/db.py`
(:data:`~commons.db.SITE_SOURCE_NATURAL_KEY` /
:data:`~commons.db.SITE_CONFLICT_NATURAL_KEY`), and ``RETURNING`` reports how many rows were
*actually* written. The read-then-write this replaced was correct only for a lone writer:
two sessions refreshing the same area both finished reading before either wrote, so both
found the key absent and both appended it. No amount of Python could close that window —
only a constraint can. It also removes the second notion of JSON equality this module used
to carry (a ``json.dumps(sort_keys=True)`` canonical form) — see
:func:`~commons.db.jsonb_digest` for why one engine now owns that comparison outright.

**3. No user-sourced value enters the commons (FR-010 / validation rule 7).** The commons is
a shared global resource; personal data is per-user and private (``user_note``). Every value
*and* every provenance entry of *every* result is scanned **before the first write**, so a
refusal leaves the session untouched rather than half-written.

**4. Nothing unstamped is ever read back (FR-003 / `contracts/sites.md`).** A stored
``fields`` blob that fails :class:`~commons.models.SiteRecordV1` validation is dropped and
logged, never returned as a half-record.

CRS discipline: geometry crosses this boundary as EPSG:4326 **(lon, lat)**, validated by
`commons/geo.py` on both sides. Spatial predicates run in PostGIS; ε is measured on the
WGS84 spheroid (``geography``), the same metric ``merge.distance_m`` uses, so the SQL
pre-filter and the Python join rule agree on 25 m.

**5. The HITL gate (T022, ADR-0023) — the second half of this module, and a different
table.** ``user_plan`` is *not* commons data: an itinerary is personal, row-scoped to the
auth subject, and nothing here writes to ``site``/``site_source``/``site_conflict``. The
lifecycle ``proposing → proposed → approved | superseded`` (plus ``compiling → compiled |
failed``) lives as **row state**, so the pause is a row rather than a suspended coroutine
and there is nothing in memory for a restart to lose. Three properties follow, and each is
a database predicate rather than an application check:

- :func:`approve_plan` is a **compare-and-set** on ``(id, user_id, status='proposed',
  itinerary_hash)``. Two racing approvals produce one update and one zero-row update; the
  loser reads the row back and replays the existing approval.
- :func:`claim_plan_for_compile` flips ``approved → compiling`` in the transaction that
  consumes it. A ``proposed`` plan is not refused by an ``if`` — it is *unclaimable*, so a
  future code path that forgets to check simply claims zero rows.
- :func:`supersede_plan` writes a **new row** at ``revision+1`` and clears **nothing**: the
  prior row keeps ``approved_at``/``approved_by`` as history and gains ``superseded_by``.

Every read here filters on ``user_id``. That is the PRD §13 #4 privacy boundary and it is
why none of these take an unscoped overload: another subject's plan must be *indistinguishable
from a missing one*, and an unscoped read is one refactor away from a ``403``-shaped answer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from pydantic import ValidationError
from shapely.geometry.base import BaseGeometry
from sqlalchemy import ColumnElement, Row, Select, func, insert, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.roles import DDLConstraintColumnRole

from commons.db import (
    POST_APPROVAL_STATUSES,
    SITE_CONFLICT_NATURAL_KEY,
    SITE_SOURCE_NATURAL_KEY,
    SRID,
    Area,
    PlanStatus,
    Site,
    SiteConflict,
    SiteSource,
    UserPlan,
    to_db_point,
)
from commons.geo import validate_lat, validate_lon
from commons.merge import (
    EPSILON_METERS,
    FieldObservation,
    MergeResult,
    decide_match,
    iter_sourced_values,
    merge_cluster,
    source_refs,
)
from commons.models import ItineraryV1, SiteRecordV1, SourceRef

__all__ = [
    "COVERED_FRACTION",
    "EDITABLE_STATUSES",
    "ApprovalOutcome",
    "Coverage",
    "CommonsWriteRefused",
    "PlanApproved",
    "PlanRecord",
    "PlanRefused",
    "RefusalReason",
    "UpsertReport",
    "approve_plan",
    "attribution_for",
    "claim_plan_for_compile",
    "coverage",
    "create_plan",
    "finish_compile",
    "load_plan",
    "load_site",
    "record_feasibility",
    "sites_in_bbox",
    "sites_within",
    "supersede_plan",
    "upsert_sites",
]

_log = logging.getLogger(__name__)


class CommonsWriteRefused(ValueError):
    """A user-sourced value reached the commons boundary (FR-010 / validation rule 7).

    Not a bug to be worked around: ``source.kind="user"`` data belongs in ``user_note``,
    row-scoped to its owner. Raised *before* any row is written, so the caller's session is
    unchanged when it propagates.
    """


#: What fraction of a requested polygon must have been researched for ``covered`` to be true.
#:
#: A **dimensionless fraction of the asked-for area**, deliberately — not a site density and
#: not a distance. A density constant would be a place-specific assumption (a desert, an
#: industrial estate and a medieval core have legitimately different densities, and SC-005
#: forbids exactly that kind of built-in expectation); "how much of what you asked about did
#: we look at" means the same thing everywhere on Earth.
#:
#: The value is high on purpose. The two errors are not symmetric: reporting an unresearched
#: region as covered is **silent** — the user sees a populated map and never learns the rest
#: was never looked at — while re-researching ground already covered is visible, idempotent
#: (:func:`upsert_sites` enriches, never forks) and costs only adapter fan-out. The residual
#: 1 % is slack for float and geodesic rounding on a re-delimitation of the *same* extent,
#: not an allowance for unresearched ground.
COVERED_FRACTION: Final[float] = 0.99


@dataclass(frozen=True, slots=True)
class Coverage:
    """What the commons already knows about an area — the `contracts/areas.md` block.

    Note the two halves answer to different scopes, and that is not an accident: the site
    count is **commons** data (shared, ADR-0008), the researched extent is read from ``area``
    and is therefore **per-user** (ADR-0015). See :func:`coverage`.
    """

    #: ``ST_Within(site.geom, polygon)`` count. Commons-wide, not per-user.
    known_site_count: int
    #: Fraction of ``polygon`` (by true surface area) inside the caller's researched extent.
    researched_fraction: float
    #: ``researched_fraction >= COVERED_FRACTION`` — "was this area researched", *not*
    #: "does it contain a site".
    covered: bool
    #: Minimum ``observed_at`` across the covered sites' provenance; ``None`` when none.
    stalest_observed_at: date | None
    #: FR-006: a refresh is always on offer once an area is covered.
    refresh_available: bool


@dataclass(frozen=True, slots=True)
class UpsertReport:
    """What one :func:`upsert_sites` pass did — the numbers T054 asserts on."""

    #: Commons site ids touched, in input order (an id may repeat if two results merged).
    written: tuple[UUID, ...]
    created: int
    updated: int
    #: ``site_conflict`` rows written this pass (0 on an idempotent re-run).
    conflicts: int
    #: ``site_source`` rows appended this pass (0 on an idempotent re-run).
    source_rows: int


# ── SQL helpers ────────────────────────────────────────────────────────────────────


def _geom(geometry: BaseGeometry) -> ColumnElement[Any]:
    """A geometry as a **bound parameter** (EWKT), never string-interpolated SQL."""
    return func.ST_GeomFromEWKT(f"SRID={SRID};{geometry.wkt}")


def _insert_new(
    session: Session,
    entity: type[SiteSource] | type[SiteConflict],
    rows: Sequence[dict[str, Any]],
    natural_key: Sequence[DDLConstraintColumnRole],
) -> int:
    """``INSERT … ON CONFLICT DO NOTHING``; returns how many rows were **actually** written.

    ``DO NOTHING`` (not ``DO UPDATE``) because both tables are ledgers: a row that already
    exists is already right, and re-stating it is not new information. It also makes the
    statement safe against duplicates *within* one batch — ``DO UPDATE`` would raise
    ``cannot affect row a second time``, ``DO NOTHING`` simply skips.

    ``RETURNING`` is what keeps :class:`UpsertReport` honest: it yields only the rows that
    won their insert, so ``source_rows``/``conflicts`` are 0 on an idempotent re-run without
    this module counting anything itself.
    """
    if not rows:
        return 0
    statement = (
        pg_insert(entity)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=list(natural_key))
        .returning(entity.id)
    )
    return len(session.execute(statement).scalars().all())


def _record_of(row: Site) -> SiteRecordV1 | None:
    """Validate a stored row into a record, or drop it (FR-003: never a half-record).

    The columns win over the blob for identity, matching how :func:`upsert_sites` writes
    them; a blob that cannot produce a fully stamped record is *not* returned.
    """
    try:
        return SiteRecordV1.model_validate(
            {**row.fields, "id": row.id, "gers_id": row.gers_id, "updated_at": row.updated_at}
        )
    except ValidationError as error:
        _log.warning(
            "commons read dropped an unstamped/invalid row: table=site site_id=%s reason=%s",
            row.id,
            error,
        )
        return None


def _records(session: Session, statement: Select[tuple[Site]]) -> tuple[SiteRecordV1, ...]:
    rows = session.execute(statement).scalars().all()
    return tuple(record for row in rows if (record := _record_of(row)) is not None)


# ── the refusal boundary (FR-010) ──────────────────────────────────────────────────


def _labelled_sources(result: MergeResult) -> Iterator[tuple[str, SourceRef]]:
    """Every ``(field, SourceRef)`` this result would put in the commons, ledger included."""
    for field, value in iter_sourced_values(result.record):
        yield field, value.source
    for observation in result.provenance:
        yield observation.field, observation.value.source
    for story in result.record.stories:
        yield "stories", story.source
        for claim in story.claims:
            yield "stories.claims", claim.source


def _refuse_user_sources(result: MergeResult) -> None:
    for field, ref in _labelled_sources(result):
        if ref.kind == "user":
            raise CommonsWriteRefused(
                f"refusing to write field {field!r} to the shared commons: its source is "
                f"kind='user' (id={ref.id!r}). Personal data is per-user and private — it "
                "belongs in `user_note`, row-scoped to its owner (FR-010 / validation rule 7)."
            )


# ── upsert (T030) ──────────────────────────────────────────────────────────────────


def _nearby(session: Session, record: SiteRecordV1) -> Sequence[Site]:
    """Commons rows within ε of the incoming location, nearest first.

    A *candidate* query only — whether any of them is the same place is decided by
    :func:`~commons.merge.decide_match`, which requires a name signal on top of ε.
    Distance is measured as ``geography`` (WGS84 spheroid metres), matching ε's units.
    """
    here = _geom(record.location.value)
    distance = func.ST_Distance(sql_cast(Site.geom, Geography), sql_cast(here, Geography))
    statement = (
        select(Site)
        .where(
            func.ST_DWithin(
                sql_cast(Site.geom, Geography), sql_cast(here, Geography), EPSILON_METERS
            )
        )
        .order_by(distance, Site.id)
    )
    return session.execute(statement).scalars().all()


def _find_existing(session: Session, record: SiteRecordV1) -> Site | None:
    """The commons row this record belongs to — id join first, then the fuzzy rule."""
    if record.gers_id is not None:
        row = session.execute(select(Site).where(Site.gers_id == record.gers_id)).scalars().first()
        if row is not None:
            return row
    for candidate in _nearby(session, record):
        previous = _record_of(candidate)
        if previous is not None and decide_match(previous, record).matched:
            return candidate
    return None


def _append_sources(
    session: Session,
    site_id: UUID,
    record: SiteRecordV1,
    observations: Iterable[FieldObservation],
) -> int:
    """Append the provenance ledger, deduped on ``(field, source, value, observed_at)``.

    The dedupe is ``uq_site_source_observation``'s, not this function's — nothing here
    compares two payloads.
    """
    candidates = [(o.field, o.value) for o in observations] + list(iter_sourced_values(record))
    rows = []
    for field, value in candidates:
        dumped = value.model_dump(mode="json")
        rows.append(
            {
                "id": uuid4(),
                "site_id": site_id,
                "field": field,
                "source": dumped["source"],
                "value": dumped["value"],
                "observed_at": value.observed_at,
            }
        )
    return _insert_new(session, SiteSource, rows, SITE_SOURCE_NATURAL_KEY)


def _write_conflicts(session: Session, site_id: UUID, record: SiteRecordV1) -> int:
    """Write the merged record's ``FieldConflict``s, deduped like the provenance ledger."""
    rows = [
        {
            "id": uuid4(),
            "site_id": site_id,
            "field": conflict.field,
            "candidates": [candidate.model_dump(mode="json") for candidate in conflict.candidates],
            "resolution": conflict.resolution,
        }
        for conflict in record.conflicts
    ]
    return _insert_new(session, SiteConflict, rows, SITE_CONFLICT_NATURAL_KEY)


def upsert_sites(session: Session, results: Iterable[MergeResult]) -> UpsertReport:
    """Land merged records in the commons: upsert ``site``, append provenance, log conflicts.

    Flushes but does **not** commit — the caller owns the transaction boundary (an API
    request, a pipeline pass), so a downstream failure still rolls the whole pass back.
    """
    pending = tuple(results)
    # The whole batch is screened first: a refusal must never leave a partial write behind.
    for result in pending:
        _refuse_user_sources(result)

    written: list[UUID] = []
    created = updated = conflicts = source_rows = 0
    for result in pending:
        existing = _find_existing(session, result.record)
        record = result.record
        observations = list(result.provenance)
        if existing is None:
            site_id = record.id
            session.add(
                Site(
                    id=site_id,
                    gers_id=record.gers_id,
                    geom=to_db_point(record.location.value),
                    fields=record.model_dump(mode="json"),
                    updated_at=record.updated_at,
                )
            )
            created += 1
        else:
            site_id = existing.id
            previous = _record_of(existing)
            if previous is not None:
                remerged = merge_cluster([previous, record])
                record = remerged.record
                observations.extend(remerged.provenance)
            # Re-keyed onto the row that already exists: this is what makes refresh
            # non-destructive and dedupe-on-refresh true (T051/T054).
            record = record.model_copy(update={"id": site_id})
            existing.gers_id = record.gers_id
            existing.geom = to_db_point(record.location.value)
            existing.fields = record.model_dump(mode="json")
            existing.updated_at = record.updated_at
            updated += 1
        # The FK target must exist before its audit rows, and the next result must be able
        # to find this row (two identical results in one batch collapse onto one site).
        session.flush()
        source_rows += _append_sources(session, site_id, record, observations)
        conflicts += _write_conflicts(session, site_id, record)
        session.flush()
        written.append(site_id)

    return UpsertReport(
        written=tuple(written),
        created=created,
        updated=updated,
        conflicts=conflicts,
        source_rows=source_rows,
    )


# ── reads (T049 + contracts/sites.md) ──────────────────────────────────────────────


def _researched_fraction(session: Session, polygon: BaseGeometry, subject: str) -> float:
    """How much of ``polygon`` lies inside ``subject``'s researched extent, in ``[0, 1]``.

    ``ST_Union`` first, then one intersection: overlapping delimitations (the normal case —
    the same old town researched at three zoom levels) must not have their areas added
    together, or a heavily re-researched area would report more than 100 % of itself.

    Measured as ``geography`` (true m² on the WGS84 spheroid), not as planar degrees. A
    degree of longitude is a different distance at every latitude, so a planar ratio would
    make the *same* pair of shapes report differently near the equator and near the poles —
    a place-dependent answer out of place-neutral code, which is what SC-005 exists to
    prevent.

    Areas that do not intersect the request are filtered out in SQL rather than unioned and
    discarded: that is the predicate ``ix_area_polygon`` serves.
    """
    here = _geom(polygon)
    researched = (
        select(func.ST_Union(Area.polygon))
        .where(
            Area.created_by == subject,
            Area.researched_at.is_not(None),
            func.ST_Intersects(Area.polygon, here),
        )
        .scalar_subquery()
    )
    overlap_m2, requested_m2 = session.execute(
        select(
            func.ST_Area(sql_cast(func.ST_Intersection(here, researched), Geography)),
            func.ST_Area(sql_cast(here, Geography)),
        )
    ).one()
    # No researched area intersects: the aggregate is NULL and so is the intersection.
    if overlap_m2 is None or not requested_m2:
        return 0.0
    # Clamped: an intersection can round a hair above the whole on a self-identical polygon.
    return min(1.0, float(overlap_m2) / float(requested_m2))


def coverage(session: Session, polygon: BaseGeometry, *, researched_by: str) -> Coverage:
    """Existing coverage of ``polygon`` — the reuse/refresh decision (FR-006 / SC-003).

    ``covered`` answers **"has this area been researched?"**, measured as the fraction of
    ``polygon`` inside the union of ``researched_by``'s completed research extents. It used
    to answer "does the commons hold a site anywhere inside this polygon" (``count > 0``),
    which is a different question with a silent failure: the web client delimits by map
    viewport (`web/src/map/delimit.ts`), so panning out one zoom step produced a polygon that
    still contained the sites researched inside it and therefore reported ``covered: true``
    for a region nobody had ever looked at — FR-006 then served reuse instead of research and
    the user saw a populated map with no hint of the gap (ADR-0018).

    Site count is **not** part of the answer. An area that was genuinely researched and found
    to hold nothing is covered — re-researching it would find nothing again — and SC-006 says
    "nothing found here" is a correct result, not an unresearched one.

    **Scope (ADR-0015).** ``researched_by`` is the auth subject, and only that subject's
    ``area`` rows are read. The commons itself is shared (ADR-0008): ``known_site_count`` and
    ``stalest_observed_at`` below count *everyone's* sites, and a second user re-delimiting a
    researched old town still sees every record. What they do not inherit is the *reuse
    decision* — they will run a pass over ground someone else already covered, which is
    wasteful but idempotent. Widening this read to all subjects would make one user's
    delimitations observable to another (the polygon is the datum ADR-0015 protects, and a
    probing caller can trace a boundary from a scalar), and that is a privacy posture Ben owns
    via PRD §13 #4 — flagged, not quietly taken. Today it is also unobservable: one subject.
    """
    within = func.ST_Within(Site.geom, _geom(polygon))
    count: int = session.execute(select(func.count()).select_from(Site).where(within)).scalar_one()
    stalest: date | None = session.execute(
        select(func.min(SiteSource.observed_at)).where(
            SiteSource.site_id.in_(select(Site.id).where(within))
        )
    ).scalar_one()
    fraction = _researched_fraction(session, polygon, researched_by)
    covered = fraction >= COVERED_FRACTION
    return Coverage(
        known_site_count=count,
        researched_fraction=fraction,
        covered=covered,
        stalest_observed_at=stalest,
        # FR-006: covered areas always offer a refresh, however fresh they are.
        refresh_available=covered,
    )


def sites_in_bbox(
    session: Session, bbox: tuple[float, float, float, float]
) -> tuple[SiteRecordV1, ...]:
    """The cited records whose location falls in ``(minlon, minlat, maxlon, maxlat)``.

    Each ordinate is validated **against its own axis**, so a lat-first bbox with
    ``|lon| > 90`` fails here instead of quietly querying the wrong hemisphere.
    """
    minlon, minlat, maxlon, maxlat = bbox
    envelope = func.ST_MakeEnvelope(
        validate_lon(minlon), validate_lat(minlat), validate_lon(maxlon), validate_lat(maxlat), SRID
    )
    # `&&` — the GiST-indexed bounding-box overlap of `contracts/sites.md`.
    statement = select(Site).where(Site.geom.bool_op("&&")(envelope)).order_by(Site.id)
    return _records(session, statement)


def sites_within(session: Session, polygon: BaseGeometry) -> tuple[SiteRecordV1, ...]:
    """The cited records strictly inside ``polygon`` (``ST_Within``, EPSG:4326)."""
    statement = select(Site).where(func.ST_Within(Site.geom, _geom(polygon))).order_by(Site.id)
    return _records(session, statement)


def load_site(session: Session, site_id: UUID) -> SiteRecordV1 | None:
    """One record by commons id; ``None`` when absent **or** unstamped (never partial)."""
    row = session.get(Site, site_id)
    return None if row is None else _record_of(row)


def attribution_for(sites: Iterable[SiteRecordV1]) -> tuple[str, ...]:
    """The `contracts/sites.md` ``attribution[]`` array: sorted, unique, stamp-derived.

    Only what the stamps carry — nothing invents an attribution string. Every reachable
    source ref counts (winning values, conflict candidates, story claims), so a credit
    cannot be lost by a value losing a merge.
    """
    return tuple(
        sorted({ref.attribution for site in sites for ref in source_refs(site) if ref.attribution})
    )


# ── the HITL gate (T022 · ADR-0023 · data-model §6) ────────────────────────────────
#
# A different table and a different posture from everything above: `user_plan` is personal
# data, so every function below takes the auth subject as a *required keyword* and puts it
# in the WHERE clause. There is deliberately no unscoped variant to reach for.

#: The states an edit may supersede. ``superseded`` is excluded so a chain cannot be
#: re-pointed and silently lose the successor it already names; ``compiling``/``compiled``/
#: ``failed`` are excluded because a plan that has been handed to the compiler is no longer
#: a draft — editing it would move the ground under a bundle that is being built from it.
#: This tuple is the UPDATE's predicate, not an ``if`` — an uneditable row matches zero rows.
EDITABLE_STATUSES: Final[tuple[PlanStatus, ...]] = ("proposing", "proposed", "approved")

#: Why an approval was refused. These are the wire ``error`` codes of
#: `contracts/plans.md` **verbatim** (except ``not_found``, which is a ``404`` with no
#: body), so `api/plans.py` maps status codes and nothing translates vocabulary.
RefusalReason = Literal[
    "not_found", "infeasible", "plan_superseded", "plan_stale", "plan_not_approvable"
]

_PLAN_COLUMNS: Final[tuple[Any, ...]] = (
    UserPlan.id,
    UserPlan.user_id,
    UserPlan.area_id,
    UserPlan.itinerary,
    UserPlan.status,
    UserPlan.revision,
    UserPlan.superseded_by,
    UserPlan.approved_at,
    UserPlan.approved_by,
    UserPlan.itinerary_hash,
    UserPlan.feasible,
    UserPlan.violations,
    UserPlan.feasibility_checked_at,
)


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """One ``user_plan`` row, read back — the server half of ``GET /plans/{plan_id}``.

    Deliberately built from **column values, never a mapped entity**: every write below is
    a conditional ``UPDATE`` the ORM does not see, and an entity left in the identity map
    would keep reporting the state the row had before the database decided otherwise.
    """

    id: UUID
    user_id: str
    area_id: UUID
    itinerary: ItineraryV1
    status: PlanStatus
    revision: int
    superseded_by: UUID | None
    approved_at: datetime | None
    approved_by: str | None
    #: The digest **as stored** at the last write of :attr:`itinerary`.
    itinerary_hash: str
    feasible: bool
    violations: tuple[str, ...]
    feasibility_checked_at: datetime | None

    @property
    def content_hash(self) -> str:
        """The RFC 8785 digest **recomputed** from :attr:`itinerary` right now.

        Equal to :attr:`itinerary_hash` unless something mutated the ``itinerary`` column
        in place without restating the digest — which is the one thing the revision counter
        cannot see, and therefore exactly what :func:`approve_plan`'s hash predicate is for
        (ADR-0023: a safety net, not the primary discriminator).
        """
        return self.itinerary.canonical_sha256()


@dataclass(frozen=True, slots=True)
class PlanApproved:
    """The plan is approved. ``newly_approved`` distinguishes the transition from a replay."""

    plan: PlanRecord
    #: ``False`` when this call found the approval already made — a double-click, a retried
    #: request, a second tab, or the loser of a race. The caller still answers ``200``:
    #: nothing failed, and re-reporting the *same* ``approved_at`` is the point.
    newly_approved: bool


@dataclass(frozen=True, slots=True)
class PlanRefused:
    """Approval did not happen. ``plan`` is the row as it actually is (``None`` = absent)."""

    reason: RefusalReason
    plan: PlanRecord | None


#: What :func:`approve_plan` returns. A tagged result rather than an exception, because a
#: superseded or infeasible plan is ordinary control flow the contract has a body for.
ApprovalOutcome = PlanApproved | PlanRefused


def _utc(value: datetime | None) -> datetime | None:
    """Normalise a ``timestamptz`` read back into UTC, so callers never compare offsets."""
    return None if value is None else value.astimezone(UTC)


def _plan_of(row: Row[Any]) -> PlanRecord:
    """Build a :class:`PlanRecord`; raises ``ValidationError`` on a non-itinerary blob.

    Write paths call this directly — they have just round-tripped a validated model, so a
    failure here is a real defect and must not be swallowed. :func:`load_plan` is the one
    place that catches, mirroring :func:`_record_of`'s never-a-half-record rule.
    """
    return PlanRecord(
        id=row.id,
        user_id=row.user_id,
        area_id=row.area_id,
        itinerary=ItineraryV1.model_validate(row.itinerary),
        status=row.status,
        revision=row.revision,
        superseded_by=row.superseded_by,
        approved_at=_utc(row.approved_at),
        approved_by=row.approved_by,
        itinerary_hash=row.itinerary_hash,
        feasible=row.feasible,
        violations=tuple(row.violations),
        feasibility_checked_at=_utc(row.feasibility_checked_at),
    )


def _update_plan(
    session: Session,
    plan_id: UUID,
    user_id: str,
    *,
    where: Sequence[ColumnElement[bool]],
    values: dict[str, Any],
) -> PlanRecord | None:
    """One conditional ``UPDATE … RETURNING``; ``None`` when it matched **zero rows**.

    Every transition in this module goes through here, so the ``user_id`` scope and the
    ``updated_at`` touch cannot be forgotten on a new code path. ``synchronize_session`` is
    off because nothing here holds mapped entities (:class:`PlanRecord`).
    """
    statement = (
        update(UserPlan)
        .where(UserPlan.id == plan_id, UserPlan.user_id == user_id, *where)
        .values(**values, updated_at=datetime.now(UTC))
        .returning(*_PLAN_COLUMNS)
        .execution_options(synchronize_session=False)
    )
    row = session.execute(statement).first()
    return None if row is None else _plan_of(row)


def load_plan(session: Session, plan_id: UUID, *, user_id: str) -> PlanRecord | None:
    """One plan by id, **scoped to its owner**; ``None`` when absent, foreign, or corrupt.

    The three cases collapse into one answer on purpose: `contracts/plans.md` requires
    another subject's plan to be a ``404`` and never a ``403``, so "exists but not yours"
    must be *indistinguishable* from "does not exist" at this boundary rather than at the
    handler's. A blob that no longer validates as an ``ItineraryV1`` is logged and joins
    them — a half-plan is never returned (FR-003's rule, applied to personal data).
    """
    row = session.execute(
        select(*_PLAN_COLUMNS).where(UserPlan.id == plan_id, UserPlan.user_id == user_id)
    ).first()
    if row is None:
        return None
    try:
        return _plan_of(row)
    except ValidationError as error:
        _log.warning(
            "plan read dropped an invalid itinerary: table=user_plan plan_id=%s reason=%s",
            plan_id,
            error,
        )
        return None


def create_plan(session: Session, itinerary: ItineraryV1, *, revision: int = 1) -> PlanRecord:
    """Insert a plan at ``proposing`` — the row the proposal stream is written against.

    ``user_id``/``area_id`` are taken from the itinerary rather than passed alongside it, so
    the row's privacy scope and the document's cannot disagree. The row **id is the
    itinerary's id**: the plan row is the approval state *of that document*, and an edit
    produces a new document (hence a new row, `contracts/plans.md`: "the superseding plan
    must be approved on its own id"). Re-inserting the same itinerary is therefore a primary
    key violation, which is the correct answer to writing one plan twice.

    Starts ``feasible=False`` with no ``feasibility_checked_at``: nothing has checked yet,
    and the post-approval `CHECK` means an unchecked plan is unapprovable in the database
    until :func:`record_feasibility` says otherwise. The ``INSERT`` executes immediately
    (autoflushing any pending ORM state ahead of it, which is how the ``area`` FK target is
    already there); the caller still owns the commit.
    """
    payload = itinerary.model_dump(mode="json")
    row = session.execute(
        insert(UserPlan)
        .values(
            id=itinerary.id,
            user_id=itinerary.user_id,
            area_id=itinerary.area_id,
            itinerary=payload,
            status="proposing",
            revision=revision,
            itinerary_hash=itinerary.canonical_sha256(),
            feasible=False,
            violations=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        .returning(*_PLAN_COLUMNS)
    ).one()
    return _plan_of(row)


def record_feasibility(
    session: Session,
    plan_id: UUID,
    *,
    user_id: str,
    feasible: bool,
    violations: Sequence[str],
    checked_at: datetime | None = None,
) -> PlanRecord | None:
    """Record the deterministic verdict and advance ``proposing → proposed``.

    Only a ``proposing``/``proposed`` row is touched, and that is a real guard rather than
    tidiness: re-running feasibility over an ``approved`` row could flip ``feasible`` to
    false, which the post-approval `CHECK` would reject — aborting the caller's whole
    transaction instead of declining one statement. A verdict that changes after approval is
    an **edit** (:func:`supersede_plan`), which is why the successor row starts unapproved
    and re-runs feasibility before approval is offered again.

    ``feasibility_checked_at`` is set here and nowhere else, so ``feasibility.checked_at``
    always names a moment the check actually ran — never ``updated_at``, which bumps on
    every write.
    """
    return _update_plan(
        session,
        plan_id,
        user_id,
        where=[UserPlan.status.in_(("proposing", "proposed"))],
        values={
            "status": "proposed",
            "feasible": feasible,
            "violations": list(violations),
            "feasibility_checked_at": checked_at or datetime.now(UTC),
        },
    )


def approve_plan(
    session: Session, plan_id: UUID, *, user_id: str, approved_by: str
) -> ApprovalOutcome:
    """The HITL gate — a **compare-and-set**, idempotent, and never two approvals.

    The transition is a single conditional ``UPDATE`` over ``(id, user_id,
    status='proposed', itinerary_hash, feasible)``. Two concurrent approvals therefore
    produce one row updated and one row *not* updated: Postgres serialises on the row lock,
    the loser re-evaluates its predicate against the winner's committed version, matches
    nothing, and replays the existing approval. One ``approved_at``, one compilable plan.

    ``feasible`` is in the predicate so an infeasible plan gets a **refusal** rather than a
    `CHECK` violation that would roll the caller's transaction back. The `CHECK` is still
    what makes it *impossible* (FR-005 is enforced in the database); the predicate is what
    makes it *answerable*.

    **The hash compared is recomputed from the stored itinerary**, not supplied by the
    caller — ``POST /plans/{id}/approve`` has an empty body, and there is nothing for a
    client to send. Equal digests mean the stored document is the one the stored digest was
    taken over; unequal means someone mutated ``itinerary`` in place, which no revision
    counter would notice.

    **After a zero-row update the row is read and branched on its actual state.** Do *not*
    infer the reason from which predicate failed: because an edit writes a **new** row
    rather than mutating this one, a superseded row's hash still matches and it is the
    *status* predicate that fails. Inferring would return an idempotent ``200`` for every
    real stale approve — the mistake ADR-0023 shipped once and corrected.
    """
    current = load_plan(session, plan_id, user_id=user_id)
    if current is None:
        return PlanRefused("not_found", None)

    now = datetime.now(UTC)
    approved = _update_plan(
        session,
        plan_id,
        user_id,
        where=[
            UserPlan.status == "proposed",
            UserPlan.itinerary_hash == current.content_hash,
            UserPlan.feasible.is_(True),
        ],
        values={"status": "approved", "approved_at": now, "approved_by": approved_by},
    )
    if approved is not None:
        return PlanApproved(plan=approved, newly_approved=True)

    after = load_plan(session, plan_id, user_id=user_id)
    if after is None:
        return PlanRefused("not_found", None)
    if after.itinerary_hash != after.content_hash:
        # Mutated in place — the row's own digest no longer describes its own content.
        return PlanRefused("plan_stale", after)
    if after.status in POST_APPROVAL_STATUSES:
        # Approval already happened, and possibly was already acted on. Nothing failed.
        # Read from `commons/db.py` rather than restated, so this branch and the two
        # `CHECK`s written over the same set cannot come to disagree about its members.
        return PlanApproved(plan=after, newly_approved=False)
    if after.status == "superseded":
        return PlanRefused("plan_superseded", after)
    if after.status == "proposed" and not after.feasible:
        return PlanRefused("infeasible", after)
    # `proposing` (the proposal has not finished) or `failed` (approved once, its compile
    # failed; re-approving does not un-fail it). Refused truthfully rather than explained
    # away as one of the cases above.
    return PlanRefused("plan_not_approvable", after)


def claim_plan_for_compile(session: Session, plan_id: UUID, *, user_id: str) -> PlanRecord | None:
    """Claim an approved plan for compilation — ``approved → compiling``, or ``None``.

    The compiler does **not** read the status and then start work. It calls this inside the
    transaction that will do the work and proceeds only if it got a row back. A ``proposed``
    plan is not refused by an ``if``; it is unclaimable, so a future code path that forgets
    to check the gate simply claims nothing. Two compilers racing on one approved plan
    produce one claim and one bundle.
    """
    return _update_plan(
        session,
        plan_id,
        user_id,
        where=[UserPlan.status == "approved"],
        values={"status": "compiling"},
    )


def finish_compile(
    session: Session, plan_id: UUID, *, user_id: str, outcome: Literal["compiled", "failed"]
) -> PlanRecord | None:
    """Release a claim: ``compiling → compiled | failed``. ``None`` if it was not claimed.

    ``approved_at``/``approved_by`` are untouched by either outcome — a failed compile does
    not un-approve the day, and ``failed`` is outside the post-approval `CHECK` set precisely
    so a plan can carry both its approval and its failure.
    """
    return _update_plan(
        session,
        plan_id,
        user_id,
        where=[UserPlan.status == "compiling"],
        values={"status": outcome},
    )


def supersede_plan(
    session: Session, plan_id: UUID, *, user_id: str, itinerary: ItineraryV1
) -> PlanRecord | None:
    """An edit: write the successor at ``revision+1`` and mark the prior row superseded.

    **An edit clears nothing.** The prior row keeps ``approved_at``/``approved_by`` as
    history and gains ``superseded_by``; the successor starts at ``proposing`` with both
    ``NULL`` and no feasibility verdict, so it must be re-checked before it can be approved.
    Clearing the timestamp instead would destroy the audit trail *and* violate the
    ``(approved_at IS NULL) = (approved_by IS NULL)`` pairing on the way.

    Returns the **successor**, or ``None`` when the prior row is absent, another subject's,
    or in a state :data:`EDITABLE_STATUSES` excludes. The caller distinguishes those with
    :func:`load_plan` — the same read-the-row discipline :func:`approve_plan` uses.

    Ordering is forced by the schema: ``superseded_by`` is a FK *and* one half of
    ``(status='superseded') = (superseded_by IS NOT NULL)``, so the successor must exist
    before the prior row can name it. The pair runs inside a **savepoint**, so a prior row
    that turns out to be unsupersedable (a concurrent edit won the race) does not leave an
    orphan successor behind, and the caller's transaction stays usable either way.
    """
    prior = load_plan(session, plan_id, user_id=user_id)
    if prior is None:
        return None

    savepoint = session.begin_nested()
    successor = create_plan(session, itinerary, revision=prior.revision + 1)
    superseded = _update_plan(
        session,
        plan_id,
        user_id,
        where=[UserPlan.status.in_(EDITABLE_STATUSES)],
        values={"status": "superseded", "superseded_by": successor.id},
    )
    if superseded is None:
        savepoint.rollback()
        return None
    savepoint.commit()
    return successor
