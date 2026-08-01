"""The source-adapter boundary — where provenance becomes mechanical (ADR-0009).

Every research source (Overture, OSM today; Wikivoyage/Wikidata at slice 002) implements
:class:`SourceAdapter`. The stamp is applied **once, here at the boundary**, so "refuse
unstamped input" (FR-003) and the ``bundleable`` quarantine hold by construction rather
than by per-source vigilance:

- a candidate whose source cannot be resolved (no id, no license in the upstream record)
  is **dropped and counted** — never stamped optimistically with a guessed license;
- everything emitted is a :class:`~commons.models.SiteRecordV1` whose every value is a
  ``SourcedValue`` built with :meth:`~commons.models.SourcedValue.stamp`, which *derives*
  ``bundleable`` from :mod:`commons.licenses`. Adapters never set that flag themselves.

:class:`FetchResult` is what an adapter returns. It **is** the ``Iterable[SiteRecordV1]``
that `tasks.md` T022 spells out (iterate it to get the records) and additionally carries
the counts a caller needs to report what was and wasn't found, plus the FR-012
graceful-degradation flag (a partial answer is a legitimate answer, an exception is not).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from shapely.geometry.base import BaseGeometry

from commons.geo import validate_lat, validate_lon
from commons.licenses import SourceKind
from commons.models import SiteRecordV1

__all__ = [
    "INVALID_GEOMETRY",
    "NO_GEOMETRY",
    "NO_LICENSE",
    "NO_NAME",
    "NO_SOURCE",
    "OUTSIDE_POLYGON",
    "UNUSABLE_LANGUAGE_TAG",
    "FetchResult",
    "SourceAdapter",
    "bbox_of",
]

# Drop reasons. A dropped candidate is counted under one of these and never emitted;
# the caller reports them (FR-012 "what was and wasn't found"). `UNUSABLE_LANGUAGE_TAG`
# is value-level: the record still ships, one name key did not.
NO_SOURCE: Final = "no_resolvable_source"
NO_LICENSE: Final = "no_license_on_source"
NO_GEOMETRY: Final = "no_geometry"
INVALID_GEOMETRY: Final = "invalid_geometry"
NO_NAME: Final = "no_usable_name"
OUTSIDE_POLYGON: Final = "outside_polygon"
UNUSABLE_LANGUAGE_TAG: Final = "unusable_language_tag"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """One adapter pass: the stamped records, why candidates were dropped, and health."""

    kind: SourceKind
    records: tuple[SiteRecordV1, ...] = ()
    #: drop reason → number of candidates dropped for it.
    dropped: Mapping[str, int] = field(default_factory=dict)
    #: FR-012: the source failed part-way; ``records`` is a *partial*, honest answer.
    degraded: bool = False
    #: Why it degraded — required when ``degraded``, forbidden otherwise.
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.degraded != bool(self.reason):
            raise ValueError(
                "FR-012: a degraded fetch must carry a reason and a healthy one must not "
                f"(degraded={self.degraded}, reason={self.reason!r})"
            )

    def __iter__(self) -> Iterator[SiteRecordV1]:
        """T022's ``fetch(polygon) -> Iterable[SiteRecordV1]`` contract."""
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)


@runtime_checkable
class SourceAdapter(Protocol):
    """Read a research source over an area and emit fully stamped commons records."""

    @property
    def kind(self) -> SourceKind:
        """The ``SourceRef.kind`` every value this adapter emits is stamped with."""

    def fetch(self, polygon: BaseGeometry) -> FetchResult:
        """Fetch every candidate inside ``polygon`` (EPSG:4326, lon/lat), stamped."""
        ...


def bbox_of(polygon: BaseGeometry) -> tuple[float, float, float, float]:
    """``(minx, miny, maxx, maxy)`` in EPSG:4326 — **lon** first, axis-checked.

    Sources are queried by bounding box and refined against the true polygon afterwards,
    so a swapped pair must fail here, not silently fetch the wrong hemisphere.
    """
    if polygon.is_empty:
        raise ValueError("cannot fetch over an empty geometry")
    minx, miny, maxx, maxy = polygon.bounds
    return validate_lon(minx), validate_lat(miny), validate_lon(maxx), validate_lat(maxy)
