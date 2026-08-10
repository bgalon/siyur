"""T020 — the ``propose_itinerary`` node: the model picks an order, machinery does the rest.

The **Opus tier** of the :class:`~commons.llm.ModelRouter` seam (ADR-0004's ``plan`` tier;
this is its first caller). What the model contributes to a day is exactly one thing: **an
ordered subset of the candidate site ids**. Everything else in the returned
:class:`~commons.models.ItineraryV1` is computed:

* **coordinates** are read off the commons record's own stamped ``location`` — the only
  place in this module that touches a number that means a place;
* **distance and duration** come from :mod:`commons.routing` (Valhalla over OSM, ODbL);
* **wall-clock times** are laid out here from ``day_start`` plus those leg durations and the
  configured dwell.

## The model cannot emit a numeric, because it is never asked for a shape that holds one

FR-004 is merge-blocking, and a prompt is not an enforcement mechanism (Constitution
Article VI). So the reply contract is *a JSON array of id strings* and :func:`_select`
refuses every element that is not one: an object — the shape a ``{"site_id": …,
"planned_start": "10:00"}`` would have to arrive in — is refused **unread**, as are a number,
an array, an unknown id and a repeat. A refusal is named in :attr:`Proposal.rejected` and
costs the day a stop; it is never repaired into a value, because a repaired model numeric is
a model numeric with the evidence removed. The request the model sees carries ids, names and
categories and **no coordinate at all**, so there is nothing spatial for it to reason from
even if it tried.

Two more things follow from "the model does not emit durations": it does not choose
``dwell_min`` (a duration), which is a caller-supplied policy, and it does not choose
``planned_start`` (a time), which is arithmetic over the routed legs.

## Stops come from the commons, and an unroutable one is dropped, not straight-lined

Every selected id must resolve to a record in ``candidates`` — the planner invents no place
(FR-002). And legs are routed **one consecutive pair at a time**, which is the part of this
module most likely to look like an inefficiency and is not: a single multi-waypoint call
fails as a *unit*, so one unreachable pair loses every leg of the day and says nothing about
which stop to drop. Attribution would then be either a guess or a straight line between the
two points, and FR-003 exists because a two-point line is a straight line pretending to be a
route. Pairwise, :class:`~commons.routing.NoRouteFound` names exactly one stop; that stop is
excluded, recorded in :attr:`Proposal.excluded`, and the day closes over the gap. The cost is
*n−1* requests to a local container.

## What it refuses to do

An area whose commons holds no candidates yields an **honest empty plan** — never padding
(`contracts/plans.md`). But a model that answers nothing usable while candidates *do* exist
raises :class:`ProposalRefused` rather than returning an empty day: an empty day is
trivially within every budget, so it would sail through :mod:`planner.feasibility`, reach the
approval gate green, and compile into a bundle of nothing. Failing to plan and finding
nothing to plan are different facts and must not share a representation.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from commons.llm import Message, ModelRouter, TaskTier
from commons.models import (
    Budgets,
    ItineraryV1,
    RouteLegV1,
    SiteRecordV1,
    Stop,
    Timeline,
    TimelineEntry,
)
from commons.routing import (
    NoRouteFound,
    PedestrianCosting,
    RoutingError,
    RoutingProvider,
    Waypoint,
)

__all__ = [
    "DEFAULT_DWELL_MIN",
    "DEFAULT_MAX_STOPS",
    "PROPOSAL_SYSTEM_PROMPT",
    "ExcludedStop",
    "Proposal",
    "ProposalRefused",
    "propose_itinerary",
]

#: Minutes at each stop. A **duration**, therefore never the model's to choose (FR-004), and
#: a parameter rather than a per-place guess: M1 gives every stop the same dwell and says so,
#: which is honest, whereas a model-supplied "45 minutes at this one" would be a number with
#: no source. Per-place dwell is a real product question and belongs to a later slice.
DEFAULT_DWELL_MIN = 45

#: The cap on a day. Enforced after the reply, not merely requested in it.
DEFAULT_MAX_STOPS = 6

#: The selection contract, stated to the model — and enforced afterwards regardless, because
#: a prompt is a courtesy and :func:`_select` is the guarantee. Sent with ``cache_prefix=True``
#: (FAIL-006: a breakpoint below the tier model's minimum cacheable prefix silently no-ops, so
#: the prefix is written to be worth caching). Editing this text owes a `prompts/` registry
#: entry and a version bump in the same PR (Article VII) — see this module's PR notes.
PROPOSAL_SYSTEM_PROMPT = (
    "You plan one day of walking sightseeing in an unfamiliar area by choosing which of the "
    "given candidate places a visitor should see, and in what order.\n\n"
    "## What you are given\n\n"
    "A JSON object with two keys. `interests` is the traveller's own words about what they "
    "want from the day, and may be empty. `candidates` is a JSON array of objects with "
    "exactly three keys: `id`, an opaque identifier; `names`, one or more names for the same "
    "place in one or more languages or scripts; and `categories`, zero or more category "
    "labels as the source data recorded them. That is the whole of the evidence. There are "
    "no coordinates, no distances, no travel times, no opening hours and no descriptions, "
    "and you must neither ask for them nor assume any.\n\n"
    "You are also told `max_stops`, the largest number of places the day may contain.\n\n"
    "## What you return\n\n"
    "Reply with a JSON array of the given `id` strings, in the order the visitor should walk "
    "them, and NOTHING else — no prose, no code fence, no objects, no scores, no coordinates, "
    "no distances, no durations, no times of day, no names. Return at most `max_stops` ids. "
    "Include an id at most once. You are selecting, not ranking: leaving a candidate out is "
    "the normal case and needs no explanation, and a shorter day of places that belong "
    "together is better than a longer one padded to the cap.\n\n"
    "Any element that is not one of the given id strings is discarded and reported as a "
    "refusal, and so is a repeated id, an id beyond the cap, and an id that was not offered "
    "to you. A malformed reply costs the day those stops and nothing else is taken from it.\n\n"
    "## The boundary, which is not a formatting preference\n\n"
    "You do not decide where anything is, how far apart two places are, how long the walk "
    "between them takes, how long the visitor stays, or what time they arrive. Every one of "
    "those numbers is computed after you answer: the distances and walking times by a routing "
    "engine over the street network, the arrival times by arithmetic over those walking times, "
    "and the opening windows by a deterministic evaluator reading each place's own opening "
    "hours in the area's local clock. A number offered by you in any of those slots would "
    "carry no source, no license and no observation date, so it is discarded unread rather "
    "than checked. This is not a trust judgement about you. It is that a plausible distance "
    "is indistinguishable from a measured one once it is written down, and the person reading "
    "it will be standing in the street with no signal and no way to tell.\n\n"
    "The same applies to places. Every candidate here was read from a licensed data source "
    "and stamped with it. A place you know of that is not in the list cannot be added, "
    "however certain you are that it exists: it would arrive with no provenance, and the "
    "traveller would be sent to somewhere nothing vouches for. Choose from the list or "
    "choose fewer places.\n\n"
    "## Choosing the places\n\n"
    "Take the traveller's `interests` as the strongest signal when they give any, and read "
    "them generously rather than literally — a request for quiet suggests avoiding the "
    "busiest attractions, not only places whose names contain a word about quiet. With no "
    "interests stated, choose the places a first-time visitor would most regret missing.\n\n"
    "In descending order of pull: named landmarks — monuments, archaeological sites, castles, "
    "fortifications, gates, towers; museums and galleries, and religious buildings of "
    "historic or architectural note; named viewpoints, beaches, gardens, parks and squares, "
    "whose draw is the setting; named markets, historic streets and quarters, harbours and "
    "waterfronts; ordinary hospitality and retail, which come last among places a visitor "
    "would choose unless a name marks one out as an institution in its own right; and "
    "infrastructure and civic function — car parks, clinics, offices, schools, stops, depots "
    "— which are the floor whatever else the name suggests, and are usually best left out "
    "entirely.\n\n"
    "Prefer a day with some variety of kind over four versions of the same experience, and "
    "prefer a candidate whose name or categories say something specific over one that could "
    "describe anything. Where two candidates look like the same real place recorded twice — a "
    "name and its transliteration, the same place in two scripts — choose one of them and "
    "leave the other out, because visiting a place twice is not a plan.\n\n"
    "A name in a script you find harder to read is a property of the source data and never a "
    "signal about the place. Do not rank such a place lower, and do not prefer a place "
    "because its name happens to be in English.\n\n"
    "## Choosing the order\n\n"
    "Order for the shape of the day rather than for distance, which you cannot see. Open "
    "where a visitor would want to start and close where they would want to end; put the "
    "demanding places earlier and the restful ones later; keep places that plainly belong to "
    "one quarter or one theme adjacent to each other. If the routing engine finds no walking "
    "path to a place you chose, that place is dropped from the day and the rest of your order "
    "is kept, so an order that reads sensibly end to end survives a dropped stop better than "
    "one that depends on a single pivot.\n\n"
    "## Worked example\n\n"
    "Given:\n\n"
    '{"interests": "old stonework, and somewhere to sit at the end",\n'
    ' "max_stops": 4,\n'
    ' "candidates": [\n'
    '   {"id": "7f3a", "names": ["Municipal Garden"], "categories": ["park"]},\n'
    '   {"id": "b129", "names": ["Upper Fortress"], "categories": ["castle", "landmark"]},\n'
    '   {"id": "c004", "names": ["Parking P3"], "categories": ["parking"]},\n'
    '   {"id": "d51e", "names": ["Museum of the Citadel"], "categories": ["museum"]},\n'
    '   {"id": "e77b", "names": ["Harbour Promenade"], "categories": ["waterfront"]}]}\n\n'
    "Reply:\n\n"
    '["b129", "d51e", "e77b", "7f3a"]\n\n'
    "The fortress and its museum answer the stated interest and lead the day; the promenade "
    "and the garden are the restful end the traveller asked for. The car park is "
    "infrastructure and is simply left out — the day is four stops because four places earned "
    "a place in it, not because the cap was four. No arrival time, walking distance or dwell "
    'appears anywhere in the reply. A reply of `[{"id": "b129", "planned_start": "10:00"}]` '
    "earns nothing: the object is refused on sight, because an object is the shape in which a "
    "time, a distance or a coordinate would arrive, and the day would simply lose that stop."
)


class ProposalRefused(RuntimeError):
    """The model produced no usable ordering, so there is no plan to return.

    Deliberately an exception and not an empty :class:`Proposal`: an itinerary with no stops
    walks zero metres in zero hours and is therefore *feasible*, so returning one would put a
    green tick on a failure and let it through the approval gate.
    """


@dataclass(frozen=True, slots=True)
class ExcludedStop:
    """A place the model chose that the day could not keep, and why."""

    site_id: UUID
    #: ``"unroutable"`` — the walking network reaches no path to it from the previous stop.
    #: The SSE ``route`` frame's ``excluded[].reason`` (`contracts/plans.md`).
    reason: str
    #: The engine's own message, kept verbatim rather than summarised.
    detail: str


@dataclass(frozen=True, slots=True)
class Proposal:
    """The proposed day, plus everything that did not make it and why."""

    itinerary: ItineraryV1
    #: Chosen, then dropped because nothing could walk there (FR-003).
    excluded: tuple[ExcludedStop, ...]
    #: Model output that was refused, one human-readable reason per offending element.
    rejected: tuple[str, ...]


def _proposal_request(candidates: Sequence[SiteRecordV1], *, interests: str, max_stops: int) -> str:
    """The user turn: **ids, names and categories only** — no coordinate crosses the seam."""
    return json.dumps(
        {
            "interests": interests,
            "max_stops": max_stops,
            "candidates": [
                {
                    "id": str(record.id),
                    "names": [record.names[tag].value for tag in sorted(record.names)],
                    "categories": [category.value for category in record.categories],
                }
                for record in candidates
            ],
        },
        ensure_ascii=False,
    )


def _decode_ordering(text: str) -> list[object]:
    """Parse the reply into raw elements, or refuse the whole thing.

    A reply that is not a JSON array is not partially usable: there is no ordering in it to
    salvage, and salvaging one by pattern-matching ids out of prose would be inventing the
    day the model failed to state.
    """
    stripped = text.strip()
    if not stripped:
        raise ProposalRefused("the model returned an empty ordering; there is no day to propose")
    try:
        decoded: object = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProposalRefused(f"the model's ordering is not JSON ({exc.msg})") from exc
    if not isinstance(decoded, list):
        raise ProposalRefused(
            f"the model returned a {type(decoded).__name__}, not an array of site ids"
        )
    return list(decoded)


def _select(
    candidates: Sequence[SiteRecordV1], elements: Sequence[object], *, max_stops: int
) -> tuple[tuple[SiteRecordV1, ...], tuple[str, ...]]:
    """Resolve the model's elements to commons records, refusing anything else.

    This is where FR-002 and FR-004 are actually enforced. Nothing is repaired: an element
    that is not a known, not-yet-chosen id string is dropped with a stated reason, and the
    day is shorter by that stop.
    """
    by_id = {str(record.id): record for record in candidates}
    chosen: list[SiteRecordV1] = []
    seen: set[str] = set()
    rejected: list[str] = []

    for element in elements:
        if not isinstance(element, str):
            rejected.append(
                f"refused a {type(element).__name__} in the ordering "
                f"({json.dumps(element, ensure_ascii=False, default=str)}): the model returns "
                "site ids only — never a coordinate, a distance, a duration or a time"
            )
            continue
        if element not in by_id:
            rejected.append(
                f"refused unknown site id {element!r}: a stop must reference a commons record "
                "and the planner may not invent a place"
            )
            continue
        if element in seen:
            rejected.append(f"refused duplicate site id {element!r}: a day visits a place once")
            continue
        if len(chosen) >= max_stops:
            rejected.append(f"refused site id {element!r}: the day is capped at {max_stops} stops")
            continue
        seen.add(element)
        chosen.append(by_id[element])

    return tuple(chosen), tuple(rejected)


def _waypoint(record: SiteRecordV1) -> Waypoint:
    """The record's **own stamped** EPSG:4326 point — (lon, lat), from geodata, never a model."""
    point = record.location.value
    return Waypoint(lon=point.x, lat=point.y)


