"""T034 + T059 — the ``curate`` node: merge, transliterate, then rank via the seam.

Three steps, in this order, and the order is the point:

1. **Merge** (:func:`commons.merge.merge_records`). That module owns the join rule (ε=25 m
   ∧ τ=0.6, union-first); nothing here re-implements or tunes it.
2. **Transliterate** (T059, :func:`commons.translit.derive_latin_names`) over the merged
   record's ``names`` **only**. Addresses are never transliterated (FAIL-001 / ADR-0010) —
   a source script is a *claim*, and the Hebrew street stored as Cyrillic is why. Every
   :class:`~commons.translit.ScriptFlag` is surfaced on :attr:`CurateResult.flags`; a
   flagged value is never transliterated and never silently dropped. Derived values
   **inherit** their parent's :class:`~commons.models.SourceRef` (license, attribution and
   ``bundleable`` carry through unchanged) and are appended to the provenance ledger, so
   nothing added at curate time is invisible to the audit trail.
3. **Rank** (Sonnet tier via :class:`commons.llm.ModelRouter`). The model sees **ids and
   names only** and may return **an ordering of ids** — nothing else. That is enforced
   mechanically, not by prompt: an unknown id is refused, a duplicate is refused, and any
   attempt to hand back a value, a field or a coordinate is refused — each into
   :attr:`CurateResult.rejected` with its reason. Ids the model omitted are appended in
   input order, so ranking can reorder the commons but can never lose from it. The model
   therefore cannot emit or compute a coordinate (FR-005) and cannot contribute an
   unstamped value (FR-003): the only thing it can influence is order.

With ``router=None`` the node is fully deterministic and offline — merge order stands and
``rejected`` is empty. That is the mode every unit test and the trajectory eval run in.

Seam purity (ADR-0004): the tier is named, never a model; no provider SDK is imported.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from commons.llm import Message, ModelRouter, TaskTier
from commons.merge import FieldObservation, MergeResult, merge_records
from commons.models import SiteRecordV1
from commons.translit import ScriptFlag, derive_latin_names

__all__ = ["RANKING_SYSTEM_PROMPT", "CurateResult", "curate"]

#: The ranking contract, stated to the model. It is *also* enforced after the fact — this
#: prompt is a courtesy to the model, never the guarantee (Constitution Article VI).
#:
#: **Sized to be cacheable (FAIL-006).** ``curate()`` sends this with ``cache_prefix=True``, and
#: Anthropic caches nothing below the tier model's minimum cacheable prefix — silently, with no
#: error — so a prefix under that bar leaves ``cache_read`` pinned at 0 for ever. Sonnet 5's
#: minimum is 1024 tokens; every paragraph below earns its place on ranking quality, and the
#: size is the consequence rather than the goal. ``evals/test_caching.py`` holds the bar; the
#: fragments are implicitly concatenated so re-wrapping this source can never put a newline
#: inside a sentence. Editing the text means mirroring it into ``prompts/research.md`` §3.2 and
#: bumping that file's ``version`` in the same PR (Article VII).
RANKING_SYSTEM_PROMPT = (
    "You rank candidate places for a day tour by traveller salience: how likely a visitor "
    "with one day in an unfamiliar area is to want to see this place, judged only from its "
    "names.\n\n"
    "## What you are given\n\n"
    "You are given a JSON array of objects with exactly two keys: `id` and `names`. `id` is "
    "an opaque identifier; `names` holds one or more names for the same place, in one or "
    "more languages or scripts. The array is one area's candidates, already merged. There "
    "are no coordinates, no categories, no descriptions and no source data: the names and "
    "the ids are the whole of the evidence, and you must neither ask for more nor assume "
    "any.\n\n"
    "## What you return\n\n"
    "Reply with a JSON array of the given `id` strings, most salient first, and NOTHING "
    "else — no prose, no code fence, no objects, no scores, no coordinates, no new or "
    "corrected field values. Include every id exactly once, including the ones you rank "
    "last. Any element that is not one of the given id strings is discarded and reported "
    "as a refusal, and so is a repeated id; the records themselves are never dropped, so a "
    "malformed reply costs ordering and nothing more.\n\n"
    "This is a boundary, not a formatting preference. Every value here carries the source "
    "it was read from, that source's license, and the date it was observed — all applied "
    "where the data was read, before you saw it. A name, a category or a coordinate "
    "offered by you would carry none of those, so it is discarded unread: correcting a "
    "name you believe is wrong, or supplying a location you believe you know, cannot "
    "succeed and only costs the reply its ordering. The order is the one thing you "
    "decide.\n\n"
    "## Judging salience\n\n"
    "Rank on how much a first-time visitor would regret missing the place — not on how "
    "locally important it is, how large it is, or how familiar it is to you. In descending "
    "order:\n\n"
    "1. Named landmarks: monuments, archaeological sites, castles, fortifications, gates, "
    "towers.\n"
    "2. Museums and galleries; religious buildings of historic or architectural note.\n"
    "3. Named viewpoints, beaches, gardens, parks and squares — places whose draw is the "
    "setting.\n"
    "4. Named markets, historic streets and quarters, harbours and waterfronts.\n"
    "5. Ordinary hospitality and retail: cafés, restaurants, bars, hotels, shops. These "
    "come last among places a visitor would choose, unless the name marks one out as an "
    "institution in its own right.\n"
    "6. Infrastructure and civic function: car parks, clinics, offices, schools, stops, "
    "depots, utility sites. These are the floor, whatever else the name suggests.\n\n"
    "A name that states its own category — `Archaeological Museum`, `Parking P3` — is "
    "strong evidence and should be used. A bare personal or place name states nothing: "
    "rank it in the middle rather than at either extreme, because guessing high and "
    "guessing low are equally wrong. Do not read prominence into a name merely for being "
    "long, official-sounding or unfamiliar to you.\n\n"
    "Within a band, prefer the entry whose name is specific to one place over a name that "
    "could belong to anything, and prefer what a visitor plausibly arrived wanting to see. "
    "Do not manufacture distinctions between ties: where two entries are equivalent on the "
    "evidence you have, either order is correct and neither is worth deliberating over.\n\n"
    "## Names in a source script\n\n"
    "Many places carry a name only in the local script, with no Latin twin. That is a "
    "property of the source data, never a signal about the place. Do not rank a place "
    "lower because its name is in a script you find harder to read, and do not prefer a "
    "place because its name happens to be in English. Where a place carries several names, "
    "judge it on the most informative one and treat the rest as the same place. A name is a "
    "claim about a place, not a fact about it: where two names disagree, rank on the one "
    "that names a category, and leave both alone.\n\n"
    "## Near-duplicates\n\n"
    "Two entries may describe one real place where the merge upstream could not prove it — "
    "a name and its transliteration, a name with and without a generic prefix, the same "
    "place recorded in two scripts. Rank such entries next to each other so the ordering "
    "reads coherently, and keep both. Never drop one, never fold them into a single id, and "
    "never report the suspicion: you cannot confirm it from names alone, and that decision "
    "belongs to the caller.\n\n"
    "## Worked examples\n\n"
    "Given:\n\n"
    '[{"id": "7f3a", "names": ["Δημοτικό Πάρκο"]},\n'
    ' {"id": "b129", "names": ["Κάστρο Ιπποτών", "Kastro Ippoton"]},\n'
    ' {"id": "c004", "names": ["Parking P3"]},\n'
    ' {"id": "d51e", "names": ["Αρχαιολογικό Μουσείο", "Archaeological Museum"]}]\n\n'
    "Reply:\n\n"
    '["b129", "d51e", "7f3a", "c004"]\n\n'
    "The castle leads as a named landmark and the museum follows it; the municipal park is "
    "a setting; the car park is infrastructure and ranks last. The castle's two names are "
    "one place, not two candidates, and its Greek name counts for neither more nor less "
    "than the Latin one. Every id appears exactly once, and the reply carries ids and "
    "nothing besides — no scores, no names, and no account of the reasoning.\n\n"
    "A near-duplicate pair, given:\n\n"
    '[{"id": "0a1b", "names": ["Παλιά Αγορά"]},\n'
    ' {"id": "0a1c", "names": ["Palia Agora"]},\n'
    ' {"id": "9d2f", "names": ["Δημαρχείο", "Town Hall"]}]\n\n'
    "Reply:\n\n"
    '["0a1b", "0a1c", "9d2f"]\n\n'
    "The market's two spellings are ranked adjacently and both kept, although they are "
    "very probably one place: establishing that is not your job, and dropping either would "
    "lose a record. The town hall is civic function and ranks below them. By contrast a "
    'reply of `[{"id": "0a1b", "score": 0.9}]` earns nothing: the object is refused on '
    "sight, because an object is the shape in which a value or a coordinate would arrive, "
    "and the three ids would simply be kept in the order they were given."
)


@dataclass(frozen=True, slots=True)
class CurateResult:
    """The merged, transliterated, ranked commons candidates plus everything refused."""

    results: tuple[MergeResult, ...]
    #: FAIL-001 script mismatches — surfaced, never silently dropped.
    flags: tuple[ScriptFlag, ...]
    #: How many ``*-Latn`` display names were added at curate time.
    derived_names: int
    #: Model output that was refused, one human-readable reason per offending element.
    rejected: tuple[str, ...]

    @property
    def conflicts(self) -> int:
        """Total :class:`~commons.models.FieldConflict`s across the merged records."""
        return sum(len(result.record.conflicts) for result in self.results)


def _transliterate(
    result: MergeResult, *, observed_at: date | None
) -> tuple[MergeResult, tuple[ScriptFlag, ...], int]:
    """Add the ``*-Latn`` twins to one merged record; originals survive verbatim (T059)."""
    derivation = derive_latin_names(result.record.names, observed_at=observed_at)
    if not derivation.derived:
        return result, derivation.flags, 0
    # `model_copy(update=...)` re-validates on `StampedModel`, so the enriched record is
    # held to the same quarantine/stamp rules as one built from scratch.
    enriched = result.record.model_copy(update={"names": dict(derivation.names)})
    ledger = tuple(
        FieldObservation(field=f"names.{tag}", value=derivation.names[tag])
        for tag in derivation.derived
    )
    return (
        replace(result, record=enriched, provenance=result.provenance + ledger),
        derivation.flags,
        len(derivation.derived),
    )


def _ranking_request(results: Sequence[MergeResult]) -> str:
    """The user turn: **ids and names only** — no coordinates ever cross the seam."""
    return json.dumps(
        [
            {
                "id": str(result.record.id),
                "names": [result.record.names[tag].value for tag in sorted(result.record.names)],
            }
            for result in results
        ],
        ensure_ascii=False,
    )


def _decode_ranking(text: str) -> tuple[list[object], str | None]:
    """Parse the model's reply into raw elements, or say why the whole reply is refused."""
    stripped = text.strip()
    if not stripped:
        return [], "the model returned an empty ranking; keeping merge order"
    try:
        decoded: object = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return [], f"the model's ranking is not JSON ({exc.msg}); keeping merge order"
    if not isinstance(decoded, list):
        return [], (
            f"the model returned a {type(decoded).__name__}, not an array of ids; "
            "keeping merge order"
        )
    return list(decoded), None


