"""The offline basemap stage — extract, glyphs, and one hash per file (T035/T035a/T035b).

First stage of the compile order (`compiler/AGENTS.md`, tech-design §5.3) and the one that
decides whether the traveller has a map at all. Ground truth is
[`docs/data/tile-source.md`](../docs/data/tile-source.md) and **ADR-0021**; where anything
here reads differently, the card wins.

Three things happen, in this order, and each of them is a documented trap:

1. **Resolve the build, then extract** (T035). Protomaps' daily planet build is the source,
   its URL is resolved at run time by probing back through the retention window, and the
   extract covers the itinerary's own bbox plus ADR-0021's margin — no more (FR-016).
2. **Select glyph ranges from the area's own label scripts** (T035a). Not a fixed range.
3. **Hash every file the stage wrote** (T035b), glyphs and sprites included.

## Why the build URL is resolved and never written down

Protomaps documents that *"URLs may change and hotlinking to these downloads are
discouraged"* and keeps roughly **a week** of builds. There is no ``/latest`` redirect and no
JSON index. A pinned URL is therefore a bug with a one-week fuse, and a *client* that reaches
for one is a hotlink. :func:`resolve_build` walks back day by day from today through
:data:`RETENTION_DAYS`, takes the first build that answers a ``HEAD``, and **fails loudly**
when none does — the mechanism ADR-0021 accepts as ours rather than upstream's, contained by
having one implementation with one call site. The resolved day becomes
``TileSourceV1.build_date``, which is what makes a bundle's basemap vintage auditable.

## The bbox is computed, never chosen

:func:`bbox_for_day` is ADR-0021's rule as code: ``unary_union`` over the day's stop points
and leg geometries, buffered **1 km in a local metric projection**, enveloped, floored at a
**2 km span**, and reprojected to EPSG:4326. The buffer is metric because a degree of
longitude is 111 km at the equator and 20 km at 80° N — a naive degree offset gives Rhodes a
different margin from Takayama, which is exactly the place-specific behaviour FR-001 forbids.
The floor exists because a compact single-block day otherwise yields a bbox **smaller than the
map viewport**, and this is also the bbox the pruned walk graph is cut to, so offline recovery
routing cannot route off the edge of the map.

Two details that look like fussiness and are not:

* The projected rectangle is **densified before it is reprojected**. Four corners alone
  reproject to a curved quadrilateral whose edge midpoints bow *inside* the corner bbox, so a
  corners-only bbox under-covers along each edge. Densifying costs microseconds; the bug it
  prevents is a map that stops rendering part-way to its own boundary.
* Bounds are rounded **outward** to 1e-6°, so the recorded bbox is exactly the extent the
  CLI was asked for and never a hair inside it.

## Glyph ranges come from the area, or the map is blank (T035a)

``scripts/fetch-basemap.sh`` prunes glyphs to U+0000–U+04FF. That is right for the Latin/Greek
dev window and renders **nothing at all** for a Hebrew, Arabic or CJK label — MapLibre draws
no glyph and reports no error. SC-009 proves genericity against a second area of different
character, so a hardcoded range would let the genericity eval pass over an unreadable map.

:func:`glyph_ranges` therefore derives the 256-codepoint ranges to vendor from the **bundled
sites' own names**, by two complementary rules:

* every block a name's codepoints actually land in — which catches script-specific marks and
  punctuation that no letter-classification table lists; **plus**
* every block of every *script* :mod:`commons.translit` detects in those names — because the
  labels the basemap renders come from the tile data, not from our records, and our records
  are a sample of the area's scripts rather than an enumeration of its labels.

The second rule is what costs bytes, and it costs them most for the ideographic scripts, where
partial coverage is also most likely. Measured on this table (2026-08-14): an area with no
labels selects **2** ranges, a Greek or Hebrew one **4**, an Arabic one **9**, a Japanese one
**114** and a Korean one **161** — times three fontstacks. ADR-0021 measured the whole Rhodes
extract at ~1.3 MB against the ≤200 MB budget and concluded the size budget is not the binding
constraint at M1, so this stage buys certainty with bytes we have. The lever, if a metro CJK
day ever breaches SC-007, is ``maxzoom=14`` (each zoom is ≈2× the archive), not a narrower
glyph set: a missing zoom level degrades, a missing glyph disappears.

Upstream not having a range is **recorded, not guessed at**: :class:`AssetSource` returns
``None`` for a range the asset host does not publish, and those are reported on
:attr:`TileStage.missing`. A *detected script* for which nothing at all could be fetched is
the T035a failure itself, and raises :class:`GlyphCoverageError` rather than shipping.

## T035b — glyphs are a bundled artifact and had no hash

``TileSourceV1.glyphs`` carries ``{path, license}`` while ``style`` carries ``{path,
sha256}``, so as the manifest stands, every bundled artifact has an integrity hash except the
one that decides whether labels render. This module computes both halves of the fix —
:attr:`TileStage.glyphs` (one :class:`~compiler.manifest.Artifact` per file) and
:attr:`TileStage.glyphs_sha256` (:func:`directory_digest` over them) — but **cannot record
them**: ``GlyphsRef`` has no ``sha256`` field, and adding one is a schema-card amendment
(`docs/data/tile-source.md`), not something a producer may invent. Until the card is amended
the digests are computed and returned, and the manifest is unable to carry them. Same for
sprites, which the card mentions only in prose under ``glyphs`` and gives no field of their
own at all.

## Seams, and what Tier 1 therefore never touches

Two injection points, both mirroring :mod:`commons.routing`'s ``RoutingProvider``: the
extractor (:class:`TileExtractor` — the ``pmtiles`` CLI in production, a file copy in tests)
and the asset host (:class:`AssetSource`). Unset means the **real** one, never the fixture: a
fixture default would hand every area on earth one recorded archive and fail no check at all,
where a missing binary fails on the first call. Tier 1 injects both, so it needs neither the
binary nor a socket.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import shutil
import subprocess
import urllib.parse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

import httpx
import pyproj
import rfc8785
import shapely
from shapely import LineString, Point, unary_union
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as project_geometry

from commons import licenses
from commons.geo import validate_lat, validate_lon
from commons.models import ArtifactRef, GlyphsRef, ItineraryV1, SiteRecordV1, TileSourceV1
from commons.translit import script_counts
from compiler.manifest import Artifact

__all__ = [
    "ARCHIVE_DIR",
    "ASSETS_URL_ENV",
    "BASE_GLYPH_RANGES",
    "BUFFER_M",
    "BUILD_HOST_ENV",
    "BUILD_SOURCE",
    "DEFAULT_ARCHIVE_NAME",
    "DEFAULT_BUILD_HOST",
    "DEFAULT_FONTSTACKS",
    "DEFAULT_PMTILES_BINARY",
    "DEFAULT_SPRITE_ASSETS",
    "EXTRACTOR_ENV",
    "FIXTURE_ARCHIVE_ENV",
    "GLYPHS_DIR",
    "GLYPH_LICENSE",
    "GLYPH_RANGE_SIZE",
    "MAXZOOM",
    "MINZOOM",
    "MIN_SPAN_M",
    "PROTOMAPS_ASSETS_URL",
    "RETENTION_DAYS",
    "SPRITES_DIR",
    "SPRITE_LICENSE",
    "TILE_ATTRIBUTION",
    "TILE_LICENSE",
    "TILE_SOURCE_KIND",
    "AssetSource",
    "Bbox",
    "BuildUnavailable",
    "ExtractFailed",
    "ExtractRequest",
    "FixtureExtractor",
    "GlyphCoverageError",
    "GlyphRange",
    "HttpAssetSource",
    "PmtilesCliExtractor",
    "ProtomapsBuild",
    "TileError",
    "TileExtractor",
    "TileStage",
    "bbox_for_day",
    "compile_tiles",
    "directory_digest",
    "glyph_ranges",
    "itinerary_bbox",
    "label_text",
    "resolve_build",
    "select_asset_source",
    "select_extractor",
]

_log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Constants — every one of them a card / ADR value, none of them place-specific
# --------------------------------------------------------------------------------------

#: The daily planet build host. Probed, never hotlinked — see the module docstring.
DEFAULT_BUILD_HOST: Final = "https://build.protomaps.com"
#: Protomaps' published retention for daily builds is about a week; the walk-back never
#: reaches past it, so a dead probe loop is a bounded 7 requests rather than an open search.
RETENTION_DAYS: Final = 7
#: ``TileSourceV1.build_source`` for this producer (the card's other value is ``planetiler``,
#: ADR-0021's sovereignty fallback, which is not wired at M1).
BUILD_SOURCE: Final = "protomaps-daily"

#: Vendored glyphs and sprites. Same host and layout ``scripts/fetch-basemap.sh`` uses, so the
#: compiled bundle and the dev assets come from one place.
PROTOMAPS_ASSETS_URL: Final = "https://protomaps.github.io/basemaps-assets"

USER_AGENT: Final = "siyur/0.0 (https://github.com/bgalon/siyur)"

#: ADR-0021's margin: 1 km geodesic around the day, with a 2 km minimum bbox span. 500 m is
#: about six minutes of straying at a sightseeing pace — thin for the FR-019 recovery promise
#: — and the extract is small enough (~1.3 MB measured) that the extra margin is free.
BUFFER_M: Final = 1000.0
MIN_SPAN_M: Final = 2000.0

#: Keep the whole sub-pyramid. z0 is a **clustered-source efficiency requirement** (a partial
#: zoom slice costs more against a clustered archive, not less), not a completeness taste.
MINZOOM: Final = 0
#: The Protomaps build ceiling, not a budget choice — the budget lever is dropping to 14.
MAXZOOM: Final = 15

#: Tile data is a Produced Work of OSM however permissive the pipeline around it is; the
#: Protomaps build code is BSD, the *data* is ODbL and renders its credit on every map.
TILE_SOURCE_KIND: Final = "osm"
TILE_LICENSE: Final = "ODbL-1.0"
TILE_ATTRIBUTION: Final = "© OpenStreetMap contributors"
#: Noto glyphs ship under the SIL Open Font License; the Protomaps sprite sheets under MIT.
GLYPH_LICENSE: Final = "OFL-1.1"
SPRITE_LICENSE: Final = "MIT"

#: Bundle-relative directories. ``DEFAULT_ARCHIVE_NAME`` is the card's own generic example
#: (``tiles/area.pmtiles``) — a name, not a place; callers pass their own.
ARCHIVE_DIR: Final = "tiles"
GLYPHS_DIR: Final = "glyphs"
SPRITES_DIR: Final = "sprites"
DEFAULT_ARCHIVE_NAME: Final = "area"

#: The three weights the base MapLibre style asks for, matching ``scripts/fetch-basemap.sh``.
#: The directory name is the fontstack verbatim, spaces and all, because that is the token
#: MapLibre substitutes into ``glyphs`` — it URL-encodes on request, so the stored name must
#: be the decoded one.
DEFAULT_FONTSTACKS: Final[tuple[str, ...]] = (
    "Noto Sans Regular",
    "Noto Sans Medium",
    "Noto Sans Italic",
)
#: Sprite sheet files, both flavours and both pixel ratios (MapLibre picks per device).
DEFAULT_SPRITE_ASSETS: Final[tuple[str, ...]] = tuple(
    f"{flavor}{suffix}"
    for flavor in ("light", "dark")
    for suffix in (".json", ".png", "@2x.json", "@2x.png")
)
#: Where the sprite sheets live under the assets host (Protomaps' current sheet version).
SPRITE_ASSET_VERSION: Final = "v4"

DEFAULT_PMTILES_BINARY: Final = "pmtiles"

EXTRACTOR_ENV: Final = "SIYUR_TILE_EXTRACTOR"
FIXTURE_ARCHIVE_ENV: Final = "SIYUR_TILE_FIXTURE"
ASSETS_URL_ENV: Final = "SIYUR_BASEMAP_ASSETS_URL"
BUILD_HOST_ENV: Final = "SIYUR_PROTOMAPS_BUILD_HOST"
#: Unset ⇒ the real CLI, **never the fixture** (module docstring).
DEFAULT_EXTRACTOR: Final = "pmtiles"

#: ``[minLon, minLat, maxLon, maxLat]``, EPSG:4326 — the card's bbox, the CLI's ``--bbox``.
type Bbox = tuple[float, float, float, float]

#: Bounds are rounded outward to this many decimal places (~0.1 m), so the recorded bbox is
#: byte-stable across runs *and* never smaller than the extent the rule computed.
_BBOX_DECIMALS: Final = 6
_BBOX_SCALE: Final = 10**_BBOX_DECIMALS

#: Maximum segment length, in projected metres, when densifying the rectangle before it is
#: reprojected. A ~4 km box at 250 m gives ~16 vertices a side; the reprojection error it
#: removes is the edge-midpoint bow described in the module docstring.
_DENSIFY_M: Final = 250.0

_WGS84: Final = pyproj.CRS.from_epsg(4326)


def _refuse_unbundleable_stamp() -> None:
    """Every licence this stage stamps must *derive* bundleable — never be asserted so.

    The same rule ``commons.routing.routing_source`` applies to a leg, applied to the three
    licences a tile bundle carries. ``TileSourceV1`` has no ``bundleable`` field to validate,
    so this import-time check is the whole of the enforcement: if the registry stops
    allowlisting one of these, the compiler refuses to start rather than emitting an archive
    the quarantine filter has no way to see.
    """
    if not licenses.bundleable(TILE_SOURCE_KIND, TILE_LICENSE):
        raise RuntimeError(
            f"tile data would not be bundleable: "
            f"{licenses.quarantine_reason(TILE_SOURCE_KIND, TILE_LICENSE)}"
        )
    for label, license_id in (("glyphs", GLYPH_LICENSE), ("sprites", SPRITE_LICENSE)):
        if not licenses.is_allowlisted(license_id):
            raise RuntimeError(
                f"bundled {label} would not be bundleable: "
                f"{licenses.quarantine_reason('commons', license_id)}"
            )


_refuse_unbundleable_stamp()


class TileError(Exception):
    """The basemap stage cannot produce a bundle. Never caught to continue."""


class BuildUnavailable(TileError):
    """No Protomaps daily build answered inside the retention window (ADR-0021)."""


class ExtractFailed(TileError):
    """``pmtiles extract`` did not produce an archive."""


class GlyphCoverageError(TileError):
    """A script present in the area's labels has no glyphs — the T035a failure, made loud."""


