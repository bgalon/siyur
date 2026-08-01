"""Unit tests — the commons data spine (`commons/models.py`), T011 of Spec 001.

Ground truth is `docs/data/poi-site.md`; these tests assert the model *is* the card:
the `SourceRef.kind` enum, BCP-47 `names` keys, the `schema_ver` literal, and — the one
that matters most — that a **bare, unstamped value is unconstructible** (FR-003 / SC-002,
Constitution Article V). Empty `stories` is valid: slice 001 defers stories to 002 without
changing the schema.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from shapely import Point

from commons.models import (
    Bcp47Tag,
    FieldConflict,
    SiteRecordV1,
    SourcedValue,
    SourceRef,
    Story,
)

OVERTURE = SourceRef(
    kind="overture",
    id="08f394…gers",
    url=None,
    license="CDLA-Permissive-2.0",
    attribution=None,
)
OSM = SourceRef(
    kind="osm",
    id="node/123456",
    license="ODbL-1.0",
    attribution="© OpenStreetMap contributors",
)
OBSERVED = date(2026, 7, 22)


def _stamped(value: Any, source: SourceRef = OVERTURE) -> dict[str, Any]:
    """A minimal correctly-stamped `SourcedValue` payload (as adapters emit it)."""
    return {
        "value": value,
        "source": source.model_dump(),
        "bundleable": True,
        "confidence": 0.8,
        "observed_at": OBSERVED.isoformat(),
    }


def _record(**overrides: Any) -> SiteRecordV1:
    payload: dict[str, Any] = {
        "names": {"en": _stamped("Palace of the Grand Master")},
        "location": _stamped({"type": "Point", "coordinates": [28.2247, 36.4443]}),
        "categories": [_stamped("attraction.castle")],
    }
    payload.update(overrides)
    return SiteRecordV1.model_validate(payload)


# --- construction ----------------------------------------------------------------


def test_card_example_row_constructs_and_round_trips() -> None:
    """Example row 1 of the schema card, verbatim, must validate and survive a round trip."""
    row: dict[str, Any] = {
        "id": "6f1c0000-0000-4000-8000-00000000c0de",
        "gers_id": "08f394…gers",
        "schema_ver": "SiteRecordV1",
        "names": {
            "en": {
                "value": "Palace of the Grand Master",
                "source": {
                    "kind": "overture",
                    "id": "08f394…gers",
                    "url": None,
                    "license": "CDLA-Permissive-2.0",
                    "attribution": None,
                },
                "bundleable": True,
                "confidence": 0.82,
                "observed_at": "2026-07-22",
            }
        },
        "location": {
            "value": {"type": "Point", "coordinates": [28.2247, 36.4443]},
            "source": {"kind": "overture", "id": "08f394…gers", "license": "CDLA-Permissive-2.0"},
            "bundleable": True,
            "confidence": 0.9,
            "observed_at": "2026-07-22",
        },
        "categories": [
            {
                "value": "attraction.castle",
                "source": {
                    "kind": "overture",
                    "id": "08f394…gers",
                    "license": "CDLA-Permissive-2.0",
                },
                "bundleable": True,
                "confidence": 0.8,
                "observed_at": "2026-07-22",
            }
        ],
        "conflicts": [],
        "updated_at": "2026-07-22T09:00:00Z",
    }
    record = SiteRecordV1.model_validate(row)

    assert record.id == UUID("6f1c0000-0000-4000-8000-00000000c0de")
    assert record.names["en"].value == "Palace of the Grand Master"
    assert record.names["en"].source.license == "CDLA-Permissive-2.0"
    # EPSG:4326 (lon, lat) — longitude first, never swapped by the model.
    assert isinstance(record.location.value, Point)
    assert (record.location.value.x, record.location.value.y) == (28.2247, 36.4443)
    assert record.updated_at == datetime(2026, 7, 22, 9, 0, tzinfo=UTC)

    dumped = record.model_dump(mode="json")
    assert dumped["location"]["value"] == {"type": "Point", "coordinates": [28.2247, 36.4443]}
    assert SiteRecordV1.model_validate(dumped) == record


def test_defaults_are_server_generated_id_and_utc_timestamp() -> None:
    record = _record()
    assert isinstance(record.id, UUID)
    assert record.updated_at.tzinfo is not None


def test_naive_updated_at_is_refused() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _record(updated_at=datetime(2026, 7, 22, 9, 0))


def test_source_kind_enum_is_the_card_enum() -> None:
    for kind in (
        "overture",
        "osm",
        "wikivoyage",
        "wikipedia",
        "wikidata",
        "commons",
        "opening_hours_js",
        "review_provider",
        "open_web",
        "user",
    ):
        assert SourceRef(kind=kind, id="x", license="proprietary").kind == kind
    with pytest.raises(ValidationError):
        SourceRef.model_validate({"kind": "yelp", "id": "x", "license": "proprietary"})


# --- FR-003 / SC-002: an unstamped value is unconstructible ----------------------


@pytest.mark.parametrize(
    ("field", "bare"),
    [
        ("names", {"en": "Palace of the Grand Master"}),
        ("categories", ["attraction.castle"]),
        ("address", "Ippoton 1"),
        ("opening_hours", "Mo-Su 09:00-19:00"),
        ("notes", ["a bare note"]),
        ("location", {"type": "Point", "coordinates": [28.2247, 36.4443]}),
    ],
)
def test_bare_field_value_is_rejected(field: str, bare: Any) -> None:
    """A value without a stamp cannot enter a record — so it can never be shown."""
    with pytest.raises(ValidationError):
        _record(**{field: bare})


def test_sourced_value_without_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SourcedValue[str].model_validate(
            {"value": "x", "bundleable": False, "confidence": 0.5, "observed_at": OBSERVED}
        )
    with pytest.raises(ValidationError):
        SourcedValue[str].model_validate(
            {
                "value": "x",
                "source": None,
                "bundleable": False,
                "confidence": 0.5,
                "observed_at": OBSERVED,
            }
        )


def test_source_ref_needs_a_real_id_and_license() -> None:
    for broken in ({"kind": "osm", "id": "", "license": "ODbL-1.0"}, {"kind": "osm", "id": "n/1"}):
        with pytest.raises(ValidationError):
            SourceRef.model_validate(broken)


def test_model_construct_is_disabled() -> None:
    """pydantic's validation-bypassing constructor would make unstamped values possible."""
    with pytest.raises(TypeError, match="bypasses validation"):
        SourcedValue[str].model_construct(value="x")
    with pytest.raises(TypeError, match="bypasses validation"):
        SiteRecordV1.model_construct()


