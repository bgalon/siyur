"""T023 — the plan pipeline. Tier 1: a fake router, a stub engine, no key, no network, no DB.

The frame sequence of `specs/002-plan-compile-offline/contracts/plans.md` is the contract
under test, but the tests are written against the ways this pipeline could stream that
sequence and still be wrong:

* an **empty day reaching ``feasibility: ok``** — zero stops walk zero metres in zero hours,
  so the arithmetic says feasible and the approval gate would wave it through
  (:mod:`planner.feasibility` names this hazard and hands it here);
* a **refusal degrading into an empty success** — "the model answered nothing usable" and
  "there is nothing here to plan" are different facts and must not share a representation;
* the **verdict never reaching the row** — ``ItineraryV1`` is ``extra="forbid"`` and the
  verdict lives on ``user_plan`` (ADR-0025 ruling 3), so this pipeline is the only thing
  carrying it there. A stream that shows violations the row does not hold would let an
  infeasible plan through a gate that reads the row;
* a frame **claiming a write that has not happened**, which is the same defect
  :func:`~planner.pipeline.run_research` avoids by persisting before it streams a ``site``.

The router and routing doubles are the ones ``tests/test_planner_propose.py`` already
builds — reused rather than re-mocked, so a change to either is felt in one place. The
``site`` helper is local because neither existing one fits: the propose tests' record has
names and categories but no hours, and the feasibility tests' has hours but no names, and
this pipeline runs both stages over the *same* record.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any
from uuid import UUID, uuid4

import pytest
from shapely import Point

from commons import repository
from commons.geo import Wgs84Point
from commons.models import Budgets, ItineraryV1, SiteRecordV1, SourcedValue, SourceRef
from planner.nodes.propose_itinerary import ProposalRefused
from planner.pipeline import (
    EMPTY_DAY_VIOLATION,
    PLAN_PHASES,
    Event,
    PlanNotRecorded,
    PlanRequest,
    PlanRow,
    run_plan,
)
from tests.test_planner_propose import (
    LEG_DISTANCE_M,
    PACE,
    FakeRouter,
    StubRouting,
)

#: A Friday — so ``Tu …`` is closed and ``Mo-Su …`` is open, with no reliance on "today".
DAY = date(2026, 8, 14)
DAY_START = time(10, 0)
OBSERVED = date(2026, 8, 1)
DWELL_MIN = 45
#: The area's own frame. Test data, not a hardcoded place: nothing in `planner/` branches
#: on either value — they are handed to `commons.opening_hours` and nowhere else.
TIMEZONE = "Europe/Athens"
COUNTRY = "GR"

OSM = SourceRef(kind="osm", id="node/1", license="ODbL-1.0", attribution="© OpenStreetMap")

OPEN_ALWAYS = "Mo-Su 00:00-24:00"
#: Closed on a Friday — the named opening-window violation.
TUESDAYS_ONLY = "Tu 09:00-14:00"


def site(name: str, *, lon: float, hours: str | None = OPEN_ALWAYS) -> SiteRecordV1:
    """A commons record carrying what *both* stages read: names, categories, point, hours."""

    def stamp(value: object) -> SourcedValue[Any]:
        return SourcedValue[Any].stamp(
            value=value, source=OSM, confidence=0.9, observed_at=OBSERVED
        )

    return SiteRecordV1(
        names={"en": stamp(name)},
        categories=(stamp("museum"),),
        location=SourcedValue[Wgs84Point].stamp(
            value=Point(lon, 36.4443), source=OSM, confidence=0.9, observed_at=OBSERVED
        ),
        opening_hours=stamp(hours) if hours is not None else None,
    )


@dataclass(frozen=True, slots=True)
class StubRow:
    """The ``user_plan`` row as the pipeline sees it — id and state, nothing else."""

    id: UUID
    status: str


@dataclass
class RecordingStore:
    """A ``user_plan`` stand-in: the two states the row really has, backed by two lists.

    ``vanish`` is the row disappearing between the insert and the verdict — a concurrent
    supersede, approve or delete. The real ``record_feasibility`` reports that as a
    zero-row ``UPDATE``, i.e. ``None``.
    """

    vanish: bool = False
    created: list[ItineraryV1] = field(default_factory=list)
    #: ``(plan_id, feasible, violations, warnings)`` — the two severities recorded as the two
    #: arguments they arrive as, so a test can prove the pipeline did not merge them.
    recorded: list[tuple[UUID, bool, tuple[str, ...], tuple[str, ...]]] = field(
        default_factory=list
    )

    def create(self, itinerary: ItineraryV1) -> StubRow:
        self.created.append(itinerary)
        return StubRow(id=itinerary.id, status="proposing")

    def record_feasibility(
        self,
        plan_id: UUID,
        *,
        feasible: bool,
        violations: Sequence[str],
        warnings: Sequence[str],
    ) -> StubRow | None:
        self.recorded.append((plan_id, feasible, tuple(violations), tuple(warnings)))
        return None if self.vanish else StubRow(id=plan_id, status="proposed")


@dataclass
class RecordingLoader:
    """The commons read, and a witness that it is not touched before the stream is pulled."""

    sites: tuple[SiteRecordV1, ...] = ()
    calls: list[UUID] = field(default_factory=list)

    def __call__(self, area_id: UUID) -> tuple[SiteRecordV1, ...]:
        self.calls.append(area_id)
        return self.sites


AREA_ID = uuid4()


def request_for(**overrides: Any) -> PlanRequest:
    defaults: dict[str, Any] = {
        "area_id": AREA_ID,
        "user_id": "test-subject",
        "budgets": Budgets(walking_m=4000.0, hours=6.0),
        "day": DAY,
        "day_start": DAY_START,
        "timezone": TIMEZONE,
        "country_code": COUNTRY,
    }
    return PlanRequest(**{**defaults, **overrides})


def reply_of(*records: SiteRecordV1) -> str:
    return json.dumps([str(record.id) for record in records])


def plan_stream(
    *,
    sites: Sequence[SiteRecordV1],
    reply: str | None = None,
    store: RecordingStore | None = None,
    loader: RecordingLoader | None = None,
    routing: StubRouting | None = None,
    router: FakeRouter | None = None,
    request: PlanRequest | None = None,
) -> Any:
    loader = loader or RecordingLoader(sites=tuple(sites))
    return run_plan(
        request or request_for(),
        load_sites=loader,
        store=store or RecordingStore(),
        router=router or FakeRouter(reply=reply if reply is not None else reply_of(*sites)),
        routing=routing or StubRouting(),
        costing=PACE,
        dwell_min=DWELL_MIN,
    )


def drain(**kwargs: Any) -> list[Event]:
    return list(plan_stream(**kwargs))


def phases_of(events: Sequence[Event]) -> list[str]:
    return [str(e.data["phase"]) for e in events if e.event == "status"]


def frame(events: Sequence[Event], name: str) -> Event:
    return next(e for e in events if e.event == name)


@pytest.fixture
def candidates() -> tuple[SiteRecordV1, ...]:
    return (
        site("Upper Fortress", lon=28.2247),
        site("Museum of the Citadel", lon=28.2251),
        site("Harbour Promenade", lon=28.2255),
    )


@pytest.fixture
def feasible_day(candidates: tuple[SiteRecordV1, ...]) -> list[Event]:
    return drain(sites=candidates)


# ── the frame sequence (contracts/plans.md) ─────────────────────────────────────────


def test_frames_arrive_in_the_contract_order(feasible_day: list[Event]) -> None:
    assert [event.event for event in feasible_day] == [
        "status",
        "status",
        "status",
        "itinerary",
        "feasibility",
        "done",
    ]


def test_the_phase_sequence_is_the_contract_trajectory(feasible_day: list[Event]) -> None:
    assert phases_of(feasible_day) == list(PLAN_PHASES)


def test_the_load_sites_frame_counts_the_commons_it_actually_read(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    loader = RecordingLoader(sites=candidates)
    events = drain(sites=candidates, loader=loader)
    first = events[0]
    assert first.data["phase"] == "load_sites"
    assert first.data["area_id"] == str(AREA_ID)
    assert first.data["candidates"] == len(candidates)
    assert loader.calls == [AREA_ID], "the loader is asked for the area the frame names"


def test_the_propose_frame_names_the_tier_and_never_a_model(feasible_day: list[Event]) -> None:
    """ADR-0004: no call site names a model, so the frame reports the *tier* it routed on."""
    proposed = next(e for e in feasible_day if e.data.get("phase") == "propose_itinerary")
    assert proposed.data["tier"] == "plan"
    assert proposed.data["stops"] == 3


def test_the_route_frame_reads_its_engine_off_the_leg_stamp(feasible_day: list[Event]) -> None:
    """The provider is provenance, not a literal — `routing_source` writes `<engine>:<costing>`."""
    routed = next(e for e in feasible_day if e.data.get("phase") == "route")
    assert routed.data["provider"] == "valhalla"
    assert routed.data["legs"] == 2
    assert routed.data["excluded"] == []


def test_the_done_frame_names_the_row_the_stream_actually_wrote(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    store = RecordingStore()
    events = drain(sites=candidates, store=store)
    done = frame(events, "done")
    assert done.data["plan_id"] == str(store.created[0].id)
    assert done.data["state"] == "proposed"


# ── the itinerary that is streamed is the itinerary that was planned ────────────────


def test_the_streamed_itinerary_is_schema_valid_and_on_the_area_local_clock(
    feasible_day: list[Event],
) -> None:
    """Two clocks: the plan's own content is area-local wall clock, start to finish."""
    payload = dict(frame(feasible_day, "itinerary").data)
    itinerary = ItineraryV1.model_validate(payload)
    assert itinerary.schema_ver == "ItineraryV1"
    assert itinerary.date == DAY, "the requested calendar day, never a server today()"
    assert itinerary.stops[0].planned_start == DAY_START
    # 45 min dwell + a 5-minute leg ⇒ the second stop starts at 10:50, area-local.
    assert itinerary.stops[1].planned_start == time(10, 50)


