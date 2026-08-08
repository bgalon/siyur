"""Wikivoyage + Wikipedia adapter — MediaWiki Action API, CC BY-SA stamped (ADR-0024).

The narration source. One class serves **both** wikis because they are the same API on two
hosts and differ only in what they are good for (`tests/fixtures/README.md`): Wikivoyage is
**destination-level** — one article per town, carrying dozens of `{{see}}`/`{{eat}}`/`{{sleep}}`
*listing templates* with their own coordinates — while Wikipedia is **POI-level**, one article
per place, reached with `list=geosearch` + `prop=extracts`. Expecting a Wikivoyage article per
POI is expecting something the source does not do.

Stamping happens **here, once, at the boundary** like every other adapter (ADR-0009), and
``bundleable`` is always *derived* by :func:`commons.licenses.bundleable` via
:meth:`SourcedValue.stamp` — never asserted. :class:`~commons.models.Story` is not a
``SourcedValue`` and so carries no flag of its own; the quarantine derives it from
``(kind, license)`` too (ADR-0025 ruling 8).

**This module writes no prose.** It fetches, parses and stamps; the story text it emits is the
article's own words, verbatim. Adapting them is `planner/nodes/narrate.py` (T032) — which keeps
this ``SourceRef``, so the credit still points at the revision the words came from.

## Six shapes of the live API that will silently produce wrong behaviour

Each was observed in the committed capture (`tests/fixtures/wikivoyage_rhodes.json`) and each
has a plausible implementation that is wrong:

1. **A *missing* page still returns `canonicalurl`/`fullurl`.** `inprop=url` answers with the
   URLs the page *would* have. Keying "does an article exist?" on a resolvable-looking URL
   marks every miss as a hit, turning FR-023's *no article ⇒ no story, nothing invented* into
   *invent a story for everywhere*. :func:`_article` keys on the ``missing`` flag alone.
2. **Missing and present pages arrive in one `batchcomplete` response.** The filter is
   per page, never per response — hence the ``for page in ...`` loop rather than an
   early return on the envelope.
3. **`lastrevid` is a decoy.** `prop=info` returns a page-level ``lastrevid`` — the page's
   *current* revision, which drifts from the revision whose content is in hand. Attribution
   uses ``revisions[0].revid``; a page with no ``revisions`` yields no article at all.
4. **The revid-pinned URL is not returned in any form.** ``canonicalurl`` is unpinned; the
   ``?oldid=<revid>`` form is *constructed* here (:func:`pinned_url`). There is nothing to
   look for in the response.
5. **The licence is not in the payload.** CC BY-SA 4.0 is a property of the wiki, asserted
   from `DATA-LICENSES.md`/ADR-0024 — and ``bundleable`` is still derived from it, not
   asserted alongside it.
6. **`SourceRef.kind` is not in the payload either.** It is *which endpoint was called*, so it
   is a field of the adapter, not something parsed out of a response.

Two more, from the same capture: geosearch is a **circle, not a bbox** (two of the twenty
Rhodes hits fall outside the area), so every candidate is clipped against the true polygon;
and ``exlimit=max`` caps extracts at 20 pages per request for anonymous clients, so a larger
title batch would return extracts for only the first 20 — see :data:`_TITLE_BATCH`.
"""

from __future__ import annotations

import math
import random
import re
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final, Literal

import httpx
import mwparserfromhell
from pyproj import Geod
from shapely.geometry.base import BaseGeometry

from commons.geo import GeometryError, Wgs84Point, point_from_lonlat
from commons.models import BCP47_PATTERN, SiteRecordV1, SourcedValue, SourceRef, Story
from commons.sources import base

__all__ = [
    "DEFAULT_LANG",
    "LICENSE",
    "LISTING_TEMPLATES",
    "MAX_RADIUS_M",
    "USER_AGENT",
    "MediaWikiAdapter",
    "MediaWikiUnavailable",
    "WikiKind",
    "endpoint_for",
    "pinned_url",
]

#: The two wikis ADR-0024 chose. Both are already ``SourceKind`` values, so no schema change.
WikiKind = Literal["wikivoyage", "wikipedia"]

