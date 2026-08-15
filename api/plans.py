"""T024 — ``POST /plans`` (SSE), ``GET /plans/{plan_id}`` and ``POST /plans/{plan_id}/approve``.

Contract: `specs/002-plan-compile-offline/contracts/plans.md`. Like `api/areas.py` this module
is deliberately thin — `planner/pipeline.py::run_plan` already yields exactly the frames the
contract specifies and `commons/repository.py` already owns the approval gate — so what lives
here is transport, the request boundary, and four decisions a transport shim has to make
honestly.

**1. The SSE idiom is `api/areas.py`'s, imported rather than re-derived.** ``_sse``,
``SSE_MEDIA_TYPE`` and ``SSE_HEADERS`` come from that module by name. This is the second
event-stream endpoint in the service and a second framing implementation is exactly how two
endpoints come to disagree about a ``\\n\\n`` — the private import is the cheaper of the two
couplings. Same for :func:`~api.areas._load_area`: the row-scope filter that makes another
user's area a ``404`` is *the* privacy boundary, and it should have one implementation, not
one per endpoint that remembers to write it.

**2. The four refusal codes are not re-spelled here.**
:data:`~commons.repository.RefusalReason`'s members *are* the wire ``error`` strings. This
module maps a refusal to a status code and translates no vocabulary, so a fifth refusal reason
cannot reach the wire under an invented third spelling (`contracts/plans.md`: "adding a member
to either side without the other is a contract break"). The ``409`` bodies are built as a
:class:`~fastapi.responses.JSONResponse` and **not** as an ``HTTPException``, because FastAPI
nests the latter under ``detail`` and the client reads ``error`` / ``violations`` /
``superseded_by`` at the top level (`web/src/plan/client.ts::approvePlan`).

**3. ``day_start`` is refused at the request boundary, not downstream.** A tz-aware
``day_start`` is a caller bug that
:func:`~planner.nodes.propose_itinerary._require_naive_day_start` raises on — but by the time
:func:`~planner.pipeline.run_plan` reaches ``propose_itinerary`` the ``load_sites`` frame has
already been yielded, the response is a committed ``200``, and there is no status code left to
send. So the same function is called from a pydantic validator, where its ``ValueError``
becomes the ``422`` the contract asks for. It is imported rather than restated: two copies of
"reject an offset here" is one copy away from a ``10:00Z`` anchoring a UTC+3 day three hours
early, which is precisely what the rule exists to stop.

**4. The ``409`` proposal guard is process-local**, exactly as `api/areas.py`'s research guard
is, and for the same reason — making it true across processes needs a database-backed claim,
which is a schema decision and an ADR rather than a silent addition here. The plan row is not
at risk either way: ``create_plan`` keys the row on the itinerary's id, so a doubled proposal
writes two distinct plans rather than corrupting one.

The transaction boundary is the API's, as in `api/areas.py`: the repository flushes, this
module commits once the stream completes and rolls back otherwise, so a client that
disconnects mid-proposal leaves no half-written plan behind.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from geoalchemy2.shape import to_shape
from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session
from starlette.responses import Response

from api.areas import SSE_HEADERS, SSE_MEDIA_TYPE, _load_area, _sse
from api.deps import RouterDep, SessionDep, SessionFactoryDep
from api.security import CurrentUser
from commons.llm import LiteLLMRouter, ModelRouter
from commons.models import Budgets, ItineraryV1, SiteRecordV1
from commons.repository import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    PlanApproved,
    PlanRecord,
    PlanRefused,
    PlanSummary,
    approve_plan,
    attribution_for,
    create_plan,
    list_plans,
    load_plan,
    load_site,
    record_feasibility,
    sites_within,
)
from commons.routing import PedestrianCosting, RoutingProvider, select_provider
from planner.nodes.propose_itinerary import _require_naive_day_start
from planner.pipeline import Event, PlanRequest, PlanRow, run_plan

__all__ = [
    "ApprovalBody",
    "ApproveResponse",
    "BudgetsBody",
    "FeasibilityBody",
    "PlanDetailResponse",
    "PlanListResponse",
    "PlanSummaryBody",
    "ProposeRequestBody",
    "router",
]

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])

#: ``app.state`` keys, mirroring `api/deps.py`'s injection style: setting either replaces the
#: default. Local to this module because these two seams have exactly one caller — the plan
#: stream — and `api/deps.py` is another agent's file this slice.
ROUTING_STATE = "routing_provider"
COSTING_STATE = "plan_costing"

#: The pace every leg of every proposed day is priced at, **stated rather than defaulted**.
#:
#: ADR-0020 forbids letting Valhalla's implicit 5.1 km/h apply and requires the caller to name
#: the pace; :class:`~commons.routing.PedestrianCosting` therefore has no default and this is
#: the call site that owes it one. 4.5 km/h is an unhurried sightseeing walk and sits inside
#: the product's 3.5–5.0 band. It is **not** a user preference: M1 has nowhere to store one
#: (there is no `user_prefs` table and the request body the contract fixes carries no pace), so
#: this is one honest number for everybody rather than a per-request guess. When the preference
#: lands it replaces this constant and nothing else moves — which is the point of it being one
#: constant at one call site.
DEFAULT_PACE_KMH: Final = 4.5

#: Areas with a proposal in flight **in this process** — see the module docstring's point 4.
_PROPOSING: set[UUID] = set()
_PROPOSING_LOCK = threading.Lock()

#: The one ``404`` body for a plan, so "unknown id" and "someone else's id" are answered by
#: the same line of code and are therefore *byte-identical* rather than merely similar
#: (`contracts/plans.md`: "indistinguishable by design").
_PLAN_NOT_FOUND: Final = "no plan with that id for this user"


# ── request/response bodies ────────────────────────────────────────────────────────


class BudgetsBody(BaseModel):
    """``budgets`` — the feasibility limits (`docs/data/itinerary.md`).

    Both are ``gt=0`` and that is the contract's ``422``: a day with a zero walking budget or
    zero hours is not a tight day, it is an unstatable one, and it would reach the gate as a
    plan that violates its own budgets no matter what the planner proposed.
    """

    model_config = ConfigDict(extra="forbid")

    #: Total walking for the day, metres.
    walking_m: float = Field(gt=0.0)
    #: Total elapsed day, hours.
    hours: float = Field(gt=0.0)

    def to_model(self) -> Budgets:
        return Budgets(walking_m=self.walking_m, hours=self.hours)


class ProposeRequestBody(BaseModel):
    """``POST /plans`` — the whole request (`contracts/plans.md`).

    ``date`` is **required and never defaulted server-side**: it is the frame
    ``opening-hours-py`` evaluates weekday rules, ``PH`` and seasonal ranges against, and a
    server-side ``date.today()`` in UTC plans Sunday for a Monday morning in Auckland.
    """

    model_config = ConfigDict(extra="forbid")

    area_id: UUID
    budgets: BudgetsBody
    #: The calendar day planned for, in the **area's** local frame.
    date: date_type
    #: Area-local wall clock the day is anchored at. Request-only — not an ``ItineraryV1``
    #: field. Must carry no UTC offset; see :meth:`_day_start_is_area_local`.
    day_start: time
    interests: str = ""
    lang: str = "en"

    @field_validator("day_start")
    @classmethod
    def _day_start_is_area_local(cls, value: time) -> time:
        """Refuse an offset-bearing ``day_start`` **here**, where a ``422`` is still possible.

        The rule itself is
        :func:`~planner.nodes.propose_itinerary._require_naive_day_start`, called rather than
        copied. pydantic coerces ``"10:00Z"``, ``"10:00+00:00"`` and a bare ``36000`` into a
        tz-aware :class:`~datetime.time`; downstream, ``datetime.combine`` adopts that offset
        and ``.time()`` silently drops it again, so a ``10:00Z`` for an area at UTC+3 anchors
        every ``planned_start`` and every opening-window check three hours early and then
        freezes into a bundle where nothing re-checks it.
        """
        return _require_naive_day_start(value)


class FeasibilityBody(BaseModel):
    """The deterministic verdict, read off the ``user_plan`` row — never recomputed here.

    **``ok`` is the approval predicate and ``violations`` is what makes it false.**
    ``warnings`` is the advisory half (ADR-0022, amended 2026-08-14) — today, every stop whose
    opening hours could not be evaluated — and it is a *separate* list rather than a flag on a
    combined one so that a client which has never heard of warnings cannot render one as a
    blocker. Both are the server's own words, verbatim (FR-005).
    """

    ok: bool
    violations: list[str]
    #: Advisory only. A non-empty ``warnings`` with ``ok: true`` is the normal case, not a
    #: contradiction — a non-empty ``violations`` with ``ok: true`` still is.
    warnings: list[str] = []
    #: UTC, and set **only** when the check ran (never mirrored from ``updated_at``, which
    #: bumps on any write and would report a time the check did not happen).
    checked_at: datetime | None


class ApprovalBody(BaseModel):
    """``approval`` — ADR-0023's seven states, exposed verbatim with no mapping layer."""

    #: ``proposing`` | ``proposed`` | ``approved`` | ``superseded`` | ``compiling`` |
    #: ``compiled`` | ``failed``. Typed as ``str`` and passed through from the row: a
    #: re-declared ``Literal`` here would be an eighth place to keep in agreement with
    #: :data:`commons.db.PlanStatus`, and the T007b reconciliation exists because those
    #: re-declarations drift.
    state: str
    approved_at: datetime | None
    superseded_by: UUID | None


