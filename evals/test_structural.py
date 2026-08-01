"""Structural evals — merge-blocking on SC-002, FR-005 and FAIL-001 (T046 / T047 / T061).

Constitution Article II: *correctness is proven by evals, not asserted*. These run in CI
job 4 (`eval-quality.yml` → `deterministic-evals`), a **required** merge check, over the
committed Rhodes fixtures with no network, no API key and no database.

**Why these are not a second copy of `tests/`.** `tests/test_pipeline.py` proves the code
works — this frame follows that one, this adapter degrades honestly. An eval asserts a
*product success criterion* over the whole output of a real pass, as a measured property:

* SC-002 says **100 %**, so the eval computes the stamped-value *rate* over every value of
  every record a pass produced and demands 1.0 with N > 0 — a sampled or per-field check
  would pass while quietly skipping a field nobody remembered.
* The walk is :func:`commons.merge.iter_sourced_values`, and
  :func:`test_the_provenance_walk_covers_every_sourced_field_of_the_schema` holds *that*
  walker to the schema: a ``SourcedValue`` field added to :class:`SiteRecordV1` later is
  covered automatically, or the eval reddens.
* The quarantine is read out of :mod:`commons.licenses`, never copied. ADR-0012 added
  ``Apache-2.0`` to the allowlist on 2026-08-01; a hardcoded list here would have gone
  stale that day and gone on passing.

Every eval that quantifies over the fixture set asserts the set is non-empty first: an
eval that passes vacuously on zero records is worse than no eval at all.
"""

from __future__ import annotations

import types
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Union, get_args, get_origin

import pytest
from shapely import Point
from shapely.geometry.base import BaseGeometry

from commons import licenses
from commons.licenses import SourceKind
from commons.merge import iter_sourced_values
from commons.models import SiteRecordV1, SourcedValue, SourceRef
from commons.sources.base import FetchResult
from planner.nodes.curate import curate
from planner.pipeline import Event
from tests.test_pipeline import drain, sourced_values
from tests.test_planner_curate import DERIVED_ON, FakeRouter, record
from tests.test_planner_research import OBSERVED, OVERPASS_JSON, osm, overture

#: FR-005 — the only source kinds that may ever stamp a coordinate. A model is not one.
GEODATA_KINDS: Final[frozenset[str]] = frozenset({"overture", "osm"})

#: A stamp for the synthetic records these evals build. ODbL is allowlisted, so a value
#: carrying it is *expected* to be bundleable — the quarantine evals below need both sides.
STAMP: Final = SourceRef(kind="osm", id="eval/stamp", license="ODbL-1.0")

#: An open-web source: never bundleable whatever license it claims (DATA-LICENSES.md).
QUARANTINED: Final = SourceRef(kind="open_web", id="https://example.invalid/", license="CC0-1.0")

#: The FAIL-001 datum, verbatim from `docs/failures/FAIL-001.md`: the Hebrew street
#: ``סגולה`` stored by a commercial POI source as the Cyrillic ``Сгула``.
MIS_SCRIPTED: Final = "Сгула 13"

#: Far enough from the Rhodes fixtures that the join rule (ε = 25 m) can never merge the
#: injected record into a real one, so "exactly one flag" stays a statement about it.
ELSEWHERE: Final = (28.30, 36.50)


# ── the pass every structural eval is measured over ───────────────────────────────────


@pytest.fixture(scope="module")
def events() -> list[Event]:
    """One real research pass over the committed Rhodes fixtures — offline, no model."""
    return drain()


@pytest.fixture(scope="module")
def records(events: list[Event]) -> tuple[SiteRecordV1, ...]:
    """The streamed sites, re-validated from the wire — what a client would actually hold."""
    return tuple(SiteRecordV1.model_validate(dict(e.data)) for e in events if e.event == "site")


@pytest.fixture(scope="module")
def values(records: tuple[SiteRecordV1, ...]) -> tuple[tuple[str, SourcedValue[Any]], ...]:
    """Every ``(field_path, SourcedValue)`` the pass produced, conflict candidates included."""
    return tuple((field, value) for r in records for field, value in iter_sourced_values(r))


