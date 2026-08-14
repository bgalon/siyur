"""T025 — the three plan endpoints against the contract (`contracts/plans.md`).

Two tiers in one file, the way `test_api_areas.py` splits them, because the contract spans
both:

- **Tier 1** — auth and the request boundary. The app is given a session factory bound to an
  engine pointing at a dead port, so a request that answers ``401``/``422`` *proves* it never
  reached storage by not hanging. The ``day_start`` rule lives here on purpose: it is the one
  ``422`` that has to be decided **before** the stream opens, since a tz-aware ``day_start``
  raises from inside ``propose_itinerary``, three frames after the ``200`` was sent.
- **Tier 2** (``@pytest.mark.integration``) — the whole gate over real PostGIS: the stream and
  the durable row it writes, the seven states exposed verbatim, the privacy boundary, and
  every one of the four refusal codes.

`test_planner_pipeline.py` already pins the *generator's* frame contract offline. What is
pinned here is everything the transport and the gate add: the status-code matrix, that another
subject's plan is **byte-identical** to a missing one, that a second approve returns the same
``approved_at``, and that the ``error`` strings on the wire are
:data:`commons.repository.RefusalReason`'s own spellings rather than a second vocabulary.

The model seam is a :class:`~tests.test_planner_propose.FakeRouter` and the routing seam a
:class:`~tests.test_planner_propose.StubRouting`, both injected through ``app.state``: no test
here opens a socket, needs an API key, or builds a routing graph. The doubles are the ones the
node tests already own, reused rather than re-mocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import box
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from api.plans import DEFAULT_PACE_KMH, _claim, _release
from commons.db import SRID, Area, UserPlan
from commons.merge import merge_records
from commons.models import ItineraryV1, SiteRecordV1
from commons.repository import (
    RefusalReason,
    claim_plan_for_compile,
    finish_compile,
    load_plan,
    supersede_plan,
    upsert_sites,
)
from commons.routing import MAX_WALKING_SPEED_KMH, MIN_WALKING_SPEED_KMH
from tests.test_api_areas import build_app, parse_sse, signed_in_client
from tests.test_planner_pipeline import (
    COUNTRY,
    DAY,
    DAY_START,
    OPEN_ALWAYS,
    TIMEZONE,
    TUESDAYS_ONLY,
    site,
)
from tests.test_planner_propose import LEG_DISTANCE_M, FakeRouter, StubRouting

Frames = list[tuple[str, dict[str, Any]]]

#: The area every Tier-2 test plans over — a box around the fixture sites' own coordinates.
#: A *test* geography, never a default: nothing in `api/plans.py` branches on either.
AREA_BOX = box(28.20, 36.43, 28.25, 36.46)
#: Somewhere the commons holds nothing — the honest zero-stop day (SC-006).
EMPTY_BOX = box(0.0, 15.0, 0.01, 15.01)

#: A budget the two-leg fixture day fits inside, and one it does not.
GENEROUS = {"walking_m": 4000.0, "hours": 6.0}
TIGHT = {"walking_m": 100.0, "hours": 6.0}


# ── harness ───────────────────────────────────────────────────────────────────────


def propose_body(area_id: str | UUID, **overrides: Any) -> dict[str, Any]:
    """The contract's request body, with every field stated (nothing is defaulted here)."""
    body: dict[str, Any] = {
        "area_id": str(area_id),
        "budgets": dict(GENEROUS),
        "interests": "old stonework",
        "date": DAY.isoformat(),
        "day_start": DAY_START.isoformat(),
        "lang": "en",
    }
    return {**body, **overrides}


def plan_app(
    engine: Engine | None = None,
    *,
    reply: str = "[]",
    routing: StubRouting | None = None,
    **state: Any,
) -> FastAPI:
    """The real app with the model and routing seams pointed at the node tests' doubles."""
    return build_app(
        engine=engine,
        model_router=FakeRouter(reply=reply),
        routing_provider=routing if routing is not None else StubRouting(),
        **state,
    )


