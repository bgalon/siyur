"""T046 — `compiler/attribution.py`: the generated `ATTRIBUTION.md`.

Ground truth: `compiler/attribution.py`'s own module docstring, `DATA-LICENSES.md` (the
registry every obligation transcribed into `_OBLIGATIONS` traces to), `commons/licenses.py`
(the quarantine rule this module leans on but never re-derives), `commons/sources/
wikivoyage.py` (where the `?oldid=<revid>`-pinned credit string is built, ADR-0024), ADR-0024
(the share-alike obligation the "Bundled text license" section discharges) and ADR-0019 (why
this file is an **aggregate** credit and not the per-value co-presence mechanism — that
distinction is *assumed* here, not re-argued).

T046's brief: ODbL present for OSM-derived artifacts; every bundled story credited exactly
once; the text license declared. Beyond that, this file pins what the module got wrong once
and the three things it must refuse rather than paper over:

1. **The grouping bug.** `_add_place_data` groups place-data credits on `(kind, license,
   attribution)` and deliberately **not** on the whole `SourceRef` — a stamp's `id` is
   per-record, so keying on it once rendered 780 near-identical lines and buried the
   obligation text underneath them. Pinned below with enough records that the regression is
   unmistakable: a constant number of lines, not one per record.
2. **Determinism.** `attribution_bytes` is what `T040` hashes, so the same bundle must render
   byte-identically however its sites, legs, notices and each record's `names` keys were
   ordered.
3. **Two revisions of one article are both named, not collapsed** — dedup is on `source.id`,
   collapsing revisions would credit a revision only partly adapted.
4. **The three refusal paths**, all `AttributionRefused`: an attribution-required license
   with an empty stamp, a `bundleable=false` value that reached frozen content (T038 did not
   run), and a license off the allowlist.
5. **`attribution_bytes` is UTF-8, LF, one trailing newline** — the one encoding rule, so two
   callers cannot give one bundle two hashes.

No real place names appear in this file's fixtures — job 4's genericity scan reads product
code, and a fixture built from a city in mind is exactly how a place literal reaches the repo.
"""

from __future__ import annotations

import hashlib
import importlib
import random
from datetime import date
from typing import Any

import pytest

from commons.geo import point_from_lonlat
from commons.licenses import SourceKind
from commons.models import (
    ArtifactRef,
    GlyphsRef,
    RouteLegV1,
    SiteRecordV1,
    SourcedValue,
    SourceRef,
    Story,
    TileSourceV1,
)
from compiler.attribution import (
    AttributionRefused,
    BundleSources,
    Notice,
    attribution_bytes,
    bundled_text_license,
    render_attribution,
)

OBSERVED = date(2026, 8, 8)


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _ref(
    kind: SourceKind,
    license_id: str,
    id_: str,
    *,
    attribution: str | None = None,
    url: str | None = None,
) -> SourceRef:
    return SourceRef(kind=kind, id=id_, url=url, license=license_id, attribution=attribution)


def _expect_str(value: str | None) -> str:
    """Narrow a fixture's own `str | None` field for `.count()` — every ref built below sets
    `attribution` explicitly, so `None` here would mean the fixture is wrong, not the module."""
    assert value is not None
    return value


#: The two ODbL stamps a bundle always carries beside its tiles (module docstring: "a bundle
#: always has both, and both are OSM Produced Works whose ODbL credit is not optional").
WALK_GRAPH_REF = _ref(
    "osm", "ODbL-1.0", "osm:walk-graph", attribution="© OpenStreetMap contributors"
)
ROUTING_REF = _ref(
    "osm", "ODbL-1.0", "valhalla:pedestrian", attribution="© OpenStreetMap contributors"
)
#: The place-data stamp most fixtures use; a courtesy-credit license (no attribution needed).
DEFAULT_PLACE_REF = _ref("overture", "CDLA-Permissive-2.0", "overture:default-1")


def _tile_source(**overrides: Any) -> TileSourceV1:
    fields: dict[str, Any] = {
        "path": "tiles/area.pmtiles",
        "format": "pmtiles",
        "sha256": _digest("pmtiles"),
        "bbox": (10.0, 20.0, 10.5, 20.5),
        "minzoom": 0,
        "maxzoom": 15,
        "build_source": "protomaps-daily",
        "build_date": date(2026, 8, 1),
        "tile_license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "style": ArtifactRef(path="style/base.json", sha256=_digest("style")),
        "glyphs": GlyphsRef(path="glyphs/", license="OFL-1.1", sha256=_digest("glyphs")),
        "sprites": GlyphsRef(path="sprites/", license="MIT", sha256=_digest("sprites")),
    }
    fields.update(overrides)
    return TileSourceV1(**fields)


