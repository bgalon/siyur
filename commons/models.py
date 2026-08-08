"""The commons data spine — ``SourcedValue``, ``SiteRecordV1``, ``ItineraryV1``,
``RouteLegV1`` and ``BundleManifestV1`` (pydantic v2).

**Schema ground truth: the cards in `docs/data/`** — [`poi-site.md`](../docs/data/poi-site.md)
(itself ground-truthed to `docs/design/tech-design.md` §1.0–§1.2), plus
[`itinerary.md`](../docs/data/itinerary.md), [`route-leg.md`](../docs/data/route-leg.md),
[`bundle-manifest.md`](../docs/data/bundle-manifest.md) and
[`tile-source.md`](../docs/data/tile-source.md) for the slice-002 models. Field names, types
and the ``SourceRef.kind`` enum are transcribed from those cards; where any other doc
differs, the card wins. Nothing here re-defines a schema.

Two invariants are enforced *structurally* rather than by convention, because both are
Constitution Article V ("provenance is mechanical") obligations that no caller may forget:

**1. No unstamped value can be constructed** (FR-003 / SC-002 / data-model §5.1). Every
fact-bearing slot on :class:`SiteRecordV1` is typed ``SourcedValue[...]``, whose ``source``
is a required :class:`SourceRef`. A bare ``"Palace of the Grand Master"`` is not a
``SourcedValue`` and fails validation, so it can never be persisted or displayed. The
usual escape hatches are closed on :class:`StampedModel`: models are ``frozen`` (no
post-hoc ``value.source = None``), ``extra="forbid"``, ``model_construct()`` (pydantic's
validation-bypassing constructor) raises, and ``model_copy(update=...)`` re-validates.

**2. The license quarantine** (data-model §5.2): ``bundleable`` must equal
``commons.licenses.bundleable(source.kind, source.license)`` — an equivalence, so the stamp
cannot be author-set ``True`` over a non-allowlisted license, nor ``False`` over an
allowlisted one. :meth:`SourcedValue.stamp` derives it for ingestion code.

**Only a ``SourcedValue`` carries a ``bundleable`` field; everything else must derive it.**
:class:`Story`, :class:`RouteLegV1` and (in ``planner``) ``ResolvedArea`` / ``AreaCandidate``
are fact-bearing structures that carry a **bare** :class:`SourceRef` and *no* ``bundleable``,
``confidence`` or quarantine validator — see `poi-site.md` and `route-leg.md`, and ADR-0025
ruling 8. A quarantine filter must therefore call
``licenses.bundleable(x.source.kind, x.source.license)`` for them, never read a stamp off
them. The rule is **structural, not a ``Story`` special case**: a filter written as
``derive if isinstance(v, Story) else v.bundleable`` reaches ``routing.legs``, finds no
attribute, and — with the likely ``getattr(v, "bundleable", False)`` repair — drops **every
walking leg from every bundle**, silently, past every hash and path check. Do not "fix" the
asymmetry by adding a ``bundleable`` field here; the derivation is the contract.

**Two clocks that must never be interchangeable** (`itinerary.md` "Timezone",
data-model §5). Planned times — :attr:`Stop.planned_start`, :attr:`TimelineEntry.start` — are
**naive ``datetime.time``** in the *area's* wall clock, given a calendar frame by
:attr:`ItineraryV1.date` and the ``area`` row's ``timezone``/``country_code``. Audit
timestamps — ``updated_at``, ``fetched_at``, ``created_at`` — are **tz-aware UTC
``datetime``**. :func:`_require_utc` and :func:`_require_naive_local` are the two guards, and
there is deliberately **no conversion helper between them**: converting one to the other is
always a decision that needs the area's timezone, which lives on a row this module does not
read.

Slice 001 populates a subset (`specs/001-research-cited-sites/data-model.md` §2): stories
are deferred to slice 002 and M2+ fields stay empty — **empty is valid, the schema is
unchanged**. Slice 002 adds the planned day, its legs and the frozen bundle manifest;
``meals``, ``variants``, ``RouteLegV1.variant``, ``schematic`` and ``Story.claims`` exist and
stay empty (M2+).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Final, Literal, NoReturn, Self
from uuid import UUID, uuid4

import rfc8785
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    StringConstraints,
    WithJsonSchema,
    field_validator,
)
from pydantic import model_validator as pydantic_model_validator
from shapely import LineString

from commons import licenses
from commons.geo import (
    CRS,
    MAX_LAT,
    MAX_LON,
    MIN_LAT,
    MIN_LON,
    GeometryError,
    Wgs84Point,
    validate_lat,
    validate_lon,
)
from commons.licenses import SourceKind

__all__ = [
    "ArtifactRef",
    "Bcp47Tag",
    "Budgets",
    "BundleContent",
    "BundleIntegrity",
    "BundleManifestV1",
    "BundleRouting",
    "BundleTiles",
    "Claim",
    "FieldConflict",
    "GlyphsRef",
    "ItineraryV1",
    "Rating",
    "ReviewSummary",
    "RouteLegV1",
    "SchematicRef",
    "Sha256Hex",
    "SiteRecordV1",
    "SourceKind",
    "SourceRef",
    "SourcedValue",
    "StampedModel",
    "Stop",
    "Story",
    "TileSourceV1",
    "Timeline",
    "TimelineEntry",
    "Wgs84LineString",
    "WithheldReason",
    "WithheldValue",
]

# BCP-47 subtags — `en`, `el`, `el-Latn`, `ja-Hira`, `pt-BR`, `zh-Hant-TW` (schema card
# `names`; data-model §3). Canonical casing: lowercase language, Titlecase script,
# UPPERCASE (or 3-digit) region — so `EN`, `en_US` and `english` are rejected rather than
# quietly creating a second key for the same language.
BCP47_PATTERN: Final[str] = r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$"

#: A BCP-47 language tag used as a key in ``names`` / ``text_by_lang``.
Bcp47Tag = Annotated[str, StringConstraints(pattern=BCP47_PATTERN)]

# FieldConflict.resolution: "unresolved" | "picked:<source.id>" | "user-override" (card).
_RESOLUTION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(unresolved|user-override|picked:.+)$")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _require_utc(value: datetime, field_name: str) -> datetime:
    """`timestamptz` discipline: naive datetimes are ambiguous, so they are refused."""
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware (timestamptz, stored UTC)")
    return value.astimezone(UTC)


def _require_calendar_date(value: object, field_name: str) -> object:
    """A calendar day is not an instant, and pydantic will happily conflate the two.

    ``ItineraryV1.date`` was the one field in the two-clocks discipline with no guard, and
    pydantic's lax mode accepts a ``datetime`` (or an offset-bearing ISO string) for a
    ``date`` field **whenever the time component is zero**, keeping that datetime's own
    calendar day regardless of its offset. Verified: a tz-aware ``2026-07-25T00:00:00Z``
    is accepted and yields ``date(2026, 7, 25)``.

    That is not hypothetical. The common "seed today" idiom —
    ``datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)`` — for a
    traveller in Auckland (UTC+13) at 10:00 local on the 26th produces
    ``2026-07-25T00:00:00Z``, so the plan is stamped **the 25th** for a day being walked on
    the **26th**. Every ``opening_hours`` evaluation then runs against the wrong weekday
    (Saturday rules on a Sunday), the feasibility verdict is wrong, and the wrong date is
    frozen into ``content.itinerary`` where nothing re-checks it — offline, undiagnosable.

    The frame is supplied by this date plus the area row's timezone; an instant carries its
    own frame and therefore cannot express "the day the traveller is having".
    """
    if isinstance(value, datetime):
        raise ValueError(
            f"{field_name} must be a calendar date, not an instant — got a datetime "
            f"({value!r}). A UTC midnight is a different calendar day either side of the "
            "date line; pass date(...) built in the AREA's frame, never "
            "datetime.now(UTC) truncated (docs/data/itinerary.md 'Timezone')"
        )
    if isinstance(value, str) and ("T" in value or "t" in value or "+" in value[10:]):
        raise ValueError(
            f"{field_name} must be a plain ISO calendar date (YYYY-MM-DD), got {value!r}. "
            "A time component or UTC offset means an instant was serialized where a day "
            "was meant"
        )
    return value


def _require_naive_local(value: time, field_name: str) -> time:
    """Wall-clock discipline: an **area-local** planned time carries no offset.

    The mirror of :func:`_require_utc`, and not symmetry for its own sake. pydantic already
    refuses a ``datetime`` in a ``time`` field and a ``time`` in a ``datetime`` field, so the
    two *types* cannot be swapped — but it accepts ``time(10, 0, tzinfo=UTC)`` happily, and
    coerces a bare ``36000`` (seconds since midnight) into ``time(10, 0, tzinfo=UTC)``.
    Either would seat a UTC instant in a slot whose entire meaning is "the clock on the wall
    where the traveller is standing", to be compared against ``opening_hours`` in the area's
    frame — hours wrong, and wrong silently, offline, with no second opinion available.
    """
    if value.tzinfo is not None:
        raise ValueError(
            f"{field_name} must be a naive area-local wall-clock time carrying no UTC "
            f"offset, got tzinfo={value.tzinfo!r}. The local frame comes from "
            "ItineraryV1.date plus the area row's timezone; UTC belongs on audit "
            "timestamps only (docs/data/itinerary.md 'Timezone')"
        )
    return value


#: Lowercase hex of a SHA-256 over an artifact's **raw bytes** (`bundle-manifest.md`
#: "Integrity discipline"). The casing is part of the contract, not a preference: the
#: launch-time verifier is TypeScript and compares these as strings.
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def _validate_bundle_path(value: str) -> str:
    """A path *inside* the downloaded bundle — so it must not be able to address anything else.

    Every one of these is resolved against the OPFS root by a client that trusts the manifest
    it downloaded; an absolute path or a ``..`` segment there is either a bug or an escape.
    Checked once here rather than at each of the seven read sites. ``glyphs`` legitimately
    names a directory prefix (``glyphs/``), so a trailing slash is fine.

    (Written as a validator rather than a ``StringConstraints`` pattern because pydantic-core
    compiles patterns with Rust's ``regex``, which has no look-around.)
    """
    if not value:
        raise ValueError("a bundle path may not be empty")
    if value.startswith("/"):
        raise ValueError(f"bundle path {value!r} must be relative to the bundle root")
    if "\\" in value or "\x00" in value:
        raise ValueError(f"bundle path {value!r} contains a path separator we do not emit")
    if any(segment == ".." for segment in value.split("/")):
        raise ValueError(f"bundle path {value!r} escapes the bundle with a '..' segment")
    return value


#: A relative artifact path inside the bundle archive (`tiles/rhodes.pmtiles`, `glyphs/`).
BundlePath = Annotated[str, AfterValidator(_validate_bundle_path)]


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Serialize per **RFC 8785 (JCS)** — the one serialization a digest may be taken over.

    ADR-0025 A5, and the reason a named standard replaced a described one: the writer is
    Python and the verifier is TypeScript, and ``json.dumps(o, sort_keys=True)`` disagrees
    with ``JSON.stringify(o)`` on both halves of every real bundle — it escapes the ``©``
    that every ODbL attribution carries, and renders ``28.0`` where JS renders ``28``, while
    ``bbox`` is the schema's only float array. Sorted keys pin neither. JCS pins string
    escaping and number formatting as well as key order, so **never hand-roll this**.

    Input must already be JSON-native (``model_dump(mode="json")``); ``rfc8785`` refuses a
    ``date``/``UUID`` rather than guessing a representation, which is the behaviour we want.
    """
    return rfc8785.dumps(payload)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    """Lowercase-hex SHA-256 over :func:`_canonical_json` of ``payload``."""
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _coerce_linestring(value: object) -> LineString:
    """Validate a route geometry into an EPSG:4326 ``LineString`` — **(lon, lat)**.

    Mirrors :data:`commons.geo.Wgs84Point`'s pattern for the one geometry type the point
    validators do not cover (leg polylines, `route-leg.md` "CRS"). Accepts a shapely
    ``LineString``, a GeoJSON ``LineString`` mapping, or a sequence of ``(lon, lat)`` pairs;
    every vertex is range-checked per axis, so a (lat, lon) polyline outside ±90 fails loudly
    instead of drawing a route through the sea.
    """
    if isinstance(value, LineString):
        return _validate_linestring(value)
    if isinstance(value, Mapping):
        geom_type = value.get("type")
        if geom_type != "LineString":
            raise GeometryError(f"expected a GeoJSON LineString, got type={geom_type!r}")
        return _linestring_from_coordinates(value.get("coordinates"))
    if not isinstance(value, (str, bytes)) and isinstance(value, Sequence):
        return _linestring_from_coordinates(value)
    raise GeometryError(
        "expected a LineString, a GeoJSON LineString mapping, or a sequence of "
        f"(lon, lat) pairs, got {type(value).__name__}"
    )


def _linestring_from_coordinates(coordinates: object) -> LineString:
    if isinstance(coordinates, (str, bytes)) or not isinstance(coordinates, Sequence):
        raise GeometryError(
            f"LineString coordinates must be a sequence of [lon, lat] pairs, got {coordinates!r}"
        )
    # RFC 7946: a LineString has ≥2 positions. (A *route* of exactly 2 is a straight line
    # pretending to be one — that is a routing-quality check, and it lives in
    # tests/test_routing.py, not in the schema.)
    if len(coordinates) < 2:
        raise GeometryError(
            f"a LineString needs at least 2 vertices ({CRS}), got {len(coordinates)}"
        )
    vertices: list[tuple[float, float]] = []
    for index, position in enumerate(coordinates):
        if isinstance(position, (str, bytes)) or not isinstance(position, Sequence):
            raise GeometryError(f"vertex {index} must be a [lon, lat] pair, got {position!r}")
        if len(position) != 2:
            raise GeometryError(
                f"vertex {index} must be exactly [lon, lat] ({CRS}, 2-D), "
                f"got {len(position)} values"
            )
        vertices.append((validate_lon(position[0]), validate_lat(position[1])))
    return _validate_linestring(LineString(vertices))


def _validate_linestring(geom: LineString) -> LineString:
    if geom.is_empty:
        raise GeometryError("expected a LineString geometry, got an empty one")
    if geom.has_z:
        raise GeometryError("expected a 2-D LineString; bundled geometry carries no z")
    # shapely 2.x: `.geom_type` / `.is_valid` (1.x `.type` is gone — AGENTS.md geo table).
    if not geom.is_valid:
        raise GeometryError(f"expected a valid LineString geometry, got {geom.geom_type}")
    for lon, lat in geom.coords:
        validate_lon(lon)
        validate_lat(lat)
    return geom


def _linestring_to_geojson(geom: LineString) -> dict[str, Any]:
    """Serialise to a GeoJSON ``LineString`` — positions are ``[lon, lat]`` (RFC 7946)."""
    line = _validate_linestring(geom)
    return {"type": "LineString", "coordinates": [[x, y] for x, y in line.coords]}


_LINESTRING_JSON_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "title": "GeoJSON LineString (EPSG:4326)",
    "properties": {
        "type": {"const": "LineString"},
        "coordinates": {
            "type": "array",
            "description": "ordered [lon, lat] positions in EPSG:4326",
            "minItems": 2,
            "items": {
                "type": "array",
                "prefixItems": [
                    {"type": "number", "minimum": MIN_LON, "maximum": MAX_LON},
                    {"type": "number", "minimum": MIN_LAT, "maximum": MAX_LAT},
                ],
                "minItems": 2,
                "maxItems": 2,
            },
        },
    },
    "required": ["type", "coordinates"],
}