# --------------------------------------------------------------------------------------
# The bbox — ADR-0021's geodesic rule, computed in metres and never in degrees
# --------------------------------------------------------------------------------------


def _projection_centre(geom: BaseGeometry) -> tuple[float, float]:
    """Centre for the local metric projection: circular-mean longitude, mean latitude.

    The longitude mean is circular rather than arithmetic because a day straddling the
    antimeridian has coordinates near +179 and −179, whose arithmetic mean is 0 — a projection
    centre on the other side of the planet, where an azimuthal projection's distortion is
    total rather than negligible. Siyur must work for any area, and Fiji is an area.
    """
    coordinates = shapely.get_coordinates(geom)
    if len(coordinates) == 0:
        raise TileError("cannot centre a projection on an empty geometry")
    sin_sum = 0.0
    cos_sum = 0.0
    lat_sum = 0.0
    for lon, lat in coordinates:
        radians = math.radians(float(lon))
        sin_sum += math.sin(radians)
        cos_sum += math.cos(radians)
        lat_sum += float(lat)
    return math.degrees(math.atan2(sin_sum, cos_sum)), lat_sum / len(coordinates)


def _local_metric_crs(lon: float, lat: float) -> pyproj.CRS:
    """An azimuthal-equidistant CRS in metres, centred on the day.

    AEQD is exact in distance *from its centre*, which is precisely what a "1 km around this
    day" buffer asks for, and its distortion over a few kilometres of surrounding extent is
    sub-metre. A fixed projection (Web Mercator, a UTM zone picked by hand) would be neither:
    Mercator's metre is a different length at every latitude, and a UTM zone is a per-place
    constant by definition.
    """
    return pyproj.CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat!r} +lon_0={lon!r} +x_0=0 +y_0=0 "
        "+ellps=WGS84 +datum=WGS84 +units=m +no_defs"
    )


