"""T044 — the licence quarantine (`compiler/quarantine.py`) against the bare-`SourceRef` trap.

Ground truth: `compiler/quarantine.py`'s own module docstring, `commons/models.py`'s
"bundleability is derived structurally" note, ADR-0025 ruling 8 (+ amendment A3/A4),
`docs/data/bundle-manifest.md`'s `withheld[]` section, and `specs/002-plan-compile-offline/
tasks.md` T038/T044.

T044's stated claim — a known `bundleable=false` value appears **nowhere** in the bundle and
its removal is recorded in `withheld[]` — is the easy 90%. The traps T038 exists to avoid are
the ones a filter can get wrong while still compiling, hashing and passing every path check:

1. **Structural derivation.** Only a `SourcedValue` carries a `bundleable` field; `Story` and
   `RouteLegV1` carry a bare `SourceRef` and must be *derived* via
   `licenses.bundleable(kind, license)`. The `getattr(v, "bundleable", False)` repair reads
   `None`/absent off either and silently drops every story and every walking leg from every
   bundle. Pinned below as the survival of a bundleable story and a bundleable leg, each with
   a failure message stating the cost.
2. **`withheld[].reason` is closed** (`license_forbids_redistribution` / `source_unavailable`
   only) and is never `licenses.quarantine_reason(...)`'s prose — that function names the
   internal allowlist, and `withheld` ships inside a file the user downloads.
3. **`unstamped` is not a member.** FR-012 refuses unstamped input; the compile *fails*
   there rather than recording a withheld placeholder that looks identical to success.

Also pinned: a refused leg raises rather than being dropped (a `withheld` entry needs a
`site_id`, and a leg has none); a refused `location` withholds the whole site; an
unclassified structure raises rather than riding through unchecked; and `withheld[].field`
paths are **pre-removal** — the property that keeps the record auditable once survivors
re-index.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, get_args
from uuid import uuid4

import pytest
from pydantic import ValidationError

from commons import licenses
from commons.models import (
    RouteLegV1,
    SiteRecordV1,
    SourceRef,
    StampedModel,
    WithheldReason,
    WithheldValue,
)
from compiler.quarantine import (
    QuarantineResult,
    UnbundleableLegError,
    UnclassifiedStructureError,
    UnstampedValueError,
    _filter,  # private on purpose: the walk's catch-all is the contract this file pins
    is_bundleable,
    quarantine_legs,
    quarantine_record,
    quarantine_sites,
)

OBSERVED = date(2026, 8, 8)

#: The M1 routing stamp `route-leg.md` fixes — routing over OSM is a Produced Work of OSM.
ROUTING_SOURCE = SourceRef(
    kind="osm",
    id="valhalla:pedestrian",
    url=None,
    license="ODbL-1.0",
    attribution="© OpenStreetMap contributors",
)


def _stamped(value: Any, *, kind: str, license_id: str, id_: str = "src") -> dict[str, Any]:
    """A `SourcedValue` payload with `bundleable` **derived**, never author-set — as every
    real adapter produces one (`SourcedValue.stamp`, `commons/licenses.py::bundleable`)."""
    return {
        "value": value,
        "source": {"kind": kind, "id": id_, "license": license_id},
        "bundleable": licenses.bundleable(kind, license_id),
        "confidence": 0.8,
        "observed_at": OBSERVED.isoformat(),
    }


def _stamped_point(
    *, kind: str, license_id: str, coordinates: tuple[float, float] = (28.2247, 36.4443)
) -> dict[str, Any]:
    return _stamped(
        {"type": "Point", "coordinates": list(coordinates)}, kind=kind, license_id=license_id
    )


def _record(**overrides: Any) -> SiteRecordV1:
    payload: dict[str, Any] = {
        "location": _stamped_point(kind="overture", license_id="CDLA-Permissive-2.0"),
        "names": {
            "en": _stamped(
                "Palace of the Grand Master", kind="overture", license_id="CDLA-Permissive-2.0"
            )
        },
    }
    payload.update(overrides)
    return SiteRecordV1.model_validate(payload)


def _leg(*, source: SourceRef = ROUTING_SOURCE, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "leg-0",
        "from_stop": 0,
        "to_stop": 1,
        "geometry": {
            "type": "LineString",
            "coordinates": [[28.2247, 36.4443], [28.2238, 36.4447]],
        },
        "distance_m": 380.0,
        "duration_s": 300,
        "source": source.model_dump(),
    }
    payload.update(overrides)
    return payload


def _leg_model(**overrides: Any) -> RouteLegV1:
    return RouteLegV1.model_validate(_leg(**overrides))


# ── T044 baseline: a known bundleable=false value is nowhere in the bundle, and recorded ───


def test_a_known_unbundleable_value_is_dropped_and_recorded() -> None:
    """The core T044 claim. `open_web` is *never* bundleable (`NEVER_BUNDLEABLE_KINDS`), even
    stamped CC0 — so this is a value quarantine must drop regardless of its licence string."""
    record = _record(notes=[_stamped("unverified rumour", kind="open_web", license_id="CC0-1.0")])

    result = quarantine_record(record)

    assert isinstance(result, QuarantineResult)
    assert len(result.sites) == 1
    assert result.sites[0].notes == ()
    assert "unverified rumour" not in result.sites[0].model_dump_json()
    assert result.withheld == (
        WithheldValue(site_id=record.id, field="notes[0]", reason="license_forbids_redistribution"),
    )


# ── trap 1: bare-SourceRef shapes (Story, RouteLegV1) must DERIVE, never read a stamp ──────


def test_a_bundleable_story_survives_quarantine_unchanged() -> None:
    """ADR-0025 ruling 8 / `commons/models.py` module docstring: `Story` carries a bare
    `SourceRef`, no `bundleable` field. If quarantine ever reads `getattr(story, "bundleable",
    False)` instead of deriving via `licenses.bundleable(...)`, this is the assertion that
    goes red — and the regression it is guarding is that a compiled bundle would carry zero
    narration for every place, silently, past every hash and path check."""
    record = _record(
        stories=[
            {
                "text_by_lang": {"en": "The cobbled street once housed the …"},
                "source": {"kind": "wikivoyage", "id": "en:Rhodes", "license": "CC-BY-SA-4.0"},
                "observed_at": OBSERVED.isoformat(),
            }
        ]
    )
    good_story = record.stories[0]

    result = quarantine_record(record)

    assert result.sites[0].stories == (good_story,), (
        "a bundleable CC-BY-SA-4.0 story must come back unchanged; losing this means the "
        "compiled bundle silently ships with no narration at all"
    )
    assert result.withheld == ()


def test_a_non_bundleable_story_is_dropped_and_recorded() -> None:
    record = _record(
        stories=[
            {
                "text_by_lang": {"en": "Kept."},
                "source": {"kind": "wikivoyage", "id": "en:Rhodes", "license": "CC-BY-SA-4.0"},
                "observed_at": OBSERVED.isoformat(),
            },
            {
                # kind alone forbids this one, whatever the licence string claims.
                "text_by_lang": {"en": "Dropped."},
                "source": {"kind": "open_web", "id": "example.com/x", "license": "CC-BY-SA-4.0"},
                "observed_at": OBSERVED.isoformat(),
            },
        ]
    )
    good_story = record.stories[0]

    result = quarantine_record(record)

    assert result.sites[0].stories == (good_story,)
    assert result.withheld == (
        WithheldValue(
            site_id=record.id, field="stories[1]", reason="license_forbids_redistribution"
        ),
    )


def test_a_bundleable_story_keeps_the_story_and_loses_a_non_bundleable_claim() -> None:
    """The module docstring's own example: "a bundleable story with a non-bundleable claim
    keeps the story and loses the claim." `Claim` is the same bare-`SourceRef` shape as
    `Story` one level down, and it is walked, not just the story's top-level stamp."""
    record = _record(
        stories=[
            {
                "text_by_lang": {"en": "The palace once …"},
                "source": {"kind": "wikivoyage", "id": "en:Rhodes", "license": "CC-BY-SA-4.0"},
                "observed_at": OBSERVED.isoformat(),
                "claims": [
                    {
                        "span": "The palace once housed the Knights.",
                        "source": {
                            "kind": "wikivoyage",
                            "id": "en:Rhodes",
                            "license": "CC-BY-SA-4.0",
                        },
                    },
                    {
                        "span": "It burned down in 1856.",
                        "source": {
                            "kind": "open_web",
                            "id": "en:Rhodes",
                            "license": "CC-BY-SA-4.0",
                        },
                    },
                ],
            }
        ]
    )
    good_claim = record.stories[0].claims[0]

    result = quarantine_record(record)

    kept_story = result.sites[0].stories[0]
    assert kept_story.claims == (good_claim,)
    assert result.withheld == (
        WithheldValue(
            site_id=record.id, field="stories[0].claims[1]", reason="license_forbids_redistribution"
        ),
    )