def seed_area(
    session: Session,
    subject: str,
    *,
    polygon: Any = AREA_BOX,
    timezone: str | None = TIMEZONE,
    country_code: str | None = COUNTRY,
) -> UUID:
    """An ``area`` row **with its local frame**, written directly.

    Deliberately not through ``POST /areas``: that endpoint resolves and stores a polygon but
    does not yet write ``timezone``/``country_code``, and a plan over a frameless area is
    ``hours_unknown`` at every stop by design (`planner/feasibility.py`). Seeding the row here
    keeps this suite testing the *plan* endpoints rather than that gap — and makes the frame an
    explicit input, so the ``None`` case can be asked for on purpose.
    """
    area = Area(
        polygon=from_shape(polygon, srid=SRID),
        name=None,
        created_by=subject,
        timezone=timezone,
        country_code=country_code,
    )
    session.add(area)
    session.commit()
    return area.id


def seed_sites(session: Session, records: tuple[SiteRecordV1, ...]) -> tuple[SiteRecordV1, ...]:
    """Land records in the commons through the real merge + repository path."""
    upsert_sites(session, merge_records(records))
    session.commit()
    return records


def reply_of(*records: SiteRecordV1) -> str:
    """What the model is allowed to say: an ordered array of candidate ids, nothing else."""
    return json.dumps([str(record.id) for record in records])


def named(frames: Frames, event: str) -> list[dict[str, Any]]:
    return [payload for name, payload in frames if name == event]


def stream_plan(client: TestClient, body: dict[str, Any]) -> Frames:
    response = client.post("/plans", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def plan_id_of(frames: Frames) -> str:
    (done,) = named(frames, "done")
    plan_id: str = done["plan_id"]
    return plan_id


# ── Tier 1: auth and the request boundary (no database) ───────────────────────────


@pytest.fixture
def offline_client() -> TestClient:
    """A signed-in client over an engine that resolves but never connects."""
    return signed_in_client(plan_app())


def test_every_plan_endpoint_is_401_unauthenticated() -> None:
    client = TestClient(plan_app())
    plan_id = uuid4()
    assert client.post("/plans", json=propose_body(uuid4())).status_code == 401
    assert client.get(f"/plans/{plan_id}").status_code == 401
    assert client.post(f"/plans/{plan_id}/approve").status_code == 401


@pytest.mark.parametrize(
    "day_start",
    ["10:00Z", "10:00+00:00", "10:00+03:00", 36000],
    ids=["utc-suffix", "utc-offset", "positive-offset", "seconds-since-midnight"],
)
def test_a_day_start_carrying_a_utc_offset_is_422(
    offline_client: TestClient, day_start: Any
) -> None:
    """The anchor of the whole day must be a **naive area-local** wall clock.

    pydantic coerces every value here into a tz-aware ``time`` (the integer becomes
    ``time(10, 0, tzinfo=UTC)``), and downstream ``datetime.combine`` adopts the offset while
    ``.time()`` drops it again — so a ``10:00Z`` for an area at UTC+3 would anchor every
    ``planned_start`` and every opening-window check three hours early, with nothing left to
    refuse it. It has to be a ``422`` here, before the ``200`` is sent, because
    ``propose_itinerary`` raises three frames into a stream that already has a status code.
    """
    response = offline_client.post("/plans", json=propose_body(uuid4(), day_start=day_start))
    assert response.status_code == 422
    assert "day_start" in response.text


def test_a_naive_day_start_passes_the_boundary(offline_client: TestClient) -> None:
    """The control: the same request with an offset-free anchor is *not* refused at ``422``.

    It reaches storage and fails there (the dead engine), which is the proof that the
    ``422`` above is the ``day_start`` rule and not the request being rejected wholesale.
    """
    with pytest.raises(Exception, match="(?i)connect|operational"):
        offline_client.post("/plans", json=propose_body(uuid4(), day_start="10:00"))


@pytest.mark.parametrize(
    "budgets",
    [
        {"walking_m": 0, "hours": 4.0},
        {"walking_m": -1.0, "hours": 4.0},
        {"walking_m": 4000.0, "hours": 0},
        {"walking_m": 4000.0, "hours": -0.5},
        {"hours": 4.0},
        {"walking_m": 4000.0},
    ],
    ids=["zero-walk", "negative-walk", "zero-hours", "negative-hours", "no-walk", "no-hours"],
)
def test_a_non_positive_or_missing_budget_is_422(
    offline_client: TestClient, budgets: dict[str, Any]
) -> None:
    """A zero budget is not a tight day; it is a day whose every plan violates it."""
    response = offline_client.post("/plans", json=propose_body(uuid4(), budgets=budgets))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "overrides",
    [
        {"budgets": None},
        {"day_start": "half past ten"},
        {"date": "not-a-date"},
        {"area_id": "not-a-uuid"},
    ],
    ids=["no-budgets", "unparseable-day-start", "unparseable-date", "unparseable-area-id"],
)
def test_an_unparseable_request_is_422(
    offline_client: TestClient, overrides: dict[str, Any]
) -> None:
    body = {**propose_body(uuid4()), **overrides}
    assert offline_client.post("/plans", json=body).status_code == 422


