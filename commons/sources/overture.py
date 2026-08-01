"""Overture **places** adapter — DuckDB over the hosted parquet (ADR-0009 option A).

Reads the places theme with DuckDB's ``spatial`` (+ ``httpfs`` for the cloud release)
extensions, pruned by the area's bounding box via the theme's own ``bbox`` column and
then refined against the true polygon in shapely. No mirror: the commons is the cache.

**The license is read per record, never assumed from the theme** (ADR-0009 / the
`DATA-LICENSES.md` Overture row): one release mixes Meta ``CDLA-Permissive-2.0``,
Foursquare ``Apache-2.0`` and AllThePlaces ``CC0-1.0``, and only CDLA/CC0 are allowlisted,
so an Apache row stamps ``bundleable=False``. Each row's ``sources`` entries carry a
JSON-pointer ``property``, so the stamp resolves per *field* where the row says so and per
*record* (``property = ""``) otherwise; a row with no license at all is **dropped and
counted**, never stamped with a guessed one.

GERS: this release folded ``gers_id`` into the ``id`` column — the card's ``gers_id`` maps
to it (`tests/fixtures/README.md`). ``names.primary`` carries **no language tag**, so it is
keyed BCP-47 ``und`` ("undetermined") rather than guessed at; ``names.common`` entries keep
their own tags.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, ClassVar, Final

import duckdb
from shapely.geometry.base import BaseGeometry

from commons.geo import GeometryError, Wgs84Point, point_from_lonlat
from commons.licenses import SourceKind
from commons.models import BCP47_PATTERN, SiteRecordV1, SourcedValue, SourceRef
from commons.sources import base

__all__ = ["CLOUD_SOURCE_TEMPLATE", "DEFAULT_RELEASE", "OvertureAdapter"]

#: Overture GA release the adapter reads by default (matches the committed fixture).
DEFAULT_RELEASE: Final = "2026-07-22.0"
CLOUD_SOURCE_TEMPLATE: Final = (
    "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
)

#: ``sources[].property`` is a JSON pointer; ``""`` means "the whole record".
_RECORD_LEVEL: Final = ""
_GEOMETRY: Final = "/geometry"
_NAMES: Final = "/properties/names"
_CATEGORIES: Final = "/properties/categories"
_ADDRESSES: Final = "/properties/addresses"
#: BCP-47 "undetermined language" — the honest key for an untagged ``names.primary``.
_UNDETERMINED: Final = "und"
#: Used when Overture states no per-row confidence.
_FALLBACK_CONFIDENCE: Final = 0.5

_BCP47: Final[re.Pattern[str]] = re.compile(BCP47_PATTERN)

_QUERY: Final = """
SELECT id, ST_X(geometry) AS lon, ST_Y(geometry) AS lat, names, categories,
       basic_category, addresses, sources, confidence
FROM read_parquet($src, hive_partitioning=1, filename=false)
WHERE bbox.xmin <= $maxx AND bbox.xmax >= $minx
  AND bbox.ymin <= $maxy AND bbox.ymax >= $miny
