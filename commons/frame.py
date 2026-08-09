"""The area's **local frame** — which clock a day is planned in, which calendar closes it.

Decisions of record: **ADR-0025 ruling 2**
(`docs/adr/0025-schema-card-amendments-for-plan-compile-travel.md`, the two fields and the rule) and
**[ADR-0029](../docs/adr/0029-offline-country-resolution-and-the-holiday-oracle.md)** (the country
dataset and the holiday oracle). Field-level authority is `docs/data/area.md`, "The local frame".

An area is a polygon, and a polygon on its own cannot answer two questions the rest of slice 002
is built on:

* **Which wall clock?** ``Stop.planned_start`` is ``10:00`` with no offset. ``ItineraryV1.date``
  supplies the day; :attr:`LocalFrame.timezone` supplies the clock that turns the pair into an
  instant.
* **Whose public holidays?** ``PH`` in an OSM ``opening_hours`` expression resolves against a
  **country's** calendar. `commons/opening_hours.py` refuses a ``PH``-bearing expression when
  ``country_code`` is ``None`` — verified, because with no country ``PH off`` closes nothing and a
  shut museum reports as *open*, the direction that strands a traveller at a locked door.

That refusal is only protective if this module never hands it a **wrong** country. So the two
values are computed here, once, by a deterministic spatial lookup over pinned offline data, and
then **stored on the area row** — never re-derived on read, never asked of the user, never emitted
by a model (AGENTS.md: the LLM does no spatial arithmetic).

## The rule, and it is one rule applied twice

**Largest area of intersection; ties break on the lexicographically smallest key** — the IANA id
for the clock, the ISO 3166-1 alpha-2 code for the calendar (`docs/data/area.md`). Not a sampled
point: a polygon 80 % in one country whose sample point lands in the other 20 % would resolve
public holidays against the wrong national calendar, and every real closure would evaluate as
open. `area.md` records that an earlier amendment demoted the rule to ``representative_point()``
and that the demotion was withdrawn; this module implements the restored rule, for **both** halves,
through one function — :func:`_largest_intersection` — so the two cannot drift apart and produce an
incoherent frame (a French country code against a German clock).

The card permits a point lookup as an *optimization* where the polygon intersects exactly one zone.
It is not taken. Establishing "exactly one zone" costs the same query as the rule itself here, so
the optimization would buy nothing and cost the property that makes the answer trustworthy.

**Straddling is ordinary** — a border town, a bay, a ring drawn across a line — and the answer must
not vary between runs. Two things make repeat runs bit-identical rather than merely usually-equal:
candidates are accumulated in **sorted index order** (float addition is not associative, so the
summation order is part of the answer), and the tie-break is a total order on the key.

## What is refused

A polygon intersecting **no** country is an **unresolvable area** and raises
:class:`FrameUnresolved`. There is no ``UTC`` fallback and no defaulted country: *a plan whose
frame is a guess is worse than a plan that was refused* (`area.md`). Note the asymmetry — the
timezone dataset tiles the oceans with ``Etc/GMT±N``, so open water resolves a *clock* and no
*country*; the country half is what fails, and it is the half a wrong answer is invisible in.

``None`` on a stored row means **unresolved**, and unresolved must reach the traveller as
``hours_unknown`` (`commons/opening_hours.py`), never as a default.

## The two pinned datasets — both offline, both read in place

* **Country** — Natural Earth 1:10m admin-0, committed at :data:`NATURAL_EARTH_ZIP` and read
  straight out of the zip by geopandas, so the committed bytes stay checksum-comparable with the
  upstream download (ADR-0029 Q1 option A). **Public domain**: the only entry in
  `DATA-LICENSES.md` with no obligation at all. Three implementation rules from that ADR are
  normative and each produces a wrong code if missed — see :func:`_country_index`.
* **Timezone** — the zone boundary polygons inside ``timezonefinder`` (pin ``~=8.2``), reached
  through ``get_polygon``. Note this reads the *polygons*, not the library's point API: the rule
  above needs areas. The data is ODbL-derived (timezone-boundary-builder, from OSM); it is read
  server-side at resolve time and the only thing retained is a scalar IANA id, so nothing is
  bundled and no attribution obligation attaches to a map — registered in `DATA-LICENSES.md`
  regardless, because a data dependency entering the product is registered like every other one
  (Constitution Article V).

Neither read touches the network, at import or at call: an area resolve that phones a timezone API
is neither reproducible in CI nor available to a compile.

## Holiday coverage is exposed here, gated elsewhere

:func:`holiday_countries` reports which alpha-2 codes ``holidays`` (python-holidays) can build a
calendar for. It is the **oracle** half of ADR-0029 Q2 — a coverage fact about the country, not a
verdict about an expression. The gate that turns "no calendar for this country" into
``hours_unknown`` belongs to feasibility (T018), and the evaluator's own, *narrower* coverage is
already enforced inside `commons/opening_hours.py` (``UnknownCountryError`` →
``country_not_supported``). The two sets are **not the same size** — see :func:`holiday_countries`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

import geopandas as gpd
from shapely import STRtree, box
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from timezonefinder import TimezoneFinder
from timezonefinder.utils import int2coord

from commons.geo import GeometryError, validate_lat, validate_lon

__all__ = [
    "COUNTRY_CODE_COLUMN",
    "NATURAL_EARTH_BYTES",
    "NATURAL_EARTH_LAYER",
    "NATURAL_EARTH_ROWS",
    "NATURAL_EARTH_SHA256",
    "NATURAL_EARTH_VERSION",
    "NATURAL_EARTH_ZIP",
    "UNCODED",
    "FrameUnresolved",
    "LocalFrame",
    "holiday_countries",
    "natural_earth_uri",
    "resolve_country_code",
    "resolve_frame",
    "resolve_timezone",
]

#: The committed country boundaries — Natural Earth **1:10m** admin-0, v5.1.1, public domain.
#: Read in place (``zip://…!….shp``), never unzipped: the archive's bytes are what
#: :data:`NATURAL_EARTH_SHA256` pins, and an unzip step would make the committed artifact
#: incomparable with the upstream download. ADR-0029 buys 1:10m's extra 4.1 MB over 1:50m for one
#: measured reason: 1:50m has **no Gibraltar row at all**, so a Gibraltar bbox resolves to ``ES``
#: — the wrong national holiday calendar for one of the most-walked tour areas on earth.
NATURAL_EARTH_ZIP: Final[Path] = (
    Path(__file__).resolve().parents[1] / "data" / "ne_10m_admin_0_countries.zip"
)
NATURAL_EARTH_LAYER: Final[str] = "ne_10m_admin_0_countries.shp"
NATURAL_EARTH_VERSION: Final[str] = "5.1.1"
#: Byte size and digest of the committed archive, asserted by ``tests/test_area_frame.py`` so an
#: accidental or silent swap of the boundary data is merge-blocking (ADR-0029's accepted cost for
#: 4.93 MB of binary in the repo). Deliberately **not** verified at import: hashing 4.9 MB on every
#: process start buys nothing a merge-blocking test does not already buy.
NATURAL_EARTH_BYTES: Final[int] = 4_930_492
NATURAL_EARTH_SHA256: Final[str] = (
    "ce1ac7036499a0edd641fbc093cd209a98f96a49d2eca8480aaacad35138a7f6"
)
#: Row count of the layer — 258 country/territory features in EPSG:4326.
NATURAL_EARTH_ROWS: Final[int] = 258

#: **``ISO_A2_EH``, never ``ISO_A2``** (ADR-0029 implementation rule 1). ``ISO_A2`` is ``-99`` for
#: 22 rows *including France and Norway*, so a lookup on it returns ``-99`` for metropolitan
#: France. ``ISO_A2_EH`` fixes France, Norway and Kosovo (``XK``) and leaves 13 ``-99`` rows, all
#: disputed or uninhabited.
COUNTRY_CODE_COLUMN: Final[str] = "ISO_A2_EH"
#: Natural Earth's "no ISO code" sentinel. **Dropped before ranking** (rule 3) — it otherwise wins:
#: verified on a Nicosia bbox, N. Cyprus (0.00121) and the UN buffer zone (0.00083) both beat
#: ``CY`` (0.00036), so a Nicosia day tour would resolve to a country code that is not a country.
UNCODED: Final[str] = "-99"

#: How many zone boundary rings to keep built. There are 1,322 in the dataset and a handful
#: answer almost every query, so this is comfortably larger than any one area needs and far
#: smaller than holding the whole world in memory.
_ZONE_POLYGON_CACHE: Final[int] = 256


class FrameUnresolved(LookupError):
    """The polygon has no local frame: it intersects no country (or no timezone).

    A **hard failure at resolve time**, deliberately not a fallback. Open ocean is the honest
    example, and it is the one that shows why the country half is the dangerous one: the timezone
    dataset tiles the oceans with ``Etc/GMT±N`` and answers cheerfully, while no national holiday
    calendar applies to a stretch of the Pacific. Defaulting either value would produce a plan
    whose times and closures *look* computed and are not.

    ``LookupError`` rather than ``ValueError``: the caller's geometry was well-formed (a malformed
    one raises :class:`~commons.geo.GeometryError` instead) — the data simply has no answer for it.
    """


@dataclass(frozen=True, slots=True)
class LocalFrame:
    """One area's derived clock and calendar. Both present, or :class:`FrameUnresolved`.

    There is no partial frame. A timezone without a country would let a plan be *scheduled* and not
    *checked*, which is precisely the state ``hours_unknown`` exists to keep visible — so the pair
    is resolved together and refused together.
    """

    #: IANA timezone id, e.g. ``Europe/Athens``. The frame every area-local wall clock is read in.
    timezone: str
    #: ISO 3166-1 alpha-2, e.g. ``GR``. The calendar ``PH`` resolves against.
    country_code: str

    @property
    def has_holiday_calendar(self) -> bool:
        """Whether ``holidays`` can build a calendar for :attr:`country_code`.

        A **coverage fact**, not a verdict: it says a public-holiday question about this country is
        answerable, not that any particular date is a holiday.

        **Measured, and it changes what this is for.** With ``holidays`` 0.102 and Natural Earth
        5.1.1 this is ``True`` for *every* code the resolver can produce: the dataset has 239 coded
        countries, the library covers 251, and 239 ⊂ 251 with nothing left over. So it never
        refuses anything today, and ADR-0029's "~127 countries stop failing open" is delivered
        somewhere else — by ``opening-hours-py`` raising ``UnknownCountryError`` outside its much
        smaller embedded database, which `commons/opening_hours.py` already turns into
        ``hours_unknown``. What this property is genuinely for is the **cross-check oracle**: the
        precondition for asking ``holidays`` whether ``ItineraryV1.date`` is a holiday, which is
        the half with subdivision support and therefore the half that closes ADR-0022 A2. Written
        down because a guard that cannot currently fire is worth naming as such rather than
        leaving for someone to trust.
        """
        return self.country_code in holiday_countries()


# ======================================================================================
# One rule, two datasets
# ======================================================================================
# Both halves reduce to: *which key's polygons overlap this area most?* Writing it once is not
# tidiness — it is what stops the country half and the timezone half answering by different rules
# and handing the planner a frame that contradicts itself.


@dataclass(frozen=True, slots=True)
class _Weigher:
    """A spatial index over candidate polygons, keyed by whatever they resolve to.

    ``tree`` is queried by **envelope**, which over-selects and never under-selects — a geometry is
    always inside its own bounding box — so the prefilter cannot lose a candidate the exact
    intersection would have kept. ``load`` turns a tree index into the real geometry, which lets the
    timezone side index 1,322 cheap boxes and pay for a ring only when one is actually a candidate:
    building all of them eagerly measures **21.6 s**, against 0.03–0.5 s for a real query.
    """

    keys: tuple[str, ...]
    tree: STRtree
    load: Callable[[int], BaseGeometry]

    def weigh(self, area: BaseGeometry) -> dict[str, float]:
        """Total intersection area per key, in square degrees.

        **Square degrees, not square metres.** For a walkable day the latitude span is small enough
        that the ``cos(lat)`` distortion is a near-constant factor across every candidate, so it
        cannot change which one is largest; and the numbers ADR-0029 verified this rule against are
        degree areas. A candidate set spanning a large latitude range could in principle flip a
        near-tie, which is a known and accepted limit rather than an undiscovered one.

        Indices are visited **in sorted order** so the per-key sums are accumulated identically on
        every run — float addition is not associative, and "the same polygon resolves the same way
        forever" is the property this module exists to provide.
        """
        weights: dict[str, float] = {}
        for index in sorted(int(hit) for hit in self.tree.query(area)):
            overlap = float(self.load(index).intersection(area).area)
            # `> 0` and not `is_empty`: a polygon touching this area along a border line yields a
            # zero-area LineString, which is not empty and is not evidence of anything. Keeping it
            # would let a degenerate input tie every candidate at 0.0 and hand the win to the
            # lexicographic tie-break — a confident answer drawn from nothing.
            if overlap <= 0.0:
                continue
            weights[self.keys[index]] = weights.get(self.keys[index], 0.0) + overlap
        return weights


def _largest_intersection(weights: Mapping[str, float]) -> str | None:
    """The normative rule: largest total area, ties on the lexicographically smallest key.

    ``None`` when nothing overlapped — the caller decides what that means, and every caller here
    decides it means *refuse*.
    """
    if not weights:
        return None
    largest = max(weights.values())
    return min(key for key, weight in weights.items() if weight == largest)


def _require_area(polygon: BaseGeometry) -> BaseGeometry:
    """Reject anything that cannot carry the rule, before it produces a plausible answer.

    A point or a line has zero area, so *every* candidate would score 0.0, every candidate would
    tie, and the tie-break would return the alphabetically first country it touched — a wrong
    answer with no symptom. The CRS check is here for the same reason: a Web Mercator polygon
    (coordinates in metres) simply intersects nothing, and "no country" would read as open ocean
    rather than as the axis/CRS mistake it is.
    """
    if polygon.geom_type not in {"Polygon", "MultiPolygon"}:  # shapely 2.x `.geom_type`
        raise GeometryError(
            f"a local frame is derived from an areal geometry, got {polygon.geom_type}"
        )
    if polygon.is_empty or polygon.area <= 0.0:
        raise GeometryError("a local frame needs a polygon enclosing space, got a degenerate one")
    min_lon, min_lat, max_lon, max_lat = polygon.bounds
    for lon in (min_lon, max_lon):
        validate_lon(lon)  # raises GeometryError naming the axis, ranges checked per axis
    for lat in (min_lat, max_lat):
        validate_lat(lat)
    return polygon


# ======================================================================================
# Country — Natural Earth 1:10m admin-0, committed and read in place
# ======================================================================================


def natural_earth_uri() -> str:
    """The geopandas URI for the committed archive — read from the zip, never unpacked."""
    return f"zip://{NATURAL_EARTH_ZIP}!{NATURAL_EARTH_LAYER}"


@lru_cache(maxsize=1)
def _country_index() -> _Weigher:
    """Load the boundaries once per process and index them. ~0.1 s, then microseconds a query.

    Two of ADR-0029's three normative rules live in these few lines, and each is a wrong
    ``country_code`` if dropped:

    * **Rule 2 — ``ISO_A2_EH`` is not unique**, so intersection areas are summed **per code** and
      never per row (``AU``×4, ``FR``×2, ``KZ``×2, ``BR``×2). Ranking rows compares fragments and
      picks the wrong winner near an overseas département or an Australian external territory.
      :meth:`_Weigher.weigh` groups by key, which is exactly this.
    * **Rule 3 — the ``-99`` rows are dropped before ranking**, here rather than after, because
      they win otherwise (see :data:`UNCODED`). If dropping them empties the candidate set that is
      a hard failure, never a fallback to the sentinel.

    One row (Egypt) is invalid per OGC — a ring self-intersection. It is **not repaired**: shapely
    2.x's overlay is robust to it (verified: the intersection computes), and a ``make_valid`` pass
    would mean the geometry we query is no longer the geometry :data:`NATURAL_EARTH_SHA256` pins.
    """
    frame = gpd.read_file(natural_earth_uri(), columns=[COUNTRY_CODE_COLUMN])
    codes: list[str] = []
    geometries: list[BaseGeometry] = []
    for code, geometry in zip(frame[COUNTRY_CODE_COLUMN], frame.geometry, strict=True):
        if code == UNCODED or geometry is None or geometry.is_empty:
            continue
        codes.append(str(code))
        geometries.append(geometry)
    indexed = tuple(geometries)
    return _Weigher(keys=tuple(codes), tree=STRtree(indexed), load=indexed.__getitem__)


def resolve_country_code(polygon: BaseGeometry) -> str:
    """ISO 3166-1 alpha-2 for an EPSG:4326 polygon, by largest intersecting area.

    Raises:
        GeometryError: the geometry is not a usable EPSG:4326 area.
        FrameUnresolved: it intersects no coded country — open ocean, or a polygon whose only
            overlaps were Natural Earth's uncoded disputed/uninhabited rows.
    """
    area = _require_area(polygon)
    code = _largest_intersection(_country_index().weigh(area))
    if code is None:
        raise FrameUnresolved(
            "this area intersects no country in the pinned boundary set, so it has no public "
            "holiday calendar and no plan over it can be checked against opening hours. "
            "Open water and a coordinate/CRS mistake both land here; neither gets a default"
        )
    return code


# ======================================================================================
# Timezone — the zone boundary polygons inside timezonefinder
# ======================================================================================


@lru_cache(maxsize=1)
def _finder() -> TimezoneFinder:
    """The zone dataset, opened once. Memory-mapped by default; no network, ever."""
    return TimezoneFinder()


@lru_cache(maxsize=_ZONE_POLYGON_CACHE)
def _zone_polygon(boundary_id: int) -> Polygon:
    """One zone boundary polygon, holes included, built on demand.

    ``get_polygon`` returns ``[shell, hole, hole, …]``; dropping the holes would silently place an
    enclave's area in the surrounding zone. Bounded cache because a handful of boundaries answer
    almost every query and rebuilding a 1,778-point ring per resolve is pure waste.
    """
    rings = _finder().get_polygon(boundary_id, coords_as_pairs=True)
    return Polygon(rings[0], rings[1:])


@lru_cache(maxsize=1)
def _zone_index() -> _Weigher:
    """Index the 1,322 zone boundaries by their **bounding boxes**, not their rings.

    ``timezonefinder`` publishes a per-boundary bbox as scaled ``int32`` vectors, which is what
    makes the largest-intersection rule affordable on a point-lookup library: the boxes index in
    milliseconds and only the survivors are ever built into geometry. The alternative — walking
    ``get_geometry`` over all 444 zones — measures 21.6 s and would push that cost into every
    area resolve.
    """
    finder = _finder()
    boundaries = finder.boundaries
    boxes = [
        box(
            int2coord(boundaries.xmin[index]),
            int2coord(boundaries.ymin[index]),
            int2coord(boundaries.xmax[index]),
            int2coord(boundaries.ymax[index]),
        )
        for index in range(len(boundaries))
    ]
    keys = tuple(str(finder.zone_name_from_boundary_id(index)) for index in range(len(boundaries)))
    return _Weigher(keys=keys, tree=STRtree(boxes), load=_zone_polygon)


def resolve_timezone(polygon: BaseGeometry) -> str:
    """IANA timezone id for an EPSG:4326 polygon, by largest intersecting area.

    The zone dataset covers the oceans (``Etc/GMT±N``), so this half almost never fails; that is
    exactly why it is never allowed to stand in for the country half.

    Raises:
        GeometryError: the geometry is not a usable EPSG:4326 area.
        FrameUnresolved: it intersects no zone.
    """
    area = _require_area(polygon)
    zone = _largest_intersection(_zone_index().weigh(area))
    if zone is None:
        raise FrameUnresolved(
            "this area intersects no timezone in the pinned boundary set, so no wall-clock time "
            "planned over it would mean anything; UTC is not a defensible default for a place"
        )
    return zone


# ======================================================================================
# The frame, and the holiday-coverage fact that hangs off it
# ======================================================================================


def resolve_frame(polygon: BaseGeometry) -> LocalFrame:
    """Derive an area's :class:`LocalFrame` from its polygon. Deterministic, offline, no clock.

    The country is resolved **first** because it is the half that fails and the cheaper of the two
    — an open-ocean delimitation is refused without paying for the zone lookup.

    Raises:
        GeometryError: the geometry is not a usable EPSG:4326 area.
        FrameUnresolved: either half has no answer. Never a partial frame, never a default.
    """
    area = _require_area(polygon)
    return LocalFrame(country_code=resolve_country_code(area), timezone=resolve_timezone(area))


@lru_cache(maxsize=1)
def holiday_countries() -> frozenset[str]:
    """The ISO 3166-1 alpha-2 codes ``holidays`` can build a public-holiday calendar for.

    Filtered to two-letter codes on purpose: ``list_supported_countries()`` returns **501** keys,
    of which 251 are alpha-2 and 250 are alpha-3 aliases for the same countries.
    ``area.country_code`` is alpha-2, and a membership test that quietly accepted ``GRC`` would
    report coverage for a value this project never produces.

    **These 251 are not the evaluator's 122.** ``opening-hours-py`` embeds a much smaller holiday
    database and raises ``UnknownCountryError`` outside it — `commons/opening_hours.py` already
    turns that into ``hours_unknown`` for a ``PH``-bearing expression. So this set is the *oracle's*
    reach (ADR-0029 Q2's cross-check, and its subdivision support), and it is deliberately the
    wider of the two: a country in here but not in the evaluator is one the oracle can still answer
    for, which is the whole point of having two sources. **Do not wire this set as the coverage
    gate** — it is a superset of the 239 codes Natural Earth can produce, so used as a gate it
    would pass everything. See :attr:`LocalFrame.has_holiday_calendar`.
    """
    from holidays.utils import list_supported_countries

    return frozenset(
        code
        for code in list_supported_countries()
        if len(code) == 2 and code.isalpha() and code.isupper()
    )