def test_the_date_is_required_and_never_defaulted_from_the_server_clock(
    offline_client: TestClient,
) -> None:
    """ADR-0025 ruling 2 — a UTC ``today()`` plans Sunday for a Monday in Auckland."""
    body = propose_body(uuid4())
    del body["date"]
    assert offline_client.post("/plans", json=body).status_code == 422


# ── Tier 2: the gate over real PostGIS ────────────────────────────────────────────

integration = pytest.mark.integration


@pytest.fixture
def sites(db_session: Session) -> tuple[SiteRecordV1, ...]:
    """Three commons records inside :data:`AREA_BOX`, open every day of the week."""
    return seed_sites(
        db_session,
        (
            site("Upper Fortress", lon=28.2247),
            site("Museum of the Citadel", lon=28.2251),
            site("Harbour Promenade", lon=28.2255),
        ),
    )


@pytest.fixture
def area_id(db_session: Session) -> UUID:
    return seed_area(db_session, "google-sub-a")


@pytest.fixture
def app(db_engine: Engine, db_session: Session, sites: tuple[SiteRecordV1, ...]) -> FastAPI:
    """The app over the migrated database, the model replying with all three site ids."""
    return plan_app(engine=db_engine, reply=reply_of(*sites))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return signed_in_client(app)


@pytest.fixture
def proposed(client: TestClient, area_id: UUID) -> Frames:
    return stream_plan(client, propose_body(area_id))


# --- POST /plans: the stream ---------------------------------------------------


@integration
def test_the_contract_frame_sequence_survives_sse_framing(proposed: Frames) -> None:
    assert [name for name, _ in proposed] == [
        "status",
        "status",
        "status",
        "itinerary",
        "feasibility",
        "done",
    ]
    phases = [payload["phase"] for name, payload in proposed if name == "status"]
    assert phases == ["load_sites", "propose_itinerary", "route"]
    assert "error" not in [name for name, _ in proposed]


@integration
def test_the_stream_proposes_a_day_from_the_commons_and_pauses_it_proposed(
    proposed: Frames, sites: tuple[SiteRecordV1, ...]
) -> None:
    (frame,) = named(proposed, "itinerary")
    itinerary = ItineraryV1.model_validate(frame)
    known = {record.id for record in sites}
    assert [stop.site_id for stop in itinerary.stops] == [record.id for record in sites]
    assert all(stop.site_id in known for stop in itinerary.stops), "FR-002: no invented place"
    assert itinerary.date == DAY, "the requested calendar day, never a server today()"
    assert itinerary.stops[0].planned_start == DAY_START

    (done,) = named(proposed, "done")
    assert done["state"] == "proposed"
    (verdict,) = named(proposed, "feasibility")
    assert verdict == {"ok": True, "violations": []}


@integration
def test_the_proposal_is_a_durable_row_that_outlives_the_request(
    proposed: Frames, db_session: Session
) -> None:
    """SC-003 — the HITL pause is a row, not a suspended coroutine, so it survives a restart."""
    plan_id = plan_id_of(proposed)
    # A session that has no idea a request ever happened.
    stored = load_plan(db_session, UUID(plan_id), user_id="google-sub-a")
    assert stored is not None
    assert stored.status == "proposed" and stored.feasible is True


