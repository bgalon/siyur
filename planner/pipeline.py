"""The research pipeline — ``resolve_area → research → curate → persist`` as a stream (T035).

The whole slice-001 write path in one generator. It yields exactly the frames
`specs/001-research-cited-sites/contracts/research.md` specifies, so ``api/areas.py`` is a
thin SSE transport shim and the trajectory eval (T048) asserts the phase sequence
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
frames (SC-006).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from commons.llm import ModelRouter
from commons.merge import MergeResult
from commons.sources.base import SourceAdapter
from planner.nodes.curate import curate
from planner.nodes.research import SourceReport, research
from planner.nodes.resolve_area import (
    AreaRequest,
    DivisionsLookup,
    Geocoder,
    resolve_area,
)

__all__ = ["PHASES", "Event", "Persist", "PersistOutcome", "run_research"]

EventName = Literal["status", "site", "summary", "done"]

#: The phase sequence the trajectory eval (T048) asserts a `superset` of.
PHASES: tuple[str, ...] = ("resolve_area", "research", "curate")


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