def _at_least(bounds: tuple[float, float, float, float], span: float) -> tuple[float, ...]:
    """Grow a projected rectangle symmetrically until neither side is shorter than ``span``."""
    min_x, min_y, max_x, max_y = bounds
    grown: list[float] = []
    for low, high in ((min_x, max_x), (min_y, max_y)):
        deficit = span - (high - low)
        pad = max(deficit, 0.0) / 2.0
        grown.append(low - pad)
        grown.append(high + pad)
    return (grown[0], grown[2], grown[1], grown[3])


def _round_out(value: float, *, up: bool) -> float:
    """Round to :data:`_BBOX_DECIMALS`, always away from the interior of the bbox."""
    scaled = value * _BBOX_SCALE
    rounded = math.ceil(scaled) if up else math.floor(scaled)
    return rounded / _BBOX_SCALE


def _wrap_lon(lon: float) -> float:
    """Fold a longitude back into [−180, 180]."""
    return ((lon + 180.0) % 360.0) - 180.0


def bbox_for_day(
    points: Iterable[Point] = (),
    lines: Iterable[LineString] = (),
    *,
    buffer_m: float = BUFFER_M,
    min_span_m: float = MIN_SPAN_M,
) -> Bbox:
    """The tile bbox for one day: the day's own extent plus ADR-0021's margin, and no more.

    ``points`` are the stops' locations and ``lines`` the leg geometries, all EPSG:4326
    (lon, lat). The result is ``[minLon, minLat, maxLon, maxLat]``, rounded outward.

    An antimeridian-crossing day returns ``minLon > maxLon``, per RFC 7946 §5.2 and the
    ``TileSourceV1.bbox`` validator, which deliberately does not order-check longitude.
    """
    geometries: list[BaseGeometry] = [*points, *lines]
    if not geometries:
        raise TileError(
            "a tile bbox needs the day's geometry: no stop points and no leg geometries were "
            "given, and a bundle with no map extent is not a smaller bundle, it is a broken one"
        )
    # shapely 2.x `unary_union` (1.x `cascaded_union` is gone — AGENTS.md geo table).
    day = unary_union(geometries)
    centre_lon, centre_lat = _projection_centre(day)
    metric = _local_metric_crs(centre_lon, centre_lat)
    to_metres = pyproj.Transformer.from_crs(_WGS84, metric, always_xy=True)
    to_degrees = pyproj.Transformer.from_crs(metric, _WGS84, always_xy=True)

    projected = project_geometry(to_metres.transform, day)
    # ADR-0021 literally: buffer the union, take its envelope. Shapely's default buffer
    # resolution puts vertices exactly on the four cardinal points of a circle, so the
    # envelope is the full ±`buffer_m` and never an inscribed approximation of it.
    envelope = _at_least(projected.buffer(buffer_m).envelope.bounds, min_span_m)
    rectangle = shapely.segmentize(shapely.box(*envelope), max_segment_length=_DENSIFY_M)
    degrees = project_geometry(to_degrees.transform, rectangle)

    # Longitudes are unwrapped around the projection centre before min/max: at the
    # antimeridian the raw values jump from +180 to −180, and a naive min/max there returns
    # the *complement* of the box — a bbox spanning the whole planet except the day.
    lons: list[float] = []
    lats: list[float] = []
    for lon, lat in shapely.get_coordinates(degrees):
        lons.append(centre_lon + _wrap_lon(float(lon) - centre_lon))
        lats.append(float(lat))
    return (
        validate_lon(_wrap_lon(_round_out(min(lons), up=False))),
        validate_lat(max(-90.0, _round_out(min(lats), up=False))),
        validate_lon(_wrap_lon(_round_out(max(lons), up=True))),
        validate_lat(min(90.0, _round_out(max(lats), up=True))),
    )


