"""T021 — the ``propose_itinerary`` node. Tier 1: a fake router, no API key, no network.

The node's whole claim is that the model contributes **an ordering of ids and nothing
else**, so every test here is written against the specific way that claim could be false and
still look true:

* an object or a number in the reply being *read* instead of refused (a model-asserted
  ``planned_start`` is the exact payload);
* an id the commons does not hold becoming a stop (the planner inventing a place);
* an unroutable stop being joined by a two-point line instead of dropped;
* a coordinate reaching the model in the request it was never supposed to see;
* an unusable reply degrading into an empty day, which walks zero metres in zero hours and
  is therefore *feasible* — a failure with a green tick on it.

The routing double implements :class:`~commons.routing.RoutingProvider` directly: the
Protocol is the contract, and the committed capture replays one three-waypoint walk which
is not the shape this node asks for (it routes consecutive **pairs**, so that it can name
the one stop a ``NoRouteFound`` is about).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any
from uuid import UUID, uuid4

import pytest
from shapely import LineString, Point

from commons.geo import Wgs84Point
from commons.llm import (
    DEFAULT_MAX_TOKENS,
    Completion,
    Effort,
    Message,
    TaskTier,
    ToolSpec,
    Usage,
)
from commons.models import Budgets, ItineraryV1, RouteLegV1, SiteRecordV1, SourcedValue, SourceRef
from commons.routing import (
    NoRouteFound,
    PedestrianCosting,
    RoutingProvider,
    TravelMatrix,
    Waypoint,
    routing_source,
)
from planner.nodes.propose_itinerary import (
    PROPOSAL_SYSTEM_PROMPT,
    Proposal,
    ProposalRefused,
    propose_itinerary,
)

DAY = date(2026, 8, 14)
DAY_START = time(10, 0)
OBSERVED = date(2026, 8, 1)
DWELL_MIN = 45
#: 300 s per leg ⇒ a 5-minute walk, so the schedule below is checkable by hand.
LEG_DURATION_S = 300
LEG_DISTANCE_M = 400.0
PACE = PedestrianCosting(walking_speed_kmh=4.2)
BUDGETS = Budgets(walking_m=4000.0, hours=6.0)

OSM = SourceRef(kind="osm", id="node/1", license="ODbL-1.0", attribution="© OpenStreetMap")
#: Three vertices, because a two-point line is a straight line pretending to be a route.
ROUTED_SHAPE = LineString([(28.2247, 36.4443), (28.2242, 36.4446), (28.2238, 36.4447)])


def site(name: str, *, lon: float, categories: tuple[str, ...] = ()) -> SiteRecordV1:
    def stamp(value: object) -> SourcedValue[Any]:
        return SourcedValue[Any].stamp(
            value=value, source=OSM, confidence=0.9, observed_at=OBSERVED
        )

    return SiteRecordV1(
        names={"en": stamp(name)},
        categories=tuple(stamp(category) for category in categories),
        location=SourcedValue[Wgs84Point].stamp(
            value=Point(lon, 36.4443), source=OSM, confidence=0.9, observed_at=OBSERVED
        ),
    )


@pytest.fixture
def candidates() -> tuple[SiteRecordV1, ...]:
    return (
        site("Upper Fortress", lon=28.2247, categories=("castle",)),
        site("Museum of the Citadel", lon=28.2251, categories=("museum",)),
        site("Harbour Promenade", lon=28.2255, categories=("waterfront",)),
        site("Parking P3", lon=28.2259, categories=("parking",)),
    )


@dataclass
class FakeRouter:
    """A local :class:`commons.llm.ModelRouter` — no provider, no key, no network."""

    reply: str = "[]"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self,
        tier: TaskTier,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_prefix: bool = False,
        long_run: bool = False,
        adaptive_thinking: bool = True,
        effort: Effort | None = None,
    ) -> Completion:
        self.calls.append(
            {
                "tier": tier,
                "system": system,
                "content": messages[-1].content,
                "cache_prefix": cache_prefix,
            }
        )
        return Completion(text=self.reply, usage=Usage(model="fake"))


@dataclass
class StubRouting:
    """A :class:`~commons.routing.RoutingProvider` double over consecutive pairs.

    ``no_route_on`` names the 0-based ``route()`` call that finds no walking path, which is
    how a test says "this particular stop is unreachable" without depending on coordinates.
    """

    no_route_on: frozenset[int] = frozenset()
    calls: list[tuple[Sequence[Waypoint], PedestrianCosting]] = field(default_factory=list)

    def route(
        self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting
    ) -> tuple[RouteLegV1, ...]:
        index = len(self.calls)
        self.calls.append((tuple(waypoints), costing))
        if index in self.no_route_on:
            raise NoRouteFound(f"no pedestrian route from waypoint {index} to waypoint {index + 1}")
        return (
            RouteLegV1(
                id="leg-0",
                from_stop=0,
                to_stop=1,
                geometry=ROUTED_SHAPE,
                distance_m=LEG_DISTANCE_M,
                duration_s=LEG_DURATION_S,
                source=routing_source(),
            ),
        )

    def matrix(self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting) -> TravelMatrix:
        raise AssertionError(
            "the node routes consecutive pairs so a NoRouteFound names one stop; it does not "
            "ask for an N×N matrix. If that changed, the exclusion logic changed with it."
        )


def propose(
    candidates: Sequence[SiteRecordV1],
    *,
    router: FakeRouter,
    routing: RoutingProvider,
    **overrides: Any,
) -> Proposal:
    return propose_itinerary(
        candidates,
        router=router,
        routing=routing,
        costing=PACE,
        user_id="test-subject",
        area_id=uuid4(),
        day=DAY,
        day_start=DAY_START,
        budgets=BUDGETS,
        dwell_min=DWELL_MIN,
        **overrides,
    )


def reply_of(*records: SiteRecordV1) -> str:
    return json.dumps([str(record.id) for record in records])


def site_ids(proposal: Proposal) -> list[UUID]:
    return [stop.site_id for stop in proposal.itinerary.stops]


# ── the happy path ──────────────────────────────────────────────────────────────────


def test_the_proposal_is_a_schema_valid_day_in_the_order_the_model_chose(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """Order is the model's; every number in the result is the machinery's.

    The stops carry the ids the model listed, in its order — but the legs are the routing
    seam's, relabelled to their position, and the clock is arithmetic over their durations:
    10:00 + 45 dwell → 10:45 walk → 10:50 → 11:35 walk → 11:40.
    """
    fortress, museum, promenade, _ = candidates
    router = FakeRouter(reply=reply_of(promenade, fortress, museum))

    proposal = propose(candidates, router=router, routing=StubRouting())
    itinerary = proposal.itinerary

    assert site_ids(proposal) == [promenade.id, fortress.id, museum.id]
    assert [stop.order for stop in itinerary.stops] == [0, 1, 2]
    assert [stop.planned_start for stop in itinerary.stops] == [
        time(10, 0),
        time(10, 50),
        time(11, 40),
    ]
    assert [(leg.id, leg.from_stop, leg.to_stop) for leg in itinerary.legs] == [
        ("leg-0", 0, 1),
        ("leg-1", 1, 2),
    ]
    assert [
        (entry.stop_order, entry.leg_id, entry.start, entry.duration_min)
        for entry in itinerary.timeline.entries
    ] == [
        (0, None, time(10, 0), 45),
        (None, "leg-0", time(10, 45), 5),
        (1, None, time(10, 50), 45),
        (None, "leg-1", time(11, 35), 5),
        (2, None, time(11, 40), 45),
    ]
    assert proposal.rejected == () and proposal.excluded == ()
    # A full round trip through the schema: nothing here is valid only because it was built
    # by the code that also validates it.
    assert ItineraryV1.model_validate(itinerary.model_dump(mode="json")) == itinerary


def test_the_model_is_asked_for_ids_and_never_shown_a_coordinate(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """FR-004 at the request boundary: there is nothing spatial in the payload to reason from."""
    router = FakeRouter(reply=reply_of(candidates[0]))

    propose(candidates, router=router, routing=StubRouting(), interests="old stonework")

    (call,) = router.calls
    assert call["tier"] is TaskTier.PLAN, "planning is the Opus tier (ADR-0004)"
    assert call["system"] == PROPOSAL_SYSTEM_PROMPT
    assert call["cache_prefix"] is True
    payload = json.loads(call["content"])
    assert payload["interests"] == "old stonework"
    assert {key for entry in payload["candidates"] for key in entry} == {
        "id",
        "names",
        "categories",
    }, "a location key here would hand the model the coordinates it must never emit"
    for record in candidates:
        longitude = record.location.value.x
        assert str(longitude) not in call["content"], f"{longitude} crossed the seam"


def test_the_pace_reaches_the_engine_explicitly_on_every_leg(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """``PedestrianCosting`` has no default speed; the node must not invent one."""
    routing = StubRouting()
    propose(candidates, router=FakeRouter(reply=reply_of(*candidates[:3])), routing=routing)

    assert [costing for _, costing in routing.calls] == [PACE, PACE]


# ── what the model may not do ───────────────────────────────────────────────────────


def test_a_site_id_the_commons_does_not_hold_is_rejected(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """FR-002: the planner may not invent a place, however confidently it is named."""
    invented = uuid4()
    router = FakeRouter(reply=json.dumps([str(candidates[0].id), str(invented)]))

    proposal = propose(candidates, router=router, routing=StubRouting())

    assert site_ids(proposal) == [candidates[0].id]
    assert len(proposal.rejected) == 1
    assert str(invented) in proposal.rejected[0]
    assert "may not invent a place" in proposal.rejected[0]


def test_a_model_asserted_numeric_is_refused_not_repaired(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """The exact payload FR-004 forbids: an object carrying a time and a duration.

    It is refused **unread** — the day keeps the one id that arrived as a string, and the
    stop it would have created never exists. Reading ``site_id`` out of the object and
    discarding the rest would be worse than accepting it: the numbers would be gone from the
    output and the fact that a model asserted them would be gone from the record.
    """
    fortress, museum, *_ = candidates
    router = FakeRouter(
        reply=json.dumps(
            [
                str(fortress.id),
                {"site_id": str(museum.id), "planned_start": "09:00", "dwell_min": 120},
                4200,
            ]
        )
    )

    proposal = propose(candidates, router=router, routing=StubRouting())

    assert site_ids(proposal) == [fortress.id], "only the id-shaped element became a stop"
    assert museum.id not in site_ids(proposal)
    assert [stop.dwell_min for stop in proposal.itinerary.stops] == [DWELL_MIN]
    assert proposal.itinerary.stops[0].planned_start == DAY_START
    assert len(proposal.rejected) == 2
    assert "refused a dict" in proposal.rejected[0] and "09:00" in proposal.rejected[0]
    assert "refused a int" in proposal.rejected[1]
    assert all("never a coordinate" in reason for reason in proposal.rejected)


def test_the_stop_cap_is_enforced_after_the_reply_not_merely_requested(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    router = FakeRouter(reply=reply_of(*candidates))

    proposal = propose(candidates, router=router, routing=StubRouting(), max_stops=2)

    assert len(proposal.itinerary.stops) == 2
    assert len(proposal.rejected) == 2
    assert all("capped at 2 stops" in reason for reason in proposal.rejected)


def test_a_repeated_id_is_refused_because_a_day_visits_a_place_once(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    fortress = candidates[0]
    router = FakeRouter(reply=reply_of(fortress, candidates[1], fortress))

    proposal = propose(candidates, router=router, routing=StubRouting())

    assert site_ids(proposal) == [fortress.id, candidates[1].id]
    assert "duplicate site id" in proposal.rejected[0]


# ── routing: an unroutable stop is dropped, never straight-lined ────────────────────


def test_an_unroutable_stop_is_excluded_never_straight_lined(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """FR-003. The day closes over the gap and says which place it lost.

    The first pair finds no path, so the *second* chosen place is dropped and the third is
    routed from the first. Nothing in the result is a two-point line: offline there is no
    way to tell a fabricated leg from a routed one, so there must not be one.
    """
    fortress, museum, promenade, _ = candidates
    router = FakeRouter(reply=reply_of(fortress, museum, promenade))

    proposal = propose(candidates, router=router, routing=StubRouting(no_route_on=frozenset({0})))

    assert site_ids(proposal) == [fortress.id, promenade.id]
    assert [(dropped.site_id, dropped.reason) for dropped in proposal.excluded] == [
        (museum.id, "unroutable")
    ]
    assert "waypoint 0 to waypoint 1" in proposal.excluded[0].detail
    assert len(proposal.itinerary.legs) == len(proposal.itinerary.stops) - 1
    for leg in proposal.itinerary.legs:
        assert len(leg.geometry.coords) >= 3, "a 2-point leg is a straight line, not a route"


# ── refusing, and refusing to pad ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply",
    ["[]", "   ", "here is a lovely day out", '{"stops": []}', '["not-a-uuid"]'],
    ids=["empty-array", "blank", "prose", "object", "no-usable-id"],
)
def test_a_reply_with_nothing_usable_refuses_rather_than_shipping_an_empty_day(
    candidates: tuple[SiteRecordV1, ...], reply: str
) -> None:
    """An empty day walks zero metres in zero hours, so it is *feasible* — and wrong.

    Returning one would put a green feasibility tick on a failed model call and let it
    through the approval gate into a bundle of nothing.
    """
    with pytest.raises(ProposalRefused):
        propose(candidates, router=FakeRouter(reply=reply), routing=StubRouting())


def test_an_area_with_no_candidates_is_an_honest_empty_plan_never_a_model_call() -> None:
    """ "Not enough here" is a finding about the area, and never padding (`contracts/plans.md`)."""
    router = FakeRouter(reply=reply_of())

    proposal = propose((), router=router, routing=StubRouting())

    assert proposal.itinerary.stops == () and proposal.itinerary.legs == ()
    assert proposal.rejected == ("the commons holds no candidate places for this area",)
    assert router.calls == [], "there is nothing for a model to order"


def test_the_double_satisfies_the_routing_protocol() -> None:
    """The double is checked against the seam, so the tests cannot drift from the contract."""
    assert isinstance(StubRouting(), RoutingProvider)