def test_records_are_frozen_so_a_stamp_cannot_be_stripped() -> None:
    record = _record()
    with pytest.raises(ValidationError):
        record.names["en"].source = None  # type: ignore[assignment]
    with pytest.raises(ValidationError):
        record.location.bundleable = True
    # sequence fields are tuples, so nothing unstamped can be appended post-validation
    assert isinstance(record.categories, tuple)
    with pytest.raises(AttributeError):
        record.categories.append("bare")  # type: ignore[attr-defined]


def test_model_copy_with_update_is_revalidated() -> None:
    record = _record()
    with pytest.raises(ValidationError):
        record.location.model_copy(update={"source": None})
    assert record.model_copy(update={"gers_id": "08f3aa…gers"}).gers_id == "08f3aa…gers"


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        _record(rating=4.6)


# --- names: BCP-47 subtags -------------------------------------------------------


@pytest.mark.parametrize("tag", ["en", "el", "he", "el-Latn", "ja-Hira", "ja-Latn", "pt-BR"])
def test_bcp47_name_keys_accepted(tag: Bcp47Tag) -> None:
    record = _record(names={tag: _stamped("Ρολόι", OSM)})
    assert tag in record.names


@pytest.mark.parametrize("tag", ["EN", "en_US", "english", "el-latn", "el-LATN", "", "en-"])
def test_non_bcp47_name_keys_rejected(tag: str) -> None:
    """Bare/mis-cased keys would silently fork a language into two entries."""
    with pytest.raises(ValidationError):
        _record(names={tag: _stamped("Ρολόι", OSM)})


