"""Unit tests — `compiler/pipeline.py` (T042, Spec 002).

Ground truth: `specs/002-plan-compile-offline/contracts/bundles.md` (the frame sequence and
every payload key), `docs/data/bundle-manifest.md` (the seven hashes, the closed `withheld`
enum) and the pipeline's own module docstring.

Tier 1 throughout: **no network, no container, no real GCS, no `pmtiles` binary**. The tile
stage's three seams take the committed `FixtureExtractor` plus a local asset source, the
walking network is a handful of `LineString`s, and the object store is a dict. Every stage
therefore runs for real — this exercises the actual compile, not a mock of it.

Per FAIL-007, each guard is proved to **bite**: unstamped input, a leg the registry refuses, a
stop whose site cannot be redistributed, a disconnected walk graph and a store that fails
mid-publish each get a test with deliberately bad input asserting the compile fails *and*
that nothing readable is left behind. A guard that has only ever been shown to pass on good
input has not been tested.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from shapely import LineString

from commons import licenses
from commons.frame import LocalFrame
from commons.models import BundleManifestV1, ItineraryV1, SiteRecordV1
from commons.routing import routing_source
from compiler.manifest import ITINERARY_PATH, Artifact, read_frozen_itinerary
from compiler.pipeline import (
    COMPILE_PHASES,
    MANIFEST_PATH,
    NARRATIONS_PATH,
    SITES_PATH,
    CompileRequest,
    Event,
    UnbundleableStopError,
    UnknownSiteError,
    in_process_compile_enabled,
    run_compile,
)
from compiler.quarantine import UnbundleableLegError, UnstampedValueError
from compiler.routes import LEGS_PATH, WALK_GRAPH_PATH, Bbox, DisconnectedWalkGraphError
from compiler.tiles import ARCHIVE_DIR, FixtureExtractor, GlyphRange, ProtomapsBuild

BUNDLE_ID = "bnd_01JTEST"
ITINERARY_ID = UUID("7be20000-0000-4000-8000-000000000042")
AREA_ID = UUID("3a110000-0000-4000-8000-000000000001")
PALACE_ID = UUID("6f1c0000-0000-4000-8000-000000000001")
STREET_ID = UUID("6f1c0000-0000-4000-8000-000000000002")
USER_ID = "google-oauth2|1103"
OBSERVED = date(2026, 8, 8)
FRAME = LocalFrame(timezone="Europe/Athens", country_code="GR")
CREATED_AT = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)

#: Two places ~140 m apart. Coordinates are EPSG:4326 (lon, lat) and are the *only* geometry
#: these tests state; every distance, bbox and edge below is computed from them by shapely.
PALACE = (28.2247, 36.4443)
STREET = (28.2260, 36.4450)

#: A marker that must appear **nowhere** in a published bundle. It rides on a value stamped
#: `open_web`, which `commons/licenses.py` refuses unconditionally — the merge-blocking
#: FR-011/SC-004 invariant, asserted by scanning every published byte for this string.
UNBUNDLEABLE_MARKER = "UNBUNDLEABLE-c9d1-do-not-ship"
REVIEW_MARKER = "https://reviews.example/UNBUNDLEABLE-review-c9d1"

BUILD = ProtomapsBuild(url="https://build.example/20260813.pmtiles", build_date=date(2026, 8, 13))
STYLE_PATH = "style/base.json"
STYLE_BYTES = b'{"version":8,"layers":[]}'
ARCHIVE_BYTES = b"PMTiles\x03" + b"fixture-archive-bytes" * 8


# --- fixtures: the day, its records, and the three injected seams -------------------------


def _stamped(value: Any, *, kind: str, license_id: str, id_: str = "src") -> dict[str, Any]:
    """A `SourcedValue` payload with `bundleable` **derived**, exactly as an adapter mints one."""
    return {
        "value": value,
        "source": {
            "kind": kind,
            "id": id_,
            "license": license_id,
            "attribution": "Overture Maps" if kind == "overture" else None,
        },
        "bundleable": licenses.bundleable(kind, license_id),
        "confidence": 0.9,
        "observed_at": OBSERVED.isoformat(),
    }


def _point(coordinates: tuple[float, float], *, kind: str = "overture") -> dict[str, Any]:
    return _stamped(
        {"type": "Point", "coordinates": list(coordinates)},
        kind=kind,
        license_id="CDLA-Permissive-2.0" if kind == "overture" else "CC0-1.0",
    )


def _story(article: str) -> dict[str, Any]:
    return {
        "text_by_lang": {"en": f"A short adapted paragraph about {article}."},
        "source": {
            "kind": "wikivoyage",
            "id": f"en:{article}",
            "url": f"https://en.wikivoyage.org/w/index.php?title={article}&oldid=44012",
            "license": "CC-BY-SA-4.0",
            "attribution": f"Wikivoyage contributors, “{article}”, CC BY-SA 4.0",
        },
        "observed_at": OBSERVED.isoformat(),
    }


def _palace(**overrides: Any) -> SiteRecordV1:
    """The first stop: a bundleable core, one story, and two values quarantine must remove."""
    payload: dict[str, Any] = {
        "id": str(PALACE_ID),
        "location": _point(PALACE),
        "names": {
            "en": _stamped(
                "Palace of the Grand Master", kind="overture", license_id="CDLA-Permissive-2.0"
            )
        },
        "stories": [_story("Rhodes")],
        "notes": [_stamped(UNBUNDLEABLE_MARKER, kind="open_web", license_id="CC0-1.0")],
        "reviews": {
            "ratings": [{"provider": "example", "stars": 4.5, "count": 12, "url": REVIEW_MARKER}],
            "fetched_at": "2026-08-08T00:00:00Z",
        },
    }
    payload.update(overrides)
    return SiteRecordV1.model_validate(payload)


def _street(**overrides: Any) -> SiteRecordV1:
    payload: dict[str, Any] = {
        "id": str(STREET_ID),
        "location": _point(STREET),
        "names": {
            "en": _stamped(
                "Street of the Knights", kind="overture", license_id="CDLA-Permissive-2.0"
            )
        },
    }
    payload.update(overrides)
    return SiteRecordV1.model_validate(payload)


def _itinerary(**overrides: Any) -> ItineraryV1:
    payload: dict[str, Any] = {
        "id": str(ITINERARY_ID),
        "user_id": USER_ID,
        "area_id": str(AREA_ID),
        "date": "2026-07-25",
        "lang": "en",
        "budgets": {"walking_m": 4000.0, "hours": 6.0},
        "stops": [
            {
                "site_id": str(PALACE_ID),
                "order": 0,
                "planned_start": "10:00",
                "dwell_min": 60,
            },
            {
                "site_id": str(STREET_ID),
                "order": 1,
                "planned_start": "11:15",
                "dwell_min": 30,
            },
        ],
        "legs": [
            {
                "id": "leg-0",
                "from_stop": 0,
                "to_stop": 1,
                "geometry": {"type": "LineString", "coordinates": [list(PALACE), list(STREET)]},
                "distance_m": 148.0,
                "duration_s": 130,
                "source": routing_source().model_dump(),
            }
        ],
    }
    payload.update(overrides)
    return ItineraryV1.model_validate(payload)


#: A single walking way running past both stops — one component, both anchors on it.
CONNECTED_LINES = (
    LineString([(28.2245, 36.4442), (28.2251, 36.4445), (28.2258, 36.4449), (28.2263, 36.4452)]),
)

#: Two ways that never meet, one by each stop. Each anchor snaps to a *different* component,
#: which is the shape `connected: false` exists to catch (plan.md risk 3).
ISLAND_LINES = (
    LineString([(28.2245, 36.4442), (28.2249, 36.4444)]),
    LineString([(28.2258, 36.4449), (28.2263, 36.4452)]),
)


@dataclass(frozen=True, slots=True)
class _Network:
    """A `WalkNetworkSource` over committed linework — no Overpass, no OSMnx."""

    lines: tuple[LineString, ...]

    def walk_lines(self, bbox: Bbox) -> tuple[LineString, ...]:
        return self.lines


@dataclass(frozen=True, slots=True)
class _Assets:
    """An `AssetSource` that always answers, so glyph coverage is never the thing under test."""

    def glyph(self, fontstack: str, glyph_range: GlyphRange) -> bytes | None:
        return f"glyph:{fontstack}:{glyph_range.label}".encode()

    def sprite(self, asset: str) -> bytes | None:
        return f"sprite:{asset}".encode()


@dataclass
class _Store:
    """An in-memory `BundleObjectStore`. `fail_on` makes one `put` fail, mid-publish."""

    objects: dict[str, bytes] = field(default_factory=dict)
    fail_on: str | None = None
    delete_calls: int = 0

    def put(self, bundle_id: str, path: str, data: bytes) -> object:
        if path == self.fail_on:
            raise RuntimeError(f"object store refused {path}")
        self.objects[f"{bundle_id}/{path}"] = data
        return None

    def delete_bundle(self, bundle_id: str) -> int:
        self.delete_calls += 1
        prefix = f"{bundle_id}/"
        removed = [name for name in self.objects if name.startswith(prefix)]
        for name in removed:
            del self.objects[name]
        return len(removed)

    def paths(self, bundle_id: str = BUNDLE_ID) -> set[str]:
        prefix = f"{bundle_id}/"
        return {name.removeprefix(prefix) for name in self.objects if name.startswith(prefix)}

    def read(self, path: str, bundle_id: str = BUNDLE_ID) -> bytes:
        return self.objects[f"{bundle_id}/{path}"]


def _staging(tmp_path: Path) -> tuple[Path, Artifact, FixtureExtractor]:
    """A staging tree with the style already written, and a fixture extractor to copy from.

    The style is produced by the **style stage**, not by the compile pipeline: it is written
    under the staging root at its bundle-relative path and handed in already hashed.
    """
    staging = tmp_path / "staging"
    style_file = staging / STYLE_PATH
    style_file.parent.mkdir(parents=True, exist_ok=True)
    style_file.write_bytes(STYLE_BYTES)

    planet = tmp_path / "planet.pmtiles"
    planet.write_bytes(ARCHIVE_BYTES)
    return staging, Artifact.from_bytes(STYLE_PATH, STYLE_BYTES), FixtureExtractor(archive=planet)


def _compile(
    tmp_path: Path,
    *,
    itinerary: ItineraryV1 | None = None,
    sites: Sequence[SiteRecordV1] | None = None,
    lines: tuple[LineString, ...] = CONNECTED_LINES,
    store: _Store | None = None,
) -> Generator[Event, None, None]:
    """One whole compile with every seam injected, returned **undriven**.

    Typed as a `Generator` rather than the `Iterator` `run_compile` declares (the house
    annotation, as in `planner/pipeline.py`), because the disconnect tests below need
    `close()` — which is exactly what a client dropping the SSE stream does to it.
    """
    staging, style, extractor = _staging(tmp_path)
    stream = run_compile(
        CompileRequest(
            bundle_id=BUNDLE_ID,
            itinerary=itinerary if itinerary is not None else _itinerary(),
            frame=FRAME,
        ),
        sites=list(sites) if sites is not None else [_palace(), _street()],
        walk_network=_Network(lines),
        store=store if store is not None else _Store(),
        staging=staging,
        style=style,
        build=BUILD,
        extractor=extractor,
        assets=_Assets(),
        created_at=CREATED_AT,
    )
    return cast("Generator[Event, None, None]", stream)


def _run(tmp_path: Path, **kwargs: Any) -> tuple[list[Event], _Store]:
    store = kwargs.pop("store", None) or _Store()
    events = list(_compile(tmp_path, store=store, **kwargs))
    return events, store


def _phase(events: Sequence[Event], name: str) -> dict[str, Any]:
    """The one `status` frame for a phase — the sequence test proves there is exactly one."""
    return dict(next(e.data for e in events if e.event == "status" and e.data["phase"] == name))


def _final(events: Sequence[Event], name: str) -> dict[str, Any]:
    return dict(next(e.data for e in events if e.event == name))


# --- the frame sequence, which is the contract ---------------------------------------------


def test_the_frames_are_the_contract_sequence_in_order(tmp_path: Path) -> None:
    """`contracts/bundles.md`: six ordered `status` frames, then `manifest`, then `done`.

    Asserted as one list rather than as memberships, because the *order* is the contract —
    the pipeline is meant to read as its own spec.
    """
    events, _ = _run(tmp_path)

    assert [event.event for event in events] == ["status"] * 6 + ["manifest", "done"]
    assert tuple(e.data["phase"] for e in events if e.event == "status") == COMPILE_PHASES


def test_the_generator_does_no_work_until_it_is_driven(tmp_path: Path) -> None:
    """The caller drives it: a stream nobody pulls extracts nothing and publishes nothing."""
    store = _Store()
    stream = _compile(tmp_path, store=store)

    assert store.objects == {}
    stream.close()
    assert store.objects == {}


def test_a_client_that_disconnects_mid_stream_publishes_nothing(tmp_path: Path) -> None:
    """A disconnect stops the pass with it — no orphaned work, no half-written bundle."""
    store = _Store()
    stream = _compile(tmp_path, store=store)

    assert next(stream).data["phase"] == "tiles"
    stream.close()

    assert store.objects == {}


# --- stage by stage ------------------------------------------------------------------------


def test_the_tiles_frame_reports_the_computed_bbox_maxzoom_and_size(tmp_path: Path) -> None:
    events, store = _run(tmp_path)
    frame = _phase(events, "tiles")

    min_lon, min_lat, max_lon, max_lat = frame["bbox"]
    # The day's own extent plus ADR-0021's margin — computed by `compiler/tiles.py`, never
    # stated here; all this asserts is that the day is inside what was extracted.
    assert min_lon < PALACE[0] < STREET[0] < max_lon
    assert min_lat < PALACE[1] < STREET[1] < max_lat
    assert frame["maxzoom"] == 15
    assert frame["bytes"] == sum(
        len(data) for path, data in store.objects.items() if _is_tile_path(path)
    )


def _is_tile_path(object_name: str) -> bool:
    path = object_name.removeprefix(f"{BUNDLE_ID}/")
    return path.split("/", 1)[0] in {ARCHIVE_DIR, "glyphs", "sprites", "style"}


def test_the_routes_frame_counts_legs_and_edges_and_reports_connected(tmp_path: Path) -> None:
    events, store = _run(tmp_path)
    frame = _phase(events, "routes")

    # Exact equality, deliberately: this frame is a contract (`contracts/bundles.md`) and a
    # subset assertion would let a key be dropped without anything noticing.
    assert frame == {
        "phase": "routes",
        "legs": 1,
        "walk_graph_edges": 1,
        "connected": True,
        # The evidence behind the verdict (ADR-0034). A compile that failed on connectivity
        # used to report only `connected: false`, so diagnosing it from outside meant
        # reproducing the fetch by hand — which is how a snapping bug survived a live failure
        # long enough to block every real area.
        "component_count": 1,
        "anchor_components": [0, 0],
        "dropped_edges": 0,
    }
    assert WALK_GRAPH_PATH in store.paths()


def test_the_bundled_legs_are_the_approved_days_own_legs(tmp_path: Path) -> None:
    """`routing/legs.json` is the freeze of the approved legs, not a compile-time re-route.

    Re-routing would replace the distances the feasibility gate checked and the user approved
    with whatever the engine says today, while `content/itinerary.json` still carried the
    approved ones — two artifacts, one day, disagreeing.
    """
    itinerary = _itinerary()
    _, store = _run(tmp_path, itinerary=itinerary)

    frozen, _ = read_frozen_itinerary(store.read(ITINERARY_PATH))
    assert [leg.distance_m for leg in frozen.legs] == [148.0]
    assert b'"distance_m":148' in store.read(LEGS_PATH)


def test_the_quarantine_frame_records_every_removal_with_a_closed_reason(tmp_path: Path) -> None:
    events, _ = _run(tmp_path)
    frame = _phase(events, "quarantine")

    assert frame["values_dropped"] == len(frame["withheld"]) == 2
    assert {row["field"] for row in frame["withheld"]} == {"notes[0]", "reviews"}
    assert {row["site_id"] for row in frame["withheld"]} == {str(PALACE_ID)}
    # The closed enum (ADR-0025 A3), never free text — and never `unstamped`.
    assert {row["reason"] for row in frame["withheld"]} == {"license_forbids_redistribution"}


def test_no_unbundleable_value_appears_anywhere_in_the_published_bundle(tmp_path: Path) -> None:
    """FR-011 / SC-004, merge-blocking: quarantine **removes**, it does not flag.

    Scanned over every published byte — artifacts and manifest alike — rather than over the
    content artifact alone, because "nowhere in the bundle" is the actual invariant.
    """
    events, store = _run(tmp_path)

    assert store.objects, "the compile published nothing, so this proves nothing"
    for name, data in store.objects.items():
        assert UNBUNDLEABLE_MARKER.encode() not in data, f"{name} carries a withheld value"
        assert REVIEW_MARKER.encode() not in data, f"{name} carries a withheld review"
    manifest = _final(events, "manifest")
    assert len(manifest["withheld"]) == 2


def test_a_place_stripped_bare_still_ships(tmp_path: Path) -> None:
    """The card's edge case: a site whose extras are all withheld keeps whatever survives."""
    _, store = _run(tmp_path)

    sites = store.read(SITES_PATH).decode()
    assert "Palace of the Grand Master" in sites
    assert '"reviews":null' in sites


