"""``resolve_area`` — the pipeline's first node: user input → exactly one EPSG:4326 polygon.

Everything downstream (`research`, `curate`, the `ST_Within` coverage query behind
`POST /areas`) is defined over *a polygon*. This node is the single place a user's
delimitation — a free-text name, a bbox, or a drawn GeoJSON ring — becomes one, and the
single place that answers "and where did that polygon come from?".

Three properties this module exists to hold:

1. **Deterministic, never model-authored** (`planner/AGENTS.md`, FR-005). Area resolution is
   geodata work: DuckDB over Overture divisions, shapely for the geometry, a bounded HTTP
   lookup for disambiguation. No provider SDK is imported here and none may be
   (`tests/test_llm_seam.py`).
2. **Generic by construction** (FR-001 / SC-005). No place name, bbox, country or language
   appears in this file. Every code path is driven by the request; there is nothing to
   branch on. The demo area is a *test fixture*, never a default.
3. **Provenance is stamped, not assumed** (Constitution Article V). A resolved polygon
   always carries a :class:`~commons.models.SourceRef`: the Overture division id with the
   license that row *states* (read per record — the divisions theme is ODbL, but the stamp
   is read, not typed from memory), the OSM element behind a Nominatim hit with ODbL and
   its required attribution, or ``kind="user"`` for a geometry the user drew — which is the
   user's own data, not commons data, and is therefore not bundleable.

**Precedence** is explicit-geometry-first: ``polygon`` → ``bbox`` → ``name``. A user who
drew a ring means that ring; a name is the only input that needs guessing.

**Overture divisions is authoritative; Nominatim is a disambiguation fallback only** —
consulted when divisions returns nothing, one bounded request, never in a loop (the OSMF
usage policy in `DATA-LICENSES.md`).

:class:`DivisionsLookup` and :class:`Geocoder` are Protocols so both are injectable: the
node is fully testable offline and the network is never reached implicitly by a test.

Shapely pin is ``~=2.1`` — ``.geom_type`` / ``from_wkb`` / ``box``, never 1.x ``.type``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Protocol, runtime_checkable

import duckdb
import httpx
from shapely import MultiPolygon, Polygon, box, from_wkb
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry

from commons.geo import CRS, GeometryError, validate_lat, validate_lon
from commons.licenses import SourceKind, normalize_license
from commons.models import SourceRef
from commons.sources.base import bbox_of
from commons.sources.osm import ATTRIBUTION as OSM_ATTRIBUTION
from commons.sources.osm import LICENSE as OSM_LICENSE
from commons.sources.osm import USER_AGENT
from commons.sources.overture import DEFAULT_RELEASE

__all__ = [
    "DIVISIONS_ATTRIBUTION",
    "DIVISIONS_SOURCE_TEMPLATE",
    "NOMINATIM_ENDPOINT",
    "PLAUSIBLE_CONFIDENCE",
    "STRONG_CONFIDENCE",
    "USER_LICENSE",
    "AreaAmbiguous",
    "AreaCandidate",
    "AreaInvalid",
    "AreaNotResolved",
    "AreaRequest",
    "AreaUnresolvable",
    "DivisionsLookup",
    "Geocoder",
    "NominatimGeocoder",
    "OvertureDivisions",
    "ResolvedArea",
    "resolve_area",
]

#: Overture divisions release read by default — same release the places adapter pins.
DIVISIONS_SOURCE_TEMPLATE: Final = (
    "s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*"
)
#: `DATA-LICENSES.md` "Overture Maps — divisions" row: attribution is mandatory.
DIVISIONS_ATTRIBUTION: Final = "© OpenStreetMap contributors, Overture Maps Foundation"
NOMINATIM_ENDPOINT: Final = "https://nominatim.openstreetmap.org/search"
_OSM_BASE: Final = "https://www.openstreetmap.org"

#: A user's own delimitation: not commons data, never bundleable (`commons.licenses`).
USER_LICENSE: Final = "user-owned"
_USER_BBOX_ID: Final = "user:bbox"
_USER_POLYGON_ID: Final = "user:polygon"

#: Name-match strength. A single **strong** candidate resolves outright; several plausible
#: ones are a disambiguation question for the user, not a coin flip.
EXACT_CONFIDENCE: Final = 1.0
PREFIX_CONFIDENCE: Final = 0.6
SUBSTRING_CONFIDENCE: Final = 0.45
STRONG_CONFIDENCE: Final = 0.8
PLAUSIBLE_CONFIDENCE: Final = 0.4

#: Licenses whose terms require a rendered attribution string (`DATA-LICENSES.md`).
_ATTRIBUTION_REQUIRED: Final[frozenset[str]] = frozenset({"ODbL-1.0", "CC-BY-4.0", "CC-BY-SA-4.0"})
_AREAL_TYPES: Final[frozenset[str]] = frozenset({"Polygon", "MultiPolygon"})

# Coarse, symmetric containment prefilter pushed into DuckDB — a division name may contain
# the query ("Foo" ⊂ "Foo District") or the query may contain it ("Foo old town" ⊃ "Foo").
# `contains()` takes no LIKE metacharacters, so the user's string needs no escaping.
#
# **Both sides are folded in SQL, and `$needle` is passed raw**, because DuckDB's `lower`
# and Python's `str.casefold` are not the same function: casefold decomposes a precomposed
# Greek vowel (U+1FC6 → U+03B7 U+0342) where `lower` leaves it composed, so a
# Python-folded needle silently matched nothing. `nfc_normalize` on both sides is what makes
# SQL agree with :func:`_normalize`. This is only a prefilter that bounds what is read —
# `_match_confidence` re-scores every row it returns, in Python, with one folding.
_DIVISIONS_QUERY: Final = """
SELECT id,
       names.primary AS primary_name,
       coalesce(map_values(names.common), []) AS alternate_names,
       sources,
       ST_AsWKB(geometry) AS wkb
