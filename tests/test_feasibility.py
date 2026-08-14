"""T019 — the feasibility verdict. Tier 1: pure arithmetic, no clock, no network, no model.

Every test here is written against a bug that would otherwise ship green. The four the
brief names — each budget breached independently, an opening-window breach, and
``hours_unknown`` **surfacing** rather than passing silently — are joined by three the
implementation would otherwise get quietly wrong:

* **the interior of a dwell**, because a place shut for an hour in the middle of a visit is
  open at both endpoints and an endpoint check calls that feasible;
* **the closing instant**, because treating ``[start, start+dwell]`` as closed-ended turns
  every perfectly-fitted stop into a violation;
* **a day that crosses midnight**, because subtracting wall-clock times backwards reports a
  four-hour day as minus twenty, which passes every budget it broke;
* **a day that crosses midnight *invisibly***, because a wrap of a whole 24 hours leaves the
  wall clocks ascending and under-counts the day by exactly a day.

Two of the assertions here were once satisfied by something other than the behaviour they
name, and both are now written against the guard itself:
:func:`~planner.feasibility._exceeds` is called directly rather than through a plan whose
arithmetic is bit-identical either way, and the first-shut-minute test asserts the phrase
``closed at 12:00`` rather than a ``12:00`` the echoed expression used to supply for free.

No instant is ever read from the system clock: the frame is a fixed ``DAY`` plus each stop's
own ``planned_start``, so a verdict here is the same verdict for ever.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any, get_args
from uuid import UUID, uuid4

import pytest
from shapely import LineString, Point

from commons.geo import Wgs84Point
from commons.models import (
    Budgets,
    ItineraryV1,
    RouteLegV1,
    SiteRecordV1,
    SourcedValue,
    SourceRef,
    Stop,
)
from commons.routing import routing_source
from planner.feasibility import (
    ADVISORY_CODES,
    FeasibilityVerdict,
    ViolationCode,
    _exceeds,
    check_feasibility,
)

#: A Friday — so ``Tu …`` is closed and ``Mo-Su …`` is open, with no reliance on "today".
DAY = date(2026, 8, 14)
OBSERVED = date(2026, 8, 1)
TIMEZONE = "Europe/Athens"
COUNTRY = "GR"

OSM = SourceRef(kind="osm", id="node/1", license="ODbL-1.0", attribution="© OpenStreetMap")
#: Any two distinct in-range points; feasibility never reads geometry, only leg *lengths*.
GEOMETRY = LineString([(28.2247, 36.4443), (28.2242, 36.4446), (28.2238, 36.4447)])


def site(*, hours: str | None = "Mo-Su 00:00-24:00") -> SiteRecordV1:
    """A commons record carrying only what feasibility reads: a point and its hours."""

    def stamp(value: object) -> SourcedValue[Any]:
        return SourcedValue[Any].stamp(
            value=value, source=OSM, confidence=0.9, observed_at=OBSERVED
        )

    return SiteRecordV1(
        location=SourcedValue[Wgs84Point].stamp(
            value=Point(28.2247, 36.4443), source=OSM, confidence=0.9, observed_at=OBSERVED
        ),
        opening_hours=stamp(hours) if hours is not None else None,
    )


def leg(index: int, distance_m: float, duration_s: int = 300) -> RouteLegV1:
    return RouteLegV1(
        id=f"leg-{index}",
        from_stop=index,
        to_stop=index + 1,
        geometry=GEOMETRY,
        distance_m=distance_m,
        duration_s=duration_s,
        source=routing_source(),
    )


def plan(
    schedule: list[tuple[UUID, time, int]],
    *,
    walking_m: float = 4000.0,
    hours: float = 6.0,
    distances: list[float] | None = None,
    durations: list[int] | None = None,
) -> ItineraryV1:
    """An itinerary from ``(site_id, planned_start, dwell_min)`` triples plus leg lengths.

    ``durations`` overrides the per-leg walking seconds, which matters only for the missed-wrap
    guard: everywhere else the legs are 5-minute walks and the clock is checkable by hand.
    """
    metres = distances or []
    seconds = durations or [300] * len(metres)
    return ItineraryV1(
        user_id="test-subject",
        area_id=uuid4(),
        date=DAY,
        lang="en",
        stops=tuple(
            Stop(site_id=site_id, order=order, planned_start=start, dwell_min=dwell)
            for order, (site_id, start, dwell) in enumerate(schedule)
        ),
        legs=tuple(
            leg(index, distance, duration)
            for index, (distance, duration) in enumerate(zip(metres, seconds, strict=True))
        ),
        budgets=Budgets(walking_m=walking_m, hours=hours),
    )


def verdict(
    itinerary: ItineraryV1,
    sites: dict[UUID, SiteRecordV1],
    *,
    country_code: str | None = COUNTRY,
    timezone: str | None = TIMEZONE,
) -> FeasibilityVerdict:
    return check_feasibility(itinerary, sites=sites, timezone=timezone, country_code=country_code)


def codes(result: FeasibilityVerdict) -> list[str]:
    return [violation.code for violation in result.violations]


# ── the feasible day ────────────────────────────────────────────────────────────────


def test_a_plan_inside_both_budgets_and_inside_its_opening_windows_is_feasible() -> None:
    """Also pins the **half-open** dwell: 09:00 + 60 min at a place open 09:00-10:00 fits.

    Evaluating the closing instant itself would report this — a stop that fits its window
    exactly — as a violation, which is the off-by-one that would make the checker unusable
    and would look like a working checker while doing it.
    """
    early, late = site(hours="Mo-Su 09:00-10:00"), site(hours="Mo-Su 11:00-17:00")
    sites = {early.id: early, late.id: late}
    itinerary = plan(
        [(early.id, time(9, 0), 60), (late.id, time(11, 0), 60)],
        walking_m=4000.0,
        hours=3.0,
        distances=[900.0],
    )

    result = verdict(itinerary, sites)

    assert result.ok, result.messages
    assert result.violations == ()
    assert result.walking_m == pytest.approx(900.0)
    assert result.elapsed_hours == pytest.approx(3.0), "09:00 to 12:00 is exactly the budget"


# ── the budgets, each breached on its own ───────────────────────────────────────────


def test_the_walking_budget_is_breached_on_its_own_and_named() -> None:
    open_site = site()
    itinerary = plan(
        [
            (open_site.id, time(10, 0), 60),
            (open_site.id, time(11, 30), 60),
            (open_site.id, time(13, 0), 60),
        ],
        walking_m=3000.0,
        hours=6.0,
        distances=[2100.0, 2100.0],
    )

    result = verdict(itinerary, {open_site.id: open_site})

    assert not result.ok
    assert codes(result) == ["walking_budget"], "the time budget and the hours are untouched"
    assert result.messages == ("walking_m 4200 > budget 3000",)
    assert result.violations[0].stop_order is None, "a budget is a fact about the whole day"


def test_the_time_budget_is_breached_on_its_own_and_named() -> None:
    open_site = site()
    itinerary = plan(
        [(open_site.id, time(9, 0), 60), (open_site.id, time(13, 0), 90)],
        walking_m=4000.0,
        hours=4.0,
        distances=[800.0],
    )

    result = verdict(itinerary, {open_site.id: open_site})

    assert not result.ok
    assert codes(result) == ["time_budget"], "the walking budget is untouched"
    assert result.messages == ("hours 5.50 > budget 4.00",)
    assert result.elapsed_hours == pytest.approx(5.5)


@pytest.mark.parametrize(
    ("total", "budget", "exceeded", "what"),
    [
        (4000.000000000001, 4000.0, False, "a representation artefact is absorbed"),
        (4000.0, 4000.0, False, "a budget met exactly is met"),
        (3999.0, 4000.0, False, "under budget is under budget"),
        (4001.0, 4000.0, True, "1 m over budget is still 1 m over budget"),
        (4000.001, 4000.0, True, "a millimetre past the tolerance is a breach"),
        (0.0, 0.0, False, "a zero budget met by a zero total (rel_tol on 0 is exact)"),
    ],
)
def test_a_budget_is_compared_with_a_float_artefact_guard_not_a_grace_margin(
    total: float, budget: float, exceeded: bool, what: str
) -> None:
    """The tolerance is asserted **on** :func:`_exceeds`, because a plan cannot reach it.

    The previous version of this test built a day of three 1000.1 m legs against a budget of
    ``1000.1 * 3`` and asserted the verdict was ``ok``. It could not fail: ``math.fsum`` returns
    the correctly-rounded sum of the exact reals and IEEE-754 multiplication returns the
    correctly-rounded exact product, so the two are **bit-identical** and ``total > budget`` is
    already ``False`` before the tolerance is consulted. Deleting the ``math.isclose`` clause
    from ``_exceeds`` left the whole suite green — the guard was untested for as long as it was
    only ever reached through a plan.
    """
    assert _exceeds(total, budget) is exceeded, what


def test_a_day_whose_legs_sum_to_its_budget_is_feasible() -> None:
    """The end-to-end half: three legs of 1000.1 m against a budget of ``1000.1 * 3`` hold."""
    open_site = site()
    itinerary = plan(
        [
            (open_site.id, time(10, 0), 30),
            (open_site.id, time(10, 40), 30),
            (open_site.id, time(11, 20), 30),
            (open_site.id, time(12, 0), 30),
        ],
        walking_m=1000.1 * 3,
        hours=3.0,
        distances=[1000.1, 1000.1, 1000.1],
    )

    assert verdict(itinerary, {open_site.id: open_site}).ok


# ── opening windows, in area-local time ─────────────────────────────────────────────


def test_a_stop_outside_its_opening_window_is_a_named_violation() -> None:
    """``Tu 09:00-14:00`` on a Friday: the weekday rule is read on the plan's own date."""
    closed = site(hours="Tu 09:00-14:00")
    itinerary = plan([(closed.id, time(10, 0), 60)], hours=6.0, distances=[])

    result = verdict(itinerary, {closed.id: closed})

    assert not result.ok
    assert codes(result) == ["outside_opening_window"]
    assert result.violations[0].stop_order == 0
    assert "stop 0 is outside its opening window" in result.messages[0]
    assert "closed at 10:00" in result.messages[0], "the offending instant is named"
    assert "2026-08-14" in result.messages[0], "and the day it falls on, since a day can wrap"


