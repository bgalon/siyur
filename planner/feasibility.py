"""T018 — the deterministic feasibility verdict: *can this day actually be walked?*

Three checks, one verdict, **named violations** (FR-005 / `data-model.md` §7 rule 1):

1. **Walking** — ``Σ legs.distance_m`` against :attr:`~commons.models.Budgets.walking_m`.
2. **Elapsed** — the span from the first stop's start to the last stop's end, against
   :attr:`~commons.models.Budgets.hours`.
3. **Opening windows** — every minute a traveller is standing at a place, that place is
   *open*, evaluated in **area-local** wall clock by :func:`commons.opening_hours.evaluate`.

**All the arithmetic of the plan lives here.** The model contributes an ordering of site ids
(:mod:`planner.nodes.propose_itinerary`) and nothing else; Valhalla contributes metres and
seconds (:mod:`commons.routing`); this module is the only place that adds them up and
compares them to a budget. That is FR-004 stated as a code layout rather than as a hope.

## The verdict is not a field of the plan

:class:`FeasibilityVerdict` is returned, never attached. ``ItineraryV1`` is
``extra="forbid"`` and ADR-0025 ruling 3 puts the verdict on the ``user_plan`` row
(``feasible`` + ``violations``), so the caller persists :attr:`FeasibilityVerdict.ok` and
:attr:`FeasibilityVerdict.messages` into those two columns. A plan is a description of a
day; whether it holds together is server state about that description.

## Two things this module refuses to be clever about

**A day that runs past midnight is measured forwards.** ``Timeline`` deliberately does not
validate its entries as ascending, and its docstring hands the question here: a stop whose
``planned_start`` is *earlier* than its predecessor's has rolled over to the next day, and
:func:`_local_starts` advances the calendar day rather than producing a negative interval.
Subtracting wall-clock times naively would report a 23-hour day as ``-1`` hours and pass
every budget it breached. The calendar day matters twice over — the second day's
``opening_hours`` rules (a different weekday, possibly a different ``PH``) are what a stop
after midnight must be checked against.

**A verdict carries no commons-derived text** (ADR-0030 A1). :attr:`Violation.message` is
*server-computed prose about a plan*, not a rendering of data — so it names positions
(``stop 2``) and never an ``opening_hours`` expression, a place name or an address. Those are
ODbL values, and the funnel that renders them attaches their attribution chip; a verdict
string reaches the DOM through a different path, where the same fragment would arrive with no
chip in frame. The stop's *own* ``opening_hours`` value already travels beside the verdict and
already carries its credit, so nothing is lost by the verdict staying quiet about it. The
casualty is :attr:`~commons.opening_hours.HoursEvaluation.detail`, which is our own text for
every reason except ``unparseable`` — where it is the parser's caret diagram with the raw
expression embedded in it. One reason leaking is the whole rule failing, so the field is
dropped from the message entirely rather than filtered per reason.

**``hours_unknown`` blocks.** It is a first-class third outcome, not "probably open"
(:mod:`commons.opening_hours`), and it is named as a violation exactly like a budget
breach. The evaluator fails closed for a reason — a ``PH``-bearing expression with no
country, an unparseable tag, a place with no hours at all — and a planner that swallowed any
of those would ship a traveller to a locked door with a green tick beside it. The area frame
(``timezone`` / ``country_code``) is nullable on the ``area`` row for rows that predate it
(`commons/db.py`), so ``None`` arrives here in normal operation: it is *unresolved*, it
reaches the traveller as ``hours_unknown``, and it is never defaulted to UTC or to a country.

That is also where the "T018 coverage gate" of `pyproject.toml` is discharged: a
``PH``-bearing expression in a country the evaluator has no calendar for arrives here as
``hours_unknown``/``country_not_supported`` and blocks. It is deliberately **not** re-asked
of ``holidays`` — :func:`commons.frame.holiday_countries` covers every code the resolver can
produce, so wired as a gate it would pass everything, and a second oracle inside the checker
would be re-opening ADR-0022.

## Two hazards this module cannot close on its own

**A plan with no stops walks zero metres in zero hours and is therefore ``ok``.** That is
arithmetically right and operationally dangerous, because an empty day would then pass the
approval gate. Nothing here can tell "the area held nothing" from "the proposal failed", so
:mod:`planner.nodes.propose_itinerary` is where that distinction is kept: it raises rather
than returning an empty day. A caller that builds an ``ItineraryV1`` by some other route owes
the same check before it trusts a green verdict.

**The schedule is never checked against the legs it was laid over.** A stop may begin before
the previous stop's dwell plus the routed walk could possibly have delivered the traveller
there, and this module still calls the day ``ok`` — it measures the times it is given, it does
not ask whether they are reachable. Unreachable today, because
:func:`planner.nodes.propose_itinerary._schedule` builds every plan by walking that arithmetic
forwards, so the only starts that exist are ones the legs support. It becomes reachable the
moment a user edits a time, and the check belongs with that slice: ``docs/data/itinerary.md``
scopes T018 to the two budgets and the opening windows, and inventing a fourth violation code
here would ship an API contract (`contracts/plans.md`) ahead of the feature that needs it.
:func:`_local_starts` uses the leg durations for the one thing that is not this check —
deciding which *calendar day* a stop falls on — and is careful to say why below.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from commons.models import ItineraryV1, SiteRecordV1
from commons.opening_hours import evaluate

__all__ = [
    "FeasibilityVerdict",
    "Violation",
    "ViolationCode",
    "check_feasibility",
]

#: What was breached. A **closed** set, because the API returns these to a client and the
#: web app renders an affordance per kind (`contracts/plans.md`); free text would make the
#: renderer parse prose. The human sentence lives in :attr:`Violation.message`.
ViolationCode = Literal[
    "walking_budget",
    "time_budget",
    "outside_opening_window",
    "hours_unknown",
    "unknown_site",
]

#: Budgets are compared with a *float-artefact* guard, not a grace margin. Summing eight
#: leg distances accumulates error around 1e-10 m on a 4 km day, and a plan reported as
#: "walking_m 4000 > budget 4000" would be a defect wearing a violation's clothes. The
#: tolerance is relative and tiny on purpose: it absorbs the representation and nothing
#: else, so a plan 1 m over budget is still a plan 1 m over budget.
_FLOAT_ARTEFACT_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class Violation:
    """One named reason this plan may not be approved."""

    code: ViolationCode
    #: The sentence persisted into ``user_plan.violations`` and streamed to the client.
    #: **Server-computed prose only** (ADR-0030 A1): no ``opening_hours`` expression, name,
    #: address or other commons-derived fragment may appear here, because this string reaches
    #: the DOM outside the attribution funnel that would credit one.
    message: str
    #: The :attr:`~commons.models.Stop.order` this concerns; ``None`` for a whole-day budget.
    #: Positions, never a site UUID — the same addressing the timeline and the legs use.
    stop_order: int | None = None


@dataclass(frozen=True, slots=True)
class FeasibilityVerdict:
    """The answer plus the two totals it was computed from.

    The totals are returned even when the verdict is ``ok``: "how much walking is this day"
    is what a user adjusts a budget against, and recomputing it in the API would be a second
    implementation of the one piece of arithmetic this module exists to own.
    """

    violations: tuple[Violation, ...]
    #: ``Σ legs.distance_m``, metres.
    walking_m: float
    #: First stop's start to the last stop's end, hours — midnight rollovers included.
    elapsed_hours: float

    @property
    def ok(self) -> bool:
        """``user_plan.feasible``. No violations is the *only* way to be feasible."""
        return not self.violations

    @property
    def messages(self) -> tuple[str, ...]:
        """``user_plan.violations`` / the SSE ``feasibility`` frame's ``violations[]``."""
        return tuple(violation.message for violation in self.violations)


