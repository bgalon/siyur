"""Unit tests — the planned day and the frozen bundle (`commons/models.py`), T012 of Spec 002.

Ground truth is `docs/data/itinerary.md` · `route-leg.md` · `bundle-manifest.md` ·
`tile-source.md`; these tests assert the models *are* those cards. Three of them exist
because the slice has already shipped tests that could not fail for the reason they were
written (FAIL-007), so each assertion below names the implementation that would break it:

- **the two clocks** — pydantic already refuses a `datetime` in a `time` field, so *that*
  assertion proves nothing about our code. What it does accept is `time(10, 0, tzinfo=UTC)`
  and a bare `36000`, which it coerces to a **UTC-stamped** time. Those are the rows that
  test `_require_naive_local`;
- **the manifest digest** — asserting a hard-coded hex string only pins today's output. The
  tests here assert the properties the *TypeScript verifier* needs (key order, raw `©`,
  `28` not `28.0`, `integrity` absent) and include a **negative control** against the
  hand-rolled `json.dumps(sort_keys=True)` writer ADR-0025 A5 documents as the defect;
- **the derive-don't-read rule** — `RouteLegV1`/`Story` having *no* `bundleable` field is the
  contract, so the test asserts the field's absence, and fails the moment someone "fixes"
  the asymmetry by adding one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from typing import Any, Literal, get_args, get_origin
from uuid import UUID, uuid4

import pytest
import rfc8785
from pydantic import ValidationError
from shapely import LineString

from commons import licenses
from commons.models import (
    ArtifactRef,
    BundleManifestV1,
    ItineraryV1,
    RouteLegV1,
    Stop,
    Story,
    TileSourceV1,
    Timeline,
    TimelineEntry,
    _canonical_sha256,  # private on purpose: the canonicalization *is* the contract here
)

AREA_ID = UUID("3a110000-0000-4000-8000-000000000001")
SITE_A = UUID("6f1c0000-0000-4000-8000-00000000c0de")
SITE_B = UUID("c9d10000-0000-4000-8000-00000000beef")
ITINERARY_ID = UUID("7be20000-0000-4000-8000-000000000042")
USER_ID = "google-oauth2|1103"

# The routing stamp `route-leg.md` fixes for every M1 leg: routing over OSM is a Produced
# Work of OSM, and the engine is named inside `id` because no `SourceKind` for a routing
# engine exists (or is being added).
ROUTING_SOURCE: dict[str, Any] = {
    "kind": "osm",
    "id": "valhalla:pedestrian",
    "url": None,
    "license": "ODbL-1.0",
    "attribution": "© OpenStreetMap contributors",
}


# Full-length stand-ins for the cards' elided `"9f2c…"` digests, which are not constructible
# (a manifest hash is 64 lowercase hex characters, and that casing is part of the contract).
def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _leg(**overrides: Any) -> dict[str, Any]:
    leg: dict[str, Any] = {
        "id": "leg-0",
        "from_stop": 0,
        "to_stop": 1,
        "mode": "walk",
        "distance_m": 380.0,
        "duration_s": 300,
        "geometry": {
            "type": "LineString",
            "coordinates": [[28.2247, 36.4443], [28.2242, 36.4446], [28.2238, 36.4447]],
        },
        "source": ROUTING_SOURCE,
    }
    leg.update(overrides)
    return leg


def _itinerary_payload(**overrides: Any) -> dict[str, Any]:
    """`itinerary.md` example row 1, with the elided ids filled in."""
    payload: dict[str, Any] = {
        "id": str(ITINERARY_ID),
        "user_id": USER_ID,
        "area_id": str(AREA_ID),
        "date": "2026-07-25",
        "lang": "en",
        "schema_ver": "ItineraryV1",
        "budgets": {"walking_m": 4000.0, "hours": 4.0},
        "stops": [
            {"site_id": str(SITE_A), "order": 0, "planned_start": "10:00", "dwell_min": 60},
            {"site_id": str(SITE_B), "order": 1, "planned_start": "11:15", "dwell_min": 45},
        ],
        "legs": [_leg()],
        "timeline": {
            "entries": [
                {"stop_order": 0, "start": "10:00", "duration_min": 60},
                {"leg_id": "leg-0", "start": "11:00", "duration_min": 5},
                {"stop_order": 1, "start": "11:15", "duration_min": 45},
            ]
        },
    }
    payload.update(overrides)
    return payload


def _itinerary(**overrides: Any) -> ItineraryV1:
    return ItineraryV1.model_validate(_itinerary_payload(**overrides))


def _tile_source(**overrides: Any) -> dict[str, Any]:
    """`tile-source.md` example row 1 — the whole object the manifest embeds."""
    tiles: dict[str, Any] = {
        "path": "tiles/rhodes.pmtiles",
        "format": "pmtiles",
        "schema_ver": "TileSourceV1",
        "sha256": _digest("pmtiles"),
        "bbox": [28.216, 36.440, 28.232, 36.451],
        "minzoom": 0,
        "maxzoom": 15,
        "build_source": "protomaps-daily",
        "build_date": "2026-07-24",
        "tile_license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "style": {"path": "style/base.json", "sha256": _digest("style")},
        "glyphs": {"path": "glyphs/", "license": "OFL-1.1"},
    }
    tiles.update(overrides)
    return tiles


def _manifest_fields(**overrides: Any) -> dict[str, Any]:
    """`bundle-manifest.md` example row 1, with the elided digests filled in."""
    fields: dict[str, Any] = {
        "bundle_id": "bnd_01J",
        "itinerary_id": str(ITINERARY_ID),
        "created_at": "2026-07-25T12:30:00Z",
        "size_bytes": 5242880,
        "schema_ver": "BundleManifestV1",
        "tiles": {"pmtiles": _tile_source()},
        "routing": {
            "walk_graph": "routing/walk_graph.geojson",
            "walk_graph_sha256": _digest("walk_graph"),
            "legs": "routing/legs.json",
            "legs_sha256": _digest("legs"),
        },
        "content": {
            "sites": "content/sites.json",
            "sites_sha256": _digest("sites"),
            "narrations": "content/narrations.json",
            "narrations_sha256": _digest("narrations"),
            "itinerary": "content/itinerary.json",
            "itinerary_sha256": _digest("itinerary"),
        },
        "attribution": {"path": "ATTRIBUTION.md", "sha256": _digest("attribution")},
        "textLicense": "CC-BY-SA-4.0",
        "withheld": [
            {
                "site_id": str(SITE_B),
                "field": "reviews",
                "reason": "license_forbids_redistribution",
            }
        ],
    }
    fields.update(overrides)
    return fields


def _manifest(**overrides: Any) -> BundleManifestV1:
    return BundleManifestV1.seal(**_manifest_fields(**overrides))


# --- construction: the cards' example rows ---------------------------------------


def test_itinerary_card_example_constructs_and_round_trips() -> None:
    itinerary = _itinerary()

    assert itinerary.date == date(2026, 7, 25)
    assert itinerary.stops[1].planned_start == time(11, 15)
    assert itinerary.legs[0].source.attribution == "© OpenStreetMap contributors"
    # EPSG:4326 (lon, lat) — longitude first, and the model never reorders it.
    assert itinerary.legs[0].geometry.coords[0] == (28.2247, 36.4443)

    dumped = itinerary.model_dump(mode="json")
    assert dumped["legs"][0]["geometry"] == {
        "type": "LineString",
        "coordinates": [[28.2247, 36.4443], [28.2242, 36.4446], [28.2238, 36.4447]],
    }
    assert ItineraryV1.model_validate(dumped) == itinerary


def test_manifest_card_example_constructs_and_round_trips() -> None:
    manifest = _manifest()

    assert manifest.created_at == datetime(2026, 7, 25, 12, 30, tzinfo=UTC)
    assert manifest.tiles.pmtiles.bbox == (28.216, 36.440, 28.232, 36.451)
    assert manifest.withheld[0].reason == "license_forbids_redistribution"

    dumped = manifest.model_dump(mode="json")
    assert BundleManifestV1.model_validate(dumped) == manifest


@pytest.mark.parametrize(
    ("model", "literal"),
    [
        (ItineraryV1, "ItineraryV1"),
        (RouteLegV1, "RouteLegV1"),
        (TileSourceV1, "TileSourceV1"),
        (BundleManifestV1, "BundleManifestV1"),
    ],
)
def test_schema_ver_is_the_literal(model: type[Any], literal: str) -> None:
    field = model.model_fields["schema_ver"]
    assert field.default == literal
    # The annotation, not just the default: `schema_ver: str = "ItineraryV1"` would satisfy
    # the line above while accepting any version string a caller invents.
    assert get_origin(field.annotation) is Literal
    assert get_args(field.annotation) == (literal,)


def test_a_v2_schema_ver_is_refused() -> None:
    with pytest.raises(ValidationError):
        _itinerary(schema_ver="ItineraryV2")
    with pytest.raises(ValidationError):
        BundleManifestV1.seal(**_manifest_fields(schema_ver="BundleManifestV2"))


# --- ItineraryV1.date: M1 and required -------------------------------------------


def test_itinerary_without_a_date_is_unconstructible() -> None:
    """Without `date`, `planned_start = 10:00` is not an instant and no opening-hours rule —
    weekday, PH, seasonal — can be evaluated (ADR-0025 ruling 2). A default would be a lie."""
    payload = _itinerary_payload()
    del payload["date"]
    with pytest.raises(ValidationError, match="date"):
        ItineraryV1.model_validate(payload)


def test_itinerary_date_is_a_calendar_date_not_an_instant() -> None:
    """The date-line bug, pinned.

    An earlier version of this test passed `"2026-07-25T10:00:00Z"` — which raises because
    the **time component is non-zero**, not because of the offset. It therefore read as
    covering this boundary while never touching it. pydantic's lax mode accepts a
    `datetime` for a `date` field whenever the time is midnight, keeping that instant's own
    calendar day: the common `datetime.now(UTC).replace(hour=0, ...)` idiom stamps a plan
    **2026-07-25** for a traveller in Auckland (UTC+13) walking it on the **26th**, and
    every opening-hours evaluation then runs against the wrong weekday. The midnight cases
    below are the ones that matter.
    """
    assert _itinerary().model_dump(mode="json")["date"] == "2026-07-25"

    seeded_today = datetime(2026, 7, 25, 21, 0, tzinfo=UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    for instant in (
        seeded_today,  # tz-aware midnight — the real-world idiom, and the real bug
        "2026-07-26T00:00:00Z",  # midnight-Z as a string
        "2026-07-25T10:00:00Z",  # non-zero time; refused for a different reason
    ):
        with pytest.raises(ValidationError):
            _itinerary(date=instant)

    # A plain calendar date, in either form, is what the field is for.
    assert _itinerary(date="2026-07-26").date == date(2026, 7, 26)
    assert _itinerary(date=date(2026, 7, 26)).date == date(2026, 7, 26)


# --- the two clocks: naive area-local time vs tz-aware UTC datetime --------------


def _stop(**overrides: Any) -> Stop:
    payload: dict[str, Any] = {
        "site_id": SITE_A,
        "order": 0,
        "planned_start": time(10, 0),
        "dwell_min": 60,
    }
    payload.update(overrides)
    return Stop.model_validate(payload)


@pytest.mark.parametrize(
    ("label", "value"),
    [
        # pydantic refuses these two outright — they document the type boundary.
        ("tz-aware datetime", datetime(2026, 7, 25, 10, 0, tzinfo=UTC)),
        ("naive datetime", datetime(2026, 7, 25, 10, 0)),
        # ...and these three it would happily accept. `time(tzinfo=UTC)` and the integer
        # (which pydantic coerces to `time(10, 0, tzinfo=UTC)`) are what `_require_naive_local`
        # is for: a UTC instant seated in a wall-clock slot, compared against opening hours in
        # the area's frame, wrong by the UTC offset and silent about it.
        ("tz-aware time", time(10, 0, tzinfo=UTC)),
        ("offset time string", "10:00+02:00"),
        ("seconds since midnight", 36000),
    ],
)
def test_planned_start_refuses_anything_carrying_a_utc_frame(label: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        _stop(planned_start=value)


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 7, 25, 10, 0, tzinfo=UTC), time(10, 0, tzinfo=UTC), 36000],
)
def test_timeline_start_refuses_anything_carrying_a_utc_frame(value: Any) -> None:
    with pytest.raises(ValidationError):
        TimelineEntry.model_validate({"stop_order": 0, "start": value, "duration_min": 60})


@pytest.mark.parametrize("value", [time(10, 0), "2026-07-25", "not-a-time"])
def test_manifest_created_at_refuses_a_wall_clock(value: Any) -> None:
    """The audit timestamp is UTC; a wall-clock value in it is the same swap, reversed."""
    with pytest.raises(ValidationError):
        BundleManifestV1.seal(**_manifest_fields(created_at=value))


def test_manifest_created_at_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        BundleManifestV1.seal(**_manifest_fields(created_at=datetime(2026, 7, 25, 12, 30)))


def test_planned_times_serialize_without_an_offset() -> None:
    """What the offline UI reads must *be* a wall clock: a `Z` or `+02:00` here would be
    parsed as an instant by the traveller's device and re-rendered in device time."""
    dumped = _itinerary().model_dump(mode="json")
    starts = [stop["planned_start"] for stop in dumped["stops"]]
    starts += [entry["start"] for entry in dumped["timeline"]["entries"]]
    assert starts == ["10:00:00", "11:15:00", "10:00:00", "11:00:00", "11:15:00"]
    assert all("Z" not in start and "+" not in start for start in starts)


