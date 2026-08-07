"""OpenStreetMap adapter — Overpass over the area, ODbL-stamped (ADR-0009 option A).

Long-tail tags Overture does not carry (`opening_hours`, local-language names, civic
detail). Every value is stamped ``kind="osm"``, ``license="ODbL-1.0"``,
``attribution="© OpenStreetMap contributors"`` — the attribution the OSMF Produced-Work
guidelines require on every rendered map (`DATA-LICENSES.md`).

**Graceful degradation is load-bearing, not decoration** (FR-012, T025): the public
Overpass instance genuinely 504s under load. So the area is queried as one *small* request
per element type — smaller queries are markedly less likely to time out, and a failure of
one type still leaves the others' results in hand. Each request gets a bounded timeout and
a bounded number of retries; when a request is finally unavailable it is recorded as a
reason and the pass continues. The result is a **partial answer flagged ``degraded=True``
with a reason** — never a hang, never a raised exception, never a discarded partial.

Overpass's own bbox filter is ``(south, west, north, east)`` — i.e. **lat first**, the
opposite of everything else in the commons; :func:`_query` is the single place that
conversion happens.
"""

from __future__ import annotations

import random
import re
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Final

import httpx
from shapely.geometry.base import BaseGeometry

from commons.geo import GeometryError, Wgs84Point, point_from_lonlat
from commons.licenses import SourceKind
from commons.models import BCP47_PATTERN, SiteRecordV1, SourcedValue, SourceRef
from commons.sources import base

__all__ = ["ATTRIBUTION", "DEFAULT_ENDPOINT", "LICENSE", "OsmAdapter", "OverpassUnavailable"]

#: The **kumi.systems mirror**, not the main `overpass-api.de` instance (ADR-0027).
#: The main instance is the most contended one and its fair-use budget (~10k queries/day,
#: 2 concurrent) is shared with everyone; the mirror is documented as more permissive
#: (`methods-stack-reference.md` §4). Overridable per adapter — a self-hosted or private
#: instance is a constructor argument, never a code change.
DEFAULT_ENDPOINT: Final = "https://overpass.kumi.systems/api/interpreter"
LICENSE: Final = "ODbL-1.0"
ATTRIBUTION: Final = "© OpenStreetMap contributors"
#: Honest User-Agent — Overpass/OSMF fair-use terms (`DATA-LICENSES.md` "API terms").
USER_AGENT: Final = "siyur/0.0 (https://github.com/bgalon/siyur)"
_OSM_BASE: Final = "https://www.openstreetmap.org"

#: Overload/gateway responses worth one more bounded attempt (504 = the ADR's flake).
_RETRYABLE: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
#: Ceiling on a single backoff sleep. Retrying is worth a few seconds, not a stalled pass —
#: a server that asks for a two-minute wait is telling us to degrade, not to hold the
#: request open (FR-012: partial results beat a hang).
_MAX_BACKOFF: Final = 8.0
#: Tag keys that describe *what a place is*; emitted as ``key.value`` categories.
_CATEGORY_TAGS: Final[tuple[str, ...]] = (
    "amenity",
    "tourism",
    "historic",
    "leisure",
    "shop",
    "craft",
    "office",
    "natural",
    "man_made",
    "place",
    "public_transport",
)
#: ``building=yes`` &c. say nothing — a tag value that is just a boolean is not a category.
_EMPTY_TAG_VALUES: Final[frozenset[str]] = frozenset({"yes", "no"})
_UNDETERMINED: Final = "und"
#: OSM carries no confidence of its own; the schema card's example rows use these.
_CONFIDENCE: Final = 0.7
_HOURS_CONFIDENCE: Final = 0.5

_BCP47: Final[re.Pattern[str]] = re.compile(BCP47_PATTERN)


class OverpassUnavailable(RuntimeError):
    """One Overpass sub-query stayed unavailable across every bounded attempt."""


def _query(
    element_type: str, bounds: tuple[float, float, float, float], timeout: float, limit: int | None
) -> str:
    minx, miny, maxx, maxy = bounds
    # Overpass bbox is (south, west, north, east) = lat, lon — the one place we flip.
    bbox = f"{miny},{minx},{maxy},{maxx}"
    out = "out center" + (f" {int(limit)}" if limit else "") + ";"
    return f'[out:json][timeout:{int(timeout)}];\n{element_type}["name"]({bbox});\n{out}'


