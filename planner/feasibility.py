"""T018 — the deterministic feasibility verdict: *can this day actually be walked?*

Three checks, one verdict, **named violations** — some of which block approval and some of
which only warn (FR-005 / `data-model.md` §7 rule 1):

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
(``feasible`` + ``violations``), so the caller persists :attr:`FeasibilityVerdict.ok` into
the first and **both** :attr:`FeasibilityVerdict.violation_messages` and
:attr:`FeasibilityVerdict.warning_messages` into the second — severity-stamped, so the two
come back apart (`commons/repository.py`). A plan is a description of a day; whether it
holds together is server state about that description.

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

**``hours_unknown`` warns; ``outside_opening_window`` blocks** (ADR-0022, amended
2026-08-14). *"We do not know"* and *"we know it is shut"* are different facts, and only the
second is a reason to refuse a day. ``hours_unknown`` is still a first-class third outcome
and still emphatically not "probably open" (:mod:`commons.opening_hours` is unchanged and
must stay so) — it is still *named*, per stop, in the verdict. What changed is its weight:
it is an **advisory** :class:`Violation`, :attr:`Violation.blocking` is ``False``, and
:attr:`FeasibilityVerdict.ok` ignores it.

The measurement that forced the change: most OSM/Overture records carry no ``opening_hours``
tag at all — **1 of 25 records in the fixture set**, and a live 6-stop day over 599
candidates produced a ``no_expression`` violation on **every** stop. Blocking on that does
not protect a traveller from a locked door; it means no real day is ever approvable, and a
gate that refuses everything is indistinguishable from a broken one. The accepted cost is
stated where it lands: a traveller can approve a day containing places that may be shut, so
the warning has to reach them **per stop** rather than as one aggregate line — which is why
each unknown stop keeps its own :class:`Violation` with its own ``stop_order`` rather than
being folded into a count.

Two things that did **not** move. ``outside_opening_window`` still blocks — a stop the
evaluator positively answered *closed* for is the case the checker exists for. And
``unknown_site`` still blocks: a stop we cannot resolve is not "hours unknown", it is a plan
referencing a place that is not in the commons, and the missing fact there is the *place*,
not its hours.

The area frame (``timezone`` / ``country_code``) is nullable on the ``area`` row for rows
that predate it (`commons/db.py`), so ``None`` arrives here in normal operation: it is
*unresolved*, it reaches the traveller as ``hours_unknown`` — and now as a warning — and it
is never defaulted to UTC or to a country. Guessing a frame would produce a confident wrong
*answer*; warning produces an honest absence of one.

That is also where the "T018 coverage gate" of `pyproject.toml` is discharged: a
``PH``-bearing expression in a country the evaluator has no calendar for arrives here as
``hours_unknown``/``country_not_supported`` and is surfaced rather than guessed. It is
deliberately **not** re-asked of ``holidays`` — :func:`commons.frame.holiday_countries`
covers every code the resolver can produce, so wired as a gate it would pass everything, and
a second oracle inside the checker would be re-opening ADR-0022.

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
from typing import Final, Literal
from uuid import UUID

from commons.models import ItineraryV1, SiteRecordV1
from commons.opening_hours import evaluate

__all__ = [
    "ADVISORY_CODES",
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

#: The codes that **warn without blocking approval** — everything else refuses the day.
#:
#: Severity is derived from the code rather than passed per instance, and that is the point:
#: "is this a reason to refuse the day" is a property of *what was found*, not of who found
#: it. A per-call flag would let one call site raise ``outside_opening_window`` as advisory
#: and another raise it as blocking, and the two would disagree about the same day. Keeping
#: it here also means the closed :data:`ViolationCode` set and the severity table are one
#: table, so adding a code without deciding its severity is not expressible.
#:
#: ``hours_unknown`` is the only member, and deliberately so — see the module docstring for
#: the measurement. ``unknown_site`` is **not** advisory: what is missing there is the place.
ADVISORY_CODES: Final[frozenset[ViolationCode]] = frozenset({"hours_unknown"})

#: Budgets are compared with a *float-artefact* guard, not a grace margin. Summing eight
#: leg distances accumulates error around 1e-10 m on a 4 km day, and a plan reported as
#: "walking_m 4000 > budget 4000" would be a defect wearing a violation's clothes. The
#: tolerance is relative and tiny on purpose: it absorbs the representation and nothing
#: else, so a plan 1 m over budget is still a plan 1 m over budget.
_FLOAT_ARTEFACT_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class Violation:
    """One named thing found wrong with this plan — blocking approval, or merely warning."""

    code: ViolationCode
    #: The sentence persisted into ``user_plan.violations`` and streamed to the client.
    #: **Server-computed prose only** (ADR-0030 A1): no ``opening_hours`` expression, name,
    #: address or other commons-derived fragment may appear here, because this string reaches
    #: the DOM outside the attribution funnel that would credit one.
    message: str
    #: The :attr:`~commons.models.Stop.order` this concerns; ``None`` for a whole-day budget.
    #: Positions, never a site UUID — the same addressing the timeline and the legs use.
    stop_order: int | None = None

    @property
    def blocking(self) -> bool:
        """Does this refuse the day, or only warn about it? See :data:`ADVISORY_CODES`."""
        return self.code not in ADVISORY_CODES


@dataclass(frozen=True, slots=True)
class FeasibilityVerdict:
    """The answer plus the two totals it was computed from.

    The totals are returned even when the verdict is ``ok``: "how much walking is this day"
    is what a user adjusts a budget against, and recomputing it in the API would be a second
    implementation of the one piece of arithmetic this module exists to own.
    """

    #: **Everything found, both severities**, in the order it was found. Nothing is dropped
    #: here and nothing is partitioned away — a caller that wants one side asks for it.
    violations: tuple[Violation, ...]
    #: ``Σ legs.distance_m``, metres.
    walking_m: float
    #: First stop's start to the last stop's end, hours — midnight rollovers included.
    elapsed_hours: float

    @property
    def blocking(self) -> tuple[Violation, ...]:
        """The ones that refuse the day. This tuple being empty is exactly :attr:`ok`."""
        return tuple(violation for violation in self.violations if violation.blocking)

    @property
    def advisory(self) -> tuple[Violation, ...]:
        """The ones that only warn — today, every one of them is ``hours_unknown``."""
        return tuple(violation for violation in self.violations if not violation.blocking)

    @property
    def ok(self) -> bool:
        """``user_plan.feasible``. **No *blocking* violation is what makes a day feasible.**

        A warning deliberately does not enter this predicate. That is the whole of the
        2026-08-14 amendment to ADR-0022, expressed as one line of code: the checker still
        reports every unevaluable stop, and the approval gate stops refusing days over them.
        """
        return not self.blocking

    @property
    def messages(self) -> tuple[str, ...]:
        """Every message, both severities — the combined view, so nothing is lost."""
        return tuple(violation.message for violation in self.violations)

    @property
    def violation_messages(self) -> tuple[str, ...]:
        """``user_plan.violations`` / the ``feasibility`` frame's ``violations[]``: blocking."""
        return tuple(violation.message for violation in self.blocking)

    @property
    def warning_messages(self) -> tuple[str, ...]:
        """The ``feasibility`` frame's ``warnings[]``: advisory, and never a reason to refuse."""
        return tuple(violation.message for violation in self.advisory)


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

    **Two outcomes with two weights.** A minute the evaluator answers ``closed`` for is an
    ``outside_opening_window`` violation and refuses the day; a minute it cannot answer at all
    is an ``hours_unknown`` warning and does not (:data:`ADVISORY_CODES`). Both are still
    raised per stop, so "may be shut" is never silently rounded down to "fine".

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
            #
            # **This one is advisory too, and that was decided rather than inherited.** It is
            # the strongest candidate for an exception: a missing frame means *every* stop's
            # wall-clock check was skipped — one systemic gap, not N independent unknowns —
            # and unlike an untagged place it is our data that is missing, so the traveller
            # cannot resolve it at all. It stays advisory anyway, for three reasons. It is the
            # *purest* case of "we do not know", which is the whole distinction A12 draws.
            # Blocking would reinstate exactly the defect A12 removed, for a whole class of
            # areas at once, and hand the user an unapprovable day they have no way to fix —
            # the gate nobody can pass. And expressing it would need either a sixth
            # `ViolationCode` (a contract change, since the web renders per kind) or a
            # per-instance severity flag, which :data:`ADVISORY_CODES` deliberately makes
            # inexpressible so severity cannot differ between two call sites raising one code.
            #
            # What makes that affordable: `POST /areas` now persists the frame, so a `None`
            # here is a **legacy row**, not a live bug — rare, and fixed by re-resolving the
            # area rather than by refusing the day. The message says the *area* has no frame,
            # so the systemic case reads differently from a per-place one.
            violations.append(
                Violation(
                    code="hours_unknown",
                    message=(
                        f"stop {stop.order} hours cannot be evaluated (no_timezone): the "
                        "area carries no local frame, so no wall-clock check is possible — "
                        "check before you go"
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
                            "so it may be shut when you arrive — check before you go"
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