@integration
def test_the_pace_every_leg_is_priced_at_is_stated_never_the_engine_default(
    db_engine: Engine, db_session: Session, sites: tuple[SiteRecordV1, ...], area_id: UUID
) -> None:
    """ADR-0020 — a defaulted pace silently reprices every leg, so the API names one."""
    routing = StubRouting()
    client = signed_in_client(plan_app(engine=db_engine, reply=reply_of(*sites), routing=routing))
    stream_plan(client, propose_body(area_id))

    assert routing.calls, "the day was never routed"
    paces = {costing.walking_speed_kmh for _, costing in routing.calls}
    assert paces == {DEFAULT_PACE_KMH}
    assert MIN_WALKING_SPEED_KMH <= DEFAULT_PACE_KMH <= MAX_WALKING_SPEED_KMH


@integration
def test_an_area_holding_no_candidates_is_an_honest_empty_day_and_is_unapprovable(
    db_engine: Engine, db_session: Session, sites: tuple[SiteRecordV1, ...]
) -> None:
    """ "Not enough here" is a real answer — never padding, and never a green tick."""
    empty = seed_area(db_session, "google-sub-a", polygon=EMPTY_BOX)
    client = signed_in_client(plan_app(engine=db_engine, reply=reply_of(*sites)))

    frames = stream_plan(client, propose_body(empty))

    assert named(frames, "status")[0]["candidates"] == 0
    itinerary = ItineraryV1.model_validate(named(frames, "itinerary")[0])
    assert itinerary.stops == ()
    (verdict,) = named(frames, "feasibility")
    assert verdict["ok"] is False and verdict["violations"]

    refusal = client.post(f"/plans/{plan_id_of(frames)}/approve")
    assert refusal.status_code == 409
    assert refusal.json()["error"] == "infeasible"


@integration
def test_an_unknown_area_is_404(client: TestClient) -> None:
    assert client.post("/plans", json=propose_body(uuid4())).status_code == 404


@integration
def test_another_users_area_cannot_be_planned_over(app: FastAPI, area_id: UUID) -> None:
    """The area scope is the same privacy boundary the research endpoint enforces."""
    intruder = signed_in_client(app, sub="google-sub-intruder")
    assert intruder.post("/plans", json=propose_body(area_id)).status_code == 404


@integration
def test_a_proposal_already_running_for_the_area_is_409(client: TestClient, area_id: UUID) -> None:
    """The idempotency guard, taken directly — the stream is synchronous, so the claim is not."""
    assert _claim(area_id) is True
    try:
        assert client.post("/plans", json=propose_body(area_id)).status_code == 409
    finally:
        _release(area_id)

    # …and the claim is released once the stream ends, so the next proposal runs.
    stream_plan(client, propose_body(area_id))
    assert _claim(area_id) is True, "the finished proposal never released its claim"
    _release(area_id)


# --- GET /plans/{plan_id} ------------------------------------------------------


@integration
def test_get_returns_the_plan_its_verdict_and_its_approval_state(
    client: TestClient, proposed: Frames
) -> None:
    body = client.get(f"/plans/{plan_id_of(proposed)}").json()

    assert ItineraryV1.model_validate(body["plan"]).schema_ver == "ItineraryV1"
    assert body["feasibility"]["ok"] is True
    assert body["feasibility"]["violations"] == []
    assert body["feasibility"]["checked_at"] is not None, "checked_at is set when the check ran"
    assert body["approval"] == {"state": "proposed", "approved_at": None, "superseded_by": None}


@integration
def test_the_attribution_unions_the_stops_records_and_the_legs_own_stamps(
    client: TestClient, proposed: Frames
) -> None:
    """Routing over OSM is a Produced Work: a leg owes ODbL wherever it renders."""
    attribution = client.get(f"/plans/{plan_id_of(proposed)}").json()["attribution"]
    assert "© OpenStreetMap contributors" in attribution, "the legs' own credit"
    assert "© OpenStreetMap" in attribution, "the stops' commons records' credit"
    assert attribution == sorted(set(attribution))