@dataclass(frozen=True, slots=True)
class OsmAdapter:
    """Fetch named OSM elements over an area, every value ODbL-stamped at ingestion.

    ``client`` lets a caller (or a test) inject an ``httpx`` transport; when it is ``None``
    a client is created per fetch with the bounded timeout and the honest User-Agent, and
    closed again.
    """

    kind: ClassVar[SourceKind] = "osm"

    endpoint: str = DEFAULT_ENDPOINT
    #: One bounded request each — a 504 on one type still leaves the others (FR-012).
    element_types: tuple[str, ...] = ("node", "way", "relation")
    timeout: float = 30.0
    #: Three attempts total. One retry cannot survive a rate limit (ADR-0027).
    retries: int = 2
    backoff: float = 0.5
    limit: int | None = None
    observed_at: date | None = None
    client: httpx.Client | None = None

    def fetch(self, polygon: BaseGeometry) -> base.FetchResult:
        bounds = base.bbox_of(polygon)
        observed = self.observed_at or datetime.now(UTC).date()
        dropped: Counter[str] = Counter()
        records: list[SiteRecordV1] = []
        seen: set[str] = set()
        failures: list[str] = []
        client = self.client or httpx.Client(
            timeout=self.timeout, headers={"User-Agent": USER_AGENT}
        )
        try:
            for element_type in self.element_types:
                try:
                    elements = self._request(
                        client, _query(element_type, bounds, self.timeout, self.limit)
                    )
                except OverpassUnavailable as unavailable:
                    failures.append(f"{element_type}: {unavailable}")
                    continue
                for element in elements:
                    record = _to_record(element, polygon, observed, dropped)
                    if record is None or record.location.source.id in seen:
                        continue
                    seen.add(record.location.source.id)
                    records.append(record)
        finally:
            if self.client is None:
                client.close()
        degraded = bool(failures)
        return base.FetchResult(
            kind=self.kind,
            records=tuple(records),
            dropped=dict(dropped),
            degraded=degraded,
            reason=f"Overpass unavailable ({'; '.join(failures)})" if degraded else None,
        )

    def _request(self, client: httpx.Client, query: str) -> list[Mapping[str, Any]]:
        """POST one sub-query with bounded retries; raise once the budget is spent."""
        last = "unknown error"
        for attempt in range(self.retries + 1):
            retry_after: float | None = None
            try:
                response = client.post(self.endpoint, data={"data": query}, timeout=self.timeout)
                if response.status_code in _RETRYABLE:
                    last = f"HTTP {response.status_code}"
                    retry_after = _retry_after_seconds(response)
                else:
                    response.raise_for_status()
                    payload = response.json()
                    return list(payload.get("elements") or ())
            except httpx.TimeoutException:
                last = f"timeout after {self.timeout}s"
            except (httpx.HTTPError, ValueError) as error:
                last = f"{type(error).__name__}: {error}"
            if attempt < self.retries and self.backoff:
                time.sleep(self._sleep_for(attempt, retry_after))
        raise OverpassUnavailable(last)

    def _sleep_for(self, attempt: int, retry_after: float | None) -> float:
        """How long to wait before the next attempt.

        **The server's own answer wins.** A `429` means the fair-use budget is spent, not
        that the request was unlucky — so a fixed sub-second wait retries into the same
        wall and burns the budget faster. When Overpass sends `Retry-After` we honour it
        (capped, see below); otherwise we back off **exponentially with jitter**, because
        every parallel research pass that fails at the same instant would otherwise retry
        at the same instant.

        The cap is deliberate: FR-012 says a slow source degrades to partial results, so a
        server asking for two minutes is telling us to give up and flag `degraded`, not to
        hold the user's stream open.
        """
        wait = retry_after if retry_after is not None else self.backoff * (2**attempt)
        wait = min(wait, _MAX_BACKOFF)
        # Full jitter over [wait/2, wait] — spreads a thundering herd without ever
        # sleeping longer than the cap, and keeps the wait deterministic enough to test.
        return wait / 2 + random.random() * (wait / 2)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse `Retry-After`, which RFC 9110 allows as **either** delta-seconds or a date.

    Returns ``None`` when the header is absent or unparseable, so the caller falls back to
    exponential backoff rather than treating a malformed header as "retry immediately".
    A negative or past value clamps to ``0`` — the server is saying "now", not "yesterday".
    """
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _stamp(value: str, ref: SourceRef, confidence: float, observed: date) -> SourcedValue[str]:
    return SourcedValue[str].stamp(
        value=value, source=ref, confidence=confidence, observed_at=observed
    )


def _names(
    tags: Mapping[str, str], ref: SourceRef, observed: date, dropped: Counter[str]
) -> dict[str, SourcedValue[str]]:
    """``name:<bcp47>`` under its tag; the untagged ``name`` under ``und`` if it adds anything.

    OSM language suffixes that are not BCP-47 subtags (``name:be-tarask``) and qualifiers
    that only look like one (``name:left``, ``name:etymology:wikidata``) are skipped and
    counted rather than corrupting the ``names`` map.
    """
    out: dict[str, SourcedValue[str]] = {}
    for key, value in tags.items():
        tag = key.removeprefix("name:")
        if tag == key or not value:
            continue
        if _BCP47.fullmatch(tag) is None:
            dropped[base.UNUSABLE_LANGUAGE_TAG] += 1
            continue
        out[tag] = _stamp(value, ref, _CONFIDENCE, observed)
    plain = tags.get("name")
    if plain and plain not in {stamped.value for stamped in out.values()}:
        out[_UNDETERMINED] = _stamp(plain, ref, _CONFIDENCE, observed)
    return out


def _categories(tags: Mapping[str, str]) -> list[str]:
    values: Iterable[str] = (
        f"{key}.{tags[key]}"
        for key in _CATEGORY_TAGS
        if tags.get(key) and tags[key] not in _EMPTY_TAG_VALUES
    )
    return list(dict.fromkeys(values))


def _address(tags: Mapping[str, str]) -> str | None:
    """Compose the ``addr:*`` tags **verbatim** — source scripts are never fixed (FAIL-001)."""
    street = " ".join(
        part for part in (tags.get("addr:street"), tags.get("addr:housenumber")) if part
    )
    parts = (street, tags.get("addr:postcode"), tags.get("addr:city"), tags.get("addr:country"))
    return ", ".join(part for part in parts if part) or None


def _to_record(
    element: Mapping[str, Any], polygon: BaseGeometry, observed: date, dropped: Counter[str]
) -> SiteRecordV1 | None:
    """One Overpass element → a stamped record, or ``None`` (counted) if unusable."""
    element_type, element_id = element.get("type"), element.get("id")
    if not element_type or element_id is None:
        dropped[base.NO_SOURCE] += 1
        return None
    # Ways/relations carry their representative point under `center` (`out center`).
    position: Mapping[str, Any] = element.get("center") or element
    lon, lat = position.get("lon"), position.get("lat")
    if lon is None or lat is None:
        dropped[base.NO_GEOMETRY] += 1
        return None
    try:
        # Coordinates come from OSM only — never model-emitted (FR-005).
        point = point_from_lonlat(lon, lat)
    except GeometryError:
        dropped[base.INVALID_GEOMETRY] += 1
        return None
    if not polygon.covers(point):
        dropped[base.OUTSIDE_POLYGON] += 1
        return None

    ref = SourceRef(
        kind="osm",
        id=f"{element_type}/{element_id}",
        url=f"{_OSM_BASE}/{element_type}/{element_id}",
        license=LICENSE,
        attribution=ATTRIBUTION,
    )
    tags: Mapping[str, str] = element.get("tags") or {}
    names = _names(tags, ref, observed, dropped)
    if not names:
        dropped[base.NO_NAME] += 1
        return None
    address = _address(tags)
    hours = tags.get("opening_hours")
    return SiteRecordV1(
        names=names,
        location=SourcedValue[Wgs84Point].stamp(
            value=point, source=ref, confidence=_CONFIDENCE, observed_at=observed
        ),
        categories=tuple(_stamp(value, ref, _CONFIDENCE, observed) for value in _categories(tags)),
        address=_stamp(address, ref, _CONFIDENCE, observed) if address else None,
        opening_hours=_stamp(hours, ref, _HOURS_CONFIDENCE, observed) if hours else None,
    )
