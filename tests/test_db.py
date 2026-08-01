"""Tier-2 persistence round-trip against a real PostGIS (T017).

Two things this proves, both of which unit tests structurally cannot:

1. **A ``SiteRecordV1`` survives storage.** ``fields`` comes back byte-identical and
   re-validates into an equal model, and ``geom`` matches ``location.value`` — read back
   through shapely *and* through PostGIS itself (``ST_X``/``ST_Y``/``ST_SRID``). The
   fixture coordinates are deliberately a pair where **both** numbers are legal latitudes
   (lon 28.22, lat 36.44), so a lon/lat swap raises nothing and slips silently past every
   range check; only comparing the axes catches it. That is the classic bug here.
2. **The privacy boundary holds** (FR-010 / data-model §5.7): a ``user_note`` row never
   appears in a commons read, and never leaks into ``site.fields`` or ``site_source``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import func, select

from commons.db import Site, SiteSource, UserNote, from_db_point, to_db_point
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
