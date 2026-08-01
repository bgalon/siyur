"""Unit tests — the merge join rule (`commons/merge.py`), T031 of Spec 001.

Ground truth: `docs/design/tech-design.md` §1.2 / `docs/data/poi-site.md` "Merge" /
`specs/001-research-cited-sites/data-model.md` §5 rule 5. The load-bearing assertion in
this file is **`test_neighbours_with_different_names_do_not_merge`**: two records 5 m
apart with different names must stay two records. Distance alone never merges — the
discovery spike measured a *median* name similarity of ≈0.1 among sub-20 m pairs, so a
distance-only join would fuse a whole old town into one POI.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pyproj import Geod
from shapely import Point

from commons.geo import GeometryError, Wgs84Point
from commons.merge import (
    EPSILON_METERS,
    TAU_NAME_SIMILARITY,
    best_name_similarity,
    cluster_records,
    decide_match,
    distance_m,
    merge_cluster,
    merge_records,
    name_similarity,
    name_similarity_by_language,
    normalize_name,
    source_refs,
)
from commons.models import SiteRecordV1, SourcedValue, SourceRef

OVERTURE = SourceRef(kind="overture", id="08f394…gers", license="CDLA-Permissive-2.0")
OSM = SourceRef(
    kind="osm",
    id="node/123456",
    license="ODbL-1.0",
    attribution="© OpenStreetMap contributors",
)
WIKIDATA = SourceRef(kind="wikidata", id="Q1049981", license="CC0-1.0")
WIKIVOYAGE = SourceRef(
    kind="wikivoyage", id="Rhodes", license="CC-BY-SA-4.0", attribution="Wikivoyage (CC BY-SA 4.0)"
)
OBSERVED = date(2026, 7, 22)
# An arbitrary anchor; nothing in `commons/` may hardcode a place (FR-001 genericity) —
# in a *test* a concrete coordinate is the fixture, not product behaviour.
ANCHOR = (28.2247, 36.4443)
_GEOD = Geod(ellps="WGS84")


def _offset(metres: float, *, azimuth: float = 90.0, origin: tuple[float, float] = ANCHOR) -> Point:
    """A point exactly ``metres`` away from ``origin`` along ``azimuth`` (WGS84 geodesic)."""
    lon, lat, _ = _GEOD.fwd(origin[0], origin[1], azimuth, metres)
    return Point(lon, lat)


def _record(
    *,
    names: dict[str, str],
    location: Point | None = None,
    gers_id: str | None = None,
    source: SourceRef = OVERTURE,
    confidence: float = 0.8,
    observed_at: date = OBSERVED,
    categories: Sequence[str] = (),
    address: str | None = None,
) -> SiteRecordV1:
    """A minimal valid slice-001 record: stamped names + a stamped location."""

    def stamp(value: object, conf: float = confidence) -> SourcedValue[Any]:
        return SourcedValue[Any].stamp(
            value=value, source=source, confidence=conf, observed_at=observed_at
        )

    return SiteRecordV1(
        gers_id=gers_id,
        names={tag: stamp(value) for tag, value in names.items()},
        location=SourcedValue[Wgs84Point].stamp(
            value=location if location is not None else Point(*ANCHOR),
            source=source,
            confidence=confidence,
            observed_at=observed_at,
        ),
        categories=tuple(stamp(value) for value in categories),
        address=None if address is None else stamp(address),
    )


# ── distance: metric, not degrees ──────────────────────────────────────────────────


@pytest.mark.parametrize("metres", [0.0, 5.0, 24.9, 25.1, 250.0])
def test_distance_is_geodesic_metres(metres: float) -> None:
    """`distance_m` inverts `Geod.fwd`, i.e. it really is metres on the WGS84 ellipsoid."""
    assert distance_m(Point(*ANCHOR), _offset(metres)) == pytest.approx(metres, abs=1e-6)


def test_distance_is_symmetric_and_zero_for_the_same_point() -> None:
    here, there = Point(*ANCHOR), _offset(17.0)
    assert distance_m(here, there) == pytest.approx(distance_m(there, here))
    assert distance_m(here, here) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("azimuth", [0.0, 90.0, 180.0, 270.0])
def test_distance_does_not_depend_on_bearing(azimuth: float) -> None:
    """A degree of longitude is not a degree of latitude — a planar ε would fail this."""
    assert distance_m(Point(*ANCHOR), _offset(20.0, azimuth=azimuth)) == pytest.approx(
        20.0, abs=1e-6
    )


def test_distance_rejects_a_swapped_coordinate_pair() -> None:
    """(lon, lat) discipline: a swapped pair with |lon| > 90 fails loudly, not silently."""
    with pytest.raises(GeometryError):
        distance_m(Point(*ANCHOR), Point(36.4443, 128.0))


# ── name similarity: deterministic, offline, script-preserving ─────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("St. Nicholas  Tower", "st nicholas tower"),
        ("PALACE of the Grand-Master", "palace of the grand master"),
        ("Ρόδος", "ροδοσ"),  # accents + final sigma folded, **script preserved**
    ],
)
def test_normalize_name_folds_case_diacritics_and_punctuation(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_normalize_name_never_transliterates() -> None:
    """Merge compares within a language; transliteration belongs to ingestion (§3)."""
    assert normalize_name("Ρόδος") != normalize_name("Rodos")


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Clock Tower", "Clocktower"),
        ("Mandraki Harbour", "Mandraki Harbor"),
        ("Clock Tower", "Archaeological Museum"),
        ("", "Elli Beach"),
    ],
)
def test_name_similarity_is_symmetric_and_bounded(left: str, right: str) -> None:
    forward, backward = name_similarity(left, right), name_similarity(right, left)
    assert forward == backward  # exact, not approx: reproducible merges need it
    assert 0.0 <= forward <= 1.0


def test_name_similarity_identical_after_normalisation_is_one() -> None:
    assert name_similarity("Elli Beach", "  elli   beach! ") == 1.0


def test_name_similarity_compares_only_shared_language_keys() -> None:
    """Cross-script comparison scores ≈0 (spike) — so it is never done."""
    left = _record(names={"en": "Rhodes", "el": "Ρόδος"})
    right = _record(names={"el": "Ρόδος", "ja": "ロドス"})
    assert set(name_similarity_by_language(left, right)) == {"el"}


def test_records_sharing_no_name_key_have_no_name_signal() -> None:
    left = _record(names={"en": "Rhodes"})
    right = _record(names={"el": "Ρόδος"})
    assert best_name_similarity(left, right) == (None, 0.0)


def test_best_name_similarity_picks_the_strongest_agreement() -> None:
    left = _record(names={"en": "Clock Tower", "el": "Ρολόι"})
    right = _record(names={"en": "Clocktower", "el": "Καφενείο"})
    tag, score = best_name_similarity(left, right)
    assert tag == "en"
    assert score == pytest.approx(name_similarity("Clock Tower", "Clocktower"))


# ── the join rule ──────────────────────────────────────────────────────────────────


def test_shared_gers_id_joins_regardless_of_distance_or_name() -> None:
    left = _record(names={"en": "Grand Master Palace"}, gers_id="08f394…gers")
    right = _record(
        names={"en": "Παλάτι"}, location=_offset(400.0), gers_id="08f394…gers", source=OSM
    )
    decision = decide_match(left, right)
    assert decision.matched is True
    assert decision.rule == "gers_id"


def test_different_gers_ids_never_join_even_when_identical_and_adjacent() -> None:
    """GERS is authoritative: two ids means two places — no fuzzy retry."""
    left = _record(names={"en": "Clock Tower"}, gers_id="08f394…a")
    right = _record(names={"en": "Clock Tower"}, location=_offset(1.0), gers_id="08f394…b")
    decision = decide_match(left, right)
    assert decision.matched is False
    assert decision.rule is None


def test_one_sided_gers_id_falls_through_to_the_fuzzy_rule() -> None:
    """Overture↔OSM share no ids in practice, so this is the common path."""
    left = _record(names={"en": "Clock Tower"}, gers_id="08f394…gers")
    right = _record(names={"en": "Clocktower"}, location=_offset(4.0), source=OSM)
    decision = decide_match(left, right)
    assert decision.matched is True
    assert decision.rule == "spatial_name"


# --- the one that matters most: distance alone never merges ---


def test_neighbours_with_different_names_do_not_merge() -> None:
    """Two differently-named POIs 5 m apart stay two records (validation rule 5).

    This is the dense-old-town case the spike measured: proximity is not identity.
    """
    left = _record(names={"en": "Clock Tower"})
    right = _record(names={"en": "Café Elli"}, location=_offset(5.0), source=OSM)
    decision = decide_match(left, right)
    assert decision.matched is False
    assert decision.distance_m is not None
    assert decision.distance_m <= EPSILON_METERS  # well inside ε …
    assert decision.name_similarity is not None
    assert decision.name_similarity < TAU_NAME_SIMILARITY  # … and still no merge
    assert "distance alone never merges" in decision.reason


def test_co_located_records_with_no_shared_language_do_not_merge() -> None:
    """No shared name key ⇒ no name signal ⇒ no merge, even at 0 m."""
    left = _record(names={"en": "Clock Tower"})
    right = _record(names={"el": "Ρολόι"}, source=OSM)
    assert decide_match(left, right).matched is False


# --- ε boundary (τ satisfied) ---
# Just inside / just outside by 0.1 m rather than exactly ε: the ellipsoid inverse is
# accurate to ~1e-8 m, so no (lon, lat) pair measures *exactly* 25.000000000 m.


@pytest.mark.parametrize(
    ("metres", "expected"),
    [(0.0, True), (24.0, True), (EPSILON_METERS - 0.1, True), (EPSILON_METERS + 0.1, False)],
)
def test_epsilon_boundary(metres: float, expected: bool) -> None:
    left = _record(names={"en": "Mandraki Harbour"})
    right = _record(names={"en": "Mandraki Harbor"}, location=_offset(metres), source=OSM)
    assert name_similarity("Mandraki Harbour", "Mandraki Harbor") >= TAU_NAME_SIMILARITY
    assert decide_match(left, right).matched is expected


# --- τ boundary (ε satisfied) ---
# Two real-world-shaped name pairs that bracket τ=0.6 closely: 0.652 and 0.585.
_JUST_ABOVE_TAU = ("Agios Fanourios", "Agios Fanourios Orthodox Church")
_JUST_BELOW_TAU = ("Kahal Shalom", "Kahal Kadosh Shalom Synagogue")


@pytest.mark.parametrize(("pair", "expected"), [(_JUST_ABOVE_TAU, True), (_JUST_BELOW_TAU, False)])
def test_tau_boundary(pair: tuple[str, str], expected: bool) -> None:
    similarity = name_similarity(*pair)
    assert (similarity >= TAU_NAME_SIMILARITY) is expected, similarity
    left = _record(names={"en": pair[0]})
    right = _record(names={"en": pair[1]}, location=_offset(3.0), source=OSM)
    decision = decide_match(left, right)
    assert decision.matched is expected
    assert decision.distance_m is not None and decision.distance_m < EPSILON_METERS


def test_far_apart_with_identical_names_does_not_merge() -> None:
    """Name alone is not enough either — both tests must pass."""
    left = _record(names={"en": "Agios Nikolaos"})
    right = _record(names={"en": "Agios Nikolaos"}, location=_offset(900.0), source=OSM)
    assert decide_match(left, right).matched is False


def test_decision_is_symmetric() -> None:
    """Merging must not depend on which record the loop happens to visit first."""
    left = _record(names={"en": "Elli Beach"})
    right = _record(names={"en": "Elly Beach"}, location=_offset(12.0), source=OSM)
    forward, backward = decide_match(left, right), decide_match(right, left)
    assert forward.matched is backward.matched is True
    assert forward.distance_m == pytest.approx(backward.distance_m)
    assert forward.name_similarity == backward.name_similarity


# ══ per-field merge: no source lost, conflicts, winner policy (T029) ═══════════════
#
# The FR-009 / validation-rule-4 invariant is asserted as a **set property**: the source
# refs reachable from the result equal the source refs on the inputs. "Reachable" = the
# winning `SourcedValue`, a `FieldConflict` candidate, or the provenance ledger
# (`site_source`) — where *agreeing* sources survive, agreement being no conflict.


def _refs(records: Sequence[SiteRecordV1]) -> frozenset[SourceRef]:
    return frozenset(ref for record in records for ref in source_refs(record))


_SOURCES = (OVERTURE, OSM, WIKIDATA, WIKIVOYAGE)
_POOL_NAMES = ("Clock Tower", "Clocktower", "Elli Beach", "Archaeological Museum")
_POOL_CATEGORIES = ("attraction.castle", "attraction.museum", "beach")
# (name index, source index, confidence × 10, metres from the anchor, days after
# OBSERVED, category index)
_ROW = st.tuples(*(st.integers(0, bound) for bound in (3, 3, 10, 60, 30, 2)))


def _row_record(row: tuple[int, int, int, int, int, int]) -> SiteRecordV1:
    name, source, confidence, metres, days, category = row
    return _record(
        names={"en": _POOL_NAMES[name]},
        location=_offset(float(metres)),
        source=_SOURCES[source],
        confidence=confidence / 10,
        observed_at=OBSERVED + timedelta(days=days),
        categories=(_POOL_CATEGORIES[category],),
        address=f"{metres} Odos Ippoton",
    )


@settings(max_examples=200, deadline=None)
@given(st.lists(_ROW, min_size=1, max_size=6))
def test_no_source_ref_is_lost(rows: list[tuple[int, int, int, int, int, int]]) -> None:
    """Property (FR-009): merging never adds or drops a source ref, whatever it decides."""
    records = [_row_record(row) for row in rows]
    assert merge_cluster(records).source_refs == _refs(records)


def test_no_source_ref_is_lost_when_three_sources_describe_one_place() -> None:
    """The same property on a concrete cluster: three sources in, three sources out."""
    records = [
        _record(names={"en": "Clock Tower"}, source=OVERTURE, address="1 Odos Orfeos"),
        _record(names={"en": "Clocktower"}, location=_offset(6.0), source=OSM, address="Orfeos 1"),
        _record(names={"en": "Clock Tower"}, location=_offset(9.0), source=WIKIDATA),
    ]
    assert merge_cluster(records).source_refs == _refs(records) == {OVERTURE, OSM, WIKIDATA}


# ── conflicts ──────────────────────────────────────────────────────────────────────


def test_disagreement_creates_a_conflict_holding_every_candidate() -> None:
    left = _record(names={"en": "Clock Tower"}, source=OVERTURE, address="1 Odos Orfeos")
    right = _record(names={"en": "Clock Tower"}, source=OSM, address="Orfeos 1")
    result = merge_cluster([left, right])
    winner = result.record.address
    assert winner is not None
    conflict = next(c for c in result.record.conflicts if c.field == "address")
    assert {c.value for c in conflict.candidates} == {"1 Odos Orfeos", "Orfeos 1"}
    assert conflict.resolution == f"picked:{winner.source.id}"
    # The losing source stays reachable *in the record itself*, not only in the ledger.
    assert OSM in {c.source for c in conflict.candidates}


def test_agreement_creates_no_conflict_but_keeps_the_second_source() -> None:
    """Two sources saying the same thing is not a disagreement — the ledger holds both."""
    left = _record(names={"en": "Elli Beach"}, source=OVERTURE, address="Akti Miaouli")
    right = _record(names={"en": "Elli Beach"}, source=OSM, address="Akti Miaouli")
    result = merge_cluster([left, right])
    assert [c.field for c in result.record.conflicts] == []
    assert result.source_refs == {OVERTURE, OSM}
    assert {o.value.source for o in result.provenance if o.field == "address"} == {OVERTURE, OSM}


def test_categories_are_unioned_not_conflicted() -> None:
    """Union-first: different categories are complementary coverage, not disagreement."""
    left = _record(names={"en": "Elli Beach"}, source=OVERTURE, categories=("beach",))
    right = _record(names={"en": "Elli Beach"}, source=OSM, categories=("beach", "leisure.bar"))
    result = merge_cluster([left, right])
    assert {c.value for c in result.record.categories} == {"beach", "leisure.bar"}
    assert [c.field for c in result.record.conflicts] == []


def test_an_earlier_conflict_survives_a_re_merge() -> None:
    """Re-research enriches; it never drops a disagreement recorded by a previous pass."""
    first = merge_cluster(
        [
            _record(names={"en": "Clock Tower"}, source=OVERTURE, address="1 Odos Orfeos"),
            _record(names={"en": "Clock Tower"}, source=OSM, address="Orfeos 1"),
        ]
    )
    again = merge_cluster(
        [first.record, _record(names={"en": "Clock Tower"}, source=WIKIDATA, address="Orfeos 1")]
    )
    assert _refs([again.record]) >= {OVERTURE, OSM, WIKIDATA}
    assert any(c.field == "address" for c in again.record.conflicts)


# ── winner policy: confidence → source trust → observed_at → content tiebreak ──────


def _address_winner(*records: SiteRecordV1) -> tuple[str, str]:
    winner = merge_cluster(records).record.address
    assert winner is not None
    return winner.value, winner.source.kind


def test_confidence_beats_source_trust() -> None:
    """Confidence dominates: a high-confidence OSM value outranks a weak Overture one."""
    assert _address_winner(
        _record(names={"en": "X"}, source=OVERTURE, address="overture", confidence=0.4),
        _record(names={"en": "X"}, source=OSM, address="osm", confidence=0.9),
    ) == ("osm", "osm")


def test_source_trust_breaks_a_confidence_tie() -> None:
    assert _address_winner(
        _record(names={"en": "X"}, source=OSM, address="osm", confidence=0.7),
        _record(names={"en": "X"}, source=OVERTURE, address="overture", confidence=0.7),
    ) == ("overture", "overture")


def test_recency_breaks_a_confidence_and_trust_tie() -> None:
    """Same kind, same confidence → the fresher `observed_at` wins (staleness, PRD §5)."""
    stale = SourceRef(kind="osm", id="node/1", license="ODbL-1.0")
    fresh = SourceRef(kind="osm", id="node/2", license="ODbL-1.0")
    assert _address_winner(
        _record(names={"en": "X"}, source=fresh, address="new", observed_at=OBSERVED),
        _record(
            names={"en": "X"},
            source=stale,
            address="old",
            observed_at=OBSERVED - timedelta(days=400),
        ),
    ) == ("new", "osm")


def test_the_final_tiebreak_is_deterministic_under_permutation() -> None:
    """Candidates equal on every policy term still elect the same winner, every run.

    Without a content-derived last resort the winner would depend on adapter ordering,
    and two identical merges could disagree — which would make conflicts un-reproducible.
    """
    records = [
        _record(
            names={"en": "X"},
            source=SourceRef(kind="osm", id=f"node/{n}", license="ODbL-1.0"),
            address=f"address {n}",
            confidence=0.7,
        )
        for n in (3, 1, 2)
    ]
    winners = {_address_winner(*order) for order in itertools.permutations(records)}
    assert winners == {("address 1", "osm")}  # lowest source.id, deterministically


def test_a_single_record_cluster_still_yields_a_result_with_its_ledger() -> None:
    """One shape to persist: a lone record round-trips, conflict-free, ledger intact."""
    only = _record(names={"en": "Elli Beach"}, source=OSM, categories=("beach",), address="Akti")
    result = merge_cluster([only])
    assert result.record.names["en"].value == "Elli Beach"
    assert result.record.conflicts == ()
    assert result.merged_from == (only.id,)
    assert result.source_refs == {OSM}


def test_merge_cluster_refuses_an_empty_cluster() -> None:
    with pytest.raises(ValueError, match="at least one record"):
        merge_cluster([])


# ── clustering: the join rule end-to-end ───────────────────────────────────────────


@settings(max_examples=200, deadline=None)
@given(st.lists(_ROW, min_size=1, max_size=6))
def test_merge_records_loses_no_source_ref(rows: list[tuple[int, int, int, int, int, int]]) -> None:
    """The FR-009 property again, now across clustering: whatever the join rule decides,
    the union of source refs over all merged records equals the union over the inputs."""
    records = [_row_record(row) for row in rows]
    results = merge_records(records)
    assert frozenset(ref for result in results for ref in result.source_refs) == _refs(records)


def test_merge_records_keeps_distinct_neighbours_apart() -> None:
    """The join rule holds end-to-end: 5 m apart, differently named ⇒ two records out."""
    records = [
        _record(names={"en": "Clock Tower"}, source=OVERTURE),
        _record(names={"en": "Café Elli"}, location=_offset(5.0), source=OSM),
    ]
    results = merge_records(records)
    assert len(results) == 2
    assert frozenset(ref for r in results for ref in r.source_refs) == _refs(records)


def test_merge_records_collapses_three_sources_for_one_place() -> None:
    records = [
        _record(names={"en": "Clock Tower"}, source=OVERTURE, address="1 Odos Orfeos"),
        _record(names={"en": "Clocktower"}, location=_offset(6.0), source=OSM, address="Orfeos 1"),
        _record(names={"en": "Clock Tower"}, location=_offset(9.0), source=WIKIDATA),
    ]
    (result,) = merge_records(records)
    assert result.merged_from == tuple(r.id for r in records)
    assert result.source_refs == {OVERTURE, OSM, WIKIDATA}


def test_a_fuzzy_chain_cannot_collapse_two_authoritative_ids() -> None:
    """GERS identity outranks a transitive fuzzy chain (A↔B, B↔C, but gers(A) ≠ gers(C))."""
    a = _record(names={"en": "Clock Tower"}, gers_id="08f394…a", source=OVERTURE)
    b = _record(names={"en": "Clocktower"}, location=_offset(3.0), source=OSM)
    c = _record(names={"en": "Clock Tower"}, location=_offset(6.0), gers_id="08f394…c")
    assert len(cluster_records([a, b, c])) == 2
    assert {r.record.gers_id for r in merge_records([a, b, c])} == {"08f394…a", "08f394…c"}


def test_merge_is_independent_of_input_order() -> None:
    """The whole merge, not just the winner: same inputs in any order ⇒ same output."""
    records = [
        _record(names={"en": "Clock Tower"}, source=OVERTURE, categories=("a",), address="one"),
        _record(names={"en": "Clocktower"}, location=_offset(4.0), source=OSM, address="two"),
        _record(names={"en": "Clock Tower"}, location=_offset(7.0), source=WIKIDATA),
    ]
    dumps = {
        merge_records(order)[0].record.model_dump_json()
        for order in itertools.permutations(records)
    }
    assert len(dumps) == 1


def test_a_merged_record_with_a_geometry_conflict_round_trips_through_json() -> None:
    """`site`/`site_conflict` are jsonb: a location disagreement must still serialise."""
    (result,) = merge_records(
        [
            _record(names={"en": "Clock Tower"}, source=OVERTURE),
            _record(names={"en": "Clocktower"}, location=_offset(4.0), source=OSM),
        ]
    )
    conflict = next(c for c in result.record.conflicts if c.field == "location")
    assert all(c.value["type"] == "Point" for c in conflict.candidates)  # GeoJSON, not shapely
    restored = SiteRecordV1.model_validate_json(result.record.model_dump_json())
    assert source_refs(restored) == _refs([result.record]) == {OVERTURE, OSM}


# --- `und` (undetermined language) cross-key matching -------------------------------
# Regression: Overture publishes `names.primary` and OSM a bare `name` tag with no
# language, so both key `und`. Requiring an exact key match made the cross-source join
# impossible in exactly the case it exists for. The Rhodes fixture anchor — the same
# real gate, named identically, 3.4 m apart, keyed `und` by Overture and `el` by OSM —
# scored 0.00 and refused to merge.


def test_und_name_matches_a_tagged_name_in_another_language() -> None:
    """`und` is the absence of a language claim, so it may match any tagged name."""
    overture_side = _record(names={"und": "Πύλη Ταρσανά"}, source=OVERTURE)
    osm_side = _record(
        names={"el": "Πύλη Ταρσανά", "en": "Gate of the Arsenal"},
        location=_offset(3.4),
        source=OSM,
    )
    tag, score = best_name_similarity(overture_side, osm_side)
    assert (tag, score) == ("el", 1.0)  # filed under the *determined* tag, not `und`

    (result,) = merge_records([overture_side, osm_side])
    assert source_refs(result.record) == {OVERTURE, OSM}  # no source lost


def test_und_still_requires_the_name_signal_distance_alone_never_merges() -> None:
    """Making `und` comparable must not weaken ε+τ into a distance-only join."""
    overture_side = _record(names={"und": "Marine Gate"}, source=OVERTURE)
    osm_side = _record(names={"el": "Φούρνος Κοκκίνη"}, location=_offset(3.4), source=OSM)

    decision = decide_match(overture_side, osm_side)
    assert decision.matched is False
    assert decision.name_similarity is not None and decision.name_similarity < TAU_NAME_SIMILARITY
    assert len(merge_records([overture_side, osm_side])) == 2