def _apply_ranking(
    results: Sequence[MergeResult], elements: Sequence[object]
) -> tuple[tuple[MergeResult, ...], tuple[str, ...]]:
    """Order ``results`` by the model's ids, refusing anything that is not one of them.

    Nothing is lost and nothing is invented: an id the model omitted is appended in input
    order, and an element that is not a known, not-yet-used id string is refused with a
    reason. An object/number/array element is the shape the model would use to smuggle a
    value or a coordinate back — it is refused on sight (FR-003 / FR-005).
    """
    by_id = {str(result.record.id): result for result in results}
    ordered: list[MergeResult] = []
    seen: set[str] = set()
    rejected: list[str] = []

    for element in elements:
        if not isinstance(element, str):
            rejected.append(
                f"refused a {type(element).__name__} in the ranking "
                f"({json.dumps(element, ensure_ascii=False, default=str)}): the model may "
                "return ids only, never a value, a field or a coordinate"
            )
            continue
        if element not in by_id:
            rejected.append(f"refused unknown id {element!r}: it is not one of the curated records")
            continue
        if element in seen:
            rejected.append(f"refused duplicate id {element!r}: each record is ranked once")
            continue
        seen.add(element)
        ordered.append(by_id[element])

    ordered.extend(result for result in results if str(result.record.id) not in seen)
    return tuple(ordered), tuple(rejected)


