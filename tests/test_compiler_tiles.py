"""Unit tests — `compiler/tiles.py` (T035 / T035a / T035b, Spec 002).

Ground truth: `docs/data/tile-source.md` and **ADR-0021**, whose "Confirmation" section asks
this file for three things by name — that the build resolver **selects inside the retention
window and raises when nothing answers**, that the buffer is asserted **against a known metric
distance** (projected, not degrees) including the 2 km floor on a single-block day, and that
the emitted `TileSourceV1` validates against the card.

Tier 1, so: **no network, no `pmtiles` binary, no container.** Both seams are injected — a
`FixtureExtractor` over a byte string written to `tmp_path`, and a `_FakeAssets` that mints
deterministic glyph bytes. The build resolver's HTTP `HEAD` is replaced by an injected
predicate over the candidate URL.

Three properties here are worth naming, because each is a failure that passes every other
gate in the pipeline:

* **The buffer is metric, so it is the same buffer everywhere.** `test_buffer_is_metric_not_
  degrees` runs the *same* day shape at Rhodes and at 78° N and asserts the geodesic spans
  match while the degree spans differ by exactly the ratio of the two cosines (3.9× here). A
  degree-offset buffer passes a Rhodes-only test and quietly gives an Arctic area a quarter
  of the margin.
* **Glyph ranges are derived, so a Hebrew area gets Hebrew.** `test_hebrew_area_selects_…`
  asserts the exact block `scripts/fetch-basemap.sh`'s U+0000–U+04FF window omits. That
  hardcoded window is the T035a bug: the map renders, the compile succeeds, the eval passes,
  and every label is blank.
* **Hashes are measured off the bytes on disk**, never off what the stage says it wrote —
  each artifact's `sha256` is re-read from the staging tree and recomputed.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from shapely import LineString, Point

from commons.merge import distance_m
from commons.models import ItineraryV1, SiteRecordV1, SourcedValue, SourceRef, TileSourceV1
from compiler.attribution import BundleSources, render_attribution
from compiler.manifest import Artifact
from compiler.tiles import (
    BASE_GLYPH_RANGES,
    BUILD_SOURCE,
    DEFAULT_FONTSTACKS,
    DEFAULT_SPRITE_ASSETS,
    EXTRACTOR_ENV,
    FIXTURE_ARCHIVE_ENV,
    GLYPH_LICENSE,
    GLYPH_LICENSE_FILE,
    GLYPHS_DIR,
    LICENSE_TEXT_ROOT,
    MAXZOOM,
    MINZOOM,
    SPRITE_LICENSE,
    SPRITE_LICENSE_FILE,
    SPRITES_DIR,
    TILE_ATTRIBUTION,
    TILE_LICENSE,
    Bbox,
    BuildUnavailable,
    ExtractFailed,
    ExtractRequest,
    FixtureExtractor,
    GlyphCoverageError,
    GlyphRange,
    PmtilesCliExtractor,
    ProtomapsBuild,
    TileError,
    TileStage,
    bbox_for_day,
    compile_tiles,
    directory_digest,
    glyph_ranges,
    itinerary_bbox,
    resolve_build,
    select_extractor,
)

# Two areas of deliberately different character, the same pair the fixtures and the genericity
# eval use: Rhodes old town (Greek) and Takayama (Japanese). Neither is baked into the module —
# they are arguments here precisely to prove that.
RHODES = (28.2240, 36.4455)
TAKAYAMA = (137.2600, 36.1405)
#: Svalbard. Not a supported area in any sense — it is here because 78° N is where a
#: degree-offset buffer stops being approximately right and starts being 5× wrong.
ARCTIC = (15.6500, 78.2200)

ARCHIVE_BYTES = b"PMTiles\x03fixture-archive-bytes"
STYLE = Artifact.from_bytes("style/base.json", b'{"version":8,"layers":[]}')

BUILD = ProtomapsBuild(url="https://build.example/20260814.pmtiles", build_date=date(2026, 8, 14))


# --------------------------------------------------------------------------------------
# Fixtures and doubles
# --------------------------------------------------------------------------------------


@dataclass
class _RecordingExtractor:
    """A `FixtureExtractor` that also keeps the request, so "never hotlinked" is checkable."""

    archive: Path
    requests: list[ExtractRequest] = field(default_factory=list)

    def extract(self, request: ExtractRequest) -> None:
        self.requests.append(request)
        FixtureExtractor(self.archive).extract(request)


@dataclass
class _FakeAssets:
    """Deterministic glyph/sprite bytes, with configurable holes. Never opens a socket."""

    #: Ranges (by label) the "host" does not publish, for any fontstack.
    absent_ranges: frozenset[str] = frozenset()
    #: When set, the *only* ranges the host publishes.
    published_ranges: frozenset[str] | None = None
    absent_sprites: frozenset[str] = frozenset()
    calls: list[tuple[str, str]] = field(default_factory=list)

    def glyph(self, fontstack: str, glyph_range: GlyphRange) -> bytes | None:
        self.calls.append((fontstack, glyph_range.label))
        if glyph_range.label in self.absent_ranges:
            return None
        if self.published_ranges is not None and glyph_range.label not in self.published_ranges:
            return None
        return f"glyph:{fontstack}:{glyph_range.label}".encode()

    def sprite(self, asset: str) -> bytes | None:
        if asset in self.absent_sprites:
            return None
        return f"sprite:{asset}".encode()


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    """A stand-in PMTiles archive on disk — the extractor seam's input, not a real pyramid."""
    path = tmp_path / "fixture" / "source.pmtiles"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ARCHIVE_BYTES)
    return path


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    return tmp_path / "staging"