TILES = _tile_source()


def _bundle(**overrides: Any) -> BundleSources:
    fields: dict[str, Any] = {"tiles": TILES, "walk_graph_source": WALK_GRAPH_REF}
    fields.update(overrides)
    return BundleSources(**fields)


def _stamped_str(value: str, ref: SourceRef, *, observed: date = OBSERVED) -> SourcedValue[str]:
    return SourcedValue[str].stamp(value=value, source=ref, confidence=0.8, observed_at=observed)


def _stamped_point(
    ref: SourceRef, *, lon: float = 10.0, lat: float = 20.0, observed: date = OBSERVED
) -> SourcedValue[Any]:
    return SourcedValue.stamp(
        value=point_from_lonlat(lon, lat), source=ref, confidence=0.8, observed_at=observed
    )


def _site(*, place_ref: SourceRef | None = None, **overrides: Any) -> SiteRecordV1:
    ref = place_ref or DEFAULT_PLACE_REF
    fields: dict[str, Any] = {
        "location": _stamped_point(ref),
        "names": {"und": _stamped_str("Place", ref)},
    }
    fields.update(overrides)
    return SiteRecordV1(**fields)


def _story(
    ref: SourceRef, text: str = "Adapted narration text.", *, observed: date = OBSERVED
) -> Story:
    return Story(text_by_lang={"en": text}, source=ref, observed_at=observed)


def _leg(n: int, *, source: SourceRef = ROUTING_REF) -> RouteLegV1:
    lon = 10.0 + n * 0.001
    return RouteLegV1(
        id=f"leg-{n}",
        from_stop=n,
        to_stop=n + 1,
        geometry=[(lon, 20.0), (lon + 0.0005, 20.0005)],
        distance_m=100.0,
        duration_s=90,
        source=source,
    )


def _notice(subject: str, license_id: str, text: str) -> Notice:
    return Notice(subject=subject, license=license_id, text=text)


# --- ODbL for every OSM-derived artifact --------------------------------------------------


def test_odbl_present_for_tiles_walk_graph_and_route_legs() -> None:
    """FR-004 / Article V, transcribed into `_OBLIGATIONS`: the basemap tiles, the pruned
    walking network and every routing leg are OSM Produced Works, and all three land in one
    ODbL section, credited once each (legs sharing one stamp grouped into a single line)."""
    legs = (_leg(0), _leg(1))
    text = render_attribution(_bundle(legs=legs))

    assert text.count("## ODbL-1.0 — Open Database License 1.0") == 1
    assert "© OpenStreetMap contributors" in text
    assert f"`{TILES.path}`" in text
    assert f"`{WALK_GRAPH_REF.id}`" in text
    assert f"`{ROUTING_REF.id}`, 2 legs" in text


def test_odbl_section_is_pinned_first_the_rest_alphabetical() -> None:
    """`_section_order`: ODbL is pulled to the front (OSMF Produced-Work guidance / Article
    V); every other license present is ordered alphabetically after it."""
    ref = _ref(
        "wikivoyage",
        "CC-BY-SA-4.0",
        "article:generic-1",
        attribution='"Generic Place", Wikivoyage, https://example.test/wiki/Generic_Place'
        "?oldid=1 — authors via page history",
        url="https://example.test/wiki/Generic_Place?oldid=1",
    )
    site = _site(stories=(_story(ref),))

    text = render_attribution(_bundle(sites=(site,)))

    odbl_at = text.index("## ODbL-1.0")
    cc_at = text.index("## CC-BY-SA-4.0")
    ofl_at = text.index("## OFL-1.1")  # the tiles' glyphs, always present
    assert odbl_at < cc_at < ofl_at


# --- glyphs and sprites: two works, two licenses, two credits ------------------------------


def _section(text: str, license_id: str) -> str:
    """The body of one license section — so a test can assert what a license does *not* cover."""
    marker = f"## {license_id} — "
    rest = text[text.index(marker) + len(marker) :]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def test_glyphs_and_sprites_are_credited_under_their_own_licenses() -> None:
    """The bundled Noto glyphs are OFL-1.1 and the sprite sheets MIT (`compiler/tiles.py`), so
    they are two credits in two sections. One combined line filed the MIT sheets under OFL and
    stated its terms over them — a defect in the artifact that discharges the bundle's legal
    obligations, not a wording one."""
    text = render_attribution(_bundle())

    ofl = _section(text, "OFL-1.1")
    mit = _section(text, "MIT")
    assert f"- Map glyphs (`{TILES.glyphs.path}`)" in ofl
    assert f"- Map sprites (`{TILES.sprites.path}`)" in mit
    # OFL's obligation — `OFL.txt` ships beside the glyphs it covers, and the fonts may not be
    # sold standalone — must reach the glyphs and nothing else.
    assert TILES.sprites.path not in ofl
    assert TILES.glyphs.path not in mit