# ── T046 · provenance completeness (SC-002 / FR-003) ──────────────────────────────────


def test_provenance_is_complete_on_every_researched_value(
    records: tuple[SiteRecordV1, ...],
    values: tuple[tuple[str, SourcedValue[Any]], ...],
) -> None:
    """SC-002 is **100 %**, not "almost" — measured as a rate over the whole pass."""
    assert records, "the fixture pass must produce records, or this eval asserts nothing"
    assert len(values) > len(records), "each record carries several values; N must be real"

    unstamped = [
        field
        for field, value in values
        if value.source is None or not value.source.kind or not value.source.license
    ]
    rate = 1.0 - len(unstamped) / len(values)
    assert rate == 1.0, (
        f"SC-002 demands 100% provenance: {len(unstamped)} of {len(values)} values across "
        f"{len(records)} records are unstamped (rate {rate:.4f}); first offenders {unstamped[:5]}"
    )


def test_the_provenance_walk_covers_every_sourced_field_of_the_schema() -> None:
    """The walk must track the schema, not a remembered field list.

    :func:`iter_sourced_values` is what every provenance and quarantine check above is
    driven by, so a ``SourcedValue`` field added to :class:`SiteRecordV1` and *not* added
    to the walk would silently escape all of them. This reddens the moment that happens.
    """
    expected = {name for name, value in _maximal_fields().items() if value is not None}
    assert len(expected) >= 10, (
        "the schema introspection found almost no sourced fields — it has broken, and every "
        f"eval driven by it would now pass vacuously (found {sorted(expected)})"
    )
    walked = {path.split(".")[0] for path, _ in iter_sourced_values(_maximal_record())}
    assert walked == expected, (
        "commons.merge.iter_sourced_values does not walk every SourcedValue-bearing field "
        f"of SiteRecordV1: missing {sorted(expected - walked)}, "
        f"unexpected {sorted(walked - expected)}"
    )


def test_the_walk_sees_every_stamped_value_that_reaches_the_wire(
    events: list[Event],
    values: tuple[tuple[str, SourcedValue[Any]], ...],
) -> None:
    """…and on live data too: nothing is serialised that the provenance walk cannot see."""
    on_the_wire = [v for e in events if e.event == "site" for v in sourced_values(dict(e.data))]
    assert on_the_wire, "no site frame streamed; this eval would assert nothing"
    assert len(on_the_wire) == len(values), (
        f"{len(on_the_wire)} stamped values reached the client but the provenance walk sees "
        f"{len(values)} — a value is escaping every provenance and quarantine check"
    )


# ── T046 · the license quarantine (Article V / DATA-LICENSES.md) ──────────────────────


def test_no_unbundleable_value_in_the_bundle(
    values: tuple[tuple[str, SourcedValue[Any]], ...],
) -> None:
    """Nothing outside the allowlist — and nothing ``open_web``/``review_provider`` — may
    ever carry ``bundleable=True``. Driven from the registry, so the eval tracks ADR-0012
    and whatever amends it next."""
    assert values, "an empty pass makes the quarantine vacuous"
    bundle = [(field, value) for field, value in values if value.bundleable]
    assert bundle, "nothing in this pass is bundleable, so the quarantine asserts nothing"

    for field, value in bundle:
        assert value.source.kind not in licenses.NEVER_BUNDLEABLE_KINDS, (
            f"{field} is bundleable but its source kind {value.source.kind!r} never may be"
        )
        assert licenses.is_allowlisted(value.source.license), (
            f"{field} is bundleable under the non-allowlisted license "
            f"{value.source.license!r}: "
            f"{licenses.quarantine_reason(value.source.kind, value.source.license)}"
        )

    # The rule is an equivalence, so the *unstamped* direction matters just as much: an
    # allowlisted value wrongly quarantined silently shrinks every offline bundle.
    for field, value in values:
        expected = licenses.bundleable(value.source.kind, value.source.license)
        assert value.bundleable is expected, (
            f"{field}: bundleable={value.bundleable} contradicts the registry — "
            f"{licenses.quarantine_reason(value.source.kind, value.source.license)}"
        )


