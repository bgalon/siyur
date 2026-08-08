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
4. **The local frame is derived here, exactly once** (ADR-0025 ruling 2, ADR-0029). Every
   resolved area carries a ``timezone`` and a ``country_code`` computed from its polygon by
   :func:`~commons.frame.resolve_frame` — the clock every downstream wall-clock time is read
   in, and the calendar ``PH`` resolves against. It is derived at *resolve* time and then
   stored, so a plan stays readable in the frame it was planned in even after the boundary
   data changes; a polygon with no frame is **refused**, never defaulted (see
   :class:`AreaFrameUnresolved`).

**Precedence** is explicit-geometry-first: ``polygon`` → ``bbox`` → ``name``. A user who
drew a ring means that ring; a name is the only input that needs guessing.

**Overture divisions is authoritative; Nominatim is a disambiguation fallback only** —
consulted when divisions returns nothing, one bounded request, never in a loop (the OSMF
usage policy in `DATA-LICENSES.md`).

**The divisions read is two passes and it is on a clock** (DU-03). The hosted release keeps
97.5 % of its bytes in the ``geometry`` column and publishes row-group statistics for
``bbox`` and nothing else, so a name lookup that projects geometry reads the entire global
theme to answer "which rows match?" — measured at 212 s. Matching now reads only the narrow
columns and the polygons of the survivors are fetched afterwards, by id. That is still not
interactive on its own; a caller-supplied :attr:`OvertureDivisions.window` is what makes it
so, and :data:`DIVISIONS_TIMEOUT` is what stops the rest becoming an open-ended hang. See
the block comment above :data:`_DIVISIONS_QUERY` for the measurements.

:class:`DivisionsLookup` and :class:`Geocoder` are Protocols so both are injectable: the
node is fully testable offline and the network is never reached implicitly by a test.

Shapely pin is ``~=2.1`` — ``.geom_type`` / ``from_wkb`` / ``box``, never 1.x ``.type``.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Protocol, runtime_checkable

import duckdb
import httpx
from shapely import MultiPolygon, Polygon, box, from_wkb
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry

from commons.frame import FrameUnresolved, LocalFrame, resolve_frame
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
    "DIVISIONS_READ_THREADS",
    "DIVISIONS_SOURCE_TEMPLATE",
    "DIVISIONS_TIMEOUT",
    "NOMINATIM_ENDPOINT",
    "PLAUSIBLE_CONFIDENCE",
    "STRONG_CONFIDENCE",
    "USER_LICENSE",
    "AreaAmbiguous",
    "AreaCandidate",
    "AreaFrameUnresolved",
    "AreaInvalid",
    "AreaLookupTimeout",
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
#: DuckDB worker threads for a **hosted** (remote) divisions read. The scan is bound by
#: per-range-request latency, not by CPU: DuckDB issues roughly one request per row group
#: per parquet leaf column (544 row groups here), so the thread count is really the request
#: concurrency, and the default — one per core — leaves the link idle. Measured over the
#: hosted release, same query, same machine: **148 s at 8 threads → 78 s at 32**. Applied
#: only to the remote path; a local extract is CPU-bound and DuckDB's own default is right.
DIVISIONS_READ_THREADS: Final = 32
#: Wall-clock ceiling on a divisions lookup, in seconds. The hosted release is remote
#: parquet with no statistics on any name column (see the block comment below), so an
#: unwindowed name search is *inherently* a full visit of the theme — measured at minutes,
#: not seconds. This bound is what stops that becoming an open-ended hang: past it the
#: lookup is abandoned and raises :class:`AreaLookupTimeout`, which is an error about the
#: source and never a "no such area". Callers that can supply a ``window`` are far inside it.
DIVISIONS_TIMEOUT: Final = 120.0
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