def _site(*, names: dict[str, str], lon: float = RHODES[0], lat: float = RHODES[1]) -> SiteRecordV1:
    source = SourceRef(kind="osm", id="node/1", license="ODbL-1.0", attribution=TILE_ATTRIBUTION)

    def stamp(value: Any) -> SourcedValue[Any]:
        return SourcedValue[Any].stamp(
            value=value, source=source, confidence=1.0, observed_at=date(2026, 8, 1)
        )

    return SiteRecordV1(
        id=uuid4(),
        names={tag: stamp(name) for tag, name in names.items()},
        location=stamp(Point(lon, lat)),
    )


def _run(
    staging: Path,
    archive: Path,
    *,
    bbox: Bbox = (28.216, 36.440, 28.232, 36.451),
    assets: _FakeAssets | None = None,
    **kwargs: Any,
) -> TileStage:
    return compile_tiles(
        staging=staging,
        bbox=bbox,
        style=STYLE,
        build=BUILD,
        extractor=FixtureExtractor(archive),
        assets=assets if assets is not None else _FakeAssets(),
        **kwargs,
    )


# --------------------------------------------------------------------------------------
# T035 — build resolution: run time, never hotlinked, never past retention
# --------------------------------------------------------------------------------------


def test_resolves_todays_build_and_records_its_date() -> None:
    today = date(2026, 8, 14)
    build = resolve_build(
        today=today,
        probe=lambda url: url.endswith("20260814.pmtiles"),
        host="https://build.example",
    )
    assert build.url == "https://build.example/20260814.pmtiles"
    assert build.build_date == today


def test_walks_back_and_records_the_resolved_day_not_today() -> None:
    """`build_date` is freshness, so it must be the day that answered — not the day we asked."""
    today = date(2026, 8, 14)
    build = resolve_build(
        today=today,
        probe=lambda url: url.endswith("20260811.pmtiles"),
        host="https://build.example",
    )
    assert build.build_date == date(2026, 8, 11)
    assert build.url.endswith("20260811.pmtiles")


def test_probe_never_reaches_past_the_retention_window() -> None:
    asked: list[str] = []

    def probe(url: str) -> bool:
        asked.append(url)
        return False

    with pytest.raises(BuildUnavailable):
        resolve_build(today=date(2026, 8, 14), probe=probe, host="https://build.example")
    # Exactly the window, newest first: retention is ~7 days and a wider search would be
    # requesting builds Protomaps has already deleted.
    assert len(asked) == 7
    assert asked[0].endswith("20260814.pmtiles")
    assert asked[-1].endswith("20260808.pmtiles")


def test_no_build_is_fatal_not_an_empty_archive() -> None:
    with pytest.raises(BuildUnavailable, match="not an upstream contract"):
        resolve_build(
            today=date(2026, 8, 14), probe=lambda url: False, host="https://build.example"
        )


def test_the_resolved_url_is_what_the_extractor_is_given(staging: Path, archive: Path) -> None:
    """The URL reaches the extractor from the resolver, so nothing downstream can pin one."""
    extractor = _RecordingExtractor(archive)
    compile_tiles(
        staging=staging,
        bbox=(28.216, 36.440, 28.232, 36.451),
        style=STYLE,
        build=BUILD,
        extractor=extractor,
        assets=_FakeAssets(),
    )
    assert [request.build_url for request in extractor.requests] == [BUILD.url]


# --------------------------------------------------------------------------------------
# T035 — the bbox: ADR-0021's metric rule, asserted in metres
# --------------------------------------------------------------------------------------


def _span_m(bbox: Bbox) -> tuple[float, float]:
    """Geodesic (width, height) of a bbox in metres, width measured at its centre latitude."""
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = (min_lat + max_lat) / 2
    mid_lon = (min_lon + max_lon) / 2
    return (
        distance_m(Point(min_lon, mid_lat), Point(max_lon, mid_lat)),
        distance_m(Point(mid_lon, min_lat), Point(mid_lon, max_lat)),
    )