#: Asserted from `DATA-LICENSES.md` (rows "Wikivoyage narration" / "Wikipedia narration"),
#: never read from a payload — trap 5. Both wikis are CC BY-SA 4.0 for text.
LICENSE: Final = "CC-BY-SA-4.0"
#: Wikimedia's User-Agent policy requires an identifying agent with a contact URL.
USER_AGENT: Final = "siyur/0.0 (https://github.com/bgalon/siyur)"
#: The wiki language edition, not a place — genericity holds: any area, any language edition.
#: ADR-0024 scopes M1 to English; a caller changes it with a constructor argument.
DEFAULT_LANG: Final = "en"
#: GeoData's ceiling on ``gsradius``. Larger areas are covered by tiling, not by asking for
#: a radius the API will reject.
MAX_RADIUS_M: Final = 10_000.0

#: Wikivoyage listing templates. The typed shorthands *are* the listing kind; the two generic
#: ones carry it in a ``type=`` parameter instead.
LISTING_TEMPLATES: Final[frozenset[str]] = frozenset(
    {"see", "do", "buy", "eat", "drink", "sleep", "go", "listing", "marker", "vicinity"}
)
_GENERIC_TEMPLATES: Final[frozenset[str]] = frozenset({"listing", "marker", "vicinity"})

#: Overload/gateway responses worth one more bounded attempt.
_RETRYABLE: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
#: Ceiling on a single backoff sleep — FR-012 prefers a flagged partial to a held-open request.
_MAX_BACKOFF: Final = 8.0
#: ``exlimit=max`` is 20 extracts per request for anonymous clients; batching more titles than
#: that returns pages for all of them but *extracts* for only the first 20 — a silent truncation.
_TITLE_BATCH: Final = 20
#: Neither wiki states a confidence. Listing coordinates are hand-placed by editors, so this
#: sits below the 0.7 the OSM adapter uses for surveyed geometry.
_CONFIDENCE: Final = 0.6
#: A listing's ``hours=`` is free prose ("Open in season till 20:00"), not opening_hours.js
#: syntax. It still ships — :mod:`commons.opening_hours` fails *closed*, so an unparseable
#: expression becomes ``hours_unknown`` with the raw text shown, never a guessed "open".
_HOURS_CONFIDENCE: Final = 0.3
#: BCP-47 "undetermined" — used when a page states a ``pagelanguage`` that is not a subtag.
_UNDETERMINED: Final = "und"

#: WGS84, as everywhere else in the commons (`commons.merge`).
_GEOD: Final[Geod] = Geod(ellps="WGS84")
#: Metres per degree of latitude at its **maximum** (the poles). Tiling divides by it, so the
#: maximum is the safe choice: it yields the *smallest* step and therefore no coverage gap.
_M_PER_DEG_LAT: Final = 111_694.0
#: Metres per degree of longitude at the equator; scaled by cos(latitude) below.
_M_PER_DEG_LON: Final = 111_320.0

_BCP47: Final[re.Pattern[str]] = re.compile(BCP47_PATTERN)


class MediaWikiUnavailable(RuntimeError):
    """One MediaWiki request stayed unavailable across every bounded attempt."""


def endpoint_for(kind: WikiKind, lang: str = DEFAULT_LANG) -> str:
    """The Action API endpoint of a language edition — ``https://en.wikivoyage.org/w/api.php``."""
    return f"https://{lang}.{kind}.org/w/api.php"


def pinned_url(canonical_url: str, revid: int) -> str:
    """``canonicalurl`` pinned to the revision actually adapted (trap 4).

    ADR-0024 A1 credits *a revision*, not a moving article, and the API returns no pinned form
    — this is the only place the ``?oldid=`` URL exists. The separator is chosen rather than
    hardcoded because a canonical URL is permitted to carry a query string of its own.
    """
    separator = "&" if "?" in canonical_url else "?"
    return f"{canonical_url}{separator}oldid={revid}"