def test_a_closure_inside_the_dwell_is_caught_not_only_the_endpoints() -> None:
    """11:30 + 120 min against a lunchtime break: open at 11:30, open at 13:29, shut at noon.

    An implementation that evaluated only arrival and departure calls this stop feasible and
    walks the traveller into a locked door halfway through their visit.
    """
    lunchtime = site(hours="Mo-Su 09:00-12:00,13:00-17:00")
    itinerary = plan([(lunchtime.id, time(11, 30), 120)], hours=6.0)

    result = verdict(itinerary, {lunchtime.id: lunchtime})

    assert codes(result) == ["outside_opening_window"]
    # `closed at`, not a bare `12:00`: the message used to echo the expression, which already
    # contains `12:00`, so this assertion held even when the LAST shut minute was reported.
    assert "closed at 12:00" in result.messages[0], "the first shut minute is the one reported"


def test_a_day_that_crosses_midnight_is_measured_forwards() -> None:
    """22:00 → 01:00 is three hours later, not twenty-one hours earlier.

    ``Timeline`` deliberately does not require ascending starts and hands this question to
    feasibility. Subtracting the wall clocks naively yields a *negative* span, which passes
    every budget it breaks — green, and completely wrong.
    """
    always = site()
    itinerary = plan(
        [(always.id, time(22, 0), 60), (always.id, time(1, 0), 60)],
        hours=2.0,
        distances=[500.0],
    )

    result = verdict(itinerary, {always.id: always})

    assert result.elapsed_hours == pytest.approx(4.0)
    assert codes(result) == ["time_budget"]
    assert result.messages == ("hours 4.00 > budget 2.00",)


