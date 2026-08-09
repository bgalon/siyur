"""T017 — the routing seam against the committed Valhalla captures (never the network).

`tests/fixtures/valhalla_rhodes_route.json` and `..._matrix.json` are **real** responses
from a real container over a real Rhodes walking graph (fixture README). Every request in
this file is served by an `httpx.MockTransport` or replayed from those captures, and one
test proves the fixture path opens no socket at all.

Three properties here are the ones that catch a *plausible* wrong answer, which is the only
kind this module produces:

* **≥3 vertices per leg.** A two-point line is a straight line pretending to be a route
  (FR-003). The capture has 26 and 33.
* **The matrix is asymmetric with a zero diagonal.** 0→2 is 504 s against 2→0's 525 s — a
  one-way, stepped old town. A fabricated or haversine table is perfectly symmetric, so
  this is the cheapest possible check that a matrix fixture is real.
* **The pace reached the engine.** A silently dropped `costing_options` block is invisible
  in the geometry and reprices the whole day at Valhalla's 5.1 km/h default, so the request
  body is asserted, not just the fact that a duration came back.
"""

from __future__ import annotations

import json
import socket
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

import httpx
import pytest
from shapely import LineString

from commons import licenses
from commons import routing as routing_module
from commons.models import RouteLegV1
from commons.routing import (
    FixtureProvider,
    NoRouteFound,
    OpenRouteServiceProvider,
    PedestrianCosting,
    RoutingError,
    RoutingProvider,
    RoutingUnavailable,
    ValhallaProvider,
    Waypoint,
    routing_source,
    select_provider,
)

FIXTURES = Path(__file__).parent / "fixtures"
ROUTE_JSON: dict[str, Any] = json.loads(
    (FIXTURES / "valhalla_rhodes_route.json").read_text("utf-8")
)
MATRIX_JSON: dict[str, Any] = json.loads(
    (FIXTURES / "valhalla_rhodes_matrix.json").read_text("utf-8")
)
#: The Rhodes old-town bbox every fixture in this directory is captured over.
RHODES = (28.216, 36.440, 28.232, 36.451)
#: The three real OSM nodes the capture routed through (fixture README).
STOPS = (
    Waypoint(lon=28.2280031, lat=36.4460502),
    Waypoint(lon=28.2282751, lat=36.4436351),
    Waypoint(lon=28.2256561, lat=36.4425208),
)
CAPTURED = PedestrianCosting(walking_speed_kmh=4.5)


@pytest.fixture(scope="module")
def legs() -> tuple[RouteLegV1, ...]:
    return FixtureProvider.from_dir().route(STOPS, costing=CAPTURED)


def valhalla(handler: Any, **kwargs: Any) -> ValhallaProvider:
    return ValhallaProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


# ── the replayed capture: geometry, units, stamp ──────────────────────────────


def test_a_leg_is_a_real_route_not_a_straight_line(legs: tuple[RouteLegV1, ...]) -> None:
    """≥3 vertices, lon-first, inside Rhodes — the decoder's three failure modes at once.

    A decoder that returned only the endpoints (or built the line from the requested
    locations rather than the encoded shape) gives 2 vertices; one that forgot polyline6's
    lat,lon order puts every vertex in the Indian Ocean and fails the bbox.
    """
    assert [len(leg.geometry.coords) for leg in legs] == [26, 33]
    min_lon, min_lat, max_lon, max_lat = RHODES
    for leg in legs:
        assert isinstance(leg.geometry, LineString)
        assert len(leg.geometry.coords) >= 3
        for lon, lat in leg.geometry.coords:
            assert min_lon <= lon <= max_lon, "longitude outside the captured bbox (axes swapped?)"
            assert min_lat <= lat <= max_lat


def test_legs_carry_metres_seconds_and_the_stop_order_they_join(
    legs: tuple[RouteLegV1, ...],
) -> None:
    """Valhalla answers in kilometres and fractional seconds; the card says metres and int."""
    assert [(leg.id, leg.from_stop, leg.to_stop) for leg in legs] == [
        ("leg-0", 0, 1),
        ("leg-1", 1, 2),
    ]
    assert [leg.distance_m for leg in legs] == pytest.approx([328.0, 370.0])
    assert [leg.duration_s for leg in legs] == [262, 297]
    assert all(isinstance(leg.duration_s, int) for leg in legs)


def test_the_replayed_durations_are_the_captured_pace_not_valhallas_default(
    legs: tuple[RouteLegV1, ...],
) -> None:
    """4.5 km/h was requested; 5.1 is what an omitted costing block would have produced.

    This is a property of the *numbers*, complementing the request-body assertion below: it
    also reddens on a units bug, since km-not-converted reads as 0.0045 km/h.
    """
    for leg in legs:
        implied_kmh = leg.distance_m / leg.duration_s * 3.6
        assert implied_kmh == pytest.approx(4.5, abs=0.05)
        assert implied_kmh != pytest.approx(routing_module.VALHALLA_DEFAULT_WALKING_SPEED_KMH)