def test_the_content_frame_counts_sites_and_narrations(tmp_path: Path) -> None:
    events, _ = _run(tmp_path)

    assert _phase(events, "content") == {"phase": "content", "sites": 2, "narrations": 1}


def test_the_story_bytes_live_in_narrations_and_not_also_in_sites(tmp_path: Path) -> None:
    """One copy, one hash: the narration artifact is what `textLicense` declares the terms of."""
    events, store = _run(tmp_path)

    prose = b"A short adapted paragraph about Rhodes."
    assert prose in store.read(NARRATIONS_PATH)
    assert prose not in store.read(SITES_PATH)
    assert store.read(NARRATIONS_PATH).startswith(f'{{"{PALACE_ID}"'.encode())
    assert _final(events, "manifest")["textLicense"] == "CC-BY-SA-4.0"


def test_a_day_with_no_story_declares_no_text_license(tmp_path: Path) -> None:
    events, store = _run(tmp_path, sites=[_palace(stories=[]), _street()])

    assert _phase(events, "content")["narrations"] == 0
    assert store.read(NARRATIONS_PATH) == b"{}"
    assert _final(events, "manifest")["textLicense"] is None


def test_the_attribution_frame_lists_what_the_file_actually_declares(tmp_path: Path) -> None:
    """The frame is read off `ATTRIBUTION.md`, so the two cannot drift apart.

    Both directions are checked: every id the frame reports has a section in the file, and the
    ODbL credit every OSM-derived bundle owes is present (SC-010).
    """
    events, store = _run(tmp_path)

    reported = _phase(events, "attribution")["licenses"]
    rendered = store.read("ATTRIBUTION.md").decode()
    for license_id in reported:
        assert f"\n## {license_id} — " in rendered
    assert reported[0] == "ODbL-1.0"
    assert set(reported) >= {"ODbL-1.0", "CDLA-Permissive-2.0", "CC-BY-SA-4.0", "OFL-1.1"}
    assert "© OpenStreetMap contributors" in rendered


