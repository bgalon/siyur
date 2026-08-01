"""T033 — the ``research`` node: fan out to the source adapters over a polygon.

One pass over every configured :class:`~commons.sources.base.SourceAdapter`, collecting
the stamped records and one :class:`SourceReport` per source, so the API layer can stream
a ``status`` event per source and a ``summary`` carrying ``sources`` + ``degraded_sources``
(``specs/001-research-cited-sites/contracts/research.md``).

**FR-012 — a partial answer is a legitimate answer, an exception is not.** An adapter that
degrades internally already says so on its :class:`~commons.sources.base.FetchResult` (the
Overpass 504 shape). An adapter that *raises* is caught here and turned into the same
shape — ``found=0``, ``degraded=True``, the exception as the reason — and the remaining
sources still run. Nothing is ever fabricated to fill the gap: a degraded source
contributes zero records, honestly reported.

**This node makes no model call — deliberately.** ``tasks.md`` T033 labels it "Haiku tier
via the seam", but there is nothing here for a model to decide: the work is a
deterministic fan-out over adapters, and every value it emits was stamped at the source
boundary (ADR-0009). Introducing a model call would put an LLM on the path that carries
coordinates, in direct tension with the standing rule that *the LLM never emits or
computes spatial data* (`planner/AGENTS.md`, FR-005) — for zero benefit. The seam is
still where curate ranks (:mod:`planner.nodes.curate`, Sonnet tier); research stays
deterministic and offline-testable. Recorded as a decision, see the PR.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from commons.models import SiteRecordV1
from commons.sources.base import FetchResult, SourceAdapter

__all__ = ["ResearchResult", "SourceReport", "research"]

#: Used when an adapter fails so early that even its ``kind`` is unreadable. An
#: unidentifiable source is still reported — never silently dropped from the summary.
UNKNOWN_KIND = "unknown"


@dataclass(frozen=True, slots=True)
class SourceReport:
    """What one source contributed to a research pass — the ``status`` event's payload."""

    kind: str
    #: Records this source actually contributed (0 for a source that failed outright).
    found: int
    #: Drop reason → count, straight from the adapter (`commons.sources.base`).
    dropped: Mapping[str, int]
    #: FR-012: the source answered partially, or not at all.
    degraded: bool
    #: Why it degraded — set exactly when ``degraded``.
    reason: str | None


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """Every stamped record the pass collected, plus one report per adapter, in order."""

    records: tuple[SiteRecordV1, ...]
    reports: tuple[SourceReport, ...]

    @property
    def degraded_sources(self) -> tuple[str, ...]:
        """The ``summary.degraded_sources`` list of the research contract."""
        return tuple(report.kind for report in self.reports if report.degraded)

    @property
    def counts(self) -> dict[str, int]:
        """``summary.sources`` — kind → records found."""
        return {report.kind: report.found for report in self.reports}


def _kind_of(adapter: SourceAdapter) -> str:
    """The adapter's ``kind``, defensively: reporting it must not itself be able to fail."""
    try:
        return str(adapter.kind)
    except Exception:  # a source that cannot even name itself is still reported
        return UNKNOWN_KIND


def _report(result: FetchResult) -> SourceReport:
    return SourceReport(
        kind=str(result.kind),
        found=len(result.records),
        dropped=dict(result.dropped),
        degraded=result.degraded,
        reason=result.reason,
    )


def research(
    polygon: BaseGeometry,
    adapters: Sequence[SourceAdapter],
    *,
    on_source: Callable[[SourceReport], None] | None = None,
) -> ResearchResult:
    """Fetch ``polygon`` from every adapter; collect records and per-source reports.

    Deterministic: adapters are visited in the given order, records are concatenated in
    that order, and ``on_source`` fires **once per adapter**, immediately after it
    returns — so a caller streams progress per source rather than waiting for the pass.
    A raising adapter degrades into a report; it never aborts the pass (FR-012).
    """
    records: list[SiteRecordV1] = []
    reports: list[SourceReport] = []

    for adapter in adapters:
        try:
            result = adapter.fetch(polygon)
        except Exception as exc:  # FR-012: degrade this source, still run the rest
            report = SourceReport(
                kind=_kind_of(adapter),
                found=0,
                dropped={},
                degraded=True,
                reason=f"{type(exc).__name__}: {exc}",
            )
        else:
            report = _report(result)
            records.extend(result.records)
        reports.append(report)
        if on_source is not None:
            on_source(report)

    return ResearchResult(records=tuple(records), reports=tuple(reports))
