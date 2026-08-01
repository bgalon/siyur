"""Geometry validation — EPSG:4326 (lon, lat), Point only. Shapely 2.x API only.

Site geometry is a **Point in EPSG:4326 with (lon, lat) ordering** (`docs/data/poi-site.md`
"CRS"; PostGIS ``geometry(Point,4326)``). Two failure modes this module exists to make
impossible:

1. **Axis swap.** GeoJSON/PostGIS are (lon, lat); many APIs and every human are (lat, lon).
   Every entry point here takes ``lon`` first, *names* it, and range-checks each axis
   against its own bounds — so a swapped pair with ``|lon| > 90`` fails loudly rather than
   landing in the sea. ``tests/test_geo.py`` property-tests that the swap is never silent.
2. **Model-emitted coordinates.** FR-005 / AGENTS.md: coordinates come from authoritative
   geodata and spatial arithmetic runs in PostGIS/shapely — never from an LLM. This module
   is the validation boundary geodata crosses on the way in.

Shapely pin is ``~=2.1``: use ``.geom_type`` / ``unary_union`` — never 1.x ``.type`` /
``cascaded_union`` (see the AGENTS.md "Geo APIs" table and ``tests/test_geo_api_pins.py``).

:data:`Wgs84Point` is the pydantic-usable form of the same rules: it validates from a
shapely ``Point``, a GeoJSON ``Point`` mapping, or a ``(lon, lat)`` pair, and serialises
back to GeoJSON.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Final

from pydantic import PlainSerializer, PlainValidator, WithJsonSchema
from shapely import Point
from shapely.geometry.base import BaseGeometry

__all__ = [
    "CRS",
    "MAX_LAT",
    "MAX_LON",
    "MIN_LAT",
    "MIN_LON",
    "GeometryError",
    "Wgs84Point",
    "coerce_point",
    "point_from_geojson",
    "point_from_lonlat",
    "point_to_geojson",
    "validate_lat",
    "validate_lon",
    "validate_point",
]

#: The one CRS the commons stores geometry in.
CRS: Final[str] = "EPSG:4326"

MIN_LON: Final[float] = -180.0
MAX_LON: Final[float] = 180.0
MIN_LAT: Final[float] = -90.0
MAX_LAT: Final[float] = 90.0


class GeometryError(ValueError):
    """Invalid geometry for the commons (wrong type, wrong CRS range, wrong axis order).

    Subclasses ``ValueError`` so pydantic reports it as a normal validation error.
    """


def _as_float(value: object, axis: str) -> float:
    """Coerce a coordinate to ``float`` without accepting junk (``bool``, ``str``, ...)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{axis} must be a number, got {type(value).__name__}: {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"{axis} must be finite, got {number!r}")
    return number


def validate_lon(lon: object) -> float:
    """Return ``lon`` as a float in [-180, 180], else raise :class:`GeometryError`."""
    number = _as_float(lon, "longitude")
    if not MIN_LON <= number <= MAX_LON:
        raise GeometryError(
            f"longitude {number!r} outside [{MIN_LON}, {MAX_LON}] ({CRS}); "
            "coordinates are (lon, lat) — is the pair swapped?"
        )
    return number


def validate_lat(lat: object) -> float:
    """Return ``lat`` as a float in [-90, 90], else raise :class:`GeometryError`."""
    number = _as_float(lat, "latitude")
    if not MIN_LAT <= number <= MAX_LAT:
        raise GeometryError(
            f"latitude {number!r} outside [{MIN_LAT}, {MAX_LAT}] ({CRS}); "
            "coordinates are (lon, lat) — is the pair swapped?"
        )
    return number


def point_from_lonlat(lon: object, lat: object) -> Point:
    """Build a validated EPSG:4326 ``Point``. **Longitude first** — always."""
    return Point(validate_lon(lon), validate_lat(lat))


def validate_point(geom: BaseGeometry) -> Point:
    """Return ``geom`` iff it is a non-empty, valid, 2-D ``Point`` inside EPSG:4326 bounds."""
    if not isinstance(geom, Point):
        # shapely 2.x: `.geom_type` (1.x `.type` is gone — AGENTS.md geo table)
        raise GeometryError(f"expected a Point geometry, got {geom.geom_type}")
    if geom.is_empty:
        raise GeometryError("expected a Point geometry, got an empty Point")
    if geom.has_z:
        raise GeometryError("expected a 2-D Point; geometry(Point,4326) carries no z")
    if not geom.is_valid:
        raise GeometryError("expected a valid Point geometry")
    validate_lon(geom.x)
    validate_lat(geom.y)
    return geom


def point_to_geojson(geom: Point) -> dict[str, Any]:
    """Serialise to a GeoJSON ``Point`` — ``coordinates`` are ``[lon, lat]`` (RFC 7946)."""
    point = validate_point(geom)
    return {"type": "Point", "coordinates": [point.x, point.y]}


def point_from_geojson(obj: Mapping[str, Any]) -> Point:
    """Parse a GeoJSON ``Point``; ``coordinates`` are read as ``[lon, lat]``, never swapped."""
    geom_type = obj.get("type")
    if geom_type != "Point":
        raise GeometryError(f"expected a GeoJSON Point, got type={geom_type!r}")
    coordinates = obj.get("coordinates")
    if isinstance(coordinates, (str, bytes)) or not isinstance(coordinates, Sequence):
        raise GeometryError(f"GeoJSON Point coordinates must be [lon, lat], got {coordinates!r}")
    if len(coordinates) != 2:
        raise GeometryError(
            f"GeoJSON Point coordinates must be exactly [lon, lat] ({CRS}, 2-D), "
            f"got {len(coordinates)} values"
        )
    return point_from_lonlat(coordinates[0], coordinates[1])


def coerce_point(value: object) -> Point:
    """Validate anything a stamped ``location`` value may arrive as into a ``Point``.

    Accepts a shapely ``Point``, a GeoJSON ``Point`` mapping, or a 2-item ``(lon, lat)``
    sequence. Everything else — including other geometry types — is rejected.
    """
    if isinstance(value, BaseGeometry):
        return validate_point(value)
    if isinstance(value, Mapping):
        return point_from_geojson(value)
    if not isinstance(value, (str, bytes)) and isinstance(value, Sequence):
        if len(value) != 2:
            raise GeometryError(
                f"a coordinate pair must be exactly (lon, lat), got {len(value)} values"
            )
        return point_from_lonlat(value[0], value[1])
    raise GeometryError(
        "expected a Point, a GeoJSON Point mapping, or a (lon, lat) pair, "
        f"got {type(value).__name__}"
    )


_POINT_JSON_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "title": "GeoJSON Point (EPSG:4326)",
    "properties": {
        "type": {"const": "Point"},
        "coordinates": {
            "type": "array",
            "description": "[lon, lat] in EPSG:4326",
            "prefixItems": [
                {"type": "number", "minimum": MIN_LON, "maximum": MAX_LON},
                {"type": "number", "minimum": MIN_LAT, "maximum": MAX_LAT},
            ],
            "minItems": 2,
            "maxItems": 2,
        },
    },
    "required": ["type", "coordinates"],
}

#: A shapely ``Point`` usable as a pydantic field: validated on the way in
#: (:func:`coerce_point`), GeoJSON on the way out (:func:`point_to_geojson`).
Wgs84Point = Annotated[
    Point,
    PlainValidator(coerce_point),
    PlainSerializer(point_to_geojson, return_type=dict[str, Any]),
    WithJsonSchema(_POINT_JSON_SCHEMA),
]