def test_the_hash_frame_counts_the_seven_manifest_artifacts(tmp_path: Path) -> None:
    """One hash per artifact, and the card's list of seven is the count the frame reports."""
    events, _ = _run(tmp_path)

    assert _phase(events, "hash") == {"phase": "hash", "artifacts": 7}


def test_every_recorded_hash_matches_the_bytes_that_were_published(tmp_path: Path) -> None:
    """The integrity contract, checked against the artifacts rather than against the manifest."""
    events, store = _run(tmp_path)
    manifest = BundleManifestV1.model_validate(_final(events, "manifest"))

    for path, digest in (
        (manifest.tiles.pmtiles.path, manifest.tiles.pmtiles.sha256),
        (manifest.tiles.pmtiles.style.path, manifest.tiles.pmtiles.style.sha256),
        (manifest.routing.walk_graph, manifest.routing.walk_graph_sha256),
        (manifest.routing.legs, manifest.routing.legs_sha256),
        (manifest.content.sites, manifest.content.sites_sha256),
        (manifest.content.narrations, manifest.content.narrations_sha256),
        (manifest.content.itinerary, manifest.content.itinerary_sha256),
        (manifest.attribution.path, manifest.attribution.sha256),
    ):
        assert hashlib.sha256(store.read(path)).hexdigest() == digest, path


