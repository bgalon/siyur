"""T026 — the Overture adapter against the committed parquet fixture (never the network).

The fixture is 200 **real** Overture places rows (release 2026-07-22.0, Rhodes old town)
deliberately spanning three per-record licenses; see `tests/fixtures/README.md`. What
these tests pin down: the license is read *per row* (not from the theme), every emitted
value is stamped, and every coordinate is the fixture's own — never synthesised.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from commons.models import SiteRecordV1, SourcedValue
from commons.sources import base
from commons.sources.overture import OvertureAdapter

FIXTURE = str(Path(__file__).parent / "fixtures" / "overture_places_rhodes.parquet")
#: The Rhodes old-town bbox the fixture was captured for (lon/lat, EPSG:4326).
AREA = box(28.216, 36.440, 28.232, 36.451)
OBSERVED = date(2026, 8, 1)


@pytest.fixture(scope="module")
def result() -> base.FetchResult:
    return OvertureAdapter(parquet=FIXTURE, observed_at=OBSERVED).fetch(AREA)


@pytest.fixture(scope="module")
def truth() -> dict[str, tuple[float, float, str]]:
    """Read the fixture independently: id → (lon, lat, record-level license)."""
    connection = duckdb.connect()
    connection.execute("INSTALL spatial; LOAD spatial;")
    rows = connection.execute(
        "SELECT id, ST_X(geometry), ST_Y(geometry), s.license "
        f"FROM read_parquet('{FIXTURE}'), UNNEST(sources) AS t(s) WHERE s.property = ''"
    ).fetchall()
    connection.close()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def values_of(record: SiteRecordV1) -> list[SourcedValue[Any]]:
    return [
        record.location,
        *record.names.values(),
        *record.categories,
        *([record.address] if record.address else []),
    ]


def test_fetch_yields_the_whole_fixture_with_nothing_dropped(result: base.FetchResult) -> None:
    assert len(result) == 200
    assert result.dropped == {}
    assert (result.degraded, result.reason) == (False, None)
    # T022: a FetchResult *is* the Iterable[SiteRecordV1] the protocol promises.
    assert list(result) == list(result.records)
    assert isinstance(result.records[0], SiteRecordV1)


def test_every_emitted_value_is_stamped(result: base.FetchResult) -> None:
    for record in result:
        assert record.names, "a record with no name must never be emitted"
        for value in values_of(record):
            assert value.source.kind == "overture"
            assert value.source.id == record.gers_id  # GERS id == the row's `id` column
            assert value.source.license
            assert value.observed_at == OBSERVED
            assert 0.0 <= value.confidence <= 1.0


def test_license_is_read_per_record_not_from_the_theme(
    result: base.FetchResult, truth: dict[str, tuple[float, float, str]]
) -> None:
    licenses = {value.source.license for record in result for value in values_of(record)}
    # The theme default is CDLA-Permissive-2.0; assuming it would be wrong for 35 rows.
    assert licenses == {"CDLA-Permissive-2.0", "Apache-2.0", "CC0-1.0"}
    for record in result:
        assert record.gers_id is not None
        expected = truth[record.gers_id][2]
        assert {value.source.license for value in values_of(record)} == {expected}


def test_every_per_record_license_in_the_fixture_is_bundleable(
    result: base.FetchResult,
) -> None:
    """All three licenses Overture mixes into one theme reach the bundle (ADR-0012).

    This test previously asserted the opposite for `Apache-2.0` and flagged the registry
    gap it had found: the license was absent from the DATA-LICENSES.md allowlist, so the
    adapter — correctly stamping what the registry said rather than what seemed right —
    quarantined 33 of the fixture's 200 rows. Ben resolved it on 2026-08-01 by adding
    Apache-2.0 to the allowlist, since it is permissive and ODbL (share-alike, strictly
    more restrictive) was already allowed.

    The adapter is unchanged: it still reads each row's own `sources` entry and derives
    `bundleable` from the registry. Only the registry moved.
    """
    per_license = {
        value.source.license: value.bundleable for record in result for value in values_of(record)
    }
    assert per_license == {
        "CDLA-Permissive-2.0": True,
        "CC0-1.0": True,
        "Apache-2.0": True,
    }


def test_coordinates_come_from_the_fixture_and_are_never_synthesised(
    result: base.FetchResult, truth: dict[str, tuple[float, float, str]]
) -> None:
    for record in result:
        assert record.gers_id is not None
        lon, lat, _ = truth[record.gers_id]
        point = record.location.value
        assert isinstance(point, Point)
        # Exact equality: the adapter passes the source ordinates through untouched,
        # lon first (a swap would put Rhodes' latitude in the longitude slot).
        assert (point.x, point.y) == (lon, lat)
        assert AREA.covers(point)


def test_polygon_refines_the_bounding_box() -> None:
    """`fetch` honours the polygon, not just the bbox DuckDB prunes with — a diamond
    inscribed in the area has the *same* bbox, so only the shapely refinement can
    reject the rows outside the shape."""
    diamond: BaseGeometry = Polygon(
        [(28.224, 36.440), (28.232, 36.4455), (28.224, 36.451), (28.216, 36.4455)]
    )
    assert diamond.bounds == AREA.bounds
    result = OvertureAdapter(parquet=FIXTURE, observed_at=OBSERVED).fetch(diamond)
    assert 0 < len(result) < 200
    assert result.dropped[base.OUTSIDE_POLYGON] == 200 - len(result)
    assert all(diamond.covers(record.location.value) for record in result)


def test_a_row_without_a_license_is_dropped_and_counted(tmp_path: Path) -> None:
    """No resolvable source ⇒ dropped with a counted reason, never optimistically stamped."""
    unlicensed = str(tmp_path / "no_sources.parquet")
    connection = duckdb.connect()
    connection.execute("INSTALL spatial; LOAD spatial;")
    connection.execute(
        f"COPY (SELECT * REPLACE (NULL AS sources) FROM read_parquet('{FIXTURE}') LIMIT 3) "
        f"TO '{unlicensed}' (FORMAT PARQUET)"
    )
    connection.close()

    result = OvertureAdapter(parquet=unlicensed, observed_at=OBSERVED).fetch(AREA)
    assert len(result) == 0
    assert result.dropped == {base.NO_LICENSE: 3}