FROM read_parquet($src, hive_partitioning=1, filename=false)
WHERE contains(nfc_normalize(lower(names.primary)), nfc_normalize(lower($needle)))
   OR contains(nfc_normalize(lower($needle)), nfc_normalize(lower(names.primary)))
   OR coalesce(list_bool_or(list_transform(
        list_filter(coalesce(map_values(names.common), []), n -> length(trim(n)) > 0),
        n -> contains(nfc_normalize(lower(n)), nfc_normalize(lower($needle)))
          OR contains(nfc_normalize(lower($needle)), nfc_normalize(lower(n))))), false)
ORDER BY id
"""


@dataclass(frozen=True, slots=True)
class AreaRequest:
    """What the user asked for. ``POST /areas`` maps onto this one-to-one."""

    name: str | None = None
    #: ``[minLon, minLat, maxLon, maxLat]`` in EPSG:4326 — **lon first**, always.
    bbox: tuple[float, float, float, float] | None = None
    #: GeoJSON ``Polygon``/``MultiPolygon`` in EPSG:4326.
    polygon: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AreaCandidate:
    """One plausible reading of a name, with its geometry and where that geometry is from."""

    name: str
    polygon: BaseGeometry
    source: SourceRef
    #: Name-match strength in [0..1] — see the ``*_CONFIDENCE`` bands above.
    confidence: float


@dataclass(frozen=True, slots=True)
class ResolvedArea:
    """The node's output: one polygon, stamped."""

    polygon: BaseGeometry
    source: SourceRef
    #: Everything considered when a *name* was resolved (winner included), so a caller can
    #: still offer "did you mean…". Empty for a user-supplied geometry: nothing was guessed.
    candidates: tuple[AreaCandidate, ...] = ()


class AreaNotResolved(LookupError):
    """A name did not become one polygon. Both subclasses map to the contract's ``404``."""

    def __init__(self, message: str, candidates: Sequence[AreaCandidate] = ()) -> None:
        super().__init__(message)
        self.candidates: tuple[AreaCandidate, ...] = tuple(candidates)


class AreaAmbiguous(AreaNotResolved):
    """Several plausible matches — ``404`` **with** ``candidates`` for the user to choose."""


class AreaUnresolvable(AreaNotResolved):
    """Nothing plausible matched — ``404``; ``candidates`` may be empty."""


class AreaInvalid(ValueError):
    """No input at all, or a geometry that is not a usable area — the contract's ``422``."""


@runtime_checkable
class DivisionsLookup(Protocol):
    """Authoritative name → administrative area lookup (Overture divisions)."""

    def search(self, name: str) -> Sequence[AreaCandidate]: ...


@runtime_checkable
class Geocoder(Protocol):
    """Disambiguation fallback, consulted only when :class:`DivisionsLookup` is silent."""

    def search(self, name: str) -> Sequence[AreaCandidate]: ...


def _collapse(text: str) -> str:
    """Whitespace-collapsed, otherwise verbatim — the form sent to a source, unfolded."""
    return " ".join(text.split())