# --- timeline addressing: stop_order (int) and leg_id (str), never a site UUID ----


def test_a_stop_has_no_id_of_its_own() -> None:
    """`stop_order` is the address; a `Stop.id` would immediately give the day two of them."""
    assert "id" not in Stop.model_fields
    with pytest.raises(ValidationError):
        _stop(id=uuid4())


def test_a_site_uuid_is_not_a_stop_address() -> None:
    """The bug ADR-0025 ruling 4 closed: the card's old example passed a *site* UUID as the
    stop reference, which cannot disambiguate a day that visits one site twice."""
    with pytest.raises(ValidationError):
        TimelineEntry.model_validate(
            {"stop_order": str(SITE_A), "start": "10:00", "duration_min": 60}
        )


@pytest.mark.parametrize(
    "entry",
    [
        {"start": "10:00", "duration_min": 60},
        {"stop_order": 0, "leg_id": "leg-0", "start": "10:00", "duration_min": 60},
    ],
    ids=["neither", "both"],
)
def test_timeline_entry_addresses_exactly_one_thing(entry: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        TimelineEntry.model_validate(entry)


def test_timeline_may_not_reference_a_stop_or_leg_that_is_not_there() -> None:
    """A dangling reference renders as a blank block **offline**, where nothing can recover."""
    with pytest.raises(ValidationError, match="stop_order=7"):
        _itinerary(timeline={"entries": [{"stop_order": 7, "start": "10:00", "duration_min": 60}]})
    with pytest.raises(ValidationError, match="leg_id='leg-9'"):
        _itinerary(timeline={"entries": [{"leg_id": "leg-9", "start": "10:00", "duration_min": 5}]})


def test_stop_orders_are_positions_so_they_must_be_dense_and_ascending() -> None:
    stops = _itinerary_payload()["stops"]
    with pytest.raises(ValidationError, match="stops\\[1\\] has order=2"):
        _itinerary(
            stops=[stops[0], {**stops[1], "order": 2}],
            legs=[_leg(to_stop=2)],
            timeline={"entries": []},
        )


def test_a_leg_may_not_dangle_or_repeat_its_id() -> None:
    with pytest.raises(ValidationError, match="to_stop=5"):
        _itinerary(legs=[_leg(to_stop=5)], timeline={"entries": []})
    with pytest.raises(ValidationError, match="not unique"):
        _itinerary(legs=[_leg(), _leg()], timeline={"entries": []})
    with pytest.raises(ValidationError, match="starts and ends"):
        _itinerary(legs=[_leg(to_stop=0)], timeline={"entries": []})


# --- M2+ slots exist and stay empty ----------------------------------------------


def test_m2_slots_default_empty() -> None:
    itinerary = _itinerary()
    assert itinerary.meals == ()
    assert itinerary.variants == {}
    assert itinerary.legs[0].variant is None
    assert _manifest().schematic is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("meals", [{"kind": "meal", "window": {"start": "13:00", "end": "14:00"}}]),
        ("variants", {"B": {"trigger": "site_closed", "changes": [], "legs": []}}),
    ],
)
def test_m2_slots_refuse_a_fill(field: str, value: Any) -> None:
    """`Anchor`/`PlanVariant`/`StopEdit` are not models in this slice, so anything put here
    is unvalidated — and would be frozen verbatim into `content.itinerary`."""
    with pytest.raises(ValidationError, match="M2\\+"):
        _itinerary(**{field: value})