def test_the_published_manifest_is_the_sealed_one_and_verifies_offline(tmp_path: Path) -> None:
    """What a device downloads must pass its own launch check (FR-013/FR-020)."""
    events, store = _run(tmp_path)

    published = BundleManifestV1.model_validate_json(store.read(MANIFEST_PATH))
    assert published.verify_manifest_sha256()
    assert published.model_dump(mode="json") == _final(events, "manifest")
    assert published.created_at == CREATED_AT
    assert published.itinerary_id == ITINERARY_ID


def test_every_manifest_path_resolves_to_a_published_object(tmp_path: Path) -> None:
    """The airplane-mode invariant: the manifest is the index, not a hint (FR-021)."""
    events, store = _run(tmp_path)
    manifest = BundleManifestV1.model_validate(_final(events, "manifest"))
    published = store.paths()

    for path in (
        manifest.tiles.pmtiles.path,
        manifest.tiles.pmtiles.style.path,
        manifest.routing.walk_graph,
        manifest.routing.legs,
        manifest.content.sites,
        manifest.content.narrations,
        manifest.content.itinerary,
        manifest.attribution.path,
    ):
        assert path in published, path
    # `glyphs.path` is a prefix, not an object — it resolves to the files under it.
    assert any(path.startswith(manifest.tiles.pmtiles.glyphs.path) for path in published)


