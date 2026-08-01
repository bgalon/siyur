"""Per-field, union-first merge — the join rule (T028 of Spec 001).

**Ground truth:** `docs/design/tech-design.md` §1.2, transcribed into
[`docs/data/poi-site.md`](../docs/data/poi-site.md) "Merge" and
`specs/001-research-cited-sites/data-model.md` §5 rule 5. Nothing is invented here.

The rule, verbatim:

    Join key: ``gers_id`` when present; else fuzzy spatial+name — distance ≤ **ε = 25 m**
    AND same-language name similarity ≥ **τ = 0.6** (values from the discovery spike).
    **Distance alone never merges** — a name signal is required.

Why the name signal is not optional: the spike measured a *median* name similarity of
≈0.1 among pairs closer than 20 m — dense old towns pack many genuinely *different* POIs
into a few metres. The posture is **union-first**: the sources are only ~27–40 % over-
lapping, so merge enriches coverage more than it reconciles, and *keeping two records is
always preferred over a wrong collapse*. Every "no" here is cheap; every wrong "yes"
fuses two real places forever.

Two implementation choices this module pins (both offline, deterministic, no new
dependency — see the PR / ADR):

* **Distance** is the **WGS84 geodesic** distance in metres via ``pyproj.Geod`` (already
  pinned, ``pyproj~=3.7``). Degrees are *not* metres — one degree of longitude shrinks
  with latitude, so a naive planar ε in degrees would be a different threshold in Rhodes
  than in Takayama, which violates the genericity rule (FR-001). ``Geod.inv`` on the
  WGS84 ellipsoid is what PostGIS ``ST_Distance(geography)`` computes, so the Python
  pre-filter and the SQL query agree on ε.
* **Name similarity** is :func:`difflib.SequenceMatcher.ratio` (stdlib) over
  case/diacritic/punctuation-normalised text, made order-symmetric. Deterministic,
  offline, dependency-free, and the same family of ratio the spike's τ was calibrated on.

Merge does **not** transliterate. Comparison is *within one BCP-47 key* — the ``el-Latn``
key already holds the transliterated form produced at ingestion (data-model §3), so
comparing like key to like key *is* the "same language, post-transliteration" rule, and
raw cross-script comparison (which the spike measured at ≈0) never happens.

**Per-field, and no source is ever discarded** (FR-009 / data-model §5 rule 4): each field
keeps one winning ``SourcedValue`` and a losing *different* value becomes a
:class:`~commons.models.FieldConflict`. Every candidate is *also* emitted as a
:class:`FieldObservation` — the in-memory form of the append-only ``site_source`` table
(tech-design §2) — which is what keeps the invariant true when two sources **agree**
(agreement is no conflict, so the ledger is the only honest place for the second source).
:attr:`MergeResult.source_refs` is the set the invariant is tested against.

Persistence is deliberately **not** here: this module produces merged records, conflicts
and provenance in memory; ``site``/``site_source``/``site_conflict`` writes are
``commons/repository.py`` (T030).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel
from pyproj import Geod
from shapely import Point

from commons.geo import validate_point
from commons.licenses import SourceKind
from commons.models import FieldConflict, SiteRecordV1, SourcedValue, SourceRef

__all__ = [
    "EPSILON_METERS",
    "SOURCE_TRUST_RANK",
    "TAU_NAME_SIMILARITY",
    "FieldObservation",
    "MatchDecision",
    "MergeResult",
    "best_name_similarity",
    "decide_match",
    "distance_m",
    "iter_sourced_values",
    "merge_cluster",
    "name_similarity",
    "name_similarity_by_language",
    "normalize_name",
    "source_refs",
]

#: ε — maximum spatial separation for a fuzzy join, in **metres**.
#: Set by the discovery spike; recorded in tech-design §1.2, the schema card and
#: data-model §5 rule 5. Never inline this number.
EPSILON_METERS: Final[float] = 25.0

#: τ — minimum **same-language** name similarity for a fuzzy join, in [0, 1].
#: Same provenance as :data:`EPSILON_METERS`. Distance alone never merges: both the
#: spatial *and* the name test must pass.
TAU_NAME_SIMILARITY: Final[float] = 0.6

#: WGS84 ellipsoid — the CRS every commons geometry is in (`commons.geo.CRS`).
_GEOD: Final[Geod] = Geod(ellps="WGS84")

#: Which rule decided a pair: the authoritative id join, the fuzzy spatial+name join,
#: or no join at all.
MatchRule = Literal["gers_id", "spatial_name"]


def distance_m(left: Point, right: Point) -> float:
    """Geodesic distance in metres between two EPSG:4326 **(lon, lat)** points.

    Both points are re-validated (`commons.geo.validate_point`) so an axis swap or an
    out-of-range coordinate fails loudly here rather than silently producing a distance
    that happens to fall inside ε.
    """
    a = validate_point(left)
    b = validate_point(right)
    # Geod.inv is (lon, lat) ordered — same axis order as the commons. Returns
    # (forward azimuth, back azimuth, distance-in-metres); only the distance is wanted.
    _, _, metres = _GEOD.inv(a.x, a.y, b.x, b.y)
    return float(metres)


def normalize_name(name: str) -> str:
    """Fold a display name to its comparison form (deterministic, script-preserving).

    Casing, diacritics, punctuation/symbols and whitespace runs carry no identity signal
    for a POI name ("St. Nicholas Tower" ≡ "st nicholas tower"; "Ρόδος" ≡ "Ροδος"), so
    they are folded away. The **script is preserved** — folding Greek to Latin here would
    silently do the transliteration that ingestion owns (data-model §3) and would defeat
    the same-language rule (FAIL-001: never trust/rewrite a value's script).
    """
    # NFKD first so combining marks are separable, then drop them (category Mn).
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    # Punctuation (P*), symbols (S*) and control/format chars (C*) become separators.
    spaced = "".join(
        " " if unicodedata.category(ch)[0] in {"P", "S", "C", "Z"} else ch for ch in stripped
    )
    return " ".join(unicodedata.normalize("NFKC", spaced).casefold().split())


def name_similarity(left: str, right: str) -> float:
    """Similarity of two names in [0, 1] — the τ signal. Symmetric and deterministic.

    ``SequenceMatcher`` is not symmetric in its arguments (its matching-block search is
    anchored on the second sequence), so the pair is **sorted** before comparison: the
    same two names always yield the same number regardless of which record is "left".
    ``autojunk`` is disabled — its heuristic drops characters that appear in >1 % of a
    long sequence, which is popularity-dependent and therefore not reproducible.
    """
    first, second = sorted((normalize_name(left), normalize_name(right)))
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    return SequenceMatcher(None, first, second, autojunk=False).ratio()


def name_similarity_by_language(left: SiteRecordV1, right: SiteRecordV1) -> dict[str, float]:
    """Per-BCP-47-key name similarity, over the keys **both** records carry.

    Only identical keys are compared (``en``↔``en``, ``el-Latn``↔``el-Latn``): that is
    the "same language, post-transliteration" rule. Records sharing no name key get an
    empty mapping — and therefore no name signal, and therefore no fuzzy join.
    """
    shared = sorted(set(left.names) & set(right.names))
    return {tag: name_similarity(left.names[tag].value, right.names[tag].value) for tag in shared}


def best_name_similarity(left: SiteRecordV1, right: SiteRecordV1) -> tuple[str | None, float]:
    """The strongest same-language name agreement as ``(bcp47_tag, similarity)``.

    Best-of rather than all-of: sources disagree on *which* languages they carry a name
    in, and one solid same-language agreement is the signal ε+τ asks for. With no shared
    key at all the answer is ``(None, 0.0)`` — no signal, never a merge.
    """
    scores = name_similarity_by_language(left, right)
    if not scores:
        return None, 0.0
    # max() over (score, tag) keeps ties deterministic: highest score, then lowest tag.
    best_score = max(scores.values())
    best_tag = min(tag for tag, score in scores.items() if score == best_score)
    return best_tag, best_score


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """Why two records were (or were not) joined — auditable, not just a bool.

    ``matched`` is the answer; ``rule``/``distance_m``/``name_similarity``/``language``
    are the evidence, so a surprising merge can be explained without re-running it.
    """

    matched: bool
    rule: MatchRule | None
    reason: str
    distance_m: float | None = None
    name_similarity: float | None = None
    language: str | None = None


def decide_match(left: SiteRecordV1, right: SiteRecordV1) -> MatchDecision:
    """Apply the join rule (tech-design §1.2 / data-model §5 rule 5) to one pair.

    1. **Both carry a ``gers_id``** → the ids decide, full stop. GERS is the
       authoritative cross-source identity; equal ids join, different ids are different
       places and are *not* retried fuzzily (that is what an authoritative key means).
    2. **Otherwise** → fuzzy: ``distance ≤ ε`` **AND** ``same-language name-sim ≥ τ``.
       Both must hold. Distance alone never merges.
    """
    if left.gers_id is not None and right.gers_id is not None:
        matched = left.gers_id == right.gers_id
        verdict = "same" if matched else "different"
        return MatchDecision(
            matched=matched,
            rule="gers_id" if matched else None,
            reason=f"both records carry a gers_id and they are {verdict}",
        )

    metres = distance_m(left.location.value, right.location.value)
    tag, similarity = best_name_similarity(left, right)
    near = metres <= EPSILON_METERS
    named = similarity >= TAU_NAME_SIMILARITY
    if near and named:
        reason = f"distance {metres:.1f} m ≤ ε and {tag} name similarity {similarity:.2f} ≥ τ"
    elif near:
        # The spike's headline failure mode: neighbours, different places.
        reason = (
            f"distance {metres:.1f} m ≤ ε but the name signal is missing "
            f"({similarity:.2f} < τ={TAU_NAME_SIMILARITY}) — distance alone never merges"
        )
    elif named:
        reason = f"name similarity {similarity:.2f} ≥ τ but distance {metres:.1f} m > ε"
    else:
        reason = f"neither test passes (distance {metres:.1f} m, name similarity {similarity:.2f})"
    return MatchDecision(
        matched=near and named,
        rule="spatial_name" if (near and named) else None,
        reason=reason,
        distance_m=metres,
        name_similarity=similarity,
        language=tag,
    )


# ── winner policy ──────────────────────────────────────────────────────────────────
#
# Verbatim from tech-design §1.2 / the schema card: **highest `confidence`, tie-broken by
# source-trust order, then most recent `observed_at`** — plus a final content-derived
# tiebreak added here so the outcome is a pure function of the candidate *set*.

#: Source-trust tiers for the *tie-break* (confidence dominates it). The card names four —
#: Overture/Wikidata > Wikivoyage > OSM tags > open_web; unnamed kinds sit with their
#: nearest kin (encyclopedic → the Wikivoyage tier; a parsed opening-hours string → the
#: OSM tier it was read from; ``user`` below all source-derived data, FR-010; and
#: ``review_provider`` with ``open_web``, both never bundleable).
SOURCE_TRUST_RANK: Final[dict[SourceKind, int]] = {
    "overture": 0,
    "wikidata": 0,
    "wikivoyage": 1,
    "wikipedia": 1,
    "commons": 1,
    "osm": 2,
    "opening_hours_js": 2,
    "user": 3,
    "review_provider": 4,
    "open_web": 4,
}

#: Unknown kinds sort last rather than raising: an unrecognised source is still a source
#: and must not be dropped (FR-009); it simply loses every tie.
_UNKNOWN_TRUST_RANK: Final[int] = max(SOURCE_TRUST_RANK.values()) + 1

#: Point equality tolerance for "is this the same value?": 7 decimals ≈ 1 cm. Float noise
#: from a round-trip through JSON/PostGIS is not a disagreement.
_POINT_PRECISION: Final[int] = 7


def _value_key(value: object) -> str:
    """A stable, comparable identity for a value — used for both equality and ordering."""
    if isinstance(value, Point):
        return f"POINT({value.x:.{_POINT_PRECISION}f} {value.y:.{_POINT_PRECISION}f})"
    return str(value)


def _winner_key(candidate: SourcedValue[Any]) -> tuple[float, int, int, str, str, str]:
    """Ascending sort key for the winner policy; ``min()`` elects the winner.

    In order: **1.** higher ``confidence`` · **2.** better source trust · **3.** more
    recent ``observed_at`` · **4.** ``source.kind``, ``source.id``, then the value — a
    content-only last resort, so two runs over the same candidates, in any input order,
    always elect the same winner.
    """
    return (
        -candidate.confidence,
        SOURCE_TRUST_RANK.get(candidate.source.kind, _UNKNOWN_TRUST_RANK),
        -candidate.observed_at.toordinal(),
        candidate.source.kind,
        candidate.source.id,
        _value_key(candidate.value),
    )


# ── the merge result: record + conflicts + the full provenance ledger ──────────────


@dataclass(frozen=True, slots=True)
class FieldObservation:
    """One candidate value for one field, kept whatever the merge decided — the in-memory
    row of the append-only ``site_source`` table (tech-design §2), the audit trail that
    lets a merge re-run under a better policy without data loss."""

    #: Dotted field path: ``location``, ``names.el-Latn``, ``categories``, …
    field: str
    value: SourcedValue[Any]


@dataclass(frozen=True, slots=True)
class MergeResult:
    """A merged record plus everything needed to justify it."""

    record: SiteRecordV1
    #: Every candidate considered, in field order — nothing here is ever dropped.
    provenance: tuple[FieldObservation, ...] = ()
    #: The ids of the input records this result was merged from (1 = nothing to merge).
    merged_from: tuple[UUID, ...] = ()

    @property
    def source_refs(self) -> frozenset[SourceRef]:
        """Every source ref reachable from this result — the FR-009 invariant's subject."""
        return source_refs(self.record) | frozenset(o.value.source for o in self.provenance)


def iter_sourced_values(record: SiteRecordV1) -> Iterator[tuple[str, SourcedValue[Any]]]:
    """Every ``(field_path, SourcedValue)`` a record carries, conflict candidates included."""
    for tag in sorted(record.names):
        yield f"names.{tag}", record.names[tag]
    yield "location", record.location
    for value in record.categories:
        yield "categories", value
    for name in _SCALAR_FIELDS:
        scalar: SourcedValue[Any] | None = getattr(record, name)
        if scalar is not None:
            yield name, scalar
    for name in ("notes", "links"):
        for value in getattr(record, name):
            yield name, value
    for conflict in record.conflicts:
        for candidate in conflict.candidates:
            yield conflict.field, candidate


def source_refs(record: SiteRecordV1) -> frozenset[SourceRef]:
    """The distinct source refs reachable in a record (values, conflicts and stories)."""
    refs = {value.source for _, value in iter_sourced_values(record)}
    for story in record.stories:
        refs.add(story.source)
        refs.update(claim.source for claim in story.claims)
    return frozenset(refs)


# ── per-field merge ────────────────────────────────────────────────────────────────

#: Single-valued ``SourcedValue | None`` fields, merged by :meth:`_FieldMerger.pick`.
_SCALAR_FIELDS: Final[tuple[str, ...]] = (
    "address",
    "opening_hours",
    "phone",
    "price",
    "accessibility",
    "website",
)


class _FieldMerger:
    """Accumulates per-field decisions for one cluster: winners, conflicts, provenance."""

    def __init__(self) -> None:
        self.provenance: list[FieldObservation] = []
        self.conflicts: list[FieldConflict] = []

    def pick(self, field: str, candidates: Sequence[SourcedValue[Any]]) -> SourcedValue[Any] | None:
        """Elect the winner for a single-valued field and record any disagreement.

        Candidates go to the provenance ledger *first*, so no later decision can lose a
        source. More than one distinct value ⇒ a :class:`~commons.models.FieldConflict`
        carrying **all** of them (winner included, so it is self-contained), resolved
        ``picked:<source.id>``.
        """
        self.provenance.extend(FieldObservation(field=field, value=c) for c in candidates)
        if not candidates:
            return None
        ordered = sorted(candidates, key=_winner_key)
        if len({_value_key(c.value) for c in ordered}) > 1:
            self.conflicts.append(
                FieldConflict(
                    field=field,
                    candidates=tuple(ordered),
                    resolution=f"picked:{ordered[0].source.id}",
                )
            )
        return ordered[0]

    def union(
        self, field: str, candidates: Sequence[SourcedValue[Any]]
    ) -> tuple[SourcedValue[Any], ...]:
        """Union-first for multi-valued fields (``categories``, ``notes``, ``links``).

        Two sources listing *different* categories are complementary, not in conflict —
        the union *is* the merge, and no conflict is recorded. Within one repeated value
        the winner policy picks the stamp to keep; losing stamps stay in the ledger.
        Output is ordered by value, so the merge is reproducible.
        """
        self.provenance.extend(FieldObservation(field=field, value=c) for c in candidates)
        groups: dict[str, list[SourcedValue[Any]]] = {}
        for candidate in candidates:
            groups.setdefault(_value_key(candidate.value), []).append(candidate)
        return tuple(min(group, key=_winner_key) for _, group in sorted(groups.items()))


def _unique[T: BaseModel](groups: Iterable[Sequence[T]]) -> list[T]:
    """Concatenate, dropping structural duplicates. Order-preserving, ``__eq__``-based —
    these models nest dicts, so they are not hashable and a ``set`` cannot be used."""
    unique: list[T] = []
    for group in groups:
        unique.extend(item for item in group if item not in unique)
    return unique


def merge_cluster(records: Sequence[SiteRecordV1]) -> MergeResult:
    """Merge records already known to be the same place, **per field**.

    A single-record cluster still returns a :class:`MergeResult` (with its provenance
    ledger), so callers have exactly one shape to persist.
    """
    if not records:
        raise ValueError("merge_cluster() needs at least one record")
    merger = _FieldMerger()

    names: dict[str, SourcedValue[str]] = {}
    for tag in sorted({tag for record in records for tag in record.names}):
        winner = merger.pick(f"names.{tag}", [r.names[tag] for r in records if tag in r.names])
        if winner is not None:
            names[tag] = winner

    # `location` is required on every record, so `pick` over a non-empty cluster always
    # elects one; the fallback keeps the type honest without an assert.
    location = merger.pick("location", [r.location for r in records]) or records[0].location
    scalars = {
        name: merger.pick(name, [v for r in records if (v := getattr(r, name)) is not None])
        for name in _SCALAR_FIELDS
    }
    multi = {
        name: merger.union(name, [v for r in records for v in getattr(r, name)])
        for name in ("categories", "notes", "links")
    }
    # Conflicts already on the inputs travel with them: a re-merge enriches, and must
    # never drop a disagreement (or its sources) recorded by an earlier pass.
    conflicts = tuple(
        sorted(
            (*_unique(r.conflicts for r in records), *merger.conflicts),
            key=lambda c: (c.field, c.resolution, len(c.candidates)),
        )
    )
    # M2+; `ReviewSummary` carries no SourceRef of its own, so the freshest wins outright.
    reviews = max(
        (r.reviews for r in records if r.reviews is not None),
        key=lambda summary: summary.fetched_at,
        default=None,
    )
    gers_ids = sorted({r.gers_id for r in records if r.gers_id is not None})
    record = SiteRecordV1(
        # Deterministic identity: the smallest input UUID. The repository (T030) re-keys
        # onto the existing commons row on upsert — nothing may assume this id is new.
        id=min(r.id for r in records),
        # A cluster holds at most one distinct gers_id — GERS is authoritative identity,
        # so the clustering layer never puts two of them in one cluster.
        gers_id=gers_ids[0] if gers_ids else None,
        names=names,
        location=location,
        categories=multi["categories"],
        address=scalars["address"],
        opening_hours=scalars["opening_hours"],
        # Narration is additive (and empty in slice 001 — FR-011).
        stories=tuple(_unique(r.stories for r in records)),
        notes=multi["notes"],
        phone=scalars["phone"],
        price=scalars["price"],
        accessibility=scalars["accessibility"],
        website=scalars["website"],
        links=multi["links"],
        reviews=reviews,
        conflicts=conflicts,
        updated_at=max(r.updated_at for r in records),
    )
    return MergeResult(
        record=record,
        provenance=tuple(merger.provenance),
        merged_from=tuple(r.id for r in records),
    )