class PlanDetailResponse(BaseModel):
    """``GET /plans/{plan_id}`` ``200``."""

    #: The ``ItineraryV1`` verbatim, per `docs/data/itinerary.md`.
    plan: ItineraryV1
    feasibility: FeasibilityBody
    approval: ApprovalBody
    #: Union across the plan's stops' commons records and its legs' stamps — derived from the
    #: stamps, never composed here (`contracts/sites.md`'s rule, applied to a plan).
    attribution: list[str]


class PlanSummaryBody(BaseModel):
    """One entry of ``GET /plans`` — enough to choose a plan, never the plan itself.

    The itinerary is deliberately absent: a list is how a traveller *finds* a day, and
    ``GET /plans/{plan_id}`` is how they read it. Shipping every ``ItineraryV1`` here would
    make the cheapest screen in the app the most expensive response it serves — and would put
    every stop's commons-derived value on a surface that renders no attribution (ADR-0019:
    attribution is co-present with its value), which is a licence problem and not only a size
    one. ``stop_count`` is the one derived number a chooser needs, and it carries no
    commons-derived text.
    """

    plan_id: UUID
    area_id: UUID
    #: ``ItineraryV1.date``. Nullable in the type only — no write path can store a plan
    #: without one — so a row whose ``jsonb`` has been mangled outside the application still
    #: lists rather than turning the whole page into a ``500``.
    date: date_type | None
    #: ADR-0023's seven states, verbatim; see :class:`ApprovalBody`.
    state: str
    feasible: bool
    stop_count: int
    created_at: datetime
    approved_at: datetime | None
    superseded_by: UUID | None


