"""T034 + T059 — the ``curate`` node. Tier 1: a fake router, no API key, no network.

Two halves. The **fixture half** drives the real adapters over the committed Rhodes data
and asserts the cross-source anchor (`tests/fixtures/README.md`: the same gate, 3.4 m
apart, seen by Overture and OSM) merges into one record keeping both source refs, and
gains an `el-Latn` twin that inherits its parent's stamp. The **fake-router half** pins
what the model is and is not allowed to do: it may reorder ids, and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from shapely import Point

from commons.geo import Wgs84Point
from commons.llm import (
    DEFAULT_MAX_TOKENS,
    Completion,
    Effort,
    Message,
    TaskTier,
    ToolSpec,
    Usage,
)
from commons.merge import MergeResult
from commons.models import SiteRecordV1, SourcedValue, SourceRef
from planner.nodes.curate import CurateResult, curate
from planner.nodes.research import research
from tests.test_planner_research import AREA, OBSERVED, OVERPASS_JSON, osm, overture

OVERPASS_JSON_NODE = "node/794491388"
GERS_ANCHOR = "0425b08a-fed7-4c86-b172-9645558883f2"
OSM = SourceRef(
    kind="osm", id=OVERPASS_JSON_NODE, license="ODbL-1.0", attribution="© OpenStreetMap"
)
DERIVED_ON = date(2026, 8, 1)
ANCHOR = (28.2247, 36.4443)


def record(
    *,
    names: dict[str, str],
    lon_lat: tuple[float, float] = ANCHOR,
    address: str | None = None,
    source: SourceRef = OSM,
) -> SiteRecordV1:
    """A minimal valid slice-001 record — stamped names, a stamped location."""

    def stamp(value: object) -> SourcedValue[Any]:
        return SourcedValue[Any].stamp(
            value=value, source=source, confidence=0.8, observed_at=OBSERVED
        )

    return SiteRecordV1(
        names={tag: stamp(value) for tag, value in names.items()},
        location=SourcedValue[Wgs84Point].stamp(
            value=Point(*lon_lat), source=source, confidence=0.8, observed_at=OBSERVED
        ),
        address=stamp(address) if address is not None else None,
    )


@dataclass
class FakeRouter:
    """A local :class:`commons.llm.ModelRouter` — no provider, no key, no network."""

    reply: str = "[]"
    raises: Exception | None = None
    calls: list[tuple[TaskTier, str]] = field(default_factory=list)

    def complete(
        self,
        tier: TaskTier,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_prefix: bool = False,
        long_run: bool = False,
        adaptive_thinking: bool = True,
        effort: Effort | None = None,
    ) -> Completion:
        self.calls.append((tier, messages[-1].content))
        if self.raises is not None:
            raise self.raises
        return Completion(text=self.reply, usage=Usage(model="fake"))


def ids_of(result: CurateResult) -> list[str]:
    return [str(merged.record.id) for merged in result.results]


# ── the fixture half: real Overture + OSM records through merge and transliteration ──


@pytest.fixture(scope="module")
def curated() -> CurateResult:
    records = research(AREA, [overture(), osm(node=OVERPASS_JSON)]).records
    return curate(records, observed_at=DERIVED_ON)


def anchor_of(curated: CurateResult) -> MergeResult:
    """The merged gate: the one result carrying both the Overture GERS id and the OSM node."""
    matches = [
        merged
        for merged in curated.results
        if merged.record.gers_id == GERS_ANCHOR
        or any(ref.id == OVERPASS_JSON_NODE for ref in merged.source_refs)
    ]
    assert len(matches) == 1, "the cross-source anchor must merge into exactly one record"
    return matches[0]


def test_the_cross_source_anchor_merges_and_keeps_both_source_refs(curated: CurateResult) -> None:
    merged = anchor_of(curated)
    assert len(merged.merged_from) == 2, "one Overture row + one OSM node, one place"
    kinds = {ref.kind for ref in merged.source_refs}
    assert kinds == {"overture", "osm"}, "FR-009: merge never discards a source"
    assert merged.record.gers_id == GERS_ANCHOR
    assert any(ref.id == OVERPASS_JSON_NODE for ref in merged.source_refs)


def test_greek_names_gain_a_latn_twin_that_inherits_its_parents_stamp(
    curated: CurateResult,
) -> None:
    names = anchor_of(curated).record.names
    parent, derived = names["el"], names["el-Latn"]
    assert parent.value == "Πύλη Ταρσανά", "the original is never overwritten"
    assert derived.value == "Pyli Tarsana"
    # T058: provenance is inherited, never invented — the very same SourceRef.
    assert derived.source == parent.source
    assert derived.bundleable is parent.bundleable is True  # ODbL is allowlisted
    assert derived.observed_at == DERIVED_ON


def test_derived_values_reach_the_provenance_ledger(curated: CurateResult) -> None:
    """Nothing added at curate time is invisible to the audit trail."""
    merged = anchor_of(curated)
    ledger = {observation.field: observation.value for observation in merged.provenance}
    assert ledger["names.el-Latn"] is merged.record.names["el-Latn"]
    assert curated.derived_names >= 1
    assert curated.conflicts == sum(len(m.record.conflicts) for m in curated.results)


# ── the fake-router half: what the model may and may not do ──────────────────────────


def test_transliteration_touches_names_only_and_never_an_address() -> None:
    """FAIL-001 / ADR-0010: addresses are validated, never transliterated."""
    result = curate(
        [record(names={"el": "Ρόδος"}, address="Οδός Σωκράτους")], observed_at=DERIVED_ON
    )
    merged = result.results[0].record
    assert merged.names["el-Latn"].value == "Rodos"
    assert result.derived_names == 1, "only the name was derived"
    assert merged.address is not None and merged.address.value == "Οδός Σωκράτους"
    # The address ledger holds the original and nothing derived from it.
    ledger = {
        observation.value.value
        for entry in result.results
        for observation in entry.provenance
        if observation.field == "address"
    }
    assert ledger == {"Οδός Σωκράτους"}


def test_a_script_mismatch_is_flagged_and_never_transliterated() -> None:
    """The FAIL-001 shape: a value whose actual script contradicts its tag."""
    result = curate([record(names={"el": "Сгула"})], observed_at=DERIVED_ON)
    assert [flag.tag for flag in result.flags] == ["el"]
    assert result.flags[0].value == "Сгула"
    assert "FAIL-001" in result.flags[0].reason
    assert "el-Latn" not in result.results[0].record.names
    assert result.derived_names == 0


def three() -> list[SiteRecordV1]:
    """Three records far enough apart that the join rule keeps them three."""
    return [
        record(names={"en": "Palace"}, lon_lat=(28.2247, 36.4443)),
        record(names={"en": "Harbour"}, lon_lat=(28.2270, 36.4450)),
        record(names={"en": "Museum"}, lon_lat=(28.2290, 36.4460)),
    ]


def test_router_none_keeps_merge_order_and_refuses_nothing() -> None:
    records = three()
    result = curate(records, observed_at=DERIVED_ON)
    assert ids_of(result) == [str(r.id) for r in records]
    assert result.rejected == ()


def test_the_model_reorders_and_sees_ids_and_names_only() -> None:
    records = three()
    order = [str(records[2].id), str(records[0].id), str(records[1].id)]
    router = FakeRouter(reply=json.dumps(order))
    result = curate(records, router=router, observed_at=DERIVED_ON)

    assert ids_of(result) == order
    assert result.rejected == ()
    tier, payload = router.calls[0]
    assert tier is TaskTier.CURATE, "curate routes at the Sonnet tier, never a named model"
    # FR-005: no coordinate ever crosses the seam — the payload is ids and names only.
    sent = json.loads(payload)
    assert [sorted(item) for item in sent] == [["id", "names"]] * 3
    assert "36.44" not in payload


def test_an_unknown_id_is_refused_and_omitted_ids_are_kept_in_input_order() -> None:
    records = three()
    router = FakeRouter(reply=json.dumps(["not-a-record-id", str(records[2].id)]))
    result = curate(records, router=router, observed_at=DERIVED_ON)

    assert len(result.rejected) == 1
    assert "unknown id" in result.rejected[0] and "not-a-record-id" in result.rejected[0]
    # Ranked first, then everything the model omitted, in input order. Nothing is lost.
    assert ids_of(result) == [str(records[2].id), str(records[0].id), str(records[1].id)]


def test_a_model_asserting_a_value_or_a_coordinate_is_refused() -> None:
    """FR-003 / FR-005: the model may return ids, never a value, a field or a point."""
    records = three()
    router = FakeRouter(
        reply=json.dumps(
            [{"id": str(records[0].id), "location": [28.2247, 36.4443], "name": "Grand Palace"}]
        )
    )
    result = curate(records, router=router, observed_at=DERIVED_ON)

    assert len(result.rejected) == 1
    assert "refused a dict" in result.rejected[0]
    assert ids_of(result) == [str(r.id) for r in records], "merge order stands"
    for merged in result.results:
        assert "Grand Palace" not in {value.value for value in merged.record.names.values()}


def test_a_duplicate_id_and_a_non_array_reply_are_refused() -> None:
    records = three()
    duplicated = FakeRouter(reply=json.dumps([str(records[0].id), str(records[0].id)]))
    result = curate(records, router=duplicated, observed_at=DERIVED_ON)
    assert len(result.rejected) == 1 and "duplicate id" in result.rejected[0]
    assert ids_of(result) == [str(r.id) for r in records]

    prose = FakeRouter(reply="The Palace is clearly the most interesting.")
    refused = curate(records, router=prose, observed_at=DERIVED_ON)
    assert len(refused.rejected) == 1 and "not JSON" in refused.rejected[0]
    assert ids_of(refused) == [str(r.id) for r in records]


def test_an_unavailable_model_costs_order_not_records() -> None:
    records = three()
    router = FakeRouter(raises=TimeoutError("the ranking call timed out"))
    result = curate(records, router=router, observed_at=DERIVED_ON)
    assert ids_of(result) == [str(r.id) for r in records]
    assert len(result.rejected) == 1 and "TimeoutError" in result.rejected[0]