@dataclass(frozen=True, slots=True)
class _Article:
    """One existing page: the fields attribution and narration need, already resolved."""

    kind: WikiKind
    title: str
    lang: str
    revid: int
    #: Unpinned ``canonicalurl``; :attr:`url` is the pinned form.
    canonical_url: str
    #: Revision timestamp → ``Story.observed_at`` (ADR-0025 ruling 8).
    revised_on: date
    #: Plain-text intro (``prop=extracts``) or raw wikitext (``rvprop=content``).
    text: str = ""
    lat: float | None = None
    lon: float | None = None

    @property
    def url(self) -> str:
        return pinned_url(self.canonical_url, self.revid)

    @property
    def ref(self) -> SourceRef:
        """The stamp. ``id`` is ``<lang>:<Page Title>`` for *both* wikis (ADR-0024) — the
        attribution compiler dedupes contributing articles on it, so a bare title from one
        adapter and a qualified one from the other would credit one article twice."""
        site = self.kind.capitalize()
        return SourceRef(
            kind=self.kind,
            id=f"{self.lang}:{self.title}",
            url=self.url,
            license=LICENSE,
            attribution=f'"{self.title}", {site}, {self.url} — authors via page history',
        )

    @property
    def tag(self) -> str:
        """The BCP-47 key the article's text is filed under, or ``und`` if unusable."""
        return self.lang if _BCP47.fullmatch(self.lang) else _UNDETERMINED

    def story(self, text: str) -> Story:
        """A ``Story`` of the article's own words — verbatim; adapting them is T032's job."""
        return Story(text_by_lang={self.tag: text}, source=self.ref, observed_at=self.revised_on)


