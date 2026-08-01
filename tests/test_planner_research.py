"""T033 — the ``research`` node over the committed fixtures. Tier 1: no network, no key.

Both real adapters are driven exactly as `test_sources_overture.py` / `test_sources_osm.py`
drive them (a local parquet; an `httpx.MockTransport`), so what is pinned here is the
node's own contract: per-source counts, one streamed report per adapter in order, and —
the load-bearing pair — that a 504 **and** a raising adapter both degrade into a report
with the other sources' records intact (FR-012).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs

import httpx
import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from commons.licenses import SourceKind
from commons.merge import iter_sourced_values
from commons.sources import base
from commons.sources.osm import OsmAdapter
from commons.sources.overture import OvertureAdapter
from planner.nodes.research import ResearchResult, SourceReport, research

FIXTURES = Path(__file__).parent / "fixtures"
PARQUET = str(FIXTURES / "overture_places_rhodes.parquet")
OVERPASS_JSON = json.loads((FIXTURES / "overpass_rhodes.json").read_text(encoding="utf-8"))
OVERPASS_504 = (FIXTURES / "overpass_504.txt").read_text(encoding="utf-8")
EMPTY = {"version": 0.6, "elements": []}
AREA = box(28.216, 36.440, 28.232, 36.451)
OBSERVED = date(2026, 8, 1)
#: The fixtures' own totals — see `tests/fixtures/README.md`.
OVERTURE_ROWS, OSM_NODES = 200, 25


def overture() -> OvertureAdapter:
    return OvertureAdapter(parquet=PARQUET, observed_at=OBSERVED)


def osm(**by_element_type: Any) -> OsmAdapter:
    """An OSM adapter whose Overpass is a mock transport; ``way=504`` fails that sub-query."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.content.decode())["data"][0]
        for element_type, reply in by_element_type.items():
            if f"{element_type}[" in query:
                if isinstance(reply, int):
                    return httpx.Response(reply, text=OVERPASS_504)
                return httpx.Response(200, json=reply)
        return httpx.Response(200, json=EMPTY)

    return OsmAdapter(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        backoff=0.0,
        observed_at=OBSERVED,
    )


class ExplodingAdapter:
    """A source that raises instead of degrading — the case FR-012 must absorb."""

    kind: ClassVar[SourceKind] = "wikidata"

    def fetch(self, polygon: BaseGeometry) -> base.FetchResult:
        raise RuntimeError("SPARQL endpoint refused the connection")


@pytest.fixture(scope="module")
def healthy() -> ResearchResult:
    return research(AREA, [overture(), osm(node=OVERPASS_JSON)])


def test_fan_out_collects_every_source_in_order(healthy: ResearchResult) -> None:
    assert healthy.counts == {"overture": OVERTURE_ROWS, "osm": OSM_NODES}
    assert len(healthy.records) == OVERTURE_ROWS + OSM_NODES
    assert [report.kind for report in healthy.reports] == ["overture", "osm"]
    assert healthy.degraded_sources == ()
    assert all(report.reason is None for report in healthy.reports)
    # Records are concatenated in adapter order, so the pass is reproducible.
    assert {value.source.kind for value in healthy.records[0].names.values()} == {"overture"}
    assert {value.source.kind for value in healthy.records[-1].names.values()} == {"osm"}


def test_on_source_fires_once_per_adapter_in_order() -> None:
    streamed: list[SourceReport] = []
    result = research(AREA, [overture(), osm(node=OVERPASS_JSON)], on_source=streamed.append)
    assert streamed == list(result.reports)
    assert [report.found for report in streamed] == [OVERTURE_ROWS, OSM_NODES]


def test_every_emitted_value_carries_a_source(healthy: ResearchResult) -> None:
    """FR-003: nothing unstamped ever reaches the node's output."""
    for record in healthy.records:
        values = list(iter_sourced_values(record))
        assert values, "a record with no sourced value is not a record"
        for _field, value in values:
            assert value.source.kind and value.source.id and value.source.license


def test_a_504_degrades_the_source_and_keeps_its_partial_results() -> None:
    result = research(AREA, [overture(), osm(node=OVERPASS_JSON, way=504, relation=504)])
    overpass = result.reports[1]
    assert (overpass.kind, overpass.degraded, overpass.found) == ("osm", True, OSM_NODES)
    assert overpass.reason is not None and "504" in overpass.reason
    assert result.degraded_sources == ("osm",)
    # A partial answer is a legitimate answer: the 25 nodes survive the failed sub-queries.
    assert len(result.records) == OVERTURE_ROWS + OSM_NODES


def test_a_raising_adapter_degrades_instead_of_aborting_the_pass() -> None:
    streamed: list[SourceReport] = []
    result = research(
        AREA, [ExplodingAdapter(), osm(node=OVERPASS_JSON)], on_source=streamed.append
    )
    exploded = result.reports[0]
    assert (exploded.kind, exploded.found, exploded.degraded) == ("wikidata", 0, True)
    assert exploded.reason is not None
    assert "RuntimeError" in exploded.reason and "SPARQL" in exploded.reason
    assert exploded.dropped == {}
    # The pass continued: the surviving source is complete, and both were streamed.
    assert result.counts == {"wikidata": 0, "osm": OSM_NODES}
    assert len(result.records) == OSM_NODES
    assert len(streamed) == 2


def test_no_adapters_yields_an_empty_honest_pass() -> None:
    """SC-006: an empty answer is zero fabricated places, not an error."""
    result = research(AREA, [])
    assert (result.records, result.reports, result.counts) == ((), (), {})
    assert result.degraded_sources == ()