class PlanListResponse(BaseModel):
    """``GET /plans`` ``200`` — newest first, and **empty is a success**.

    A caller with no plans gets ``{"plans": []}`` and ``200``, never a ``404``: "you have not
    planned anything yet" is an answer, and making it share a status code with "something
    broke" is how a first-run screen ends up rendering an error at the one moment the product
    has to be welcoming.
    """

    plans: list[PlanSummaryBody]


class ApproveResponse(BaseModel):
    """``POST /plans/{plan_id}/approve`` ``200`` — the same body on a replay."""

    plan_id: UUID
    state: str
    #: Nullable in the type only: every post-approval status carries it by database `CHECK`.
    #: A response model that could not represent ``None`` would turn a row the database says
    #: is impossible into a ``500`` instead of an honest answer.
    approved_at: datetime | None


# ── the seams the plan stream needs ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _PlanStore:
    """:class:`~planner.pipeline.PlanStore` bound to a session and the auth subject.

    Both writes flush and do not commit — this module owns the transaction boundary — and
    ``record_feasibility`` is scoped to ``user_id`` like every other ``user_plan`` read and
    write, even though the row was inserted two statements earlier: the scope filter is the
    invariant, not a check someone remembered to do upstream.
    """

    session: Session
    user_id: str

    def create(self, itinerary: ItineraryV1) -> PlanRow:
        return create_plan(self.session, itinerary)

    def record_feasibility(
        self,
        plan_id: UUID,
        *,
        feasible: bool,
        violations: Sequence[str],
        warnings: Sequence[str],
    ) -> PlanRow | None:
        return record_feasibility(
            self.session,
            plan_id,
            user_id=self.user_id,
            feasible=feasible,
            violations=list(violations),
            warnings=list(warnings),
        )