def test_a_wrap_the_legs_prove_is_caught_although_the_wall_clocks_still_ascend() -> None:
    """10:00 then 11:00, with a twenty-five-hour walk between them: 1.75 hours, or 25.75?

    The descending-wall-clock rule sees nothing here — 11:00 is after 10:00 — so the naive
    reading is 1.75 hours against a 6-hour budget: feasible, approved, compiled, and wrong by
    exactly 24 hours in the one direction that puts a green tick on a day nobody could walk.
    The routed leg is what settles it: the traveller cannot be at the second stop before
    10:45 + 25 h, so an 11:00 start is at least the *next* day's 11:00.

    ``walking_m`` is set high enough that the distance budget stays out of the way — this is a
    test about the clock, and a second violation would let it pass on the wrong one.
    """
    always = site()
    itinerary = plan(
        [(always.id, time(10, 0), 45), (always.id, time(11, 0), 45)],
        walking_m=200_000.0,
        hours=6.0,
        distances=[100_000.0],
        durations=[25 * 3600],
    )

    result = verdict(itinerary, {always.id: always})

    assert result.elapsed_hours == pytest.approx(25.75), "10:00 day one to 11:45 day two"
    assert codes(result) == ["time_budget"]
    assert result.messages == ("hours 25.75 > budget 6.00",)