def itinerary_bbox(
    itinerary: ItineraryV1,
    sites: Iterable[SiteRecordV1],
    *,
    buffer_m: float = BUFFER_M,
    min_span_m: float = MIN_SPAN_M,
) -> Bbox:
    """:func:`bbox_for_day` over a planned day — stop locations plus every leg geometry.

    A ``Stop`` carries only a ``site_id``; the coordinate lives on the site record, so the
    day's records are passed alongside. A stop whose site is missing **raises**: silently
    skipping it would shrink the bbox around a place the traveller is standing in, and the
    symptom (the map ending short of a stop) appears offline, where nothing can refetch it.
    """
    located: dict[UUID, Point] = {site.id: site.location.value for site in sites}
    missing = [stop.site_id for stop in itinerary.stops if stop.site_id not in located]
    if missing:
        raise TileError(
            f"{len(missing)} stop(s) reference a site not among the records given "
            f"({', '.join(sorted(str(site_id) for site_id in missing))}); the tile bbox would "
            "exclude a place the day actually visits"
        )
    return bbox_for_day(
        [located[stop.site_id] for stop in itinerary.stops],
        [leg.geometry for leg in itinerary.legs],
        buffer_m=buffer_m,
        min_span_m=min_span_m,
    )


# --------------------------------------------------------------------------------------
# Build resolution — T035, "resolved at run time, never hotlinked"
# --------------------------------------------------------------------------------------

#: Answers "does this build URL exist?". Injectable so Tier 1 resolves without a socket.
type BuildProbe = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class ProtomapsBuild:
    """A daily planet build that answered: the URL to extract from and the day it was built.

    ``build_date`` is the **resolved** day, not today — a bundle compiled the morning after a
    build failure draws from the previous day's planet, and ``TileSourceV1.build_date`` has to
    say so for freshness to mean anything.
    """

    url: str
    build_date: date