#: A shapely ``LineString`` usable as a pydantic field: validated on the way in, GeoJSON on
#: the way out. Lives here rather than in :mod:`commons.geo` only because it is used by
#: :class:`RouteLegV1` and nothing else yet.
Wgs84LineString = Annotated[
    LineString,
    PlainValidator(_coerce_linestring),
    PlainSerializer(_linestring_to_geojson, return_type=dict[str, Any]),
    WithJsonSchema(_LINESTRING_JSON_SCHEMA),
]


class StampedModel(BaseModel):
    """Base for every commons model: frozen, closed, and validation-bypass-proof.

    ``frozen`` + ``extra="forbid"`` alone still leave two doors open through which an
    unvalidated (and therefore possibly unstamped) model can appear; both are shut here so
    that "a value without provenance is unconstructible" is a property of the type, not a
    review comment.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        validate_assignment=True,
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> NoReturn:
        """Disabled: it is pydantic's *skip validation* constructor (Article V).

        It would happily build a ``SourcedValue`` with no ``source`` or a ``bundleable``
        stamp that contradicts its license. Use ``model_validate`` / the normal
        constructor / :meth:`SourcedValue.stamp`.
        """
        raise TypeError(
            f"{cls.__name__}.model_construct() is disabled: it bypasses validation, and "
            "every commons value must carry a validated provenance + license stamp "
            "(Constitution Article V). Use the constructor or model_validate()."
        )

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Like ``BaseModel.model_copy``, but an ``update`` is **re-validated**.

        Stock ``model_copy(update=...)`` writes straight into ``__dict__``, which would let
        a caller strip a ``source`` or flip a ``bundleable`` stamp on a frozen model.
        """
        if not update:
            return super().model_copy(deep=deep)
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return type(self).model_validate({**fields, **update})