def _route_pairwise(
    chosen: Sequence[SiteRecordV1], *, routing: RoutingProvider, costing: PedestrianCosting
) -> tuple[tuple[SiteRecordV1, ...], tuple[RouteLegV1, ...], tuple[ExcludedStop, ...]]:
    """Route consecutive pairs, dropping any stop the walking network cannot reach.

    Legs come back from the seam labelled for *their own* call (``leg-0``, stops 0→1), so
    each is relabelled to its position in the day. ``model_copy(update=…)`` re-validates on a
    ``StampedModel``, so a relabelled leg is held to the same rules as one built from scratch
    — and a forgotten relabel is not a silent bug: ``ItineraryV1`` rejects duplicate leg ids.
    """
    kept: list[SiteRecordV1] = []
    legs: list[RouteLegV1] = []
    excluded: list[ExcludedStop] = []

    for record in chosen:
        if not kept:
            kept.append(record)
            continue
        try:
            routed = routing.route([_waypoint(kept[-1]), _waypoint(record)], costing=costing)
        except NoRouteFound as exc:
            # FR-003: no path means no stop. Joining the two points with a line would be a
            # straight line pretending to be a route, and offline there is nothing to correct
            # it with — the traveller would simply be walked into the sea.
            excluded.append(ExcludedStop(site_id=record.id, reason="unroutable", detail=str(exc)))
            continue
        if len(routed) != 1:
            raise RoutingError(
                f"routing returned {len(routed)} legs for one pair of waypoints; the seam "
                "contract is one leg per consecutive pair"
            )
        index = len(kept) - 1
        legs.append(
            routed[0].model_copy(
                update={"id": f"leg-{index}", "from_stop": index, "to_stop": index + 1}
            )
        )
        kept.append(record)

    return tuple(kept), tuple(legs), tuple(excluded)