# ======================================================================================
# One fold, two arms (FAIL-005)
# ======================================================================================
# The name prefilter is pushed into DuckDB while :func:`_match_confidence` re-scores in
# Python, so **two engines fold the same strings**. Their folds must be derived from one
# definition, never assumed equal: DuckDB's ``lower()`` is Unicode *simple* (1:1) case
# folding, ``str.casefold()`` is *full* (1:N), and they disagree on 274 codepoints — far
# too many to reconcile by hand, and the reason a Greek query once matched nothing.
#
# So the fold is defined **once, here**, and emitted as two arms generated from the same
# three tables — :func:`_normalize` (Python) and :func:`_fold_sql` (DuckDB):
#
#     whitespace-collapse → NFC → simple lowercase → NFC → 1:N expansions → 1:1 variants
#
# `tests/test_cross_engine_normalization.py` proves the two arms agree on **every**
# codepoint, not on a sample. Three choices worth stating:
#
# 1. **Simple lowercase, not casefold, is the base** — precisely because simple
#    lowercasing is what a database can reproduce. Python's ``str.lower()`` and DuckDB's
#    ``lower()`` differ on exactly *one* codepoint (U+0130 İ), which the first expansion
#    closes. Casefold would have left 274 open.
# 2. **NFC on both ends.** Normalizing *first* is what makes the fold composition
#    invariant (`lower(U+0130)` and `lower('I' + U+0307)` differ, and normalizing
#    afterwards cannot undo that); normalizing *again* afterwards recomposes sequences
#    that lowercasing itself produced (Η + U+0342 → η + U+0342 → U+1FC6).
# 3. **The expansion/variant tables buy back recall**, deliberately: ς matches σ, ß
#    matches ss, և matches եւ. That is now a product choice stated in one place, not an
#    accident of which engine happened to run.
#
# The property this optimises for: **the prefilter can never drop a row the Python scorer
# would have accepted**, because the prefilter and the scorer are the same function. A
# prefilter is allowed to over-select; it is never allowed to under-select.

#: Every codepoint Python's ``str.split()`` treats as whitespace — derived from Python
#: itself rather than typed out, so the SQL arm cannot drift from :func:`_collapse`. The
#: bound keeps import cheap and is pinned by a test that scans the whole codepoint space.
_WHITESPACE_MAX: Final = 0x3001
_FOLD_WHITESPACE: Final = "".join(
    chr(code) for code in range(_WHITESPACE_MAX) if not chr(code).split()
)

#: 1:N mappings full case folding performs and a simple ``lower()`` does not. The first
#: entry reconciles U+0130 (Python lowers İ to ``i`` + U+0307, DuckDB to ``i``); the rest
#: are the letters that genuinely *are* a sequence — sharp s, ligatures, the Armenian ew.
_FOLD_EXPANSIONS: Final[tuple[tuple[str, str], ...]] = (
    ("i̇", "i"),
    ("ß", "ss"),
    ("ŉ", "ʼn"),
    ("և", "եւ"),
    ("ẚ", "aʾ"),
    ("ﬀ", "ff"),
    ("ﬁ", "fi"),
    ("ﬂ", "fl"),
    ("ﬃ", "ffi"),
    ("ﬄ", "ffl"),
    ("ﬅ", "st"),
    ("ﬆ", "st"),
    ("ﬓ", "մն"),
    ("ﬔ", "մե"),
    ("ﬕ", "մի"),
    ("ﬖ", "վն"),
    ("ﬗ", "մխ"),
)

#: 1:1 equivalences: separate codepoints for one letter that full folding unifies and
#: ``lower()`` keeps apart — Greek final sigma and the symbol variants, the Cyrillic
#: historic letters, long s. ``ς → σ`` is the one FAIL-005 was written about.
_FOLD_VARIANTS: Final[dict[str, str]] = {
    "ſ": "s",
    "µ": "μ",
    "ͅ": "ι",
    "ι": "ι",
    "ϐ": "β",
    "ϵ": "ε",
    "ϑ": "θ",
    "ϴ": "θ",
    "ϰ": "κ",
    "ϖ": "π",
    "ϱ": "ρ",
    "ς": "σ",
    "ϕ": "φ",
    "ẛ": "ṡ",
    "ᲀ": "в",
    "ᲁ": "д",
    "ᲂ": "о",
    "ᲃ": "с",
    "ᲄ": "т",
    "ᲅ": "т",
    "ᲆ": "ъ",
    "ᲇ": "ѣ",
    "ᲈ": "ꙋ",
}
_FOLD_TABLE: Final = str.maketrans(_FOLD_VARIANTS)