class SourceRef(StampedModel):
    """Where a value came from — the provenance half of the stamp (schema card)."""

    kind: SourceKind
    #: GERS id / OSM ``type/id`` / QID / article title / URL.
    id: str = Field(min_length=1)
    url: str | None = None
    #: SPDX id | ``"proprietary"`` | ``"user-owned"``; drives ``bundleable``.
    #: Registry: `DATA-LICENSES.md`.
    license: str = Field(min_length=1)
    #: Rendered string when the license requires it (ODbL, CC-BY-SA).
    attribution: str | None = None


class SourcedValue[T](StampedModel):
    """A fact plus its stamp. **Every** value Siyur shows is one of these, never bare."""

    value: T
    source: SourceRef
    #: May this be baked into an offline bundle? License-gated — see :mod:`commons.licenses`.
    bundleable: bool
    #: Curation/merge confidence, [0..1].
    confidence: float = Field(ge=0.0, le=1.0)
    #: UTC date we fetched/derived it; drives staleness / refresh-on-reuse.
    observed_at: date

    @pydantic_model_validator(mode="after")
    def _enforce_quarantine(self) -> Self:
        expected = licenses.bundleable(self.source.kind, self.source.license)
        if self.bundleable is not expected:
            raise ValueError(
                f"bundleable={self.bundleable} contradicts the license quarantine: "
                f"{licenses.quarantine_reason(self.source.kind, self.source.license)} "
                f"⇒ bundleable must be {expected}"
            )
        return self

    @classmethod
    def stamp(
        cls,
        *,
        value: T,
        source: SourceRef,
        confidence: float,
        observed_at: date,
    ) -> Self:
        """Stamp a value, **deriving** ``bundleable`` from the quarantine rule.

        The ingestion path: adapters never decide bundleability, the registry does.
        """
        return cls(
            value=value,
            source=source,
            bundleable=licenses.bundleable(source.kind, source.license),
            confidence=confidence,
            observed_at=observed_at,
        )