def test_a_stop_a_few_minutes_earlier_than_the_walk_allows_is_not_rolled_a_whole_day() -> None:
    """The catch-up takes whole days only, so a small scheduling error stays small.

    A stop that starts before the previous dwell plus the routed walk could deliver the
    traveller is the schedule-vs-legs gap the module docstring names, and it is out of T018
    scope. What must **not** happen is answering a five-minute error with a 24-hour jump: the
    day would be reported as 25.58 hours over a 6-hour budget, which is a loudly wrong number
    standing in for a quietly missing check.
    """
    always = site()
    itinerary = plan(
        [(always.id, time(10, 0), 45), (always.id, time(10, 50), 45)],
        hours=6.0,
        distances=[900.0],
        durations=[600],  # ten minutes' walk from a 10:45 departure ⇒ 10:55, not 10:50
    )

    result = verdict(itinerary, {always.id: always})

    assert result.elapsed_hours == pytest.approx(1.5833333, rel=1e-6), "10:00 to 11:35"
    assert result.ok, "the un-walkable five minutes are the next slice's check, not a wrap"


# ── the severity split: "we don't know" is not "we know it's shut" ──────────────────


def test_a_stop_we_know_is_shut_blocks_and_a_stop_we_cannot_check_does_not() -> None:
    """The whole amendment in one assertion pair, over two otherwise identical days.

    ADR-0022 as amended on 2026-08-14: ``outside_opening_window`` refuses the day, because the
    evaluator positively answered *closed*; ``hours_unknown`` warns, because it answered
    nothing. Swap the two severities and **both halves of this test fail** — which is the
    point of asserting them together rather than in two files.

    Written against the failure that forced the change: most OSM/Overture records carry no
    ``opening_hours`` tag at all, so blocking on the second case meant no real day was ever
    approvable — a gate that refuses everything, which is indistinguishable from a broken one.
    """
    shut = site(hours="Tu 09:00-14:00")  # a Tuesday rule, and DAY is a Friday
    unchecked = site(hours=None)

    known_shut = verdict(plan([(shut.id, time(10, 0), 60)], hours=6.0), {shut.id: shut})
    unknown = verdict(plan([(unchecked.id, time(10, 0), 60)], hours=6.0), {unchecked.id: unchecked})

    assert codes(known_shut) == ["outside_opening_window"]
    assert not known_shut.ok, "a place we KNOW is shut still refuses the day"
    assert known_shut.violation_messages and known_shut.warning_messages == ()

    assert codes(unknown) == ["hours_unknown"]
    assert unknown.ok, "a place we cannot CHECK is a warning, not a refusal"
    assert unknown.warning_messages and unknown.violation_messages == ()
    assert unknown.messages == unknown.warning_messages, "nothing is lost from the combined view"


def test_a_warning_and_a_violation_on_the_same_day_are_kept_apart() -> None:
    """Both are reported, in one verdict, on opposite sides of the approval predicate.

    The day is refused — but for the budget, not for the stop nobody could check, and the
    two sentences must not arrive as one undifferentiated list. A partition that dropped the
    advisory entry to "simplify" the payload would silently un-warn the traveller about the
    one stop that might be locked.
    """
    unchecked = site(hours=None)
    itinerary = plan(
        [(unchecked.id, time(10, 0), 60), (unchecked.id, time(11, 30), 60)],
        walking_m=500.0,
        hours=6.0,
        distances=[2100.0],
    )

    result = verdict(itinerary, {unchecked.id: unchecked})

    assert not result.ok, "the walking budget is breached, and a budget still blocks"
    assert [v.code for v in result.blocking] == ["walking_budget"]
    assert [v.code for v in result.advisory] == ["hours_unknown", "hours_unknown"]
    assert len(result.messages) == 3, "the combined view keeps every sentence"
    assert set(result.messages) == set(result.violation_messages) | set(result.warning_messages)
    assert [v.stop_order for v in result.advisory] == [0, 1], "per stop, never one summary line"