def _quote(text: str) -> str:
    """One SQL string literal — used only for this module's own generated fold tables."""
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def _fold_sql(expr: str) -> str:
    """The SQL arm of the fold: DuckDB's expression for :func:`_normalize` of ``expr``.

    Generated from the same tables the Python arm reads, so the two cannot be edited apart.
    """
    blanks = " " * len(_FOLD_WHITESPACE)
    folded = (
        f"trim(regexp_replace(translate({expr}, {_quote(_FOLD_WHITESPACE)}, "
        f"{_quote(blanks)}), ' +', ' ', 'g'))"
    )
    folded = f"nfc_normalize(lower(nfc_normalize({folded})))"
    for source, replacement in _FOLD_EXPANSIONS:
        folded = f"replace({folded}, {_quote(source)}, {_quote(replacement)})"
    sources = _quote("".join(_FOLD_VARIANTS))
    targets = _quote("".join(_FOLD_VARIANTS.values()))
    return f"translate({folded}, {sources}, {targets})"


def _collapse(text: str) -> str:
    """Whitespace-collapsed, otherwise verbatim — the form sent to a source, unfolded."""
    return " ".join(text.split())


def _normalize(text: str) -> str:
    """The Python arm of the fold: a case-, whitespace- and composition-insensitive key.

    Script-agnostic — no transliteration, no language guess (FR-001) — and identical, by
    construction, to what :func:`_fold_sql` computes inside DuckDB. See the block comment
    above for why the base is ``str.lower()`` and not ``str.casefold()``.
    """
    folded = unicodedata.normalize("NFC", _collapse(text))
    folded = unicodedata.normalize("NFC", folded.lower())
    for source, replacement in _FOLD_EXPANSIONS:
        folded = folded.replace(source, replacement)
    return folded.translate(_FOLD_TABLE)


#: Coarse, symmetric containment prefilter pushed into DuckDB — a division name may contain
#: the query ("Foo" ⊂ "Foo District") or the query may contain it ("Foo old town" ⊃ "Foo").
#: `contains()` takes no LIKE metacharacters, so the user's string needs no escaping.
#:
#: **Both operands are folded in SQL and `$needle` is passed raw.** Even though the two
#: arms are now provably equal, a Python-folded needle would make that equality *load
#: bearing at runtime* rather than merely pinned by a test — each engine folds what it
#: compares. This only bounds what is read; `_match_confidence` re-scores every row.
_PRIMARY_FOLD: Final = _fold_sql("names.primary")
_ALTERNATE_FOLD: Final = _fold_sql("n")
_NEEDLE_FOLD: Final = _fold_sql("$needle")
_NAME_MATCH: Final = f"""contains({_PRIMARY_FOLD}, {_NEEDLE_FOLD})
   OR contains({_NEEDLE_FOLD}, {_PRIMARY_FOLD})
   OR coalesce(list_bool_or(list_transform(
        list_filter(coalesce(map_values(names.common), []), n -> length(trim(n)) > 0),
        n -> contains({_ALTERNATE_FOLD}, {_NEEDLE_FOLD})
          OR contains({_NEEDLE_FOLD}, {_ALTERNATE_FOLD}))), false)"""

_READ_DIVISIONS: Final = "read_parquet($src, hive_partitioning=1, filename=false)"

# ======================================================================================
# Two passes, because 97 % of this theme is geometry (the DU-03 performance defect)
# ======================================================================================
# Measured on release ``2026-07-22.0`` from ``parquet_metadata``: ``division_area`` is 8
# files / 544 row groups / 1,073,093 rows / **4.47 GB** compressed — of which the
# ``geometry`` column alone is **4.36 GB (97.5 %)**. Everything this resolver matches on is
# in the remaining 2.5 %: ``names.primary`` 1.1 MB, ``names.common`` 0.7 MB, ``sources``
# 1.8 MB, ``id`` 2.9 MB, ``bbox`` 2.4 MB *per file*.
#
# And **only ``bbox.*`` carries row-group statistics** in this release — ``id``, ``names``,
# ``country`` and ``subtype`` are all written with none — so a name predicate can prune
# nothing at all: DuckDB must visit every row group. Projecting the geometry alongside that
# predicate therefore streams the entire global theme to answer "which rows match?".
#
# So the lookup is split. :data:`_DIVISIONS_QUERY` answers *which* rows match, reading only
# the narrow columns; :data:`_GEOMETRY_QUERY` then fetches the polygons of the handful that
# survived, by id. Semantics are unchanged — same prefilter, same ``ORDER BY id``, same
# row limit — only the bytes differ.
#
# The one column that *can* prune is ``bbox``, and the theme is spatially clustered (median
# row-group span ≈ 4.8° lon × 3.0° lat), so a **caller-supplied** search window is the only
# lever that makes this path genuinely interactive. It is caller-supplied precisely because
# FR-001/SC-005 forbid this module knowing a single coordinate of its own.
#
# Measured end to end against the release, same machine and link, resolving one name:
#
#     212 s  before — one statement, name predicate + ``ST_AsWKB(geometry)``
#      73 s  after, no window — two passes, narrow projection, 32 read threads
#      18 s  after, with a caller-supplied window — and one precise candidate, not 20
#
# **73 s is not interactive, and no amount of query surgery makes it so**: with no statistics
# on any name column, matching *is* a full visit of 544 row groups, and reading a single
# narrow column of this theme cold measures 26–37 s on its own. The remaining fix is not a
# query — it is a cached divisions extract (see the class docstring), and until it exists
# :data:`DIVISIONS_TIMEOUT` is what keeps the failure bounded and legible.