def test_buffer_is_one_km_on_every_side() -> None:
    """A 1 km buffer around a ~1.1 km walk ⇒ a ~3.1 km span, measured on the ellipsoid."""
    west = Point(RHODES[0], RHODES[1])
    east = Point(RHODES[0] + 0.0124, RHODES[1])  # ≈1,110 m at this latitude
    walk = distance_m(west, east)
    width, _height = _span_m(bbox_for_day([west, east]))
    assert width == pytest.approx(walk + 2000.0, rel=0.01)


def test_single_block_day_is_floored_at_the_two_km_minimum_span() -> None:
    """ADR-0021's floor: a bbox smaller than the map viewport is not a smaller bundle."""
    bbox = bbox_for_day([Point(*RHODES)], buffer_m=100.0)
    width, height = _span_m(bbox)
    assert width == pytest.approx(2000.0, rel=0.01)
    assert height == pytest.approx(2000.0, rel=0.01)
    # …and the floor is a floor, not a constant: drop it and the 100 m buffer shows through.
    tight_width, _ = _span_m(bbox_for_day([Point(*RHODES)], buffer_m=100.0, min_span_m=0.0))
    assert tight_width == pytest.approx(200.0, rel=0.02)


def test_buffer_is_metric_not_degrees() -> None:
    """The same day shape gets the same *metres* at 36° N and at 78° N — the genericity rule.

    A degree-offset buffer would give both the same *degrees*, which at 78° N is about a fifth
    of the distance. Rhodes-only tests cannot see that; this one is the whole point of FR-001.
    """
    shapes = {
        name: bbox_for_day([Point(lon, lat)])
        for name, (lon, lat) in (("rhodes", RHODES), ("takayama", TAKAYAMA), ("arctic", ARCTIC))
    }
    widths = {name: _span_m(bbox)[0] for name, bbox in shapes.items()}
    for name, width in widths.items():
        assert width == pytest.approx(2000.0, rel=0.01), name

    def degree_width(bbox: Bbox) -> float:
        return bbox[2] - bbox[0]

    # The degree widths must differ by exactly the ratio of the two cosines — 3.9× here.
    # That equality is what proves the buffer was applied in metres and converted, rather
    # than applied in degrees and merely measured in metres.
    assert degree_width(shapes["arctic"]) / degree_width(shapes["rhodes"]) == pytest.approx(
        math.cos(math.radians(RHODES[1])) / math.cos(math.radians(ARCTIC[1])), rel=0.02
    )


def test_bbox_covers_every_input_coordinate_and_no_more(staging: Path) -> None:
    """Tight itinerary bbox + the stray margin, and nothing beyond it (FR-016)."""
    stops = [Point(*RHODES), Point(RHODES[0] + 0.004, RHODES[1] + 0.003)]
    leg = LineString([(RHODES[0], RHODES[1]), (RHODES[0] + 0.004, RHODES[1] + 0.003)])
    bbox = bbox_for_day(stops, [leg])
    min_lon, min_lat, max_lon, max_lat = bbox
    for point in stops:
        assert min_lon < point.x < max_lon
        assert min_lat < point.y < max_lat
    # The day itself is ~500 m across, so the extent is the day plus 2 km of margin and the
    # 2 km floor — not the island, and not a region.
    width, height = _span_m(bbox)
    assert width < 3000.0
    assert height < 3000.0


def test_leg_geometry_widens_the_bbox() -> None:
    """Legs are part of the day's extent: a walk east must extend the map east."""
    stop = Point(*RHODES)
    without = bbox_for_day([stop])
    with_leg = bbox_for_day(
        [stop], [LineString([(RHODES[0], RHODES[1]), (RHODES[0] + 0.02, RHODES[1])])]
    )
    assert with_leg[2] > without[2]


def test_bbox_validates_as_a_tile_source_bbox() -> None:
    """ADR-0021's confirmation: the computed bbox is one `TileSourceV1` accepts as-is."""
    bbox = bbox_for_day([Point(*TAKAYAMA)])
    source = _tile_source(bbox=list(bbox))
    assert source.bbox == bbox


def test_an_empty_day_is_refused_rather_than_given_a_default_extent() -> None:
    with pytest.raises(TileError, match="broken one"):
        bbox_for_day()


def test_antimeridian_day_keeps_rfc7946_ordering() -> None:
    """A day straddling ±180° is an area too (SC-009). The bbox says so; the CLI refuses it."""
    bbox = bbox_for_day([Point(179.999, -16.5), Point(-179.999, -16.5)])
    assert bbox[0] > bbox[2], "an antimeridian bbox has minLon > maxLon (RFC 7946 §5.2)"
    assert bbox[0] > 179.0
    assert bbox[2] < -179.0
    request = ExtractRequest(
        build_url=BUILD.url, bbox=bbox, maxzoom=MAXZOOM, destination=Path("unused.pmtiles")
    )
    assert request.crosses_antimeridian
    with pytest.raises(ExtractFailed, match="complement of the day"):
        PmtilesCliExtractor().extract(request)