# --- the spine's guarantees, inherited not re-implemented ------------------------


@pytest.mark.parametrize("model", [ItineraryV1, RouteLegV1, BundleManifestV1, Stop, Timeline])
def test_new_models_inherit_the_stamped_model_guarantees(model: type[Any]) -> None:
    assert model.model_config["frozen"] is True
    assert model.model_config["extra"] == "forbid"
    with pytest.raises(TypeError, match="bypasses validation"):
        model.model_construct()


def test_a_leg_without_a_source_is_unconstructible() -> None:
    payload = _leg()
    del payload["source"]
    with pytest.raises(ValidationError):
        RouteLegV1.model_validate(payload)


def test_models_are_frozen_and_closed() -> None:
    itinerary = _itinerary()
    with pytest.raises(ValidationError):
        itinerary.stops[0].dwell_min = 90
    with pytest.raises(ValidationError):
        _itinerary(rating=4.6)


# --- derive-don't-read: RouteLegV1 and Story carry a bare SourceRef --------------


@pytest.mark.parametrize("model", [RouteLegV1, Story])
def test_bare_sourceref_structures_have_no_bundleable_field(model: type[Any]) -> None:
    """ADR-0025 ruling 8. Quarantine must *derive* for these; a `bundleable` field here would
    make `getattr(v, "bundleable", False)` look correct — and that repair drops every walking
    leg from every bundle, silently, past every hash and path check."""
    assert "bundleable" not in model.model_fields
    assert "confidence" not in model.model_fields