def test_a_bundleable_leg_survives_quarantine_unchanged() -> None:
    """The sibling pin for `RouteLegV1` — the same bare-`SourceRef` shape as `Story`, and the
    regression the module docstring names outright: a filter that cannot derive for this shape
    drops every walking leg from every bundle, silently, past every hash and path check."""
    leg = _leg_model()
    assert licenses.bundleable(leg.source.kind, leg.source.license) is True  # ground truth

    kept = quarantine_legs((leg,))

    assert kept == (leg,), (
        "a bundleable ODbL routing leg must come back unchanged from quarantine_legs; losing "
        "this means the traveller's day has no routes, offline, with nothing to fall back to"
    )


def test_a_refused_leg_raises_instead_of_being_dropped() -> None:
    """Every `withheld[]` entry needs a `site_id`, and a leg has none — `withheld` is keyed by
    site, not leg. A dropped leg is therefore a silent gap in the traveller's day that the
    manifest cannot express, so this must be a compile failure, never a removal."""
    bad_leg = _leg_model(source=SourceRef(kind="open_web", id="x", license="CC0-1.0"))

    with pytest.raises(UnbundleableLegError, match="leg-0"):
        quarantine_legs((bad_leg,))


# ── withheld[].reason: closed enum, never licenses.quarantine_reason's prose ───────────────