def _head_probe(url: str) -> bool:
    """Default probe: a ``HEAD`` against the build host, following redirects."""
    try:
        response = httpx.head(
            url, timeout=20.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
    except httpx.HTTPError:  # DNS, TLS, timeout — indistinguishable from "not published yet"
        return False
    return response.is_success


def resolve_build(
    *,
    today: date | None = None,
    probe: BuildProbe | None = None,
    retention_days: int = RETENTION_DAYS,
    host: str | None = None,
) -> ProtomapsBuild:
    """Find a live daily build, newest first, inside the retention window (ADR-0021).

    ``today`` and ``probe`` are injected by tests and by nothing else; ``today`` defaults to
    the **UTC** date, because the build names are UTC calendar days and a machine in UTC+13
    would otherwise probe tomorrow first and always waste a request.

    Raises :class:`BuildUnavailable` when the window is exhausted. That is deliberately fatal:
    the alternative is an empty archive, which compiles, hashes and verifies, and is a blank
    map on the traveller's device.
    """
    base = (host or os.environ.get(BUILD_HOST_ENV) or DEFAULT_BUILD_HOST).rstrip("/")
    ask = probe if probe is not None else _head_probe
    start = today if today is not None else datetime.now(UTC).date()
    for back in range(retention_days):
        day = start - timedelta(days=back)
        url = f"{base}/{day.strftime('%Y%m%d')}.pmtiles"
        if ask(url):
            _log.info("protomaps daily build resolved: %s", day.isoformat())
            return ProtomapsBuild(url=url, build_date=day)
    raise BuildUnavailable(
        f"no Protomaps daily build answered in the last {retention_days} days at {base}. "
        "The URL scheme is our own mechanism, not an upstream contract (ADR-0021) — check "
        "whether it changed rather than widening the window"
    )


# --------------------------------------------------------------------------------------
# The extraction seam
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractRequest:
    """One ``pmtiles extract`` call, as data — so a fixture can satisfy it without a binary."""

    build_url: str
    bbox: Bbox
    maxzoom: int
    #: Filesystem destination (staging), unrelated to where the archive sits in the bundle.
    destination: Path

    @property
    def crosses_antimeridian(self) -> bool:
        """RFC 7946 §5.2's ``minLon > maxLon``, which one ``--bbox`` cannot express."""
        return self.bbox[0] > self.bbox[2]

    @property
    def bbox_arg(self) -> str:
        """``--bbox=W,S,E,N``, formatted at the precision the bbox was rounded to."""
        return ",".join(f"{bound:.{_BBOX_DECIMALS}f}" for bound in self.bbox)


@runtime_checkable
class TileExtractor(Protocol):
    """Produces the PMTiles archive at ``request.destination``. One method, on purpose."""

    def extract(self, request: ExtractRequest) -> None: ...


@dataclass(frozen=True, slots=True)
class PmtilesCliExtractor:
    """The engine of record: go-pmtiles' ``pmtiles extract`` (ADR-0021 option A).

    The whole sub-pyramid is kept (``--maxzoom`` only, no ``--minzoom``), because a
    partial-zoom slice of a clustered archive costs more than the full one.

    **A system binary, deliberately not a Python dependency.** There is no ``pmtiles``
    package in ``pyproject.toml`` and this module does not want one: go-pmtiles is a Go CLI
    distributed as a release binary (``brew install pmtiles``), and adding a PyPI package by
    that name would be exactly the slopsquatting shape Constitution Article V screens for.
    The argv below was checked against the installed CLI rather than against its README
    (**go-pmtiles 1.31.2**, 2026-08-14): ``pmtiles extract <input> <output>`` with
    ``--bbox=min_lon,min_lat,max_lon,max_lat`` and ``--maxzoom``, positional in that order.
    """

    binary: str = DEFAULT_PMTILES_BINARY
    #: A planet-scale extract is minutes of range requests, not seconds.
    timeout_s: float = 900.0

    def extract(self, request: ExtractRequest) -> None:
        if request.crosses_antimeridian:
            raise ExtractFailed(
                f"bbox {request.bbox} crosses the antimeridian (minLon > maxLon, RFC 7946 "
                "§5.2). `pmtiles extract` takes one W,S,E,N window and would read this as the "
                "complement of the day — the whole planet minus it. Such a day needs two "
                "extracts and a merge; refusing here beats emitting a plausible wrong archive"
            )
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            self.binary,
            "extract",
            request.build_url,
            str(request.destination),
            f"--bbox={request.bbox_arg}",
            f"--maxzoom={request.maxzoom}",
        ]
        try:
            # Fixed argv, no shell: the build URL and bbox are our own computed strings.
            completed = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout_s, check=False
            )
        except FileNotFoundError as exc:
            raise ExtractFailed(
                f"the {self.binary!r} CLI is not on PATH. go-pmtiles is a system binary, not a "
                "Python dependency — install it (`brew install pmtiles`) or inject a "
                "TileExtractor"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ExtractFailed(
                f"`{self.binary} extract` exceeded {self.timeout_s:.0f}s for bbox "
                f"{request.bbox_arg}"
            ) from exc
        if completed.returncode != 0:
            raise ExtractFailed(
                f"`{self.binary} extract` exited {completed.returncode}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        # Verify the artifact, not the exit code: a truncated or absent archive still leaves a
        # 0 on some paths, and the symptom of shipping one is a blank map with a valid hash.
        if not request.destination.is_file() or request.destination.stat().st_size == 0:
            raise ExtractFailed(
                f"`{self.binary} extract` reported success but wrote no archive at "
                f"{request.destination}"
            )


@dataclass(frozen=True, slots=True)
class FixtureExtractor:
    """Copies a committed archive into place — Tier 1's extractor, and never the default."""

    archive: Path

    def extract(self, request: ExtractRequest) -> None:
        if not self.archive.is_file():
            raise ExtractFailed(f"fixture archive {self.archive} does not exist")
        request.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.archive, request.destination)


def select_extractor() -> TileExtractor:
    """The extractor named by :data:`EXTRACTOR_ENV`; the CLI when unset (module docstring)."""
    choice = os.environ.get(EXTRACTOR_ENV, DEFAULT_EXTRACTOR).strip().lower()
    if choice == "pmtiles":
        return PmtilesCliExtractor()
    if choice == "fixture":
        archive = os.environ.get(FIXTURE_ARCHIVE_ENV)
        if not archive:
            raise TileError(
                f"{EXTRACTOR_ENV}=fixture needs {FIXTURE_ARCHIVE_ENV} to name an archive"
            )
        return FixtureExtractor(Path(archive))
    raise TileError(f"unknown {EXTRACTOR_ENV}={choice!r}; expected 'pmtiles' or 'fixture'")


# --------------------------------------------------------------------------------------
# Glyph ranges — T035a
# --------------------------------------------------------------------------------------

#: MapLibre requests glyphs in fixed 256-codepoint ranges (``{fontstack}/{first}-{last}.pbf``).
GLYPH_RANGE_SIZE: Final = 256


@dataclass(frozen=True, slots=True, order=True)
class GlyphRange:
    """One 256-codepoint glyph range, named the way MapLibre asks for it."""

    first: int
    last: int

    @classmethod
    def containing(cls, codepoint: int) -> GlyphRange:
        first = (codepoint // GLYPH_RANGE_SIZE) * GLYPH_RANGE_SIZE
        return cls(first=first, last=first + GLYPH_RANGE_SIZE - 1)

    @property
    def label(self) -> str:
        return f"{self.first}-{self.last}"

    def __str__(self) -> str:
        return self.label


def _ranges_covering(first: int, last: int) -> frozenset[GlyphRange]:
    return frozenset(
        GlyphRange.containing(codepoint) for codepoint in range(first, last + 1, GLYPH_RANGE_SIZE)
    ) | {GlyphRange.containing(last)}


#: Always vendored, whatever the area: ASCII plus Latin-1 and Latin Extended-A. Every style
#: draws digits, punctuation and the road-shield/units text from here regardless of the local
#: script, and two ranges is ~40 KB per fontstack.
BASE_GLYPH_RANGES: Final[frozenset[GlyphRange]] = _ranges_covering(0x0000, 0x01FF)

#: ISO 15924 script → the codepoint ranges a **renderer** needs for it.
#:
#: Deliberately *not* the table in :mod:`commons.translit`, and not derivable from it: that one
#: classifies **letters** so a provenance guard can say which script a name is written in, and
#: it excludes exactly what rendering cannot do without — combining marks (Hebrew niqqud,
#: Arabic harakat), script punctuation, and the presentation forms shaping engines fall back
#: to. Two tables, two questions; the detection half below is still translit's, so the ISO
#: codes cannot drift apart.
_SCRIPT_RANGES: Final[dict[str, tuple[tuple[int, int], ...]]] = {
    "Latn": ((0x0000, 0x024F), (0x1E00, 0x1EFF), (0x2C60, 0x2C7F)),
    "Grek": ((0x0370, 0x03FF), (0x1F00, 0x1FFF)),
    "Cyrl": ((0x0400, 0x052F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F)),
    "Armn": ((0x0530, 0x058F), (0xFB13, 0xFB17)),
    "Hebr": ((0x0590, 0x05FF), (0xFB1D, 0xFB4F)),
    "Arab": (
        (0x0600, 0x06FF),
        (0x0750, 0x077F),
        (0x08A0, 0x08FF),
        (0xFB50, 0xFDFF),
        (0xFE70, 0xFEFF),
    ),
    "Thaa": ((0x0780, 0x07BF),),
    "Deva": ((0x0900, 0x097F), (0xA8E0, 0xA8FF)),
    "Beng": ((0x0980, 0x09FF),),
    "Guru": ((0x0A00, 0x0A7F),),
    "Gujr": ((0x0A80, 0x0AFF),),
    "Orya": ((0x0B00, 0x0B7F),),
    "Taml": ((0x0B80, 0x0BFF),),
    "Telu": ((0x0C00, 0x0C7F),),
    "Knda": ((0x0C80, 0x0CFF),),
    "Mlym": ((0x0D00, 0x0D7F),),
    "Sinh": ((0x0D80, 0x0DFF),),
    "Thai": ((0x0E00, 0x0E7F),),
    "Laoo": ((0x0E80, 0x0EFF),),
    "Tibt": ((0x0F00, 0x0FFF),),
    "Mymr": ((0x1000, 0x109F),),
    "Geor": ((0x10A0, 0x10FF), (0x1C90, 0x1CBF), (0x2D00, 0x2D2F)),
    "Ethi": ((0x1200, 0x139F),),
    "Khmr": ((0x1780, 0x17FF), (0x19E0, 0x19FF)),
    "Hang": (
        (0x1100, 0x11FF),
        (0x3000, 0x303F),
        (0x3130, 0x318F),
        (0xA960, 0xA97F),
        (0xAC00, 0xD7FF),
    ),
    "Hira": ((0x3000, 0x303F), (0x3040, 0x309F)),
    "Kana": ((0x3000, 0x303F), (0x30A0, 0x30FF), (0x31F0, 0x31FF)),
    "Hani": (
        (0x2E80, 0x2EFF),
        (0x3000, 0x303F),
        (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF),
        (0xF900, 0xFAFF),
    ),
}


def glyph_ranges(texts: Iterable[str]) -> tuple[GlyphRange, ...]:
    """The glyph ranges an area's labels need, derived from the area's own text (T035a).

    Union of three things, sorted: :data:`BASE_GLYPH_RANGES`; the range each observed
    codepoint falls in; and the full range set of each *script* :func:`commons.translit.
    script_counts` detects. See the module docstring for why the last one is worth its bytes.
    """
    selected = set(BASE_GLYPH_RANGES)
    scripts: set[str] = set()
    for text in texts:
        for char in text:
            selected.add(GlyphRange.containing(ord(char)))
        scripts.update(script_counts(text))
    for script in sorted(scripts):
        for first, last in _SCRIPT_RANGES.get(script, ()):
            selected |= _ranges_covering(first, last)
    return tuple(sorted(selected))


def label_text(sites: Iterable[SiteRecordV1], extra: Iterable[str] = ()) -> tuple[str, ...]:
    """Every string whose script this area's glyph selection is derived from.

    The bundled sites' display names in **every** tagged language, not just the presentation
    one: a bundle rendered in ``en`` still draws the map's own Greek/Hebrew/Japanese labels
    from the tile data, and the ``el``/``he``/``ja`` names are our only sample of them.
    """
    names = [value.value for site in sites for value in site.names.values()]
    return (*names, *extra)


# --------------------------------------------------------------------------------------
# The asset seam — glyphs and sprites
# --------------------------------------------------------------------------------------


@runtime_checkable
class AssetSource(Protocol):
    """Supplies vendored glyph ranges and sprite sheets.

    Both methods return ``None`` for "this host does not publish that asset", which is a
    normal answer (no fontstack covers every range) and distinct from a failure, which raises.
    """

    def glyph(self, fontstack: str, glyph_range: GlyphRange) -> bytes | None: ...

    def sprite(self, asset: str) -> bytes | None: ...


class HttpAssetSource:
    """Fetches from the Protomaps basemaps-assets host — the same source as the dev script."""

    def __init__(self, base_url: str | None = None, *, client: httpx.Client | None = None) -> None:
        self._base = (base_url or os.environ.get(ASSETS_URL_ENV) or PROTOMAPS_ASSETS_URL).rstrip(
            "/"
        )
        self._client = client or httpx.Client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    def glyph(self, fontstack: str, glyph_range: GlyphRange) -> bytes | None:
        # The fontstack is percent-encoded on the wire and stored decoded on disk, exactly as
        # MapLibre substitutes it into the `glyphs` template.
        quoted = urllib.parse.quote(fontstack)
        return self._get(f"{self._base}/fonts/{quoted}/{glyph_range.label}.pbf")

    def sprite(self, asset: str) -> bytes | None:
        return self._get(f"{self._base}/sprites/{SPRITE_ASSET_VERSION}/{asset}")

    def _get(self, url: str) -> bytes | None:
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise TileError(f"fetching {url} failed: {exc}") from exc
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if not response.is_success:
            raise TileError(f"fetching {url} failed: HTTP {response.status_code}")
        return response.content


def select_asset_source() -> AssetSource:
    """The live asset host. A seam for symmetry with :func:`select_extractor`, no env switch:
    there is no fixture *default* to guard against, and tests inject their own source."""
    return HttpAssetSource()


# --------------------------------------------------------------------------------------
# The stage
# --------------------------------------------------------------------------------------


def directory_digest(artifacts: Iterable[Artifact]) -> str:
    """One integrity hash over a **directory** of artifacts (T035b's missing glyph hash).

    ``glyphs.path`` names a directory prefix, not a file, so there is no single byte stream to
    hash. This digests the JCS (RFC 8785) serialization of ``[[path, sha256], …]`` sorted by
    path — the same canonicalization the manifest seal uses, chosen for the same reason: the
    verifier is TypeScript, and "sorted keys, UTF-8" pins neither string escaping nor number
    formatting. Covering the **paths** as well as the bytes is what makes a renamed, added or
    removed range file detectable; a digest over concatenated bytes would not be.
    """
    pairs = sorted((artifact.path, artifact.sha256) for artifact in artifacts)
    return hashlib.sha256(rfc8785.dumps(pairs)).hexdigest()


@dataclass(frozen=True, slots=True)
class TileStage:
    """Everything the basemap stage produced — the embedded source plus every file's hash."""

    #: The whole ``TileSourceV1`` the manifest embeds verbatim at ``tiles.pmtiles``.
    source: TileSourceV1
    build: ProtomapsBuild
    archive: Artifact
    #: The style artifact this stage was handed — echoed so a caller sums one list, not two.
    style: Artifact
    #: One :class:`~compiler.manifest.Artifact` per vendored glyph range file.
    glyphs: tuple[Artifact, ...]
    #: :func:`directory_digest` over :attr:`glyphs`. **Not yet recordable in the manifest** —
    #: ``GlyphsRef`` has no ``sha256`` field; see the module docstring (T035b).
    glyphs_sha256: str
    sprites: tuple[Artifact, ...]
    sprites_sha256: str
    #: The ranges selected for this area, and the scripts they were derived from — reported so
    #: the genericity eval can assert on the *selection*, not only on the bytes.
    ranges: tuple[GlyphRange, ...]
    scripts: tuple[str, ...]
    #: ``(fontstack, range label)`` the asset host does not publish. Empty is the normal case.
    missing: tuple[tuple[str, str], ...]

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        """Every file this stage wrote, plus the style it was given — for the bundle writer."""
        return (self.archive, self.style, *self.glyphs, *self.sprites)

    @property
    def size_bytes(self) -> int:
        """``build_manifest(tiles_size_bytes=…)`` — every byte the tiles stage contributes.

        Includes the style JSON, which this stage does not write but does carry: the manifest
        counts the whole ``tiles`` group under one number, and leaving the style out would
        under-report the download by the one file the map cannot start without.
        """
        return sum(artifact.size_bytes for artifact in self.artifacts)


def compile_tiles(
    *,
    staging: Path,
    bbox: Bbox,
    style: Artifact,
    sites: Iterable[SiteRecordV1] = (),
    extra_label_text: Iterable[str] = (),
    name: str = DEFAULT_ARCHIVE_NAME,
    maxzoom: int = MAXZOOM,
    fontstacks: Sequence[str] = DEFAULT_FONTSTACKS,
    sprite_assets: Sequence[str] = DEFAULT_SPRITE_ASSETS,
    build: ProtomapsBuild | None = None,
    extractor: TileExtractor | None = None,
    assets: AssetSource | None = None,
) -> TileStage:
    """Run the basemap stage: extract the archive, vendor the glyphs, hash everything.

    Args:
        staging: filesystem directory the bundle is assembled in. Bundle-relative paths are
            mirrored under it, so ``tiles/<name>.pmtiles`` lands at the same place in both.
        bbox: from :func:`itinerary_bbox` — the day's extent plus the margin, already rounded.
        style: the base MapLibre style artifact, produced by the **style stage** and passed in
            already hashed. Generating it here would be a second implementation of
            ``web/src/map/basemap.ts``'s layer list, which is the one thing worse than an
            extra parameter.
        sites: the **post-quarantine** records. Their names are what the glyph selection is
            derived from (T035a); ``extra_label_text`` adds anything else known to be rendered.
        name: archive stem. Defaults to the card's own generic ``area`` — a bundle is named by
            its caller, never by a place baked in here.

    ``build``, ``extractor`` and ``assets`` default to the live resolver, the ``pmtiles`` CLI
    and the Protomaps asset host; Tier 1 injects all three.
    """
    resolved = build if build is not None else resolve_build()
    extract = extractor if extractor is not None else select_extractor()
    source = assets if assets is not None else select_asset_source()

    archive_path = f"{ARCHIVE_DIR}/{name}.pmtiles"
    destination = staging / archive_path
    extract.extract(
        ExtractRequest(build_url=resolved.url, bbox=bbox, maxzoom=maxzoom, destination=destination)
    )
    archive = Artifact.from_file(archive_path, destination)

    texts = label_text(sites, extra_label_text)
    ranges = glyph_ranges(texts)
    scripts = tuple(sorted({script for text in texts for script in script_counts(text)}))
    _log.info(
        "glyph selection: %d range(s) for script(s) %s",
        len(ranges),
        ", ".join(scripts) or "none detected",
    )

    glyphs, missing = _vendor_glyphs(staging, source, fontstacks, ranges, scripts)
    sprites = _vendor_sprites(staging, source, sprite_assets)

    return TileStage(
        source=TileSourceV1(
            path=archive_path,
            sha256=archive.sha256,
            bbox=bbox,
            minzoom=MINZOOM,
            maxzoom=maxzoom,
            build_source=BUILD_SOURCE,
            build_date=resolved.build_date,
            tile_license=TILE_LICENSE,
            attribution=TILE_ATTRIBUTION,
            style=ArtifactRef(path=style.path, sha256=style.sha256),
            glyphs=GlyphsRef(path=f"{GLYPHS_DIR}/", license=GLYPH_LICENSE),
        ),
        build=resolved,
        archive=archive,
        style=style,
        glyphs=glyphs,
        glyphs_sha256=directory_digest(glyphs),
        sprites=sprites,
        sprites_sha256=directory_digest(sprites),
        ranges=ranges,
        scripts=scripts,
        missing=missing,
    )


def _write(staging: Path, path: str, data: bytes) -> Artifact:
    """Write one bundle-relative artifact into the staging tree and hash its bytes."""
    destination = staging / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return Artifact.from_bytes(path, data)


def _vendor_glyphs(
    staging: Path,
    assets: AssetSource,
    fontstacks: Sequence[str],
    ranges: Sequence[GlyphRange],
    scripts: Sequence[str],
) -> tuple[tuple[Artifact, ...], tuple[tuple[str, str], ...]]:
    """Fetch and write every selected range for every fontstack; hash each file (T035b)."""
    written: list[Artifact] = []
    missing: list[tuple[str, str]] = []
    covered: set[GlyphRange] = set()
    for fontstack in fontstacks:
        for glyph_range in ranges:
            data = assets.glyph(fontstack, glyph_range)
            if data is None:
                missing.append((fontstack, glyph_range.label))
                continue
            covered.add(glyph_range)
            written.append(
                _write(staging, f"{GLYPHS_DIR}/{fontstack}/{glyph_range.label}.pbf", data)
            )
    _refuse_uncovered_scripts(scripts, covered)
    if missing:
        _log.warning("asset host published no glyphs for %d fontstack/range pair(s)", len(missing))
    return tuple(written), tuple(missing)


def _refuse_uncovered_scripts(scripts: Sequence[str], covered: set[GlyphRange]) -> None:
    """A detected script with **no** vendored range is the exact T035a failure — so it raises.

    Partial coverage is logged and reported; total absence is not, because that is the case
    where every label in that script renders as nothing and MapLibre says so nowhere. A blank
    map discovered in airplane mode has no remedy; a failed compile has an obvious one.
    """
    blind = [
        script
        for script in scripts
        if script in _SCRIPT_RANGES
        and not any(
            glyph_range in covered
            for first, last in _SCRIPT_RANGES[script]
            for glyph_range in _ranges_covering(first, last)
        )
    ]
    if blind:
        raise GlyphCoverageError(
            f"the area's labels are written in {', '.join(blind)} and not one glyph range was "
            "vendored for it — every label in that script would render as nothing, silently, "
            "offline (T035a). Check the asset host's fontstacks before compiling"
        )


def _vendor_sprites(
    staging: Path, assets: AssetSource, sprite_assets: Sequence[str]
) -> tuple[Artifact, ...]:
    """Fetch, write and hash the sprite sheets.

    Hashed for the same reason as the glyphs, and with the same gap: ``TileSourceV1`` gives
    sprites no field at all — the card mentions them only in prose under ``glyphs`` — so these
    hashes are returned and, until the card is amended, cannot be recorded in the manifest.
    A missing sprite sheet costs icons rather than every label, so an absent asset is skipped
    rather than fatal.
    """
    written: list[Artifact] = []
    for asset in sprite_assets:
        data = assets.sprite(asset)
        if data is None:
            _log.warning("asset host published no sprite %r", asset)
            continue
        written.append(_write(staging, f"{SPRITES_DIR}/{asset}", data))
    return tuple(written)