@dataclass(frozen=True, slots=True)
class MediaWikiAdapter:
    """Fetch narration + listings over an area from one wiki, stamped at ingestion.

    ``client`` injects an ``httpx`` transport exactly as the Overpass adapter does — the whole
    module is drivable from the committed fixture, and no test path reaches Wikimedia. Left
    ``None``, a client is created per call with the bounded timeout and the honest User-Agent.
    """

    kind: WikiKind = "wikivoyage"
    lang: str = DEFAULT_LANG
    #: Override for a mirror or a self-hosted MediaWiki; ``None`` derives it from kind + lang.
    endpoint: str | None = None
    #: ``gslimit`` — 50 is the anonymous ceiling for ``list=geosearch``.
    limit: int = 50
    #: Ceiling on geosearch circles per fetch. A polygon needing more is covered as far as the
    #: budget reaches and flagged ``degraded`` (FR-012) rather than fanning out unbounded.
    max_tiles: int = 32
    timeout: float = 30.0
    retries: int = 2
    backoff: float = 0.5
    #: Ingestion date stamped on every ``SourcedValue``; defaults to today (UTC) per fetch.
    #: Distinct from ``Story.observed_at``, which is the *revision's* date.
    observed_at: date | None = None
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("wikivoyage", "wikipedia"):
            raise ValueError(f"kind must be 'wikivoyage' or 'wikipedia', not {self.kind!r}")

    @property
    def url(self) -> str:
        return self.endpoint or endpoint_for(self.kind, self.lang)

    def fetch(self, polygon: BaseGeometry) -> base.FetchResult:
        """Every place this wiki knows inside ``polygon``, stamped (``SourceAdapter``)."""
        centres, radius, truncated = _cover(base.bbox_of(polygon), self.max_tiles)
        observed = self.observed_at or datetime.now(UTC).date()
        dropped: Counter[str] = Counter()
        records: list[SiteRecordV1] = []
        failures: list[str] = []
        if truncated:
            failures.append(f"area needs more than max_tiles={self.max_tiles} geosearch circles")

        with _session(self.client, self.timeout) as client:
            #: title → its geosearch coordinates. Insertion-ordered, so it doubles as the
            #: de-duplicated title list: adjacent circles overlap by construction and return
            #: the same article more than once, and the first sighting wins.
            hits: dict[str, tuple[float, float] | None] = {}
            for lat, lon in centres:
                try:
                    found = self._geosearch(client, lat, lon, radius)
                except MediaWikiUnavailable as unavailable:
                    failures.append(f"geosearch @ {lat:.4f},{lon:.4f}: {unavailable}")
                    continue
                for hit in found:
                    title = str(hit.get("title") or "")
                    if not title or title in hits:
                        continue
                    lat_hit, lon_hit = _as_float(hit.get("lat")), _as_float(hit.get("lon"))
                    hits[title] = (
                        (lat_hit, lon_hit) if lat_hit is not None and lon_hit is not None else None
                    )

            for batch in _batched(list(hits), _TITLE_BATCH):
                try:
                    articles = self._articles(client, batch)
                except MediaWikiUnavailable as unavailable:
                    failures.append(f"content for {len(batch)} titles: {unavailable}")
                    continue
                for article in articles:
                    records.extend(self._records(article, hits, polygon, observed, dropped))

        degraded = bool(failures)
        return base.FetchResult(
            kind=self.kind,
            records=tuple(records),
            dropped=dict(dropped),
            degraded=degraded,
            reason=f"MediaWiki degraded ({'; '.join(failures)})" if degraded else None,
        )

    def lookup(self, titles: Sequence[str]) -> dict[str, Story]:
        """FR-023, directly: *is there an article for this place?* — keyed by requested title.

        A title with no article — or with an article that has no intro text — is simply
        **absent** from the result. There is no empty-string story and no placeholder, because
        a caller that iterates this mapping can then only narrate places the wiki actually
        covers, which is FR-023. Raises :class:`MediaWikiUnavailable` if a
        batch cannot be fetched — unlike :meth:`fetch`, this answers about *specific* titles,
        so silently returning "no article" for a network failure would be a lie.
        """
        stories: dict[str, Story] = {}
        with _session(self.client, self.timeout) as client:
            for batch in _batched(list(titles), _TITLE_BATCH):
                payload = self._get(client, self._extract_params(batch, coordinates=False))
                # The API normalises titles ("gate_of_the_arsenal" → "Gate of the Arsenal"),
                # so the key the caller asked with is recovered from `query.normalized`.
                requested = {
                    str(entry.get("to")): str(entry.get("from"))
                    for entry in _query(payload).get("normalized") or ()
                }
                for article in _articles_in(payload, self.kind, self.lang):
                    if article.text:
                        stories[requested.get(article.title, article.title)] = article.story(
                            article.text
                        )
        return stories

    # --- requests -----------------------------------------------------------------

    def _geosearch(
        self, client: httpx.Client, lat: float, lon: float, radius: float
    ) -> list[Mapping[str, Any]]:
        payload = self._get(
            client,
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "list": "geosearch",
                # Four decimals ≈ 11 m — precise enough to centre a kilometre-wide circle and
                # coarse enough that two near-identical areas share one cached API response.
                "gscoord": f"{lat:.4f}|{lon:.4f}",
                "gsradius": str(int(radius)),
                "gslimit": str(self.limit),
            },
        )
        return list(_query(payload).get("geosearch") or ())

    def _articles(self, client: httpx.Client, titles: Sequence[str]) -> list[_Article]:
        """Content for a batch of titles — wikitext on Wikivoyage, intro extracts elsewhere."""
        if self.kind == "wikivoyage":
            # The structured prize is in the templates, so this asks for **wikitext**, not the
            # rendering (ADR-0024 rejects scraping HTML precisely because it discards them).
            params = {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "revisions|info",
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "inprop": "url",
                "titles": "|".join(titles),
            }
        else:
            params = self._extract_params(titles, coordinates=True)
        return _articles_in(self._get(client, params), self.kind, self.lang)

    def _extract_params(self, titles: Sequence[str], *, coordinates: bool) -> dict[str, str]:
        props = "extracts|info|revisions" + ("|coordinates" if coordinates else "")
        return {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": props,
            "exintro": "1",
            "explaintext": "1",
            "exlimit": "max",
            "rvprop": "ids|timestamp",
            "rvslots": "main",
            "inprop": "url",
            "titles": "|".join(titles),
        }

    def _get(self, client: httpx.Client, params: Mapping[str, str]) -> Mapping[str, Any]:
        """One bounded GET with retries; raise once the budget is spent (FR-012 upstream)."""
        last = "unknown error"
        for attempt in range(self.retries + 1):
            retry_after: float | None = None
            try:
                response = client.get(self.url, params=dict(params), timeout=self.timeout)
                if response.status_code in _RETRYABLE:
                    last = f"HTTP {response.status_code}"
                    retry_after = _retry_after_seconds(response)
                else:
                    response.raise_for_status()
                    payload: dict[str, Any] = response.json()
                    error = payload.get("error")
                    if error:
                        # The Action API answers 200 with an `error` object for a malformed
                        # request. Repeating an identical bad request cannot help: terminal.
                        raise MediaWikiUnavailable(f"API error {error.get('code', 'unknown')!r}")
                    return payload
            except httpx.TimeoutException:
                last = f"timeout after {self.timeout}s"
            except (httpx.HTTPError, ValueError) as error:
                last = f"{type(error).__name__}: {error}"
            if attempt < self.retries and self.backoff:
                time.sleep(_sleep_for(self.backoff, attempt, retry_after))
        raise MediaWikiUnavailable(last)

    # --- records ------------------------------------------------------------------

    def _records(
        self,
        article: _Article,
        hits: Mapping[str, tuple[float, float] | None],
        polygon: BaseGeometry,
        observed: date,
        dropped: Counter[str],
    ) -> list[SiteRecordV1]:
        if self.kind == "wikipedia":
            record = _from_page(article, hits, polygon, observed, dropped)
            return [record] if record else []
        # A Wikivoyage destination article is *not* itself a place, and its own coordinates are
        # not clipped: the article may sit outside a neighbourhood-sized polygon while the
        # listings it carries sit inside it. Only the listings become records.
        records: list[SiteRecordV1] = []
        seen: set[tuple[str, str, str]] = set()
        for listing_kind, params in _listings(article.text):
            key = (params.get("name", ""), params.get("lat", ""), params.get("long", ""))
            if key in seen:
                continue
            seen.add(key)
            record = _from_listing(listing_kind, params, article, polygon, observed, dropped)
            if record:
                records.append(record)
        return records