def test_withheld_reason_is_the_enum_member_not_licenses_prose() -> None:
    """ADR-0025 A3: `withheld` ships inside a file the user downloads, so `reason` is closed
    to exactly two members. `licenses.quarantine_reason` returns a *different* string — prose
    naming the internal allowlist — and this module deliberately does not source from it."""
    record = _record(notes=[_stamped("x", kind="review_provider", license_id="CC0-1.0")])
    prose = licenses.quarantine_reason("review_provider", "CC0-1.0")
    assert prose != "license_forbids_redistribution"  # the mutation this pins would emit prose

    result = quarantine_record(record)

    assert result.withheld[0].reason == "license_forbids_redistribution"
    assert result.withheld[0].reason != prose
    assert result.withheld[0].reason in get_args(WithheldReason)


def test_withheld_reason_field_rejects_anything_outside_the_closed_set() -> None:
    """The enum is closed at the model too — a belt-and-braces pin alongside the assertion
    above, since a `WithheldValue` cannot even be constructed with a free-text reason."""
    prose = licenses.quarantine_reason("open_web", "proprietary")
    with pytest.raises(ValidationError):
        WithheldValue.model_validate({"site_id": uuid4(), "field": "reviews", "reason": prose})


# ── unstamped input is refused (FR-012), never recorded as a withheld placeholder ──────────


class _NoProvenance:
    """A structure carrying no `source` attribute or key at all — nothing to derive from."""


def test_an_unstamped_object_is_refused_not_withheld() -> None:
    """FR-012: unstamped input is *refused* — the compile fails. `WithheldReason` deliberately
    has no `unstamped` member (`bundle-manifest.md`), because a placeholder in a downloaded
    manifest is indistinguishable from success. `is_bundleable` must raise here, never return
    `False` and let the caller record-and-continue."""
    with pytest.raises(UnstampedValueError):
        is_bundleable(_NoProvenance())


def test_a_mapping_with_no_source_key_is_also_refused() -> None:
    """`_stamp_of` reads a stamp off either an attribute or a `"source"` mapping key (a
    payload arriving from storage or an API body) — pin both doors."""
    with pytest.raises(UnstampedValueError):
        is_bundleable({"value": "x"})


def test_a_mapping_with_an_unvalidated_source_dict_is_still_refused() -> None:
    """A raw `dict` under `"source"` is merely *claimed* provenance, not a validated
    `SourceRef` — accepting it would mint the FR-012 stamp at compile time instead of
    demanding it at ingestion, which is exactly the gap FR-012 exists to close."""
    with pytest.raises(UnstampedValueError):
        is_bundleable({"source": {"kind": "osm", "id": "x", "license": "ODbL-1.0"}})


# ── a refused `location` withholds the whole site, under that one path ─────────────────────


def test_a_refused_location_withholds_the_whole_site() -> None:
    """`location` is the record's one required `SourcedValue`, so a refused location cannot be
    *removed* — there is no record left to bundle. The site is withheld wholesale and nothing
    else about it is reported, because nothing else about it ships either."""
    record = _record(
        location=_stamped_point(kind="open_web", license_id="CC0-1.0"),
        notes=[_stamped("keep?", kind="overture", license_id="CDLA-Permissive-2.0")],
    )

    result = quarantine_record(record)

    assert result.sites == ()
    assert result.withheld == (
        WithheldValue(site_id=record.id, field="location", reason="license_forbids_redistribution"),
    )


def test_quarantine_sites_concatenates_kept_sites_and_withheld_across_records() -> None:
    keep_record = _record()
    drop_record = _record(location=_stamped_point(kind="open_web", license_id="CC0-1.0"))
    assert keep_record.id != drop_record.id  # server-generated ids, not accidentally equal

    result = quarantine_sites((keep_record, drop_record))

    assert result.sites == (keep_record,)
    assert result.withheld == (
        WithheldValue(
            site_id=drop_record.id, field="location", reason="license_forbids_redistribution"
        ),
    )


# ── an unrecognised structure raises rather than riding into the bundle unchecked ──────────