#: The columns pass 1 reads, and **nothing else** — in particular no geometry, and only the
#: two ``sources`` fields :func:`_license_of` looks at rather than the whole 7-field struct,
#: because every extra parquet leaf is another 544 range requests against the release.
#: ``bbox.xmin``/``bbox.ymin`` come along as the anchor pass 2 is pruned by.
_DIVISIONS_COLUMNS: Final = """
SELECT id,
       names.primary AS primary_name,
       coalesce(map_values(names.common), []) AS alternate_names,
       list_transform(
         coalesce(sources, []),
         s -> {'property': s.property, 'license': s.license}
       ) AS licensing,
       bbox.xmin AS min_lon,
       bbox.ymin AS min_lat
"""

#: Pass 1 — *which* divisions match the name.
_DIVISIONS_QUERY: Final = f"""
{_DIVISIONS_COLUMNS}
FROM {_READ_DIVISIONS}
WHERE {_NAME_MATCH}
ORDER BY id
"""

#: Pass 1 with a caller-supplied search window — the same statement plus the places
#: adapter's overlap predicate (`commons/sources/overture.py`), which *does* prune row
#: groups. This is the only form of this lookup that is interactive against the release.
_DIVISIONS_QUERY_IN_WINDOW: Final = f"""
{_DIVISIONS_COLUMNS}
FROM {_READ_DIVISIONS}
WHERE bbox.xmin <= $max_lon AND bbox.xmax >= $min_lon
  AND bbox.ymin <= $max_lat AND bbox.ymax >= $min_lat
  AND ({_NAME_MATCH})
ORDER BY id
"""

#: Pass 2 — the polygons of the rows pass 1 kept, and nothing else. ``id`` is the
#: correctness filter; the bbox range only bounds what is *scanned*, and it is deliberately
#: **not** the overlap predicate the places adapter uses. Overlap (``xmin <= max AND
#: xmax >= min``) is a half-space test on a polygon theme: it keeps every row group west of
#: one bound, which prunes almost nothing. Ranging each corner against **the corner values
#: pass 1 actually read** turns it into a point probe per candidate, which is what row-group
#: statistics can answer. Both bounds come from the matched rows, never from this module.
_GEOMETRY_QUERY: Final = f"""
SELECT id, ST_AsWKB(geometry) AS wkb
FROM {_READ_DIVISIONS}
WHERE bbox.xmin BETWEEN $min_lon AND $max_lon
  AND bbox.ymin BETWEEN $min_lat AND $max_lat
  AND list_contains($ids, id)
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
    """The node's output: one polygon, stamped, in a named local frame."""

    polygon: BaseGeometry
    source: SourceRef
    #: IANA timezone id (``Europe/Athens``) derived from :attr:`polygon` — see
    #: :func:`~commons.frame.resolve_frame`. Never asked of the user, never model-supplied.
    timezone: str
    #: ISO 3166-1 alpha-2 (``GR``) derived from :attr:`polygon` by the same rule. What
    #: ``PH`` in an OSM ``opening_hours`` expression resolves against.
    country_code: str
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


class AreaFrameUnresolved(AreaInvalid):
    """The polygon is a fine area and has **no local frame** — open ocean, in practice.

    A subclass of :class:`AreaInvalid` so it lands on the contract's ``422`` without
    `api/areas.py` learning a new branch, and a *named* subclass so a caller that wants to
    say "that is open water" rather than "that is not an area" can. It is deliberately not
    an :class:`AreaNotResolved`: nothing was ambiguous and nothing failed to match — the
    request is simply unprocessable, because a day tour with no country has no public
    holiday calendar and no plan over it could be checked (`commons/frame.py`).
    """


