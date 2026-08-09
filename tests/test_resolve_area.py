"""T032 — ``resolve_area`` as a Tier-1 unit: offline, no network, no API key.

Every source this node can consult is injected: a fake :class:`DivisionsLookup`/
:class:`Geocoder` for the policy tests, a DuckDB parquet written into ``tmp_path`` for
:class:`OvertureDivisions`, and an ``httpx.MockTransport`` for :class:`NominatimGeocoder`.
Nothing here reaches Overture or the OSMF, and nothing is committed as a binary fixture —
the parquet is hand-authored per test from WKT, so `tests/fixtures/README.md` gains no row.

The place names below are **arbitrary invented labels** and the coordinates are an
arbitrary unit square offset per area: FR-001/SC-005 say no place may be special, so the
tests deliberately name none. :func:`test_module_hardcodes_no_coordinate_literal` is the
mechanical half of that (a preview of T063's AST scan, scoped to this module).
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from shapely import box
from shapely.geometry.base import BaseGeometry

import planner.nodes.resolve_area as node
from commons.models import SourceRef
from commons.sources.osm import USER_AGENT
from planner.nodes.resolve_area import (
    AreaAmbiguous,
    AreaCandidate,
    AreaInvalid,
    AreaNotResolved,
    AreaRequest,
    AreaUnresolvable,
    DivisionsLookup,
    Geocoder,
    NominatimGeocoder,
    OvertureDivisions,
    resolve_area,
)

# Two unrelated areas, at different signs on both axes and in different scripts, used to
# prove the code path is the same for both. Neither is a real place.
AREA_A = ("Alpha Ward", 10.0, 20.0, "en")
AREA_B = ("Βῆτα Ward", -70.0, -30.0, "el")
#: A second, unrelated label for AREA_B — exercises the `names.common` branch.
ALTERNATE_B = "Second Ward"


def square(min_lon: float, min_lat: float) -> BaseGeometry:
    return box(min_lon, min_lat, min_lon + 1.0, min_lat + 1.0)


def wkt_square(min_lon: float, min_lat: float) -> str:
    return str(square(min_lon, min_lat).wkt)


def geojson(min_lon: float, min_lat: float) -> dict[str, Any]:
    ring = [
        [min_lon, min_lat],
        [min_lon + 1.0, min_lat],
        [min_lon + 1.0, min_lat + 1.0],
        [min_lon, min_lat + 1.0],
        [min_lon, min_lat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def candidate(name: str, polygon: BaseGeometry, confidence: float = 1.0) -> AreaCandidate:
    return AreaCandidate(
        name=name,
        polygon=polygon,
        source=SourceRef(kind="overture", id=f"division/{name}", license="ODbL-1.0"),
        confidence=confidence,
    )


@dataclass
class FakeLookup:
    """Stands in for either protocol — they are structurally identical by design."""

    results: tuple[AreaCandidate, ...] = ()
    calls: list[str] = field(default_factory=list)

    def search(self, name: str) -> Sequence[AreaCandidate]:
        self.calls.append(name)
        return self.results


class ExplodingLookup:
    def search(self, name: str) -> Sequence[AreaCandidate]:
        raise AssertionError(f"this source must not be consulted (was asked for {name!r})")


def test_fakes_satisfy_the_injected_protocols() -> None:
    assert isinstance(FakeLookup(), DivisionsLookup)
    assert isinstance(FakeLookup(), Geocoder)


# --- user-supplied geometry ------------------------------------------------


def test_bbox_resolves_to_that_box_stamped_as_the_users_own() -> None:
    resolved = resolve_area(AreaRequest(bbox=(10.0, 20.0, 11.0, 21.0)))
    assert resolved.polygon.equals(square(10.0, 20.0))
    assert resolved.polygon.geom_type == "Polygon"
    assert resolved.source.kind == "user"
    assert resolved.source.license == node.USER_LICENSE
    assert resolved.candidates == ()


def test_geojson_polygon_is_returned_as_is() -> None:
    resolved = resolve_area(AreaRequest(polygon=geojson(-70.0, -30.0)))
    assert resolved.polygon.equals(square(-70.0, -30.0))
    assert resolved.source.kind == "user"


def test_geojson_multipolygon_is_accepted() -> None:
    request = AreaRequest(
        polygon={
            "type": "MultiPolygon",
            "coordinates": [
                geojson(10.0, 20.0)["coordinates"],
                geojson(-70.0, -30.0)["coordinates"],
            ],
        }
    )
    resolved = resolve_area(request)
    assert resolved.polygon.geom_type == "MultiPolygon"
    assert len(resolved.polygon.geoms) == 2


def test_a_transposed_bbox_is_caught_by_the_axis_assertion() -> None:
    """[minLon, minLat, ...] given lat-first: 121 is a fine longitude and no latitude.

    The honest bbox moved one degree west at T008 and the reason is worth recording: it used
    to sit at 122°E/24°N, which is **open sea**, and an area over open sea now has no local
    frame and is refused (`commons/frame.py`). The arbitrary-coordinate ethos of this module
    survives — no place is named and nothing branches on where this is — but a coordinate
    pair that resolves an area has to fall on land now, because a country code is derived
    from it. That is the derivation working, not the test being made to pass.
    """
    honest = (121.0, 24.0, 121.5, 24.5)
    assert resolve_area(AreaRequest(bbox=honest)).polygon.bounds == honest
    transposed = (honest[1], honest[0], honest[3], honest[2])
    with pytest.raises(AreaInvalid, match="latitude"):
        resolve_area(AreaRequest(bbox=transposed))


def test_a_transposed_bbox_that_stays_in_range_is_caught_by_the_ordering_assertion() -> None:
    """Both axes in range after the swap — only min<max per axis still catches it."""
    with pytest.raises(AreaInvalid, match="transposed"):
        resolve_area(AreaRequest(bbox=(20.0, 10.0, 20.5, 11.0)[::-1]))


@pytest.mark.parametrize(
    "bbox",
    [
        (10.0, 20.0, 10.0, 21.0),  # zero width
        (10.0, 20.0, 11.0, 20.0),  # zero height
        (11.0, 20.0, 10.0, 21.0),  # inverted longitude
        (10.0, 20.0, 11.0),  # not four values
        (10.0, 20.0, 181.0, 21.0),  # longitude out of range
    ],
)
def test_degenerate_or_malformed_bbox_is_invalid(bbox: tuple[float, ...]) -> None:
    with pytest.raises(AreaInvalid):
        resolve_area(AreaRequest(bbox=bbox))  # type: ignore[arg-type]


def test_self_intersecting_polygon_is_invalid() -> None:
    bowtie = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]],
    }
    with pytest.raises(AreaInvalid, match="self-intersecting|invalid"):
        resolve_area(AreaRequest(polygon=bowtie))


def test_zero_area_polygon_is_invalid() -> None:
    sliver = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [0.0, 0.0]]],
    }
    with pytest.raises(AreaInvalid):
        resolve_area(AreaRequest(polygon=sliver))


def test_a_point_is_not_an_area() -> None:
    with pytest.raises(AreaInvalid, match="Polygon or MultiPolygon"):
        resolve_area(AreaRequest(polygon={"type": "Point", "coordinates": [10.0, 20.0]}))


def test_an_empty_request_is_invalid() -> None:
    with pytest.raises(AreaInvalid, match="none was given"):
        resolve_area(AreaRequest())
    with pytest.raises(AreaInvalid, match="none was given"):
        resolve_area(AreaRequest(name="   "))


def test_explicit_geometry_wins_over_a_name() -> None:
    """polygon → bbox → name: a drawn ring is never re-guessed by a lookup."""
    request = AreaRequest(
        name=AREA_A[0], bbox=(10.0, 20.0, 11.0, 21.0), polygon=geojson(-70.0, -30.0)
    )
    resolved = resolve_area(request, divisions=ExplodingLookup(), geocoder=ExplodingLookup())
    assert resolved.polygon.equals(square(-70.0, -30.0))

    without_polygon = AreaRequest(name=AREA_A[0], bbox=(10.0, 20.0, 11.0, 21.0))
    bbox_resolved = resolve_area(
        without_polygon, divisions=ExplodingLookup(), geocoder=ExplodingLookup()
    )
    assert bbox_resolved.polygon.equals(square(10.0, 20.0))


# --- name resolution policy ------------------------------------------------


def test_one_division_hit_resolves_and_the_geocoder_is_never_asked() -> None:
    divisions = FakeLookup((candidate(AREA_A[0], square(10.0, 20.0)),))
    resolved = resolve_area(
        AreaRequest(name=AREA_A[0]), divisions=divisions, geocoder=ExplodingLookup()
    )
    assert resolved.polygon.equals(square(10.0, 20.0))
    assert resolved.source.kind == "overture"
    assert divisions.calls == [AREA_A[0]]


def test_several_hits_are_ambiguous_and_carry_the_candidates() -> None:
    hits = (candidate(AREA_A[0], square(10.0, 20.0)), candidate(AREA_A[0], square(-70.0, -30.0)))
    with pytest.raises(AreaAmbiguous) as raised:
        resolve_area(AreaRequest(name=AREA_A[0]), divisions=FakeLookup(hits))
    assert raised.value.candidates == hits
    assert isinstance(raised.value, AreaNotResolved | LookupError)


def test_implausible_candidates_never_resolve_and_never_disambiguate() -> None:
    noise = (candidate(AREA_A[0], square(10.0, 20.0), confidence=0.1),)
    with pytest.raises(AreaUnresolvable) as raised:
        resolve_area(AreaRequest(name=AREA_A[0]), divisions=FakeLookup(noise))
    assert raised.value.candidates == noise


def test_zero_division_hits_falls_through_to_the_geocoder() -> None:
    divisions = FakeLookup(())
    geocoder = FakeLookup((candidate(AREA_B[0], square(-70.0, -30.0)),))
    resolved = resolve_area(AreaRequest(name=AREA_B[0]), divisions=divisions, geocoder=geocoder)
    assert resolved.polygon.equals(square(-70.0, -30.0))
    assert divisions.calls == [AREA_B[0]] and geocoder.calls == [AREA_B[0]]


def test_zero_hits_from_both_is_unresolvable() -> None:
    with pytest.raises(AreaUnresolvable) as raised:
        resolve_area(AreaRequest(name=AREA_B[0]), divisions=FakeLookup(), geocoder=FakeLookup())
    assert raised.value.candidates == ()


def test_the_winner_still_reports_what_else_was_considered() -> None:
    hits = (
        candidate(AREA_A[0], square(10.0, 20.0)),
        candidate(f"{AREA_A[0]} outskirts", square(-70.0, -30.0), confidence=0.45),
    )
    resolved = resolve_area(AreaRequest(name=AREA_A[0]), divisions=FakeLookup(hits))
    assert resolved.polygon.equals(square(10.0, 20.0))
    assert resolved.candidates == hits


# --- OvertureDivisions, offline against a hand-authored parquet -------------


DivisionRow = tuple[str, str, dict[str, str], str | None, str]


def write_divisions(path: Path, rows: Sequence[DivisionRow], *, bbox: bool = True) -> str:
    """Write a minimal ``divisions/division_area`` parquet.

    ``id``, ``names``, ``sources``, ``geometry`` and — like the real theme, and unlike the
    first version of this helper — the ``bbox`` struct the resolver's two-pass read is
    pruned by, derived from the geometry rather than typed (there is no place in a fixture
    for a hand-written coordinate). ``bbox=False`` writes the older, narrower shape so a
    source that lacks the column can be exercised deliberately.
    """
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL spatial; LOAD spatial;")
        connection.execute(
            "CREATE TABLE division_area (id VARCHAR, "
            'names STRUCT("primary" VARCHAR, common MAP(VARCHAR, VARCHAR)), '
            "sources STRUCT(property VARCHAR, license VARCHAR)[], geometry GEOMETRY"
            + (
                ", bbox STRUCT(xmin DOUBLE, xmax DOUBLE, ymin DOUBLE, ymax DOUBLE))"
                if bbox
                else ")"
            )
        )
        for division_id, primary, common, license_id, wkt in rows:
            connection.execute(
                "INSERT INTO division_area VALUES ($id, "
                "{'primary': $primary, 'common': MAP($keys::VARCHAR[], $values::VARCHAR[])}, "
                "CASE WHEN $license IS NULL THEN [] "
                "ELSE [{'property': '', 'license': $license}] END, ST_GeomFromText($wkt)"
                + (
                    ", {'xmin': ST_XMin(ST_GeomFromText($wkt)), "
                    "'xmax': ST_XMax(ST_GeomFromText($wkt)), "
                    "'ymin': ST_YMin(ST_GeomFromText($wkt)), "
                    "'ymax': ST_YMax(ST_GeomFromText($wkt))})"
                    if bbox
                    else ")"
                ),
                {
                    "id": division_id,
                    "primary": primary,
                    "keys": list(common),
                    "values": list(common.values()),
                    "license": license_id,
                    "wkt": wkt,
                },
            )
        connection.execute(f"COPY division_area TO '{path}' (FORMAT PARQUET)")
    finally:
        connection.close()
    return str(path)


@pytest.fixture
def divisions_parquet(tmp_path: Path) -> str:
    return write_divisions(
        tmp_path / "division_area.parquet",
        [
            ("division/a", AREA_A[0], {AREA_A[3]: AREA_A[0]}, "ODbL-1.0", wkt_square(10.0, 20.0)),
            (
                "division/b",
                AREA_B[0],
                {AREA_A[3]: ALTERNATE_B},
                "ODbL-1.0",
                wkt_square(-70.0, -30.0),
            ),
            ("division/c", f"{AREA_A[0]} Extension", {}, None, wkt_square(11.0, 21.0)),
        ],
    )


def test_overture_divisions_reads_a_local_parquet_and_stamps_provenance(
    divisions_parquet: str,
) -> None:
    hits = OvertureDivisions(parquet=divisions_parquet).search(AREA_A[0])
    exact = [hit for hit in hits if hit.confidence == node.EXACT_CONFIDENCE]
    assert [hit.name for hit in exact] == [AREA_A[0]]
    stamp = exact[0].source
    assert (stamp.kind, stamp.id) == ("overture", "division/a")
    assert stamp.license == "ODbL-1.0"
    assert stamp.attribution == node.DIVISIONS_ATTRIBUTION
    assert exact[0].polygon.equals(square(10.0, 20.0))


def test_overture_divisions_drops_a_row_that_states_no_license(divisions_parquet: str) -> None:
    """Never stamp a guessed license — an unlicensed row is simply not a candidate."""
    hits = OvertureDivisions(parquet=divisions_parquet).search(f"{AREA_A[0]} Extension")
    assert "division/c" not in {hit.source.id for hit in hits}


def test_overture_divisions_matches_an_alternate_name(divisions_parquet: str) -> None:
    hits = OvertureDivisions(parquet=divisions_parquet).search(ALTERNATE_B)
    assert [hit.source.id for hit in hits] == ["division/b"]
    # The candidate is labelled with the division's own primary name, not the query.
    assert hits[0].name == AREA_B[0]


def test_overture_divisions_folds_case_script_and_composition(divisions_parquet: str) -> None:
    """The regression that caught DuckDB `lower` ≠ Python `casefold` on precomposed Greek."""
    hits = OvertureDivisions(parquet=divisions_parquet).search(f"  {AREA_B[0].upper()} ")
    assert [hit.source.id for hit in hits] == ["division/b"]
    assert hits[0].confidence == node.EXACT_CONFIDENCE


def test_overture_divisions_returns_nothing_for_an_unknown_name(divisions_parquet: str) -> None:
    assert OvertureDivisions(parquet=divisions_parquet).search("no such area anywhere") == ()
    assert OvertureDivisions(parquet=divisions_parquet).search("   ") == ()


def test_resolve_area_end_to_end_over_the_local_parquet(divisions_parquet: str) -> None:
    resolved = resolve_area(
        AreaRequest(name=AREA_B[0]),
        divisions=OvertureDivisions(parquet=divisions_parquet),
        geocoder=ExplodingLookup(),
    )
    assert resolved.polygon.equals(square(-70.0, -30.0))
    assert resolved.source.attribution == node.DIVISIONS_ATTRIBUTION


# --- DU-03: the two passes, the window, and the deadline -------------------
#
# The defect these pin: one statement matched on `names` *and* projected `ST_AsWKB(geometry)`
# over the hosted release, where `geometry` is 97.5 % of the theme's 4.47 GB and no name
# column carries row-group statistics — so answering "which rows match?" dragged the whole
# global theme across the wire. `POST /areas` with a name took **212 s** measured; the
# browser tab froze. Matching now reads the narrow columns and the geometry of the survivors
# is fetched afterwards, by id, pruned by the only statistics the theme publishes.


def test_the_matching_pass_never_reads_the_geometry_column() -> None:
    """The regression guard on the defect itself, and it is about *bytes*, not results.

    A name predicate can prune nothing on this theme (only ``bbox`` is indexed), so every
    column the matching statement projects is read for the entire release. Putting geometry
    back into it is a 97.5 %-of-4.47 GB mistake that no behavioural test would notice — the
    answers would be identical, just minutes later.
    """
    assert "geometry" not in node._DIVISIONS_QUERY, (
        "the matching pass is projecting geometry again — that is the whole DU-03 defect"
    )
    assert "geometry" not in node._DIVISIONS_QUERY_IN_WINDOW
    assert "geometry" in node._GEOMETRY_QUERY, "pass 2 must be the one that reads geometry"
    # …and pass 2 must stay pruned by the indexed column, or it reads the whole theme too.
    assert "bbox.xmin" in node._GEOMETRY_QUERY and "bbox.ymin" in node._GEOMETRY_QUERY


def test_both_passes_together_still_produce_the_stamped_candidate(divisions_parquet: str) -> None:
    """Splitting the read must not split the record: name, polygon and stamp travel together."""
    hits = OvertureDivisions(parquet=divisions_parquet).search(AREA_B[0])
    assert [hit.source.id for hit in hits] == ["division/b"]
    assert hits[0].polygon.equals(square(-70.0, -30.0))
    assert hits[0].source.license == "ODbL-1.0"
    assert hits[0].confidence == node.EXACT_CONFIDENCE


@pytest.mark.parametrize("area", [AREA_A, AREA_B])
def test_a_caller_supplied_window_bounds_the_search_without_naming_a_place(
    area: tuple[str, float, float, str], divisions_parquet: str
) -> None:
    """The lever that makes this interactive — and it is an argument, never a constant.

    Both areas are found by *their own* window and by neither the other's, through one code
    path with nothing to branch on (FR-001/SC-005). The windows are built from the fixture's
    coordinates here for exactly the reason the module may not hold any: they are data.
    """
    name, min_lon, min_lat, _language = area
    other = AREA_B if area == AREA_A else AREA_A
    around = (min_lon - 0.5, min_lat - 0.5, min_lon + 1.5, min_lat + 1.5)
    elsewhere = (other[1] - 0.5, other[2] - 0.5, other[1] + 1.5, other[2] + 1.5)

    inside = OvertureDivisions(parquet=divisions_parquet, window=around).search(name)
    assert [hit.polygon.bounds for hit in inside] == [square(min_lon, min_lat).bounds]
    assert OvertureDivisions(parquet=divisions_parquet, window=elsewhere).search(name) == ()
    # …and the unwindowed lookup still finds it: the window bounds the read, not the meaning.
    assert [hit.source.id for hit in inside] == [
        hit.source.id
        for hit in OvertureDivisions(parquet=divisions_parquet).search(name)
        if hit.polygon.bounds == square(min_lon, min_lat).bounds
    ]


def test_a_lookup_past_its_deadline_raises_instead_of_reporting_no_such_area(
    divisions_parquet: str,
) -> None:
    """A timeout is a fact about the *source*, never about the place (FAIL-005's lesson).

    ``timeout=0`` is the deterministic, offline way to reach the deadline branch: no clock
    to race, no fixture big enough to be slow, no network. The classification is what
    matters — this must not be catchable as either of the outcomes the API turns into a
    ``404`` or a ``422``, or an unreachable source becomes a confident "no such place".
    """
    lookup = OvertureDivisions(parquet=divisions_parquet, timeout=0.0)
    with pytest.raises(node.AreaLookupTimeout) as raised:
        lookup.search(AREA_A[0])
    assert isinstance(raised.value, TimeoutError)
    assert not isinstance(raised.value, (AreaNotResolved, AreaInvalid))
    assert "divisions" in str(raised.value)


def test_a_divisions_timeout_propagates_and_the_fallback_is_not_asked(
    divisions_parquet: str,
) -> None:
    """`resolve_area` already refuses to let a divisions failure become a `404`; a slow
    authoritative source is the same failure with a clock on it, so Nominatim — which
    answers a *different* question — must not be consulted to paper over it."""
    with pytest.raises(node.AreaLookupTimeout):
        resolve_area(
            AreaRequest(name=AREA_A[0]),
            divisions=OvertureDivisions(parquet=divisions_parquet, timeout=0.0),
            geocoder=ExplodingLookup(),
        )


def test_read_concurrency_is_raised_for_the_release_and_left_alone_for_an_extract(
    divisions_parquet: str,
) -> None:
    """``read_threads`` is request concurrency for a latency-bound remote scan, so it is
    wrong for a local file — where the same number would just oversubscribe the CPU."""
    plain = duckdb.connect()
    try:
        default = plain.execute("SELECT current_setting('threads')").fetchone()
    finally:
        plain.close()
    assert default is not None

    local = OvertureDivisions(parquet=divisions_parquet, read_threads=default[0] + 7)._connect()
    try:
        assert local.execute("SELECT current_setting('threads')").fetchone() == default
    finally:
        local.close()


def test_a_divisions_source_without_the_bbox_column_fails_loudly(tmp_path: Path) -> None:
    """Both passes read ``bbox``. A source that lacks it is unusable, and an unusable
    authoritative source must raise — never quietly answer "no such area"."""
    parquet = write_divisions(
        tmp_path / "no_bbox.parquet",
        [("division/a", AREA_A[0], {}, "ODbL-1.0", wkt_square(10.0, 20.0))],
        bbox=False,
    )
    with pytest.raises(duckdb.Error):
        OvertureDivisions(parquet=parquet).search(AREA_A[0])


# --- NominatimGeocoder, offline against a mocked transport -----------------


def nominatim_result(name: str, min_lon: float, min_lat: float, **extra: Any) -> dict[str, Any]:
    return {
        "osm_type": "relation",
        "osm_id": 12345,
        "name": name,
        "display_name": f"{name}, Some Region",
        "geojson": geojson(min_lon, min_lat),
        **extra,
    }


def geocoder_over(payload: Any, seen: list[httpx.Request] | None = None) -> NominatimGeocoder:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}
    )
    return NominatimGeocoder(client=client)


def test_nominatim_returns_a_stamped_odbl_candidate() -> None:
    seen: list[httpx.Request] = []
    hits = geocoder_over([nominatim_result(AREA_A[0], 10.0, 20.0)], seen).search(AREA_A[0])
    assert len(hits) == 1
    assert hits[0].polygon.equals(square(10.0, 20.0))
    assert hits[0].source.kind == "osm"
    assert hits[0].source.id == "relation/12345"
    assert hits[0].source.license == "ODbL-1.0"
    assert hits[0].source.attribution == "© OpenStreetMap contributors"
    # OSMF policy: exactly one request, an honest User-Agent, the query passed through.
    assert len(seen) == 1
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert seen[0].url.params["q"] == AREA_A[0]


def test_nominatim_skips_results_that_are_not_areas() -> None:
    point = nominatim_result(AREA_A[0], 10.0, 20.0)
    point["geojson"] = {"type": "Point", "coordinates": [10.0, 20.0]}
    assert geocoder_over([point]).search(AREA_A[0]) == ()


def test_nominatim_unavailable_is_no_worse_than_no_fallback() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("nominatim unreachable")

    geocoder = NominatimGeocoder(client=httpx.Client(transport=httpx.MockTransport(failing)))
    with pytest.raises(AreaUnresolvable):
        resolve_area(AreaRequest(name=AREA_A[0]), divisions=FakeLookup(), geocoder=geocoder)


def test_nominatim_only_disambiguates_what_divisions_could_not_answer(
    divisions_parquet: str,
) -> None:
    seen: list[httpx.Request] = []
    resolved = resolve_area(
        AreaRequest(name=AREA_A[0]),
        divisions=OvertureDivisions(parquet=divisions_parquet),
        geocoder=geocoder_over([nominatim_result(AREA_A[0], -70.0, -30.0)], seen),
    )
    assert resolved.source.kind == "overture"
    assert seen == []


# --- genericity (FR-001 / SC-005) -----------------------------------------


@pytest.mark.parametrize("area", [AREA_A, AREA_B])
def test_two_unrelated_areas_take_the_same_path(
    area: tuple[str, float, float, str], divisions_parquet: str
) -> None:
    """Same node, same lookup, no branch on the name: only the request differs."""
    name, min_lon, min_lat, _language = area
    by_name = resolve_area(
        AreaRequest(name=name), divisions=OvertureDivisions(parquet=divisions_parquet)
    )
    by_bbox = resolve_area(AreaRequest(bbox=(min_lon, min_lat, min_lon + 1.0, min_lat + 1.0)))
    by_polygon = resolve_area(AreaRequest(polygon=geojson(min_lon, min_lat)))
    assert by_name.polygon.equals(by_bbox.polygon)
    assert by_bbox.polygon.equals(by_polygon.polygon)
    assert (by_name.source.kind, by_bbox.source.kind) == ("overture", "user")


def test_module_hardcodes_no_coordinate_literal() -> None:
    """A preview of T063's scan: no lat/lon pair or bbox may be frozen into this node."""
    tree = ast.parse(Path(node.__file__).read_text(encoding="utf-8"))
    frozen = [
        ast.unparse(literal)
        for literal in ast.walk(tree)
        if isinstance(literal, (ast.List, ast.Tuple))
        and len(literal.elts) in (2, 4)
        and all(
            isinstance(element, ast.Constant) and isinstance(element.value, (int, float))
            for element in literal.elts
        )
    ]
    assert frozen == [], f"coordinate-shaped literal(s) in {Path(node.__file__).name}: {frozen}"


# --- FAIL-005 residuals: the prefilter must never drop a row the scorer accepts ---------
#
# The DuckDB prefilter is a *gate*: a row it does not return is a row `_match_confidence`
# never sees, so a fold the two engines disagree on is a silently empty answer rather than
# a low score. These go through the real parquet, not a fake lookup, because the gate only
# exists in SQL. Each label below is an invented name carrying one letter where Unicode's
# **full** case folding and a database's **simple** `lower()` used to part company.

#: Ends in a final sigma (ς) — the letter half of Greek place names end in.
FOLD_AREA_SIGMA = "Δέλτας Ward"
#: Carries U+0130 — the letter whose *lowercase* depends on how it was composed.
FOLD_AREA_DOTTED = "İota Ward"
#: Carries ß, which full folding expands to `ss` and `lower()` leaves alone.
FOLD_AREA_SHARP = "Straße Ward"


@pytest.fixture
def folding_parquet(tmp_path: Path) -> str:
    return write_divisions(
        tmp_path / "folding.parquet",
        [
            ("division/sigma", FOLD_AREA_SIGMA, {}, "ODbL-1.0", wkt_square(30.0, 40.0)),
            ("division/dotted", FOLD_AREA_DOTTED, {}, "ODbL-1.0", wkt_square(31.0, 41.0)),
            ("division/sharp", FOLD_AREA_SHARP, {}, "ODbL-1.0", wkt_square(32.0, 42.0)),
        ],
    )


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        # FAIL-005 residual 1: full vs simple case folding. A user typing a medial sigma
        # scored 1.0 in Python and was dropped by the SQL prefilter before anyone looked.
        ("Δέλτασ Ward", "division/sigma"),
        ("ΔΈΛΤΑΣ WARD", "division/sigma"),
        ("STRASSE Ward", "division/sharp"),
        # FAIL-005 residual 2: `nfc_normalize(lower(x))` normalized in the wrong order, so
        # a stored NFC name and a decomposed query folded to different strings.
        ("İota Ward", "division/dotted"),
        ("İOTA WARD", "division/dotted"),
        ("Iota Ward", "division/dotted"),
    ],
)
def test_the_prefilter_returns_every_row_the_scorer_would_score_exactly(
    typed: str, expected: str, folding_parquet: str
) -> None:
    """Each spelling reaches the scorer *and* scores 1.0 — one fold, not two."""
    hits = OvertureDivisions(parquet=folding_parquet).search(typed)
    assert [hit.source.id for hit in hits] == [expected], (
        f"the DuckDB prefilter dropped {typed!r} — the SQL arm of the fold disagrees with "
        "the Python arm again (FAIL-005)"
    )
    assert hits[0].confidence == node.EXACT_CONFIDENCE


@pytest.mark.parametrize("stored", [FOLD_AREA_SIGMA, FOLD_AREA_DOTTED, FOLD_AREA_SHARP])
def test_the_two_arms_of_the_fold_agree_on_the_names_in_the_parquet(
    stored: str, folding_parquet: str
) -> None:
    """The gate and the scorer read the same key — asserted against a real DuckDB read.

    Stated separately from the search above because it is the *invariant*, not a symptom:
    if these two strings ever differ again, some other spelling is being dropped silently.
    """
    connection = duckdb.connect()
    try:
        row = connection.execute(f"SELECT {node._fold_sql('$v')}", {"v": stored}).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row[0] == node._normalize(stored)
