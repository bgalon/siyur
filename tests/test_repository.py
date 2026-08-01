"""Tier-2 — the commons repository over a real PostGIS (T030 / T049 / T054 / T055).

Everything here is driven by the **committed real-data fixtures** (200 Overture places,
25 Overpass nodes — `tests/fixtures/README.md`) run through the real adapters and the real
merge, so what reaches the database is what a research pass would actually produce. The
cross-source anchor is the Gate of the Arsenal: the *same* gate, 3.4 m apart, keyed ``und``
by Overture and ``el`` by OSM — the pair the fuzzy join exists for.

The two failure modes this file is built around:

1. **A silent lon/lat transposition.** Rhodes sits at lon 28.2 / lat 36.4 and *both* are
   legal latitudes, so a swapped pair passes every range check. Only comparing the axes
   explicitly — and querying a lat-first bbox and getting nothing back — catches it.
2. **Refresh that forks or forgets.** Re-running a pass must land on the same ``site`` row,
   append no duplicate ``site_source``, and lose no source ref (T054).

The two adapter passes deliberately carry **different** ``observed_at`` dates so
``Coverage.stalest_observed_at`` has a real minimum to find.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from shapely.geometry import Point, box
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from commons.db import Site, SiteConflict, SiteSource, to_db_point
from commons.merge import merge_records, source_refs
from commons.models import SiteRecordV1, SourcedValue, SourceRef
from commons.repository import (
    CommonsWriteRefused,
    attribution_for,
    coverage,
    load_site,
    sites_in_bbox,
    sites_within,
    upsert_sites,
)
from commons.sources.osm import ATTRIBUTION, OsmAdapter
from commons.sources.overture import OvertureAdapter

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"
PARQUET = str(FIXTURES / "overture_places_rhodes.parquet")
OVERPASS = json.loads((FIXTURES / "overpass_rhodes.json").read_text(encoding="utf-8"))

#: The Rhodes old-town area both fixtures were captured for (lon/lat, EPSG:4326).
AREA = box(28.216, 36.440, 28.232, 36.451)
BBOX = (28.216, 36.440, 28.232, 36.451)
#: Two passes, two dates — so `stalest_observed_at` is a minimum, not a constant.
FRESH = date(2026, 8, 1)
STALE = date(2026, 7, 20)

#: The cross-source anchor (fixtures/README.md): one gate, two sources, 3.4 m apart.
GATE_GERS = "0425b08a-fed7-4c86-b172-9645558883f2"
GATE_OSM = "node/794491388"
#: 27 m from the gate — outside ε, so it stays a second place. The union-first control.
NEIGHBOUR_GERS = "00cf4dda-3a5d-4865-a962-d50d4e1ec17d"

USER = SourceRef(kind="user", id="user-a", license="user-owned")


@pytest.fixture(scope="module")
def overture() -> dict[str, SiteRecordV1]:
    """The parquet fixture through the real adapter, keyed by GERS id. Never the network."""
    result = OvertureAdapter(parquet=PARQUET, observed_at=FRESH).fetch(AREA)
    return {record.gers_id: record for record in result if record.gers_id is not None}


@pytest.fixture(scope="module")
def osm() -> dict[str, SiteRecordV1]:
    """The Overpass fixture through the real adapter, keyed by ``type/id``. Mock transport."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=OVERPASS))
    )
    adapter = OsmAdapter(client=client, backoff=0.0, observed_at=STALE, element_types=("node",))
    return {record.location.source.id: record for record in adapter.fetch(AREA)}


def _count(session: Session, table: type[Any]) -> int:
    total: int = session.execute(select(func.count()).select_from(table)).scalar_one()
    return total


def _ledger(session: Session) -> set[tuple[str, ...]]:
    """The append-only provenance table as comparable tuples — for "nothing was lost"."""
    rows = session.execute(select(SiteSource)).scalars().all()
    return {
        (
            str(row.site_id),
            row.field,
            json.dumps(row.source, sort_keys=True),
            json.dumps(row.value, sort_keys=True),
            row.observed_at.isoformat(),
        )
        for row in rows
    }


def _reachable_refs(session: Session, site_id: UUID) -> set[SourceRef]:
    """Every source ref still findable for a site — on the record *or* in the ledger."""
    record = load_site(session, site_id)
    assert record is not None
    stored = session.execute(
        select(SiteSource.source).where(SiteSource.site_id == site_id)
    ).scalars()
    return set(source_refs(record)) | {SourceRef.model_validate(ref) for ref in stored}