def test_the_done_frame_reports_the_size_against_the_budget(tmp_path: Path) -> None:
    events, _ = _run(tmp_path)
    manifest = _final(events, "manifest")
    done = _final(events, "done")

    assert done["bundle_id"] == BUNDLE_ID
    assert done["size_bytes"] == manifest["size_bytes"]
    assert done["budget_bytes"] == 200 * 1024 * 1024
    assert done["over_budget"] is False


def test_an_over_budget_day_still_compiles_and_says_so(tmp_path: Path) -> None:
    """The ≤200 MB budget is reported, not enforced — the manifest has no over-budget field."""
    staging, style, extractor = _staging(tmp_path)
    events = list(
        run_compile(
            CompileRequest(
                bundle_id=BUNDLE_ID, itinerary=_itinerary(), frame=FRAME, budget_bytes=16
            ),
            sites=[_palace(), _street()],
            walk_network=_Network(CONNECTED_LINES),
            store=_Store(),
            staging=staging,
            style=style,
            build=BUILD,
            extractor=extractor,
            assets=_Assets(),
        )
    )

    done = _final(events, "done")
    assert done["over_budget"] is True
    assert done["budget_bytes"] == 16


# --- the guards, proved to bite (FAIL-007) --------------------------------------------------


class _Unstamped:
    """A structure with no `SourceRef` at all — nothing to read a stamp off or derive one from."""