def test_every_streamed_leg_carries_its_odbl_stamp(feasible_day: list[Event]) -> None:
    """Routing over OSM is a Produced Work — attribution renders wherever a leg does."""
    itinerary = ItineraryV1.model_validate(dict(frame(feasible_day, "itinerary").data))
    assert itinerary.legs
    for leg in itinerary.legs:
        assert leg.source.license == "ODbL-1.0"
        assert leg.source.attribution == "© OpenStreetMap contributors"


def test_every_stop_references_a_commons_record(
    feasible_day: list[Event], candidates: tuple[SiteRecordV1, ...]
) -> None:
    """FR-002 — the planner invents no place, so no stop can name one."""
    itinerary = ItineraryV1.model_validate(dict(frame(feasible_day, "itinerary").data))
    known = {record.id for record in candidates}
    assert [stop.site_id for stop in itinerary.stops]
    assert all(stop.site_id in known for stop in itinerary.stops)


# ── the verdict, and its journey to the row ─────────────────────────────────────────


def test_a_day_inside_its_budgets_and_windows_streams_ok(feasible_day: list[Event]) -> None:
    verdict = frame(feasible_day, "feasibility")
    assert verdict.data["ok"] is True
    assert verdict.data["violations"] == []
    assert verdict.data["warnings"] == [], "these fixtures are all tagged Mo-Su 00:00-24:00"