def _normalize(text: str) -> str:
    """Case-, whitespace- and composition-insensitive comparison key.

    Script-agnostic: no transliteration, no language guess (FR-001). ``NFC`` after
    ``casefold`` because casefold *decomposes* some precomposed letters — two spellings of
    the same Greek word must compare equal, and must also agree with the SQL prefilter.
    """
    return unicodedata.normalize("NFC", _collapse(text).casefold())


def _match_confidence(needle: str, names: Iterable[str | None]) -> float:
    """How strongly ``needle`` (already normalized) matches any of a candidate's names.

    Pure string comparison over whatever names the source stated — there is no place list,
    no country bias and no language assumption anywhere in it (FR-001).
    """
    best = 0.0
    for name in names:
        other = _normalize(name) if name else ""
        if not other:
            continue
        if other == needle:
            return EXACT_CONFIDENCE
        if other.startswith(needle) or needle.startswith(other):
            best = max(best, PREFIX_CONFIDENCE)
        elif needle in other or other in needle:
            best = max(best, SUBSTRING_CONFIDENCE)
    return best


def _attribution_for(license_id: str, attribution: str) -> str | None:
    """``attribution`` iff this license's terms require one — canonicalised, not guessed."""
    return attribution if normalize_license(license_id) in _ATTRIBUTION_REQUIRED else None


def _validate_area(geom: BaseGeometry) -> BaseGeometry:
    """Return ``geom`` iff it is a usable EPSG:4326 area, else raise :class:`AreaInvalid`.

    Rejects the whole family of "technically a geometry, useless as an area": a point or
    line, an empty geometry, a self-intersecting ring, a zero-area sliver, and — via
    :func:`~commons.sources.base.bbox_of` — anything whose bounds are not lon/lat in range.
    """
    if geom.geom_type not in _AREAL_TYPES:  # shapely 2.x: `.geom_type`, never 1.x `.type`
        raise AreaInvalid(f"an area must be a Polygon or MultiPolygon, got {geom.geom_type}")
    if geom.is_empty:
        raise AreaInvalid("an area must be a non-empty geometry")
    if not geom.is_valid:
        raise AreaInvalid("invalid area geometry (self-intersecting or malformed ring)")
    if geom.area <= 0.0:
        raise AreaInvalid("degenerate area geometry: it encloses no space")
    try:
        bbox_of(geom)  # axis-checked bounds: lon against [-180,180], lat against [-90,90]
    except (GeometryError, ValueError) as error:
        raise AreaInvalid(f"area geometry is not valid {CRS} (lon, lat): {error}") from error
    return geom


def _ring(coordinates: object, what: str) -> list[tuple[float, float]]:
    """One GeoJSON linear ring → validated ``(lon, lat)`` pairs. Position 0 *is* longitude."""
    if isinstance(coordinates, (str, bytes)) or not isinstance(coordinates, Sequence):
        raise AreaInvalid(f"{what} must be an array of [lon, lat] positions")
    ring: list[tuple[float, float]] = []
    for position in coordinates:
        if isinstance(position, (str, bytes)) or not isinstance(position, Sequence):
            raise AreaInvalid(f"{what} position must be [lon, lat], got {position!r}")
        if len(position) < 2:
            raise AreaInvalid(f"{what} position must be [lon, lat], got {len(position)} values")
        ring.append((validate_lon(position[0]), validate_lat(position[1])))
    return ring


def _polygon_from_geojson(obj: Mapping[str, Any]) -> BaseGeometry:
    """GeoJSON ``Polygon``/``MultiPolygon`` → shapely, every position axis-checked."""
    geom_type = obj.get("type")
    if geom_type not in _AREAL_TYPES:
        raise AreaInvalid(f"an area must be a GeoJSON Polygon or MultiPolygon, got {geom_type!r}")
    coordinates = obj.get("coordinates")
    if isinstance(coordinates, (str, bytes)) or not isinstance(coordinates, Sequence):
        raise AreaInvalid(f"GeoJSON {geom_type} needs a `coordinates` array")
    try:
        if geom_type == "Polygon":
            rings = [_ring(ring, "polygon ring") for ring in coordinates]
            if not rings:
                raise AreaInvalid("a GeoJSON Polygon needs at least an exterior ring")
            geom: BaseGeometry = Polygon(rings[0], rings[1:])
        else:
            parts = [[_ring(ring, "polygon ring") for ring in part or ()] for part in coordinates]
            if not parts or any(not part for part in parts):
                raise AreaInvalid("each GeoJSON MultiPolygon part needs an exterior ring")
            geom = MultiPolygon([(part[0], part[1:]) for part in parts])
    except (ShapelyError, TypeError) as error:
        raise AreaInvalid(f"unusable GeoJSON {geom_type}: {error}") from error
    return _validate_area(geom)