class _Alien:
    """Not a `StampedModel`, not a JSON leaf, not a `Mapping`/sequence — quarantine has no
    rule for this shape, and a walk that shrugged would ship it unchecked."""


def test_an_unrecognised_type_raises_rather_than_riding_through() -> None:
    with pytest.raises(UnclassifiedStructureError):
        _filter(_Alien(), "mystery", uuid4(), [])


class _MysteryStructure(StampedModel):
    """A `StampedModel` with no `source` field — not a `SourcedValue`, not one of the
    declared never-bundleable structures, not a declared stampless container. This is the
    "field added to `SiteRecordV1` later" case the module docstring names: quarantine must
    stop rather than guess."""

    note: str


def test_a_stamped_model_with_no_classification_raises_rather_than_riding_through() -> None:
    with pytest.raises(UnclassifiedStructureError):
        _filter(_MysteryStructure(note="unclassified"), "mystery", uuid4(), [])


# ── withheld[].field is pre-removal: survivors re-index, the recorded path does not ────────


def test_withheld_field_paths_are_pre_removal_survivors_reindex() -> None:
    """`bundle-manifest.md`: `field` addresses the record **as it stood before quarantine
    ran**. Three notes with the middle one refused: the surviving two re-index to
    `notes[0]`/`notes[1]` in the output, but the recorded path must still say `notes[1]` —
    the position the refused note held in the *input* — because that is the only path that
    makes `withheld` auditable against the pre-quarantine record."""
    record = _record(
        notes=[
            _stamped("keep A", kind="overture", license_id="CDLA-Permissive-2.0"),
            _stamped("drop me", kind="open_web", license_id="CC0-1.0"),
            _stamped("keep B", kind="osm", license_id="ODbL-1.0"),
        ]
    )
    keep_a, _dropped, keep_b = record.notes

    result = quarantine_record(record)

    assert result.sites[0].notes == (keep_a, keep_b)
    assert result.withheld == (
        WithheldValue(site_id=record.id, field="notes[1]", reason="license_forbids_redistribution"),
    )


def test_withheld_field_paths_use_dotted_keys_for_mapping_fields() -> None:
    """The card's own example path shape (`names.el`): a dropped entry inside a `Mapping`
    field is addressed by its key dotted onto the parent path, not indexed like a sequence."""
    record = _record(
        names={
            "en": _stamped("Rhodes", kind="overture", license_id="CDLA-Permissive-2.0"),
            "el": _stamped("Ρόδος", kind="open_web", license_id="CC0-1.0"),
        }
    )

    result = quarantine_record(record)

    assert set(result.sites[0].names) == {"en"}
    assert result.withheld == (
        WithheldValue(site_id=record.id, field="names.el", reason="license_forbids_redistribution"),
    )


# ── a stampless container (FieldConflict) never discards its bundleable candidate ──────────


def test_a_field_conflict_keeps_its_bundleable_candidate_and_loses_the_other() -> None:
    """§1.2: merge never discards a source, so a conflict routinely carries one allowlisted
    candidate beside one that is not. `FieldConflict` carries no `SourceRef` of its own — it
    is a stampless container, filtered element-wise so the surviving candidate is not thrown
    out along with the one the licence forbids."""
    record = _record(
        conflicts=[
            {
                "field": "names.en",
                "candidates": [
                    _stamped("Rhodes", kind="overture", license_id="CDLA-Permissive-2.0"),
                    _stamped("Ρόδος", kind="open_web", license_id="CC0-1.0"),
                ],
                "resolution": "unresolved",
            }
        ]
    )
    good_candidate = record.conflicts[0].candidates[0]

    result = quarantine_record(record)

    kept_conflict = result.sites[0].conflicts[0]
    assert kept_conflict.candidates == (good_candidate,)
    assert result.withheld == (
        WithheldValue(
            site_id=record.id,
            field="conflicts[0].candidates[1]",
            reason="license_forbids_redistribution",
        ),
    )


# ── a never-bundleable structure (ReviewSummary) is withheld wholesale, no SourceRef needed ─


def test_reviews_are_always_withheld_review_providers_are_never_bundleable() -> None:
    """`ReviewSummary` carries no `SourceRef` at all — its schema pins its provenance to
    `review_provider`, which `NEVER_BUNDLEABLE_KINDS` refuses unconditionally, so there is a
    definite answer with nothing to derive it from."""
    record = _record(
        reviews={
            "ratings": [
                {"provider": "tripadvisor", "stars": 4.5, "count": 120, "url": "https://example/x"}
            ],
            "fetched_at": datetime(2026, 8, 8, tzinfo=UTC).isoformat(),
        }
    )

    result = quarantine_record(record)

    assert result.sites[0].reviews is None
    assert result.withheld == (
        WithheldValue(site_id=record.id, field="reviews", reason="license_forbids_redistribution"),
    )
