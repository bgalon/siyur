"""T027 — the OSM adapter against the committed Overpass fixtures (never the network).

`tests/fixtures/overpass_rhodes.json` is 25 **real** OSM nodes from the Rhodes old-town
bbox and `overpass_504.txt` is a **real captured** Overpass 504 body (see the fixture
README). Every request here is served by an `httpx.MockTransport`, so the tests pin tag
mapping, ODbL stamping, Greek `name:el` preservation and — the load-bearing one — that a
504 degrades into a flagged *partial* answer instead of an exception or a hang.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from shapely.geometry import Point, box

from commons.models import SiteRecordV1, SourcedValue
from commons.sources import base
from commons.sources.osm import ATTRIBUTION, LICENSE, OsmAdapter

FIXTURES = Path(__file__).parent / "fixtures"
OVERPASS_JSON = json.loads((FIXTURES / "overpass_rhodes.json").read_text(encoding="utf-8"))
OVERPASS_504 = (FIXTURES / "overpass_504.txt").read_text(encoding="utf-8")
EMPTY = {"version": 0.6, "elements": []}
AREA = box(28.216, 36.440, 28.232, 36.451)
OBSERVED = date(2026, 8, 1)
#: The fixture's cross-source anchor: "Gate of the Arsenal" (see fixtures/README.md).
GATE = "node/794491388"


def adapter(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> OsmAdapter:
    """An adapter wired to a mock transport — no network, no retry sleeping."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OsmAdapter(client=client, backoff=0.0, observed_at=OBSERVED, **kwargs)


def query_of(request: httpx.Request) -> str:
    """The Overpass QL the adapter posted (form-encoded as ``data=…``)."""
    return parse_qs(request.content.decode())["data"][0]


def serve(**by_element_type: Any) -> Callable[[httpx.Request], httpx.Response]:
    """Answer each per-element-type sub-query with a payload or an HTTP status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = query_of(request)
        for element_type, reply in by_element_type.items():
            if f"{element_type}[" in query:
                if isinstance(reply, int):
                    return httpx.Response(reply, text=OVERPASS_504)
                return httpx.Response(200, json=reply)
        return httpx.Response(200, json=EMPTY)

    return handler


@pytest.fixture(scope="module")
def result() -> base.FetchResult:
    return adapter(serve(node=OVERPASS_JSON)).fetch(AREA)


def values_of(record: SiteRecordV1) -> list[SourcedValue[Any]]:
    return [
        record.location,
        *record.names.values(),
        *record.categories,
        *(value for value in (record.address, record.opening_hours) if value),
    ]


def test_every_value_is_stamped_odbl_with_attribution(result: base.FetchResult) -> None:
    assert len(result) == 25
    assert (result.degraded, result.reason) == (False, None)
    for record in result:
        assert record.gers_id is None  # OSM shares no GERS ids with Overture
        for value in values_of(record):
            assert value.source.kind == "osm"
            assert (value.source.license, value.source.attribution) == (LICENSE, ATTRIBUTION)
            assert value.bundleable is True  # ODbL is allowlisted
            assert value.source.id.startswith(("node/", "way/", "relation/"))
            assert value.observed_at == OBSERVED


def test_tags_map_to_names_categories_address_and_hours(result: base.FetchResult) -> None:
    by_id = {record.location.source.id: record for record in result}
    gate = by_id[GATE]
    assert gate.names["el"].value == "Πύλη Ταρσανά"
    assert gate.names["en"].value == "Gate of the Arsenal"
    assert "und" not in gate.names  # the bare `name` duplicates `name:el`, so it adds nothing
    assert "historic.city_gate" in {value.value for value in gate.categories}
    assert gate.location.source.url == "https://www.openstreetmap.org/node/794491388"

    fuel = by_id["node/297525681"]
    assert fuel.opening_hours is not None
    assert (fuel.opening_hours.value, fuel.opening_hours.source.license) == ("24/7", LICENSE)
    assert "amenity.fuel" in {value.value for value in fuel.categories}

    addressed = [record.address.value for record in result if record.address]
    assert any("," in address for address in addressed), "addr:* tags compose an address"


def test_greek_names_are_preserved_verbatim(result: base.FetchResult) -> None:
    greek = {
        record.location.source.id: record.names["el"].value
        for record in result
        if "el" in record.names
    }
    assert len(greek) == 6  # the six `name:el` nodes in the fixture
    assert greek["node/126244310"] == "Ρόδος"
    assert greek[GATE] == "Πύλη Ταρσανά"


def test_coordinates_come_from_the_fixture_and_are_never_synthesised(
    result: base.FetchResult,
) -> None:
    truth = {
        f"{element['type']}/{element['id']}": (element["lon"], element["lat"])
        for element in OVERPASS_JSON["elements"]
    }
    for record in result:
        point = record.location.value
        assert isinstance(point, Point)
        # lon first — Overpass reports lat/lon and its bbox filter is lat-first too.
        assert (point.x, point.y) == truth[record.location.source.id]
        assert AREA.covers(point)


def test_the_bbox_sent_to_overpass_is_lat_first() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(query_of(request))
        return httpx.Response(200, json=EMPTY)

    adapter(handler).fetch(AREA)
    assert all("(36.44,28.216,36.451,28.232)" in query for query in sent)
    assert len(sent) == 3  # one small bounded request per element type


def test_a_504_yields_a_flagged_partial_never_an_exception() -> None:
    """FR-012: the `node` results survive a 504 on the `way`/`relation` sub-queries."""
    result = adapter(serve(node=OVERPASS_JSON, way=504, relation=504)).fetch(AREA)
    assert len(result) == 25, "a partial result is never discarded"
    assert result.degraded is True
    assert result.reason is not None
    assert "504" in result.reason and "way" in result.reason and "relation" in result.reason


def test_a_total_outage_degrades_instead_of_raising() -> None:
    result = adapter(serve(node=504, way=504, relation=504)).fetch(AREA)
    assert (len(result), result.degraded) == (0, True)
    assert result.reason is not None and "504" in result.reason


def test_retries_are_bounded_and_timeouts_degrade() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("too slow", request=request)

    result = adapter(handler, retries=2, element_types=("node",)).fetch(AREA)
    assert attempts == 3, "bounded: retries + 1 attempts, then give up"
    assert (result.degraded, len(result)) == (True, 0)
    assert result.reason is not None and "timeout" in result.reason