def test_each_of_those_credits_reads_the_license_off_its_own_ref() -> None:
    """Neither license string is written in `attribution.py`: re-stamping the sprites moves
    that credit and leaves the glyphs where they were. A hardcoded license is what made the
    conflation invisible, so the fix is only durable if nothing here restates one."""
    tiles = _tile_source(
        sprites=GlyphsRef(path="sprites/", license="Apache-2.0", sha256=_digest("sprites"))
    )

    text = render_attribution(_bundle(tiles=tiles))

    assert f"- Map sprites (`{tiles.sprites.path}`)" in _section(text, "Apache-2.0")
    assert f"- Map glyphs (`{tiles.glyphs.path}`)" in _section(text, "OFL-1.1")
    assert "Map sprites" not in _section(text, "OFL-1.1")


# --- every bundled story credited exactly once ---------------------------------------------


def test_each_contributing_article_is_credited_exactly_once() -> None:
    """FR-024 / SC-010: three distinct articles, each credited once. A fourth story that
    reuses one of those same articles (identical `source`, as two listings drawn from one
    page would) must not add a second credit line for it."""
    ref_a = _ref(
        "wikivoyage",
        "CC-BY-SA-4.0",
        "article:generic-a",
        attribution='"Generic A", Wikivoyage, https://example.test/wiki/Generic_A?oldid=1 — '
        "authors via page history",
        url="https://example.test/wiki/Generic_A?oldid=1",
    )
    ref_b = _ref(
        "wikivoyage",
        "CC-BY-SA-4.0",
        "article:generic-b",
        attribution='"Generic B", Wikivoyage, https://example.test/wiki/Generic_B?oldid=1 — '
        "authors via page history",
        url="https://example.test/wiki/Generic_B?oldid=1",
    )
    ref_c = _ref(
        "wikipedia",
        "CC-BY-SA-4.0",
        "article:generic-c",
        attribution='"Generic C", Wikipedia, https://example.test/wiki/Generic_C?oldid=1 — '
        "authors via page history",
        url="https://example.test/wiki/Generic_C?oldid=1",
    )
    site_1 = _site(stories=(_story(ref_a), _story(ref_b)))
    site_2 = _site(stories=(_story(ref_a),))  # same article + same revision as site_1
    site_3 = _site(stories=(_story(ref_c),))

    text = render_attribution(_bundle(sites=(site_1, site_2, site_3)))

    assert text.count(_expect_str(ref_a.attribution)) == 1
    assert text.count(_expect_str(ref_b.attribution)) == 1
    assert text.count(_expect_str(ref_c.attribution)) == 1


def test_two_revisions_of_one_article_are_both_named_not_collapsed() -> None:
    """ADR-0024 A1: the credit names the revision adapted, not a moving article. Two stories
    citing the same article id but two different pinned revisions must both survive — the
    dedup that collapses identical revisions (above) must not also collapse distinct ones."""
    url_1 = "https://example.test/wiki/Generic_A?oldid=100"
    url_2 = "https://example.test/wiki/Generic_A?oldid=200"
    rev_1 = _ref(
        "wikivoyage",
        "CC-BY-SA-4.0",
        "article:generic-a",
        attribution=f'"Generic A", Wikivoyage, {url_1} — authors via page history',
        url=url_1,
    )
    rev_2 = _ref(
        "wikivoyage",
        "CC-BY-SA-4.0",
        "article:generic-a",
        attribution=f'"Generic A", Wikivoyage, {url_2} — authors via page history',
        url=url_2,
    )
    site = _site(stories=(_story(rev_1), _story(rev_2)))

    text = render_attribution(_bundle(sites=(site,)))
    lines = text.splitlines()

    assert f"- {rev_1.attribution}" in lines
    assert f"  - also adapted from {url_2}" in lines
    # One credit block for the article: the primary line carries the full credit, the extra
    # revision is named by its own URL rather than restating the title — not two independently
    # sorted top-level bullets.
    assert text.count("Generic A") == 1


# --- the text license declaration (ADR-0024's SA half) --------------------------------------