class Claim(StampedModel):
    """[M2+] Per-claim provenance for a sentence/span of a story (schema card)."""

    #: The span of story text this source backs. (The card writes ``{span, SourceRef}``
    #: without pinning a representation; the minimal reading — the text itself — is used.)
    span: str
    source: SourceRef


class Story(StampedModel):
    """An adapted CC-BY-SA narration with per-article attribution (PRD §7).

    **Not a :class:`SourcedValue`** — see the module docstring. A story carries a *bare*
    :class:`SourceRef` and no ``bundleable`` stamp, because the whole story is one attributed
    adaptation of one article rather than a stamped value in a field map. Quarantine must
    therefore **derive**: ``licenses.bundleable(story.source.kind, story.source.license)``.
    Reading a ``bundleable`` attribute off a story gets ``None`` and silently drops (or
    silently ships) every narration in the bundle. ADR-0025 ruling 8 accepts this asymmetry
    for M1 and names it as debt; ADR-0025 A4 is where it gets folded in if a ``SiteRecordV2``
    is ever opened.
    """

    #: ``en`` canonical (+ translations at M3).
    text_by_lang: dict[Bcp47Tag, str]
    #: CC-BY-SA article; attribution required.
    source: SourceRef
    #: UTC date the article **revision** was fetched — the staleness key for bundled
    #: narration (ADR-0025 ruling 8). Required, and deliberately without a
    #: ``default_factory``: "today" is not an observation, and a default would let a story
    #: adapted from a two-year-old cached revision claim to be fresh.
    observed_at: date
    claims: tuple[Claim, ...] = ()


class Rating(StampedModel):
    """[M2+] One provider's rating inside a :class:`ReviewSummary`."""

    provider: str
    stars: float
    count: int | None = None
    url: str


class ReviewSummary(StampedModel):
    """[M2+] Link-and-summarize reviews. Always ``bundleable=false``, live-online-only.

    Its sources are ``kind="review_provider"``, which :mod:`commons.licenses` pins to
    ``bundleable=False`` unconditionally — the quarantine needs no special case here.
    """

    ratings: tuple[Rating, ...] = ()
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def _fetched_at_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, "fetched_at")


class FieldConflict(StampedModel):
    """An unresolved disagreement between sources — merge never discards a source (§1.2)."""

    field: str = Field(min_length=1)
    #: The disagreeing values, each still fully stamped.
    candidates: tuple[SourcedValue[Any], ...]
    #: ``"unresolved"`` | ``"picked:<source.id>"`` | ``"user-override"``.
    resolution: str

    @field_validator("resolution")
    @classmethod
    def _known_resolution(cls, value: str) -> str:
        if not _RESOLUTION_PATTERN.match(value):
            raise ValueError(
                "resolution must be 'unresolved', 'user-override' or 'picked:<source.id>', "
                f"got {value!r}"
            )
        return value


