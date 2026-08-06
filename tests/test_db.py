"""Tier-2 persistence round-trip against a real PostGIS (T017).

Three things this proves, all of which unit tests structurally cannot:

1. **A ``SiteRecordV1`` survives storage.** ``fields`` comes back byte-identical and
   re-validates into an equal model, and ``geom`` matches ``location.value`` — read back
   through shapely *and* through PostGIS itself (``ST_X``/``ST_Y``/``ST_SRID``). The
   fixture coordinates are deliberately a pair where **both** numbers are legal latitudes
   (lon 28.22, lat 36.44), so a lon/lat swap raises nothing and slips silently past every
   range check; only comparing the axes catches it. That is the classic bug here.
2. **The privacy boundary holds** (FR-010 / data-model §5.7): a ``user_note`` row never
   appears in a commons read, and never leaks into ``site.fields`` or ``site_source``.
3. **Idempotence is an invariant of the data, not of one code path.** The ledger tables
   carry unique indexes over their natural keys (migration ``0003_dedupe_natural_keys``);
   the tests below drive them through the ORM with no repository involved, so they fail if
   the constraint is missing however careful `commons/repository.py` is being.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from commons.db import Site, SiteConflict, SiteSource, UserNote, from_db_point, to_db_point
from commons.geo import point_from_lonlat
from commons.models import SiteRecordV1, SourcedValue, SourceRef

pytestmark = pytest.mark.integration

# Palace of the Grand Master, Rhodes. |lon| < 90 and |lat| < 90 on purpose — see docstring.
LON = 28.2247
LAT = 36.4443

OBSERVED = date(2026, 7, 22)
OVERTURE = SourceRef(kind="overture", id="08f394gers", license="CDLA-Permissive-2.0")
USER = SourceRef(kind="user", id="user-a", license="user-owned")

#: The `SourcedValue` map persisted in `site.fields`. `notes` are excluded by design (they
#: are private — `user_note`), as are `conflicts` (`site_conflict`) and `stories`.
COMMONS_FIELDS = ("names", "location", "categories", "address", "opening_hours")


def _stamp(value: Any, source: SourceRef = OVERTURE) -> SourcedValue[Any]:
    return SourcedValue.stamp(value=value, source=source, confidence=0.9, observed_at=OBSERVED)


def _record() -> SiteRecordV1:
    return SiteRecordV1(
        gers_id="08f394gers",
        names={"en": _stamp("Palace of the Grand Master"), "el": _stamp("Παλάτι")},
        location=_stamp(point_from_lonlat(LON, LAT)),
        categories=(_stamp("attraction.castle"),),
        address=_stamp("Ippoton, Rhodes 851 00"),
        opening_hours=_stamp("Mo-Su 08:00-20:00"),
        updated_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
    )


def _fields(record: SiteRecordV1) -> dict[str, Any]:
    dumped = record.model_dump(mode="json")
    return {name: dumped[name] for name in COMMONS_FIELDS}


def _persist(session: Any, record: SiteRecordV1) -> None:
    session.add(
        Site(
            id=record.id,
            gers_id=record.gers_id,
            geom=to_db_point(record.location.value),
            fields=_fields(record),
            updated_at=record.updated_at,
        )
    )
    session.commit()
    session.expunge_all()


def test_site_record_round_trips_with_fields_intact(db_session: Any) -> None:
    record = _record()
    _persist(db_session, record)

    row = db_session.execute(select(Site)).scalar_one()
    assert row.id == record.id
    assert row.gers_id == record.gers_id
    assert row.fields == _fields(record)

    # The stored jsonb re-validates into an *equal* model: no stamp, no BCP-47 key and no
    # `bundleable` flag was lost or coerced on the way through Postgres.
    restored = SiteRecordV1.model_validate(
        {**row.fields, "id": row.id, "gers_id": row.gers_id, "updated_at": row.updated_at}
    )
    assert restored == record
    assert restored.names["el"].source.license == "CDLA-Permissive-2.0"


def test_geom_matches_location_value_in_lon_lat_order(db_session: Any) -> None:
    record = _record()
    _persist(db_session, record)
    row = db_session.execute(select(Site)).scalar_one()

    point = from_db_point(row.geom)
    assert (point.x, point.y) == (LON, LAT)
    assert point.equals(record.location.value)

    # Ask PostGIS, not our own round-trip helper: ST_X is longitude, ST_Y is latitude.
    lon, lat, srid = db_session.execute(
        select(func.ST_X(Site.geom), func.ST_Y(Site.geom), func.ST_SRID(Site.geom))
    ).one()
    assert (lon, lat) == pytest.approx((LON, LAT))
    assert srid == 4326
    # Explicitly: the axes are NOT swapped. Both values are legal latitudes, so this is the
    # only assertion in the file that a lon/lat transposition would fail.
    assert lon != pytest.approx(LAT)
    assert row.fields["location"]["value"]["coordinates"] == [LON, LAT]


def test_user_note_is_never_returned_by_a_commons_read(db_session: Any) -> None:
    record = _record()
    _persist(db_session, record)
    secret = "meet Dana here at 18:00"
    db_session.add(
        UserNote(
            user_id="user-a",
            site_id=record.id,
            value=_stamp(secret, USER).model_dump(mode="json"),
        )
    )
    db_session.commit()

    # The commons read — the coverage query of data-model §4, over the GiST-indexed geom.
    bbox = func.ST_MakeEnvelope(LON - 0.01, LAT - 0.01, LON + 0.01, LAT + 0.01, 4326)
    sites = db_session.execute(select(Site).where(func.ST_Within(Site.geom, bbox))).scalars().all()
    assert len(sites) == 1
    payload = json.dumps(sites[0].fields)
    assert secret not in payload
    assert '"kind": "user"' not in payload

    # …and no `kind="user"` provenance reached the append-only audit table either.
    assert db_session.execute(select(SiteSource)).all() == []

    # The note itself is row-scoped: only its owner's query returns it.
    others = db_session.execute(select(UserNote).where(UserNote.user_id == "user-b")).all()
    assert others == []
    owned = db_session.execute(select(UserNote).where(UserNote.user_id == "user-a")).scalars().all()
    assert owned[0].value["value"] == secret


# ── the idempotence invariant (uq_site_source_observation / uq_site_conflict_record) ──

#: The serialised ``SourceRef`` of :data:`OVERTURE`, and the *same* object with its keys
#: written in a different order. Equal as ``jsonb``, unequal as raw text — the distinction
#: the natural key's ``md5(source::text)`` half has to get right.
SOURCE_JSON: dict[str, Any] = {
    "kind": "overture",
    "id": "08f394gers",
    "license": "CDLA-Permissive-2.0",
}
SOURCE_JSON_PERMUTED: dict[str, Any] = {
    "license": "CDLA-Permissive-2.0",
    "id": "08f394gers",
    "kind": "overture",
}


def _observation(site_id: Any, **overrides: Any) -> SiteSource:
    """One provenance row. ``value`` is a bare JSON **string**, as a name observation is."""
    row: dict[str, Any] = {
        "site_id": site_id,
        "field": "names.en",
        "source": SOURCE_JSON,
        "value": "Palace of the Grand Master",
        "observed_at": OBSERVED,
    }
    return SiteSource(**{**row, **overrides})


@pytest.fixture
def site_id(db_session: Any) -> Any:
    """A committed ``site`` row for the ledger rows below to hang off (the FK is NO ACTION)."""
    record = _record()
    _persist(db_session, record)
    return record.id


def test_the_same_observation_cannot_be_recorded_twice(db_session: Any, site_id: Any) -> None:
    """The constraint, not the repository: re-stating an observation is refused by Postgres."""
    db_session.add(_observation(site_id))
    db_session.commit()

    db_session.add(_observation(site_id))
    with pytest.raises(IntegrityError, match="uq_site_source_observation"):
        db_session.commit()
    db_session.rollback()
    assert db_session.execute(select(func.count()).select_from(SiteSource)).scalar_one() == 1


def test_reordering_the_json_keys_does_not_make_a_new_observation(
    db_session: Any, site_id: Any
) -> None:
    """The jsonb-vs-text equality trap (FAIL-005's class), pinned.

    ``SOURCE_JSON`` and ``SOURCE_JSON_PERMUTED`` are the same payload with the keys written
    in a different order. They are equal as ``jsonb`` and **unequal** as naive text, so a
    unique index over a raw text rendering would let the duplicate through. It does not,
    because ``jsonb`` normalises key order on parse and the key hashes ``jsonb``'s own
    canonical output — the semantics the SQL and the Python now share by having only one
    of them do the comparing at all.
    """
    db_session.add(_observation(site_id, source=SOURCE_JSON))
    db_session.commit()

    db_session.add(_observation(site_id, source=SOURCE_JSON_PERMUTED))
    with pytest.raises(IntegrityError, match="uq_site_source_observation"):
        db_session.commit()
    db_session.rollback()


def test_the_same_value_observed_on_a_later_date_is_a_new_row(
    db_session: Any, site_id: Any
) -> None:
    """``site_source`` stays genuinely append-only.

    ``observed_at`` is part of the natural key precisely so the constraint cannot turn a
    legitimate re-observation into an error: seeing the same name again next month *is* new
    information (it is what drives staleness / refresh-on-reuse) and must still insert.
    """
    db_session.add(_observation(site_id))
    db_session.add(_observation(site_id, observed_at=date(2026, 9, 1)))
    db_session.commit()

    dates = db_session.execute(select(SiteSource.observed_at).order_by(SiteSource.observed_at))
    assert list(dates.scalars()) == [OBSERVED, date(2026, 9, 1)]


def test_a_value_too_large_to_index_directly_is_still_accepted(
    db_session: Any, site_id: Any
) -> None:
    """Why the key hashes instead of indexing the ``jsonb`` columns directly.

    A btree index tuple is capped at 2704 bytes and a plain composite unique index over
    ``jsonb`` makes an oversized ``INSERT`` *fail* rather than merely index poorly. The
    payload here is 3 200 incompressible hex characters, comfortably past that cap; under
    the direct index this row would be rejected outright — the exact failure the constraint
    exists to prevent, inverted.
    """
    oversized = secrets.token_hex(1600)
    assert len(oversized) > 2704
    db_session.add(_observation(site_id, field="address", value=oversized))
    db_session.commit()

    (stored,) = db_session.execute(select(SiteSource.value)).scalars().all()
    assert stored == oversized


def test_a_conflict_cannot_be_recorded_twice_but_a_new_resolution_can(
    db_session: Any, site_id: Any
) -> None:
    """``site_conflict``'s natural key, including the same key-order equivalence."""
    candidates = [{"value": "A", "confidence": 0.8}, {"value": "B", "confidence": 0.7}]
    permuted = [{"confidence": 0.8, "value": "A"}, {"confidence": 0.7, "value": "B"}]
    db_session.add(
        SiteConflict(
            site_id=site_id, field="names.en", candidates=candidates, resolution="unresolved"
        )
    )
    db_session.commit()

    # A different `resolution` over the same candidates is a different record, and inserts.
    db_session.add(
        SiteConflict(
            site_id=site_id, field="names.en", candidates=candidates, resolution="picked:08f394gers"
        )
    )
    db_session.commit()
    assert db_session.execute(select(func.count()).select_from(SiteConflict)).scalar_one() == 2

    db_session.add(
        SiteConflict(
            site_id=site_id, field="names.en", candidates=permuted, resolution="unresolved"
        )
    )
    with pytest.raises(IntegrityError, match="uq_site_conflict_record"):
        db_session.commit()
    db_session.rollback()


def test_the_value_column_holds_json_scalars_not_only_objects(
    db_session: Any, site_id: Any
) -> None:
    """``SiteSource.value`` is ``JsonValue``, and the column really does round-trip all of it.

    ``SourcedValue[T]`` is generic: a ``names.en`` observation stores a bare string while a
    ``location`` observation stores a GeoJSON mapping. The old ``Mapped[dict[str, Any]]``
    annotation described only the second and type-checked only because ``model_dump()``
    hands back ``Any``.
    """
    geojson = {"type": "Point", "coordinates": [LON, LAT]}
    for index, value in enumerate([geojson, "a bare string", 42, 0.5, True, None, ["a", "b"]]):
        db_session.add(_observation(site_id, field=f"f{index}", value=value))
    db_session.commit()

    stored = db_session.execute(select(SiteSource.value).order_by(SiteSource.field)).scalars().all()
    assert stored == [geojson, "a bare string", 42, 0.5, True, None, ["a", "b"]]


def test_the_natural_key_leaves_distinct_observations_alone(db_session: Any, site_id: Any) -> None:
    """Every component of the key is load-bearing: vary any one and the row is new."""
    db_session.add(_observation(site_id))
    db_session.commit()

    other_site = SiteRecordV1(
        names={"en": _stamp("Elsewhere")},
        location=_stamp(point_from_lonlat(LON + 0.01, LAT + 0.01)),
        updated_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
    )
    _persist(db_session, other_site)

    for override in (
        {"site_id": other_site.id},
        {"field": "names.el"},
        {"source": {**SOURCE_JSON, "id": "a-different-id"}},
        {"value": "A Different Name"},
        {"observed_at": date(2026, 9, 1)},
    ):
        db_session.add(_observation(override.pop("site_id", site_id), **override))
    db_session.commit()

    assert db_session.execute(select(func.count()).select_from(SiteSource)).scalar_one() == 6


def test_a_uuid_collision_is_not_how_duplicates_are_caught(db_session: Any, site_id: Any) -> None:
    """The key is the observation, not the surrogate id — two ids, one observation, one row."""
    db_session.add(_observation(site_id, id=uuid4()))
    db_session.commit()
    db_session.add(_observation(site_id, id=uuid4()))
    with pytest.raises(IntegrityError, match="uq_site_source_observation"):
        db_session.commit()
    db_session.rollback()