def _load_sites_from(session: Session, polygon: BaseGeometry) -> Any:
    """A :class:`~planner.pipeline.LoadSites` over the area's **stored** polygon.

    The pipeline hands back the ``area_id`` it was given; the polygon is resolved here because
    the API has already read the row (and proved it belongs to the caller) and ``user_plan``
    scoping must not be re-derived inside ``planner/``.
    """

    def load_sites(area_id: UUID) -> Sequence[SiteRecordV1]:
        return sites_within(session, polygon)

    return load_sites


def get_routing(request: Request) -> RoutingProvider:
    """The engine, from ``app.state`` or from ``SIYUR_ROUTING_PROVIDER`` (`commons/routing.py`).

    A misconfigured provider name is a ``503`` rather than a ``500``: the service is up and the
    request is well-formed — the deployment is missing a routing engine, and that is what the
    same code says for a missing ``SIYUR_DATABASE_URL`` (`api/deps.py`).
    """
    injected: RoutingProvider | None = getattr(request.app.state, ROUTING_STATE, None)
    if injected is not None:
        return injected
    try:
        return select_provider()
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error


RoutingDep = Annotated[RoutingProvider, Depends(get_routing)]


def get_costing(request: Request) -> PedestrianCosting:
    """The pace this day is priced at. See :data:`DEFAULT_PACE_KMH` for why it is stated."""
    injected: PedestrianCosting | None = getattr(request.app.state, COSTING_STATE, None)
    return (
        injected if injected is not None else PedestrianCosting(walking_speed_kmh=DEFAULT_PACE_KMH)
    )


CostingDep = Annotated[PedestrianCosting, Depends(get_costing)]


def _model_router(injected: ModelRouter | None) -> ModelRouter:
    """The model seam. Unlike ``curate``, proposing has **no deterministic fallback**.

    `planner/nodes/curate.py` degrades to a deterministic ranking when no router is
    configured; ``propose_itinerary`` cannot, because the model's ordered id list *is* the
    day. So an absent seam builds the real :class:`~commons.llm.LiteLLMRouter` rather than
    quietly proposing nothing — a missing API key then fails loudly at the provider instead of
    arriving as "the model selected no usable place".
    """
    return injected if injected is not None else LiteLLMRouter()


def _claim(area_id: UUID) -> bool:
    """Take the in-process proposal claim for ``area_id``; ``False`` if one is held."""
    with _PROPOSING_LOCK:
        if area_id in _PROPOSING:
            return False
        _PROPOSING.add(area_id)
        return True


def _release(area_id: UUID) -> None:
    with _PROPOSING_LOCK:
        _PROPOSING.discard(area_id)


# ── helpers ───────────────────────────────────────────────────────────────────────