def test_bundled_text_license_is_declared_when_stories_are_present() -> None:
    ref = _ref(
        "wikipedia",
        "CC-BY-SA-4.0",
        "article:generic-a",
        attribution='"Generic A", Wikipedia, https://example.test/wiki/Generic_A?oldid=1 — '
        "authors via page history",
        url="https://example.test/wiki/Generic_A?oldid=1",
    )
    site = _site(stories=(_story(ref),))
    bundle = _bundle(sites=(site,))

    assert bundled_text_license(bundle.sites) == "CC-BY-SA-4.0"
    text = render_attribution(bundle)
    heading, _, rest = text.partition("## Bundled text license")
    assert heading != text  # the heading is actually present
    assert "CC-BY-SA-4.0" in rest
    assert "no narration text" not in rest


def test_bundled_text_license_is_null_and_says_so_with_no_stories() -> None:
    bundle = _bundle()  # no sites at all — nothing narrated
    assert bundled_text_license(bundle.sites) is None
    text = render_attribution(bundle)
    assert "`textLicense`" in text
    assert "no narration text" in text


# --- the grouping bug: N records from one source, a constant number of lines ---------------


def test_place_data_from_one_source_renders_one_line_not_one_per_record() -> None:
    """The regression that shipped once: `_add_place_data` groups on `(kind, license,
    attribution)` and deliberately **not** on the whole `SourceRef` — a stamp's `id` is
    per-record (a GERS id, an OSM element id), so grouping on the full stamp emits one credit
    line per place. 120 records sharing one source identity but 120 distinct record ids must
    still render as exactly one aggregate line."""
    n = 120
    sites = tuple(
        _site(place_ref=_ref("overture", "CDLA-Permissive-2.0", f"overture:record-{i}"))
        for i in range(n)
    )
    # `_site` stamps both `location` and one `names` entry from `place_ref`, so n records
    # carry 2n stamped values sharing this one (kind, license, attribution) triple.
    values = 2 * n

    text = render_attribution(_bundle(sites=sites))

    lines = [line for line in text.splitlines() if line.startswith("- Overture Maps,")]
    assert len(lines) == 1, (
        f"expected exactly one aggregate credit line for {values} Overture-sourced place "
        f"values sharing one (kind, license, attribution), got {len(lines)} — grouping on the "
        "whole SourceRef (its per-record id) regresses to one near-identical line per place "
        "and buries the licence obligation text underneath them"
    )
    assert f"{values} bundled place value" in lines[0]


# --- determinism: T040 hashes these bytes -----------------------------------------------


def test_render_attribution_is_deterministic_under_input_reordering() -> None:
    """`attribution_bytes` is what T040 hashes into `manifest.attribution.sha256` — the same
    bundle must render byte-identically however its sites, legs, notices, and each record's
    `names` key order were produced."""
    ref_a = _ref(
        "wikivoyage",
        "CC-BY-SA-4.0",
        "article:generic-a",
        attribution='"Generic A", Wikivoyage, https://example.test/wiki/Generic_A?oldid=1 — '
        "authors via page history",
        url="https://example.test/wiki/Generic_A?oldid=1",
    )
    ref_b = _ref(
        "wikipedia",
        "CC-BY-SA-4.0",
        "article:generic-b",
        attribution='"Generic B", Wikipedia, https://example.test/wiki/Generic_B?oldid=1 — '
        "authors via page history",
        url="https://example.test/wiki/Generic_B?oldid=1",
    )
    overture_ref = _ref("overture", "CDLA-Permissive-2.0", "overture:record-1")
    osm_ref = _ref("osm", "ODbL-1.0", "osm:node/1", attribution="© OpenStreetMap contributors")

    site_1 = _site(
        place_ref=overture_ref,
        names={
            "en": _stamped_str("Place One", overture_ref),
            "und": _stamped_str("Original Name", overture_ref),
        },
        categories=(
            _stamped_str("category.a", overture_ref),
            _stamped_str("category.b", overture_ref),
        ),
        stories=(_story(ref_a),),
    )
    site_2 = _site(
        place_ref=osm_ref,
        names={
            "el": _stamped_str("Place Two", osm_ref),
            "en": _stamped_str("Place Two (en)", osm_ref),
        },
        stories=(_story(ref_b),),
    )
    site_3 = _site(
        place_ref=overture_ref,
        notes=(_stamped_str("a note", overture_ref),),
        stories=(_story(ref_a),),  # same article as site_1 — dedup must stay stable too
    )
    sites = [site_1, site_2, site_3]
    legs = [_leg(0), _leg(1), _leg(2)]
    notices = [
        _notice("Dependency One", "Apache-2.0", "Copyright (c) Dependency One contributors.\n"),
        _notice("Dependency Two", "MIT", "Copyright (c) Dependency Two contributors.\n"),
    ]

    baseline = attribution_bytes(
        _bundle(sites=tuple(sites), legs=tuple(legs), notices=tuple(notices))
    )

    rng = random.Random(20260808)
    reordered_sites = [
        site.model_copy(update={"names": dict(reversed(list(site.names.items())))})
        for site in sites
    ]
    rng.shuffle(reordered_sites)
    reordered_legs = list(legs)
    rng.shuffle(reordered_legs)
    reordered_notices = list(notices)
    rng.shuffle(reordered_notices)
    assert [s.id for s in reordered_sites] != [s.id for s in sites], "shuffle must move something"

    shuffled = attribution_bytes(
        _bundle(
            sites=tuple(reordered_sites),
            legs=tuple(reordered_legs),
            notices=tuple(reordered_notices),
        )
    )

    assert shuffled == baseline


