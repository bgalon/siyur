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
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final, Literal

from pyproj import Geod
from shapely import Point

from commons.geo import validate_point
from commons.models import SiteRecordV1

__all__ = [
    "EPSILON_METERS",
    "TAU_NAME_SIMILARITY",
    "MatchDecision",
    "best_name_similarity",
    "decide_match",
    "distance_m",
    "name_similarity",
    "name_similarity_by_language",
    "normalize_name",
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