ORDER BY id
"""


@dataclass(frozen=True, slots=True)
class OvertureAdapter:
    """Fetch Overture places over an area, every value stamped at ingestion.

    ``parquet`` points the adapter at a local file/glob (the committed fixture, an offline
    extract); left ``None`` it reads the hosted release — tests never hit the network.
    """

    kind: ClassVar[SourceKind] = "overture"

    parquet: str | None = None
    release: str = DEFAULT_RELEASE
    limit: int | None = None
    #: Ingestion date stamped on every value; defaults to today (UTC) per fetch.
    observed_at: date | None = None
    s3_region: str = "us-west-2"

    def fetch(self, polygon: BaseGeometry) -> base.FetchResult:
        minx, miny, maxx, maxy = base.bbox_of(polygon)
        observed = self.observed_at or datetime.now(UTC).date()
        dropped: Counter[str] = Counter()
        records = []
        for row in self._read(minx, miny, maxx, maxy):
            record = _to_record(row, polygon, observed, dropped)
            if record is not None:
                records.append(record)
        return base.FetchResult(kind=self.kind, records=tuple(records), dropped=dict(dropped))

    def _read(self, minx: float, miny: float, maxx: float, maxy: float) -> list[Any]:
        source = self.parquet or CLOUD_SOURCE_TEMPLATE.format(release=self.release)
        connection = duckdb.connect()
        try:
            connection.execute("INSTALL spatial; LOAD spatial;")
            if self.parquet is None:
                connection.execute("INSTALL httpfs; LOAD httpfs;")
                connection.execute("SET s3_region = $region;", {"region": self.s3_region})
            sql = _QUERY if self.limit is None else f"{_QUERY} LIMIT {int(self.limit)}"
            params = {"src": source, "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy}
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()


def _license_for(sources: Sequence[Mapping[str, Any]] | None, pointer: str) -> str | None:
    """The license Overture states **for this field**, else the record-level one.

    ``None`` means the row states no license at all — the caller drops the candidate.
    """
    record_level: str | None = None
    for entry in sources or ():
        license_id = entry.get("license")
        if not license_id:
            continue
        if entry.get("property") == pointer:
            return str(license_id)
        if entry.get("property") == _RECORD_LEVEL:
            record_level = str(license_id)
    return record_level


def _stamp(value: str, ref: SourceRef, confidence: float, observed: date) -> SourcedValue[str]:
    return SourcedValue[str].stamp(
        value=value, source=ref, confidence=confidence, observed_at=observed
    )


def _names(
    names: Mapping[str, Any] | None,
    ref: SourceRef,
    confidence: float,
    observed: date,
    dropped: Counter[str],
) -> dict[str, SourcedValue[str]]:
    """``names.common`` under its own BCP-47 tags; the untagged ``primary`` under ``und``."""
    out: dict[str, SourcedValue[str]] = {}
    common: Mapping[str, str] = (names or {}).get("common") or {}
    for tag, value in common.items():
        if not value:
            continue
        if _BCP47.fullmatch(tag) is None:
            dropped[base.UNUSABLE_LANGUAGE_TAG] += 1
            continue
        out[tag] = _stamp(value, ref, confidence, observed)
    primary = (names or {}).get("primary")
    if primary and primary not in {stamped.value for stamped in out.values()}:
        out[_UNDETERMINED] = _stamp(primary, ref, confidence, observed)
    return out


def _categories(basic_category: str | None, categories: Mapping[str, Any] | None) -> list[str]:
    """``basic_category`` first, then the taxonomy primary/alternates; order-stable, deduped."""
    candidates: Iterable[str | None] = (
        basic_category,
        (categories or {}).get("primary"),
        *((categories or {}).get("alternate") or ()),
    )
    return list(dict.fromkeys(value for value in candidates if value))


def _address(addresses: Sequence[Mapping[str, Any]] | None) -> str | None:
    """The first postal address, joined **verbatim** — never transliterated (FAIL-001)."""
    if not addresses:
        return None
    first = addresses[0]
    parts = (first.get(key) for key in ("freeform", "locality", "postcode", "region", "country"))
    return ", ".join(part for part in parts if part) or None


def _to_record(
    row: Any, polygon: BaseGeometry, observed: date, dropped: Counter[str]
) -> SiteRecordV1 | None:
    """One parquet row → a stamped record, or ``None`` (counted) if it cannot be stamped."""
    gers_id, lon, lat, names, categories, basic_category, addresses, sources, confidence = row
    if not gers_id:
        dropped[base.NO_SOURCE] += 1
        return None
    if lon is None or lat is None:
        dropped[base.NO_GEOMETRY] += 1
        return None
    try:
        # Coordinates come from the source geometry only — never model-emitted (FR-005).
        point = point_from_lonlat(lon, lat)
    except GeometryError:
        dropped[base.INVALID_GEOMETRY] += 1
        return None
    if not polygon.covers(point):
        dropped[base.OUTSIDE_POLYGON] += 1
        return None

    def ref_for(pointer: str) -> SourceRef | None:
        license_id = _license_for(sources, pointer)
        if license_id is None:
            return None
        return SourceRef(kind="overture", id=str(gers_id), license=license_id)

    location_ref = ref_for(_GEOMETRY)
    names_ref = ref_for(_NAMES)
    if location_ref is None or names_ref is None:
        dropped[base.NO_LICENSE] += 1
        return None
    score = float(confidence) if confidence is not None else _FALLBACK_CONFIDENCE
    score = min(max(score, 0.0), 1.0)
    stamped_names = _names(names, names_ref, score, observed, dropped)
    if not stamped_names:
        dropped[base.NO_NAME] += 1
        return None
    category_ref = ref_for(_CATEGORIES)
    stamped_categories: tuple[SourcedValue[str], ...] = ()
    if category_ref is not None:
        stamped_categories = tuple(
            _stamp(value, category_ref, score, observed)
            for value in _categories(basic_category, categories)
        )
    address_ref = ref_for(_ADDRESSES)
    address = _address(addresses)
    return SiteRecordV1(
        gers_id=str(gers_id),
        names=stamped_names,
        location=SourcedValue[Wgs84Point].stamp(
            value=point, source=location_ref, confidence=score, observed_at=observed
        ),
        categories=stamped_categories,
        address=(
            _stamp(address, address_ref, score, observed)
            if address is not None and address_ref is not None
            else None
        ),
    )
