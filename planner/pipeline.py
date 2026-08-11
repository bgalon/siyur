"""The pipelines — one generator per write path, each yielding its contract's frames.

:func:`run_research` is ``resolve_area → research → curate → persist`` (T035, slice 001);
:func:`run_plan` is ``load_sites → propose_itinerary → route → feasibility → persist``
(T023, slice 002). Both yield exactly the frames their contract specifies, so ``api/``
stays a thin SSE transport shim and the trajectory evals assert the phase sequence
**offline, with no HTTP, no database and no model**.

Three design calls worth stating, because each one is load-bearing:

**A generator, not a callback soup.** The caller drives it. A client that disconnects
simply stops pulling and the pass stops with it — there is no orphaned background task and
no half-written area.

**Persistence is injected** (:class:`Persist`), never imported. ``planner/`` therefore has
no dependency on :mod:`commons.repository`, the API owns the transaction boundary (the
repository flushes, it does not commit), and this module is unit-testable against an
in-memory fake.

**Research is invoked once per adapter, not once for all of them.**
:func:`planner.nodes.research.research` accepts the whole adapter sequence, but it is a
plain function: it returns only when every adapter has finished, so a single call could
not emit progress *during* a slow Overpass fetch — the user would wait in silence and then
get every ``status`` frame at once. Adapters are independent by construction, so driving
them one at a time is semantically identical and actually streams (FR-012: the user sees
progress and partial results). The per-adapter :class:`~planner.nodes.research.SourceReport`
values are concatenated in adapter order, so the summary is unchanged.

Nothing here fabricates a place: every emitted ``site`` is a record some adapter stamped
and the merge kept, and an empty area yields ``summary.sites == 0`` with zero ``site``
frames (SC-006). Nothing fabricates a *day* either: every ``Stop`` :func:`run_plan` streams
references a record :func:`run_research` already put in the commons.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from typing import Any, Final, Literal, Protocol, runtime_checkable
from uuid import UUID

from commons.llm import ModelRouter, TaskTier
from commons.merge import MergeResult
from commons.models import Budgets, ItineraryV1, RouteLegV1, SiteRecordV1
from commons.routing import PedestrianCosting, RoutingProvider
from commons.sources.base import SourceAdapter
from planner.feasibility import check_feasibility
from planner.nodes.curate import curate
from planner.nodes.propose_itinerary import (
    DEFAULT_DWELL_MIN,
    DEFAULT_MAX_STOPS,
    Proposal,
    propose_itinerary,
)
from planner.nodes.research import SourceReport, research
from planner.nodes.resolve_area import (
    AreaRequest,
    DivisionsLookup,
    Geocoder,
    resolve_area,
)

__all__ = [
    "EMPTY_DAY_VIOLATION",
    "PHASES",
    "PLAN_PHASES",
    "Event",
    "LoadSites",
    "Persist",
    "PersistOutcome",
    "PlanNotRecorded",
    "PlanRequest",
    "PlanRow",
    "PlanStore",
    "run_plan",
    "run_research",
]

EventName = Literal["status", "site", "summary", "itinerary", "feasibility", "done"]

#: The phase sequence the trajectory eval (T048) asserts a `superset` of.
PHASES: tuple[str, ...] = ("resolve_area", "research", "curate")

#: The plan stream's phases (`specs/002-plan-compile-offline/contracts/plans.md`). Slice
#: 002 extends the trajectory to ``… → curate → propose_itinerary``; the two pipelines are
#: separate endpoints, so the *combined* journey is the superset, not this tuple alone.
PLAN_PHASES: tuple[str, ...] = ("load_sites", "propose_itinerary", "route")


@dataclass(frozen=True, slots=True)
class Event:
    """One SSE frame: the ``event:`` name plus its JSON-serialisable ``data:`` payload."""

    event: EventName
    data: Mapping[str, Any]


@runtime_checkable
class PersistOutcome(Protocol):
    """What a persist call reports back. :class:`commons.repository.UpsertReport` satisfies it."""

    @property
    def created(self) -> int: ...

    @property
    def updated(self) -> int: ...

    @property
    def conflicts(self) -> int: ...


@runtime_checkable
class Persist(Protocol):
    """Write merged records to the commons. Injected, so ``planner/`` stays storage-agnostic."""

    def __call__(self, results: Sequence[MergeResult]) -> PersistOutcome: ...


def _source_event(report: SourceReport) -> Event:
    """One ``status`` frame per source, carrying what was *and was not* found (FR-012)."""
    data: dict[str, Any] = {
        "phase": "research",
        "source": report.kind,
        "found": report.found,
        "degraded": report.degraded,
    }
    if report.reason is not None:
        data["reason"] = report.reason
    if report.dropped:
        data["dropped"] = dict(report.dropped)
    return Event("status", data)


def run_research(
    request: AreaRequest,
    *,
    adapters: Sequence[SourceAdapter],
    persist: Persist | None = None,
    router: ModelRouter | None = None,
    divisions: DivisionsLookup | None = None,
    geocoder: Geocoder | None = None,
    area_id: UUID | None = None,
    observed_at: date | None = None,
) -> Iterator[Event]:
    """Run one research pass over ``request``, yielding progress as it happens.

    Resolution failures propagate as
    :class:`~planner.nodes.resolve_area.AreaInvalid` /
    :class:`~planner.nodes.resolve_area.AreaNotResolved` from the **first** ``next()``,
    before any frame is yielded — so the caller maps them onto ``422``/``404`` while it can
    still send a status code, and a stream that has started always completes.
    """
    area = resolve_area(request, divisions=divisions, geocoder=geocoder)
    yield Event(
        "status",
        {"phase": "resolve_area", "msg": "polygon ready", "source": area.source.kind},
    )

    records: list[Any] = []
    reports: list[SourceReport] = []
    for adapter in adapters:
        found = research(area.polygon, [adapter])
        records.extend(found.records)
        reports.extend(found.reports)
        for report in found.reports:
            yield _source_event(report)

    curated = curate(records, router=router, observed_at=observed_at)
    yield Event(
        "status",
        {
            "phase": "curate",
            "merged": len(curated.results),
            "conflicts": curated.conflicts,
            "derived_names": curated.derived_names,
            "flags": len(curated.flags),
            "rejected": list(curated.rejected),
        },
    )

    # Persist before streaming the records, so a `site` frame is only ever emitted for
    # something that actually reached the commons — the client is never shown a place
    # that a failed write silently dropped.
    outcome = persist(curated.results) if persist is not None else None

    for result in curated.results:
        yield Event("site", result.record.model_dump(mode="json"))

    yield Event(
        "summary",
        {
            "sites": len(curated.results),
            "new": outcome.created if outcome is not None else 0,
            "reused": outcome.updated if outcome is not None else 0,
            "conflicts": outcome.conflicts if outcome is not None else curated.conflicts,
            "sources": {report.kind: report.found for report in reports},
            "degraded_sources": [r.kind for r in reports if r.degraded],
        },
    )
    yield Event("done", {"area_id": str(area_id) if area_id is not None else None})


# ── the plan pipeline (T023 · contracts/plans.md) ─────────────────────────────────


#: The violation a zero-stop day is refused with. Not a
#: :class:`~planner.feasibility.Violation`: :data:`~planner.feasibility.ViolationCode` is a
#: **closed** set the web app renders per kind, and "there is no day here" is not a breach of
#: a budget or a window — it is the absence of the thing budgets are checked against.
#: :mod:`planner.feasibility` says so itself: an empty day walks zero metres in zero hours,
#: is therefore arithmetically ``ok``, and "nothing here can tell 'the area held nothing'
#: from 'the proposal failed'". This pipeline *can* tell them apart — a refusal raises out of
#: :func:`~planner.nodes.propose_itinerary.propose_itinerary` and never reaches here, so a
#: zero-stop itinerary arriving at this point means exactly one thing: the commons held no
#: candidates. That is an honest plan and it is streamed as one (`contracts/plans.md`: a
#: shorter honest plan or "not enough here", never padding) — and it is written to the row
#: ``feasible=False``, which is what makes it unapprovable, because
#: ``commons.repository.approve_plan`` has ``feasible IS TRUE`` in its predicate and the
#: post-approval `CHECK` makes it impossible below that. No new gate is invented here; the
#: existing FR-005 gate is simply told the truth.
EMPTY_DAY_VIOLATION: Final = (
    "the plan has no stops: the commons holds no candidate places for this area, so there "
    "is no day to walk and nothing to approve — research the area first"
)


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """What the day is asked for, and the area frame it is asked in.

    ``day``/``day_start`` are **area-local wall clock** and ``timezone``/``country_code``
    are what makes that frame meaningful — the two clocks never mix here: nothing in this
    module reads ``now()``, and every timestamp on the ``user_plan`` row is UTC and is
    stamped by the repository, not by the planner.
    """

    area_id: UUID
    user_id: str
    budgets: Budgets
    #: ``ItineraryV1.date`` — the calendar day, in the area's frame. Never defaulted from
    #: the server clock (`contracts/plans.md`: a UTC ``today()`` plans the wrong weekday for
    #: half the planet, and every opening window is then checked against it).
    day: date
    #: Request-only: the wall clock the day is anchored at. ``budgets.hours`` is a duration
    #: and cannot anchor anything.
    day_start: time
    #: The ``area`` row's IANA id / ISO 3166-1 alpha-2, or ``None`` when that row predates
    #: the frame columns. ``None`` is *unresolved* and reaches the traveller as
    #: ``hours_unknown``; it is never defaulted to UTC or to a country (`commons/db.py`).
    timezone: str | None = None
    country_code: str | None = None
    interests: str = ""
    lang: str = "en"


@runtime_checkable
class LoadSites(Protocol):
    """Read the area's commons records. Injected, mirroring :class:`Persist`.

    Takes the ``area_id`` the frame reports rather than a polygon, so the one identifier the
    stream is *about* is the one identifier the loader is asked for;
    ``commons.repository.sites_within`` is the implementation, behind whatever polygon
    lookup the API already did.
    """

    def __call__(self, area_id: UUID) -> Sequence[SiteRecordV1]: ...


@runtime_checkable
class PlanRow(Protocol):
    """The persisted plan, read back. :class:`commons.repository.PlanRecord` satisfies it.

    Structural for the same reason :class:`PersistOutcome` is: ``planner/`` names no
    storage type, so the ``done`` frame can report the row's **actual** id and state rather
    than the state the pipeline hoped it wrote.
    """

    @property
    def id(self) -> UUID: ...

    @property
    def status(self) -> str: ...


@runtime_checkable
class PlanStore(Protocol):
    """The durable HITL pause. Injected, so ``planner/`` stays storage-agnostic.

    Two calls rather than one, because the ``user_plan`` row genuinely has two states in
    this stream: :meth:`create` inserts it ``proposing`` (nothing has judged it yet, and the
    database's post-approval `CHECK` makes an unchecked plan unapprovable), and
    :meth:`record_feasibility` writes the verdict and advances it to ``proposed``. Collapsing
    them would mean either inserting a row already claiming a verdict it does not have, or
    holding the proposal in memory until the check finished — and the pause is durable
    precisely so it survives the process (SC-003).

    ``commons.repository.create_plan`` / ``record_feasibility`` are the implementations;
    the API adapts them by binding the session and the auth subject, which is also where the
    transaction boundary stays (the repository flushes, it does not commit).
    """

    def create(self, itinerary: ItineraryV1) -> PlanRow: ...

    def record_feasibility(
        self, plan_id: UUID, *, feasible: bool, violations: Sequence[str]
    ) -> PlanRow | None: ...


class PlanNotRecorded(RuntimeError):
    """The verdict did not reach the row, so the stream must not claim it did.

    ``record_feasibility`` returns ``None`` when its conditional ``UPDATE`` matched zero
    rows — the row was concurrently superseded, approved or deleted between the insert and
    the check. Swallowing that would stream ``feasibility``/``done`` over a row whose stored
    verdict is something else entirely; the plan the client is shown and the plan the
    approval gate reads would be two different things.
    """


def _routing_provider(legs: Sequence[RouteLegV1]) -> str | None:
    """Which engine routed the day, read off the legs' **own stamp** — never named here.

    ``commons.routing.routing_source`` writes the engine into ``SourceRef.id`` as
    ``<engine>:<costing>`` (`docs/data/route-leg.md`), so this frame reports what actually
    produced these legs. A literal ``"valhalla"`` would keep saying Valhalla after the
    provider was re-pointed — the exact drift a provenance stamp exists to prevent.
    ``None`` when the day has no leg: nothing routed, so there is no engine to name.
    """
    return legs[0].source.id.split(":", 1)[0] if legs else None


def _route_event(proposal: Proposal) -> Event:
    """The ``route`` frame: how many legs held, and which stops the network could not reach.

    ``excluded`` carries ``site_id`` + ``reason`` per the contract. A stop is *dropped* here,
    never straight-lined (FR-003), so this frame is the only place the client learns a
    chosen place fell out of the day.
    """
    legs = proposal.itinerary.legs
    return Event(
        "status",
        {
            "phase": "route",
            "provider": _routing_provider(legs),
            "legs": len(legs),
            "excluded": [
                {"site_id": str(stop.site_id), "reason": stop.reason} for stop in proposal.excluded
            ],
        },
    )


def run_plan(
    request: PlanRequest,
    *,
    load_sites: LoadSites,
    store: PlanStore,
    router: ModelRouter,
    routing: RoutingProvider,
    costing: PedestrianCosting,
    max_stops: int = DEFAULT_MAX_STOPS,
    dwell_min: int = DEFAULT_DWELL_MIN,
) -> Iterator[Event]:
    """Propose one day over ``request``'s area and pause it at the approval gate.

    Frames, in order (`contracts/plans.md`): ``status`` ×3 → ``itinerary`` → ``feasibility``
    → ``done``. The stream is the frames as they happen, not a list assembled and then
    replayed — a client that disconnects simply stops pulling.

    **Persistence precedes the frame that claims it**, exactly as :func:`run_research`
    persists before streaming a ``site``: the row is inserted before the ``itinerary`` frame
    and the verdict is written before the ``feasibility`` frame, so nothing the client is
    shown is a plan a failed write silently dropped. That is also why ``store`` is required
    while :func:`run_research`'s ``persist`` is optional — the ``done`` frame names a
    ``plan_id`` and a state, and those are claims about a durable row that survives a
    restart (SC-003). A stream that could emit them with nothing written would be a claim
    with nothing behind it.

    :param load_sites: the commons records a stop may reference. The **only** source of
        candidates: the planner invents no place (FR-002).
    :param costing: the traveller's pace, explicitly — no default (ADR-0020), because a
        defaulted pace silently reprices every leg of the day.

    Raises:
        ProposalRefused: the model answered nothing usable **while candidates existed**.
            Deliberately not caught, and deliberately not degraded into an empty plan: an
            empty day is trivially within every budget, so it would reach the approval gate
            green (see :mod:`planner.nodes.propose_itinerary`). It escapes after the
            ``load_sites`` frame and before anything is written, so no ``user_plan`` row
            exists to approve and the transport turns it into an in-band ``error`` frame
            the way ``api/areas.py`` already does — the status code is long gone by then.
        PlanNotRecorded: the verdict did not reach the row.
        RoutingError / RoutingUnavailable: the routing engine itself failed, as distinct
            from "no path to this one place", which is a fact about the plan and is handled.
    """
    candidates = tuple(load_sites(request.area_id))
    yield Event(
        "status",
        {
            "phase": "load_sites",
            "area_id": str(request.area_id),
            "candidates": len(candidates),
        },
    )

    proposal = propose_itinerary(
        candidates,
        router=router,
        routing=routing,
        costing=costing,
        user_id=request.user_id,
        area_id=request.area_id,
        day=request.day,
        day_start=request.day_start,
        budgets=request.budgets,
        lang=request.lang,
        interests=request.interests,
        max_stops=max_stops,
        dwell_min=dwell_min,
    )
    itinerary = proposal.itinerary
    yield Event(
        "status",
        {
            "phase": "propose_itinerary",
            # The **tier**, not the model. ADR-0004's seam exists so no call site names a
            # model, and `contracts/plans.md` calls this "the Opus tier … ADR-0004's `plan`
            # tier" — the same fact. A literal "opus" here would go stale the day the tier
            # is re-pinned and would be a model name in `planner/`, which is the one thing
            # the seam forbids.
            "tier": TaskTier.PLAN.value,
            "stops": len(itinerary.stops),
            # What the model offered and was refused — an object, a number, an unknown or a
            # repeated id (FR-004). Nothing else carries these to the client, and a refusal
            # that is invisible is indistinguishable from a model that never tried.
            "rejected": list(proposal.rejected),
        },
    )
    yield _route_event(proposal)

    created = store.create(itinerary)
    yield Event("itinerary", itinerary.model_dump(mode="json"))

    verdict = check_feasibility(
        itinerary,
        sites={record.id: record for record in candidates},
        timezone=request.timezone,
        country_code=request.country_code,
    )
    # The verdict is *returned*, never attached: `ItineraryV1` is `extra="forbid"` and
    # ADR-0025 ruling 3 puts `feasible` + `violations` on the row. This is the one place
    # that carries it from the checker to the row.
    violations = verdict.messages if itinerary.stops else (EMPTY_DAY_VIOLATION, *verdict.messages)
    recorded = store.record_feasibility(created.id, feasible=not violations, violations=violations)
    if recorded is None:
        raise PlanNotRecorded(
            f"the feasibility verdict for plan {created.id} matched no row: the plan was "
            "superseded, approved or removed while it was being checked"
        )

    yield Event("feasibility", {"ok": not violations, "violations": list(violations)})
    yield Event("done", {"plan_id": str(recorded.id), "state": recorded.status})