def test_every_leg_is_stamped_as_a_produced_work_of_osm(legs: tuple[RouteLegV1, ...]) -> None:
    """The exact stamp `route-leg.md` fixes — and `bundleable` **derived**, never a field."""
    for leg in legs:
        assert leg.source.model_dump() == {
            "kind": "osm",
            "id": "valhalla:pedestrian",
            "url": None,
            "license": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
        }
        assert licenses.bundleable(leg.source.kind, leg.source.license) is True
    # A leg that grew a `bundleable` field would let a filter read the stamp off it instead
    # of deriving — and the `getattr(v, "bundleable", False)` repair drops every leg from
    # every bundle silently (ADR-0025 ruling 8).
    assert "bundleable" not in RouteLegV1.model_fields


def test_a_stamp_that_would_not_derive_to_bundleable_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The quarantine check inside `routing_source` fires, rather than being commentary."""
    monkeypatch.setattr(routing_module, "LICENSE", "proprietary")
    with pytest.raises(RoutingError, match="bundleable"):
        routing_source()


def test_the_fixture_path_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0020's Tier-1 guarantee, enforced rather than described."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the fixture provider opened a socket; it must be offline")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    provider = FixtureProvider.from_dir()
    assert len(provider.route(STOPS, costing=CAPTURED)) == 2
    assert provider.matrix(STOPS, costing=CAPTURED).size == 3


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"costing": PedestrianCosting(walking_speed_kmh=3.6)}, "recorded at 4.5"),
        ({"costing": CAPTURED, "waypoints": STOPS[:2]}, "replays what was captured"),
    ],
)
def test_a_recording_refuses_to_answer_a_question_it_did_not_record(
    kwargs: dict[str, Any], match: str
) -> None:
    """Replaying a 4.5 km/h Rhodes walk for another pace or another day is the Andorra trap
    in fixture form: plausible numbers, no engine behind them."""
    call = {"waypoints": STOPS, **kwargs}
    with pytest.raises(RoutingError, match=match):
        FixtureProvider.from_dir().route(call["waypoints"], costing=call["costing"])


# ── the matrix ────────────────────────────────────────────────────────────────


def test_the_matrix_is_asymmetric_with_a_zero_diagonal() -> None:
    """Real network, not a haversine stand-in — and the row/column order is not transposed."""
    matrix = FixtureProvider.from_dir().matrix(STOPS, costing=CAPTURED)
    assert matrix.size == 3
    assert [matrix.duration_s(i, i) for i in range(3)] == [0, 0, 0]
    assert matrix.duration_s(0, 2) == 504
    assert matrix.duration_s(2, 0) == 525
    assert matrix.duration_s(0, 2) != matrix.duration_s(2, 0)
    # Kilometres in the capture; metres in the DTO, like `RouteLegV1.distance_m`.
    assert matrix.distance_m(0, 2) == pytest.approx(629.0)
    assert matrix.distance_m(2, 0) == pytest.approx(654.0)


def test_an_unreachable_pair_is_named_never_zero() -> None:
    """Valhalla nulls a cell it cannot connect. `int(cell or 0)` would make two stops
    adjacent in zero seconds, which is a feasible-looking lie rather than a missing value."""
    holed = json.loads(json.dumps(MATRIX_JSON))
    holed["sources_to_targets"][0][2] = {
        "from_index": 0,
        "to_index": 2,
        "time": None,
        "distance": None,
    }
    with pytest.raises(NoRouteFound, match="waypoint 0 to waypoint 2"):
        FixtureProvider(route_response=ROUTE_JSON, matrix_response=holed).matrix(
            STOPS, costing=CAPTURED
        )


# ── the Valhalla client: what actually goes on the wire ───────────────────────


def test_the_requested_pace_reaches_the_engine() -> None:
    """The assertion that catches a dropped costing block — invisible in the geometry."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=ROUTE_JSON)

    valhalla(handler).route(STOPS, costing=PedestrianCosting(walking_speed_kmh=3.8))
    assert seen[0]["costing"] == "pedestrian"
    assert seen[0]["costing_options"] == {
        "pedestrian": {"walking_speed": 3.8, "walkway_factor": 0.9}
    }
    assert seen[0]["units"] == "kilometers"
    # Valhalla takes named lat/lon keys — assert them, so a swap cannot hide in a pair.
    assert seen[0]["locations"][0] == {"lat": 36.4460502, "lon": 28.2280031}