def test_a_quarantined_source_stays_quarantined_through_curation() -> None:
    """Curation derives new values (T059); the quarantine must survive the derivation.

    The Rhodes fixtures are wholly allowlisted, so the *negative* half of the rule cannot
    be observed on them. This drives an ``open_web`` record — never bundleable whatever
    license it claims — through the real curate node and checks the twin it produces.
    """
    result = curate([record(names={"el": "Ρόδος"}, source=QUARANTINED)], observed_at=DERIVED_ON)
    merged = result.results[0].record

    assert merged.names["el-Latn"].value == "Rodos", "quarantined ≠ dropped: it is still curated"
    quarantined = [(f, v.bundleable) for f, v in iter_sourced_values(merged)]
    assert quarantined, "the curated record carries no values; nothing was checked"
    assert not any(bundleable for _, bundleable in quarantined), (
        f"an open_web value reached the bundle: {[f for f, b in quarantined if b]}"
    )


# ── T047 · geometry provenance (FR-005) ───────────────────────────────────────────────


def test_every_location_traces_to_authoritative_geodata(
    records: tuple[SiteRecordV1, ...],
    values: tuple[tuple[str, SourcedValue[Any]], ...],
) -> None:
    """A coordinate is never model-emitted or model-computed: it cites Overture or OSM.

    Every ``location`` the walk yields is checked — the merged winner *and* every losing
    candidate parked in a ``FieldConflict`` — so a model-sourced coordinate cannot hide in
    the conflict ledger either.
    """
    assert records, "no records; there is no geometry to trace"
    locations = [value for field, value in values if field == "location"]
    assert len(locations) >= len(records), "every record carries a location; N must be real"

    foreign = {value.source.kind for value in locations if value.source.kind not in GEODATA_KINDS}
    assert not foreign, (
        f"FR-005: {len(locations)} coordinates must all trace to {sorted(GEODATA_KINDS)}, "
        f"but some cite {sorted(foreign)} — a coordinate may never be model-authored"
    )


def test_a_model_that_tries_to_emit_a_coordinate_moves_nothing(
    records: tuple[SiteRecordV1, ...],
) -> None:
    """The bite: a model that *tries* to hand back a location changes neither the record
    set nor any location's provenance — it is refused, with a reason (FR-003 / FR-005)."""
    smuggled = '[{"id": "any", "location": {"type": "Point", "coordinates": [0.0, 0.0]}}]'
    events = drain(router=FakeRouter(reply=smuggled))

    frame = next(e for e in events if e.data.get("phase") == "curate")
    assert frame.data["rejected"], "the smuggled coordinate must be refused, with a reason"

    after = tuple(SiteRecordV1.model_validate(dict(e.data)) for e in events if e.event == "site")
    assert _geometry_provenance(after) == _geometry_provenance(records), (
        "the model's attempt to emit a coordinate changed the geometry or its provenance"
    )


# ── T061 · the FAIL-001 regression (Article IV) ───────────────────────────────────────


def test_the_committed_fixture_carries_no_script_mismatch(events: list[Event]) -> None:
    """The baseline the next eval is measured against: clean data raises no flag, and the
    Greek names still gain their Latin twins (SC-004)."""
    frame = next(e for e in events if e.data.get("phase") == "curate")
    assert frame.data["merged"] > 0, "an empty curate frame makes the baseline meaningless"
    assert frame.data["flags"] == 0, f"unexpected script flags on clean data: {frame.data}"
    assert frame.data["derived_names"] > 0, "the Greek fixture names must transliterate"


