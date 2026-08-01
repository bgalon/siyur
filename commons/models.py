"""The commons data spine — ``SourcedValue`` and ``SiteRecordV1`` (pydantic v2).

**Schema ground truth: [`docs/data/poi-site.md`](../docs/data/poi-site.md)** (itself
ground-truthed to `docs/design/tech-design.md` §1.0–§1.2). Field names, types and the
``SourceRef.kind`` enum are transcribed from that card; where any other doc differs, the
card wins. Nothing here re-defines the schema.

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

Slice 001 populates a subset (`specs/001-research-cited-sites/data-model.md` §2): stories
are deferred to slice 002 and M2+ fields stay empty — **empty is valid, the schema is
unchanged**.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Annotated, Any, Final, Literal, NoReturn, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic import model_validator as pydantic_model_validator

from commons import licenses
from commons.geo import Wgs84Point
from commons.licenses import SourceKind

__all__ = [
    "Bcp47Tag",
    "Claim",
    "FieldConflict",
    "Rating",
    "ReviewSummary",
    "SiteRecordV1",
    "SourceKind",
    "SourceRef",
    "SourcedValue",
    "StampedModel",
    "Story",
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

    Out of scope for slice 001 (FR-011) — ``SiteRecordV1.stories`` is empty and valid.
    """

    #: ``en`` canonical (+ translations at M3).
    text_by_lang: dict[Bcp47Tag, str]
    #: CC-BY-SA article; attribution required.
    source: SourceRef
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