def test_itinerary_bbox_refuses_a_stop_whose_site_is_missing() -> None:
    site = _site(names={"el": "Ρόδος"})
    itinerary = _itinerary(site_ids=[site.id])
    assert itinerary_bbox(itinerary, [site]) == bbox_for_day([site.location.value])
    with pytest.raises(TileError, match="a place the day actually visits"):
        itinerary_bbox(itinerary, [])


# --------------------------------------------------------------------------------------
# T035a — glyph ranges derived from the area's own scripts
# --------------------------------------------------------------------------------------

#: The exact window `scripts/fetch-basemap.sh` hardcodes (U+0000–U+04FF).
DEV_SCRIPT_RANGES = frozenset(GlyphRange(first, first + 255) for first in range(0, 0x0500, 256))
HEBREW_BLOCK = GlyphRange(0x0500, 0x05FF)
ARABIC_BLOCK = GlyphRange(0x0600, 0x06FF)
HIRAGANA_BLOCK = GlyphRange(0x3000, 0x30FF)
GREEK_BLOCK = GlyphRange(0x0300, 0x03FF)


def test_hebrew_area_selects_the_block_the_dev_script_omits() -> None:
    """The T035a bug, made a failing assertion: U+0000–U+04FF renders Hebrew as nothing."""
    ranges = set(glyph_ranges(["רחוב סגולה"]))
    assert HEBREW_BLOCK in ranges
    assert HEBREW_BLOCK not in DEV_SCRIPT_RANGES


def test_japanese_area_selects_kana_and_han() -> None:
    ranges = set(glyph_ranges(["高山市", "さんまち通り"]))
    assert HIRAGANA_BLOCK in ranges
    assert GlyphRange(0x4E00, 0x4EFF) in ranges  # a Han block, widened from the script
    assert HEBREW_BLOCK not in ranges, "selection is derived, not a union of every script"


def test_arabic_area_selects_arabic() -> None:
    assert ARABIC_BLOCK in set(glyph_ranges(["شارع الحمرا"]))


def test_greek_area_selects_greek_and_nothing_exotic() -> None:
    ranges = set(glyph_ranges(["Ρόδος", "Πύλη Ταρσανά"]))
    assert GREEK_BLOCK in ranges
    assert ranges.isdisjoint({HEBREW_BLOCK, ARABIC_BLOCK, HIRAGANA_BLOCK})


def test_base_ranges_are_always_present_even_with_no_labels() -> None:
    """Digits, punctuation and the style's own Latin text are drawn whatever the area is."""
    assert set(glyph_ranges([])) == set(BASE_GLYPH_RANGES)


def test_selection_is_sorted_and_deterministic() -> None:
    names = ["Ρόδος", "רחוב סגולה", "高山市"]
    first = glyph_ranges(names)
    assert first == glyph_ranges(reversed(names))
    assert list(first) == sorted(first)


def test_ranges_are_the_256_codepoint_windows_maplibre_asks_for() -> None:
    assert GlyphRange.containing(0x05D0) == HEBREW_BLOCK
    assert HEBREW_BLOCK.label == "1280-1535"


# --------------------------------------------------------------------------------------
# T035/T035b — the stage: what it wrote, and what each file hashes to
# --------------------------------------------------------------------------------------


def test_emitted_tile_source_matches_the_schema_card(staging: Path, archive: Path) -> None:
    stage = _run(staging, archive)
    source = stage.source
    assert source.schema_ver == "TileSourceV1"
    assert source.format == "pmtiles"
    assert source.path == "tiles/area.pmtiles", "the default name is generic, not a place"
    assert (source.minzoom, source.maxzoom) == (MINZOOM, MAXZOOM) == (0, 15)
    assert source.build_source == BUILD_SOURCE
    assert source.build_date == BUILD.build_date
    assert source.tile_license == TILE_LICENSE == "ODbL-1.0"
    assert source.attribution == TILE_ATTRIBUTION
    assert source.glyphs.license == GLYPH_LICENSE == "OFL-1.1"
    assert source.glyphs.path == "glyphs/"
    assert source.sprites.license == SPRITE_LICENSE == "MIT"
    assert source.sprites.path == "sprites/"
    # T035b: the two directory refs carry the digests this stage computed, and carry the
    # *same* strings the stage reports — one digest, recorded and echoed, never recomputed.
    assert source.glyphs.sha256 == stage.glyphs_sha256 == directory_digest(stage.glyphs)
    assert source.sprites.sha256 == stage.sprites_sha256 == directory_digest(stage.sprites)
    assert (source.style.path, source.style.sha256) == (STYLE.path, STYLE.sha256)
    # Re-validating the emitted object is the card check ADR-0021 asks for: `TileSourceV1` is
    # `extra="forbid"` and validates its own bbox/zoom range.
    assert TileSourceV1.model_validate(source.model_dump(mode="json")) == source