class AreaLookupTimeout(TimeoutError):
    """The **authoritative** divisions lookup ran past its deadline and was abandoned.

    Deliberately *not* an :class:`AreaNotResolved`. "The source did not answer in time" and
    "there is no such place" are different facts, and collapsing them would turn a slow
    remote read into a confident ``404`` — the same silent-wrong-answer shape FAIL-005 was
    written about. It is not an :class:`AreaInvalid` either: the user's request was fine.

    It therefore propagates out of :func:`resolve_area` for the API to map, and the honest
    mapping is **``504 Gateway Timeout``** (`api/areas.py` maps ``AreaInvalid`` → 422 and
    ``AreaNotResolved`` → 404; until it learns this one, an unhandled ``500`` is still an
    honest "we failed", never a false "no such area").
    """


@runtime_checkable
class DivisionsLookup(Protocol):
    """Authoritative name → administrative area lookup (Overture divisions)."""

    def search(self, name: str) -> Sequence[AreaCandidate]: ...


@runtime_checkable
class Geocoder(Protocol):
    """Disambiguation fallback, consulted only when :class:`DivisionsLookup` is silent."""

    def search(self, name: str) -> Sequence[AreaCandidate]: ...


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


def _frame_of(polygon: BaseGeometry) -> LocalFrame:
    """Derive the area's clock and calendar, or refuse the area.

    The whole derivation is one call because `commons/frame.py` owns the rule; this function
    exists only to translate its refusal into the node's own error vocabulary, so the API
    keeps mapping one exception family and an unresolvable frame does not surface as a
    ``500``. Nothing here supplies a fallback — that is the point of the refusal.
    """
    try:
        return resolve_frame(polygon)
    except FrameUnresolved as error:
        raise AreaFrameUnresolved(str(error)) from error


def _user_area(polygon: BaseGeometry, source_id: str) -> ResolvedArea:
    """Stamp a geometry the user delimited themselves. ``kind="user"`` ⇒ not bundleable.

    The frame is derived from the ring even though the user drew it: a delimitation says
    *where*, never *which clock* — `docs/data/area.md` example 3 is explicit about it.
    """
    frame = _frame_of(polygon)
    return ResolvedArea(
        polygon=polygon,
        source=SourceRef(kind="user", id=source_id, license=USER_LICENSE),
        timezone=frame.timezone,
        country_code=frame.country_code,
    )