def test_a_leg_may_not_be_author_stamped_bundleable() -> None:
    with pytest.raises(ValidationError):
        RouteLegV1.model_validate(_leg(bundleable=True))


def test_the_routing_stamp_derives_to_bundleable() -> None:
    """What makes a leg bundleable is the produced-work chain, not a field: routing over OSM
    is ODbL, and `licenses.bundleable("osm", "ODbL-1.0")` is what lets it into the bundle.

    **What this does NOT assert, deliberately made visible.** An earlier version read back
    `kind == "osm"` and `id == "valhalla:pedestrian"` from the literals the fixture had just
    supplied — no assertion touched `commons/models.py` at all. `route-leg.md` says "a leg
    with any other kind/license pair is a defect, not a variation", and **the model does not
    enforce that**: stamping is deferred to `commons/routing.py` (T016), which does not exist
    yet. The negative case below records the gap rather than papering over it — it asserts
    the *current* permissiveness, so whoever adds the convention check to the model will see
    this test go red and can delete it deliberately.
    """
    leg = RouteLegV1.model_validate(_leg())
    assert licenses.bundleable(leg.source.kind, leg.source.license) is True

    mis_stamped = _leg()
    mis_stamped["source"] = {
        **mis_stamped["source"],
        "kind": "open_web",
        "license": "proprietary",
        "attribution": None,
    }
    bad = RouteLegV1.model_validate(mis_stamped)
    assert licenses.bundleable(bad.source.kind, bad.source.license) is False, (
        "quarantine still derives correctly for a mis-stamped leg — so a wrong stamp costs "
        "the traveller every route while every hash and path check passes (T016/T038)"
    )