def test_stops_whose_hours_cannot_be_checked_warn_on_the_wire_and_still_stream_ok() -> None:
    """ADR-0022 as amended 2026-08-14, at the frame the client actually reads.

    Untagged places are the common case, not the exotic one — most OSM/Overture records carry
    no ``opening_hours`` at all — so this is what a real day looks like on the wire: ``ok``
    true, ``violations`` empty, and one ``warnings`` entry **per stop** so the traveller is
    told which places to check rather than being handed a single "some hours are unknown".
    """
    untagged = (
        site("Upper Fortress", lon=28.2247, hours=None),
        site("Museum of the Citadel", lon=28.2251, hours=None),
    )
    store = RecordingStore()
    events = drain(sites=untagged, store=store)

    verdict = frame(events, "feasibility")
    assert verdict.data["ok"] is True, "no real day would ever be approvable otherwise"
    assert verdict.data["violations"] == []
    warnings = list(verdict.data["warnings"])
    assert len(warnings) == len(untagged)
    assert all("no_expression" in message for message in warnings)
    assert [message.split()[1] for message in warnings] == ["0", "1"], "named per stop"

    # …and the row holds the same split, because the gate and the panel both read the row.
    _, feasible, recorded_violations, recorded_warnings = store.recorded[0]
    assert feasible is True
    assert recorded_violations == ()
    assert list(recorded_warnings) == warnings