# --- parsing ----------------------------------------------------------------------


def _query(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    section: Mapping[str, Any] = payload.get("query") or {}
    return section


def _articles_in(payload: Mapping[str, Any], kind: WikiKind, lang: str) -> list[_Article]:
    """Every *existing* page in one response. Trap 2: the filter is per page, not per response —
    one ``batchcomplete`` envelope carries the misses and the hits together."""
    return [
        article
        for page in _query(payload).get("pages") or ()
        if (article := _article(page, kind, lang)) is not None
    ]


def _article(page: Mapping[str, Any], kind: WikiKind, lang: str) -> _Article | None:
    """One page → an article, or ``None`` when the wiki has none (FR-023).

    Trap 1: ``missing`` is the only evidence that counts. ``inprop=url`` returns
    ``canonicalurl``/``fullurl``/``editurl`` for a page that does not exist — they are the URLs
    it *would* have — so a URL-presence test reports an article for every place on earth.
    """
    if page.get("missing") or page.get("invalid"):
        return None
    revisions = page.get("revisions") or ()
    canonical = page.get("canonicalurl") or page.get("fullurl")
    title = page.get("title")
    if not revisions or not canonical or not title:
        return None
    revision: Mapping[str, Any] = revisions[0]
    # Trap 3: `page["lastrevid"]` is the page's *current* revision and drifts from the one whose
    # text is in hand. Attribution must name the revision actually adapted.
    revid = revision.get("revid")
    revised = _parse_date(revision.get("timestamp"))
    if revid is None or revised is None:
        return None
    coordinates = page.get("coordinates") or ()
    point: Mapping[str, Any] = coordinates[0] if coordinates else {}
    slot: Mapping[str, Any] = (revision.get("slots") or {}).get("main") or {}
    return _Article(
        kind=kind,
        title=str(title),
        lang=str(page.get("pagelanguage") or lang),
        revid=int(revid),
        canonical_url=str(canonical),
        revised_on=revised,
        text=str(page.get("extract") or slot.get("content") or "").strip(),
        lat=_as_float(point.get("lat")),
        lon=_as_float(point.get("lon")),
    )


def _listings(wikitext: str) -> Iterator[tuple[str, dict[str, str]]]:
    """``(listing kind, parameters)`` for every listing template, values stripped of markup.

    ``mwparserfromhell`` parses the templates as templates (ADR-0024 P1); a regex over the same
    wikitext re-derives a messy grammar and extracts silently wrong values.
    """
    for template in mwparserfromhell.parse(wikitext).filter_templates():
        name = str(template.name).strip().lower()
        if name not in LISTING_TEMPLATES:
            continue
        params = {
            str(param.name).strip().lower(): str(param.value.strip_code()).strip()
            for param in template.params
        }
        kind = params.get("type") if name in _GENERIC_TEMPLATES else name
        yield (kind or name), params


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(timestamp: Any) -> date | None:
    """ISO-8601 ``2026-06-07T13:23:53Z`` → its UTC date. ``None`` if unparseable."""
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC).date()
    except ValueError:
        return None