def _local_starts(itinerary: ItineraryV1) -> tuple[datetime, ...]:
    """Each stop's start as a **naive area-local** ``datetime``, rolling over at midnight.

    ``ItineraryV1.date`` plus ``Stop.planned_start`` is the complete instant (the card's
    "Timezone" note); the only thing that has to be *decided* is what a start earlier than
    its predecessor means, and the answer is "the next day" — an ordered day is not
    required to be an ascending one.

    **Two rollover rules, because one of them under-counts in the dangerous direction.**

    The wall clock alone only reveals a wrap that leaves it *descending*: 22:00 → 01:00 is
    visibly the next day. A wrap of a whole multiple of 24 hours leaves the clock ascending
    and is invisible — 10:00 then 11:00 reads as one hour whether the second stop is an hour
    later or twenty-five, and the short reading is the one that fits inside a budget. Missing
    it under-counts the day by exactly 24 hours per missed wrap, which is a green tick on a
    day nobody could walk.

    So the day is also caught up against the earliest instant the traveller could *be* at each
    stop — the previous stop's dwell plus the routed leg into this one — and the catch-up is
    taken in **whole days only** (floor division, never a rounding up). That is the line that
    keeps this from becoming the schedule-vs-legs check the module docstring says is out of
    scope: a stop scheduled five minutes earlier than the walk allows is left exactly where it
    is, unremarked, because rolling it a whole day forward would answer a small scheduling
    error with a 24-hour lie. Only a deficit that is *itself* a whole day or more is a wrap,
    and a wrap is the only thing being recovered here.

    A missing leg contributes zero, which degrades to the wall-clock rule alone — the honest
    behaviour, since a plan with no legs offers no lower bound to catch up against.

    **What this deliberately does not recover.** A wrap the legs do not *prove* stays hidden.
    Two stops an hour apart on the clock with a nine-hour walk between them are unreachable as
    scheduled, but the deficit is under a day, so nothing here rolls them: the plan is read as
    the 1.75 hours it says. That is not the wrap rule falling short — it is the schedule-vs-legs
    gap the module docstring names, wearing a different hat, and closing it means a violation
    for "this stop cannot be reached", which is the next slice's check and the next slice's
    ``ViolationCode``. Guessing a 24-hour wrap to stand in for it would report a five-minute
    scheduling error as a day and a quarter.
    """
    #: Legs address stops by position, so the leg *into* stop ``n`` is the one ending there.
    leg_into = {leg.to_stop: leg for leg in itinerary.legs}
    starts: list[datetime] = []
    day_offset = 0
    previous_end: datetime | None = None

    for index, stop in enumerate(itinerary.stops):
        if index and stop.planned_start < itinerary.stops[index - 1].planned_start:
            day_offset += 1
        start = datetime.combine(itinerary.date, stop.planned_start) + timedelta(days=day_offset)

        if previous_end is not None:
            leg = leg_into.get(stop.order)
            earliest = previous_end + timedelta(seconds=leg.duration_s if leg is not None else 0)
            missed_wraps = (earliest - start) // timedelta(days=1)
            if missed_wraps > 0:
                day_offset += missed_wraps
                start += timedelta(days=missed_wraps)

        starts.append(start)
        previous_end = start + timedelta(minutes=stop.dwell_min)

    return tuple(starts)