def test_upsert_round_trips_a_record_with_geom_matching_location(
    db_session: Session, overture: dict[str, SiteRecordV1]
) -> None:
    (result,) = merge_records([overture[GATE_GERS]])
    report = upsert_sites(db_session, [result])
    db_session.commit()

    assert (report.created, report.updated) == (1, 0)
    (site_id,) = report.written
    loaded = load_site(db_session, site_id)
    assert loaded == result.record, "`fields` survives jsonb: every stamp re-validates"
    assert sites_in_bbox(db_session, BBOX) == (loaded,)
    assert sites_within(db_session, AREA) == (loaded,)

    point = result.record.location.value
    lon, lat = db_session.execute(select(func.ST_X(Site.geom), func.ST_Y(Site.geom))).one()
    assert (lon, lat) == pytest.approx((point.x, point.y))
    # Both ordinates are legal *latitudes*, so no range check can catch a transposition —
    # only comparing the axes to each other, and querying a lat-first bbox, can.
    assert lon != pytest.approx(lat)
    assert sites_in_bbox(db_session, (36.440, 28.216, 36.451, 28.232)) == ()


def test_upserting_the_same_result_twice_creates_no_duplicates(
    db_session: Session, overture: dict[str, SiteRecordV1], osm: dict[str, SiteRecordV1]
) -> None:
    """T054 — a refresh over a covered area dedupes onto the existing row and loses nothing."""
    (result,) = merge_records([overture[GATE_GERS], osm[GATE_OSM]])
    first = upsert_sites(db_session, [result])
    db_session.commit()
    assert (first.created, first.updated) == (1, 0)
    assert first.source_rows > 0 and first.conflicts > 0
    ledger = _ledger(db_session)

    second = upsert_sites(db_session, [result])
    db_session.commit()

    assert (second.created, second.updated) == (0, 1)
    assert second.written == first.written
    # Append-only must not mean append-*always*: the natural key already existed.
    assert (second.source_rows, second.conflicts) == (0, 0)
    assert _count(db_session, Site) == 1
    assert _count(db_session, SiteSource) == first.source_rows
    assert _count(db_session, SiteConflict) == first.conflicts
    assert _ledger(db_session) == ledger
    assert result.source_refs <= _reachable_refs(db_session, first.written[0])


def test_a_refresh_enriches_the_existing_row_and_keeps_both_sources(
    db_session: Session, overture: dict[str, SiteRecordV1], osm: dict[str, SiteRecordV1]
) -> None:
    """A later pass carrying a *new* source for a known field: winner + conflict candidate."""
    (osm_pass,) = merge_records([osm[GATE_OSM]])
    (site_id,) = upsert_sites(db_session, [osm_pass]).written
    db_session.commit()
    before = _ledger(db_session)

    (overture_pass,) = merge_records([overture[GATE_GERS]])
    report = upsert_sites(db_session, [overture_pass])
    db_session.commit()

    # The fuzzy join (ε + a name signal) found the existing row: enriched, not forked.
    assert (report.created, report.updated) == (0, 1)
    assert report.written == (site_id,)
    assert _count(db_session, Site) == 1
    # The ledger only ever grows; not one prior observation was rewritten.
    assert before < _ledger(db_session)

    record = load_site(db_session, site_id)
    assert record is not None
    assert {ref.kind for ref in source_refs(record)} == {"osm", "overture"}
    assert record.gers_id == GATE_GERS, "the refresh brought the authoritative id in"
    # The sources disagree on `location` (3.4 m apart) — both survive, one as the winner
    # and one as a conflict candidate. No source is ever discarded (FR-009).
    (conflict,) = [c for c in record.conflicts if c.field == "location"]
    assert {candidate.source.kind for candidate in conflict.candidates} == {"osm", "overture"}
    assert report.conflicts == _count(db_session, SiteConflict) == 1
    assert osm_pass.source_refs | overture_pass.source_refs <= _reachable_refs(db_session, site_id)


