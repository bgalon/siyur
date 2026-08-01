"""Property tests — EPSG:4326 geometry validation (`commons/geo.py`), T013 of Spec 001.

Three properties, checked with `hypothesis` over generated coordinates (data-model §5.3):

1. **Round trip** — any in-range (lon, lat) survives `Point` ↔ GeoJSON unchanged.
2. **Out of range is refused** — lon ∉ [-180, 180] or lat ∉ [-90, 90] raises, never clamps.
3. **The axis order is never silently swapped** — (lon, lat), longitude first, everywhere;
   a swapped pair either lands on the same coordinates it was given or fails loudly.

Shapely 2.x API only (`.geom_type`, `unary_union`) — see the AGENTS.md "Geo APIs" table.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from shapely import Point, unary_union

from commons.geo import (
    MAX_LAT,
    MAX_LON,
    MIN_LAT,
    MIN_LON,
    GeometryError,
    Wgs84Point,
    coerce_point,
    point_from_geojson,
    point_from_lonlat,
    point_to_geojson,
    validate_lat,
    validate_lon,
    validate_point,
)
from commons.models import SiteRecordV1, SourcedValue, SourceRef

lons = st.floats(min_value=MIN_LON, max_value=MAX_LON, allow_nan=False, allow_infinity=False)
lats = st.floats(min_value=MIN_LAT, max_value=MAX_LAT, allow_nan=False, allow_infinity=False)

out_of_range_lons = st.one_of(
    st.floats(min_value=MAX_LON, max_value=1e6, exclude_min=True, allow_nan=False),
    st.floats(min_value=-1e6, max_value=MIN_LON, exclude_max=True, allow_nan=False),
)
out_of_range_lats = st.one_of(
    st.floats(min_value=MAX_LAT, max_value=1e6, exclude_min=True, allow_nan=False),
    st.floats(min_value=-1e6, max_value=MIN_LAT, exclude_max=True, allow_nan=False),
)

# Longitudes that cannot possibly be a latitude — the swap detector.
unambiguous_lons = st.one_of(
    st.floats(min_value=MAX_LAT, max_value=MAX_LON, exclude_min=True, allow_nan=False),
    st.floats(min_value=MIN_LON, max_value=MIN_LAT, exclude_max=True, allow_nan=False),
)

OSM = SourceRef(kind="osm", id="node/123456", license="ODbL-1.0")


# --- 1. round trip ---------------------------------------------------------------


@given(lon=lons, lat=lats)
def test_generated_coordinates_round_trip(lon: float, lat: float) -> None:
    point = point_from_lonlat(lon, lat)
    assert (point.x, point.y) == (lon, lat)

    geojson = point_to_geojson(point)
    assert geojson == {"type": "Point", "coordinates": [lon, lat]}

    reparsed = point_from_geojson(geojson)
    assert (reparsed.x, reparsed.y) == (lon, lat)
    assert reparsed.equals(point)


@given(lon=lons, lat=lats)
def test_round_trip_through_a_stamped_model(lon: float, lat: float) -> None:
    """The same guarantee through the pydantic seam a `SiteRecordV1.location` uses."""
    location = SourcedValue[Wgs84Point].stamp(
        value=Point(lon, lat), source=OSM, confidence=0.7, observed_at=date(2026, 7, 20)
    )
    record = SiteRecordV1(location=location)
    dumped = record.model_dump(mode="json")
    assert dumped["location"]["value"] == {"type": "Point", "coordinates": [lon, lat]}

    restored = SiteRecordV1.model_validate(dumped)
    assert (restored.location.value.x, restored.location.value.y) == (lon, lat)


@given(lon=lons, lat=lats)
def test_accepted_input_shapes_agree(lon: float, lat: float) -> None:
    point = point_from_lonlat(lon, lat)
    for accepted in (point, {"type": "Point", "coordinates": [lon, lat]}, (lon, lat), [lon, lat]):
        coerced = coerce_point(accepted)
        assert (coerced.x, coerced.y) == (lon, lat)


# --- 2. out of range is refused --------------------------------------------------


@given(lon=out_of_range_lons, lat=lats)
def test_out_of_range_longitude_is_rejected(lon: float, lat: float) -> None:
    with pytest.raises(GeometryError):
        validate_lon(lon)
    with pytest.raises(GeometryError):
        point_from_lonlat(lon, lat)
    with pytest.raises(GeometryError):
        coerce_point({"type": "Point", "coordinates": [lon, lat]})
    with pytest.raises(GeometryError):
        validate_point(Point(lon, lat))


@given(lon=lons, lat=out_of_range_lats)
def test_out_of_range_latitude_is_rejected(lon: float, lat: float) -> None:
    with pytest.raises(GeometryError):
        validate_lat(lat)
    with pytest.raises(GeometryError):
        point_from_lonlat(lon, lat)
    with pytest.raises(GeometryError):
        coerce_point(Point(lon, lat))


@given(lon=out_of_range_lons, lat=out_of_range_lats)
def test_out_of_range_coordinates_never_reach_a_record(lon: float, lat: float) -> None:
    with pytest.raises(ValidationError):
        SiteRecordV1.model_validate(
            {
                "location": {
                    "value": {"type": "Point", "coordinates": [lon, lat]},
                    "source": OSM.model_dump(),
                    "bundleable": True,
                    "confidence": 0.7,
                    "observed_at": "2026-07-20",
                }
            }
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_coordinates_are_rejected(bad: float) -> None:
    with pytest.raises(GeometryError):
        point_from_lonlat(bad, 0.0)
    with pytest.raises(GeometryError):
        point_from_lonlat(0.0, bad)


@pytest.mark.parametrize("bad", ["28.2247", None, True, {"lon": 1}])
def test_non_numeric_coordinates_are_rejected(bad: object) -> None:
    with pytest.raises(GeometryError):
        point_from_lonlat(bad, 0.0)


# --- 3. (lon, lat) ordering is never silently swapped ----------------------------


@given(lon=unambiguous_lons, lat=lats)
def test_swapped_pair_is_refused_not_reinterpreted(lon: float, lat: float) -> None:
    """|lon| > 90, so feeding the pair the other way round cannot silently succeed."""
    point = point_from_lonlat(lon, lat)
    assert point.x == lon
    assert point.y == lat

    with pytest.raises(GeometryError):
        point_from_lonlat(lat, lon)
    with pytest.raises(GeometryError):
        coerce_point({"type": "Point", "coordinates": [lat, lon]})
    with pytest.raises(GeometryError):
        coerce_point((lat, lon))


@given(lon=lons, lat=lats)
def test_geojson_coordinates_are_read_longitude_first(lon: float, lat: float) -> None:
    parsed = point_from_geojson({"type": "Point", "coordinates": [lon, lat]})
    assert parsed.x == lon
    assert parsed.y == lat


# --- geometry type / shape guards ------------------------------------------------


def test_only_points_are_accepted() -> None:
    # shapely 2.x `unary_union` (1.x `cascaded_union` is gone — AGENTS.md geo table)
    multipoint = unary_union([Point(28.2247, 36.4443), Point(28.2235, 36.4451)])
    assert multipoint.geom_type == "MultiPoint"
    with pytest.raises(GeometryError, match="MultiPoint"):
        coerce_point(multipoint)


def test_empty_and_three_dimensional_points_are_rejected() -> None:
    with pytest.raises(GeometryError, match="empty"):
        coerce_point(Point())
    with pytest.raises(GeometryError, match="2-D"):
        coerce_point(Point(28.2247, 36.4443, 12.0))
    with pytest.raises(GeometryError):
        coerce_point({"type": "Point", "coordinates": [28.2247, 36.4443, 12.0]})


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        {"type": "Point"},
        {"type": "Point", "coordinates": "28.2247,36.4443"},
        {"coordinates": [28.2247, 36.4443]},
        (28.2247,),
        "POINT (28.2247 36.4443)",
        28.2247,
        None,
    ],
)
def test_non_point_input_is_rejected(bad: object) -> None:
    with pytest.raises(GeometryError):
        coerce_point(bad)
