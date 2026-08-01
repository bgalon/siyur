"""T036 — the pipeline over the committed fixtures. Tier 1: no network, no key, no database.

What is pinned here is the **stream contract** of
`specs/001-research-cited-sites/contracts/research.md`: the frame sequence, the
all-values-stamped invariant, honest reporting of a degraded source, and the SC-006
promise that an empty area yields zero sites rather than an invented one.

The adapters and the fake router are the ones the node tests already build — reused
rather than re-mocked, so a change to either is felt in exactly one place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from commons.licenses import SourceKind
from commons.merge import MergeResult
from commons.models import SiteRecordV1
from commons.sources import base
from planner.nodes.resolve_area import AreaInvalid, AreaRequest
from planner.pipeline import PHASES, Event, run_research
from tests.test_planner_curate import FakeRouter
from tests.test_planner_research import (
    AREA,
    OSM_NODES,
    OVERPASS_JSON,
    OVERTURE_ROWS,
    osm,
    overture,
)

#: Somewhere the fixtures say nothing about — the SC-006 "empty area" case.
NOWHERE = box(0.0, 0.0, 0.001, 0.001)


def request_for(area: BaseGeometry) -> AreaRequest:
    """A user-drawn polygon, so resolution is deterministic and offline."""
    return AreaRequest(bbox=area.bounds)


@dataclass
class RecordingPersist:
    """A commons stand-in: structurally a ``Persist``, backed by a list instead of PostGIS."""

    seen: list[MergeResult] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    conflicts: int = 0

    def __call__(self, results: Sequence[MergeResult]) -> RecordingPersist:
        self.seen.extend(results)
        self.created = len(results)
        self.conflicts = sum(len(result.record.conflicts) for result in results)
        return self


class OrderRecordingAdapter:
    """Wraps a real adapter and notes *when* it was asked — the streaming claim's witness.

    ``kind`` delegates rather than being fixed, so a wrapped source still reports itself
    honestly; hard-coding it here would mislabel every frame the wrapper passes through.
    """

    def __init__(self, inner: base.SourceAdapter, log: list[str]) -> None:
        self.inner = inner
        self.log = log

    @property
    def kind(self) -> SourceKind:
        return self.inner.kind

    def fetch(self, polygon: BaseGeometry) -> base.FetchResult:
        self.log.append(self.inner.kind)
        return self.inner.fetch(polygon)


def drain(*, area: BaseGeometry = AREA, **kwargs: Any) -> list[Event]:
    adapters = kwargs.pop("adapters", None) or [overture(), osm(node=OVERPASS_JSON)]
    return list(run_research(request_for(area), adapters=adapters, **kwargs))


def phases_of(events: Sequence[Event]) -> list[str]:
    return [str(e.data["phase"]) for e in events if e.event == "status"]


def sourced_values(payload: Any) -> list[dict[str, Any]]:
    """Every ``SourcedValue``-shaped dict anywhere in a streamed site frame."""
    found: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if "value" in payload and "source" in payload:
            found.append(payload)
        for nested in payload.values():
            found.extend(sourced_values(nested))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(sourced_values(item))
    return found


@pytest.fixture(scope="module")
def healthy() -> list[Event]:
    return drain()


# ── the frame sequence (contracts/research.md, trajectory eval T048) ──────────


def test_phase_sequence_is_a_superset_of_the_trajectory(healthy: list[Event]) -> None:
    assert set(PHASES) <= set(phases_of(healthy))
    # …and in order: resolution precedes research, which precedes curation.
    ordered = [p for p in phases_of(healthy) if p in PHASES]
    assert ordered[0] == "resolve_area"
    assert ordered[-1] == "curate"


def test_frames_arrive_in_the_contract_order(healthy: list[Event]) -> None:
    names = [event.event for event in healthy]
    assert names[0] == "status"
    assert names[-2:] == ["summary", "done"]
    # Every `site` frame follows every `status` frame and precedes the summary.
    assert names.index("site") > max(i for i, n in enumerate(names) if n == "status")


def test_one_status_frame_per_source_naming_what_it_found(healthy: list[Event]) -> None:
    research_frames = [e for e in healthy if e.data.get("phase") == "research"]
    assert {str(e.data["source"]) for e in research_frames} == {"overture", "osm"}
    assert {str(e.data["source"]): e.data["found"] for e in research_frames} == {
        "overture": OVERTURE_ROWS,
        "osm": OSM_NODES,
    }


# ── the invariants the stream exists to guarantee ─────────────────────────────


def test_every_streamed_value_carries_a_source(healthy: list[Event]) -> None:
    """FR-003 / SC-002 — 100%, not 'almost every'."""
    values = [v for e in healthy if e.event == "site" for v in sourced_values(dict(e.data))]
    assert values, "the fixture area must stream sites, or this asserts nothing"
    for value in values:
        assert value["source"]["kind"]
        assert value["source"]["license"]
        assert value["bundleable"] is not None


def test_streamed_sites_are_schema_valid(healthy: list[Event]) -> None:
    frames = [dict(e.data) for e in healthy if e.event == "site"]
    assert frames
    for frame in frames:
        assert SiteRecordV1.model_validate(frame).schema_ver == "SiteRecordV1"


def test_locations_trace_to_a_geodata_source(healthy: list[Event]) -> None:
    """FR-005 — a coordinate is never model-emitted, so it always cites Overture or OSM."""
    for event in healthy:
        if event.event == "site":
            assert dict(event.data)["location"]["source"]["kind"] in {"overture", "osm"}


def test_summary_counts_the_pass_it_actually_ran(healthy: list[Event]) -> None:
    summary = next(e for e in healthy if e.event == "summary")
    sites = [e for e in healthy if e.event == "site"]
    assert summary.data["sites"] == len(sites)
    assert summary.data["sources"] == {"overture": OVERTURE_ROWS, "osm": OSM_NODES}
    assert summary.data["degraded_sources"] == []


# ── the honest-failure cases ──────────────────────────────────────────────────


def test_empty_area_reports_zero_and_fabricates_nothing() -> None:
    """SC-006 — 'nothing found' is a real answer; an invented place would not be."""
    events = drain(area=NOWHERE, adapters=[overture(), osm()])
    summary = next(e for e in events if e.event == "summary")
    assert summary.data["sites"] == 0
    assert [e for e in events if e.event == "site"] == []
    assert next(e for e in events if e.event == "done").event == "done"


def test_a_degraded_source_is_reported_and_the_others_survive() -> None:
    """FR-012 — a partial answer is a legitimate answer; silence is not."""
    events = drain(adapters=[overture(), osm(node=OVERPASS_JSON, way=504)])
    summary = next(e for e in events if e.event == "summary")
    assert "osm" in summary.data["degraded_sources"]
    assert summary.data["sources"]["overture"] == OVERTURE_ROWS
    degraded = next(e for e in events if e.data.get("source") == "osm")
    assert degraded.data["degraded"] is True
    assert degraded.data["reason"]


def test_resolution_failure_raises_before_any_frame_is_yielded() -> None:
    """The caller must still be able to send a 422 — so nothing may have streamed yet."""
    stream = run_research(AreaRequest(), adapters=[overture()])
    with pytest.raises(AreaInvalid):
        next(stream)


# ── the model may rank, and may do nothing else ───────────────────────────────


def test_model_asserted_values_are_rejected_not_streamed() -> None:
    """FR-003/FR-005 — an object in the ranking is how a value or coordinate sneaks back."""
    smuggled = '[{"id": "x", "location": {"type": "Point", "coordinates": [0, 0]}}]'
    events = drain(router=FakeRouter(reply=smuggled))
    curate_frame = next(e for e in events if e.data.get("phase") == "curate")
    assert curate_frame.data["rejected"], "the smuggled object must be refused, with a reason"
    # Refusing it costs ordering only — no record is lost and none is invented.
    assert len([e for e in events if e.event == "site"]) == curate_frame.data["merged"]


def test_a_dead_router_costs_ordering_never_records() -> None:
    events = drain(router=FakeRouter(raises=TimeoutError("model unreachable")))
    curate_frame = next(e for e in events if e.data.get("phase") == "curate")
    assert curate_frame.data["rejected"]
    assert curate_frame.data["merged"] > 0


# ── persistence is injected, and gates what the client is shown ───────────────


def test_persist_receives_the_curated_records_before_any_site_frame() -> None:
    """A `site` frame promises the place reached the commons — so the write happens first."""
    persist = RecordingPersist()
    stream = run_research(request_for(AREA), adapters=[overture()], persist=persist)
    events: list[Event] = []
    for event in stream:
        if event.event == "site" and not events:
            assert persist.seen, "a site was streamed before it was persisted"
        events.append(event)
    summary = next(e for e in events if e.event == "summary")
    assert summary.data["new"] == len(persist.seen)
    assert summary.data["reused"] == 0


def test_without_a_persist_nothing_is_written_and_the_stream_still_completes() -> None:
    events = drain(adapters=[overture()])
    assert next(e for e in events if e.event == "summary").data["new"] == 0
    assert events[-1].event == "done"


# ── the design claim in the module docstring ──────────────────────────────────


def test_progress_streams_before_the_next_source_is_fetched() -> None:
    """Why research is driven per-adapter: a slow source must not silence the fast one."""
    log: list[str] = []
    adapters = [
        OrderRecordingAdapter(overture(), log),
        OrderRecordingAdapter(osm(node=OVERPASS_JSON), log),
    ]
    stream = run_research(request_for(AREA), adapters=adapters)
    next(stream)  # resolve_area
    assert log == [], "no source is touched until the polygon is resolved"
    first = next(stream)  # the first source's status frame
    assert first.data["source"] == "overture"
    assert log == ["overture"], "the second source must not have been fetched yet"
    rest = list(stream)
    assert log == ["overture", "osm"]
    # …and the wrapper did not relabel the source it delegates to.
    assert [e.data["source"] for e in rest if e.data.get("phase") == "research"] == ["osm"]