def test_a_user_sourced_value_is_refused_and_nothing_at_all_is_written(
    db_session: Session, overture: dict[str, SiteRecordV1]
) -> None:
    """FR-010 / validation rule 7 — refused at the boundary, before any partial write."""
    private = SourcedValue[Any].stamp(
        value="meet Dana here at 18:00", source=USER, confidence=1.0, observed_at=FRESH
    )
    tainted = overture[GATE_GERS].model_copy(update={"notes": (private,)})
    # The clean record comes *first* in the batch: the refusal must still leave it unwritten.
    results = merge_records([overture[NEIGHBOUR_GERS]]) + merge_records([tainted])

    with pytest.raises(CommonsWriteRefused, match="notes"):
        upsert_sites(db_session, results)
    db_session.commit()

    assert _count(db_session, Site) == 0
    assert _count(db_session, SiteSource) == 0
    assert _count(db_session, SiteConflict) == 0


def test_a_record_written_in_one_session_is_readable_in_another(
    db_engine: Engine, db_session: Session, overture: dict[str, SiteRecordV1]
) -> None:
    """T055 — the commons is a shared global resource (ADR-0008): no per-user scoping."""
    (result,) = merge_records([overture[GATE_GERS]])
    (site_id,) = upsert_sites(db_session, [result]).written
    db_session.commit()

    # A different signed-in user is a different Session over the same commons. These reads
    # take no `user_id` — that is the point; only `user_note` is row-scoped.
    with Session(db_engine) as other:
        assert load_site(other, site_id) == result.record
        assert [record.id for record in sites_in_bbox(other, BBOX)] == [site_id]
        assert coverage(other, AREA).known_site_count == 1


def test_coverage_counts_st_within_and_reports_the_stalest_observation(
    db_session: Session, overture: dict[str, SiteRecordV1], osm: dict[str, SiteRecordV1]
) -> None:
    """T049 / FR-006 — the `contracts/areas.md` coverage block."""
    results = merge_records([overture[GATE_GERS], overture[NEIGHBOUR_GERS], osm[GATE_OSM]])
    assert len(results) == 2, "the gate merges across sources; the 27 m neighbour does not"
    upsert_sites(db_session, results)
    db_session.commit()

    whole = coverage(db_session, AREA)
    assert whole.known_site_count == sum(1 for r in results if AREA.covers(r.record.location.value))
    assert whole.known_site_count == 2
    assert (whole.covered, whole.refresh_available) == (True, True)
    assert whole.stalest_observed_at == STALE, "the minimum across the covered sites' ledger"

    # Tighten onto the Overture-only neighbour: the OSM observation is no longer in scope.
    neighbour = overture[NEIGHBOUR_GERS].location.value
    tight = box(neighbour.x - 1e-4, neighbour.y - 1e-4, neighbour.x + 1e-4, neighbour.y + 1e-4)
    only = coverage(db_session, tight)
    assert (only.known_site_count, only.stalest_observed_at) == (1, FRESH)

    empty = coverage(db_session, box(-9.5, -9.5, -9.0, -9.0))
    assert empty.known_site_count == 0
    assert (empty.covered, empty.refresh_available) == (False, False)
    assert empty.stalest_observed_at is None


def test_attribution_comes_only_from_the_stamps(
    overture: dict[str, SiteRecordV1], osm: dict[str, SiteRecordV1]
) -> None:
    """`contracts/sites.md` ``attribution[]`` — ODbL credit for an OSM-derived record."""
    assert ATTRIBUTION == "© OpenStreetMap contributors"
    assert attribution_for([osm[GATE_OSM]]) == (ATTRIBUTION,)
    # Overture stamps no attribution string, so none is invented for it.
    assert attribution_for([overture[GATE_GERS]]) == ()
    (merged,) = merge_records([overture[GATE_GERS], osm[GATE_OSM]])
    assert attribution_for([merged.record]) == (ATTRIBUTION,)


def test_an_unstamped_row_is_rejected_at_the_db_boundary(db_session: Session) -> None:
    """FR-003 / SC-002 — a half-record is dropped, never returned without its provenance."""
    site_id = uuid4()
    db_session.add(
        Site(
            id=site_id,
            geom=to_db_point(Point(28.2247, 36.4443)),
            # No `source` on the value: unconstructible through the model, but a hand-written
            # row (or an older schema) could still put it here.
            fields={
                "location": {
                    "value": {"type": "Point", "coordinates": [28.2247, 36.4443]},
                    "bundleable": True,
                    "confidence": 0.9,
                    "observed_at": "2026-08-01",
                }
            },
            updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    db_session.commit()

    assert load_site(db_session, site_id) is None
    assert sites_in_bbox(db_session, BBOX) == ()
    assert sites_within(db_session, AREA) == ()