# --- Story.observed_at (T013) ----------------------------------------------------


def _story_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "text_by_lang": {"en": "The cobbled street once housed the …"},
        "source": {
            "kind": "wikivoyage",
            "id": "en:Rhodes",
            "url": "https://en.wikivoyage.org/wiki/Rhodes?oldid=4812301",
            "license": "CC-BY-SA-4.0",
            "attribution": '"Rhodes", Wikivoyage — authors via page history',
        },
        "observed_at": "2026-07-25",
    }
    payload.update(overrides)
    return payload


def test_a_story_must_carry_the_date_its_revision_was_fetched() -> None:
    """Bundled narration inherits no `SourcedValue` stamp, so without this it has no
    staleness key at all — the gap ADR-0025 ruling 8 closed."""
    assert Story.model_validate(_story_payload()).observed_at == date(2026, 7, 25)
    payload = _story_payload()
    del payload["observed_at"]
    with pytest.raises(ValidationError, match="observed_at"):
        Story.model_validate(payload)


def test_story_claims_stay_empty_in_m1() -> None:
    assert Story.model_validate(_story_payload()).claims == ()


# --- geometry: EPSG:4326, lon first, shapely 2.x -------------------------------


def test_leg_geometry_accepts_the_three_shapes_a_route_arrives_as() -> None:
    from_shapely = RouteLegV1.model_validate(
        _leg(geometry=LineString([(28.2247, 36.4443), (28.2238, 36.4447)]))
    )
    from_pairs = RouteLegV1.model_validate(_leg(geometry=[(28.2247, 36.4443), (28.2238, 36.4447)]))
    assert from_shapely.geometry == from_pairs.geometry
    assert from_shapely.geometry.geom_type == "LineString"


