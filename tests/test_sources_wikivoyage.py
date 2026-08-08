"""T033 — the Wikivoyage/Wikipedia adapter against the committed MediaWiki fixture.

`tests/fixtures/wikivoyage_rhodes.json` is a **real** capture of five MediaWiki Action API
requests over Rhodes (see the fixture README). Every request here is served by an
`httpx.MockTransport`, so no test path reaches Wikimedia. The module docstring of
`commons/sources/wikivoyage.py` names six shapes of the live API that will silently produce
wrong behaviour; each numbered trap below pins the one that guards against it.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from shapely.geometry import box

from commons import licenses
from commons.models import Story
from commons.sources import base
from commons.sources.wikivoyage import LICENSE, MediaWikiAdapter, WikiKind, pinned_url

FIXTURES = Path(__file__).parent / "fixtures"
DATA = json.loads((FIXTURES / "wikivoyage_rhodes.json").read_text(encoding="utf-8"))
CALLS = {call["name"]: call for call in DATA["calls"]}

WIKIVOYAGE_GEOSEARCH = CALLS["wikivoyage_geosearch"]["response"]
WIKIVOYAGE_PAGE = CALLS["wikivoyage_page_rhodes_city"]["response"]
WIKIVOYAGE_NO_ARTICLE = CALLS["wikivoyage_no_article"]["response"]
WIKIPEDIA_GEOSEARCH = CALLS["wikipedia_geosearch"]["response"]
WIKIPEDIA_EXTRACTS_BATCH = CALLS["wikipedia_extracts_batch"]["response"]

#: Matches `_capture.area_bbox_4326` in the fixture (lon/lat, EPSG:4326).
AREA = box(28.216, 36.44, 28.232, 36.451)
#: The date the adapter "fetched" — deliberately distinct from every revision timestamp in
#: the fixture, so a test that reads `SourcedValue.observed_at` instead of `Story.observed_at`
#: (the two clocks `commons/models.py` warns about) would be caught rather than coincide.
OBSERVED = date(2026, 8, 8)


def adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    kind: WikiKind = "wikivoyage",
    **kwargs: Any,
) -> MediaWikiAdapter:
    """An adapter wired to a mock transport — no network, no retry sleeping."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return MediaWikiAdapter(kind=kind, client=client, observed_at=OBSERVED, backoff=0.0, **kwargs)