@integration
def test_a_compiling_plan_reports_compiling_and_not_an_unknown_state(
    client: TestClient, db_session: Session, proposed: Frames
) -> None:
    """ADR-0023's seven states, verbatim — the T007b hazard, taken directly.

    A three-value ``approval.state`` would render this plan as an unknown state and leave the
    UI unable to tell "approved, idle" from "approved, compile running".
    """
    plan_id = UUID(plan_id_of(proposed))
    assert client.post(f"/plans/{plan_id}/approve").status_code == 200
    assert claim_plan_for_compile(db_session, plan_id, user_id="google-sub-a") is not None
    db_session.commit()

    body = client.get(f"/plans/{plan_id}").json()
    assert body["approval"]["state"] == "compiling"
    assert body["approval"]["approved_at"] is not None


# --- the privacy boundary ------------------------------------------------------


@integration
def test_another_users_plan_is_byte_identical_to_a_missing_one(
    app: FastAPI, client: TestClient, proposed: Frames
) -> None:
    """`contracts/plans.md`: indistinguishable **by design** — same status, same bytes.

    Compared as raw ``content``, not as parsed JSON: a difference in wording, key order or
    whitespace is exactly the side channel that tells a prober an id exists.
    """
    mine = plan_id_of(proposed)
    intruder = signed_in_client(app, sub="google-sub-intruder")

    for method, path in (("GET", "/plans/%s"), ("POST", "/plans/%s/approve")):
        call = intruder.get if method == "GET" else intruder.post
        foreign = call(path % mine)
        unknown = call(path % uuid4())
        assert foreign.status_code == unknown.status_code == 404
        assert foreign.content == unknown.content
        assert b"403" not in foreign.content


@integration
def test_a_second_user_cannot_approve_someone_elses_plan(
    app: FastAPI, client: TestClient, db_session: Session, proposed: Frames
) -> None:
    """A ``404`` that also has to be *true*: the plan must still be unapproved afterwards."""
    plan_id = UUID(plan_id_of(proposed))
    intruder = signed_in_client(app, sub="google-sub-intruder")

    assert intruder.post(f"/plans/{plan_id}/approve").status_code == 404

    db_session.expire_all()
    stored = load_plan(db_session, plan_id, user_id="google-sub-a")
    assert stored is not None and stored.status == "proposed" and stored.approved_at is None


# --- POST /plans/{plan_id}/approve --------------------------------------------


@integration
def test_approve_transitions_the_plan_and_reports_when(
    client: TestClient, proposed: Frames
) -> None:
    plan_id = plan_id_of(proposed)
    body = client.post(f"/plans/{plan_id}/approve").json()

    assert body["plan_id"] == plan_id
    assert body["state"] == "approved"
    assert datetime.fromisoformat(body["approved_at"]).tzinfo is not None
    assert client.get(f"/plans/{plan_id}").json()["approval"]["state"] == "approved"


@integration
def test_approve_is_idempotent_and_replays_the_same_approved_at(
    client: TestClient, proposed: Frames
) -> None:
    """A double-click, a retry or a raced pair must never produce two divergent bundles."""
    plan_id = plan_id_of(proposed)
    first = client.post(f"/plans/{plan_id}/approve")
    second = client.post(f"/plans/{plan_id}/approve")

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    assert second.json()["approved_at"] == first.json()["approved_at"]


@integration
def test_an_approved_plan_is_not_mutated_by_anything_that_follows(
    client: TestClient, area_id: UUID, proposed: Frames
) -> None:
    """The approval is *of a specific day*: proposing another one leaves this one alone."""
    plan_id = plan_id_of(proposed)
    approved = client.post(f"/plans/{plan_id}/approve").json()
    before = client.get(f"/plans/{plan_id}").content

    successor = stream_plan(client, propose_body(area_id))

    assert plan_id_of(successor) != plan_id, "a second proposal is a new plan, not an edit"
    assert client.get(f"/plans/{plan_id}").content == before
    assert client.post(f"/plans/{plan_id}/approve").json() == approved