def test_archive_hash_is_the_hash_of_the_bytes_on_disk(staging: Path, archive: Path) -> None:
    """Measured off the artifact, not off what the stage reports about it."""
    stage = _run(staging, archive)
    written = (staging / stage.source.path).read_bytes()
    assert written == ARCHIVE_BYTES
    assert stage.source.sha256 == hashlib.sha256(written).hexdigest()
    assert stage.archive.size_bytes == len(ARCHIVE_BYTES)


def test_every_artifact_hash_matches_its_file(staging: Path, archive: Path) -> None:
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})])
    for artifact in stage.artifacts:
        if artifact == STYLE:
            continue  # written by the style stage, not this one
        on_disk = (staging / artifact.path).read_bytes()
        assert artifact.sha256 == hashlib.sha256(on_disk).hexdigest(), artifact.path
        assert artifact.size_bytes == len(on_disk), artifact.path


def test_glyph_files_land_under_the_path_the_manifest_names(staging: Path, archive: Path) -> None:
    stage = _run(staging, archive, sites=[_site(names={"he": "רחוב סגולה"})])
    hebrew = [
        artifact
        for artifact in stage.glyphs
        if artifact.path.endswith(f"/{HEBREW_BLOCK.label}.pbf")
    ]
    assert len(hebrew) == len(DEFAULT_FONTSTACKS), "every fontstack needs the area's own script"
    for artifact in hebrew:
        assert artifact.path.startswith(stage.source.glyphs.path)
        assert (staging / artifact.path).is_file()


def test_glyph_digest_notices_a_flipped_byte_and_a_missing_file(
    staging: Path, archive: Path, tmp_path: Path
) -> None:
    """T035b's whole point: a corrupted glyph set must be detectable before the map is blank."""
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})])
    baseline = stage.glyphs_sha256

    corrupted = [
        Artifact(path=a.path, sha256=_flip(a.sha256), size_bytes=a.size_bytes) if index == 0 else a
        for index, a in enumerate(stage.glyphs)
    ]
    assert directory_digest(corrupted) != baseline
    # Paths are covered too — a removed or renamed range file changes the digest, which a
    # digest over concatenated bytes alone would not catch.
    assert directory_digest(stage.glyphs[1:]) != baseline
    renamed = (
        Artifact(path="glyphs/other/0-255.pbf", sha256=stage.glyphs[0].sha256, size_bytes=1),
        *stage.glyphs[1:],
    )
    assert directory_digest(renamed) != baseline
    # …and it is order-independent, so two runs that iterate fontstacks differently agree.
    assert directory_digest(reversed(stage.glyphs)) == baseline


def test_size_bytes_totals_every_file_including_the_style(staging: Path, archive: Path) -> None:
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})])
    on_disk = sum(
        (staging / artifact.path).stat().st_size
        for artifact in stage.artifacts
        if artifact != STYLE
    )
    assert stage.size_bytes == on_disk + STYLE.size_bytes


def test_sprites_are_written_and_hashed(staging: Path, archive: Path) -> None:
    stage = _run(staging, archive)
    assert {artifact.path for artifact in stage.sprites} >= {
        "sprites/light.json",
        "sprites/dark@2x.png",
    }
    for artifact in stage.sprites:
        assert artifact.sha256 == hashlib.sha256((staging / artifact.path).read_bytes()).hexdigest()
    assert stage.sprites_sha256 == directory_digest(stage.sprites)


def _remeasure(staging: Path, directory: str) -> str:
    """The verifier's half of T035b: re-hash what is actually in the staged directory.

    Deliberately walks the filesystem rather than `stage.glyphs`, because a digest that only
    ever sees the producer's own in-memory list cannot notice anything happening to the files.
    """
    root = staging / directory
    return directory_digest(
        Artifact.from_file(str(path.relative_to(staging)), path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


@pytest.mark.parametrize("directory", ["glyphs", "sprites"])
def test_a_corrupted_file_makes_the_recorded_digest_disagree(
    staging: Path, archive: Path, directory: str
) -> None:
    """The reason the field exists. A hash that is merely *present* is not integrity: flipping
    one bit in one vendored file, exactly as a truncated download or a bad sector would, has to
    make the bundle's own directory re-measurement disagree with what the manifest recorded."""
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})])
    recorded = getattr(stage.source, directory).sha256
    assert _remeasure(staging, directory) == recorded, "an untouched bundle verifies"
    # …and it verifies because the digest covers **the bytes in the bundle**, not bytes the
    # producer happened to have in hand. Each file the ref names is re-read from the staging
    # tree and re-hashed: a stage that hashed one buffer and wrote another — a truncated
    # write, a source file copied instead of the emitted one — fails here rather than
    # shipping a digest that verifies cleanly and protects nothing.
    for artifact in getattr(stage, directory):
        on_disk = (staging / artifact.path).read_bytes()
        assert artifact.sha256 == hashlib.sha256(on_disk).hexdigest(), artifact.path

    victim = staging / getattr(stage, directory)[0].path
    mutated = bytearray(victim.read_bytes())
    mutated[0] ^= 0x01
    victim.write_bytes(bytes(mutated))

    assert _remeasure(staging, directory) != recorded