def _choose(name: str, candidates: Sequence[AreaCandidate]) -> ResolvedArea:
    """One strong match wins; several plausible ones are a question; none is a dead end."""
    strong = [c for c in candidates if c.confidence >= STRONG_CONFIDENCE]
    plausible = [c for c in candidates if c.confidence >= PLAUSIBLE_CONFIDENCE]
    shortlist = strong or plausible
    if len(shortlist) == 1:
        winner = shortlist[0]
        # The frame comes from the winning *polygon*, never from the name that found it: a
        # division called "Foo" says nothing about which clock Foo keeps, and a name that
        # implied one would be the model guessing geography by another route.
        frame = _frame_of(winner.polygon)
        return ResolvedArea(
            polygon=winner.polygon,
            source=winner.source,
            timezone=frame.timezone,
            country_code=frame.country_code,
            candidates=tuple(candidates),
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
        AreaFrameUnresolved: a usable area with no local frame (open ocean) — a subclass of
            ``AreaInvalid``, so an existing ``422`` mapping already covers it.
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
class _Countdown:
    """One wall-clock budget shared by both passes of a lookup — see :data:`DIVISIONS_TIMEOUT`."""

    seconds: float
    started: float

    @classmethod
    def of(cls, seconds: float) -> _Countdown:
        return cls(seconds=seconds, started=time.monotonic())

    def remaining(self) -> float:
        return self.seconds - (time.monotonic() - self.started)


@dataclass(frozen=True, slots=True)
class _DivisionMatch:
    """A row that passed the SQL prefilter *and* the Python re-score. Geometry still pending.

    Carrying the row's own ``bbox`` is what lets the geometry pass be pruned: it is the only
    column the theme publishes statistics for, and it comes from the data, never from here.
    """

    id: str
    name: str
    license_id: str
    confidence: float
    #: The row's own lower-left bbox corner — the value pass 2 probes the statistics with.
    min_lon: float
    min_lat: float


def _bounded(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    params: Mapping[str, Any],
    clock: _Countdown,
    what: str,
) -> list[Any]:
    """Run one statement under ``clock``, **abandoning** it rather than waiting forever.

    DuckDB has no query timeout, so the statement runs on a worker thread and the deadline
    is enforced by :meth:`~duckdb.DuckDBPyConnection.interrupt`, which unwinds the running
    query rather than leaking a thread that keeps pulling remote parquet.

    The deadline is a **ceiling on waiting, not a stopwatch**: unwinding lets in-flight HTTP
    range requests drain, so the call returns a few seconds late — measured at 16.3 s for a
    10 s budget against the hosted release. Bounded is the property being bought here; to
    the nearest second is not.
    """
    remaining = clock.remaining()
    if remaining <= 0.0:
        raise AreaLookupTimeout(f"the Overture divisions lookup ran out of time before {what}")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="divisions") as pool:
        future: Future[list[Any]] = pool.submit(
            lambda: list(connection.execute(sql, dict(params)).fetchall())
        )
        try:
            return future.result(timeout=remaining)
        except FutureTimeout:
            connection.interrupt()
            raise AreaLookupTimeout(
                f"the Overture divisions lookup exceeded {clock.seconds:g}s while {what} — "
                "the authoritative source did not answer in time. This says nothing about "
                "whether the area exists. Narrow the search with a window, or point the "
                "resolver at a local divisions extract"
            ) from None


