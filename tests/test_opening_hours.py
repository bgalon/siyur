"""Unit tests — the opening-hours evaluator (`commons/opening_hours.py`), T015 of Spec 002.

The claim under test is narrow and load-bearing: **this module never answers "open" unless it
is sure** (ADR-0022 fail-closed posture, FR-004/FR-005, SC-002). So the file is two tables —
verdicts that must be right, and refusals that must *stay* refusals — plus one test per guard
that has been observed to be defeatable.

**Every instant here is a frozen constant**; nothing calls ``datetime.now()``, in an assertion
or anywhere else, and :func:`test_the_caller_supplied_instant_is_the_one_evaluated` asserts the
*mechanism* — that the instant reaching the library is the one the caller passed — because
``OpeningHours.state()`` with no argument silently evaluates at wall-clock *now*.

**Which strings are real.** ``24/7`` and ``Mo-Fr 09:00-17:00`` are read out of the captured
Overpass fixtures at collection time (Rhodes and Takayama respectively) rather than retyped, so
the table cannot drift from the tags that actually exist. They are also the *only* two
``opening_hours`` tags in the two 25-element fixtures — real, and a thin sample. The remaining
expressions are hand-written in the same grammar to reach the selectors the fixtures do not
contain (``PH``, ``SH``, sun events, the ``unknown`` modifier); they are labelled as such below
and no genericity claim rests on them.

**This table was proved able to fail.** A deliberately naive wrapper (``try:
OpeningHours(expr).state(when) except ParserError: hours_unknown``) was substituted for the
implementation: **22 of 38 tests went red**, including every SH row, both missing-country rows,
the coords row and the sun-event row — and the naive wrapper answered those with *verdicts*
(`SH off` → closed, `PH off` with no country → open), never an exception. Four single-guard
mutants were run on top of that; each reddens its own rows and nothing else, except
``auto_country`` — see the note on that row. Without this step the file would be decoration:
`SH` parses cleanly, so a `try/except` guard passes a rejection table while never firing
(ADR-0022 A1, FAIL-005's shape).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from opening_hours import State
from shapely import Point

from commons.opening_hours import HoursEvaluation, HoursState, evaluate

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture_opening_hours(fixture: str) -> list[str]:
    """Every ``opening_hours`` tag in a captured Overpass response, in document order."""
    elements = json.loads((FIXTURES / fixture).read_text())["elements"]
    return [
        tags["opening_hours"]
        for element in elements
        if (tags := element.get("tags", {})) and "opening_hours" in tags
    ]


#: Real tag, Rhodes fixture (node 297525681, a filling station) — the always-open case.
ALWAYS = _fixture_opening_hours("overpass_rhodes.json")[0]
#: Real tag, Takayama fixture (node 1421002874, a post office) — the ordinary weekday case.
WEEKDAYS = _fixture_opening_hours("overpass_takayama.json")[0]

# Frozen instants, all naive **area-local wall clock** (the only frame this module accepts).
#: Tuesday — an ordinary working weekday in both fixture areas.
TUE_MIDDAY = datetime(2026, 3, 24, 12, 0)
TUE_EVENING = datetime(2026, 3, 24, 20, 0)
#: Sunday — outside a Mo-Fr rule, and not a holiday anywhere relevant.
SUN_MIDDAY = datetime(2026, 3, 22, 12, 0)
#: Wednesday 25 March 2026 — **Greek Independence Day**, a public holiday in GR and an
#: ordinary Wednesday in JP. The whole PH story turns on this one instant.
GREEK_PH_MIDDAY = datetime(2026, 3, 25, 12, 0)
#: Northern summer solstice, half an hour before the generic 19:00 fallback sunset and about
#: an hour before the *real* Rhodes sunset (20:28 local). Chosen to separate the two.
SOLSTICE_EVENING = datetime(2026, 6, 21, 19, 30)

GR = {"timezone": "Europe/Athens", "country_code": "GR"}
JP = {"timezone": "Asia/Tokyo", "country_code": "JP"}
#: An area row that carries a timezone but no country — ADR-0022 A2's dangerous input.
NO_COUNTRY: dict[str, Any] = {"timezone": "Europe/Athens", "country_code": None}
#: EPSG:4326 **(lon, lat)** — house order; the wrapper flips it for the library.
RHODES = Point(28.2276, 36.4443)


# --- the verdict table: answers that must be right -------------------------------


@pytest.mark.parametrize(
    ("expression", "when", "frame", "expected"),
    [
        # Real fixture tags.
        (ALWAYS, TUE_MIDDAY, GR, "open"),
        (ALWAYS, GREEK_PH_MIDDAY, GR, "open"),
        (WEEKDAYS, TUE_MIDDAY, JP, "open"),
        (WEEKDAYS, TUE_EVENING, JP, "closed"),
        (WEEKDAYS, SUN_MIDDAY, JP, "closed"),
        # Hand-written, same grammar: the PH selector with a country behind it. The pair is
        # the point — the same expression, the same instant, two countries, two answers.
        ("Mo-Fr 09:00-17:00; PH off", GREEK_PH_MIDDAY, GR, "closed"),
        ("Mo-Fr 09:00-17:00; PH off", GREEK_PH_MIDDAY, JP, "open"),
        # A closed rule may carry the mapper's own comment; it must not change the verdict.
        ('Mo-Fr 09:00-17:00; Su off "closed on Sundays"', SUN_MIDDAY, GR, "closed"),
        # "SH" as prose inside a quoted comment is *not* the school-holiday selector; a
        # substring search here would refuse a perfectly evaluable expression.
        ('Mo-Fr 09:00-17:00 || "SHop closed"', TUE_MIDDAY, GR, "open"),
    ],
)
def test_verdicts_at_frozen_instants(
    expression: str, when: datetime, frame: dict[str, Any], expected: HoursState
) -> None:
    result = evaluate(expression, when, **frame)
    assert result.state == expected
    # A verdict carries no refusal reason — the two are mutually exclusive by contract.
    assert result.reason is None
    assert result.expression == expression


def test_public_holiday_needs_the_country_to_be_the_right_one() -> None:
    """The A2 measurement, pinned: the country is what makes the holiday exist."""
    greek_holiday = evaluate("Mo-Fr 09:00-17:00; PH off", GREEK_PH_MIDDAY, **GR)
    japanese_wednesday = evaluate("Mo-Fr 09:00-17:00; PH off", GREEK_PH_MIDDAY, **JP)
    assert (greek_holiday.state, japanese_wednesday.state) == ("closed", "open")


# --- the rejection table: refusals that must stay refusals ------------------------


@pytest.mark.parametrize(
    ("expression", "when", "frame", "location", "reason"),
    [
        # ⚠️ SH parses cleanly and evaluates to CLOSED, so nothing raises here. These rows
        # are red against any try/except-based wrapper (ADR-0022 A1).
        ("SH off", TUE_MIDDAY, GR, None, "school_holiday_selector"),
        ("Mo-Fr 09:00-17:00; SH off", TUE_MIDDAY, GR, None, "school_holiday_selector"),
        ("Mo-Fr 09:00-17:00, SH 10:00-12:00", TUE_MIDDAY, GR, None, "school_holiday_selector"),
        ("PH,SH off", TUE_MIDDAY, GR, None, "school_holiday_selector"),
        # An unterminated comment hides nothing: the quote never closes, so the tail stays in
        # the scanned text and the selector is still seen (it also fails to parse).
        ('Mo-Fr 09:00-17:00 || "unclosed SH', TUE_MIDDAY, GR, None, "school_holiday_selector"),
        # ⚠️ PH without a country evaluates to *open* inside the library. Red against any
        # wrapper that passes country_code=None through (ADR-0022 A2).
        (
            "Mo-Fr 09:00-17:00; PH off",
            GREEK_PH_MIDDAY,
            NO_COUNTRY,
            None,
            "public_holiday_without_country",
        ),
        ("PH off", GREEK_PH_MIDDAY, NO_COUNTRY, None, "public_holiday_without_country"),
        # ⚠️ …and it must stay refused when coordinates are available, because the library
        # will otherwise infer a country from them and answer confidently — the reason coords
        # are so tempting is that sun events need them.
        #
        # Measured, so nobody over-trusts this row: it is red against a wrapper with no
        # pre-construction guard (it answers *closed* there, from an inferred country) and red
        # if the guard is deleted (*open*). It stays **green** if only `auto_country=False` is
        # removed, because the guard refuses before construction and the flag never gets a say.
        # `test_the_caller_supplied_instant_is_the_one_evaluated` is what pins the flag itself.
        (
            "Mo-Fr 09:00-17:00; PH off",
            GREEK_PH_MIDDAY,
            NO_COUNTRY,
            RHODES,
            "public_holiday_without_country",
        ),
        # A country the embedded holiday database does not cover is not a country we can
        # resolve PH against (verified absent from v2.1.4: IL, IN, SS, AQ).
        (
            "Mo-Fr 09:00-17:00; PH off",
            GREEK_PH_MIDDAY,
            {"timezone": "Asia/Jerusalem", "country_code": "IL"},
            None,
            "country_not_supported",
        ),
        # Sun events without coordinates get a generic day, not a refusal, from the library.
        ("sunrise-sunset", SOLSTICE_EVENING, GR, None, "sun_event_unsupported"),
        ("Mo-Su dawn-dusk", SOLSTICE_EVENING, GR, None, "sun_event_unsupported"),
        # Genuinely malformed input — the only rows a try/except wrapper would catch.
        ("not opening hours at all", TUE_MIDDAY, GR, None, "unparseable"),
        ("Mo-Fr 09:00-17:00 ||", TUE_MIDDAY, GR, None, "unparseable"),
        # No tag at all is not "open all day"; it is a place we know nothing about.
        (None, TUE_MIDDAY, GR, None, "no_expression"),
        ("   ", TUE_MIDDAY, GR, None, "no_expression"),
        # The mapper's own `unknown` modifier — their refusal, carried through as ours.
        ('Mo-Fr 09:00-17:00 unknown "call ahead"', TUE_MIDDAY, GR, None, "expression_says_unknown"),
        # A timezone the area row cannot supply is data, not a caller bug: fail closed.
        (
            ALWAYS,
            TUE_MIDDAY,
            {"timezone": "Mars/Olympus", "country_code": "GR"},
            None,
            "unknown_timezone",
        ),
    ],
)
def test_rejections_are_hours_unknown_never_open(
    expression: str | None,
    when: datetime,
    frame: dict[str, Any],
    location: Point | None,
    reason: str,
) -> None:
    result = evaluate(expression, when, location=location, **frame)
    assert result.state == "hours_unknown"
    assert result.reason == reason
    assert result.is_unknown
    # ADR-0022: "rejected loudly **with the raw string shown**". A refusal the traveller
    # cannot see is indistinguishable from a bug, so the expression survives verbatim.
    assert result.expression == expression
    # Every refusal says something; an empty `detail` would reach the log as a silent shrug.
    assert result.detail


def test_no_input_in_the_rejection_table_can_ever_read_open() -> None:
    """The fail-closed claim stated once, over the whole table, in one assertion.

    Parametrised rows fail one at a time; this is the sentence the module actually promises.
    """
    refusals = [
        evaluate("SH off", TUE_MIDDAY, **GR),
        evaluate("Mo-Fr 09:00-17:00; PH off", GREEK_PH_MIDDAY, **NO_COUNTRY),
        evaluate("Mo-Fr 09:00-17:00; PH off", GREEK_PH_MIDDAY, location=RHODES, **NO_COUNTRY),
        evaluate("sunrise-sunset", SOLSTICE_EVENING, **GR),
        evaluate("not opening hours at all", TUE_MIDDAY, **GR),
        evaluate(None, TUE_MIDDAY, **GR),
    ]
    assert [r.state for r in refusals] == ["hours_unknown"] * len(refusals)


# --- the guards that have been observed to be defeatable --------------------------


def test_school_holidays_are_detected_as_a_token_not_a_substring() -> None:
    """`SH` the selector is refused; the letters "SH" in prose or a word are not.

    Over-refusing is cheap but not free — every false positive is a stop the traveller cannot
    approve — so the scan runs on the expression with quoted comments stripped out.
    """
    assert evaluate("SH off", TUE_MIDDAY, **GR).reason == "school_holiday_selector"
    assert evaluate('Mo-Fr 09:00-17:00 || "SHop closed"', TUE_MIDDAY, **GR).state == "open"
    assert evaluate('Mo-Fr 09:00-17:00 || "closed during SH"', TUE_MIDDAY, **GR).state == "open"


def test_a_country_outside_the_holiday_database_only_blocks_holiday_rules() -> None:
    """An unsupported country is fatal to `PH` and irrelevant to everything else.

    The library raises `UnknownCountryError` at *construction*, whatever the expression says —
    so refusing on it unconditionally would make every place in an uncovered country
    unapprovable over a fact its opening hours do not depend on.

    This test is deliberately coupled to the pinned version's embedded holiday data: `IL` is
    absent from 2.1.4 (as are `IN`, `SS`, `AQ`). If a future release adds it, this goes red —
    which is the *signal*, not the bug. ADR-0022's coverage claim would then need re-checking,
    and a real area would have gained PH support nobody noticed.
    """
    israel = {"timezone": "Asia/Jerusalem", "country_code": "IL"}
    assert (
        evaluate("Mo-Fr 09:00-17:00; PH off", TUE_MIDDAY, **israel).reason
        == "country_not_supported"
    )
    assert evaluate(WEEKDAYS, TUE_MIDDAY, **israel).state == "open"
    assert evaluate(ALWAYS, GREEK_PH_MIDDAY, **israel).state == "open"


def test_sun_events_are_refused_outright_even_with_coordinates() -> None:
    """A sun expression is `hours_unknown` whether or not the place has coordinates.

    This test was the inverse until 2026-08-08: it asserted that coordinates unlock the real
    sun (sunset 20:28 local at Rhodes on the solstice) and only a *missing* location was
    refused. That behaviour required `auto_timezone=True`, and the flag reads `tzf-dist`
    (**ODbL-1.0**) — the single share-alike component among the wheel's 144. A share-alike
    obligation is not worth a selector M1 barely uses, so the flag is pinned off and solar
    computation goes with it: verified, `coords` then yield a flat 07:00–19:00 day,
    byte-identical to supplying none.

    Which is why refusal is now **unconditional**. Accepting a sun expression *with*
    coordinates would return that generic window dressed as the sun — at Reykjavik in winter
    it reports open at 08:00 and 17:00 with the sun down. Same silent-wrong-answer class as
    `PH` without a country, so the same answer: refuse.
    """
    for location in (RHODES, None):
        result = evaluate("sunrise-sunset", SOLSTICE_EVENING, location=location, **GR)
        assert result.state == "hours_unknown"
        assert result.reason == "sun_event_unsupported"
        assert result.expression == "sunrise-sunset", "the raw expression is always retained"

    # An ordinary expression is unaffected — the refusal is scoped to the selector.
    assert evaluate(WEEKDAYS, TUE_MIDDAY, location=RHODES, **GR).state == "open"


def test_the_caller_supplied_instant_is_the_one_evaluated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mechanism, not outcome: no clock read, and the two pinned kwargs actually go through.

    `OpeningHours.state()` evaluates at *now* when called with no argument, and `auto_country`
    / `auto_timezone` default to `True`. All three are invisible in a verdict — a wrapper that
    dropped the instant would still return plausible answers most of the day — so the call
    itself is recorded and asserted. The behavioural rows above catch the same regressions from
    the outside; this one names them, so a failure says which kwarg went missing.
    """
    calls: list[dict[str, Any]] = []

    class _Recorder:
        def __init__(self, expression: str, **kwargs: Any) -> None:
            calls.append({"expression": expression, **kwargs})

        def state(self, when: datetime) -> tuple[State, str]:
            calls[-1]["when"] = when
            return (State.OPEN, "")

    # String target: `commons.opening_hours.OpeningHours` is a re-imported name, and reading it
    # as an attribute trips mypy's no-implicit-reexport under strict.
    monkeypatch.setattr("commons.opening_hours.OpeningHours", _Recorder)
    evaluate(WEEKDAYS, TUE_MIDDAY, location=RHODES, **GR)

    assert len(calls) == 1
    call = calls[0]
    assert call["when"] == TUE_MIDDAY, "state() must be given the caller's instant, never now()"
    assert call["auto_country"] is False, "a country inferred from coords is not the audited one"
    assert call["auto_timezone"] is False, (
        "pinned OFF for licensing: the flag reads tzf-dist (ODbL-1.0), the one share-alike "
        "component among the wheel's 144 (ADR-0022 A6). The cost — coords become inert for "
        "solar work — is paid by refusing sun expressions outright, not by flipping this."
    )
    assert call["country"] == "GR"
    # (lat, lon) at the library boundary; (lon, lat) everywhere in Siyur.
    assert call["coords"] == (RHODES.y, RHODES.x)