def test_an_infeasible_day_still_streams_its_itinerary_then_names_every_violation() -> None:
    """FR-005 — the plan is shown, and the specific budget and window breached are named."""
    sites = (
        site("Upper Fortress", lon=28.2247),
        site("Closed Museum", lon=28.2251, hours=TUESDAYS_ONLY),
        site("Harbour Promenade", lon=28.2255),
    )
    store = RecordingStore()
    events = drain(
        sites=sites,
        store=store,
        request=request_for(budgets=Budgets(walking_m=500.0, hours=6.0)),
    )

    names = [event.event for event in events]
    assert names.index("itinerary") < names.index("feasibility")

    violations = list(frame(events, "feasibility").data["violations"])
    assert frame(events, "feasibility").data["ok"] is False
    # Two legs of 400 m each against a 500 m budget — the named breach, with both numbers.
    assert f"walking_m {2 * LEG_DISTANCE_M:.0f} > budget 500" in violations
    # The window breach names *which* stop and *when* — the wording is `planner.feasibility`'s
    # to choose, but a violation that does not identify its stop is not a named violation.
    assert any("stop 1" in message and "closed at 10:50" in message for message in violations)

    # …and the row holds exactly what the stream showed, because the gate reads the row.
    plan_id, feasible, recorded, recorded_warnings = store.recorded[0]
    assert plan_id == store.created[0].id
    assert feasible is False
    assert list(recorded) == violations
    assert recorded_warnings == (), "these stops are all tagged, so nothing is unknown"