def endpoint_route(
    mapping: Mapping[str, Mapping[str, Any]],
) -> Callable[[httpx.Request], httpx.Response]:
    """Route a request to a canned response keyed by ``"<wiki>_geosearch"`` / ``"<wiki>_content"``.

    Mirrors `test_sources_osm.py`'s `serve()`: routing is by *shape* of the request (which
    wiki, geosearch vs. a content fetch), not by the exact title list, so one fixture response
    can serve a test that queries a filtered or reordered set of titles.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        wiki = "wikivoyage" if "wikivoyage" in request.url.host else "wikipedia"
        action = "geosearch" if params.get("list") == "geosearch" else "content"
        key = f"{wiki}_{action}"
        payload = mapping.get(key)
        if payload is None:
            raise AssertionError(f"test sent an unexpected {key} request: {dict(params)}")
        return httpx.Response(200, json=dict(payload))

    return handler


# ── pinned_url: the revid-pinned URL is constructed, never returned (trap 4) ───────────────


def test_pinned_url_appends_oldid_to_the_real_canonical_url() -> None:
    canonical = WIKIVOYAGE_PAGE["query"]["pages"][0]["canonicalurl"]
    revid = WIKIVOYAGE_PAGE["query"]["pages"][0]["revisions"][0]["revid"]
    assert pinned_url(canonical, revid) == (
        "https://en.wikivoyage.org/wiki/Rhodes_(city)?oldid=5289050"
    )


def test_pinned_url_uses_an_ampersand_when_the_canonical_url_already_has_a_query_string() -> None:
    """No captured `canonicalurl` in this fixture carries a query string — `?` is always the
    separator in the response itself — so the `&` branch is exercised with a hand-built URL
    that is still shaped exactly like a MediaWiki `canonicalurl` would be if it ever did."""
    assert pinned_url("https://en.wikivoyage.org/wiki/Rhodes?action=history", 5289050) == (
        "https://en.wikivoyage.org/wiki/Rhodes?action=history&oldid=5289050"
    )


# ── trap 1 + 2: a missing page still has a URL, and misses ride with hits in one response ──


def test_missing_pages_carry_a_resolvable_looking_url_in_the_fixture() -> None:
    """Ground truth for the next test: both misses in `wikipedia_extracts_batch` have a
    `canonicalurl`, so keying "does an article exist?" on URL presence would report an
    article for both — the exact failure FR-023 forbids."""
    missing = [p for p in WIKIPEDIA_EXTRACTS_BATCH["query"]["pages"] if p.get("missing")]
    assert len(missing) == 2
    assert all(page.get("canonicalurl") for page in missing)
    assert all(page.get("fullurl") for page in missing)


def test_the_missing_flag_alone_refuses_a_page_even_if_it_carries_revisions() -> None:
    """`_article()` has two guards — `missing` and `not revisions` — and every missing page
    in this fixture also lacks `revisions`, so the two are redundant against every real shape
    here: removing the `missing` check is not observable through any fixture-derived data,
    because the `revisions` guard would still refuse the page. This pins the `missing` guard
    on its own contract with a synthetic case the real API never returns (`missing: True`
    *and* a full `revisions` array together) — the one case that makes the `missing` check
    load-bearing rather than redundant, e.g. against a future cheaper existence probe that
    reports `missing` without `rvprop`."""
    real_page = copy.deepcopy(WIKIVOYAGE_PAGE["query"]["pages"][0])
    real_page["missing"] = True
    response = {"batchcomplete": True, "query": {"pages": [real_page]}}

    ad = adapter(endpoint_route({"wikivoyage_content": response}), kind="wikivoyage")

    assert ad.lookup(["Rhodes (city)"]) == {}


def test_lookup_drops_missing_pages_from_a_single_batchcomplete_response() -> None:
    """FR-023 + trap 2: `wikipedia_extracts_batch` is one response holding 4 real pages and
    2 missing ones (`Gate of the Arsenal` / `Πύλη Ταρσανά`) — the filter must be per page."""
    titles = [
        "Archaeological Museum of Rhodes",
        "Panagia tou Kastrou",
        "Church of St John of the Collachium",
        "Fortifications of Rhodes",
        "Gate of the Arsenal",
        "Πύλη Ταρσανά",
    ]
    ad = adapter(endpoint_route({"wikipedia_content": WIKIPEDIA_EXTRACTS_BATCH}), kind="wikipedia")

    stories = ad.lookup(titles)

    assert set(stories) == {
        "Archaeological Museum of Rhodes",
        "Panagia tou Kastrou",
        "Church of St John of the Collachium",
        "Fortifications of Rhodes",
    }
    fortifications = stories["Fortifications of Rhodes"]
    assert fortifications.source.kind == "wikipedia"
    assert fortifications.source.id == "en:Fortifications of Rhodes"
    assert fortifications.source.license == LICENSE == "CC-BY-SA-4.0"
    assert fortifications.source.url == (
        "https://en.wikipedia.org/wiki/Fortifications_of_Rhodes?oldid=1365028683"
    )
    assert fortifications.source.attribution == (
        '"Fortifications of Rhodes", Wikipedia, '
        "https://en.wikipedia.org/wiki/Fortifications_of_Rhodes?oldid=1365028683"
        " — authors via page history"
    )
    assert fortifications.observed_at == date(2026, 7, 19)  # revisions[0].timestamp
    assert not hasattr(fortifications, "bundleable")  # Story derives it; never asserts it
    assert licenses.bundleable(fortifications.source.kind, fortifications.source.license) is True


def test_a_title_with_no_article_on_either_wiki_yields_no_story_wikipedia() -> None:
    """Isolates the 2 missing pages from `wikipedia_extracts_batch` (filtered, not
    fabricated) so this asserts the missing-only case on its own, distinct from the
    combined-response case `test_lookup_drops_missing_pages_...` already covers."""
    missing_only = {
        "batchcomplete": True,
        "query": {
            "pages": [p for p in WIKIPEDIA_EXTRACTS_BATCH["query"]["pages"] if p.get("missing")]
        },
    }
    ad = adapter(endpoint_route({"wikipedia_content": missing_only}), kind="wikipedia")
    assert ad.lookup(["Gate of the Arsenal", "Πύλη Ταρσανά"]) == {}


def test_a_title_with_no_article_on_either_wiki_yields_no_story_wikivoyage() -> None:
    """The same place, the same absence, on the other wiki (`wikivoyage_no_article` call) —
    FR-023 does not invent a story on either source."""
    ad = adapter(endpoint_route({"wikivoyage_content": WIKIVOYAGE_NO_ARTICLE}), kind="wikivoyage")
    assert ad.lookup(["Gate of the Arsenal", "Πύλη Ταρσανά"]) == {}


# ── trap 3: `lastrevid` is a decoy — attribution must key on revisions[0].revid ────────────


def test_lastrevid_and_revisions_revid_agree_in_the_raw_capture() -> None:
    """Documents why the next test must mutate rather than use the capture as-is: every page
    in this fixture happens to have `lastrevid == revisions[0].revid` (README says so too), so
    a test built straight from the capture cannot fail if the adapter reads the wrong field."""
    for call in ("wikivoyage_page_rhodes_city", "wikipedia_extracts_batch"):
        for page in CALLS[call]["response"]["query"]["pages"]:
            if page.get("missing"):
                continue
            assert page["lastrevid"] == page["revisions"][0]["revid"]


def test_attribution_uses_revisions_revid_not_the_decoy_lastrevid() -> None:
    """The strongest form the task asked for: real fixture data, with only the top-level
    `lastrevid` decoy changed so it disagrees with `revisions[0].revid` — the field
    attribution must actually key on. A `page["lastrevid"]`-keyed adapter would pin the URL to
    9999999 and this assertion would fail."""
    decoyed_page = copy.deepcopy(WIKIVOYAGE_PAGE["query"]["pages"][0])
    assert decoyed_page["revisions"][0]["revid"] == 5289050
    decoyed_page["lastrevid"] = 9_999_999
    decoyed_response = {"batchcomplete": True, "query": {"pages": [decoyed_page]}}

    ad = adapter(endpoint_route({"wikivoyage_content": decoyed_response}), kind="wikivoyage")
    story = ad.lookup(["Rhodes (city)"])["Rhodes (city)"]

    assert story.source.url == "https://en.wikivoyage.org/wiki/Rhodes_(city)?oldid=5289050"
    assert "9999999" not in story.source.url
    assert story.observed_at == date(2026, 6, 7)  # revisions[0].timestamp, not `touched`


# ── an unstamped story is refused (FR-003, applied to Story too) ───────────────────────────


def test_a_story_with_no_source_ref_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError):
        Story(text_by_lang={"en": "some text"}, observed_at=date(2026, 8, 8))  # type: ignore[call-arg]


# ── Wikivoyage fetch(): listing templates become records, stamped at the article's ref ─────


@pytest.fixture(scope="module")
def wikivoyage_result() -> base.FetchResult:
    ad = adapter(
        endpoint_route(
            {
                "wikivoyage_geosearch": WIKIVOYAGE_GEOSEARCH,
                "wikivoyage_content": WIKIVOYAGE_PAGE,
            }
        ),
        kind="wikivoyage",
    )
    return ad.fetch(AREA)


def values_of(record: Any) -> list[Any]:
    return [
        record.location,
        *record.names.values(),
        *record.categories,
        *(
            value
            for value in (
                record.address,
                record.opening_hours,
                record.phone,
                record.price,
                record.website,
            )
            if value
        ),
    ]


def test_every_listing_value_is_stamped_cc_by_sa_and_bundleable_is_derived(
    wikivoyage_result: base.FetchResult,
) -> None:
    assert len(wikivoyage_result) > 0
    assert wikivoyage_result.degraded is False
    expected_url = "https://en.wikivoyage.org/wiki/Rhodes_(city)?oldid=5289050"
    expected_attribution = (
        '"Rhodes (city)", Wikivoyage, https://en.wikivoyage.org/wiki/Rhodes_(city)'
        "?oldid=5289050 — authors via page history"
    )
    for record in wikivoyage_result:
        # Every listing is credited to the *article*, not to itself — one SourceRef.id
        # shared by every listing the article carries.
        assert record.location.source.id == "en:Rhodes (city)"
        for value in values_of(record):
            assert value.source.kind == "wikivoyage"
            assert value.source.license == "CC-BY-SA-4.0"
            assert value.source.url == expected_url
            assert value.source.attribution == expected_attribution
            # Derived, not author-asserted: SourcedValue._enforce_quarantine would already
            # refuse a mismatch, but this cross-checks against the registry function itself.
            assert value.bundleable == licenses.bundleable(value.source.kind, value.source.license)
            assert value.bundleable is True
            assert value.observed_at == OBSERVED  # ingestion date, never the revision date


def test_geosearch_circle_vs_true_polygon_drops_out_of_area_listings(
    wikivoyage_result: base.FetchResult,
) -> None:
    """The area is a polygon and geosearch is a circle (README); the same clip applies to
    every listing coordinate the article carries, not only to geosearch hits."""
    by_name = {record.names["en"].value: record for record in wikivoyage_result}

    # Real, out-of-bbox listing coordinates from the captured wikitext.
    assert "Rhodes International Airport" not in by_name  # lat 36.405419, south of the bbox
    assert "Blue Star Ferries Dock" not in by_name  # long 28.2379, east of max_lon 28.232
    # Real listings whose lat=/long= are simply blank in the wikitext.
    assert "Temple of Pythian Apollo" not in by_name
    assert "Stoa building" not in by_name

    assert wikivoyage_result.dropped[base.OUTSIDE_POLYGON] >= 2
    assert wikivoyage_result.dropped[base.NO_GEOMETRY] >= 2


def test_optional_listing_fields_are_none_when_blank_and_present_when_filled(
    wikivoyage_result: base.FetchResult,
) -> None:
    by_name = {record.names["en"].value: record for record in wikivoyage_result}

    palace = by_name["Palace of the Grand Master of the Knights of Rhodes"]
    assert palace.address is None and palace.opening_hours is None
    assert palace.phone is None and palace.price is None and palace.website is None
    assert "listing.see" in {value.value for value in palace.categories}

    museum = by_name["Rhodes Archaeological Museum"]
    assert museum.address is not None and museum.address.value == "Megalou Alexandrou Square"
    assert museum.phone is not None and museum.phone.value == "+30 2241 075674, +30 2241 034719"
    assert museum.opening_hours is not None
    assert museum.opening_hours.value == "Open in season till 20:00"
    assert museum.opening_hours.confidence == 0.3  # free-prose hours score lower than the rest
    assert museum.location.confidence == 0.6
    assert museum.price is not None and museum.price.value == "€3"
    assert museum.website is not None
    assert museum.website.value == "http://odysseus.culture.gr/h/1/eh155.jsp?obj_id=3312"
    assert museum.stories and museum.stories[0].text_by_lang["en"].startswith("It has two floors")


# ── Wikipedia fetch(): geosearch + prop=coordinates, not the geosearch hit's own lat/lon ───


@pytest.fixture(scope="module")
def wikipedia_geosearch_subset() -> dict[str, Any]:
    """`wikipedia_extracts_batch` was captured for a curated 6-title batch (4 real articles +
    the 2 missing cross-source-anchor titles), not the natural 20-title batch a `fetch()` over
    the full `wikipedia_geosearch` fixture would send. Filtering the real 20 hits down to the
    4 this fixture also has content for keeps every byte real; nothing here is fabricated."""
    present = {
        "Archaeological Museum of Rhodes",
        "Panagia tou Kastrou",
        "Church of St John of the Collachium",
        "Fortifications of Rhodes",
    }
    hits = [hit for hit in WIKIPEDIA_GEOSEARCH["query"]["geosearch"] if hit["title"] in present]
    assert len(hits) == 4
    return {"batchcomplete": True, "query": {"geosearch": hits}}


def test_wikipedia_fetch_locates_from_prop_coordinates_and_drops_the_batchs_misses(
    wikipedia_geosearch_subset: dict[str, Any],
) -> None:
    ad = adapter(
        endpoint_route(
            {
                "wikipedia_geosearch": wikipedia_geosearch_subset,
                "wikipedia_content": WIKIPEDIA_EXTRACTS_BATCH,
            }
        ),
        kind="wikipedia",
    )

    result = ad.fetch(AREA)

    assert result.degraded is False
    by_name = {record.names["en"].value: record for record in result}
    # The 2 missing titles ride along in the same `wikipedia_extracts_batch` response but were
    # never geosearch hits here either — they must not surface as phantom records.
    assert set(by_name) == {
        "Archaeological Museum of Rhodes",
        "Panagia tou Kastrou",
        "Church of St John of the Collachium",
        "Fortifications of Rhodes",
    }

    fortifications = by_name["Fortifications of Rhodes"]
    # `prop=coordinates` (36.44385254, 28.22916102), not the geosearch hit's own
    # (36.44385254463199, 28.229161022192063) — close but a different string, so a test that
    # merely checked "inside the polygon" could not tell the two apart.
    assert (fortifications.location.value.x, fortifications.location.value.y) == (
        28.22916102,
        36.44385254,
    )
    assert fortifications.location.source.id == "en:Fortifications of Rhodes"
    assert fortifications.location.source.license == "CC-BY-SA-4.0"
    assert fortifications.location.bundleable is True
    assert fortifications.stories and fortifications.stories[0].observed_at == date(2026, 7, 19)