@pytest.mark.parametrize(
    ("label", "geometry"),
    [
        # A swapped pair whose "latitude" is out of range — the axis-order tripwire.
        ("swapped axes", {"type": "LineString", "coordinates": [[36.44, 128.22], [36.45, 128.23]]}),
        ("one vertex", {"type": "LineString", "coordinates": [[28.2247, 36.4443]]}),
        (
            "3-D vertex",
            {"type": "LineString", "coordinates": [[28.22, 36.44, 12.0], [28.23, 36.45]]},
        ),
        ("a point", {"type": "Point", "coordinates": [28.2247, 36.4443]}),
        ("no geometry", None),
    ],
)
def test_leg_geometry_refuses_what_is_not_a_wgs84_route(label: str, geometry: Any) -> None:
    with pytest.raises(ValidationError):
        RouteLegV1.model_validate(_leg(geometry=geometry))


# --- the manifest: seven per-artifact hashes, one per path -----------------------


SEVEN_HASHES = [
    ("tiles", "pmtiles", "sha256"),
    ("routing", "walk_graph_sha256"),
    ("routing", "legs_sha256"),
    ("content", "sites_sha256"),
    ("content", "narrations_sha256"),
    ("content", "itinerary_sha256"),
    ("attribution", "sha256"),
]


@pytest.mark.parametrize("path", SEVEN_HASHES, ids=[".".join(p) for p in SEVEN_HASHES])
def test_every_artifact_carries_its_own_hash(path: tuple[str, ...]) -> None:
    """One hash per artifact — a group hash cannot say *which* file corrupted (FR-013)."""
    node: Any = _manifest().model_dump(mode="json")
    for key in path:
        node = node[key]
    assert isinstance(node, str) and len(node) == 64


def test_the_frozen_itinerary_has_a_path_of_its_own() -> None:
    """ADR-0025 ruling 1: without it the traveller's day exists only as a database row they
    cannot reach in airplane mode."""
    assert _manifest().content.itinerary == "content/itinerary.json"


def test_manifest_embeds_the_whole_tile_source_not_a_four_field_summary() -> None:
    """ADR-0025 ruling 7. `attribution` and `build_date` discharge ODbL and drive freshness;
    a manifest that held only the required subset could not."""
    summary = {
        "path": "tiles/a.pmtiles",
        "sha256": _digest("x"),
        "bbox": [0, 0, 1, 1],
        "maxzoom": 15,
    }
    with pytest.raises(ValidationError):
        BundleManifestV1.seal(**_manifest_fields(tiles={"pmtiles": summary}))
    pmtiles = _manifest().tiles.pmtiles
    assert (pmtiles.attribution, pmtiles.build_date, pmtiles.tile_license) == (
        "© OpenStreetMap contributors",
        date(2026, 7, 24),
        "ODbL-1.0",
    )