class SiteRecordV1(StampedModel):
    """The commons record — one row per real-world place, globally shared (schema card).

    Slice 001 fills ``id``, ``location``, ``names``, ``categories``, ``schema_ver``,
    ``updated_at`` plus ``address``/``opening_hours`` where the source carries them;
    ``stories`` and the M2+ fields stay empty and that is valid.
    """

    #: Our stable id (server-generated).
    id: UUID = Field(default_factory=uuid4)
    #: Overture GERS — cross-source join key when present; usually null between Overture↔OSM.
    gers_id: str | None = None
    #: Keyed by **BCP-47 subtag** (``en``, ``el``, ``el-Latn``), not bare language codes.
    names: dict[Bcp47Tag, SourcedValue[str]] = Field(default_factory=dict)
    #: EPSG:4326 (lon, lat); from authoritative geodata only, never model-emitted (FR-005).
    location: SourcedValue[Wgs84Point]
    #: Overture ``basic_category`` + OSM tags.
    categories: tuple[SourcedValue[str], ...] = ()
    #: Source scripts are untrustworthy — validate, never transliterate (FAIL-001).
    address: SourcedValue[str] | None = None
    #: opening_hours.js syntax; evaluated in the area's local wall-clock time.
    opening_hours: SourcedValue[str] | None = None
    #: ≥1 adapted CC-BY-SA story at M1; **empty for slice 001** (FR-011) and valid.
    stories: tuple[Story, ...] = ()
    #: Free text; user notes are ``source.kind="user"``, stored private, never auto-published.
    notes: tuple[SourcedValue[str], ...] = ()
    phone: SourcedValue[str] | None = None
    #: Tickets/fees.
    price: SourcedValue[str] | None = None
    accessibility: SourcedValue[str] | None = None
    #: Official / booking.
    website: SourcedValue[str] | None = None
    links: tuple[SourcedValue[str], ...] = ()
    reviews: ReviewSummary | None = None
    conflicts: tuple[FieldConflict, ...] = ()
    #: UTC.
    updated_at: datetime = Field(default_factory=_utcnow)
    schema_ver: Literal["SiteRecordV1"] = "SiteRecordV1"

    @field_validator("updated_at")
    @classmethod
    def _updated_at_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, "updated_at")


# --- the planned day (docs/data/itinerary.md, docs/data/route-leg.md) -------------


class Stop(StampedModel):
    """One place in the day: which site, where in the order, when, and for how long."""

    #: References :attr:`SiteRecordV1.id`; the site must already exist in the commons.
    site_id: UUID
    #: 0-based position in the day. **A ``Stop`` has no id of its own** — the timeline
    #: addresses it by this position (``stop_order``), which is the same address
    #: :attr:`RouteLegV1.from_stop` / ``to_stop`` use. A day that visits one site twice has
    #: two stops with one ``site_id`` and two ``order``s, which a *site* UUID cannot
    #: disambiguate: that was the bug ADR-0025 ruling 4 closed.
    order: int = Field(ge=0)
    #: **Naive** area-local wall clock (``HH:MM``), read on :attr:`ItineraryV1.date` in the
    #: area's timezone. Never UTC, never the traveller's device clock.
    planned_start: time
    #: Minutes spent here.
    dwell_min: int = Field(ge=0)

    @field_validator("planned_start")
    @classmethod
    def _planned_start_is_area_local(cls, value: time) -> time:
        return _require_naive_local(value, "planned_start")


class TimelineEntry(StampedModel):
    """One row of the day's timeline — **exactly one** of ``stop_order`` / ``leg_id``."""

    #: The :attr:`Stop.order` this entry times, when it times a stop.
    stop_order: int | None = Field(default=None, ge=0)
    #: The :attr:`RouteLegV1.id` this entry times, when it times a leg.
    leg_id: str | None = Field(default=None, min_length=1)
    #: **Naive** area-local wall clock, as :attr:`Stop.planned_start`.
    start: time
    duration_min: int = Field(ge=0)

    @field_validator("start")
    @classmethod
    def _start_is_area_local(cls, value: time) -> time:
        return _require_naive_local(value, "start")

    @pydantic_model_validator(mode="after")
    def _addresses_exactly_one_thing(self) -> Self:
        """An entry times a stop **or** a leg. Both or neither is not a variation.

        Neither would render as an unlabelled block in the offline timeline; both would make
        the entry's meaning depend on which key the reader checks first.
        """
        addressed = [name for name in ("stop_order", "leg_id") if getattr(self, name) is not None]
        if len(addressed) != 1:
            raise ValueError(
                "a TimelineEntry addresses exactly one of stop_order (a Stop.order) or "
                f"leg_id (a RouteLegV1.id); got {addressed or 'neither'}"
            )
        return self


class Timeline(StampedModel):
    """The ordered day. Simple times/durations at M1; the rich timeline is M2 (PRD §13 #5)."""

    #: Ordered by the producer. Deliberately **not** validated as monotonic in ``start``: a
    #: day that runs past midnight is ordered but not ascending, and the schema is not the
    #: place to decide that question — feasibility is (``planner/feasibility.py``).
    entries: tuple[TimelineEntry, ...] = ()


class Budgets(StampedModel):
    """The feasibility limits the plan must hold within (`itinerary.md` "Feasibility")."""

    #: Total walking for the day, **metres**.
    walking_m: float = Field(ge=0.0)
    #: Total elapsed day, **hours** (float).
    hours: float = Field(ge=0.0)


class RouteLegV1(StampedModel):
    """A precomputed walking leg between two stops — Valhalla-produced (`route-leg.md`).

    **Not a :class:`SourcedValue`**: like :class:`Story`, it carries a bare
    :class:`SourceRef` and no ``bundleable`` field, so quarantine derives (module docstring).

    The stamp every M1 leg carries is fixed by the card — ``kind="osm"``,
    ``id="valhalla:pedestrian"``, ``license="ODbL-1.0"``,
    ``attribution="© OpenStreetMap contributors"`` — because routing over OSM data is a
    Produced Work of OSM, and there is **no ``SourceKind`` for a routing engine**: the engine
    is named inside ``id`` as ``<engine>:<costing>``. That convention is applied where legs
    are *created* (``commons/routing.py``), not asserted here, so that the one place deciding
    a leg's provenance stays the one place that fetched it.
    """

    #: Unique **within the itinerary** (e.g. ``leg-0``); what ``TimelineEntry.leg_id`` names.
    id: str = Field(min_length=1)
    #: The :attr:`Stop.order` of the origin / destination — positions, never UUIDs.
    from_stop: int = Field(ge=0)
    to_stop: int = Field(ge=0)
    mode: Literal["walk"] = "walk"
    #: Decoded leg polyline, EPSG:4326 ``[[lon, lat], …]``.
    geometry: Wgs84LineString
    #: Metres.
    distance_m: float = Field(ge=0.0)
    #: Seconds, at Valhalla's default pedestrian speed. Legs are duration-only — they carry
    #: no wall clock; the itinerary's timeline is what places them in area-local time.
    duration_s: int = Field(ge=0)
    source: SourceRef
    #: The card allows an embedded leg to omit this; a default means it is always present on
    #: the way out and accepted either way on the way in.
    schema_ver: Literal["RouteLegV1"] = "RouteLegV1"
    #: [M2+] which Plan variant this leg belongs to; the base plan is ``None``.
    variant: Literal["B", "C"] | None = None