def _polygon_from_bbox(bbox: Sequence[float]) -> BaseGeometry:
    """``[minLon, minLat, maxLon, maxLat]`` → a box, with the axes asserted per slot.

    Each slot is validated against **its own** axis, so a lat/lon-transposed bbox fails here
    rather than quietly delimiting the wrong hemisphere; the min<max assertions catch the
    transpositions that happen to stay in range on both axes.
    """
    values = tuple(bbox)
    if len(values) != 4:
        raise AreaInvalid(f"bbox must be [minLon, minLat, maxLon, maxLat], got {len(values)}")
    min_lon, min_lat = validate_lon(values[0]), validate_lat(values[1])
    max_lon, max_lat = validate_lon(values[2]), validate_lat(values[3])
    if min_lon >= max_lon or min_lat >= max_lat:
        raise AreaInvalid(
            f"bbox is [minLon, minLat, maxLon, maxLat] in {CRS} and must increase on both "
            f"axes, got lon {min_lon}→{max_lon}, lat {min_lat}→{max_lat} — are the axes "
            "transposed?"
        )
    return _validate_area(box(min_lon, min_lat, max_lon, max_lat))


def _user_area(polygon: BaseGeometry, source_id: str) -> ResolvedArea:
    """Stamp a geometry the user delimited themselves. ``kind="user"`` ⇒ not bundleable."""
    return ResolvedArea(
        polygon=polygon,
        source=SourceRef(kind="user", id=source_id, license=USER_LICENSE),
    )


def _choose(name: str, candidates: Sequence[AreaCandidate]) -> ResolvedArea:
    """One strong match wins; several plausible ones are a question; none is a dead end."""
    strong = [c for c in candidates if c.confidence >= STRONG_CONFIDENCE]
    plausible = [c for c in candidates if c.confidence >= PLAUSIBLE_CONFIDENCE]
    shortlist = strong or plausible
    if len(shortlist) == 1:
        winner = shortlist[0]
        return ResolvedArea(
            polygon=winner.polygon, source=winner.source, candidates=tuple(candidates)
        )
    if shortlist:
        raise AreaAmbiguous(
            f"{len(shortlist)} plausible areas match {name!r}; ask the user which one",
            shortlist,
        )
    raise AreaUnresolvable(f"no area matched {name!r}", candidates)


def resolve_area(
    request: AreaRequest,
    *,
    divisions: DivisionsLookup | None = None,
    geocoder: Geocoder | None = None,
) -> ResolvedArea:
    """Resolve a user-delimited area to one stamped EPSG:4326 polygon.

    Precedence is explicit geometry first: ``polygon`` → ``bbox`` → ``name``.

    Raises:
        AreaInvalid: nothing was supplied, or the supplied geometry is not a usable area.
        AreaAmbiguous: the name has several plausible readings (carries ``candidates``).
        AreaUnresolvable: the name matched nothing.
    """
    try:
        if request.polygon is not None:
            return _user_area(_polygon_from_geojson(request.polygon), _USER_POLYGON_ID)
        if request.bbox is not None:
            return _user_area(_polygon_from_bbox(request.bbox), _USER_BBOX_ID)
    except GeometryError as error:
        raise AreaInvalid(str(error)) from error
    if request.name is None or not request.name.strip():
        raise AreaInvalid("an area needs a name, a bbox or a polygon — none was given")
    name = request.name
    # Overture divisions is authoritative; the geocoder only disambiguates its silence.
    candidates = tuple((divisions or OvertureDivisions()).search(name))
    if not candidates:
        candidates = tuple((geocoder or NominatimGeocoder()).search(name))
    return _choose(name, candidates)


def _license_of(sources: Sequence[Mapping[str, Any]] | None) -> str | None:
    """The license the row **states** (record-level first), mirroring ``commons.sources.overture``.

    ``None`` means the row states none: the candidate is dropped, never optimistically
    stamped with the theme's usual license.
    """
    stated: str | None = None
    for entry in sources or ():
        license_id = entry.get("license")
        if not license_id:
            continue
        if entry.get("property") in (None, ""):
            return str(license_id)
        stated = stated or str(license_id)
    return stated