def _plan_not_found() -> HTTPException:
    """The ``404`` for an unknown **or** another subject's plan — one body for both.

    :func:`~commons.repository.load_plan` already collapses "absent", "foreign" and "corrupt"
    into ``None``, so this handler never learns which it was and cannot leak it by accident.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_PLAN_NOT_FOUND)


def _require_plan(session: Session, plan_id: UUID, subject: str) -> PlanRecord:
    plan = load_plan(session, plan_id, user_id=subject)
    if plan is None:
        raise _plan_not_found()
    return plan


def _attribution(session: Session, plan: PlanRecord) -> list[str]:
    """Every credit the plan owes: its stops' commons records **and** its legs' own stamps.

    Legs are counted separately because a leg is not a :class:`~commons.models.SourcedValue`
    and carries a bare :class:`~commons.models.SourceRef` — routing over OSM is a Produced
    Work, so "© OpenStreetMap contributors" is owed by a day whose *stops* happen to come from
    a permissive source. A stop whose record has since gone from the commons contributes
    nothing rather than raising: the plan is still readable, and an attribution list is a
    union of what is there.
    """
    sites = [
        record
        for stop in plan.itinerary.stops
        if (record := load_site(session, stop.site_id)) is not None
    ]
    credits = set(attribution_for(sites))
    credits.update(leg.source.attribution for leg in plan.itinerary.legs if leg.source.attribution)
    return sorted(credits)


def _detail(session: Session, plan: PlanRecord) -> PlanDetailResponse:
    return PlanDetailResponse(
        plan=plan.itinerary,
        feasibility=FeasibilityBody(
            ok=plan.feasible,
            violations=list(plan.violations),
            # Read off the row's own severity split, never re-derived from the message text:
            # `commons.repository` stores the severity beside each message precisely so the
            # read-back path does not have to guess which half a sentence came from.
            warnings=list(plan.warnings),
            checked_at=plan.feasibility_checked_at,
        ),
        approval=ApprovalBody(
            state=plan.status,
            approved_at=plan.approved_at,
            superseded_by=plan.superseded_by,
        ),
        attribution=_attribution(session, plan),
    )


def _refusal(refused: PlanRefused) -> Response:
    """A refusal → its ``409``, with the reason spelled **exactly** as the repository spells it.

    All four are ``409``: each is a refusal of a transition against the row's current state,
    not a complaint about the request (which has no body to get wrong). The payloads are the
    contract's — ``violations`` for ``infeasible``, ``superseded_by`` for ``plan_superseded``,
    ``state`` for ``plan_not_approvable`` — and they are top-level keys rather than an
    ``HTTPException``'s ``detail``, because that is what `web/src/plan/client.ts` reads.
    """
    body: dict[str, Any] = {"error": refused.reason}
    plan = refused.plan
    if refused.reason == "infeasible" and plan is not None:
        body["violations"] = list(plan.violations)
    elif refused.reason == "plan_superseded" and plan is not None:
        body["superseded_by"] = str(plan.superseded_by) if plan.superseded_by else None
    elif refused.reason == "plan_not_approvable" and plan is not None:
        body["state"] = plan.status
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=body)


def _plan_frames(
    session: Session, area_id: UUID, first: Event, rest: Iterator[Event]
) -> Iterator[str]:
    """Stream the pipeline, then commit — the transaction boundary the repository leaves open.

    Byte-for-byte the shape of `api/areas.py::_research_frames`, and for the same reasons: the
    commit happens only after the last frame, any failure (a client disconnect arrives as
    ``GeneratorExit``) rolls the whole proposal back, and a stream that has already sent a
    ``200`` reports its failure **in band** as an ``error`` frame rather than dying silently —
    the status code is long gone by then.
    """
    try:
        yield _sse(first.event, first.data)
        for event in rest:
            yield _sse(event.event, event.data)
        session.commit()
    except Exception as error:  # noqa: BLE001 — the status code is long gone; say so in-band
        session.rollback()
        _log.exception("proposal failed mid-stream for area %s", area_id)
        yield _sse(
            "error",
            {"area_id": str(area_id), "error": type(error).__name__, "detail": str(error)},
        )
    finally:
        session.close()
        _release(area_id)


# ── endpoints ─────────────────────────────────────────────────────────────────────


@router.post("", summary="Propose a day over the area and pause it at the gate (SSE)")
def propose_plan(
    user: CurrentUser,
    factory: SessionFactoryDep,
    model_router: RouterDep,
    routing: RoutingDep,
    costing: CostingDep,
    body: ProposeRequestBody,
) -> Response:
    """T024 — drive :func:`~planner.pipeline.run_plan` and stream its frames as SSE.

    Dependency order is ``user`` → ``factory`` → ``body``, which is what makes ``401`` answer
    before ``422`` and ``422`` before storage is touched (`api/deps.py` point 3).

    The first ``next()`` runs before a byte is sent, so a failure in ``load_sites`` still
    becomes a real status code; everything after it streams, and the proposal commits or rolls
    back whole.
    """
    with factory() as lookup:
        area = _load_area(lookup, body.area_id, user.sub)
        polygon: BaseGeometry = to_shape(area.polygon)
        # Read while the row is loaded: `None` means the area has no resolved local frame, and
        # `planner.feasibility` turns that into `hours_unknown` per stop rather than guessing
        # UTC. Never defaulted here either — this module has no more information than the row.
        timezone = area.timezone
        country_code = area.country_code

    if not _claim(body.area_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a proposal is already running for area {body.area_id}",
        )

    session = factory()
    try:
        stream = run_plan(
            PlanRequest(
                area_id=body.area_id,
                user_id=user.sub,
                budgets=body.budgets.to_model(),
                day=body.date,
                day_start=body.day_start,
                timezone=timezone,
                country_code=country_code,
                interests=body.interests,
                lang=body.lang,
            ),
            load_sites=_load_sites_from(session, polygon),
            store=_PlanStore(session=session, user_id=user.sub),
            router=_model_router(model_router),
            routing=routing,
            costing=costing,
        )
        first = next(stream)
    except BaseException:
        session.close()
        _release(body.area_id)
        raise

    return StreamingResponse(
        _plan_frames(session, body.area_id, first, stream),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


#: ``?limit=`` — the range is the request boundary's, so an out-of-range value is a ``422``
#: rather than a silently clamped page. The clamp still exists one layer down
#: (:func:`~commons.repository._bounded`), because the query is what must be bounded.
LimitQuery = Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)]


def _summary(plan: PlanSummary) -> PlanSummaryBody:
    return PlanSummaryBody(
        plan_id=plan.id,
        area_id=plan.area_id,
        date=plan.date,
        # Passed through from the row, like `ApprovalBody.state`: no DB→API mapping layer
        # exists for the seven states and none is introduced here.
        state=plan.status,
        feasible=plan.feasible,
        stop_count=plan.stop_count,
        created_at=plan.created_at,
        approved_at=plan.approved_at,
        superseded_by=plan.superseded_by,
    )


@router.get("", response_model=PlanListResponse, summary="The caller's plans, newest first")
def list_the_callers_plans(
    user: CurrentUser, session: SessionDep, limit: LimitQuery = LIST_LIMIT_DEFAULT
) -> PlanListResponse:
    """The app's memory: every plan this subject has made, most recent first.

    Without this endpoint a ``plan_id`` exists only in the response that created it, so
    closing the tab loses the day permanently (`docs/design/usable-m1-plan.md` Phase A). The
    scope is :func:`~commons.repository.list_plans`' ``WHERE user_id``, not a filter applied
    to a wider read — a list is where an unscoped query stops being one leaked row and becomes
    the whole table.
    """
    return PlanListResponse(
        plans=[_summary(plan) for plan in list_plans(session, user_id=user.sub, limit=limit)]
    )


@router.get("/{plan_id}", response_model=PlanDetailResponse, summary="The plan and its state")
def get_plan(user: CurrentUser, session: SessionDep, plan_id: UUID) -> PlanDetailResponse:
    """T024 — the itinerary, its approval state and its feasibility verdict.

    An unknown ``plan_id`` and another subject's ``plan_id`` are the *same* ``404``: the
    scoping is :func:`~commons.repository.load_plan`'s, so this handler is never told which
    of the two happened.
    """
    return _detail(session, _require_plan(session, plan_id, user.sub))


@router.post("/{plan_id}/approve", summary="The HITL gate — proposed → approved")
def approve(user: CurrentUser, session: SessionDep, plan_id: UUID) -> Response:
    """T024 — the one transition that unlocks ``POST /bundles``. Empty body; idempotent.

    The idempotency is the repository's compare-and-set, not a check here: a second approve
    finds the transition already made and replays the **same** ``approved_at``, so a
    double-click or a raced pair can never produce two divergent bundles.
    """
    outcome = approve_plan(session, plan_id, user_id=user.sub, approved_by=user.sub)
    if isinstance(outcome, PlanApproved):
        # Commit on a replay too: it is a no-op write-wise, and not committing would leave the
        # session's transaction open for the next request on this connection.
        session.commit()
        return JSONResponse(
            content=ApproveResponse(
                plan_id=outcome.plan.id,
                state=outcome.plan.status,
                approved_at=outcome.plan.approved_at,
            ).model_dump(mode="json")
        )
    session.rollback()
    if outcome.reason == "not_found":
        raise _plan_not_found()
    return _refusal(outcome)