@pytest.mark.parametrize("directory", ["glyphs", "sprites"])
def test_a_deleted_file_makes_the_recorded_digest_disagree(
    staging: Path, archive: Path, directory: str
) -> None:
    """The other half of the same promise, and the one a digest over concatenated bytes would
    miss for an empty file: the *listing* is hashed, so a file that is simply gone is caught."""
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})])
    recorded = getattr(stage.source, directory).sha256

    (staging / getattr(stage, directory)[0].path).unlink()

    assert _remeasure(staging, directory) != recorded


def test_an_asset_host_that_published_no_sprites_still_records_a_digest(
    staging: Path, archive: Path
) -> None:
    """A sprite the host does not publish is skipped, not fatal (icons, not every label) — so
    the sheet-less case has to produce a *valid* recorded digest rather than an empty string,
    and a different one from a populated directory.

    What survives that case is the licence text: MIT binds the notice to the sheets whether
    eight of them shipped or none did, and it is vendored from the repo rather than fetched,
    so no answer from the asset host can take it away."""
    stage = _run(
        staging, archive, assets=_FakeAssets(absent_sprites=frozenset(DEFAULT_SPRITE_ASSETS))
    )
    assert [artifact.path for artifact in stage.sprites] == [f"{SPRITES_DIR}/{SPRITE_LICENSE_FILE}"]
    assert stage.source.sprites.sha256 == directory_digest(stage.sprites)
    assert stage.source.sprites.sha256 != directory_digest(())
    assert stage.source.sprites.sha256 != stage.source.glyphs.sha256
    # Re-validating proves the empty-directory digest is still a legal `Sha256Hex`.
    assert TileSourceV1.model_validate(stage.source.model_dump(mode="json")) == stage.source


def test_a_range_the_host_does_not_publish_is_recorded_not_invented(
    staging: Path, archive: Path
) -> None:
    assets = _FakeAssets(absent_ranges=frozenset({"768-1023"}))
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})], assets=assets)
    assert set(stage.missing) == {(stack, "768-1023") for stack in DEFAULT_FONTSTACKS}
    assert all("768-1023" not in artifact.path for artifact in stage.glyphs)


def test_a_script_with_no_glyphs_at_all_fails_the_compile(staging: Path, archive: Path) -> None:
    """Partial coverage is reported; total absence is the T035a failure and must be loud."""
    assets = _FakeAssets(published_ranges=frozenset({"0-255", "256-511"}))
    with pytest.raises(GlyphCoverageError, match="render as nothing"):
        _run(staging, archive, sites=[_site(names={"he": "רחוב סגולה"})], assets=assets)


def test_stage_reports_the_scripts_it_derived_from(staging: Path, archive: Path) -> None:
    stage = _run(
        staging,
        archive,
        sites=[_site(names={"el": "Ρόδος", "en": "Rhodes"})],
        assets=_FakeAssets(),
    )
    assert stage.scripts == ("Grek", "Latn")
    assert GREEK_BLOCK in set(stage.ranges)


def test_bbox_reaches_the_cli_argument_unchanged(staging: Path, archive: Path) -> None:
    extractor = _RecordingExtractor(archive)
    bbox: Bbox = (28.216, 36.44, 28.232, 36.451)
    compile_tiles(
        staging=staging,
        bbox=bbox,
        style=STYLE,
        build=BUILD,
        extractor=extractor,
        assets=_FakeAssets(),
    )
    request = extractor.requests[0]
    assert request.bbox == bbox
    assert request.bbox_arg == "28.216000,36.440000,28.232000,36.451000"
    assert request.maxzoom == MAXZOOM


def test_all_names_feed_the_selection_not_only_the_presentation_language(
    staging: Path, archive: Path
) -> None:
    """A bundle read in `en` still renders the map's own Japanese labels."""
    stage = _run(staging, archive, sites=[_site(names={"en": "Takayama", "ja": "高山市"})])
    assert "Hani" in stage.scripts
    assert HIRAGANA_BLOCK in set(stage.ranges)


# --------------------------------------------------------------------------------------
# The licence texts — the two obligations `ATTRIBUTION.md` states, made true
# --------------------------------------------------------------------------------------
#
# `compiler/attribution.py` asserted both of these on every generated ATTRIBUTION.md while
# nothing in `compiler/` wrote either file: the bundle claimed compliance in the same artifact
# that failed it, and OFL §2 makes the fonts' claim a real breach rather than an untidy one.
# These tests are about that specific lie, so they read the claim out of the rendered
# attribution rather than restating it — a reworded obligation cannot quietly desert its file.

#: The walk graph's stamp: required by `BundleSources` and irrelevant here, so it is the
#: plainest legal ODbL ref rather than anything the licence assertions depend on.
WALK_GRAPH_SOURCE = SourceRef(
    kind="osm", id="valhalla:pedestrian", license="ODbL-1.0", attribution=TILE_ATTRIBUTION
)