def test_source_script_and_derived_latin_name_coexist() -> None:
    """ADR-0010: the derived `*-Latn` value never overwrites the source-script original."""
    record = _record(
        names={
            "el": _stamped("Ρολόι", OSM),
            "el-Latn": _stamped("Roloi", OSM),
        }
    )
    assert record.names["el"].value == "Ρολόι"
    assert record.names["el"].source.attribution == "© OpenStreetMap contributors"
    assert record.names["el-Latn"].value == "Roloi"


# --- schema_ver + empty-is-valid -------------------------------------------------


def test_schema_ver_is_the_literal() -> None:
    assert _record().schema_ver == "SiteRecordV1"
    with pytest.raises(ValidationError):
        _record(schema_ver="SiteRecordV2")


def test_empty_stories_is_valid() -> None:
    """Slice 001 defers stories to slice 002 (FR-011); the schema itself is unchanged."""
    assert _record().stories == ()
    assert _record(stories=[]).stories == ()


def test_stories_still_accept_a_cc_by_sa_story() -> None:
    story = Story(
        text_by_lang={"en": "The cobbled street once housed the …"},
        source=SourceRef(
            kind="wikivoyage",
            id="Rhodes",
            url="https://en.wikivoyage.org/wiki/Rhodes",
            license="CC-BY-SA-4.0",
            attribution="Wikivoyage: Rhodes (CC BY-SA 4.0)",
        ),
    )
    record = _record(stories=[story])
    assert record.stories[0].source.attribution is not None
    assert record.stories[0].claims == ()


def test_m2_plus_fields_default_empty() -> None:
    record = _record()
    assert record.phone is None
    assert record.price is None
    assert record.accessibility is None
    assert record.website is None
    assert record.reviews is None
    assert record.links == ()
    assert record.notes == ()
    assert record.conflicts == ()
    assert record.gers_id is None


# --- conflicts: no source is ever lost -------------------------------------------


def test_field_conflict_keeps_every_candidate_stamped() -> None:
    conflict = FieldConflict.model_validate(
        {
            "field": "names.en",
            "candidates": [_stamped("Palace of the Grand Master"), _stamped("Kastello", OSM)],
            "resolution": "unresolved",
        }
    )
    record = _record(conflicts=[conflict])
    assert {candidate.source.id for candidate in record.conflicts[0].candidates} == {
        OVERTURE.id,
        OSM.id,
    }


def test_field_conflict_candidates_must_be_stamped() -> None:
    with pytest.raises(ValidationError):
        FieldConflict.model_validate(
            {"field": "names.en", "candidates": ["Kastello"], "resolution": "unresolved"}
        )


@pytest.mark.parametrize("resolution", ["unresolved", "user-override", "picked:node/123456"])
def test_field_conflict_resolution_vocabulary(resolution: str) -> None:
    conflict = FieldConflict.model_validate(
        {"field": "address", "candidates": [_stamped("Ippoton 1")], "resolution": resolution}
    )
    assert conflict.resolution == resolution


def test_field_conflict_rejects_unknown_resolution() -> None:
    with pytest.raises(ValidationError, match="resolution must be"):
        FieldConflict.model_validate(
            {"field": "address", "candidates": [_stamped("Ippoton 1")], "resolution": "whatever"}
        )


def test_records_with_equal_content_compare_equal() -> None:
    shared = uuid4()
    stamp = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
    assert _record(id=shared, updated_at=stamp) == _record(id=shared, updated_at=stamp)