# --- attribution_bytes: the one encoding rule -----------------------------------------------


def test_attribution_bytes_is_utf8_lf_with_exactly_one_trailing_newline() -> None:
    """T040 hashes these bytes; a second caller re-encoding `render_attribution`'s string
    output independently must be unable to produce a different result, because there is
    exactly one encoding rule and it lives here, not at each call site."""
    bundle = _bundle(legs=(_leg(0),))
    raw = attribution_bytes(bundle)

    assert raw == render_attribution(bundle).encode("utf-8")
    assert raw.decode("utf-8") == render_attribution(bundle)
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")


# --- the three refusal paths: AttributionRefused, never a generic credit line ---------------


def test_refuses_an_attribution_required_license_with_no_attribution() -> None:
    """An ODbL-stamped value reaching the generator with an empty `attribution` is an
    upstream bug (FR-012). Refusing is the only correct response — a generic credit line
    would name nobody and discharge nothing."""
    bad_walk_graph = _ref("osm", "ODbL-1.0", "osm:walk-graph", attribution=None)

    with pytest.raises(AttributionRefused, match="requires attribution"):
        render_attribution(_bundle(walk_graph_source=bad_walk_graph))


def test_refuses_a_bundleable_false_value_reaching_frozen_content() -> None:
    """A `bundleable=false` value present in frozen content means the quarantine filter
    (T038) did not run — no wording written here can fix that, so this refuses too."""
    smuggled_ref = _ref("open_web", "CC0-1.0", "open-web:unverified-1")
    smuggled = SourcedValue[str].stamp(
        value="an unverified claim", source=smuggled_ref, confidence=0.5, observed_at=OBSERVED
    )
    site = _site(notes=(smuggled,))

    with pytest.raises(AttributionRefused, match="bundleable=false"):
        render_attribution(_bundle(sites=(site,)))


def test_refuses_a_license_off_the_bundleable_allowlist() -> None:
    """A license string never added to `BUNDLEABLE_LICENSES` must stop the compile rather
    than ship silently. Reached here through the glyphs' own `license` field, which the model
    does not itself allowlist-check — that check is this module's job."""
    bundle = _bundle(
        tiles=_tile_source(
            glyphs=GlyphsRef(
                path="glyphs/", license="Some-Unknown-License-9.9", sha256=_digest("glyphs")
            )
        )
    )

    with pytest.raises(AttributionRefused, match="not on the bundleable allowlist"):
        render_attribution(bundle)


# --- the import-time tripwire: an allowlisted license with no obligation text ---------------


def test_import_time_tripwire_refuses_a_license_with_no_obligation_text() -> None:
    """`compiler/attribution.py` raises at import if `BUNDLEABLE_LICENSES` ever outruns
    `_OBLIGATIONS` — a bundle must never redistribute data under a license it states no terms
    for. Exercised by patching the registry and reloading the module under test, restored
    (and reloaded again) in `finally` so no other test in this session sees the patched state.
    """
    import commons.licenses as licenses_module
    import compiler.attribution as attribution_module

    original_licenses = licenses_module.BUNDLEABLE_LICENSES
    # `BUNDLEABLE_LICENSES` is `Final` — reassigning it is exactly what mypy exists to forbid
    # everywhere except here, where the whole point is to simulate the registry outrunning
    # this module's obligation text.
    licenses_module.BUNDLEABLE_LICENSES = (  # type: ignore[misc]
        original_licenses | frozenset({"Some-New-License-9.9"})
    )
    try:
        with pytest.raises(ImportError, match="no obligation text"):
            importlib.reload(attribution_module)
    finally:
        licenses_module.BUNDLEABLE_LICENSES = original_licenses  # type: ignore[misc]
        importlib.reload(attribution_module)
