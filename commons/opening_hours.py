"""Deterministic opening-hours evaluation — *is this place open at this area-local instant?*

**Decision of record: [ADR-0022](../docs/adr/0022-opening-hours-evaluator-opening-hours-py.md)**
(with its amendments A1–A3), sharpened by `docs/data/poi-site.md` "Timezone". The engine is
**`opening-hours-py`** (the Rust bindings, MIT OR Apache-2.0) — *not* the abandoned
`opening-hours` v0.1.1 one hyphen away; the distribution name is pinned in `pyproject.toml`
and guarded by the job-6 supply-chain gate.

This module is the *only* place in Siyur that answers the open/closed question, for three
reasons that are all invariants rather than preferences:

* **FR-004 — the model never evaluates hours.** Feasibility is deterministic machinery
  precisely so a plausible-sounding wrong answer cannot enter a plan. Nothing here calls the
  ``ModelRouter`` seam, and nothing here may.
* **It is offline and clock-free.** The caller passes the instant; this module never reads a
  clock, a network or a database. (``OpeningHours.state()`` evaluates at *now* when called
  with no argument — the one API footgun that would make every test time-dependent, so
  :func:`evaluate` always passes ``when`` explicitly.)
* **It fails closed.** Everything it cannot answer *confidently* is
  ``hours_unknown`` — a **first-class third outcome**, not a silent default — carrying the raw
  expression so the UI can show the traveller what it refused to interpret and FR-005 can name
  the violation. **Nothing is ever defaulted to "open".**

## Why the guards are token scans and input checks, not a ``try/except``

Four behaviours of the library were established **by execution** (2026-08-07/08, v2.1.4).
Each one defeats the obvious implementation, which is why they are written down here:

1. **``SH`` does not raise.** ``OpeningHours("SH off")`` constructs cleanly and evaluates to
   ``CLOSED``; only genuinely malformed input raises ``ParserError``. A
   ``try/except ParserError`` wrapper would therefore evaluate school-holiday rules as
   ordinary ones — wrong answers, no exception — *while passing every row of its own
   rejection table*. So :func:`evaluate` **scans for the ``SH`` token itself** (ADR-0022 A1).
2. **``OpeningHours(...).warnings`` is an attribute, not a method, and is empty for ``SH``.**
   It is not an alternative detector.
3. **A missing country degrades to "open", the dangerous direction.**
   ``Mo-Fr 09:00-17:00; PH off`` on Greek Independence Day (Wed 2026-03-25) evaluates to
   *open* with no country and *closed* with ``country="GR"``. A ``PH``-bearing expression with
   no ``country_code`` therefore yields ``hours_unknown`` — **passing ``None`` through is worse
   than not evaluating** (ADR-0022 A2).
4. **``.state()`` takes a ``datetime``, not a string** — a string raises ``TypeError``, which
   *reads* like a parse failure and would be swallowed by a broad ``except`` (A3). Hence the
   ``except`` clauses below name their exception types and nothing here catches
   ``Exception``.

A fifth, same-shaped hazard was found while implementing: **the library infers a country from
``coords`` by default** (the stub's ``auto_country``/``auto_timezone`` both default to
``True``). Verified on the same PH day: ``coords`` alone → *closed* (a country was inferred and
never supplied), ``coords`` with ``auto_country=False`` → *open*. Sun events need coordinates,
so the moment :func:`evaluate` is handed a ``location`` the default would quietly satisfy guard
3 with an **un-audited** country and the ``hours_unknown`` branch would become **unreachable**.
The frame of record is the ``area`` row's ``timezone``/``country_code`` (ADR-0025) — derived
once, deterministically, and *stored*; an inference made inside the evaluator is neither audited
nor visible in the plan. Hence ``auto_country=False``, pinned at :func:`_construct`.

The two flags are **not** the same knob, and that asymmetry is measured, not assumed — see
:func:`_construct` before "making them consistent".

## The frame the caller must supply

The two-clocks discipline of `commons.models` applies here unchanged: ``when`` is a **naive
area-local wall-clock ``datetime``** — the clock on the wall where the traveller is standing,
framed by ``ItineraryV1.date`` plus the area's ``timezone`` — never a UTC instant. There is
deliberately no conversion helper: converting one to the other is a decision that needs the
area row, which this module does not read. A tz-aware ``when`` is a **caller bug and raises**;
bad *data* (an unparseable expression, an unsupported country, an unknown timezone) is not a
bug and fails closed instead.

## Named import hazard

This module is ``commons.opening_hours`` and the library is top-level ``opening_hours``. Python
3 absolute imports keep them apart (``from opening_hours import …`` inside a package module
resolves to the library), but running this file as a *script* would shadow it. Import it as
``commons.opening_hours``, always.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

# ``ParserError`` / ``UnknownCountryError`` are absent from the wheel's pyo3-generated
# ``__init__.pyi`` (it only declares ``OpeningHours``, ``State`` and ``validate``), so mypy
# reports `attr-defined` for them even though they exist at runtime and are what the library
# actually raises. The ignore is scoped to this statement; removing it fails `mypy --strict`.
from opening_hours import (  # type: ignore[attr-defined]
    OpeningHours,
    ParserError,
    State,
    UnknownCountryError,
)
from shapely import Point

from commons.geo import validate_point

__all__ = [
    "HoursEvaluation",
    "HoursState",
    "UnknownReason",
    "evaluate",
]

#: The three outcomes. ``hours_unknown`` is a *result*, not an error and not a default: it
#: blocks approval (FR-005) and is rendered with the raw expression beside it.
HoursState = Literal["open", "closed", "hours_unknown"]

#: Why an evaluation came back ``hours_unknown``. The UI renders the reason next to the raw
#: expression; feasibility (T018) may treat the reasons differently — ``no_expression`` ("this
#: place has no hours tagged at all") is a very different conversation with the traveller from
#: ``school_holiday_selector`` ("we can read the tag but refuse to guess at it").
UnknownReason = Literal[
    "no_expression",
    "school_holiday_selector",
    "public_holiday_without_country",
    "country_not_supported",
    "sun_event_unsupported",
    "unknown_timezone",
    "unparseable",
    "expression_says_unknown",
]

# A quoted comment is free text and may contain anything, including the letters "SH" — the
# selector `SH` and the comment `"SHop closed"` must not be confused. Stripping *balanced*
# quoted spans first is what makes the scans below token scans rather than substring searches.
#
# An **unterminated** quote deliberately does not match, so its text stays in the scanned
# string: the conservative direction. `Mo-Fr 09:00-17:00 || "unclosed SH` is then detected as
# SH-bearing rather than parsed — and it does not parse anyway (verified: ParserError), so the
# outcome is `hours_unknown` either way. Over-refusing on a malformed expression costs nothing;
# under-detecting a selector costs a wrong verdict.
_QUOTED_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r'"[^"]*"')

# Selectors in this grammar are case-sensitive and uppercase — verified: `sh off` and `Sh off`
# both raise ParserError, only `SH off` parses. So the scans match uppercase exactly, which
# keeps them from firing on lowercase prose. The letter guards make them *token* matches
# (`PH,SH off`, `Mo-Fr; SH off` hit; a word containing the letters does not).
_SH_SELECTOR_RE: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z])SH(?![A-Za-z])")
_PH_SELECTOR_RE: Final[re.Pattern[str]] = re.compile(r"(?<![A-Za-z])PH(?![A-Za-z])")

# Sun-event variable times. Lowercase in the grammar, unlike the holiday selectors above.
_SUN_EVENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])(sunrise|sunset|dawn|dusk)(?![A-Za-z])"
)


@dataclass(frozen=True, slots=True)
class HoursEvaluation:
    """One evaluation of one expression at one instant — the answer *and* its justification.

    ``expression`` is retained on every outcome, ``hours_unknown`` included: ADR-0022's
    "rejected loudly **with the raw string shown**". A refusal the traveller cannot see is
    indistinguishable from a bug.
    """

    #: ``open`` | ``closed`` | ``hours_unknown``.
    state: HoursState
    #: The raw OSM ``opening_hours`` value as tagged — never normalised, never repaired.
    #: ``None`` only when the place had no ``opening_hours`` at all.
    expression: str | None
    #: Set **iff** ``state == "hours_unknown"``; the machine-readable "why".
    reason: UnknownReason | None = None
    #: Our diagnostic prose (parser message, offending selector). For logs and dev, not a UI
    #: string — a pest-parser caret diagram is not something to show a traveller.
    detail: str = ""
    #: The **mapper's** comment carried by the expression itself (`… unknown "call ahead"`,
    #: `Su off "holiday"`), returned alongside the state by the library. Distinct from
    #: ``detail`` in origin and in trust: this is OSM data, that is our own text.
    comment: str = ""

    @property
    def is_unknown(self) -> bool:
        """Convenience for the FR-005 gate: this evaluation cannot support an approval."""
        return self.state == "hours_unknown"


def _unknown(
    expression: str | None,
    reason: UnknownReason,
    detail: str,
) -> HoursEvaluation:
    """The only way this module produces a non-verdict — one funnel, so none can drift open."""
    return HoursEvaluation(
        state="hours_unknown", expression=expression, reason=reason, detail=detail
    )


def _require_naive_local(when: object) -> datetime:
    """``when`` is a wall clock, not an instant — the mirror of ``models._require_naive_local``.

    Both failures here are *caller* bugs and so they raise rather than fail closed: a wrong
    type or a UTC-stamped ``when`` means the calling code confused the two clocks, and silently
    answering ``hours_unknown`` would hide that behind a plausible refusal. Bad *data* (the
    expression, the timezone, the country) fails closed instead — that distinction is the whole
    reason the ``except`` clauses below are narrow.

    The type check is first and explicit because of ADR-0022 A3: handing the library a string
    raises a ``TypeError`` whose message reads like a parse failure ("failed to extract enum
    DateTimeMaybeAware"), which is exactly the sort of thing a broad ``except`` turns into a
    confident wrong answer.
    """
    if not isinstance(when, datetime):
        raise TypeError(
            f"when must be a datetime in the area's local wall clock, got "
            f"{type(when).__name__}: {when!r}. A date has no time of day and a string is not "
            "an instant — the library's own TypeError for a string reads like a parse error"
        )
    if when.tzinfo is not None:
        raise ValueError(
            f"when must be a naive area-local wall-clock datetime carrying no UTC offset, got "
            f"tzinfo={when.tzinfo!r}. opening_hours is read in the frame of the clock on the "
            "wall where the traveller is standing (docs/data/itinerary.md 'Timezone'); "
            "convert with the area's timezone before calling, never here"
        )
    return when


def evaluate(
    expression: str | None,
    when: datetime,
    *,
    timezone: str,
    country_code: str | None,
    location: Point | None = None,
) -> HoursEvaluation:
    """Evaluate an OSM ``opening_hours`` expression at an **area-local** instant.

    :param expression: the raw tag value (``SiteRecordV1.opening_hours``), or ``None`` when the
        place carries no hours at all — which is ``hours_unknown``/``no_expression``, never
        "open". Whether an untagged place blocks a whole day is *feasibility's* policy call
        (T018); this module only refuses to invent an answer.
    :param when: naive local wall-clock ``datetime`` (see :func:`_require_naive_local`).
    :param timezone: the area's IANA id (``area.timezone``). Required, not optional: it is what
        makes sun events resolve in the right frame, and asking for it every time is what stops
        a caller from forgetting it in the one place it matters.
    :param country_code: the area's ISO 3166-1 alpha-2 code (``area.country_code``), or ``None``
        when the area row does not carry one — in which case a ``PH``-bearing expression is
        refused rather than guessed.
    :param location: the place's EPSG:4326 point (**lon, lat** — house order), needed only by
        expressions with sun events. Converted to the library's ``(lat, lon)`` at the boundary.

    Guards run in this order, and the order is load-bearing: the caller contract is checked
    before any data, and the token scans run **before** parsing because a scan cannot be
    defeated by the parser cheerfully accepting the string (ADR-0022 A1).
    """
    when = _require_naive_local(when)

    if expression is None or not expression.strip():
        return _unknown(expression, "no_expression", "no opening_hours value on this place")

    # Comments are free text; strip them so the scans below cannot fire on prose.
    selectors = _QUOTED_COMMENT_RE.sub(" ", expression)

    if _SH_SELECTOR_RE.search(selectors):
        # ADR-0022's accepted loss, paid out loud. `opening-hours-py` embeds no school-holiday
        # calendar, so it happily evaluates an SH rule as if the holiday never applied — which
        # is a wrong answer wearing a verdict's clothes. The reference implementation
        # (`opening_hours.js`) does resolve SH, and buying it costs a second language runtime
        # in every compile and CI job — the loss ADR-0022 chose to pay, here, out loud.
        return _unknown(
            expression,
            "school_holiday_selector",
            "SH (school holidays) has no calendar in this evaluator and is never approximated",
        )

    has_public_holiday = bool(_PH_SELECTOR_RE.search(selectors))
    if has_public_holiday and country_code is None:
        # The A2 case. Without a country the holiday calendar is empty, so `PH off` silently
        # stops closing anything and a closed museum reports as open — SC-002's exact failure,
        # in the direction that strands a traveller at a locked door.
        return _unknown(
            expression,
            "public_holiday_without_country",
            "PH (public holidays) needs the area's country_code; without it holidays are "
            "silently ignored and the place reports as open",
        )

    if _SUN_EVENT_RE.search(selectors):
        # Refused UNCONDITIONALLY, not merely when `location` is missing — and the reason is
        # licensing, not capability. Solar computation is gated by `auto_timezone`, which is
        # pinned OFF because the flag reads `tzf-dist` (ODbL-1.0), the one share-alike
        # component among the wheel's 144 (ADR-0022 A6). With it off, `coords` are inert for
        # solar work: verified at Rhodes on the 2026 solstice, `sunrise-sunset` answers a
        # generic 07:00–19:00 day whether or not coordinates are supplied.
        #
        # So supplying `location` cannot rescue a sun expression, and accepting one would
        # return that generic window as if it were the sun. Verified dangerous direction:
        # at Reykjavik on the winter solstice it reports *open* at 08:00, 09:00, 16:00, 17:00
        # and 18:00 while the sun is down. `location` is still taken and still validated —
        # it is what a future ADR would need to turn this back on — but it does not unlock
        # a verdict today.
        return _unknown(
            expression,
            "sun_event_unsupported",
            "sunrise/sunset/dawn/dusk need solar computation, which is disabled because it "
            "reads ODbL timezone-boundary data (ADR-0022 A6); the evaluator would otherwise "
            "substitute a generic day that is wrong away from the equator",
        )

    try:
        zone = ZoneInfo(timezone)
    except (KeyError, ValueError) as exc:
        # `ZoneInfoNotFoundError` subclasses `KeyError`; a non-normalised key ("", "a/../b")
        # raises `ValueError`. The timezone arrives from the stored area row, so a bad one is
        # bad data, not a bug in the caller — it fails closed like any other unreadable input.
        return _unknown(expression, "unknown_timezone", f"unusable timezone {timezone!r}: {exc}")

    # (lat, lon) here, (lon, lat) everywhere in Siyur. `validate_point` re-checks the ranges so
    # a swapped pair is caught at this boundary rather than becoming a plausible sunset.
    coords: tuple[float, float] | None = None
    if location is not None:
        point = validate_point(location)
        coords = (point.y, point.x)

    try:
        hours = _construct(expression, zone, country_code, coords)
    except ParserError as exc:
        # The only thing a parse failure licenses is "we could not read this".
        return _unknown(expression, "unparseable", str(exc))
    except UnknownCountryError as exc:
        # The country is not in the embedded holiday database (verified absent: IL, IN, SS, AQ;
        # present: GR, JP, CN, US, BR, VA). ADR-0022 fail-closed rule 3: a PH-bearing rule
        # degrades to `hours_unknown`, never to "open".
        if has_public_holiday:
            return _unknown(
                expression,
                "country_not_supported",
                f"country {country_code!r} is not in the evaluator's holiday database, so PH "
                f"cannot be resolved: {exc}",
            )
        # No PH (and SH was rejected above), so the holiday calendar cannot influence this
        # expression at all — the country is dead weight and dropping it changes no verdict.
        # Refusing here would block approval over a fact the answer does not depend on.
        hours = _construct(expression, zone, None, coords)

    state, comment = hours.state(when)
    if state is State.OPEN:
        return HoursEvaluation(state="open", expression=expression, comment=comment)
    if state is State.CLOSED:
        return HoursEvaluation(state="closed", expression=expression, comment=comment)
    # `State.UNKNOWN` is the mapper's own `unknown` modifier ("may be open depending on
    # context") — a deliberate statement that the hours are not knowable from the tag. It is
    # not our refusal, so it keeps its own reason, but it collapses into the same outcome:
    # nothing here can support an approval.
    return HoursEvaluation(
        state="hours_unknown",
        expression=expression,
        reason="expression_says_unknown",
        detail="the expression itself declares this interval unknown",
        comment=comment,
    )


def _construct(
    expression: str,
    zone: ZoneInfo,
    country_code: str | None,
    coords: tuple[float, float] | None,
) -> OpeningHours:
    """The single construction site — so the two flag values cannot be forgotten at one.

    Both are stated explicitly even though one matches the library default, because both are
    *decisions*, and they go opposite ways. They look like one setting; they are not, and
    "tidying" them into agreement breaks something either way. Both readings below are from
    execution against v2.1.4, not from the docs.

    **``auto_country=False`` — pinned off.** The default (``True``) makes the evaluator resolve
    a country from ``coords`` by itself, which would make the
    ``public_holiday_without_country`` guard in :func:`evaluate` **unreachable** the moment a
    ``location`` is passed: the wrapper would return a confident verdict on a country nobody
    supplied. Verified on Greek Independence Day 2026-03-25 with ``Mo-Fr 09:00-17:00; PH off``
    and no ``country``: ``coords`` + default → **closed** (a country was inferred);
    ``coords`` + ``auto_country=False`` → **open**. A guard that cannot fire is precisely the
    defect this module exists to prevent, so inference is off and the country comes from the
    audited ``area`` row or the expression is refused (ADR-0025 / ADR-0022 A2).

    Be honest about what that flag is doing *today*, or the next reader will over-trust it:
    given the guard order in :func:`evaluate` it is **defence in depth, with no observable
    behaviour of its own**. An explicitly supplied country always wins over inference
    (verified: ``country="JP"`` with Rhodes ``coords`` answers *open* on Greek Independence Day
    whichever way the flag is set), and ``country_code=None`` only reaches this function for an
    expression with no ``PH`` selector — where the holiday calendar cannot change the verdict
    anyway. Flip the flag alone and every behavioural test still passes; that is why
    ``tests/test_opening_hours.py`` asserts the *kwargs of the call* as well as the answers.
    It earns its place by making the ``PH`` guard's removal or reordering fail closed instead
    of silently confident.

    **``auto_timezone=False`` — pinned off, and the reason is licensing.** Of the 144
    components in the wheel's CycloneDX SBOM, exactly one is share-alike: ``tzf-dist``
    (**ODbL-1.0**), the timezone-boundary polygons this flag reads. ``country-boundaries``
    behind ``auto_country`` is Apache-2.0. With both flags off we read neither dataset, so
    the ``DATA-LICENSES.md`` row stays a *code-dependency* row rather than acquiring a
    share-alike obligation — and a caller who quietly omits these kwargs would change the
    project's licence position without noticing (ADR-0022 A6).

    **The cost is real and is paid explicitly, not hidden.** Despite the name, this flag also
    gates whether ``coords`` are used for *solar* computation at all. Verified at Rhodes
    (36.4443, 28.2276) on the 2026 summer solstice for ``sunrise-sunset``, ``Europe/Athens``
    supplied explicitly in both runs: ``True`` gives the real solar window **05:49–20:28**
    local, ``False`` gives **07:00–19:00** — byte-identical to passing no coordinates at all.

    So with the flag off, ``coords`` cannot rescue a sun expression. That is exactly why the
    sun-event guard above refuses **unconditionally** rather than only when ``location`` is
    missing: a generic window returned as if it were the sun is the same silent-wrong-answer
    class as ``PH`` without a country, and it points either way depending on latitude and
    season. An earlier version of this module pinned ``True`` for the solar accuracy and had
    not yet seen the SBOM; the licence position is not tradeable for a selector M1 barely
    uses.
    """
    return OpeningHours(
        expression,
        timezone=zone,
        country=country_code,
        coords=coords,
        auto_country=False,
        auto_timezone=False,
    )
