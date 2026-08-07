"""T027 — the OSM adapter against the committed Overpass fixtures (never the network).

`tests/fixtures/overpass_rhodes.json` is 25 **real** OSM nodes from the Rhodes old-town
bbox and `overpass_504.txt` is a **real captured** Overpass 504 body (see the fixture
README). Every request here is served by an `httpx.MockTransport`, so the tests pin tag
mapping, ODbL stamping, Greek `name:el` preservation and — the load-bearing one — that a
504 degrades into a flagged *partial* answer instead of an exception or a hang.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from shapely.geometry import Point, box

from commons.models import SiteRecordV1, SourcedValue
from commons.sources import base
from commons.sources import osm as osm_module
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


# ── ADR-0027: a retry policy that can actually survive a rate limit ───────────────
#
# The 2026-08-07 Rhodes pass lost every `relation` to `HTTP 429` while the old policy
# retried once, 0.5s later, into the same spent fair-use budget. A 429 means "your quota
# is gone", not "you were unlucky" — so the wait must come from the server when it offers
# one, and grow when it does not. These pin the policy, not the implementation's arithmetic.


def _sleepless(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> OsmAdapter:
    """Like `adapter()` but keeps a real `backoff`, so the sleep can be observed."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OsmAdapter(client=client, observed_at=OBSERVED, element_types=("node",), **kwargs)


def _serve(status: int, headers: dict[str, str] | None = None) -> Callable[..., httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, json=EMPTY)

    return handler


def test_retry_after_seconds_is_honoured_over_our_own_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server's own answer wins — retrying sooner than asked just burns the budget."""
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)

    result = _sleepless(_serve(429, {"Retry-After": "3"}), retries=1).fetch(AREA)

    # Full jitter over [wait/2, wait]: never longer than asked, never instant.
    assert len(waits) == 1 and 1.5 <= waits[0] <= 3.0
    assert result.degraded is True


def test_backoff_grows_exponentially_when_the_server_offers_no_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `Retry-After` ⇒ widening windows, not a flat 0.5s that retries into the wall.

    **The jitter is pinned deliberately.** Asserting each sleep merely falls inside its
    jitter window `[w/2, w]` does *not* discriminate: the previous linear policy
    (0.5, 1.0, 1.5) lands inside the exponential windows (0.5, 1.0, 2.0) at **every**
    attempt, so such a test passes against the very bug it guards — the FAIL-007 shape,
    a test asserting what the code does rather than what is required. Pinning
    `random.random()` to its maximum makes the wait exactly the window ceiling, so the
    doubling is asserted and linear backoff fails at the third attempt (1.5 ≠ 2.0).
    """
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)
    monkeypatch.setattr(random, "random", lambda: 1.0)

    _sleepless(_serve(503), retries=3, backoff=0.5).fetch(AREA)

    assert waits == [0.5, 1.0, 2.0], "each window must double; linear would give 0.5, 1.0, 1.5"


def test_an_absurd_retry_after_is_capped_rather_than_stalling_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-012: a source asking for two minutes means degrade, not hold the stream open."""
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)

    result = _sleepless(_serve(429, {"Retry-After": "120"}), retries=1).fetch(AREA)

    assert waits and max(waits) <= osm_module._MAX_BACKOFF
    assert result.degraded is True


def test_a_malformed_retry_after_falls_back_instead_of_retrying_instantly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unparseable header must not read as "retry now" — the worst possible reading."""
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)

    _sleepless(_serve(429, {"Retry-After": "soon-ish"}), retries=1, backoff=0.5).fetch(AREA)

    assert len(waits) == 1 and waits[0] > 0.0


def test_retry_after_accepts_an_http_date_not_only_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RFC 9110 allows either form, and Overpass mirrors differ on which they send."""
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=4))

    _sleepless(_serve(429, {"Retry-After": when}), retries=1).fetch(AREA)

    assert len(waits) == 1 and 0.0 < waits[0] <= 4.0


def test_the_default_endpoint_is_the_mirror_not_the_main_instance() -> None:
    """ADR-0027: the main instance's fair-use budget is shared with the whole world."""
    assert "kumi.systems" in OsmAdapter().endpoint
    assert "overpass-api.de" not in OsmAdapter().endpoint


def test_three_attempts_by_default_because_one_retry_cannot_survive_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, json=EMPTY)

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    result = _sleepless(handler).fetch(AREA)

    assert attempts == 3, "retries=2 ⇒ three attempts"
    assert result.degraded is True