def test_unstamped_input_refuses_the_compile_and_publishes_nothing(tmp_path: Path) -> None:
    """FR-012: a value is withheld **or** refused, never both — and refusal fails the compile.

    The unstamped structure is smuggled past pydantic on purpose: a validated record cannot
    carry one, and what is under test is that the *pipeline* propagates the refusal and leaves
    nothing behind, not that quarantine can spot it (`tests/test_compiler_quarantine.py`).
    """
    record = _palace()
    object.__setattr__(record, "location", _Unstamped())
    store = _Store()

    with pytest.raises(UnstampedValueError):
        list(_compile(tmp_path, sites=[record, _street()], store=store))

    assert store.objects == {}


def test_a_leg_the_registry_refuses_fails_the_compile(tmp_path: Path) -> None:
    """A dropped leg is a silent gap in the day, so `quarantine_legs` raises instead."""
    itinerary = _itinerary()
    refused = itinerary.legs[0].model_copy(
        update={
            "source": {
                "kind": "open_web",
                "id": "scraped:route",
                "license": "CC0-1.0",
                "attribution": None,
            }
        }
    )
    store = _Store()

    with pytest.raises(UnbundleableLegError):
        list(
            _compile(
                tmp_path, itinerary=itinerary.model_copy(update={"legs": (refused,)}), store=store
            )
        )

    assert store.objects == {}


