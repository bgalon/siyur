"""The commons repository — the only place merged records reach PostGIS (T030 / T049).

`commons/merge.py` deliberately stops in memory; this module is the other half: it takes
:class:`~commons.merge.MergeResult`s and lands them on ``site`` / ``site_source`` /
``site_conflict`` (`commons/db.py`, data-model §4), and it answers the two reads the API
contracts need — coverage (`contracts/areas.md`) and the bbox/polygon site read
(`contracts/sites.md`).

Four properties this module exists to guarantee:

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
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from pydantic import ValidationError
from shapely.geometry.base import BaseGeometry
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import Session

from commons.db import SRID, Site, SiteConflict, SiteSource, to_db_point
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
from commons.models import SiteRecordV1, SourceRef

__all__ = [
    "Coverage",
    "CommonsWriteRefused",
    "UpsertReport",
    "attribution_for",
    "coverage",
    "load_site",
    "sites_in_bbox",
    "sites_within",
    "upsert_sites",
]

_log = logging.getLogger(__name__)


class CommonsWriteRefused(ValueError):
    """A user-sourced value reached the commons boundary (FR-010 / validation rule 7).

    Not a bug to be worked around: ``source.kind="user"`` data belongs in ``user_note``,
    row-scoped to its owner. Raised *before* any row is written, so the caller's session is
    unchanged when it propagates.
    """


@dataclass(frozen=True, slots=True)
class Coverage:
    """What the commons already knows about an area — the `contracts/areas.md` block."""

    #: ``ST_Within(site.geom, polygon)`` count.
    known_site_count: int
    #: ``known_site_count > 0``.
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


def _canonical(payload: Any) -> str:
    """Order-independent identity for a JSONB payload — the dedupe key's comparable half."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
    """Append the provenance ledger, deduped on ``(field, source, value, observed_at)``."""
    seen = {
        (field, _canonical(source), _canonical(value), observed_at)
        for field, source, value, observed_at in session.execute(
            select(
                SiteSource.field, SiteSource.source, SiteSource.value, SiteSource.observed_at
            ).where(SiteSource.site_id == site_id)
        ).all()
    }
    candidates = [(o.field, o.value) for o in observations] + list(iter_sourced_values(record))
    appended = 0
    for field, value in candidates:
        dumped = value.model_dump(mode="json")
        key = (field, _canonical(dumped["source"]), _canonical(dumped["value"]), value.observed_at)
        if key in seen:
            continue
        seen.add(key)
        session.add(
            SiteSource(
                site_id=site_id,
                field=field,
                source=dumped["source"],
                value=dumped["value"],
                observed_at=value.observed_at,
            )
        )
        appended += 1
    return appended


def _write_conflicts(session: Session, site_id: UUID, record: SiteRecordV1) -> int:
    """Write the merged record's ``FieldConflict``s, deduped like the provenance ledger."""
    seen = {
        (field, _canonical(candidates), resolution)
        for field, candidates, resolution in session.execute(
            select(SiteConflict.field, SiteConflict.candidates, SiteConflict.resolution).where(
                SiteConflict.site_id == site_id
            )
        ).all()
    }
    written = 0
    for conflict in record.conflicts:
        candidates = [candidate.model_dump(mode="json") for candidate in conflict.candidates]
        key = (conflict.field, _canonical(candidates), conflict.resolution)
        if key in seen:
            continue
        seen.add(key)
        session.add(
            SiteConflict(
                site_id=site_id,
                field=conflict.field,
                candidates=candidates,
                resolution=conflict.resolution,
            )
        )
        written += 1
    return written


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


def coverage(session: Session, polygon: BaseGeometry) -> Coverage:
    """Existing commons coverage of ``polygon`` — the reuse/refresh decision (FR-006)."""
    within = func.ST_Within(Site.geom, _geom(polygon))
    count: int = session.execute(select(func.count()).select_from(Site).where(within)).scalar_one()
    stalest: date | None = session.execute(
        select(func.min(SiteSource.observed_at)).where(
            SiteSource.site_id.in_(select(Site.id).where(within))
        )
    ).scalar_one()
    covered = count > 0
    return Coverage(
        known_site_count=count,
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