def _exceeds(total: float, budget: float) -> bool:
    """``total > budget``, with float summation artefacts excluded (see the tolerance)."""
    return total > budget and not math.isclose(total, budget, rel_tol=_FLOAT_ARTEFACT_TOLERANCE)


def _check_hours(
    itinerary: ItineraryV1,
    starts: tuple[datetime, ...],
    sites: Mapping[UUID, SiteRecordV1],
    *,
    timezone: str | None,
    country_code: str | None,
) -> list[Violation]:
    """One violation at most per stop: the first minute of the dwell that is not *open*.

    **Every minute of ``[planned_start, planned_start + dwell_min)`` is evaluated**, not just
    the two endpoints. A place tagged ``Mo-Fr 09:00-12:00,13:00-17:00`` is open at 11:30 and
    open at 13:30, and shut for the hour between — an endpoint check calls that stop feasible
    and sends the traveller to a closed door in the middle of their visit. Minute granularity
    is exhaustive rather than a sample, because both the schema (``HH:MM`` starts,
    ``dwell_min`` integers) and OSM's ``opening_hours`` grammar are minute-granular. Measured
    cost is **~10 µs per minute evaluated, end to end through**
    :func:`commons.opening_hours.evaluate`
    (2,000 iterations of ``Mo-Su 09:00-12:00,13:00-17:00``, Python 3.12 / darwin), so a
    two-hour dwell is about 1.2 ms. Almost all of it is construction, not evaluation:
    ``OpeningHours.state()`` alone measures ~0.6 µs, and ``evaluate`` builds a fresh
    ``OpeningHours`` per call, so a 45-minute dwell re-parses one expression 45 times. Caching
    that parse would buy roughly 17× here and is deliberately not done — the seam that would
    hold the cache is :mod:`commons.opening_hours`, not this module, and a millisecond a day
    is not yet worth a cache with an invalidation question attached to it.

    The interval is **half-open**. A stop of 09:00 + 60 min at a place open ``09:00-10:00``
    fits exactly; evaluating the closing instant itself would report every perfectly-fitted
    stop as a violation. A zero-minute dwell still evaluates its single arrival instant.

    **No message here quotes the expression it evaluated** — see the module docstring's
    ADR-0030 A1 note. The reason code is ours; the tag is the commons'.
    """
    violations: list[Violation] = []
    for stop, start in zip(itinerary.stops, starts, strict=True):
        site = sites.get(stop.site_id)
        if site is None:
            # The planner invents no place (FR-002), and neither does the checker: a stop we
            # cannot resolve is not silently skipped, because skipping it is indistinguishable
            # from checking it and finding it open.
            violations.append(
                Violation(
                    code="unknown_site",
                    message=(
                        f"stop {stop.order} references site {stop.site_id} which is not in "
                        "the commons, so its opening hours cannot be checked"
                    ),
                    stop_order=stop.order,
                )
            )
            continue

        if timezone is None:
            # `None` on the area row means *unresolved*, and unresolved reaches the traveller
            # as hours_unknown (commons/db.py). Substituting UTC here would make every window
            # check answer confidently in the wrong frame.
            violations.append(
                Violation(
                    code="hours_unknown",
                    message=(
                        f"stop {stop.order} hours cannot be evaluated (no_timezone): the "
                        "area carries no local frame, so no wall-clock check is possible"
                    ),
                    stop_order=stop.order,
                )
            )
            continue

        expression = site.opening_hours.value if site.opening_hours is not None else None
        for minute in range(max(stop.dwell_min, 1)):
            when = start + timedelta(minutes=minute)
            result = evaluate(
                expression,
                when,
                timezone=timezone,
                country_code=country_code,
                # The place's own stamped point. Inert today — the evaluator refuses sun
                # events unconditionally (ADR-0022 A6) — but this is the argument that
                # becomes load-bearing if solar computation is ever turned back on, and
                # passing it here keeps that a one-line change in one module.
                location=site.location.value,
            )
            if result.state == "open":
                continue
            if result.state == "closed":
                violations.append(
                    Violation(
                        code="outside_opening_window",
                        message=(
                            f"stop {stop.order} is outside its opening window — closed "
                            f"at {when:%H:%M} on {when:%Y-%m-%d} (area-local)"
                        ),
                        stop_order=stop.order,
                    )
                )
            else:
                violations.append(
                    Violation(
                        code="hours_unknown",
                        message=(
                            f"stop {stop.order} hours cannot be evaluated ({result.reason}), "
                            "so this day cannot be approved over them"
                        ),
                        stop_order=stop.order,
                    )
                )
            break
    return violations