class ItineraryV1(StampedModel):
    """The planned day — composed, **per-user, private** (`docs/data/itinerary.md`).

    Personal data: it lives in ``user_plan`` row-scoped to ``user_id`` and is never written
    to the commons (PRD §13 #4). The compile step freezes this object itself into
    ``BundleManifestV1.content.itinerary``, which is what the offline timeline renders from.

    **The feasibility verdict is not here.** It is server state on the ``user_plan`` row
    (``feasible`` + ``violations``), because a plan is a description of a day and not a
    judgement about it — and because ``extra="forbid"`` means an ad-hoc ``_feasibility`` key
    is unconstructible anyway (ADR-0025 ruling 3).
    """

    id: UUID = Field(default_factory=uuid4)
    #: The auth subject; the row-level scope key that *is* the privacy boundary.
    user_id: str = Field(min_length=1)
    #: The resolved area this plan covers — and the **only** source of the ``timezone`` /
    #: ``country_code`` every local time here is read in. The itinerary never restates them.
    area_id: UUID
    #: The calendar date the day is planned for, in the area's local frame — **M1 and
    #: required**. Without it a ``planned_start`` of ``10:00`` is not an instant at all, and
    #: opening hours cannot be evaluated: weekday rules, ``PH`` and seasonal ranges all need
    #: the date (ADR-0025 ruling 2). The field name shadows :class:`datetime.date` inside
    #: this class body only, which is what the card names it; the annotation resolves against
    #: the module globals, so the type is unaffected.
    date: date
    #: Presentation language (``en`` at M1).
    lang: Bcp47Tag
    #: Ordered; ``stops[i].order == i``. Deliberately **not** constrained to ≥1 here:
    #: "≥1 stop" is a slice fill-set (`data-model.md` §1), and ADR-0025 ruling 11 is the
    #: precedent for not letting a fill-set aspiration harden into a validation rule.
    stops: tuple[Stop, ...] = ()
    #: Walking legs between stops, precomputed by Valhalla.
    legs: tuple[RouteLegV1, ...] = ()
    timeline: Timeline = Field(default_factory=Timeline)
    budgets: Budgets
    #: [M2+] meal / fixed-appointment anchors. ``Anchor`` is deliberately **not** a model in
    #: this slice, so this stays empty — and is *validated* empty rather than typed
    #: ``tuple[Any, ...]`` and trusted: an unvalidated payload here would ride straight into
    #: the frozen ``content.itinerary`` artifact, which nothing downstream re-checks.
    meals: tuple[Any, ...] = ()
    #: [M2+] Plan B/C contingencies, keyed ``"B"`` / ``"C"``. Empty for the same reason.
    variants: dict[str, Any] = Field(default_factory=dict)
    schema_ver: Literal["ItineraryV1"] = "ItineraryV1"

    @field_validator("date", mode="before")
    @classmethod
    def _date_is_a_calendar_day_not_an_instant(cls, value: object) -> object:
        return _require_calendar_date(value, "date")

    @pydantic_model_validator(mode="after")
    def _m2_slots_stay_empty(self) -> Self:
        filled = [name for name in ("meals", "variants") if getattr(self, name)]
        if filled:
            raise ValueError(
                f"{', '.join(filled)} is M2+ and must stay empty: the models it would hold "
                "(Anchor, PlanVariant, StopEdit) are not defined in this slice, so anything "
                "put here is unvalidated and would be frozen into the offline bundle as-is"
            )
        return self

    @pydantic_model_validator(mode="after")
    def _every_reference_resolves(self) -> Self:
        """Stops are positions, legs are ids, and nothing may point at what is not there.

        The whole plan addresses stops by position and legs by id (ADR-0025 ruling 4). That
        only works if positions are actually positions: duplicated or gappy ``order``s make
        ``stop_order`` ambiguous or dangling, and a dangling reference is invisible until the
        travel UI renders a blank block **offline**, where there is nothing to fall back to.
        """
        for index, stop in enumerate(self.stops):
            if stop.order != index:
                raise ValueError(
                    f"stops must be ordered with stops[i].order == i (0-based position in "
                    f"the day); stops[{index}] has order={stop.order}"
                )
        orders = {stop.order for stop in self.stops}

        leg_ids: set[str] = set()
        for leg in self.legs:
            if leg.id in leg_ids:
                raise ValueError(f"leg id {leg.id!r} is not unique within the itinerary")
            leg_ids.add(leg.id)
            for endpoint, order in (("from_stop", leg.from_stop), ("to_stop", leg.to_stop)):
                if order not in orders:
                    raise ValueError(
                        f"leg {leg.id!r} {endpoint}={order} is not a Stop.order in this "
                        f"itinerary (stop orders: {sorted(orders)})"
                    )
            if leg.from_stop == leg.to_stop:
                raise ValueError(f"leg {leg.id!r} starts and ends at stop {leg.from_stop}")

        for position, entry in enumerate(self.timeline.entries):
            if entry.stop_order is not None and entry.stop_order not in orders:
                raise ValueError(
                    f"timeline entry {position} references stop_order={entry.stop_order}, "
                    f"which is not a Stop.order in this itinerary (stop orders: "
                    f"{sorted(orders)})"
                )
            if entry.leg_id is not None and entry.leg_id not in leg_ids:
                raise ValueError(
                    f"timeline entry {position} references leg_id={entry.leg_id!r}, which is "
                    f"not a leg of this itinerary (leg ids: {sorted(leg_ids)})"
                )
        return self

    def canonical_sha256(self) -> str:
        """The ``user_plan.itinerary_hash`` — what an approval is *of* (ADR-0023).

        RFC 8785 over the whole object, for the reason in :func:`_canonical_json`: ADR-0025
        A5 pins the same canonicalization here as for the manifest, because "SHA-256 over the
        canonical ItineraryV1 JSON" has exactly the same two divergences. An approval
        compare-and-set that hashed with ``json.dumps(sort_keys=True)`` would be comparing a
        digest of a serialization nothing else produces.
        """
        return _canonical_sha256(self.model_dump(mode="json"))