def test_the_bundled_text_license_is_machine_readable_under_the_cards_spelling() -> None:
    """`textLicense`, camelCase: a reuser must not have to parse ATTRIBUTION.md prose to
    learn that the narration is share-alike, and the TS verifier reads this exact key."""
    dumped = _manifest().model_dump(mode="json")
    assert dumped["textLicense"] == "CC-BY-SA-4.0"
    assert "text_license" not in dumped
    # Null is the correct value when no story was bundled — not an omission.
    assert BundleManifestV1.seal(**_manifest_fields(textLicense=None)).textLicense is None


@pytest.mark.parametrize("reason", ["license_forbids_redistribution", "source_unavailable"])
def test_withheld_reason_accepts_the_closed_set(reason: str) -> None:
    manifest = _manifest(withheld=[{"site_id": str(SITE_B), "field": "notes[2]", "reason": reason}])
    assert manifest.withheld[0].reason == reason


@pytest.mark.parametrize(
    "reason",
    [
        # Free text is refused because `withheld` ships inside a file the user downloads.
        "license 'proprietary' is not on the bundleable allowlist",
        # `unstamped` is deliberately NOT a member: FR-012 refuses unstamped input and the
        # compile fails, so a value is withheld *or* refused, never both.
        "unstamped",
    ],
)
def test_withheld_reason_refuses_anything_outside_it(reason: str) -> None:
    with pytest.raises(ValidationError):
        _manifest(withheld=[{"site_id": str(SITE_B), "field": "reviews", "reason": reason}])


@pytest.mark.parametrize("bad", ["9F2C" + "0" * 60, "9f2c", "", "zz" + "0" * 62])
def test_hashes_must_be_64_lowercase_hex(bad: str) -> None:
    """The verifier compares these as strings; casing is contract, not preference."""
    with pytest.raises(ValidationError):
        ArtifactRef.model_validate({"path": "ATTRIBUTION.md", "sha256": bad})