@integration
def test_an_infeasible_plan_is_409_with_its_violations_and_stays_unapproved(
    db_engine: Engine, db_session: Session, sites: tuple[SiteRecordV1, ...], area_id: UUID
) -> None:
    """FR-005's mechanical form: nothing downstream can be reached around the violations."""
    client = signed_in_client(plan_app(engine=db_engine, reply=reply_of(*sites)))
    frames = stream_plan(client, propose_body(area_id, budgets=dict(TIGHT)))
    plan_id = plan_id_of(frames)

    response = client.post(f"/plans/{plan_id}/approve")

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "infeasible"
    assert any(f"> budget {TIGHT['walking_m']:.0f}" in message for message in body["violations"])
    assert body["violations"] == named(frames, "feasibility")[0]["violations"]
    assert client.get(f"/plans/{plan_id}").json()["approval"]["state"] == "proposed"


@integration
def test_a_violation_never_quotes_the_commons_expression_it_evaluated(
    db_engine: Engine, db_session: Session, area_id: UUID
) -> None:
    """ADR-0030 A1 — the verdict names the stop; the tag renders through its own chip.

    An ``opening_hours`` expression is ODbL commons text, and a server-composed sentence is a
    surface with no attribution stamp in frame.
    """
    closed = seed_sites(
        db_session,
        (
            site("Upper Fortress", lon=28.2247, hours=OPEN_ALWAYS),
            site("Closed Museum", lon=28.2251, hours=TUESDAYS_ONLY),
        ),
    )
    client = signed_in_client(plan_app(engine=db_engine, reply=reply_of(*closed)))

    frames = stream_plan(client, propose_body(area_id))
    violations = named(frames, "feasibility")[0]["violations"]
    detail = client.get(f"/plans/{plan_id_of(frames)}").json()

    assert violations, "a Tuesday-only stop on a Friday is a violation"
    assert any("stop 1" in message for message in violations), "the breach names its stop order"
    for message in violations + detail["feasibility"]["violations"]:
        assert TUESDAYS_ONLY not in message


@integration
def test_a_superseded_plan_is_409_naming_the_successor_to_approve_instead(
    client: TestClient, db_session: Session, proposed: Frames
) -> None:
    plan_id = UUID(plan_id_of(proposed))
    prior = load_plan(db_session, plan_id, user_id="google-sub-a")
    assert prior is not None
    successor = supersede_plan(
        db_session,
        plan_id,
        user_id="google-sub-a",
        itinerary=prior.itinerary.model_copy(update={"id": uuid4()}),
    )
    assert successor is not None
    db_session.commit()

    response = client.post(f"/plans/{plan_id}/approve")

    assert response.status_code == 409
    assert response.json() == {
        "error": "plan_superseded",
        "superseded_by": str(successor.id),
    }
    assert client.get(f"/plans/{plan_id}").json()["approval"]["state"] == "superseded"


@integration
def test_a_plan_edited_under_the_client_is_409_stale(
    client: TestClient, db_session: Session, proposed: Frames
) -> None:
    """Approval is of a **specific day**: an in-place edit invalidates the compare-and-set."""
    plan_id = UUID(plan_id_of(proposed))
    stored = load_plan(db_session, plan_id, user_id="google-sub-a")
    assert stored is not None
    edited = stored.itinerary.model_copy(update={"lang": "fr"}).model_dump(mode="json")
    # The digest is deliberately *not* restated — that is what "mutated in place" means, and
    # it is the one change the revision counter cannot see.
    db_session.execute(update(UserPlan).where(UserPlan.id == plan_id).values(itinerary=edited))
    db_session.commit()

    response = client.post(f"/plans/{plan_id}/approve")

    assert response.status_code == 409
    assert response.json() == {"error": "plan_stale"}


@integration
def test_a_plan_in_a_state_approval_is_not_defined_from_is_409_not_approvable(
    client: TestClient, db_session: Session, proposed: Frames
) -> None:
    """A failed compile does not un-approve the day, and re-approving does not un-fail it."""
    plan_id = UUID(plan_id_of(proposed))
    assert client.post(f"/plans/{plan_id}/approve").status_code == 200
    assert claim_plan_for_compile(db_session, plan_id, user_id="google-sub-a") is not None
    assert finish_compile(db_session, plan_id, user_id="google-sub-a", outcome="failed") is not None
    db_session.commit()

    response = client.post(f"/plans/{plan_id}/approve")

    assert response.status_code == 409
    assert response.json() == {"error": "plan_not_approvable", "state": "failed"}