#: ``(bundle directory, filename, SPDX id)`` for each licence text this stage emits.
VENDORED_LICENSES = (
    (GLYPHS_DIR, GLYPH_LICENSE_FILE, GLYPH_LICENSE),
    (SPRITES_DIR, SPRITE_LICENSE_FILE, SPRITE_LICENSE),
)


def _attribution(stage: TileStage) -> str:
    """This bundle's ATTRIBUTION.md, whitespace-flattened so a re-wrap is not a test failure."""
    rendered = render_attribution(
        BundleSources(tiles=stage.source, walk_graph_source=WALK_GRAPH_SOURCE)
    )
    return " ".join(rendered.split())


def test_the_licence_files_attribution_promises_are_in_the_bundle(
    staging: Path, archive: Path
) -> None:
    """The defect itself: ATTRIBUTION.md named `OFL.txt` and no `OFL.txt` was ever written."""
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})])
    rendered = _attribution(stage)

    assert "`OFL.txt` ships in the bundle beside the glyphs it covers." in rendered
    assert (staging / GLYPHS_DIR / GLYPH_LICENSE_FILE).is_file()
    assert "The copyright notice and the license text travel with the work." in rendered
    assert (staging / SPRITES_DIR / SPRITE_LICENSE_FILE).is_file()


@pytest.mark.parametrize(("directory", "filename", "license_id"), VENDORED_LICENSES)
def test_a_bundled_licence_text_is_the_vendored_bytes_verbatim(
    staging: Path, archive: Path, directory: str, filename: str, license_id: str
) -> None:
    """Verbatim, from a committed file — a reflowed or summarised licence is not the licence.

    The two content assertions are the guard against a well-meaning replacement: the SIL text
    without the Noto copyright line, or a "MIT licensed" one-liner in place of Mapzen's notice,
    would satisfy every hash in this file and discharge neither obligation.
    """
    _run(staging, archive)
    vendored = (LICENSE_TEXT_ROOT / directory / filename).read_bytes()

    written = (staging / directory / filename).read_bytes()
    assert written == vendored

    text = vendored.decode("utf-8")
    if license_id == "OFL-1.1":
        assert "SIL OPEN FONT LICENSE Version 1.1" in text
        assert "The Noto Project Authors" in text
    else:
        assert "The MIT License (MIT)" in text
        assert "Copyright (c) 2017 Mapzen" in text


@pytest.mark.parametrize(("directory", "filename", "_license_id"), VENDORED_LICENSES)
def test_a_licence_text_is_hashed_and_digested_like_every_other_bundled_file(
    staging: Path, archive: Path, directory: str, filename: str, _license_id: str
) -> None:
    """Not a parallel copy path: the licence is inside the ref's own directory digest.

    Which means a licence text stripped out of a bundle in transit is caught by the same check
    that catches a truncated glyph — the one file that proves compliance is not the one file
    nothing verifies.
    """
    stage = _run(staging, archive, sites=[_site(names={"el": "Ρόδος"})])
    path = f"{directory}/{filename}"
    staged = [artifact for artifact in getattr(stage, directory) if artifact.path == path]
    assert len(staged) == 1, f"{path} is missing from stage.{directory}"

    on_disk = (staging / path).read_bytes()
    assert staged[0].sha256 == hashlib.sha256(on_disk).hexdigest()
    assert staged[0].size_bytes == len(on_disk)

    recorded = getattr(stage.source, directory).sha256
    assert _remeasure(staging, directory) == recorded, "an untouched bundle verifies"
    without = tuple(a for a in getattr(stage, directory) if a.path != path)
    assert directory_digest(without) != recorded, "the licence text is inside the digest"

    (staging / path).unlink()
    assert _remeasure(staging, directory) != recorded


def test_a_missing_vendored_licence_text_fails_the_compile(
    staging: Path, archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loud, not skipped. Compiling without it means shipping Noto glyphs in breach of OFL §2
    under an ATTRIBUTION.md that says otherwise, which is worse than not compiling."""
    monkeypatch.setattr("compiler.tiles.LICENSE_TEXT_ROOT", tmp_path / "no-licenses")
    with pytest.raises(TileError, match="licence text is missing"):
        _run(staging, archive)


# --------------------------------------------------------------------------------------
# The seams themselves
# --------------------------------------------------------------------------------------


def test_default_extractor_is_the_cli_never_the_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixture default would hand every area on earth one recorded archive, silently."""
    monkeypatch.delenv(EXTRACTOR_ENV, raising=False)
    monkeypatch.delenv(FIXTURE_ARCHIVE_ENV, raising=False)
    assert isinstance(select_extractor(), PmtilesCliExtractor)


def test_fixture_extractor_needs_an_explicit_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXTRACTOR_ENV, "fixture")
    monkeypatch.delenv(FIXTURE_ARCHIVE_ENV, raising=False)
    with pytest.raises(TileError, match=FIXTURE_ARCHIVE_ENV):
        select_extractor()
    monkeypatch.setenv(FIXTURE_ARCHIVE_ENV, "/does/not/matter.pmtiles")
    assert isinstance(select_extractor(), FixtureExtractor)