def test_the_verdict_reaches_the_row_and_is_never_attached_to_the_plan(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """ADR-0025 ruling 3 — `feasible`/`violations` are row state; `ItineraryV1` forbids extras."""
    store = RecordingStore()
    events = drain(sites=candidates, store=store)
    assert store.recorded == [(store.created[0].id, True, (), ())]
    payload = dict(frame(events, "itinerary").data)
    assert "feasible" not in payload
    assert "violations" not in payload
    assert "feasibility" not in payload


def test_a_verdict_that_matches_no_row_stops_the_stream_instead_of_claiming_it_landed(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """A superseded row must not be reported back as this plan, feasible and proposed."""
    stream = plan_stream(sites=candidates, store=RecordingStore(vanish=True))
    events: list[Event] = []
    with pytest.raises(PlanNotRecorded):
        for event in stream:
            events.append(event)
    assert [e.event for e in events] == ["status", "status", "status", "itinerary"]


# ── the two ways a day can be empty, which are not the same way ─────────────────────


def test_an_area_with_no_candidates_is_an_honest_empty_plan_that_cannot_be_approved() -> None:
    """The hazard `planner.feasibility` names: zero stops is arithmetically feasible.

    The stream is honest — the day is proposed, streamed and persisted — but the verdict is
    `ok=false` with the empty-day violation, and the row is written `feasible=False`, which
    is what `approve_plan`'s `feasible IS TRUE` predicate and the post-approval CHECK act on.
    """
    store = RecordingStore()
    router = FakeRouter()
    events = drain(sites=(), store=store, router=router)

    assert events[0].data["candidates"] == 0
    itinerary = ItineraryV1.model_validate(dict(frame(events, "itinerary").data))
    assert itinerary.stops == ()
    assert router.calls == [], "an empty area is not dressed up with a model call"

    verdict = frame(events, "feasibility")
    assert verdict.data["ok"] is False
    assert verdict.data["violations"] == [EMPTY_DAY_VIOLATION]
    assert verdict.data["warnings"] == [], "an absent day is a refusal, never an advisory"
    assert store.recorded == [(store.created[0].id, False, (EMPTY_DAY_VIOLATION,), ())]


def test_a_model_that_answers_nothing_usable_raises_and_writes_no_row(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """A refusal is not an empty success — and it leaves nothing behind to approve."""
    store = RecordingStore()
    stream = plan_stream(sites=candidates, reply="[]", store=store)

    first = next(stream)
    assert first.data["phase"] == "load_sites"
    with pytest.raises(ProposalRefused):
        next(stream)

    assert store.created == [], "a refused proposal must not leave a durable plan row"
    assert store.recorded == []


# ── the model ranks, and does nothing else ──────────────────────────────────────────


def test_a_model_asserted_numeric_is_refused_and_the_refusal_is_streamed(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """FR-004 — an object is the shape a `planned_start` would arrive in. It costs a stop."""
    smuggled = json.dumps(
        [str(candidates[0].id), {"id": str(candidates[1].id), "planned_start": "10:00"}]
    )
    events = drain(sites=candidates, reply=smuggled)

    proposed = next(e for e in events if e.data.get("phase") == "propose_itinerary")
    assert proposed.data["stops"] == 1, "the smuggled object is refused, not repaired"
    assert any("planned_start" in reason for reason in proposed.data["rejected"])

    itinerary = ItineraryV1.model_validate(dict(frame(events, "itinerary").data))
    assert [stop.site_id for stop in itinerary.stops] == [candidates[0].id]


def test_an_unroutable_stop_is_named_in_the_route_frame_and_dropped(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """FR-003 — never a straight line pretending to be a route; the day closes over the gap."""
    events = drain(sites=candidates, routing=StubRouting(no_route_on=frozenset({0})))

    routed = next(e for e in events if e.data.get("phase") == "route")
    assert routed.data["excluded"] == [{"site_id": str(candidates[1].id), "reason": "unroutable"}]
    itinerary = ItineraryV1.model_validate(dict(frame(events, "itinerary").data))
    assert [stop.site_id for stop in itinerary.stops] == [candidates[0].id, candidates[2].id]
    assert len(itinerary.legs) == 1


# ── streaming, and the write that precedes the frame claiming it ────────────────────


def test_nothing_is_read_or_written_until_the_stream_is_pulled(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """A generator, not a list built and then yielded."""
    loader = RecordingLoader(sites=candidates)
    store = RecordingStore()
    stream = plan_stream(sites=candidates, loader=loader, store=store)

    assert loader.calls == []
    next(stream)
    assert loader.calls == [AREA_ID]
    assert store.created == [], "the plan is not written before it is planned"


def test_the_row_exists_before_the_itinerary_frame_claims_it(
    candidates: tuple[SiteRecordV1, ...],
) -> None:
    """The `run_research` rule applied to a plan: never show what a failed write dropped."""
    store = RecordingStore()
    seen: list[str] = []
    for event in plan_stream(sites=candidates, store=store):
        if event.event == "itinerary":
            assert store.created, "an itinerary was streamed before the row was inserted"
        if event.event == "feasibility":
            assert store.recorded, "a verdict was streamed before it reached the row"
        seen.append(event.event)
    assert seen[-1] == "done"


# ── the seam the API will plug into ─────────────────────────────────────────────────


def test_the_repository_row_satisfies_the_pipeline_protocol() -> None:
    """`commons.repository.PlanRecord` is what `api/plans.py` hands back — mypy is the check."""
    itinerary = ItineraryV1(
        user_id="test-subject",
        area_id=AREA_ID,
        date=DAY,
        lang="en",
        budgets=Budgets(walking_m=4000.0, hours=6.0),
    )
    record = repository.PlanRecord(
        id=itinerary.id,
        user_id=itinerary.user_id,
        area_id=itinerary.area_id,
        itinerary=itinerary,
        status="proposed",
        revision=1,
        superseded_by=None,
        approved_at=None,
        approved_by=None,
        itinerary_hash=itinerary.canonical_sha256(),
        feasible=False,
        violations=(),
        warnings=(),
        feasibility_checked_at=None,
    )
    row: PlanRow = record
    assert row.id == itinerary.id
    assert row.status == "proposed"
