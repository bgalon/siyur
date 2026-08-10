"""T019 — the feasibility verdict. Tier 1: pure arithmetic, no clock, no network, no model.

Every test here is written against a bug that would otherwise ship green. The four the
brief names — each budget breached independently, an opening-window breach, and
``hours_unknown`` blocking rather than passing — are joined by three the implementation
would otherwise get quietly wrong:

* **the interior of a dwell**, because a place shut for an hour in the middle of a visit is
  open at both endpoints and an endpoint check calls that feasible;
* **the closing instant**, because treating ``[start, start+dwell]`` as closed-ended turns
  every perfectly-fitted stop into a violation;
* **a day that crosses midnight**, because subtracting wall-clock times backwards reports a
  four-hour day as minus twenty, which passes every budget it broke.

No instant is ever read from the system clock: the frame is a fixed ``DAY`` plus each stop's
own ``planned_start``, so a verdict here is the same verdict for ever.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
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
from planner.feasibility import FeasibilityVerdict, check_feasibility

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


def leg(index: int, distance_m: float) -> RouteLegV1:
    return RouteLegV1(
        id=f"leg-{index}",
        from_stop=index,
        to_stop=index + 1,
        geometry=GEOMETRY,
        distance_m=distance_m,
        duration_s=300,
        source=routing_source(),
    )


def plan(
    schedule: list[tuple[UUID, time, int]],
    *,
    walking_m: float = 4000.0,
    hours: float = 6.0,
    distances: list[float] | None = None,
) -> ItineraryV1:
    """An itinerary from ``(site_id, planned_start, dwell_min)`` triples plus leg lengths."""
    return ItineraryV1(
        user_id="test-subject",
        area_id=uuid4(),
        date=DAY,
        lang="en",
        stops=tuple(
            Stop(site_id=site_id, order=order, planned_start=start, dwell_min=dwell)
            for order, (site_id, start, dwell) in enumerate(schedule)
        ),
        legs=tuple(leg(index, metres) for index, metres in enumerate(distances or [])),
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


def test_a_budget_met_exactly_is_met_and_not_a_float_artefact() -> None:
    """Three legs of 1000.1 m sum to 3000.3 with representation error; the budget holds."""
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
    assert "stop 0 outside opening window Tu 09:00-14:00" in result.messages[0]
    assert "10:00" in result.messages[0], "the offending instant is named, not just the rule"


def test_a_closure_inside_the_dwell_is_caught_not_only_the_endpoints() -> None:
    """11:30 + 120 min against a lunchtime break: open at 11:30, open at 13:29, shut at noon.

    An implementation that evaluated only arrival and departure calls this stop feasible and
    walks the traveller into a locked door halfway through their visit.
    """
    lunchtime = site(hours="Mo-Su 09:00-12:00,13:00-17:00")
    itinerary = plan([(lunchtime.id, time(11, 30), 120)], hours=6.0)

    result = verdict(itinerary, {lunchtime.id: lunchtime})

    assert codes(result) == ["outside_opening_window"]
    assert "12:00" in result.messages[0], "the first shut minute is the one reported"


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


# ── hours_unknown blocks; it is never "probably open" ───────────────────────────────


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
def test_hours_that_cannot_be_evaluated_block_rather_than_pass_silently(
    hours: str | None, country_code: str | None, reason: str
) -> None:
    """Every ``hours_unknown`` route out of the evaluator is a violation, not a shrug.

    The stop is inside every budget and would otherwise be feasible; the *only* thing
    standing between it and an approval is that the checker refuses to guess.
    """
    unknown = site(hours=hours)
    itinerary = plan([(unknown.id, time(10, 0), 60)], hours=6.0)

    result = verdict(itinerary, {unknown.id: unknown}, country_code=country_code)

    assert not result.ok, "hours_unknown is not 'probably open'"
    assert codes(result) == ["hours_unknown"]
    assert reason in result.messages[0], "the machine-readable reason reaches the traveller"


def test_an_area_with_no_local_frame_yields_hours_unknown_never_a_default_clock() -> None:
    """``area.timezone`` is nullable for rows that predate it; ``None`` means unresolved.

    Substituting UTC would answer every window check confidently in the wrong frame — the
    failure mode that is invisible precisely where it matters.
    """
    always = site()
    itinerary = plan([(always.id, time(10, 0), 60)], hours=6.0)

    result = verdict(itinerary, {always.id: always}, timezone=None, country_code=None)

    assert codes(result) == ["hours_unknown"]
    assert "no_timezone" in result.messages[0]


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