def curate(
    records: Sequence[SiteRecordV1],
    *,
    router: ModelRouter | None = None,
    observed_at: date | None = None,
) -> CurateResult:
    """Merge stamped records into commons candidates, transliterate, then rank them.

    ``observed_at`` dates the derived ``*-Latn`` values (defaults to today, injected in
    tests for reproducibility). ``router=None`` skips the ranking call entirely.
    """
    merged: list[MergeResult] = []
    flags: list[ScriptFlag] = []
    derived_names = 0
    for result in merge_records(records):
        enriched, result_flags, derived = _transliterate(result, observed_at=observed_at)
        merged.append(enriched)
        flags.extend(result_flags)
        derived_names += derived

    ordered: tuple[MergeResult, ...] = tuple(merged)
    rejected: tuple[str, ...] = ()
    if router is not None and merged:
        try:
            completion = router.complete(
                TaskTier.CURATE,
                system=RANKING_SYSTEM_PROMPT,
                messages=[Message(role="user", content=_ranking_request(merged))],
                cache_prefix=True,
            )
        except Exception as exc:  # FR-012: an unavailable model costs order, not records
            rejected = (f"refused the ranking ({type(exc).__name__}: {exc}); keeping merge order",)
        else:
            elements, refusal = _decode_ranking(completion.text)
            ordered, element_refusals = _apply_ranking(merged, elements)
            rejected = ((refusal,) if refusal else ()) + element_refusals

    return CurateResult(
        results=ordered,
        flags=tuple(flags),
        derived_names=derived_names,
        rejected=rejected,
    )