def test_a_stop_whose_site_cannot_be_redistributed_fails_the_compile(tmp_path: Path) -> None:
    """A withheld `location` leaves nothing to draw: refused rather than compiled around."""
    store = _Store()

    with pytest.raises(UnbundleableStopError) as caught:
        list(
            _compile(
                tmp_path,
                sites=[_palace(location=_point(PALACE, kind="open_web")), _street()],
                store=store,
            )
        )

    assert str(PALACE_ID) in str(caught.value)
    assert store.objects == {}


def test_a_stop_with_no_record_fails_the_compile(tmp_path: Path) -> None:
    store = _Store()

    with pytest.raises(UnknownSiteError):
        list(_compile(tmp_path, sites=[_street()], store=store))

    assert store.objects == {}


def test_a_disconnected_walk_graph_fails_after_the_truthful_frame(tmp_path: Path) -> None:
    """`connected: false` fails the compile — but the frame that says so is streamed first."""
    store = _Store()
    stream = _compile(tmp_path, lines=ISLAND_LINES, store=store)
    seen: list[Event] = []

    with pytest.raises(DisconnectedWalkGraphError):
        for event in stream:
            seen.append(event)

    assert [event.data["phase"] for event in seen] == ["tiles", "routes"]
    assert seen[-1].data["connected"] is False
    assert MANIFEST_PATH not in store.paths()
    assert store.objects == {}


def test_a_failure_mid_publish_leaves_no_readable_bundle(tmp_path: Path) -> None:
    """The one that is easy to get wrong: a failure **after** a put must leave nothing.

    The store accepts the artifacts and then refuses the manifest — the worst ordering, since
    every byte the bundle needs is already there. Nothing may survive it.
    """
    store = _Store(fail_on=MANIFEST_PATH)

    with pytest.raises(RuntimeError, match="object store refused"):
        list(_compile(tmp_path, store=store))

    assert store.delete_calls == 1
    assert store.objects == {}


def test_a_failure_mid_publish_reports_the_store_error_not_the_cleanup(tmp_path: Path) -> None:
    """A cleanup that fails must not replace the error that actually failed the compile."""

    class _Undeletable(_Store):
        def delete_bundle(self, bundle_id: str) -> int:
            self.delete_calls += 1
            raise RuntimeError("delete is down too")

    store = _Undeletable(fail_on=SITES_PATH)

    with pytest.raises(RuntimeError, match="object store refused"):
        list(_compile(tmp_path, store=store))

    assert store.delete_calls == 1
    # The manifest is the index, so the orphaned objects are unreadable rather than partial.
    assert MANIFEST_PATH not in store.paths()


def test_an_artifact_missing_from_staging_fails_before_it_is_published(tmp_path: Path) -> None:
    """A manifest naming a path whose hash covers other bytes fails offline — so it fails here."""
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    planet = tmp_path / "planet.pmtiles"
    planet.write_bytes(ARCHIVE_BYTES)
    store = _Store()

    stream = run_compile(
        CompileRequest(bundle_id=BUNDLE_ID, itinerary=_itinerary(), frame=FRAME),
        sites=[_palace(), _street()],
        walk_network=_Network(CONNECTED_LINES),
        store=store,
        staging=staging,
        # Hashed but never written under the staging root — the style stage did not run.
        style=Artifact.from_bytes(STYLE_PATH, STYLE_BYTES),
        build=BUILD,
        extractor=FixtureExtractor(archive=planet),
        assets=_Assets(),
    )

    with pytest.raises(Exception, match="staging tree has no file"):
        list(stream)

    assert store.objects == {}


# --- the M1 flag ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("1", True), ("true", True), ("on", True), ("0", False), ("no", False)],
)
def test_the_in_process_flag_defaults_on_and_reads_the_env(
    value: str | None, expected: bool
) -> None:
    """Unset means **on**: M1 has no other compile path, so a default-off flag disables the
    only implementation there is (tech-design §5.3)."""
    env = {} if value is None else {"SIYUR_COMPILE_IN_PROCESS": value}

    assert in_process_compile_enabled(env) is expected