def test_unknown_extractor_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXTRACTOR_ENV, "protomaps-api")
    with pytest.raises(TileError, match="unknown"):
        select_extractor()


def test_missing_cli_says_it_is_a_system_binary(tmp_path: Path) -> None:
    """The dependency is go-pmtiles on PATH, not a Python package — the error must say so."""
    extractor = PmtilesCliExtractor(binary="siyur-no-such-pmtiles-binary")
    with pytest.raises(ExtractFailed, match="system binary, not a Python dependency"):
        extractor.extract(
            ExtractRequest(
                build_url=BUILD.url,
                bbox=(28.216, 36.440, 28.232, 36.451),
                maxzoom=MAXZOOM,
                destination=tmp_path / "out.pmtiles",
            )
        )


def test_the_extractor_never_uses_a_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The argv reaches ``execvp`` as a vector, and no element is ever shell-interpreted.

    This test exists to hold up the ``# nosemgrep`` in
    :meth:`~compiler.tiles.PmtilesCliExtractor.extract`. That suppression's justification is
    "``shell=False``, so an argument cannot become a command" — a claim about code that a
    future edit could silently falsify, since adding ``shell=True`` is a one-word change that
    turns every element of ``argv`` into shell input and makes the suppressed finding real.

    A suppression whose premise nothing checks is how a real vulnerability ends up wearing a
    comment that says it is fine. So the premise is asserted here: ``shell`` is never passed
    truthy, and the command is handed over as a **list**, not a joined string.
    """
    captured: dict[str, object] = {}

    def _fake_run(argv: object, **kwargs: object) -> object:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        raise FileNotFoundError("not actually running pmtiles")

    monkeypatch.setattr("compiler.tiles.subprocess.run", _fake_run)

    with pytest.raises(ExtractFailed):
        PmtilesCliExtractor().extract(
            ExtractRequest(
                build_url=BUILD.url,
                bbox=(28.216, 36.440, 28.232, 36.451),
                maxzoom=MAXZOOM,
                destination=tmp_path / "out.pmtiles",
            )
        )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    # Not `is False` — absent is also correct, and the point is that it is never truthy.
    assert not kwargs.get("shell"), "shell=True would make the suppressed semgrep finding real"
    assert isinstance(captured["argv"], list), "a joined string would be shell-parseable"


def test_fixture_extractor_refuses_a_missing_archive(tmp_path: Path) -> None:
    with pytest.raises(ExtractFailed, match="does not exist"):
        FixtureExtractor(tmp_path / "absent.pmtiles").extract(
            ExtractRequest(
                build_url=BUILD.url,
                bbox=(28.216, 36.440, 28.232, 36.451),
                maxzoom=MAXZOOM,
                destination=tmp_path / "out.pmtiles",
            )
        )


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _flip(digest: str) -> str:
    """A different, still-valid lowercase-hex sha256 (`Sha256Hex` refuses anything else)."""
    return ("0" if digest[0] != "0" else "1") + digest[1:]


def _tile_source(**overrides: Any) -> TileSourceV1:
    payload: dict[str, Any] = {
        "path": "tiles/area.pmtiles",
        "sha256": hashlib.sha256(ARCHIVE_BYTES).hexdigest(),
        "bbox": [28.216, 36.440, 28.232, 36.451],
        "minzoom": MINZOOM,
        "maxzoom": MAXZOOM,
        "build_source": BUILD_SOURCE,
        "build_date": "2026-08-14",
        "tile_license": TILE_LICENSE,
        "attribution": TILE_ATTRIBUTION,
        "style": {"path": STYLE.path, "sha256": STYLE.sha256},
        "glyphs": {
            "path": "glyphs/",
            "license": GLYPH_LICENSE,
            "sha256": hashlib.sha256(b"glyphs").hexdigest(),
        },
        "sprites": {
            "path": "sprites/",
            "license": SPRITE_LICENSE,
            "sha256": hashlib.sha256(b"sprites").hexdigest(),
        },
    }
    payload.update(overrides)
    return TileSourceV1.model_validate(payload)


def _itinerary(*, site_ids: list[UUID]) -> ItineraryV1:
    return ItineraryV1.model_validate(
        {
            "user_id": "google-oauth2|1103",
            "area_id": str(uuid4()),
            "date": "2026-08-14",
            "lang": "en",
            "budgets": {"walking_m": 0.0, "hours": 0.0},
            "stops": [
                {
                    "site_id": str(site_id),
                    "order": order,
                    "planned_start": "10:00",
                    "dwell_min": 30,
                }
                for order, site_id in enumerate(site_ids)
            ],
        }
    )