@dataclass(frozen=True, slots=True)
class OvertureDivisions:
    """Name → administrative area over the Overture **divisions/division_area** theme.

    Read with DuckDB ``spatial`` (+ ``httpfs`` for the hosted release) exactly as
    :class:`~commons.sources.overture.OvertureAdapter` reads places. ``parquet`` points at a
    local file/glob so tests never touch the network; left ``None`` it reads the release.

    A DuckDB failure is **not** swallowed: an unreachable authoritative source must surface
    as an error, not as a false "no such area". A *slow* one is the same fact with a clock on
    it — see :class:`AreaLookupTimeout` and :data:`DIVISIONS_TIMEOUT`.

    The lookup is **two passes** (see the block comment above :data:`_DIVISIONS_QUERY`):
    which rows match, then the geometry of those rows. ``window`` is a caller-supplied
    ``[minLon, minLat, maxLon, maxLat]`` search box — the map viewport, the user's current
    area, anything the caller knows — and it is the difference between an interactive lookup
    and a minutes-long one, because ``bbox`` is the only column the theme indexes. It is a
    parameter and never a constant: FR-001/SC-005 mean this module knows no coordinates.

    **What would actually fix the unwindowed case**, since this class cannot: ``parquet``
    already accepts a local file or glob, so a build step that reads the release once and
    writes a divisions extract — the columns above, no geometry duplication beyond what is
    needed, sorted so ``bbox`` and ``names`` both carry row-group statistics — turns every
    later lookup into a local scan. The theme is 4.47 GB hosted but only ~110 MB of it is
    non-geometry; an extract that keeps ``id``/``names``/``sources``/``bbox`` plus the
    geometry is a one-off download, and one that drops geometry entirely (fetching polygons
    from the release by id, pruned by ``bbox``, exactly as pass 2 already does) is ~110 MB.
    That is a deliverable, not a parameter, which is why it is written here and not done.
    """

    kind: ClassVar[SourceKind] = "overture"

    parquet: str | None = None
    release: str = DEFAULT_RELEASE
    limit: int = 20
    s3_region: str = "us-west-2"
    #: Wall-clock ceiling for the whole lookup, both passes together.
    timeout: float = DIVISIONS_TIMEOUT
    #: Request concurrency for the hosted release; ignored for a local ``parquet``.
    read_threads: int = DIVISIONS_READ_THREADS
    #: Optional caller-supplied ``(minLon, minLat, maxLon, maxLat)`` search window.
    window: tuple[float, float, float, float] | None = None

    def search(self, name: str) -> Sequence[AreaCandidate]:
        needle = _normalize(name)
        if not needle:
            return ()
        clock = _Countdown.of(self.timeout)
        connection = self._connect()
        try:
            matches = self._match(connection, _collapse(name), needle, clock)
            if not matches:
                return ()
            polygons = self._polygons(connection, matches, clock)
        finally:
            connection.close()
        return tuple(
            AreaCandidate(
                name=match.name,
                polygon=polygon,
                source=SourceRef(
                    kind=self.kind,
                    id=match.id,
                    license=match.license_id,
                    attribution=_attribution_for(match.license_id, DIVISIONS_ATTRIBUTION),
                ),
                confidence=match.confidence,
            )
            for match in matches
            if (polygon := polygons.get(match.id)) is not None
        )

    # -- the two passes ---------------------------------------------------------------

    def _match(
        self,
        connection: duckdb.DuckDBPyConnection,
        raw: str,
        needle: str,
        clock: _Countdown,
    ) -> list[_DivisionMatch]:
        """Pass 1 — *which* divisions match, re-scored in Python. No geometry is read."""
        params: dict[str, Any] = {"src": self._source, "needle": raw}
        sql = _DIVISIONS_QUERY
        if self.window is not None:
            sql = _DIVISIONS_QUERY_IN_WINDOW
            params |= self._window_params(self.window)
        rows = _bounded(
            connection, f"{sql} LIMIT {int(self.limit)}", params, clock, "matching the name"
        )
        matches: list[_DivisionMatch] = []
        for division_id, primary, alternates, licensing, min_lon, min_lat in rows:
            license_id = _license_of(licensing)
            confidence = _match_confidence(needle, (primary, *(alternates or ())))
            if not division_id or license_id is None or confidence <= 0.0:
                continue
            if min_lon is None or min_lat is None:
                continue  # no bbox ⇒ no anchor to fetch its geometry by; the row is unusable
            matches.append(
                _DivisionMatch(
                    id=str(division_id),
                    name=str(primary or raw),
                    license_id=license_id,
                    confidence=confidence,
                    min_lon=float(min_lon),
                    min_lat=float(min_lat),
                )
            )
        return matches

    def _polygons(
        self,
        connection: duckdb.DuckDBPyConnection,
        matches: Sequence[_DivisionMatch],
        clock: _Countdown,
    ) -> dict[str, BaseGeometry]:
        """Pass 2 — the polygons of exactly those rows, keyed by id.

        The bounds are the **range of the matches' own lower-left corners**, so the geometry
        scan is pruned to the row groups whose ``bbox.xmin``/``bbox.ymin`` statistics could
        contain one. A row whose geometry is missing or is not a usable area is simply
        absent from the result: the same drop the single-pass version did inline, in the
        same place in the pipeline.
        """
        params: dict[str, Any] = {"src": self._source, "ids": [match.id for match in matches]}
        params |= self._window_params(
            (
                min(match.min_lon for match in matches),
                min(match.min_lat for match in matches),
                max(match.min_lon for match in matches),
                max(match.min_lat for match in matches),
            )
        )
        rows = _bounded(connection, _GEOMETRY_QUERY, params, clock, "fetching the geometry")
        polygons: dict[str, BaseGeometry] = {}
        for division_id, wkb in rows:
            if not division_id or wkb is None:
                continue
            try:
                polygons[str(division_id)] = _validate_area(from_wkb(bytes(wkb)))
            except (AreaInvalid, ShapelyError):
                continue
        return polygons

    # -- plumbing ---------------------------------------------------------------------

    @property
    def _source(self) -> str:
        return self.parquet or DIVISIONS_SOURCE_TEMPLATE.format(release=self.release)

    @staticmethod
    def _window_params(window: tuple[float, float, float, float]) -> dict[str, Any]:
        """A search box → the four bound parameters both windowed statements read."""
        min_lon, min_lat, max_lon, max_lat = window
        return {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        }

    def _connect(self) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect()
        try:
            connection.execute("INSTALL spatial; LOAD spatial;")
            if self.parquet is None:
                connection.execute("INSTALL httpfs; LOAD httpfs;")
                connection.execute("SET s3_region = $region;", {"region": self.s3_region})
                # Request concurrency, not parallelism — see DIVISIONS_READ_THREADS.
                connection.execute("SET threads = $threads;", {"threads": self.read_threads})
        except BaseException:
            connection.close()
            raise
        return connection


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