def test_a_stop_the_commons_cannot_resolve_blocks_although_its_hours_are_unknown_too() -> None:
    """The distinction the amendment does **not** blur: the missing fact is the *place*.

    An unresolvable stop looks like an hours problem from inside :func:`_check_hours` — no
    record, therefore no expression — and treating it as one would let a plan referencing a
    place that is not in the commons reach the approval gate green.
    """
    missing_id = uuid4()
    result = verdict(plan([(missing_id, time(10, 0), 60)], hours=6.0), {})

    assert codes(result) == ["unknown_site"]
    assert not result.ok
    assert result.warning_messages == ()


def test_every_violation_code_has_a_severity_and_only_hours_unknown_is_advisory() -> None:
    """Mechanical, and deliberately so: a new code cannot arrive without a severity decision.

    :data:`~planner.feasibility.ADVISORY_CODES` is a subset of the closed
    :data:`~planner.feasibility.ViolationCode` set, so severity is decided by the same table
    that declares the code — there is no second list to forget to update.
    """
    all_codes = set(get_args(ViolationCode))
    assert ADVISORY_CODES <= all_codes, "an advisory code that is not a violation code"
    assert set(ADVISORY_CODES) == {"hours_unknown"}
    assert all_codes - set(ADVISORY_CODES) == {
        "walking_budget",
        "time_budget",
        "outside_opening_window",
        "unknown_site",
    }


# ── hours_unknown warns; it is still never "probably open" ──────────────────────────


@pytest.mark.parametrize(
    ("hours", "country_code", "reason"),
    [
        pytest.param(None, COUNTRY, "no_expression", id="no-hours-tagged-at-all"),
        pytest.param(
            "Mo-Su 09:00-17:00; PH off",
            None,
            "public_holiday_without_country",
            id="PH-with-no-country-on-the-area-row",
        ),
        # The "T018 coverage gate" `pyproject.toml` names: a `PH`-bearing expression in a
        # country the evaluator holds no calendar for is `hours_unknown`, never an answer.
        # It is delivered by `commons/opening_hours.py` raising on construction and by this
        # module refusing to approve over it — deliberately NOT by a second holiday oracle
        # (`commons/frame.py::holiday_countries` says outright it would pass everything).
        # Coupled to the pinned evaluator's embedded data, as `tests/test_opening_hours.py`
        # is: `IL` is absent from v2.1.4, and this going red is the signal, not the bug.
        pytest.param(
            "Mo-Su 09:00-17:00; PH off", "IL", "country_not_supported", id="uncovered-country"
        ),
        pytest.param("SH off", COUNTRY, "school_holiday_selector", id="school-holidays"),
        pytest.param("Mo-Su sunrise-sunset", COUNTRY, "sun_event_unsupported", id="sun-events"),
        pytest.param("nonsense((", COUNTRY, "unparseable", id="unparseable"),
    ],
)
def test_hours_that_cannot_be_evaluated_warn_by_name_rather_than_pass_silently(
    hours: str | None, country_code: str | None, reason: str
) -> None:
    """Every ``hours_unknown`` route out of the evaluator is *named*, and none of them blocks.

    The two halves are one test on purpose. "It warns" alone would pass against a checker
    that had quietly stopped evaluating hours at all; "it is named, with the evaluator's own
    reason" is what proves the refusal to guess is still happening — the evaluator
    (`commons/opening_hours.py`) is unchanged and still fails closed, and this is the planner
    declining to turn its honest "I don't know" into a refusal of the whole day.
    """
    unknown = site(hours=hours)
    itinerary = plan([(unknown.id, time(10, 0), 60)], hours=6.0)

    result = verdict(itinerary, {unknown.id: unknown}, country_code=country_code)

    assert result.ok, "hours_unknown warns; it does not refuse the day"
    assert codes(result) == ["hours_unknown"], "and it is still raised, not swallowed"
    assert result.warning_messages == result.messages
    assert result.violation_messages == ()
    assert reason in result.messages[0], "the machine-readable reason reaches the traveller"
    if hours is not None:
        assert hours not in result.messages[0], "the tag itself is ODbL and stays out (ADR-0030)"


# ── a verdict is server prose, never a rendering of commons data ────────────────────