def check_feasibility(
    itinerary: ItineraryV1,
    *,
    sites: Mapping[UUID, SiteRecordV1],
    timezone: str | None,
    country_code: str | None,
) -> FeasibilityVerdict:
    """Judge ``itinerary`` against its own budgets and its sites' opening hours.

    :param sites: the commons records the stops reference, keyed by
        :attr:`~commons.models.SiteRecordV1.id`. A stop whose site is absent is a named
        ``unknown_site`` violation, never a skipped check.
    :param timezone: the ``area`` row's IANA id, or ``None`` when that row has no frame yet.
    :param country_code: the ``area`` row's ISO 3166-1 alpha-2 code, or ``None``. Passed
        straight to :func:`commons.opening_hours.evaluate`, which is what turns a
        ``PH``-bearing expression with no country into ``hours_unknown`` — that refusal is
        the evaluator's and is deliberately not second-guessed here.

    Pure, offline and clock-free: it reads no database, opens no socket and never calls
    ``now()``. The instant every check runs at comes from ``itinerary.date`` plus each
    stop's ``planned_start``, so the same plan yields the same verdict for ever.
    """
    starts = _local_starts(itinerary)

    walking_m = math.fsum(leg.distance_m for leg in itinerary.legs)
    elapsed_hours = 0.0
    if itinerary.stops:
        end = starts[-1] + timedelta(minutes=itinerary.stops[-1].dwell_min)
        elapsed_hours = (end - starts[0]).total_seconds() / 3600.0

    violations: list[Violation] = []
    if _exceeds(walking_m, itinerary.budgets.walking_m):
        violations.append(
            Violation(
                code="walking_budget",
                message=(f"walking_m {walking_m:.0f} > budget {itinerary.budgets.walking_m:.0f}"),
            )
        )
    if _exceeds(elapsed_hours, itinerary.budgets.hours):
        violations.append(
            Violation(
                code="time_budget",
                message=f"hours {elapsed_hours:.2f} > budget {itinerary.budgets.hours:.2f}",
            )
        )
    violations.extend(
        _check_hours(itinerary, starts, sites, timezone=timezone, country_code=country_code)
    )

    return FeasibilityVerdict(
        violations=tuple(violations), walking_m=walking_m, elapsed_hours=elapsed_hours
    )