def _batched(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# --- stamping ---------------------------------------------------------------------


def _stamp(value: str, ref: SourceRef, confidence: float, observed: date) -> SourcedValue[str]:
    return SourcedValue[str].stamp(
        value=value, source=ref, confidence=confidence, observed_at=observed
    )


def _located(
    lat: float | None,
    lon: float | None,
    polygon: BaseGeometry,
    dropped: Counter[str],
) -> Wgs84Point | None:
    """The candidate's point, clipped to the true polygon — geosearch is a circle, not a bbox."""
    if lat is None or lon is None:
        dropped[base.NO_GEOMETRY] += 1
        return None
    try:
        # Coordinates come from the wiki's own geodata; never model-emitted (FR-005).
        point = point_from_lonlat(lon, lat)
    except GeometryError:
        dropped[base.INVALID_GEOMETRY] += 1
        return None
    if not polygon.covers(point):
        dropped[base.OUTSIDE_POLYGON] += 1
        return None
    return point


def _from_page(
    article: _Article,
    hits: Mapping[str, tuple[float, float] | None],
    polygon: BaseGeometry,
    observed: date,
    dropped: Counter[str],
) -> SiteRecordV1 | None:
    """A Wikipedia article → one record: its title, its coordinates, its intro as a story."""
    lat, lon = article.lat, article.lon
    if lat is None or lon is None:
        # `prop=coordinates` is the authority; the geosearch hit is the fallback for a page
        # whose coordinates live on a wrapper the extension does not index.
        lat, lon = hits.get(article.title) or (None, None)
    point = _located(lat, lon, polygon, dropped)
    if point is None:
        return None
    ref = article.ref
    return SiteRecordV1(
        names={article.tag: _stamp(article.title, ref, _CONFIDENCE, observed)},
        location=SourcedValue[Wgs84Point].stamp(
            value=point, source=ref, confidence=_CONFIDENCE, observed_at=observed
        ),
        # FR-023: no extract ⇒ no story. The place still exists; nothing is written for it.
        stories=(article.story(article.text),) if article.text else (),
    )


def _from_listing(
    listing_kind: str,
    params: Mapping[str, str],
    article: _Article,
    polygon: BaseGeometry,
    observed: date,
    dropped: Counter[str],
) -> SiteRecordV1 | None:
    """One Wikivoyage listing template → a stamped record, or ``None`` (counted) if unusable.

    Every value carries the *article's* ``SourceRef``: the article is what asserts the listing,
    and crediting it once per contributing article is what ADR-0024's dedupe-on-``source.id``
    expects.
    """
    name = params.get("name") or params.get("alt")
    if not name:
        dropped[base.NO_NAME] += 1
        return None
    lat, lon = params.get("lat", ""), params.get("long", "")
    if not lat.strip() and not lon.strip():
        dropped[base.NO_GEOMETRY] += 1
        return None
    point = _located(_as_float(lat), _as_float(lon), polygon, dropped)
    if point is None:
        return None

    ref = article.ref
    content = params.get("content", "")
    return SiteRecordV1(
        names={article.tag: _stamp(name, ref, _CONFIDENCE, observed)},
        location=SourcedValue[Wgs84Point].stamp(
            value=point, source=ref, confidence=_CONFIDENCE, observed_at=observed
        ),
        # `listing.see` / `listing.eat` — the template *is* the taxonomy this source has.
        categories=(_stamp(f"listing.{listing_kind}", ref, _CONFIDENCE, observed),),
        address=_optional(params.get("address"), ref, _CONFIDENCE, observed),
        opening_hours=_optional(params.get("hours"), ref, _HOURS_CONFIDENCE, observed),
        phone=_optional(params.get("phone"), ref, _CONFIDENCE, observed),
        price=_optional(params.get("price"), ref, _CONFIDENCE, observed),
        website=_optional(params.get("url"), ref, _CONFIDENCE, observed),
        # FR-023 again, at listing granularity: an empty `content=` yields no story.
        stories=(article.story(content),) if content else (),
    )


def _optional(
    value: str | None, ref: SourceRef, confidence: float, observed: date
) -> SourcedValue[str] | None:
    return _stamp(value, ref, confidence, observed) if value else None


# --- area coverage ----------------------------------------------------------------


def _cover(
    bounds: tuple[float, float, float, float], max_tiles: int
) -> tuple[list[tuple[float, float]], float, bool]:
    """``(geosearch centres, radius m, truncated)`` covering a bbox with circles.

    ``list=geosearch`` takes a centre and a radius capped at :data:`MAX_RADIUS_M`, so an area
    wider than 20 km needs several passes. Small areas — the common case — get exactly one
    circle sized to the bbox's half-diagonal, rounded up to a whole 100 m so that two nearly
    identical areas produce the *same* URL and hit Wikimedia's cache instead of re-querying.
    """
    minx, miny, maxx, maxy = bounds
    centre = ((miny + maxy) / 2, (minx + maxx) / 2)
    half_diagonal = max(
        float(_GEOD.inv(centre[1], centre[0], x, y)[2]) for x in (minx, maxx) for y in (miny, maxy)
    )
    if half_diagonal <= MAX_RADIUS_M:
        # 10 m is GeoData's floor on `gsradius`.
        rounded = math.ceil(max(half_diagonal, 10.0) / 100.0) * 100.0
        return [centre], min(rounded, MAX_RADIUS_M), False

    # A circle of radius r covers the square of side r√2 inscribed in it; stepping by that side
    # leaves no gap at the corners, where a naive step of 2r would.
    side = MAX_RADIUS_M * math.sqrt(2)
    # Metres per degree of longitude is largest nearest the equator, which is where the step in
    # *degrees* is smallest — use that latitude everywhere so no column is under-covered.
    ref_lat = 0.0 if miny <= 0 <= maxy else min(abs(miny), abs(maxy))
    lat_step = side / _M_PER_DEG_LAT
    lon_step = side / max(_M_PER_DEG_LON * math.cos(math.radians(ref_lat)), 1e-6)
    rows = max(1, math.ceil((maxy - miny) / lat_step))
    cols = max(1, math.ceil((maxx - minx) / lon_step))
    centres = [
        (miny + (row + 0.5) * (maxy - miny) / rows, minx + (col + 0.5) * (maxx - minx) / cols)
        for row in range(rows)
        for col in range(cols)
    ]
    return centres[:max_tiles], MAX_RADIUS_M, len(centres) > max_tiles


# --- transport ---------------------------------------------------------------------


@contextmanager
def _session(client: httpx.Client | None, timeout: float) -> Iterator[httpx.Client]:
    """Borrow the injected client, or own one for the duration and close it again."""
    if client is not None:
        yield client
        return
    owned = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
    try:
        yield owned
    finally:
        owned.close()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After``, which RFC 9110 allows as **either** delta-seconds or an HTTP date.

    ``None`` (absent or unparseable) means "fall back to exponential backoff" rather than
    "retry immediately"; a past date clamps to 0 — the server is saying now, not yesterday.
    """
    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _sleep_for(backoff: float, attempt: int, retry_after: float | None) -> float:
    """The server's own answer wins, capped; otherwise exponential backoff with full jitter.

    A 429 means the fair-use budget is spent, not that the request was unlucky, so a fixed
    sub-second wait retries into the same wall. The cap is deliberate: FR-012 says a slow
    source degrades to a flagged partial, not to a held-open request.
    """
    wait = min(retry_after if retry_after is not None else backoff * (2**attempt), _MAX_BACKOFF)
    return wait / 2 + random.random() * (wait / 2)