# --- the frozen offline artifact (docs/data/bundle-manifest.md, tile-source.md) ---


class ArtifactRef(StampedModel):
    """A bundled file plus the hash of its bytes — one hash per artifact, never a group."""

    path: BundlePath
    sha256: Sha256Hex


class GlyphsRef(StampedModel):
    """Noto glyphs (and sprites), OFL. ``path`` is a directory prefix (``glyphs/``).

    No ``sha256`` here yet: the card carries ``{path, license}`` and T035b is the task that
    closes that gap. Do not add one ahead of the card.
    """

    path: BundlePath
    license: str = Field(min_length=1)


class SchematicRef(StampedModel):
    """[M2+] the illustrated-map style artifact."""

    style_json: BundlePath
    sha256: Sha256Hex


class TileSourceV1(StampedModel):
    """The offline basemap archive — a PMTiles extract (`docs/data/tile-source.md`).

    The manifest embeds **this whole object** at ``tiles.pmtiles``, not a summary of it:
    ``path``/``sha256``/``bbox``/``maxzoom`` are the subset the travel client must read to
    open the archive and frame the map, while ``build_source``/``build_date``/
    ``tile_license``/``attribution`` are what discharge the ODbL obligation and drive
    freshness (ADR-0025 ruling 7).
    """

    path: BundlePath
    format: Literal["pmtiles"] = "pmtiles"
    #: Integrity hash of the archive bytes — this *is* ``tiles.pmtiles.sha256``, not a copy.
    sha256: Sha256Hex
    #: ``[minLon, minLat, maxLon, maxLat]`` in **EPSG:4326** (the tile pyramid itself is Web
    #: Mercator internally; every coordinate Siyur reasons about is 4326).
    bbox: tuple[float, float, float, float]
    minzoom: int = Field(ge=0)
    maxzoom: int = Field(ge=0)
    #: ``protomaps-daily``, or ``planetiler`` for the self-build fallback.
    build_source: str = Field(min_length=1)
    #: The planet build date (UTC calendar date); the freshness key.
    build_date: date
    #: SPDX; ``ODbL-1.0`` for OSM-derived tiles.
    tile_license: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    #: The base MapLibre style JSON (no customization at M1).
    style: ArtifactRef
    glyphs: GlyphsRef
    schema_ver: Literal["TileSourceV1"] = "TileSourceV1"

    @field_validator("bbox")
    @classmethod
    def _bbox_is_wgs84(
        cls, value: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        min_lon, min_lat, max_lon, max_lat = value
        for lon in (min_lon, max_lon):
            validate_lon(lon)
        for lat in (min_lat, max_lat):
            validate_lat(lat)
        if min_lat > max_lat:
            raise GeometryError(
                f"bbox latitudes are inverted: minLat={min_lat} > maxLat={max_lat}; "
                f"bbox is [minLon, minLat, maxLon, maxLat] ({CRS})"
            )
        # Longitudes are deliberately NOT ordered-checked: RFC 7946 §5.2 gives an
        # antimeridian-crossing bbox a minLon *greater* than its maxLon, and Siyur must work
        # for any area (SC-009). Rejecting that would quietly exclude Fiji and Chukotka.
        return value

    @pydantic_model_validator(mode="after")
    def _zoom_range_is_a_range(self) -> Self:
        if self.minzoom > self.maxzoom:
            raise ValueError(f"minzoom {self.minzoom} exceeds maxzoom {self.maxzoom}")
        return self


class BundleTiles(StampedModel):
    """``BundleManifestV1.tiles`` — the embedded basemap source."""

    pmtiles: TileSourceV1


class BundleRouting(StampedModel):
    """``BundleManifestV1.routing`` — the pruned walk graph and the frozen legs.

    Two artifacts, **two hashes**: a hash spanning both could not say which one corrupted,
    which is exactly what FR-013 asks it to say (ADR-0025 ruling 5).
    """

    #: Topologically-noded GeoJSON line network for on-device off-route recovery.
    walk_graph: BundlePath
    walk_graph_sha256: Sha256Hex
    #: The frozen :class:`RouteLegV1` list.
    legs: BundlePath
    legs_sha256: Sha256Hex


class BundleContent(StampedModel):
    """``BundleManifestV1.content`` — **post-quarantine** site data, narration and the day.

    ``itinerary`` is the frozen :class:`ItineraryV1` JSON the offline timeline renders from.
    Without it the traveller's day exists only as a database row they cannot reach in
    airplane mode, which is what ADR-0025 ruling 1 fixed.
    """

    sites: BundlePath
    sites_sha256: Sha256Hex
    narrations: BundlePath
    narrations_sha256: Sha256Hex
    itinerary: BundlePath
    itinerary_sha256: Sha256Hex


#: Why the quarantine filter removed a value — a **closed** set, never free text (ADR-0025
#: A3). ``withheld`` ships inside a file the user downloads and can open, so the vocabulary
#: is fixed at exactly what the "needs connectivity" affordance needs; free text would be a
#: channel through which any future withholding rule — including one touching the private
#: side of the PRD §13 #4 boundary — could carry content out. Note there is deliberately no
#: ``unstamped`` member: FR-012 *refuses* unstamped input and the compile fails, so a value
#: is withheld **or** refused, never both. Do not source these strings from
#: ``licenses.quarantine_reason``, which returns prose naming the internal allowlist.
WithheldReason = Literal["license_forbids_redistribution", "source_unavailable"]


class WithheldValue(StampedModel):
    """One value the quarantine filter removed, and why."""

    site_id: UUID
    #: Dotted path into the record as it stood **before** quarantine ran (``reviews``,
    #: ``notes[2]``, ``names.el``). Because quarantine *removes* entries, surviving lists
    #: re-index: the travel UI may use this to say *that* something was withheld and of what
    #: kind, and must **not** use it to position a placeholder inside the surviving list.
    field: str = Field(min_length=1)
    reason: WithheldReason


class BundleIntegrity(StampedModel):
    """``BundleManifestV1.integrity`` — the launch-time check (iOS eviction guard)."""

    manifest_sha256: Sha256Hex


#: The ``integrity.manifest_sha256`` of a manifest that has not been sealed yet. It never
#: reaches a digest: :meth:`BundleManifestV1.seal` removes the whole ``integrity`` key before
#: canonicalizing, so this placeholder exists only to satisfy the field being required.
_UNSEALED_MANIFEST_SHA256: Final[str] = "0" * 64


class BundleManifestV1(StampedModel):
    """The frozen offline artifact — the **airplane-mode contract** (`bundle-manifest.md`).

    Everything the travel UI reads resolves to a path in here, and every path has exactly one
    hash beside it. The manifest is written to GCS and downloaded whole into OPFS; it is an
    object, never a database row.
    """

    bundle_id: str = Field(min_length=1)
    #: The :class:`ItineraryV1` this bundle freezes (whose JSON is at ``content.itinerary``).
    itinerary_id: UUID
    #: UTC — the one UTC field in the bundle. Every *planned* time inside it is area-local
    #: wall clock, frozen as planned; the traveller's device clock need not match.
    created_at: datetime = Field(default_factory=_utcnow)
    #: Total bundle size, reported **before** download (FR-014); ≤200 MB budget target.
    size_bytes: int = Field(ge=0)
    tiles: BundleTiles
    routing: BundleRouting
    content: BundleContent
    #: ``ATTRIBUTION.md``, regenerated per bundle — hashed like every other artifact, because
    #: it is legally obligated content and not a decoration (ADR-0025 ruling 5).
    attribution: ArtifactRef
    #: The license of the **bundled narration text**: ``"CC-BY-SA-4.0"`` whenever any story
    #: is present, ``None`` when ``content.narrations`` is empty (ADR-0024). Attribution
    #: discharges BY; this declaration discharges **SA**, and it is machine-readable on
    #: purpose so a reuser need not parse ``ATTRIBUTION.md`` prose to learn the terms.
    #: Spelled in camelCase because the card spells it that way and this object is read by
    #: the TypeScript verifier — a Python alias would give the field two spellings, and the
    #: canonical digest below depends on exactly which one was serialized.
    textLicense: str | None = None  # camelCase deliberately — see above
    #: What quarantine removed. Empty = nothing withheld.
    withheld: tuple[WithheldValue, ...] = ()
    integrity: BundleIntegrity
    #: [M2+] the illustrated-map render.
    schematic: SchematicRef | None = None
    schema_ver: Literal["BundleManifestV1"] = "BundleManifestV1"

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, "created_at")

    def canonical_payload(self) -> dict[str, Any]:
        """This manifest as JSON-native data with the ``integrity`` key **removed**.

        *Removed* — not set to ``null``, not set to ``""``. A hash cannot cover itself, and
        the three readings differ: JCS serializes a present ``null`` key, so nulling it would
        produce a different digest from omitting it, and the two sides would each be
        self-consistent and mutually wrong.
        """
        payload = self.model_dump(mode="json")
        del payload["integrity"]
        return payload

    def computed_manifest_sha256(self) -> str:
        """SHA-256 over :meth:`canonical_payload`, RFC 8785 (JCS), lowercase hex."""
        return _canonical_sha256(self.canonical_payload())

    def verify_manifest_sha256(self) -> bool:
        """Does the recorded ``integrity.manifest_sha256`` match this manifest's content?

        This is the launch-time check's Python half (its TypeScript counterpart is T053).
        It returns a bool rather than raising because a failed check is a *finding* — an
        unusable bundle to report — not an exceptional condition.
        """
        return self.integrity.manifest_sha256 == self.computed_manifest_sha256()

    @classmethod
    def seal(cls, **fields: Any) -> Self:
        """Build a manifest and stamp its own digest into ``integrity.manifest_sha256``.

        The chicken-and-egg — ``integrity`` is required, and the digest is taken over the
        manifest *without* it — is resolved by validating once with a placeholder digest and
        re-validating with the real one. The placeholder can never leak into a digest,
        because :meth:`canonical_payload` deletes the key it lives under. An ``integrity``
        passed in ``fields`` is ignored: sealing is the act of computing it.
        """
        draft = cls.model_validate(
            {**fields, "integrity": {"manifest_sha256": _UNSEALED_MANIFEST_SHA256}}
        )
        return draft.model_copy(
            update={"integrity": BundleIntegrity(manifest_sha256=draft.computed_manifest_sha256())}
        )