def test_a_mis_scripted_value_is_flagged_never_transliterated_never_dropped() -> None:
    """FAIL-001: a value whose stored script contradicts its language tag.

    Asserted **through the pipeline** — `tests/test_translit.py` already covers the helper.
    What matters at this level is the triple the failure entry demands: the mismatch is
    *counted* on the ``curate`` status frame, the value is *never* transliterated, and the
    record is *never* silently dropped from the stream.
    """
    injected = record(names={"el": MIS_SCRIPTED}, lon_lat=ELSEWHERE)
    events = drain(adapters=[overture(), osm(node=OVERPASS_JSON), _Injected((injected,))])

    frame = next(e for e in events if e.data.get("phase") == "curate")
    assert frame.data["flags"] == 1, (
        f"the pipeline must surface the FAIL-001 script mismatch, got {frame.data}"
    )
    assert frame.data["derived_names"] > 0, "one bad value must not stop the rest transliterating"

    streamed = tuple(SiteRecordV1.model_validate(dict(e.data)) for e in events if e.event == "site")
    assert len(streamed) == frame.data["merged"], "every merged record must reach the client"
    flagged = [r for r in streamed if MIS_SCRIPTED in {v.value for v in r.names.values()}]
    assert len(flagged) == 1, "the flagged record was dropped instead of being surfaced"
    assert flagged[0].names["el"].value == MIS_SCRIPTED, "the original is never repaired"
    assert "el-Latn" not in flagged[0].names, "a flagged value is never transliterated"


# ── helpers ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Injected:
    """A source that emits exactly the records it was handed — the FAIL-001 injection point.

    ``kind`` is only the label the ``status``/``summary`` frames report this source under;
    the records carry their own stamps. ``user`` is the honest label for hand-built input.
    """

    records: tuple[SiteRecordV1, ...]

    kind: ClassVar[SourceKind] = "user"

    def fetch(self, polygon: BaseGeometry) -> FetchResult:
        return FetchResult(kind=self.kind, records=self.records)


def _geometry_provenance(records: Sequence[SiteRecordV1]) -> list[tuple[str, str, str]]:
    """``(kind, source id, WKT)`` per record, sorted — identity that survives a re-run.

    Record ``id``s are server-generated per pass, so two passes over the same fixtures are
    only comparable through what the *source* said.
    """
    return sorted(
        (r.location.source.kind, r.location.source.id, r.location.value.wkt) for r in records
    )


def _sourced_parameter(annotation: Any) -> Any | None:
    """``T`` if ``annotation`` is a concrete ``SourcedValue[T]``, else ``None``.

    Pydantic materialises a parametrized generic model as a real subclass, so
    ``get_origin(SourcedValue[str])`` is ``None`` and the parameters live on the model's
    own generic metadata instead.
    """
    metadata = getattr(annotation, "__pydantic_generic_metadata__", None)
    if not metadata or metadata.get("origin") is not SourcedValue:
        return None
    return metadata["args"][0]


def _fill(annotation: Any) -> Any:
    """A populated instance of ``annotation`` iff it can carry a ``SourcedValue``, else None.

    Annotation-driven rather than field-name-driven: that is what makes
    :func:`test_the_provenance_walk_covers_every_sourced_field_of_the_schema` notice a
    field nobody told it about.
    """
    origin, args = get_origin(annotation), get_args(annotation)
    parameter = _sourced_parameter(annotation)
    if parameter is not None:
        # `Wgs84Point` is an Annotated[Point, …] alias, so anything that is not plain `str`
        # is the geometry slot.
        value: Any = "Ρόδος" if parameter is str else Point(28.2247, 36.4443)
        return SourcedValue[Any].stamp(
            value=value, source=STAMP, confidence=1.0, observed_at=OBSERVED
        )
    if origin in (types.UnionType, Union):
        filled = [_fill(arg) for arg in args if arg is not type(None)]
        return next((value for value in filled if value is not None), None)
    if origin is tuple:
        inner = _fill(args[0])
        return None if inner is None else (inner,)
    if origin is dict:
        inner = _fill(args[1])
        return None if inner is None else {"el": inner}
    return None


def _maximal_fields() -> dict[str, Any]:
    """Field name → a populated value for every ``SourcedValue``-bearing field (else None)."""
    return {name: _fill(f.annotation) for name, f in SiteRecordV1.model_fields.items()}


def _maximal_record() -> SiteRecordV1:
    """A record with **every** sourced field populated — the walk's coverage is read off it."""
    return SiteRecordV1(**{n: v for n, v in _maximal_fields().items() if v is not None})