def _schedule(
    kept: Sequence[SiteRecordV1],
    legs: Sequence[RouteLegV1],
    *,
    day: date,
    day_start: time,
    dwell_min: int,
) -> tuple[tuple[Stop, ...], Timeline]:
    """Lay the routed day onto the area-local wall clock, from ``day_start`` forwards.

    Leg seconds are rounded **up** to whole minutes: the timeline is minute-granular, and
    rounding a 61-second walk down schedules the traveller to arrive before they could have
    got there. A day that runs past midnight simply wraps — ``ItineraryV1`` carries one
    ``date`` and :mod:`planner.feasibility` is what reads the wrap forwards.
    """
    clock = datetime.combine(day, day_start)
    stops: list[Stop] = []
    entries: list[TimelineEntry] = []

    for index, record in enumerate(kept):
        if index:
            leg = legs[index - 1]
            walk_min = math.ceil(leg.duration_s / 60)
            entries.append(TimelineEntry(leg_id=leg.id, start=clock.time(), duration_min=walk_min))
            clock += timedelta(minutes=walk_min)
        stops.append(
            Stop(
                site_id=record.id,
                order=index,
                planned_start=clock.time(),
                dwell_min=dwell_min,
            )
        )
        entries.append(TimelineEntry(stop_order=index, start=clock.time(), duration_min=dwell_min))
        clock += timedelta(minutes=dwell_min)

    return tuple(stops), Timeline(entries=tuple(entries))