# --- the caller contract: two clocks, and a bug is not a refusal ------------------


@pytest.mark.parametrize(
    "when",
    [
        # A string reads like a parse failure inside the library (ADR-0022 A3) — caught here
        # first, with a message that says what actually happened.
        "2026-03-24T12:00",
        date(2026, 3, 24),
        1774353600,
        None,
    ],
)
def test_a_non_datetime_instant_raises_rather_than_refusing(when: object) -> None:
    with pytest.raises(TypeError, match="local wall clock"):
        evaluate(ALWAYS, when, **GR)  # type: ignore[arg-type]


def test_a_utc_instant_is_refused_because_it_is_the_wrong_clock() -> None:
    """The two-clocks discipline: `commons.models` guards it on the way in, this on the way out.

    A UTC-stamped `when` means the caller conflated an audit timestamp with a wall clock, and
    the answer would be wrong by the area's offset — three hours of "open" in Athens, silently.
    That is a bug, so it raises; unreadable *data* fails closed instead.
    """
    with pytest.raises(ValueError, match="naive area-local wall-clock"):
        evaluate(ALWAYS, datetime(2026, 3, 24, 12, 0, tzinfo=UTC), **GR)


def test_the_pre_1900_library_limit_is_carried_through_rather_than_hidden() -> None:
    """ADR-0022: "expressions evaluate as closed before 1900 and after 9999" — carried as-is.

    Asserted because it is surprising (`24/7` is *closed* in 1899) and because it is the range
    boundary: it also shows nothing escapes uncaught there, which for a plan being compiled for
    an offline device is the difference between a wrong answer and a crash.
    """
    assert evaluate(ALWAYS, datetime(1899, 12, 31, 12, 0), **GR).state == "closed"
    assert evaluate(ALWAYS, datetime(1900, 1, 2, 12, 0), **GR).state == "open"
    assert evaluate(ALWAYS, datetime.max, **GR).state == "open"


def test_the_result_is_frozen() -> None:
    """A verdict is evidence; nothing downstream may edit one into a different answer."""
    result: HoursEvaluation = evaluate(ALWAYS, TUE_MIDDAY, **GR)
    with pytest.raises(AttributeError):
        result.state = "closed"  # type: ignore[misc]