@dataclass(frozen=True, slots=True)
class OvertureDivisions:
    """Name → administrative area over the Overture **divisions/division_area** theme.

    Read with DuckDB ``spatial`` (+ ``httpfs`` for the hosted release) exactly as
    :class:`~commons.sources.overture.OvertureAdapter` reads places. ``parquet`` points at a
    local file/glob so tests never touch the network; left ``None`` it reads the release.

    A DuckDB failure is **not** swallowed: an unreachable authoritative source must surface
    as an error, not as a false "no such area".
    """

    kind: ClassVar[SourceKind] = "overture"

    parquet: str | None = None
    release: str = DEFAULT_RELEASE
    limit: int = 20
    s3_region: str = "us-west-2"

    def search(self, name: str) -> Sequence[AreaCandidate]:
        needle = _normalize(name)
        if not needle:
            return ()
        candidates: list[AreaCandidate] = []
        for division_id, primary, alternates, sources, wkb in self._read(_collapse(name)):
            license_id = _license_of(sources)
            confidence = _match_confidence(needle, (primary, *(alternates or ())))
            if not division_id or wkb is None or license_id is None or confidence <= 0.0:
                continue
            try:
                polygon = _validate_area(from_wkb(bytes(wkb)))
            except (AreaInvalid, ShapelyError):
                continue
            candidates.append(
                AreaCandidate(
                    name=str(primary or name),
                    polygon=polygon,
                    source=SourceRef(
                        kind=self.kind,
                        id=str(division_id),
                        license=license_id,
                        attribution=_attribution_for(license_id, DIVISIONS_ATTRIBUTION),
                    ),
                    confidence=confidence,
                )
            )
        return tuple(candidates)

    def _read(self, needle: str) -> list[Any]:
        source = self.parquet or DIVISIONS_SOURCE_TEMPLATE.format(release=self.release)
        connection = duckdb.connect()
        try:
            connection.execute("INSTALL spatial; LOAD spatial;")
            if self.parquet is None:
                connection.execute("INSTALL httpfs; LOAD httpfs;")
                connection.execute("SET s3_region = $region;", {"region": self.s3_region})
            sql = f"{_DIVISIONS_QUERY} LIMIT {int(self.limit)}"
            return connection.execute(sql, {"src": source, "needle": needle}).fetchall()
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class NominatimGeocoder:
    """Disambiguation fallback over Nominatim — **one** bounded request, never a loop.

    OSMF usage policy (`DATA-LICENSES.md` "API terms"): ≤1 req/s, an honest User-Agent, no
    bulk use. This class makes exactly one GET per :meth:`search`, with no retry, so the
    rate limit holds without a sleep; ``client`` lets a caller or a test inject a transport.

    Everything it returns is OSM-derived, so it is stamped ODbL with the attribution the
    Produced-Work guidelines require — the same constants ``commons/sources/osm.py`` uses.
    """

    kind: ClassVar[SourceKind] = "osm"

    endpoint: str = NOMINATIM_ENDPOINT
    timeout: float = 10.0
    limit: int = 5
    client: httpx.Client | None = None

    def search(self, name: str) -> Sequence[AreaCandidate]:
        needle = _normalize(name)
        if not needle:
            return ()
        payload = self._request(name)
        candidates = []
        for result in payload:
            candidate = self._to_candidate(result, needle)
            if candidate is not None:
                candidates.append(candidate)
        return tuple(candidates)

    def _request(self, name: str) -> list[Mapping[str, Any]]:
        """One GET. The *fallback* being unavailable must not raise — it returns nothing."""
        params: dict[str, str | int] = {
            "q": name,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "addressdetails": 0,
            "limit": self.limit,
        }
        client = self.client or httpx.Client(
            timeout=self.timeout, headers={"User-Agent": USER_AGENT}
        )
        try:
            response = client.get(self.endpoint, params=params, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        finally:
            if self.client is None:
                client.close()
        return list(body) if isinstance(body, list) else []

    def _to_candidate(self, result: Mapping[str, Any], needle: str) -> AreaCandidate | None:
        osm_type, osm_id = result.get("osm_type"), result.get("osm_id")
        geojson = result.get("geojson")
        if not osm_type or osm_id is None or not isinstance(geojson, Mapping):
            return None
        try:
            polygon = _polygon_from_geojson(geojson)
        except (AreaInvalid, GeometryError):
            return None  # Nominatim answers points and lines too; an area query wants areas
        display = str(result.get("display_name") or "")
        names = (result.get("name"), display.split(",")[0], display)
        confidence = _match_confidence(needle, names)
        if confidence <= 0.0:
            return None
        element = f"{osm_type}/{osm_id}"
        return AreaCandidate(
            name=str(result.get("name") or display or element),
            polygon=polygon,
            source=SourceRef(
                kind=self.kind,
                id=element,
                url=f"{_OSM_BASE}/{element}",
                license=OSM_LICENSE,
                attribution=OSM_ATTRIBUTION,
            ),
            confidence=confidence,
        )