def test_the_matrix_call_is_sources_to_targets_with_the_same_costing() -> None:
    """ADR-0020 allows exactly two endpoints; the matrix is one call, not N² routes."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=MATRIX_JSON)

    valhalla(handler).matrix(STOPS, costing=CAPTURED)
    assert len(seen) == 1
    assert seen[0].url.path == "/sources_to_targets"
    body = json.loads(seen[0].content)
    assert body["sources"] == body["targets"]
    assert body["costing_options"]["pedestrian"]["walking_speed"] == 4.5


def test_no_suitable_edges_is_a_named_no_route_not_a_generic_failure() -> None:
    """The real 400 body a graph that does not cover the area returns (the Andorra trap).

    It must be distinguishable from "the engine is down": the caller drops an unroutable
    stop (T021) but a `RoutingUnavailable` means retry the compile, not edit the plan.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error_code": 171,
                "error": "No suitable edges near location",
                "status_code": 400,
            },
        )

    with pytest.raises(NoRouteFound, match="171"):
        valhalla(handler).route(STOPS, costing=CAPTURED)


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(lambda request: httpx.Response(503, text="upstream down"), id="5xx"),
        pytest.param(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("connection refused")),
            id="transport",
        ),
    ],
)
def test_an_unreachable_engine_is_unavailable_not_a_route(handler: Any) -> None:
    with pytest.raises(RoutingUnavailable):
        valhalla(handler).route(STOPS, costing=CAPTURED)


# ── the pace band ─────────────────────────────────────────────────────────────


def test_walking_speed_has_no_default() -> None:
    """Mechanical, because the omission is the whole point: a default here reprices every
    feasibility verdict at somebody else's walking speed and no behavioural test notices."""
    speed = next(f for f in fields(PedestrianCosting) if f.name == "walking_speed_kmh")
    assert speed.default is MISSING and speed.default_factory is MISSING
    with pytest.raises(TypeError):
        PedestrianCosting()  # type: ignore[call-arg]


@pytest.mark.parametrize("kmh", [3.0, 5.1, 25.0])
def test_a_pace_outside_the_band_is_refused(kmh: float) -> None:
    """5.1 is in this list on purpose: Valhalla's own default is outside our band."""
    with pytest.raises(ValueError, match="pace band"):
        PedestrianCosting(walking_speed_kmh=kmh)


# ── the dev fallback ──────────────────────────────────────────────────────────


def _ors_directions() -> dict[str, Any]:
    """An ORS `foot-walking` GeoJSON reply, hand-written — see the module note in
    `commons/routing.py`: there is no captured ORS response, so this pins our request
    encoding and arithmetic, not the vendor's shape."""
    return {
        "features": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[28.228, 36.446], [28.2281, 36.4450], [28.2283, 36.4436]],
                },
                "properties": {
                    "way_points": [0, 2],
                    # ORS's own duration, at its own speed — deliberately not 400/pace.
                    "segments": [{"distance": 400.0, "duration": 288.0}],
                },
            }
        ]
    }


def test_the_fallback_honours_the_pace_and_names_its_own_engine() -> None:
    """ORS exposes no walking-speed parameter, so the pace must be applied by us or lost.

    Losing it is the failure ADR-0020 names, so duration is derived from the routed distance
    and the requested pace; ORS's own 288 s is dropped. And the leg says which engine ran:
    `valhalla:pedestrian` on a leg Valhalla never computed is a provenance claim, not a
    convention.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ors_directions())

    provider = OpenRouteServiceProvider(
        api_key="dev-key", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    (leg,) = provider.route(STOPS[:2], costing=PedestrianCosting(walking_speed_kmh=4.0))
    assert leg.distance_m == pytest.approx(400.0)
    assert leg.duration_s == 360  # 400 m at 4.0 km/h — not the 288 s ORS reported
    assert leg.source.id == "openrouteservice:foot-walking"
    assert leg.source.license == "ODbL-1.0"  # ORS routes over OSM too
    assert seen[0].headers["Authorization"] == "dev-key"
    assert json.loads(seen[0].content)["coordinates"][0] == [28.2280031, 36.4460502]


# ── selection ─────────────────────────────────────────────────────────────────


def test_the_provider_is_selected_by_env_and_never_defaults_to_a_recording() -> None:
    """An unset variable must not hand every area on earth the recorded Rhodes geometry."""
    assert isinstance(select_provider({}), ValhallaProvider)
    assert isinstance(select_provider({"SIYUR_ROUTING_PROVIDER": "fixture"}), FixtureProvider)
    ors = select_provider({"SIYUR_ROUTING_PROVIDER": "ors", "SIYUR_ORS_API_KEY": "k"})
    assert isinstance(ors, OpenRouteServiceProvider)
    assert select_provider({"SIYUR_VALHALLA_URL": "http://valhalla:8002"}) == ValhallaProvider(
        endpoint="http://valhalla:8002"
    )
    with pytest.raises(ValueError, match="unknown"):
        select_provider({"SIYUR_ROUTING_PROVIDER": "osrm"})
    with pytest.raises(ValueError, match="SIYUR_ORS_API_KEY"):
        select_provider({"SIYUR_ROUTING_PROVIDER": "ors"})


def test_all_three_implementations_satisfy_the_protocol() -> None:
    for provider in (
        ValhallaProvider(),
        OpenRouteServiceProvider(api_key="k"),
        FixtureProvider.from_dir(),
    ):
        assert isinstance(provider, RoutingProvider)