@pytest.mark.parametrize("path", ["/etc/passwd", "../../secrets/key", "tiles/../../x", ""])
def test_bundle_paths_may_not_escape_the_bundle(path: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactRef.model_validate({"path": path, "sha256": _digest("x")})


# --- bbox: EPSG:4326, and generic enough for an antimeridian area ----------------


def test_bbox_rejects_out_of_range_and_inverted_latitudes() -> None:
    with pytest.raises(ValidationError):
        TileSourceV1.model_validate(_tile_source(bbox=[28.216, 96.0, 28.232, 97.0]))
    with pytest.raises(ValidationError, match="inverted"):
        TileSourceV1.model_validate(_tile_source(bbox=[28.216, 36.451, 28.232, 36.440]))


def test_bbox_allows_an_antimeridian_crossing_area() -> None:
    """RFC 7946 §5.2: a bbox crossing 180° has minLon > maxLon. Ordering longitudes would
    quietly exclude Fiji and Chukotka — and SC-009 says Siyur works for *any* area."""
    tiles = TileSourceV1.model_validate(_tile_source(bbox=[177.0, -18.5, -179.5, -17.5]))
    assert tiles.bbox == (177.0, -18.5, -179.5, -17.5)


# --- integrity: RFC 8785, `integrity` removed, and the digest that must not lie ---


def test_seal_produces_a_manifest_that_verifies_itself() -> None:
    manifest = _manifest()
    assert manifest.verify_manifest_sha256() is True
    assert manifest.integrity.manifest_sha256 == manifest.computed_manifest_sha256()


def test_a_tampered_manifest_fails_its_own_check() -> None:
    """FR-020: this is what makes a corrupted/evicted bundle report unusable rather than
    render a partial day."""
    sealed = _manifest()
    tampered = sealed.model_copy(update={"size_bytes": sealed.size_bytes + 1})
    assert tampered.verify_manifest_sha256() is False


def test_the_digest_changes_when_any_covered_value_changes() -> None:
    """A digest over a constant, or over an empty payload, would pass every test above."""
    baseline = _manifest().computed_manifest_sha256()
    assert _manifest(size_bytes=5242881).computed_manifest_sha256() != baseline
    assert _manifest(bundle_id="bnd_other").computed_manifest_sha256() != baseline
    assert (
        _manifest(
            withheld=[{"site_id": str(SITE_B), "field": "reviews", "reason": "source_unavailable"}]
        ).computed_manifest_sha256()
        != baseline
    )


def test_the_integrity_key_is_removed_before_hashing_not_nulled() -> None:
    """ "Removed, not nulled, not emptied" is load-bearing: JCS serializes a present `null`,
    so a nulling writer and an omitting verifier are each self-consistent and disagree."""
    payload = _manifest().canonical_payload()
    assert "integrity" not in payload
    # ...and therefore the recorded digest cannot influence the computed one.
    sealed = _manifest()
    relabelled = sealed.model_copy(update={"integrity": {"manifest_sha256": _digest("other")}})
    assert relabelled.computed_manifest_sha256() == sealed.computed_manifest_sha256()
    assert relabelled.verify_manifest_sha256() is False


def test_the_digest_is_stable_under_key_reordering() -> None:
    """The property the TS verifier needs: it rebuilds the object from parsed JSON, whose key
    order is whatever the parser produced. Hashing insertion order would fail here."""
    payload = _manifest().canonical_payload()
    shuffled = dict(reversed(list(payload.items())))
    assert list(shuffled) != list(payload)
    assert _canonical_sha256(shuffled) == _manifest().computed_manifest_sha256()


def test_the_digest_is_not_the_hand_rolled_sorted_keys_digest() -> None:
    """The negative control for ADR-0025 A5 — the documented defect, run against a manifest.

    `json.dumps(sort_keys=True)` pins key order and nothing else. The **escaping** divergence
    fires on every manifest, because every one carries `©` in its ODbL attribution. The
    **number** divergence fires only when a `bbox` bound happens to be integral — pydantic
    coerces `28` to `28.0`, Python writes `28.0`, JS and JCS write `28` — so this test pins a
    bbox that has one. That intermittency is the argument, not a caveat: a hand-rolled writer
    verifies fine on most areas and fails on some, offline, on a device.
    """
    manifest = _manifest(tiles={"pmtiles": _tile_source(bbox=[28.0, 36.0, 28.5, 36.5])})
    payload = manifest.canonical_payload()

    hand_rolled = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert "\\u00a9" in hand_rolled
    assert '"bbox":[28.0,36.0,28.5,36.5]' in hand_rolled

    canonical = rfc8785.dumps(payload).decode()
    assert "©" in canonical and "\\u00a9" not in canonical
    assert '"bbox":[28,36,28.5,36.5]' in canonical

    naive_digest = hashlib.sha256(hand_rolled.encode()).hexdigest()
    assert manifest.computed_manifest_sha256() != naive_digest

    # And the escaping divergence alone is enough: same test, ordinary non-integral bbox.
    ordinary = _manifest()
    ordinary_hand_rolled = json.dumps(
        ordinary.canonical_payload(), sort_keys=True, separators=(",", ":")
    )
    assert "\\u00a9" in ordinary_hand_rolled
    assert (
        ordinary.computed_manifest_sha256()
        != hashlib.sha256(ordinary_hand_rolled.encode()).hexdigest()
    )


def test_itinerary_hash_uses_the_same_canonicalization() -> None:
    """`user_plan.itinerary_hash` is what an approval is *of* (ADR-0023); A5 pins it to the
    same standard, and it has the same two failure modes."""
    itinerary = _itinerary()
    assert itinerary.canonical_sha256() == _canonical_sha256(itinerary.model_dump(mode="json"))
    assert itinerary.canonical_sha256() == _itinerary().canonical_sha256()
    assert _itinerary(user_id="google-oauth2|9").canonical_sha256() != itinerary.canonical_sha256()