def propose_itinerary(
    candidates: Sequence[SiteRecordV1],
    *,
    router: ModelRouter,
    routing: RoutingProvider,
    costing: PedestrianCosting,
    user_id: str,
    area_id: UUID,
    day: date,
    day_start: time,
    budgets: Budgets,
    lang: str = "en",
    interests: str = "",
    max_stops: int = DEFAULT_MAX_STOPS,
    dwell_min: int = DEFAULT_DWELL_MIN,
) -> Proposal:
    """Propose one walking day over ``candidates``. The model orders ids; nothing else.

    :param candidates: the commons records a stop may reference. The model sees their ids,
        names and categories — never their locations.
    :param costing: the traveller's pace, explicitly. :class:`PedestrianCosting` has no
        default ``walking_speed_kmh`` on purpose (ADR-0020) and this node adds none: a
        defaulted pace silently reprices every leg of the day.
    :param day: populates ``ItineraryV1.date`` — the calendar day in the *area's* frame.
        Named ``day`` because ``date`` is the type it is annotated with.
    :param day_start: the area-local wall clock the day begins at, a request-only input
        (`contracts/plans.md`); ``budgets.hours`` is a duration and cannot anchor a day.

    Raises:
        ProposalRefused: the model failed to produce a usable ordering (see the module
            docstring for why this is not an empty plan).
        RoutingError / RoutingUnavailable: the engine itself failed. Only
            :class:`~commons.routing.NoRouteFound` is handled here, because "no path to this
            place" is a fact about the plan and "the engine is down" is not.
    """
    if not candidates:
        # "Not enough here" is a finding about the area, and the honest answer is a short
        # plan — never padding, and never a model call to dress up an empty list.
        return Proposal(
            itinerary=_itinerary(
                user_id=user_id,
                area_id=area_id,
                day=day,
                lang=lang,
                budgets=budgets,
                stops=(),
                legs=(),
                timeline=Timeline(),
            ),
            excluded=(),
            rejected=("the commons holds no candidate places for this area",),
        )

    completion = router.complete(
        TaskTier.PLAN,
        system=PROPOSAL_SYSTEM_PROMPT,
        messages=[
            Message(
                role="user",
                content=_proposal_request(candidates, interests=interests, max_stops=max_stops),
            )
        ],
        cache_prefix=True,
    )

    chosen, rejected = _select(candidates, _decode_ordering(completion.text), max_stops=max_stops)
    if not chosen:
        raise ProposalRefused(
            "the model selected no usable place from "
            f"{len(candidates)} candidates: {'; '.join(rejected) or 'the ordering was empty'}"
        )

    kept, legs, excluded = _route_pairwise(chosen, routing=routing, costing=costing)
    stops, timeline = _schedule(kept, legs, day=day, day_start=day_start, dwell_min=dwell_min)
    return Proposal(
        itinerary=_itinerary(
            user_id=user_id,
            area_id=area_id,
            day=day,
            lang=lang,
            budgets=budgets,
            stops=stops,
            legs=legs,
            timeline=timeline,
        ),
        excluded=excluded,
        rejected=rejected,
    )


def _itinerary(
    *,
    user_id: str,
    area_id: UUID,
    day: date,
    lang: str,
    budgets: Budgets,
    stops: tuple[Stop, ...],
    legs: tuple[RouteLegV1, ...],
    timeline: Timeline,
) -> ItineraryV1:
    """One construction site, so the two return paths cannot disagree about the shape."""
    return ItineraryV1(
        user_id=user_id,
        area_id=area_id,
        date=day,
        lang=lang,
        stops=stops,
        legs=legs,
        timeline=timeline,
        budgets=budgets,
    )