def test_the_wire_error_codes_are_the_repositorys_own_spellings() -> None:
    """`contracts/plans.md`: a closed set of four, spelled identically on both sides.

    Tier 1 and deliberately mechanical: the four literals below are what
    `web/src/plan/client.ts` keys :class:`PlanApprovalConflictError` on and what the four
    ``409`` tests above assert byte-for-byte. Adding a member to
    :data:`~commons.repository.RefusalReason` without deciding its status code here fails
    *this* test rather than reaching the wire under an invented third spelling.
    """
    wire = {"infeasible", "plan_superseded", "plan_stale", "plan_not_approvable"}
    assert set(get_args(RefusalReason)) == wire | {"not_found"}


@integration
def test_an_unknown_plan_id_is_404_on_both_read_and_approve(client: TestClient) -> None:
    unknown = uuid4()
    assert client.get(f"/plans/{unknown}").status_code == 404
    assert client.post(f"/plans/{unknown}/approve").status_code == 404


@integration
def test_the_approval_survives_the_process_that_made_it(
    app: FastAPI, client: TestClient, proposed: Frames, db_session: Session
) -> None:
    """SC-003 — read back through a *new* client over a *new* session, not from memory."""
    plan_id = plan_id_of(proposed)
    approved_at = client.post(f"/plans/{plan_id}/approve").json()["approved_at"]

    later = signed_in_client(app)
    body = later.get(f"/plans/{plan_id}").json()

    assert body["approval"]["state"] == "approved"
    assert body["approval"]["approved_at"] == approved_at
    db_session.expire_all()
    stored = load_plan(db_session, UUID(plan_id), user_id="google-sub-a")
    assert stored is not None and stored.approved_at is not None
    assert stored.approved_at.astimezone(UTC) == datetime.fromisoformat(approved_at)


# --- the day the fixtures describe --------------------------------------------


@integration
def test_the_streamed_day_is_the_day_the_row_holds(client: TestClient, proposed: Frames) -> None:
    """The stream and the read-back path describe one row; they must not contradict."""
    streamed = named(proposed, "itinerary")[0]
    stored = client.get(f"/plans/{plan_id_of(proposed)}").json()["plan"]
    assert ItineraryV1.model_validate(stored) == ItineraryV1.model_validate(streamed)
    walked = sum(leg["distance_m"] for leg in stored["legs"])
    assert walked == pytest.approx(LEG_DISTANCE_M * (len(stored["stops"]) - 1))


@integration
def test_a_frameless_area_reaches_the_traveller_as_hours_unknown_not_as_a_guess(
    db_engine: Engine, db_session: Session, sites: tuple[SiteRecordV1, ...]
) -> None:
    """``timezone IS NULL`` is *unresolved*; substituting UTC would answer in the wrong frame.

    The plan is still proposed and still readable — it simply cannot be approved, which is the
    honest outcome for a day nothing can check the opening windows of.
    """
    frameless = seed_area(db_session, "google-sub-a", timezone=None, country_code=None)
    client = signed_in_client(plan_app(engine=db_engine, reply=reply_of(*sites)))

    frames = stream_plan(client, propose_body(frameless))

    (verdict,) = named(frames, "feasibility")
    assert verdict["ok"] is False
    assert all("hours cannot be evaluated" in message for message in verdict["violations"])
    assert client.post(f"/plans/{plan_id_of(frames)}/approve").status_code == 409


@integration
def test_the_plan_is_private_and_never_reaches_the_shared_commons(
    client: TestClient, proposed: Frames, db_session: Session
) -> None:
    """FR-007 — an itinerary is personal data; ``GET /sites`` is the shared commons.

    Checked over the wire rather than in the table, because the leak that matters is the one a
    signed-in stranger could read.
    """
    itinerary = named(proposed, "itinerary")[0]
    body = client.get("/sites?bbox=28.20,36.43,28.25,36.46").json()

    assert body["sites"], "the commons the plan drew on is still readable"
    assert all("stops" not in site_body for site_body in body["sites"])
    assert itinerary["id"] not in {site_body["id"] for site_body in body["sites"]}