@pytest.mark.parametrize(
    ("hours", "country_code"),
    [
        pytest.param("Tu 09:00-14:00", COUNTRY, id="closed-on-the-day"),
        pytest.param("Mo-Su 09:00-12:00,13:00-17:00", COUNTRY, id="shut-inside-the-dwell"),
        pytest.param("nonsense((", COUNTRY, id="unparseable-so-the-detail-quotes-it"),
        pytest.param("Mo-Su 09:00-17:00; PH off", None, id="PH-with-no-country"),
    ],
)
def test_no_violation_message_carries_the_opening_hours_expression(
    hours: str, country_code: str | None
) -> None:
    """ADR-0030 A1: a server-computed verdict embeds no commons-derived text.

    The expression is an ODbL value. Rendered through the attribution funnel it arrives with
    its chip; embedded in a verdict string it reaches the DOM by a path that credits nothing,
    which is an attribution failure that reads like a helpful message. The stop's own
    ``opening_hours`` travels beside the verdict and already carries the credit, so the
    verdict says *which stop* and stays quiet about the tag.

    ``unparseable`` is the case that makes this a test rather than a convention: the evaluator's
    ``detail`` there is the parser's own diagnostic, which quotes the raw expression — so
    forwarding ``detail`` would leak it through a field documented as "our own text".
    """
    tagged = site(hours=hours)
    itinerary = plan([(tagged.id, time(11, 30), 120)], hours=6.0)

    result = verdict(itinerary, {tagged.id: tagged}, country_code=country_code)

    # Both severities are inspected: the rule is about what a server-composed sentence may
    # contain, and a warning reaches the DOM by exactly the same uncaptioned path a violation
    # does. Two of these fixtures now warn rather than block, which changes nothing here.
    assert result.messages, "the fixture is meant to produce a message to inspect"
    for message in result.messages:
        assert hours not in message
        for fragment in ("09:00-14:00", "13:00-17:00", "nonsense", "PH off"):
            assert fragment not in message, f"{fragment!r} is source text, not server prose"
        assert message.startswith(f"stop {result.violations[0].stop_order} ")


def test_an_area_with_no_local_frame_yields_hours_unknown_never_a_default_clock() -> None:
    """``area.timezone`` is nullable for rows that predate it; ``None`` means unresolved.

    Substituting UTC would answer every window check confidently in the wrong frame — the
    failure mode that is invisible precisely where it matters. The honest answer is "we could
    not check", which is an ``hours_unknown`` warning like any other: the day is planned and
    approvable, and every stop on it says so.

    **This assertion is a decision, not a side effect of the code path** (see
    :func:`~planner.feasibility._check_hours`). ``no_timezone`` is the strongest candidate for
    staying blocking — it means every check on the day was skipped, and it is *our* data that
    is missing — and it is advisory anyway, because blocking it would hand the user an
    unapprovable day they have no way to fix. Flip it and this test is what says so out loud.
    """
    always = site()
    itinerary = plan([(always.id, time(10, 0), 60)], hours=6.0)

    result = verdict(itinerary, {always.id: always}, timezone=None, country_code=None)

    assert codes(result) == ["hours_unknown"]
    assert "no_timezone" in result.messages[0]
    assert result.ok and result.warning_messages == result.messages


# ── a stop the commons cannot resolve ───────────────────────────────────────────────


def test_a_stop_whose_site_is_not_in_the_commons_is_named_not_skipped() -> None:
    """Skipping the check would be indistinguishable from running it and finding it open."""
    missing_id = uuid4()
    itinerary = plan([(missing_id, time(10, 0), 60)], hours=6.0)

    result = verdict(itinerary, {})

    assert codes(result) == ["unknown_site"]
    assert str(missing_id) in result.messages[0]


def test_the_verdict_reports_its_totals_even_when_the_day_holds() -> None:
    """The API returns the totals a user adjusts a budget against; nothing recomputes them."""
    always = site()
    itinerary = plan(
        [(always.id, time(10, 0), 30), (always.id, time(10, 45), 30)],
        distances=[600.0],
        hours=6.0,
    )

    result = verdict(itinerary, {always.id: always})

    assert result.ok
    assert result.walking_m == pytest.approx(600.0)
    assert result.elapsed_hours == pytest.approx(1.25), "10:00 to 11:15 is an hour and a quarter"
